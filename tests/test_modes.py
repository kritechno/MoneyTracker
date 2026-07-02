import unittest
from types import SimpleNamespace

import bot


def _ctx():
    return SimpleNamespace(user_data={})


def _msg(reply_to_id=None):
    reply = SimpleNamespace(message_id=reply_to_id) if reply_to_id else None
    return SimpleNamespace(reply_to_message=reply)


class TakeModeTest(unittest.TestCase):
    """Режимы «жду следующее сообщение» одноразовые, с TTL и привязкой к промпту."""

    def test_no_mode(self):
        self.assertIsNone(bot._take_mode(_ctx(), _msg()))

    def test_fresh_mode_consumed_once(self):
        ctx = _ctx()
        bot._arm_mode(ctx, "start_balance", prompt_id=10)
        mode = bot._take_mode(ctx, _msg())
        self.assertEqual(mode["kind"], "start_balance")
        # Одноразовость: второе сообщение уже не попадает в режим.
        self.assertIsNone(bot._take_mode(ctx, _msg()))

    def test_expired_mode_dropped(self):
        ctx = _ctx()
        bot._arm_mode(ctx, "profile_name", prompt_id=10)
        ctx.user_data["mode"]["ts"] -= bot._MODE_TTL + 1
        self.assertIsNone(bot._take_mode(ctx, _msg()))

    def test_expired_mode_honored_for_direct_reply(self):
        ctx = _ctx()
        bot._arm_mode(ctx, "set_balance", prompt_id=10)
        ctx.user_data["mode"]["ts"] -= bot._MODE_TTL + 1
        mode = bot._take_mode(ctx, _msg(reply_to_id=10))
        self.assertEqual(mode["kind"], "set_balance")

    def test_reply_to_other_message_respects_ttl(self):
        ctx = _ctx()
        bot._arm_mode(ctx, "edit", data={"id": 1}, prompt_id=10)
        ctx.user_data["mode"]["ts"] -= bot._MODE_TTL + 1
        self.assertIsNone(bot._take_mode(ctx, _msg(reply_to_id=99)))

    def test_mode_carries_data(self):
        ctx = _ctx()
        bot._arm_mode(ctx, "wallet_for", data="Тур 2027", prompt_id=5)
        mode = bot._take_mode(ctx, _msg(reply_to_id=5))
        self.assertEqual(mode["data"], "Тур 2027")


if __name__ == "__main__":
    unittest.main()
