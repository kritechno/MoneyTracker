import json
from datetime import date

from google import genai
from google.genai import types

from config import CATEGORIES, CURRENCIES, GEMINI_API_KEY, GEMINI_MODEL

_client = genai.Client(api_key=GEMINI_API_KEY)

_SYSTEM = f"""Ты — ассистент по учёту трат. На вход поступает фото чека и/или подпись пользователя.
Определи трату и верни СТРОГО один JSON-объект без пояснений и markdown.

Поля:
- "date": дата траты в формате YYYY-MM-DD. Бери из чека, если её нет — используй переданную сегодняшнюю дату.
- "description": краткое описание траты на русском.
    * Для ресторана/кафе пиши: Ресторан «Название». Если названия нет — просто: Ресторан.
      НЕ перечисляй блюда — только название заведения.
    * Для заправки: Заправка «Название» (или просто Заправка).
    * Для отеля: Отель «Название» (или просто Отель).
    * Для прочего — короткое осмысленное описание (магазин, такси, и т.п.).
- "amount": итоговая сумма (число, без валютного знака, итог по чеку).
- "currency": один из {CURRENCIES}. Определи по символам/тексту чека (₸/тг/тенге=KZT, сом=KGS, $/USD=USD).
    Помни: большие суммы (тысячи и десятки тысяч) для еды/воды/музея — это почти всегда тенге (KZT), а не доллары.
    Если валюту определить невозможно — используй валюту по умолчанию, указанную в сообщении пользователя.
- "category": один из {CATEGORIES}. Ресторан/кафе/еда -> Питание. Отель/гостиница -> Отель.
    Заправка/топливо -> Бензин. Остальное -> Прочее.

Подпись пользователя имеет приоритет над содержимым чека, если они противоречат.
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


def analyze(
    image_bytes: bytes | None,
    media_type: str | None,
    caption: str | None,
    default_currency: str = "KZT",
) -> dict:
    today = date.today().isoformat()
    user_note = caption.strip() if caption else ""
    prompt = (
        f"Сегодняшняя дата: {today}.\n"
        f"Валюта по умолчанию (если не определить иначе): {default_currency}.\n"
        f"Подпись пользователя: {user_note or '(нет)'}\n"
        "Определи трату и верни JSON."
    )

    parts = []
    if image_bytes:
        parts.append(
            types.Part.from_bytes(data=image_bytes, mime_type=media_type or "image/jpeg")
        )
    parts.append(types.Part.from_text(text=prompt))

    response = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM,
            max_output_tokens=1024,
            temperature=0,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return _normalize(_extract_json(response.text), default_currency)
