"""Гид без активного тура не должен «терять» деньги.

До этих проверок /add у нового гида отвечал «✅ Пополнил кошелёк», а движение
уходило в файл-пустышку `.no-active-tour` (его потом не читает даже openpyxl —
расширения нет). Обмен и /startbalance падали с InvalidFileException. Здесь
закрыты оба уровня: хендлеры в bot.py и подстраховка в _atomic_save.
"""

import asyncio
import importlib
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace


def _reload_stack():
    import config
    import settings
    import excel_store
    importlib.reload(config)
    importlib.reload(settings)
    importlib.reload(excel_store)
    return settings, excel_store


class _Msg:
    """Минимальная замена telegram.Message: копит ответы бота."""

    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return SimpleNamespace(message_id=1)


class _Query:
    """Минимальная замена CallbackQuery."""

    def __init__(self):
        self.answers = []

    async def answer(self, text=None, **kw):
        self.answers.append(text)


class NoTourGuardTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["EXCEL_PATH"] = os.path.join(self.tmp, "expenses.xlsx")
        os.environ["SETTINGS_PATH"] = os.path.join(self.tmp, "settings.json")
        self.s, self.store = _reload_stack()
        self.GUIDE = 999
        self.s.set_current_uid(self.GUIDE)  # гид без единого тура

    def tearDown(self):
        self.s.set_current_uid(None)
        os.environ.pop("EXCEL_PATH", None)
        os.environ.pop("SETTINGS_PATH", None)
        _reload_stack()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _files(self):
        return sorted(os.listdir(self.tmp))

    # --- уровень хранилища ---------------------------------------------------

    def test_wallet_write_without_tour_raises_and_creates_nothing(self):
        with self.assertRaises(self.store.NoActiveTour):
            self.store.add_movement("Пополнение", "USD", 5000)
        self.assertNotIn(self.store.NO_TOUR_FILE, self._files())

    def test_exchange_and_start_balance_without_tour_raise(self):
        with self.assertRaises(self.store.NoActiveTour):
            self.store.add_exchange("USD", 100, "KGS", 8700)
        with self.assertRaises(self.store.NoActiveTour):
            self.store.set_start_balance([(500.0, "USD")])
        self.assertNotIn(self.store.NO_TOUR_FILE, self._files())

    def test_reads_without_tour_stay_safe(self):
        """Чтения должны молча возвращать пусто, а не падать."""
        self.assertEqual(sum(self.store.wallet_net().values()), 0.0)
        self.assertEqual(
            sum(self.store.compute_totals()["per_currency"].values()), 0.0
        )
        self.assertEqual(self._files(), [])

    def test_writes_work_once_the_guide_has_a_tour(self):
        """Защита не должна мешать нормальной работе."""
        self.store.create_profile("Тур гида")
        self.s.set_active_profile("Тур гида", self.GUIDE)
        net = self.store.add_movement("Пополнение", "USD", 5000)
        self.assertEqual(net["USD"], 5000)
        self.assertNotIn(self.store.NO_TOUR_FILE, self._files())

    # --- уровень хендлеров ---------------------------------------------------

    async def test_add_cmd_without_tour_warns_and_writes_nothing(self):
        import bot
        msg = _Msg()
        ctx = SimpleNamespace(args=["5000", "USD"], user_data={})
        await bot.add_cmd(SimpleNamespace(message=msg), ctx)
        self.assertEqual(msg.replies, [bot._NO_TOUR_MSG])
        self.assertNotIn(self.store.NO_TOUR_FILE, self._files())

    async def test_exchange_text_without_tour_warns_instead_of_preview(self):
        import bot
        msg = _Msg()
        handled = await bot._maybe_handle_exchange(msg, "поменял 100 долларов 8700 сом")
        self.assertTrue(handled)  # съедено обменом, не уйдёт в трату
        self.assertEqual(msg.replies, [bot._NO_TOUR_MSG])
        self.assertNotIn(self.store.NO_TOUR_FILE, self._files())

    async def test_start_balance_prompt_without_tour_warns(self):
        import bot
        msg = _Msg()
        ctx = SimpleNamespace(user_data={})
        await bot._prompt_start_balance(msg, ctx)
        self.assertEqual(msg.replies, [bot._NO_TOUR_MSG])

    async def test_set_balance_prompt_without_tour_warns(self):
        import bot
        msg = _Msg()
        ctx = SimpleNamespace(user_data={})
        await bot._prompt_set_balance(msg, ctx)
        self.assertEqual(msg.replies, [bot._NO_TOUR_MSG])

    async def test_add_cmd_still_works_with_a_tour(self):
        import bot
        self.store.create_profile("Тур гида")
        self.s.set_active_profile("Тур гида", self.GUIDE)
        msg = _Msg()
        ctx = SimpleNamespace(args=["5000", "USD"], user_data={})
        await bot.add_cmd(SimpleNamespace(message=msg), ctx)
        self.assertTrue(msg.replies and "Пополнил кошелёк" in msg.replies[0])


if __name__ == "__main__":
    unittest.main()
