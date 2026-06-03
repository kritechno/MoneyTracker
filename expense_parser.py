import re
from dataclasses import dataclass
from datetime import date

import currency

_NUMBER_PATTERN = r"(\d[\d\s.,]*\d|\d)"
_NUMBER_RE = re.compile(_NUMBER_PATTERN)
_AMOUNT_RE = re.compile(
    rf"{_NUMBER_PATTERN}\s*([a-zA-Zа-яёА-ЯЁ$₸.]+)?",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"(?i)\b("
    r"вчера|позавчера|сегодня|завтра|"
    r"понедельник|вторник|сред[ау]|четверг|пятниц[ау]|суббот[ау]|воскресень[ея]|"
    r"январ[ьяе]|феврал[ьяе]|март[ае]?|апрел[ьяе]|ма[йяе]|июн[ьяе]|"
    r"июл[ьяе]|август[ае]?|сентябр[ьяе]|октябр[ьяе]|ноябр[ьяе]|декабр[ьяе]"
    r")\b|"
    r"\b\d{1,4}[./-]\d{1,2}(?:[./-]\d{1,4})?\b"
)

_TRIM_RE = re.compile(r"^[\s,.;:!?\-—–«»\"']+|[\s,.;:!?\-—–«»\"']+$")
_FILLER_RE = re.compile(
    r"(?i)\b(за|на|в|во|по|и|из|для|купил|купила|купили|оплатил|оплатила|"
    r"заплатил|заплатила|покупка|чек)\b"
)


@dataclass(frozen=True)
class ParseResult:
    entry: dict | None
    reason: str

    @property
    def parsed(self) -> bool:
        return self.entry is not None

    @property
    def should_fallback(self) -> bool:
        return self.reason == "fallback"

    @property
    def no_amount(self) -> bool:
        return self.reason == "no_amount"


def parse_expense(text: str | None, default_currency: str = "KZT") -> ParseResult:
    text = (text or "").strip()
    if not text:
        return ParseResult(None, "no_amount")
    if _DATE_RE.search(text):
        return ParseResult(None, "fallback")

    amount_matches = list(_NUMBER_RE.finditer(text))
    if not amount_matches:
        return ParseResult(None, "no_amount")
    if len(amount_matches) != 1:
        return ParseResult(None, "fallback")

    match = _amount_match_at(text, amount_matches[0].start())
    if match is None:
        return ParseResult(None, "fallback")

    amount = currency.normalize_number(match.group(1))
    if amount is None or amount <= 0:
        return ParseResult(None, "no_amount")

    explicit_currency = currency.match_currency(match.group(2))
    expense_currency = explicit_currency or _safe_default_currency(default_currency)
    description_text = _description_without_amount(text, match, explicit_currency)
    description, category = _normalize_description(description_text)

    return ParseResult(
        {
            "date": date.today().isoformat(),
            "description": description,
            "amount": round(amount, 2),
            "currency": expense_currency,
            "category": category,
        },
        "parsed",
    )


def _safe_default_currency(default_currency: str) -> str:
    cur = (default_currency or "KZT").upper().strip()
    return cur if cur in {"KZT", "KGS", "USD", "EUR", "UZS", "TJS"} else "KZT"


def _amount_match_at(text: str, start: int) -> re.Match | None:
    for match in _AMOUNT_RE.finditer(text):
        if match.start() == start:
            return match
    return None


def _description_without_amount(
    text: str, match: re.Match, explicit_currency: str | None
) -> str:
    end = match.end()
    if match.group(2) and explicit_currency is None:
        end = match.start(2)
    desc = f"{text[:match.start()]} {text[end:]}"
    desc = _FILLER_RE.sub(" ", desc)
    desc = re.sub(r"\s+", " ", desc)
    return _TRIM_RE.sub("", desc).strip()


def _normalize_description(text: str) -> tuple[str, str]:
    original = text.strip()
    lowered = original.lower()

    for keyword in ("ресторан", "кафе", "кофейня", "столовая", "бар"):
        if keyword in lowered:
            return _named_description("Ресторан", original, keyword), "Питание"
    if any(word in lowered for word in ("еда", "обед", "ужин", "завтрак", "кофе", "плов")):
        return _fallback_description(original, "Еда"), "Питание"

    for keyword in ("заправка", "азс"):
        if keyword in lowered:
            return _named_description("Заправка", original, keyword), "Бензин"
    if any(word in lowered for word in ("бензин", "топливо", "дизель")):
        return "Заправка", "Бензин"

    for keyword in ("отель", "гостиница", "хостел", "hostel", "hotel"):
        if keyword in lowered:
            return _named_description("Отель", original, keyword), "Отель"
    if any(word in lowered for word in ("жилье", "жильё", "апартаменты", "airbnb")):
        return _fallback_description(original, "Отель"), "Отель"

    if any(word in lowered for word in ("такси", "яндекс", "uber", "bolt")):
        return "Такси", "Прочее"
    if any(word in lowered for word in ("магазин", "маркет", "супермаркет", "продукты")):
        return "Магазин", "Прочее"

    return _fallback_description(original, "Трата"), "Прочее"


def _named_description(prefix: str, text: str, keyword: str) -> str:
    lowered = text.lower()
    idx = lowered.find(keyword)
    name = text[idx + len(keyword) :] if idx >= 0 else ""
    name = _TRIM_RE.sub("", name).strip()
    if not name:
        return prefix
    return f"{prefix} «{name}»"


def _fallback_description(text: str, fallback: str) -> str:
    if not text:
        return fallback
    return text[:1].upper() + text[1:]
