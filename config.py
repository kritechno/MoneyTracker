import os
import re

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
EXCEL_PATH = os.getenv("EXCEL_PATH", "expenses.xlsx").strip()
# Папка для приложенных к тратам фото-чеков. По умолчанию рядом с Excel-файлом
# или в BILLS_DIR, если нужно хранить чеки на отдельном постоянном диске.
BILLS_DIR = (
    os.getenv("BILLS_DIR", "").strip()
    or os.path.join(os.path.dirname(EXCEL_PATH) or ".", "bills")
)

# Кто может пользоваться ботом. Пусто → первый написавший становится владельцем
# (auto-claim, см. settings.owner_id). Можно задать явно: ALLOWED_USER_IDS=123,456
ALLOWED_USER_IDS = {
    int(x) for x in re.split(r"[\s,]+", os.getenv("ALLOWED_USER_IDS", "")) if x.isdigit()
}

# Поддерживаемые валюты и категории
CURRENCIES = ["KZT", "KGS", "USD", "EUR", "UZS", "TJS"]
CURRENCY_LABELS = {
    "KZT": "Тенге",
    "KGS": "Сом",
    "USD": "Доллар",
    "EUR": "Евро",
    "UZS": "Сум",
    "TJS": "Сомони",
}
CATEGORIES = ["Отель", "Питание", "Бензин", "Прочее"]

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY не задан в .env")
