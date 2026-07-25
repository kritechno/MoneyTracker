import contextvars
import json
import os
import threading

from config import CURRENCIES

# Путь к settings.json. SETTINGS_PATH можно направить на постоянное хранилище,
# если бот запускается не с локального диска. Локально — рядом с кодом.
_PATH = os.getenv("SETTINGS_PATH") or os.path.join(os.path.dirname(__file__), "settings.json")

# Тур (профиль) по умолчанию. Каждый тур — отдельный .xlsx-файл, имя файла = имя тура.
DEFAULT_PROFILE = "FDTG tour 2026"
_DEFAULT_PROFILE = {"file": f"{DEFAULT_PROFILE}.xlsx"}
_DEFAULTS = {"default_currency": "KZT"}

# Настройки читаются и пишутся из нескольких потоков (asyncio.to_thread в bot.py),
# а с несколькими гидами — ещё и одновременно. Общий RLock сериализует
# read-modify-write, а сохранение делаем атомарно (temp-файл + os.replace),
# чтобы параллельные записи не оставили обрезанный/битый JSON.
_lock = threading.RLock()

# «Текущий пользователь» этого обновления. bot.py выставляет его один раз на
# апдейт (pre-handler), а функции состояния без явного uid берут его отсюда —
# так каждый гид автоматически работает со своим туром/валютой, и не нужно
# протаскивать uid через ~45 вызовов. Через asyncio.to_thread contextvar
# копируется в рабочий поток, а в тестах он не выставлен → фолбэк на глобальное.
_current_uid: contextvars.ContextVar = contextvars.ContextVar("current_uid", default=None)


def set_current_uid(uid: int | None) -> None:
    _current_uid.set(int(uid) if uid is not None else None)


def _effective_uid(uid: int | None) -> int | None:
    return uid if uid is not None else _current_uid.get()


# --- Загрузка/сохранение ----------------------------------------------------


def _ensure_profiles(data: dict) -> dict:
    """Гарантирует наличие тура по умолчанию, активного тура и карты user_state."""
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        data["profiles"] = {DEFAULT_PROFILE: dict(_DEFAULT_PROFILE)}
    if data.get("active_profile") not in data["profiles"]:
        data["active_profile"] = next(iter(data["profiles"]))
    # user_state: {"<uid>": {"active_profile": ..., "default_currency": ...}} —
    # у каждого гида свой активный тур и валюта по умолчанию. Глобальные
    # active_profile/default_currency остаются как legacy-фолбэк (uid=None, тесты).
    if not isinstance(data.get("user_state"), dict):
        data["user_state"] = {}
    return data


def _load() -> dict:
    with _lock:
        if os.path.exists(_PATH):
            try:
                with open(_PATH, encoding="utf-8") as f:
                    return _ensure_profiles({**_DEFAULTS, **json.load(f)})
            except Exception:
                return _ensure_profiles(dict(_DEFAULTS))
        return _ensure_profiles(dict(_DEFAULTS))


def _save(data: dict) -> None:
    with _lock:
        tmp = f"{_PATH}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _PATH)  # атомарная подмена — никаких обрезанных файлов


def _ustate(data: dict, uid: int) -> dict:
    """Возвращает (создавая при необходимости) запись состояния пользователя."""
    us = data.setdefault("user_state", {})
    key = str(uid)
    st = us.get(key)
    if not isinstance(st, dict):
        st = {}
        us[key] = st
    return st


# --- Владелец ---------------------------------------------------------------


def get_owner_id() -> int | None:
    """Telegram-id владельца бота. None — ещё не присвоен (auto-claim)."""
    val = _load().get("owner_id")
    return val if isinstance(val, int) else None


def set_owner_id(user_id: int) -> None:
    with _lock:
        data = _load()
        data["owner_id"] = int(user_id)
        _save(data)


# --- Доп. список доступа ----------------------------------------------------
# Владелец может выдать доступ другим пользователям командой /allow, не трогая
# .env и не передеплоивая. Эти id хранятся здесь, в settings.json, и работают
# вдобавок к владельцу и списку ALLOWED_USER_IDS из .env.


