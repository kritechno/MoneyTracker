"""Сценарий как на проде: владелец (админ) ведёт тур Памир, приходит второй гид.

Проверяем четыре требования:
  1. владелец видит ВСЕ туры (в т.ч. созданные гидами);
  2. владелец остаётся на своём активном туре, что бы ни делал гид;
  3. новый тур гида принадлежит гиду;
  4. гид не видит туров владельца.
"""
import asyncio
import importlib
import os
import shutil
import tempfile
import types
import unittest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

OWNER = 398944142   # админ из ALLOWED_USER_IDS (как на проде)
GUIDE = 424313526   # гид, добавленный через /allow
TOURS = ["FDTG tour 2026", "ЦАР 06.2026", "11 перевалов июль 2026",
         "Дни в Бишкеке", "Памир ТяньШань 2026"]
ACTIVE = "Памир ТяньШань 2026"


def _user(uid):
    return types.SimpleNamespace(id=uid)


class MultiGuideLiveSetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["EXCEL_PATH"] = os.path.join(self.tmp, "expenses.xlsx")
        os.environ["SETTINGS_PATH"] = os.path.join(self.tmp, "settings.json")
        import config, settings, excel_store, bot
        importlib.reload(config)
        importlib.reload(settings)
        importlib.reload(excel_store)
        importlib.reload(bot)
        self.s, self.store, self.bot = settings, excel_store, bot
        # Прод-режим доступа: админы заданы через ALLOWED_USER_IDS.
        self.bot.ALLOWED_USER_IDS = {OWNER}
        # Состояние «до обновления»: туры без владельцев, один общий активный.
        for name in TOURS:
            self.store.create_profile(name)
        self.s.set_active_profile(ACTIVE)          # глобальный (legacy) активный

    def tearDown(self):
        self.s.set_current_uid(None)
        os.environ.pop("EXCEL_PATH", None)
        os.environ.pop("SETTINGS_PATH", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_owner_keeps_pamir_active_after_upgrade(self):
        # Первое сообщение владельца после обновления: подхватывает свой тур.
        asyncio.run(self.bot._adopt_global_tour(OWNER))
        self.assertEqual(self.s.get_active_profile(OWNER), ACTIVE)

    def test_owner_sees_all_tours_including_guide_created(self):
        asyncio.run(self.bot._adopt_global_tour(OWNER))
        # Гид создаёт свой тур.
        self.s.set_current_uid(GUIDE)
        self.store.create_profile("Гид Тур 2026")
        self.s.set_current_uid(None)

        visible = asyncio.run(self.bot._visible_profiles(_user(OWNER)))
        for name in TOURS:
            self.assertIn(name, visible)
        self.assertIn("Гид Тур 2026", visible)      # требование 1

    def test_guide_switching_does_not_move_owner(self):
        asyncio.run(self.bot._adopt_global_tour(OWNER))
        # Гид создаёт тур и переключается на него.
        self.s.set_current_uid(GUIDE)
        self.store.create_profile("Гид Тур 2026")
        self.s.set_active_profile("Гид Тур 2026")
        # Гид даже пробует встать на тур владельца.
        self.s.set_active_profile(ACTIVE)
        self.s.set_current_uid(None)

        # Владелец всё ещё на Памире.                требование 2
        self.assertEqual(self.s.get_active_profile(OWNER), ACTIVE)
        # И его записи идут в Памир, а не в тур гида.
        self.s.set_current_uid(OWNER)
        self.assertEqual(self.s.get_active_profile(), ACTIVE)
        self.s.set_current_uid(None)

    def test_guide_new_tour_is_owned_by_guide(self):
        self.s.set_current_uid(GUIDE)
        self.store.create_profile("Гид Тур 2026")
        self.s.set_current_uid(None)
        self.assertEqual(self.s.profile_owner("Гид Тур 2026"), GUIDE)  # требование 3

    def test_guide_sees_only_own_tours(self):
        self.s.set_current_uid(GUIDE)
        self.store.create_profile("Гид Тур 2026")
        self.s.set_current_uid(None)
        visible = asyncio.run(self.bot._visible_profiles(_user(GUIDE)))
        self.assertEqual(visible, ["Гид Тур 2026"])                     # требование 4

    def test_guide_gets_no_tour_prompt_before_creating(self):
        # Гид — не админ, глобальный тур не «усыновляет».
        asyncio.run(self.bot._adopt_global_tour(GUIDE))
        self.assertIsNone(self.s.get_active_profile(GUIDE))
        self.assertEqual(asyncio.run(self.bot._visible_profiles(_user(GUIDE))), [])

    def test_assign_makes_tour_visible_to_guide_and_keeps_owner_view(self):
        # Админ отдаёт существующий тур гиду — тур появляется у гида, админ видит всё.
        self.s.set_profile_owner("Дни в Бишкеке", GUIDE)
        self.assertEqual(
            asyncio.run(self.bot._visible_profiles(_user(GUIDE))), ["Дни в Бишкеке"]
        )
        owner_view = asyncio.run(self.bot._visible_profiles(_user(OWNER)))
        self.assertIn("Дни в Бишкеке", owner_view)
        self.assertEqual(len(owner_view), len(TOURS))
        # И снятие владельца возвращает тур в «ничьи» (виден только админу).
        self.s.set_profile_owner("Дни в Бишкеке", None)
        self.assertEqual(asyncio.run(self.bot._visible_profiles(_user(GUIDE))), [])

    def test_known_user_ids_lists_admin_and_guides(self):
        self.s.add_allowed_user_id(GUIDE)
        ids = self.bot._known_user_ids()
        self.assertIn(OWNER, ids)   # админ из ALLOWED_USER_IDS
        self.assertIn(GUIDE, ids)   # гид из /allow

    def test_import_filename_maps_to_tour_name(self):
        self.assertEqual(
            self.store.profile_name_from_filename("Памир ТяньШань 2026.xlsx"), ACTIVE
        )

    def test_guide_cannot_hijack_owner_tour_by_typing_its_name(self):
        # Гид вводит название существующего (не своего) тура — занято, не активируем.
        self.assertTrue(self.s.profile_exists(ACTIVE))
        self.assertFalse(self.s.owns(GUIDE, ACTIVE))
        self.assertFalse(GUIDE in self.bot.ALLOWED_USER_IDS)

    def test_owner_expense_lands_in_pamir_while_guide_writes_elsewhere(self):
        asyncio.run(self.bot._adopt_global_tour(OWNER))
        self.s.set_current_uid(GUIDE)
        self.store.create_profile("Гид Тур 2026")
        self.s.set_active_profile("Гид Тур 2026")
        self.store.add_expense({"date": "2026-07-25", "description": "Гид обед",
                                "amount": 40, "currency": "USD",
                                "category": "Питание", "amount_usd": 40})
        self.s.set_current_uid(OWNER)
        self.store.add_expense({"date": "2026-07-25", "description": "Мой ужин",
                                "amount": 700, "currency": "TJS",
                                "category": "Питание", "amount_usd": 75.8})
        self.s.set_current_uid(None)

        pamir = self.store.compute_totals(profile=ACTIVE)
        guide = self.store.compute_totals(profile="Гид Тур 2026")
        self.assertEqual(pamir["count"], 1)
        self.assertEqual(pamir["per_currency"]["TJS"], 700)
        self.assertEqual(guide["count"], 1)
        self.assertEqual(guide["per_currency"]["USD"], 40)
        self.assertEqual(pamir["per_currency"]["USD"], 0)  # не перемешалось


if __name__ == "__main__":
    unittest.main()
