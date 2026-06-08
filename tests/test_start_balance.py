import importlib
import os
import shutil
import tempfile
import unittest


def _reload_stack():
    """Перезагружает config → settings → excel_store, чтобы подхватить пути из env."""
    import config
    import settings
    import excel_store

    importlib.reload(config)
    importlib.reload(settings)
    importlib.reload(excel_store)
    return settings, excel_store


class StartBalanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["EXCEL_PATH"] = os.path.join(self.tmp, "expenses.xlsx")
        os.environ["SETTINGS_PATH"] = os.path.join(self.tmp, "settings.json")
        self.settings, self.store = _reload_stack()
        # Страховка: ни в коем случае не работаем по реальному файлу трат.
        self.assertTrue(self.store.EXCEL_PATH.startswith(self.tmp))

    def tearDown(self):
        os.environ.pop("EXCEL_PATH", None)
        os.environ.pop("SETTINGS_PATH", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        _reload_stack()  # вернуть настоящие пути остальным тестам

    def test_set_then_get(self):
        self.store.set_start_balance([(100, "USD"), (5000, "KZT")])
        start = self.store.get_start_balance()
        self.assertEqual(start["USD"], 100.0)
        self.assertEqual(start["KZT"], 5000.0)

    def test_replace_not_append(self):
        self.store.set_start_balance([(100, "USD"), (5000, "KZT")])
        self.store.set_start_balance([(200, "USD")])
        start = self.store.get_start_balance()
        self.assertEqual(start["USD"], 200.0)
        self.assertEqual(start["KZT"], 0.0)  # прежний «Старт» по KZT убран

    def test_top_ups_survive_correction(self):
        self.store.set_start_balance([(100, "USD")])
        self.store.add_movement("Пополнение", "USD", 50)
        # Правка начального баланса не должна стирать пополнение.
        net = self.store.set_start_balance([(200, "USD")])
        self.assertEqual(self.store.get_start_balance()["USD"], 200.0)
        self.assertEqual(net["USD"], 250.0)  # 200 (старт) + 50 (пополнение)

    def test_clear_to_zero_keeps_top_ups(self):
        self.store.set_start_balance([(100, "USD")])
        self.store.add_movement("Пополнение", "USD", 50)
        net = self.store.set_start_balance([])
        self.assertEqual(self.store.get_start_balance()["USD"], 0.0)
        self.assertEqual(net["USD"], 50.0)

    def test_merges_duplicate_currencies(self):
        self.store.set_start_balance([(100, "USD"), (50, "USD")])
        self.assertEqual(self.store.get_start_balance()["USD"], 150.0)


if __name__ == "__main__":
    unittest.main()
