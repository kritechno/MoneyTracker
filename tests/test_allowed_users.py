import importlib
import os
import shutil
import tempfile
import unittest


def _reload_settings():
    """Перезагружает settings, чтобы подхватить SETTINGS_PATH из env."""
    import settings

    importlib.reload(settings)
    return settings


class AllowedUsersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["SETTINGS_PATH"] = os.path.join(self.tmp, "settings.json")
        self.settings = _reload_settings()
        # Страховка: работаем только по временному settings.json.
        self.assertTrue(self.settings._PATH.startswith(self.tmp))

    def tearDown(self):
        os.environ.pop("SETTINGS_PATH", None)
        _reload_settings()  # вернуть настоящий путь остальным тестам
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_by_default(self):
        self.assertEqual(self.settings.get_allowed_user_ids(), set())

    def test_add_then_get(self):
        self.assertTrue(self.settings.add_allowed_user_id(123))
        self.assertEqual(self.settings.get_allowed_user_ids(), {123})

    def test_add_is_idempotent(self):
        self.assertTrue(self.settings.add_allowed_user_id(123))
        self.assertFalse(self.settings.add_allowed_user_id(123))
        self.assertEqual(self.settings.get_allowed_user_ids(), {123})

    def test_remove(self):
        self.settings.add_allowed_user_id(123)
        self.settings.add_allowed_user_id(456)
        self.assertTrue(self.settings.remove_allowed_user_id(123))
        self.assertFalse(self.settings.remove_allowed_user_id(123))
        self.assertEqual(self.settings.get_allowed_user_ids(), {456})

    def test_persists_across_reload(self):
        self.settings.add_allowed_user_id(789)
        self.settings = _reload_settings()  # эмулируем перезапуск бота
        self.assertIn(789, self.settings.get_allowed_user_ids())

    def test_ignores_garbage_in_file(self):
        self.settings.add_allowed_user_id(123)
        # Подкладываем мусор — get не должен падать, только валидные id.
        data = self.settings._load()
        data["allowed_user_ids"] = [123, "456", "x", None]
        self.settings._save(data)
        self.assertEqual(self.settings.get_allowed_user_ids(), {123, 456})


if __name__ == "__main__":
    unittest.main()
