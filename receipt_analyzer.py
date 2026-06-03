import json
import logging
import time
from datetime import date

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from config import CATEGORIES, CURRENCIES, GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger("moneytracker")

_client = genai.Client(api_key=GEMINI_API_KEY)

# Транзитные ошибки Gemini (перегрузка/лимит) — повторяем с нарастающей паузой,
# иначе один 503 «high demand» молча теряет трату пользователя.
_RETRY_CODES = {429, 500, 503}
_RETRY_BACKOFF = (1.0, 3.0)  # паузы перед повторами; всего попыток = len + 1


class ModelOverloaded(Exception):
    """Gemini вернул 429/500/503 после всех повторов — стоит попробовать позже."""

_SYSTEM = f"""Ты — ассистент по учёту трат. На вход поступает текстовое описание траты от пользователя.
Определи трату и верни СТРОГО один JSON-объект без пояснений и markdown.

Поля:
- "date": дата траты в формате YYYY-MM-DD. Если в тексте даты нет — используй переданную сегодняшнюю дату.
- "description": краткое описание траты на русском.
    * Для ресторана/кафе пиши: Ресторан «Название». Если названия нет — просто: Ресторан.
      НЕ перечисляй блюда — только название заведения.
    * Для заправки: Заправка «Название» (или просто Заправка).
    * Для отеля: Отель «Название» (или просто Отель).
    * Для прочего — короткое осмысленное описание (магазин, такси, и т.п.).
- "amount": сумма траты (число, без валютного знака).
- "currency": один из {CURRENCIES}. Определи по словам/символам (₸/тг/тенге=KZT, сом=KGS, $/доллар=USD, €/евро=EUR, сум=UZS, сомони=TJS).
    Помни: большие суммы (тысячи и десятки тысяч) для еды/воды/музея — это почти всегда тенге (KZT), а не доллары.
    Если валюту определить невозможно — используй валюту по умолчанию, указанную в сообщении пользователя.
- "category": один из {CATEGORIES}. Ресторан/кафе/еда -> Питание. Отель/гостиница -> Отель.
    Заправка/топливо -> Бензин. Остальное -> Прочее.

Верни только JSON.
"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"Не удалось распарсить ответ модели: {text}")
    return json.loads(text[start : end + 1])


def _normalize(data: dict, default_currency: str = "KZT") -> dict:
    today = date.today().isoformat()
    parsed_date = str(data.get("date") or today).strip() or today

    currency = str(data.get("currency") or default_currency).upper().strip()
    if currency not in CURRENCIES:
        currency = default_currency

    category = str(data.get("category") or "Прочее").strip()
    if category not in CATEGORIES:
        category = "Прочее"

    try:
        amount = round(float(data.get("amount")), 2)
    except (TypeError, ValueError):
        amount = 0.0

    description = str(data.get("description") or "Без описания").strip()

    return {
        "date": parsed_date,
        "description": description,
        "amount": amount,
        "currency": currency,
        "category": category,
    }


def analyze(text: str | None, default_currency: str = "KZT") -> dict:
    today = date.today().isoformat()
    user_note = text.strip() if text else ""
    prompt = (
        f"Сегодняшняя дата: {today}.\n"
        f"Валюта по умолчанию (если не определить иначе): {default_currency}.\n"
        f"Трата от пользователя: {user_note or '(нет)'}\n"
        "Определи трату и верни JSON."
    )
    parts = [types.Part.from_text(text=prompt)]
    response = _generate(parts)
    return _normalize(_extract_json(response.text), default_currency)


def _generate(parts: list):
    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM,
        max_output_tokens=1024,
        temperature=0,
        response_mime_type="application/json",
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    last_exc = None
    for pause in (*_RETRY_BACKOFF, None):
        try:
            return _client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[types.Content(role="user", parts=parts)],
                config=config,
            )
        except genai_errors.APIError as exc:
            if exc.code not in _RETRY_CODES:
                raise
            last_exc = exc
            if pause is None:
                break
            logger.warning("Gemini %s, повтор через %.0f c", exc.code, pause)
            time.sleep(pause)
    raise ModelOverloaded(str(last_exc)) from last_exc
