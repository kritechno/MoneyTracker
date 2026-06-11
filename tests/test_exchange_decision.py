import os
import unittest

# bot.py импортирует config, который требует токены в окружении. Для юнит-тестов
# чистой логики обмена достаточно любых непустых значений — сеть не дёргается.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot  # noqa: E402
import currency  # noqa: E402


def _decide(text: str) -> str:
    """Повторяет решение handle_text: есть ли глагол обмена и сколько распознано
    пар (сумма+валюта). Возвращает exchange / ask / expense."""
    if not bot._ACTION_RE.search(text):
        return "expense"
    return bot._action_kind(currency.parse_amounts(text))


class ExchangeDecisionTest(unittest.TestCase):
    def test_two_currencies_is_exchange(self):
        self.assertEqual(_decide("поменял 100 долларов на 48000 тенге"), "exchange")
        self.assertEqual(_decide("обменял 30 долларов 15000 тенге"), "exchange")
        self.assertEqual(_decide("разменял 67 долларов 5360 сом"), "exchange")

    def test_half_formed_exchange_asks_not_records(self):
        # Одна сторона без валюты — раньше уходило в трату-призрак, теперь спрашиваем.
        self.assertEqual(_decide("поменял 100 долларов на 48000"), "ask")
        self.assertEqual(_decide("поменял 100 долларов"), "ask")

    def test_action_word_without_currency_is_normal_expense(self):
        # «поменял» в смысле «заменил» — это обычная трата, не обмен валюты.
        self.assertEqual(_decide("поменял колесо 5000"), "expense")
        self.assertEqual(_decide("замена масла 8000"), "expense")

    def test_action_kind_pure(self):
        self.assertEqual(bot._action_kind([(1, "USD"), (2, "KZT")]), "exchange")
        self.assertEqual(bot._action_kind([(1, "USD")]), "ask")
        self.assertEqual(bot._action_kind([]), "expense")


if __name__ == "__main__":
    unittest.main()
