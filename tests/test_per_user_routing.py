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


def _exp(desc, amt, cur="USD", cat="Прочее"):
    return {"date": "2026-07-25", "description": desc, "amount": amt,
            "currency": cur, "category": cat, "amount_usd": amt}


class PerUserRoutingTest(unittest.TestCase):
    """Проверяет, что contextvar (settings.set_current_uid) маршрутизирует
    все операции без явного profile в тур именно текущего гида — это ядро
    перехода на per-guide state."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["EXCEL_PATH"] = os.path.join(self.tmp, "expenses.xlsx")
        os.environ["SETTINGS_PATH"] = os.path.join(self.tmp, "settings.json")
        self.s, self.store = _reload_stack()
        self.A, self.B = 111, 222

    def tearDown(self):
        self.s.set_current_uid(None)
        os.environ.pop("EXCEL_PATH", None)
        os.environ.pop("SETTINGS_PATH", None)
        _reload_stack()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_route_to_each_users_active_tour(self):
        self.store.create_profile("Alpha")
        self.store.create_profile("Beta")
        self.s.set_active_profile("Alpha", uid=self.A)
        self.s.set_active_profile("Beta", uid=self.B)

        self.s.set_current_uid(self.A)
        self.assertEqual(self.s.get_active_profile(), "Alpha")  # no uid → contextvar
        self.store.add_expense(_exp("A lunch", 100, "USD"))      # no profile → Alpha

        self.s.set_current_uid(self.B)
        self.assertEqual(self.s.get_active_profile(), "Beta")
        self.store.add_expense(_exp("B taxi", 50, "USD"))         # no profile → Beta

        self.assertEqual(self.store.compute_totals(profile="Alpha")["count"], 1)
        self.assertEqual(self.store.compute_totals(profile="Alpha")["per_currency"]["USD"], 100)
        self.assertEqual(self.store.compute_totals(profile="Beta")["count"], 1)
        self.assertEqual(self.store.compute_totals(profile="Beta")["per_currency"]["USD"], 50)

    def test_one_users_switch_does_not_move_another(self):
        self.store.create_profile("Alpha")
        self.store.create_profile("Beta")
        self.s.set_active_profile("Alpha", uid=self.A)
        self.s.set_active_profile("Beta", uid=self.B)

        self.s.set_current_uid(self.A)
        self.s.set_active_profile("Beta")           # A switches (contextvar → A)
        self.assertEqual(self.s.get_active_profile(self.A), "Beta")
        self.assertEqual(self.s.get_active_profile(self.B), "Beta")  # B untouched

    def test_default_currency_isolated_with_global_fallback(self):
        self.s.set_current_uid(None)
        self.s.set_default_currency("KGS")          # global
        self.s.set_current_uid(self.A)
        self.s.set_default_currency("TJS")          # A personal
        self.assertEqual(self.s.get_default_currency(), "TJS")
        self.s.set_current_uid(self.B)
        self.assertEqual(self.s.get_default_currency(), "KGS")  # fallback

    def test_created_tour_is_owned_by_current_user(self):
        self.s.set_current_uid(self.A)
        self.store.create_profile("A tour")
        self.assertEqual(self.s.profile_owner("A tour"), self.A)
        self.assertEqual(self.s.list_profiles(self.A), ["A tour"])
        self.assertEqual(self.s.list_profiles(self.B), [])

    def test_new_guest_has_no_active_tour(self):
        self.s.set_current_uid(self.A)
        self.assertIsNone(self.s.get_active_profile())
        self.assertIsNone(self.s.get_active_file())


if __name__ == "__main__":
    unittest.main()