def _stored_allowed_ids(data: dict) -> list[int]:
    out: list[int] = []
    for x in data.get("allowed_user_ids", []) or []:
        try:
            uid = int(x)
        except (TypeError, ValueError):
            continue
        if uid not in out:
            out.append(uid)
    return out


def get_allowed_user_ids() -> set[int]:
    """Telegram-id, которым владелец выдал доступ через /allow."""
    return set(_stored_allowed_ids(_load()))


def add_allowed_user_id(user_id: int) -> bool:
    """Добавляет id в список доступа. False — если он там уже был."""
    with _lock:
        data = _load()
        ids = _stored_allowed_ids(data)
        if int(user_id) in ids:
            return False
        ids.append(int(user_id))
        data["allowed_user_ids"] = ids
        _save(data)
        return True


def remove_allowed_user_id(user_id: int) -> bool:
    """Убирает id из списка доступа. False — если его там не было."""
    with _lock:
        data = _load()
        ids = _stored_allowed_ids(data)
        if int(user_id) not in ids:
            return False
        data["allowed_user_ids"] = [x for x in ids if x != int(user_id)]
        _save(data)
        return True


# --- Валюта по умолчанию (на пользователя) ----------------------------------


def get_default_currency(uid: int | None = None) -> str:
    data = _load()
    uid = _effective_uid(uid)
    if uid is not None:
        st = data.get("user_state", {}).get(str(uid))
        if isinstance(st, dict) and st.get("default_currency"):
            return st["default_currency"]
    return data.get("default_currency", "KZT")


def set_default_currency(currency: str, uid: int | None = None) -> bool:
    currency = (currency or "").upper().strip()
    if currency not in CURRENCIES:
        return False
    with _lock:
        data = _load()
        uid = _effective_uid(uid)
        if uid is None:
            data["default_currency"] = currency
        else:
            _ustate(data, uid)["default_currency"] = currency
        _save(data)
        return True


# --- Профили ---------------------------------------------------------------


def ensure_defaults() -> None:
    """Записывает структуру профилей в settings.json, если её ещё нет."""
    _save(_load())


def _active_name(data: dict, uid: int | None) -> str | None:
    """Имя активного тура: персональное для uid, иначе глобальное (legacy)."""
    if uid is not None:
        st = data.get("user_state", {}).get(str(uid))
        if isinstance(st, dict) and st.get("active_profile") in data["profiles"]:
            return st["active_profile"]
        return None  # у пользователя ещё нет своего активного тура
    return data["active_profile"]


def list_profiles(uid: int | None = None) -> list[str]:
    """Список туров. uid=None — все (админ/legacy); иначе — только принадлежащие
    этому пользователю (владелец тура == uid)."""
    data = _load()
    names = list(data["profiles"].keys())
    if uid is None:
        return names
    out = []
    for n in names:
        prof = data["profiles"][n]
        owner = prof.get("owner_uid") if isinstance(prof, dict) else None
        if isinstance(owner, int) and owner == int(uid):
            out.append(n)
    return out


def get_active_profile(uid: int | None = None) -> str | None:
    return _active_name(_load(), _effective_uid(uid))


def get_active_file(uid: int | None = None) -> str | None:
    """Имя .xlsx-файла активного тура пользователя (без папки) или None."""
    data = _load()
    name = _active_name(data, _effective_uid(uid))
    if name is None:
        return None
    prof = data["profiles"].get(name)
    file = prof.get("file") if isinstance(prof, dict) else None
    return file or f"{name}.xlsx"


def get_profile_file(name: str | None = None) -> str | None:
    """Имя файла указанного тура (или глобального активного, если name=None)."""
    data = _load()
    name = name or data["active_profile"]
    prof = data["profiles"].get(name)
    return prof.get("file") if isinstance(prof, dict) else None


def set_profile_file(name: str, file: str) -> bool:
    with _lock:
        data = _load()
        if name not in data["profiles"]:
            return False
        data["profiles"][name]["file"] = file
        _save(data)
        return True


def profile_exists(name: str) -> bool:
    return (name or "").strip() in _load()["profiles"]


def set_active_profile(name: str, uid: int | None = None) -> bool:
    name = (name or "").strip()
    with _lock:
        data = _load()
        if name not in data["profiles"]:
            return False
        uid = _effective_uid(uid)
        if uid is None:
            data["active_profile"] = name
        else:
            _ustate(data, uid)["active_profile"] = name
        _save(data)
        return True


