import unittest

import expense_parser


class ExpenseParserTest(unittest.TestCase):
    def test_restaurant_with_explicit_currency(self):
        result = expense_parser.parse_expense("Ресторан Plov 4500 тенге", "USD")

        self.assertTrue(result.parsed)
        self.assertEqual(result.entry["amount"], 4500)
        self.assertEqual(result.entry["currency"], "KZT")
        self.assertEqual(result.entry["category"], "Питание")
        self.assertEqual(result.entry["description"], "Ресторан «Plov»")

    def test_default_currency_without_explicit_currency(self):
        result = expense_parser.parse_expense("Такси 1200", "KGS")

        self.assertTrue(result.parsed)
        self.assertEqual(result.entry["amount"], 1200)
        self.assertEqual(result.entry["currency"], "KGS")
        self.assertEqual(result.entry["category"], "Прочее")
        self.assertEqual(result.entry["description"], "Такси")

    def test_gas_category_and_usd(self):
        result = expense_parser.parse_expense("Заправка 30 долларов", "KZT")

        self.assertTrue(result.parsed)
        self.assertEqual(result.entry["currency"], "USD")
        self.assertEqual(result.entry["category"], "Бензин")
        self.assertEqual(result.entry["description"], "Заправка")

    def test_hotel_description(self):
        result = expense_parser.parse_expense("отель Nomad 100 USD", "KZT")

        self.assertTrue(result.parsed)
        self.assertEqual(result.entry["currency"], "USD")
        self.assertEqual(result.entry["category"], "Отель")
        self.assertEqual(result.entry["description"], "Отель «Nomad»")

    def test_amount_only_uses_default_currency(self):
        result = expense_parser.parse_expense("1200", "KZT")

        self.assertTrue(result.parsed)
        self.assertEqual(result.entry["amount"], 1200)
        self.assertEqual(result.entry["currency"], "KZT")
        self.assertEqual(result.entry["category"], "Прочее")
        self.assertEqual(result.entry["description"], "Трата")

    def test_store_description(self):
        result = expense_parser.parse_expense("магазин 5000", "KZT")

        self.assertTrue(result.parsed)
        self.assertEqual(result.entry["description"], "Магазин")
        self.assertEqual(result.entry["category"], "Прочее")

    def test_no_amount_does_not_fallback(self):
        result = expense_parser.parse_expense("такси", "KZT")

        self.assertFalse(result.parsed)
        self.assertTrue(result.no_amount)
        self.assertFalse(result.should_fallback)

    def test_multiple_numbers_fallback(self):
        result = expense_parser.parse_expense("купил 2 хлеба 100 сом", "KZT")

        self.assertFalse(result.parsed)
        self.assertTrue(result.should_fallback)

    def test_date_like_text_fallback(self):
        result = expense_parser.parse_expense("вчера такси 1200", "KZT")

        self.assertFalse(result.parsed)
        self.assertTrue(result.should_fallback)


if __name__ == "__main__":
    unittest.main()
