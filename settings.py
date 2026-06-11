import json
import os

from config import CURRENCIES

# Путь к settings.json. SETTINGS_PATH можно направить на постоянное хранилище,
# если бот запускается не с локального диска. Локально — рядом с кодом.
_PATH = os.getenv("SETTINGS_PATH") or os.path.join(os.path.dirname(__file__), "settings.json")

# Тур (профиль) по умолчанию. Каждый тур — отдельный .xlsx-файл, имя файла = имя тура.
DEFAULT_PROFILE = "FDTG tour 2026"
_DEFAULT_PROFILE = {"file": f"{DEFAULT_PROFILE}.xlsx"}
_DEFAULTS = {"default_currency": "KZT"}


def _ensure_profiles(data: dict) -> dict:
    """Гарантирует наличие тура по умолчанию и активного тура."""
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        data["profiles"] = {DEFAULT_PROFILE: dict(_DEFAULT_PROFILE)}
    if data.get("active_profile") not in data["profiles"]:
        data["active_profile"] = next(iter(data["profiles"]))
    return data


def _load() -> dict:
    if os.path.exists(_PATH):
        try:
            with open(_PATH, encoding="utf-8") as f:
                return _ensure_profiles({**_DEFAULTS, **json.load(f)})
        except Exception:
            return _ensure_profiles(dict(_DEFAULTS))
    return _ensure_profiles(dict(_DEFAULTS))


def _save(data: dict) -> None:
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_owner_id() -> int | None:
    """Telegram-id владельца бота. None — ещё не присвоен (auto-claim)."""
    val = _load().get("owner_id")
    return val if isinstance(val, int) else None


def set_owner_id(user_id: int) -> None:
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
    data = _load()
    ids = _stored_allowed_ids(data)
    if int(user_id) not in ids:
        return False
    data["allowed_user_ids"] = [x for x in ids if x != int(user_id)]
    _save(data)
    return True


def get_default_currency() -> str:
    return _load().get("default_currency", "KZT")


def set_default_currency(currency: str) -> bool:
    currency = (currency or "").upper().strip()
    if currency not in CURRENCIES:
        return False
    data = _load()
    data["default_currency"] = currency
    _save(data)
    return True


# --- Профили ---------------------------------------------------------------


def ensure_defaults() -> None:
    """Записывает структуру профилей в settings.json, если её ещё нет."""
    _save(_load())


def list_profiles() -> list[str]:
    return list(_load()["profiles"].keys())


def get_active_profile() -> str:
    return _load()["active_profile"]


def get_active_file() -> str:
    """Имя .xlsx-файла активного тура (без папки)."""
    data = _load()
    prof = data["profiles"][data["active_profile"]]
    file = prof.get("file") if isinstance(prof, dict) else None
    # Подстраховка на случай старой записи до миграции excel_store.migrate_to_files.
    return file or f"{data['active_profile']}.xlsx"


def get_profile_file(name: str | None = None) -> str | None:
    """Имя файла указанного тура (или активного, если name=None)."""
    data = _load()
    name = name or data["active_profile"]
    prof = data["profiles"].get(name)
    return prof.get("file") if isinstance(prof, dict) else None


def set_profile_file(name: str, file: str) -> bool:
    data = _load()
    if name not in data["profiles"]:
        return False
    data["profiles"][name]["file"] = file
    _save(data)
    return True


def profile_exists(name: str) -> bool:
    return (name or "").strip() in _load()["profiles"]


def set_active_profile(name: str) -> bool:
    name = (name or "").strip()
    data = _load()
    if name not in data["profiles"]:
        return False
    data["active_profile"] = name
    _save(data)
    return True


def all_profiles() -> dict:
    """Полная карта туров: name -> {file} (или старое {data, summary, wallet_sheet}
    до миграции excel_store.migrate_to_files)."""
    return _load()["profiles"]


def register_profile(name: str, file: str) -> None:
    """Записывает тур как {file}. Перетирает старую запись (листы) при миграции."""
    data = _load()
    data["profiles"][name.strip()] = {"file": file}
    _save(data)


def delete_profile(name: str) -> bool:
    """Удаляет тур из настроек. Последний тур удалить нельзя (всегда нужен хотя бы
    один). Если удаляем активный — активным становится любой из оставшихся.
    Возвращает True, если тур удалён."""
    name = (name or "").strip()
    data = _load()
    profiles = data["profiles"]
    if name not in profiles or len(profiles) <= 1:
        return False
    del profiles[name]
    if data.get("active_profile") == name:
        data["active_profile"] = next(iter(profiles))
    _save(data)
    return True


# --- Кошелёк ----------------------------------------------------------------
# Источник истины по остатку — лист «Кошелёк» в файле тура (см. excel_store).
# Ниже — только хелперы разовой миграции совсем старого формата кошелька, когда
# остаток лежал словарём прямо в settings.json.


def get_legacy_wallet(name: str) -> dict | None:
    """Старый формат кошелька (словарь валют в settings.json) для миграции."""
    stored = _load()["profiles"].get(name, {}).get("wallet")
    return stored if isinstance(stored, dict) else None


def clear_legacy_wallet(name: str) -> None:
    data = _load()
    prof = data["profiles"].get(name)
    if prof and "wallet" in prof:
        del prof["wallet"]
        _save(data)
