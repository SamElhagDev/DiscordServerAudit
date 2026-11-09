import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from utils import permissions

CFG = {"admin_role": "Bot Admin", "user_role": "Rancher"}


def _cfg():
    return patch("utils.permissions.config.get", side_effect=lambda k, d=None: CFG.get(k, d))


def _role(name, rid):
    return SimpleNamespace(name=name, id=rid)


class _Ctx:
    def __init__(self, guild, author):
        self.guild = guild
        self.author = author
        self.command = SimpleNamespace(qualified_name="rules")
        self.sent = []

    async def send(self, msg, **kw):
        self.sent.append((msg, kw))


class ResolveAndMemberTests(unittest.TestCase):
    def setUp(self):
        self.rancher = _role("Rancher", 1)
        self.admin = _role("Bot Admin", 2)
        self.guild = SimpleNamespace(name="G", id=9, roles=[self.rancher, self.admin])

    def _member(self, *roles):
        return SimpleNamespace(roles=list(roles))

    def test_user_role_member_allowed(self):
        with _cfg():
            self.assertTrue(permissions.member_has_user_role(self._member(self.rancher), self.guild))

    def test_admin_member_allowed(self):
        with _cfg():
            self.assertTrue(permissions.member_has_user_role(self._member(self.admin), self.guild))

    def test_plain_member_denied(self):
        with _cfg():
            self.assertFalse(permissions.member_has_user_role(self._member(), self.guild))

    def test_duplicate_user_role_fails_closed(self):
        dup = _role("Rancher", 3)
        guild = SimpleNamespace(name="G", id=9, roles=[self.rancher, dup, self.admin])
        role, reason = permissions.resolve_unique_role(guild, "Rancher")
        self.assertIsNone(role)
        self.assertEqual(reason, "ambiguous")
        with _cfg():
            self.assertFalse(permissions.member_has_user_role(self._member(self.rancher), guild))

    def test_missing_user_role_still_admits_admin(self):
        guild = SimpleNamespace(name="G", id=9, roles=[self.admin])
        with _cfg():
            self.assertTrue(permissions.member_has_user_role(self._member(self.admin), guild))

    def test_unconfigured_role_reason(self):
        self.assertEqual(permissions.resolve_unique_role(self.guild, None), (None, "unconfigured"))

    def test_missing_reason(self):
        self.assertEqual(permissions.resolve_unique_role(self.guild, "Nope"), (None, "missing"))


class GateTests(unittest.TestCase):
    def test_user_role_gate_denies_with_message(self):
        guild = SimpleNamespace(name="G", id=9, roles=[_role("Rancher", 1)])
        ctx = _Ctx(guild, SimpleNamespace(roles=[], id=5))
        with _cfg():
            ok = asyncio.run(_predicate(permissions.has_user_role(), ctx))
        self.assertFalse(ok)
        self.assertEqual(ctx.sent[0][0], "❌ You need the **Rancher** role to use rules commands.")
        self.assertTrue(ctx.sent[0][1]["ephemeral"])

    def test_user_role_gate_dm_denied(self):
        ctx = _Ctx(None, SimpleNamespace(roles=[], id=5))
        with _cfg():
            self.assertFalse(asyncio.run(_predicate(permissions.has_user_role(), ctx)))

    def test_user_role_gate_allows_admin(self):
        admin = _role("Bot Admin", 2)
        guild = SimpleNamespace(name="G", id=9, roles=[admin, _role("Rancher", 1)])
        ctx = _Ctx(guild, SimpleNamespace(roles=[admin], id=5))
        with _cfg():
            self.assertTrue(asyncio.run(_predicate(permissions.has_user_role(), ctx)))

    def test_user_role_gate_ambiguous_message(self):
        guild = SimpleNamespace(name="G", id=9, roles=[_role("Rancher", 1), _role("Rancher", 3)])
        ctx = _Ctx(guild, SimpleNamespace(roles=[], id=5))
        with _cfg():
            self.assertFalse(asyncio.run(_predicate(permissions.has_user_role(), ctx)))
        self.assertIn("Multiple roles are named **Rancher**", ctx.sent[0][0])
        self.assertTrue(ctx.sent[0][0].endswith("before rules commands will work."))

    def test_user_role_gate_admits_admin_when_user_role_duplicated(self):
        admin = _role("Bot Admin", 2)
        guild = SimpleNamespace(name="G", id=9, roles=[_role("Rancher", 1), _role("Rancher", 3), admin])
        ctx = _Ctx(guild, SimpleNamespace(roles=[admin], id=5))
        with _cfg():
            self.assertTrue(asyncio.run(_predicate(permissions.has_user_role(), ctx)))
        self.assertEqual(ctx.sent, [])

    def test_user_role_gate_unconfigured_message(self):
        guild = SimpleNamespace(name="G", id=9, roles=[_role("Bot Admin", 2)])
        ctx = _Ctx(guild, SimpleNamespace(roles=[], id=5))
        cfg = {"admin_role": "Bot Admin"}
        with patch("utils.permissions.config.get", side_effect=lambda k, d=None: cfg.get(k, d)):
            self.assertFalse(asyncio.run(_predicate(permissions.has_user_role(), ctx)))
        self.assertEqual(ctx.sent[0][0], "❌ `user_role` is not configured in config.yaml.")

    def test_admin_gate_unchanged_denial(self):
        guild = SimpleNamespace(name="G", id=9, roles=[_role("Bot Admin", 2)])
        ctx = _Ctx(guild, SimpleNamespace(roles=[], id=5))
        with _cfg():
            self.assertFalse(asyncio.run(_predicate(permissions.has_admin_role(), ctx)))
        self.assertEqual(ctx.sent[0][0], "❌ You need the **Bot Admin** role to use this command.")


async def _predicate(decorator, ctx):
    """Extract the predicate from a commands.check decorator by applying it to a stub."""
    async def stub(*a, **k):
        pass
    decorated = decorator(stub)
    pred = decorated.__commands_checks__[0]
    return await pred(ctx)


if __name__ == "__main__":
    unittest.main()