def all_profiles() -> dict:
    """Полная карта туров: name -> {file, owner_uid?} (или старое {data, summary,
    wallet_sheet} до миграции excel_store.migrate_to_files)."""
    return _load()["profiles"]


def register_profile(name: str, file: str, owner_uid: int | None = None) -> None:
    """Записывает тур как {file[, owner_uid]}. Перетирает старую запись (листы)
    при миграции, но сохраняет прежнего владельца, если новый не передан."""
    with _lock:
        data = _load()
        name = name.strip()
        old = data["profiles"].get(name)
        keep_owner = owner_uid
        if keep_owner is None and isinstance(old, dict) and isinstance(old.get("owner_uid"), int):
            keep_owner = old["owner_uid"]  # правка файла тура не меняет владельца
        if keep_owner is None:
            keep_owner = _current_uid.get()  # новый тур → владелец = создатель
        entry = {"file": file}
        if keep_owner is not None:
            entry["owner_uid"] = int(keep_owner)
        data["profiles"][name] = entry
        _save(data)


def delete_profile(name: str) -> bool:
    """Удаляет тур из настроек. Последний тур удалить нельзя (всегда нужен хотя бы
    один). Если удаляем глобальный активный — активным становится любой из
    оставшихся; персональные активные, указывавшие на удалённый тур, сбрасываются.
    Возвращает True, если тур удалён."""
    name = (name or "").strip()
    with _lock:
        data = _load()
        profiles = data["profiles"]
        if name not in profiles or len(profiles) <= 1:
            return False
        del profiles[name]
        if data.get("active_profile") == name:
            data["active_profile"] = next(iter(profiles))
        for st in data.get("user_state", {}).values():
            if isinstance(st, dict) and st.get("active_profile") == name:
                st["active_profile"] = None
        _save(data)
        return True


# --- Владение туром ---------------------------------------------------------
# С несколькими гидами каждый тур принадлежит своему создателю (owner_uid).
# Гид видит и переключает только свои туры; админ (см. bot._is_admin) — все.
# Туры без owner_uid — «ничьи» (legacy до назначения владельца): их видит только
# админ, что делает миграцию безопасной (ни один существующий тур не пропадает).


def profile_owner(name: str) -> int | None:
    prof = _load()["profiles"].get((name or "").strip())
    owner = prof.get("owner_uid") if isinstance(prof, dict) else None
    return owner if isinstance(owner, int) else None


def owns(uid: int, name: str) -> bool:
    """True, если тур принадлежит этому пользователю."""
    return profile_owner(name) == int(uid)


def set_profile_owner(name: str, uid: int | None) -> bool:
    """Назначает (или снимает, если uid=None) владельца тура."""
    name = (name or "").strip()
    with _lock:
        data = _load()
        prof = data["profiles"].get(name)
        if not isinstance(prof, dict):
            return False
        if uid is None:
            prof.pop("owner_uid", None)
        else:
            prof["owner_uid"] = int(uid)
        _save(data)
        return True


def seed_user_state(uid: int, active_profile: str | None = None,
                    default_currency: str | None = None) -> None:
    """Разовое заполнение персонального состояния (для миграции/онбординга)."""
    with _lock:
        data = _load()
        st = _ustate(data, uid)
        if active_profile is not None and active_profile in data["profiles"]:
            st["active_profile"] = active_profile
        if default_currency is not None:
            st["default_currency"] = default_currency
        _save(data)


# --- Кошелёк ----------------------------------------------------------------
# Источник истины по остатку — лист «Кошелёк» в файле тура (см. excel_store).
# Ниже — только хелперы разовой миграции совсем старого формата кошелька, когда
# остаток лежал словарём прямо в settings.json.


def get_legacy_wallet(name: str) -> dict | None:
    """Старый формат кошелька (словарь валют в settings.json) для миграции."""
    stored = _load()["profiles"].get(name, {}).get("wallet")
    return stored if isinstance(stored, dict) else None


def clear_legacy_wallet(name: str) -> None:
    with _lock:
        data = _load()
        prof = data["profiles"].get(name)
        if prof and "wallet" in prof:
            del prof["wallet"]
            _save(data)
