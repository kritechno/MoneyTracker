import importlib
import os
import shutil
import tempfile
import unittest


def _reload_stack():
    import config
    import settings
    import excel_store

    importlib.reload(config)
    importlib.reload(settings)
    importlib.reload(excel_store)
    return settings, excel_store


class WalletMovementsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["EXCEL_PATH"] = os.path.join(self.tmp, "expenses.xlsx")
        os.environ["SETTINGS_PATH"] = os.path.join(self.tmp, "settings.json")
        self.settings, self.store = _reload_stack()
        self.assertTrue(self.store.EXCEL_PATH.startswith(self.tmp))

    def tearDown(self):
        os.environ.pop("EXCEL_PATH", None)
        os.environ.pop("SETTINGS_PATH", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        _reload_stack()

    def test_movements_get_unique_ids(self):
        self.store.add_movement("Пополнение", "USD", 100)
        self.store.add_movement("Пополнение", "KZT", 5000)
        ids = [m["id"] for m in self.store.list_movements()]
        self.assertEqual(len(ids), 2)
        self.assertTrue(all(isinstance(i, int) for i in ids))
        self.assertEqual(len(set(ids)), 2)  # уникальны

    def test_delete_single_movement(self):
        self.store.add_movement("Пополнение", "USD", 100)
        self.store.add_movement("Пополнение", "USD", 40)
        movements = self.store.list_movements()
        deleted = self.store.delete_movement(movements[0]["id"])
        self.assertEqual(len(deleted), 1)
        self.assertEqual(self.store.wallet_net()["USD"], 40.0)

    def test_delete_unknown_id_returns_none(self):
        self.store.add_movement("Пополнение", "USD", 100)
        self.assertIsNone(self.store.delete_movement(99999))

    def test_delete_exchange_removes_both_legs(self):
        self.store.add_movement("Пополнение", "USD", 1000)
        self.store.add_exchange("USD", 100, "KZT", 48700)
        before = self.store.wallet_net()
        self.assertEqual(before["USD"], 900.0)
        self.assertEqual(before["KZT"], 48700.0)

        legs = [m for m in self.store.list_movements() if m["kind"] == "Обмен"]
        self.assertEqual(len(legs), 2)
        deleted = self.store.delete_movement(legs[0]["id"])  # одну ногу...
        self.assertEqual(len(deleted), 2)  # ...удаляет обе

        after = self.store.wallet_net()
        self.assertEqual(after["USD"], 1000.0)  # обмен полностью откатан
        self.assertEqual(after["KZT"], 0.0)
        self.assertEqual([m["kind"] for m in self.store.list_movements()], ["Пополнение"])

    def test_deleting_one_exchange_keeps_other(self):
        self.store.add_exchange("USD", 100, "KZT", 48700)
        self.store.add_exchange("USD", 200, "KZT", 97300)
        legs2 = [m for m in self.store.list_movements()
                 if m["currency"] == "USD" and m["amount"] == -200]
        self.assertEqual(len(legs2), 1)
        self.store.delete_movement(legs2[0]["id"])
        net = self.store.wallet_net()
        # Удалён только второй обмен; первый цел.
        self.assertEqual(net["USD"], -100.0)
        self.assertEqual(net["KZT"], 48700.0)

    def test_reversal_pair_deletes_together(self):
        # Точный сценарий ЦАР: ошибочный «Возврат» обмена 100 USD <-> 48 700 KZT.
        self.store.add_movement("Пополнение", "USD", 6000)
        self.store.add_exchange("USD", 100, "KZT", 48700)
        self.store.add_exchange("KZT", 48700, "USD", 100, "Возврат")  # лишний откат
        self.assertEqual(self.store.wallet_net()["USD"], 6000.0)  # 5900 + 100 (возврат)

        rev = [m for m in self.store.list_movements() if m["kind"] == "Возврат"]
        self.assertEqual(len(rev), 2)
        self.store.delete_movement(rev[0]["id"])
        net = self.store.wallet_net()
        self.assertEqual(net["USD"], 5900.0)   # лишние +100 ушли
        self.assertEqual(net["KZT"], 48700.0)


if __name__ == "__main__":
    unittest.main()
