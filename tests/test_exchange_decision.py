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


def _buttons(kb):
    return [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]


class ExchangeUndoConfirmTest(unittest.TestCase):
    """Реальный обмен двигает деньги, поэтому отмена/возврат не должны срабатывать
    одним кликом — раньше случайный тап по старой кнопке откатывал реальный обмен."""

    gave, received = (100.0, "USD"), (48700.0, "KZT")

    def test_applied_keyboard_does_not_move_money_in_one_tap(self):
        # Под записанным обменом — только запрос подтверждения (exunq), не сам возврат.
        btns = _buttons(bot._exchange_primary_kb("applied", self.gave, self.received))
        self.assertEqual(len(btns), 1)
        tag = btns[0][1].split("|")[0]
        self.assertEqual(tag, "exunq")
        self.assertNotIn(tag, ("exun", "exap"))

    def test_undo_confirm_has_yes_and_no(self):
        btns = _buttons(bot._exchange_confirm_kb("undo", self.gave, self.received))
        tags = [cb.split("|")[0] for _, cb in btns]
        self.assertEqual(tags, ["exun", "exsa"])  # «Да» двигает деньги, «Нет» — нет

    def test_reversed_keyboard_asks_before_reapply(self):
        btns = _buttons(bot._exchange_primary_kb("reversed", self.gave, self.received))
        self.assertEqual(btns[0][1].split("|")[0], "exapq")

    def test_redo_confirm_has_yes_and_no(self):
        btns = _buttons(bot._exchange_confirm_kb("redo", self.gave, self.received))
        tags = [cb.split("|")[0] for _, cb in btns]
        self.assertEqual(tags, ["exap", "exsr"])


class MovementGroupingTest(unittest.TestCase):
    """Список движений кошелька склеивает две ноги обмена в одну запись."""

    def test_exchange_legs_grouped(self):
        rows = [
            {"id": 1, "kind": "Пополнение", "currency": "USD", "amount": 6000, "note": ""},
            {"id": 2, "kind": "Обмен", "currency": "USD", "amount": -100, "note": "→ 48,700.00 KZT"},
            {"id": 3, "kind": "Обмен", "currency": "KZT", "amount": 48700, "note": "← 100.00 USD"},
        ]
        groups = bot._group_movements(rows)
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups[0]["legs"]), 1)   # Пополнение
        self.assertEqual(len(groups[1]["legs"]), 2)   # обе ноги обмена вместе
        self.assertEqual(groups[1]["id"], 2)          # удаление по id первой ноги
        self.assertIn("USD", bot._movement_label(groups[1]))
        self.assertIn("KZT", bot._movement_label(groups[1]))

    def test_single_topup_label(self):
        rows = [{"id": 5, "kind": "Пополнение", "currency": "USD", "amount": 500, "note": ""}]
        groups = bot._group_movements(rows)
        self.assertEqual(len(groups), 1)
        self.assertIn("+500", bot._movement_label(groups[0]))


if __name__ == "__main__":
    unittest.main()
