import unittest

import currency


class CurrencyMatchTest(unittest.TestCase):
    def test_somoni_exact(self):
        self.assertEqual(currency.match_currency("сомони"), "TJS")
        self.assertEqual(currency.match_currency("tjs"), "TJS")

    def test_somoni_misspellings_are_tjs(self):
        # «сомани» (через «а») и другие формы сомони не должны уходить в KGS.
        for token in ("сомани", "сомон", "соман", "сомоні", "Сомани"):
            self.assertEqual(currency.match_currency(token), "TJS", token)

    def test_kyrgyz_som_is_kgs(self):
        for token in ("сом", "сома", "сомы", "сомов", "сомах", "kgs"):
            self.assertEqual(currency.match_currency(token), "KGS", token)

    def test_uzs_not_confused_with_som(self):
        self.assertEqual(currency.match_currency("сум"), "UZS")
        self.assertEqual(currency.match_currency("сума"), "UZS")

    def test_full_message_from_the_wild(self):
        # Реальный случай: «Ужин 700 сомани» записывался как 700 KGS.
        self.assertEqual(
            currency.parse_amounts("Ужин 700 сомани"), [(700.0, "TJS")]
        )

    def test_exchange_message_still_parses(self):
        self.assertEqual(
            currency.parse_amounts("поменял 300 долларов на 2775 сомони"),
            [(300.0, "USD"), (2775.0, "TJS")],
        )


if __name__ == "__main__":
    unittest.main()
