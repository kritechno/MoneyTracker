import re
import time

import httpx

# Базовый источник курсов: 1 USD = rates[CUR] единиц валюты CUR.
_RATES_URL = "https://open.er-api.com/v6/latest/USD"
_CACHE_TTL = 3600  # 1 час

# Запасные курсы на случай отсутствия интернета (примерные, обновите при желании).
_FALLBACK = {
    "USD": 1.0,
    "KZT": 480.0,
    "KGS": 87.0,
    "EUR": 0.92,
    "UZS": 12700.0,
    "TJS": 10.9,
}

_cache = {"rates": None, "ts": 0.0}


def _fetch_rates() -> dict:
    resp = httpx.get(_RATES_URL, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    rates = data.get("rates") or {}
    if "USD" not in rates:
        rates["USD"] = 1.0
    return rates


def get_rates() -> dict:
    now = time.time()
    if _cache["rates"] and now - _cache["ts"] < _CACHE_TTL:
        return _cache["rates"]
    try:
        rates = _fetch_rates()
        _cache["rates"] = rates
        _cache["ts"] = now
        return rates
    except Exception:
        return _cache["rates"] or _FALLBACK


# Распознавание валюты по словам/символам в тексте.
_CURRENCY_WORDS = [
    ("USD", ["доллар", "доллара", "долларов", "долл", "бакс", "баксов", "usd", "$", "у.е", "уе"]),
    ("KZT", ["тенге", "тнг", "тг", "₸", "kzt", "кзт"]),
    ("EUR", ["евро", "еуро", "евр", "eur", "€"]),
    ("UZS", ["сум", "сума", "сумов", "uzs", "узс"]),
    # TJS раньше KGS: «сомони» начинается на «сом» и иначе попало бы в KGS.
    # Точное «сом…» разводит match_currency отдельно; здесь — кириллический код
    # и сокращение сомони, которые иначе не распознавались и падали в валюту по
    # умолчанию (та же ошибка, что и «сомани», но другим путём).
    ("TJS", ["сомони", "tjs", "тжс", "смн"]),
    ("KGS", ["сом", "сома", "сомов", "kgs", "кгс"]),
]


def match_currency(token: str | None) -> str | None:
    if not token:
        return None
    token = token.lower().strip()
    # «сом…» неоднозначно: киргизский сом (KGS) и таджикский сомони (TJS) имеют
    # общий префикс. Сомони — это «сом» + гласная + «н» (сомони, а также частые
    # опечатки/формы сомани/сомон/соман); киргизские формы (сом/сома/сомы/сомов)
    # такого «н» не имеют. Раньше распознавали только точное «сомони», и любая
    # другая запись «сом…» проваливалась в KGS.
    if token.startswith("сом"):
        return "TJS" if re.match(r"сом[оа]н", token) else "KGS"
    for code, words in _CURRENCY_WORDS:
        for w in words:
            if token.startswith(w) or token == w:
                return code
    return None


def parse_amounts(text: str) -> list[tuple[float, str]]:
    """Извлекает пары (сумма, валюта) из текста, напр.
    «30 долларов 15000 тенге» -> [(30.0, 'USD'), (15000.0, 'KZT')]."""
    results: list[tuple[float, str]] = []
    # число (с пробелами/запятыми как разделителями тысяч, точкой как десятичной)
    pattern = re.compile(r"(\d[\d\s.,]*\d|\d)\s*([a-zA-Zа-яёА-ЯЁ$₸.]+)?")
    for match in pattern.finditer(text):
        raw_num, raw_cur = match.group(1), match.group(2)
        num = _normalize_number(raw_num)
        if num is None:
            continue
        cur = match_currency(raw_cur)
        if cur is None:
            continue
        results.append((num, cur))
    return results


def parse_single_amount(text: str) -> tuple[float, str | None] | None:
    """Парсит одну сумму с необязательной валютой: «4500» -> (4500.0, None),
    «4500 тенге» -> (4500.0, 'KZT'). Для правки суммы существующей траты."""
    m = re.search(r"(\d[\d\s.,]*\d|\d)\s*([a-zA-Zа-яёА-ЯЁ$₸.]+)?", text or "")
    if not m:
        return None
    num = _normalize_number(m.group(1))
    if num is None:
        return None
    cur = match_currency(m.group(2))
    return num, cur


def normalize_number(raw: str) -> float | None:
    raw = raw.strip()
    if "," in raw and "." not in raw and " " not in raw:
        head, tail = raw.rsplit(",", 1)
        if len(tail) in (1, 2):
            cleaned = f"{head}.{tail}"
        else:
            cleaned = raw.replace(",", "")
    elif "." in raw and "," not in raw:
        cleaned = raw.replace(" ", "")
    else:
        cleaned = raw.replace(" ", "").replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


_normalize_number = normalize_number


def to_usd(amount: float, currency: str) -> float:
    currency = (currency or "USD").upper()
    if currency == "USD":
        return round(amount, 2)
    rates = get_rates()
    rate = rates.get(currency) or _FALLBACK.get(currency)
    if not rate:
        return round(amount, 2)
    return round(amount / rate, 2)


def from_usd(amount_usd: float, currency: str) -> float:
    currency = (currency or "USD").upper()
    if currency == "USD":
        return round(amount_usd, 2)
    rates = get_rates()
    rate = rates.get(currency) or _FALLBACK.get(currency)
    if not rate:
        return round(amount_usd, 2)
    return round(amount_usd * rate, 2)


def convert(amount: float, from_cur: str, to_cur: str) -> float:
    """Конвертирует сумму из одной валюты в другую по рыночному курсу."""
    from_cur = (from_cur or "USD").upper()
    to_cur = (to_cur or "USD").upper()
    if from_cur == to_cur:
        return round(amount, 2)
    return from_usd(to_usd(amount, from_cur), to_cur)
