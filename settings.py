import json
import os

from config import CURRENCIES

# Путь к settings.json. На Railway указывает на persistent Volume (SETTINGS_PATH),
# иначе настройки/владелец/профили сотрутся при каждом редеплое. Локально — рядом с кодом.
_PATH = os.getenv("SETTINGS_PATH") or os.path.join(os.path.dirname(__file__), "settings.json")

# Профиль по умолчанию: все исторические траты лежат на листах «Расходы»/«Итоги».
DEFAULT_PROFILE = "FDTG tour 2026"
# wallet_sheet включён в дефолт, чтобы свежий settings.json (напр. на чистом
# Railway Volume) совпадал с текущим и кошелёк «Кошелёк» сразу работал.
_DEFAULT_PROFILE_SHEETS = {"data": "Расходы", "summary": "Итоги", "wallet_sheet": "Кошелёк"}
_DEFAULTS = {"default_currency": "KZT"}


def _ensure_profiles(data: dict) -> dict:
    """Гарантирует наличие профиля по умолчанию и активного профиля."""
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        data["profiles"] = {DEFAULT_PROFILE: dict(_DEFAULT_PROFILE_SHEETS)}
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


def get_active_sheets() -> tuple[str, str]:
    data = _load()
    sheets = data["profiles"][data["active_profile"]]
    return sheets["data"], sheets["summary"]


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
    """Полная карта профилей: name -> {data, summary, wallet_sheet?, wallet?}."""
    return _load()["profiles"]


def register_profile(
    name: str, data_sheet: str, summary_sheet: str, wallet_sheet: str
) -> None:
    data = _load()
    data["profiles"][name.strip()] = {
        "data": data_sheet,
        "summary": summary_sheet,
        "wallet_sheet": wallet_sheet,
    }
    _save(data)


# --- Кошелёк ----------------------------------------------------------------
# Источник истины — лист «Кошелёк» в Excel (см. excel_store). Здесь хранится
# только имя этого листа. Остаток считается как поступления/обмены − траты.


def get_wallet_sheet(name: str | None = None) -> str | None:
    data = _load()
    name = name or data["active_profile"]
    return data["profiles"].get(name, {}).get("wallet_sheet")


def get_active_wallet_sheet() -> str | None:
    return get_wallet_sheet()


def set_wallet_sheet(name: str, wallet_sheet: str) -> bool:
    data = _load()
    if name not in data["profiles"]:
        return False
    data["profiles"][name]["wallet_sheet"] = wallet_sheet
    _save(data)
    return True


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
