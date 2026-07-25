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


class UserStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["EXCEL_PATH"] = os.path.join(self.tmp, "expenses.xlsx")
        os.environ["SETTINGS_PATH"] = os.path.join(self.tmp, "settings.json")
        self.settings, self.store = _reload_stack()
        self.A, self.B = 111, 222  # два гида

    def tearDown(self):
        os.environ.pop("EXCEL_PATH", None)
        os.environ.pop("SETTINGS_PATH", None)
        _reload_stack()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_active_profile_is_per_user(self):
        self.store.create_profile("Tour A")
        self.store.create_profile("Tour B")
        self.settings.set_active_profile("Tour A", uid=self.A)
        self.settings.set_active_profile("Tour B", uid=self.B)
        # Каждый гид на своём туре; переключение одного не трогает другого.
        self.assertEqual(self.settings.get_active_profile(self.A), "Tour A")
        self.assertEqual(self.settings.get_active_profile(self.B), "Tour B")
        self.settings.set_active_profile("Tour A", uid=self.B)
        self.assertEqual(self.settings.get_active_profile(self.A), "Tour A")
        self.assertEqual(self.settings.get_active_profile(self.B), "Tour A")

    def test_no_uid_is_global_legacy(self):
        # Старый API без uid работает как прежде (глобальный активный тур).
        self.store.create_profile("Tour A")
        self.settings.set_active_profile("Tour A")
        self.assertEqual(self.settings.get_active_profile(), "Tour A")

    def test_new_user_has_no_active(self):
        self.assertIsNone(self.settings.get_active_profile(self.A))
        self.assertIsNone(self.settings.get_active_file(self.A))

    def test_default_currency_per_user_with_global_fallback(self):
        self.settings.set_default_currency("KGS")           # глобальный дефолт
        self.settings.set_default_currency("TJS", uid=self.A)
        self.assertEqual(self.settings.get_default_currency(self.A), "TJS")
        self.assertEqual(self.settings.get_default_currency(self.B), "KGS")  # фолбэк
        self.assertEqual(self.settings.get_default_currency(), "KGS")

    def test_ownership_filters_profile_list(self):
        self.settings.register_profile("A owns", "A owns.xlsx", owner_uid=self.A)
        self.settings.register_profile("B owns", "B owns.xlsx", owner_uid=self.B)
        self.assertEqual(self.settings.list_profiles(self.A), ["A owns"])
        self.assertEqual(self.settings.list_profiles(self.B), ["B owns"])
        self.assertTrue(self.settings.owns(self.A, "A owns"))
        self.assertFalse(self.settings.owns(self.A, "B owns"))
        # Админ/legacy (uid=None) видит все.
        self.assertIn("A owns", self.settings.list_profiles())
        self.assertIn("B owns", self.settings.list_profiles())

    def test_register_preserves_owner_on_overwrite(self):
        self.settings.register_profile("Tour", "Tour.xlsx", owner_uid=self.A)
        self.settings.register_profile("Tour", "Tour.xlsx")  # миграция файла
        self.assertEqual(self.settings.profile_owner("Tour"), self.A)

    def test_set_profile_owner_reassigns(self):
        self.settings.register_profile("Tour", "Tour.xlsx", owner_uid=self.A)
        self.assertTrue(self.settings.set_profile_owner("Tour", self.B))
        self.assertEqual(self.settings.profile_owner("Tour"), self.B)

    def test_delete_profile_clears_personal_active(self):
        self.store.create_profile("Tour A")
        self.settings.set_active_profile("Tour A", uid=self.A)
        self.assertTrue(self.settings.delete_profile("Tour A"))
        self.assertIsNone(self.settings.get_active_profile(self.A))

    def test_atomic_save_leaves_no_temp_files(self):
        self.settings.set_active_profile("FDTG tour 2026", uid=self.A)
        leftovers = [f for f in os.listdir(self.tmp) if ".tmp" in f]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
