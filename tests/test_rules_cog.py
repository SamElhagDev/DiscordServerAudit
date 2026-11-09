import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from cogs.rules import Rules
from utils.rules_errors import RulesError


def _ctx():
    ctx = MagicMock()
    ctx.guild = SimpleNamespace(id=1)
    ctx.author = SimpleNamespace(id=2)
    ctx.send = AsyncMock()
    ctx.defer = AsyncMock()
    return ctx


class AutocompleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_autocomplete_empty_for_non_user_role(self):
        cog = Rules(SimpleNamespace())
        with patch("cogs.rules.member_has_user_role", return_value=False), \
             patch("cogs.rules.database.search_game_names") as search:
            inter = SimpleNamespace(guild=SimpleNamespace(id=1), user=object())
            self.assertEqual(await cog.game_autocomplete(inter, "ca"), [])
            search.assert_not_called()

    async def test_autocomplete_empty_in_dm(self):
        cog = Rules(SimpleNamespace())
        inter = SimpleNamespace(guild=None, user=object())
        self.assertEqual(await cog.game_autocomplete(inter, ""), [])

    async def test_autocomplete_returns_choices_for_user_role(self):
        cog = Rules(SimpleNamespace())
        with patch("cogs.rules.member_has_user_role", return_value=True), \
             patch("cogs.rules.database.search_game_names", return_value=["Catan"]):
            inter = SimpleNamespace(guild=SimpleNamespace(id=1), user=object())
            out = await cog.game_autocomplete(inter, "ca")
        self.assertEqual([(c.name, c.value) for c in out], [("Catan", "Catan")])

    async def test_autocomplete_truncates_to_25_and_100_chars(self):
        cog = Rules(SimpleNamespace())
        names = ["n" * 150] + [f"g{i}" for i in range(40)]
        with patch("cogs.rules.member_has_user_role", return_value=True), \
             patch("cogs.rules.database.search_game_names", return_value=names):
            inter = SimpleNamespace(guild=SimpleNamespace(id=1), user=object())
            out = await cog.game_autocomplete(inter, "")
        self.assertLessEqual(len(out), 25)
        self.assertLessEqual(len(out[0].name), 100)


class AddSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_file_and_url_rejected(self):
        cog = Rules(SimpleNamespace())
        ctx = _ctx()
        await cog.addsource.callback(cog, ctx, "G", file=object(), url="http://x")
        ctx.send.assert_awaited_once_with("❌ Provide either a file or a url, not both.",
            allowed_mentions=ANY)

    async def test_neither_rejected(self):
        cog = Rules(SimpleNamespace())
        ctx = _ctx()
        await cog.addsource.callback(cog, ctx, "G")
        ctx.send.assert_awaited_once_with("❌ Provide a file attachment or a url.",
            allowed_mentions=ANY)

    async def test_mime_checked_before_read(self):
        cog = Rules(SimpleNamespace())
        ctx = _ctx()
        f = SimpleNamespace(filename="a.exe", size=10, read=AsyncMock())
        with patch("cogs.rules.database.run", new=AsyncMock(side_effect=lambda fn, *a: fn(*a))), \
             patch("cogs.rules.rules_service.resolve_game", return_value={"id": 1, "name": "G"}), \
             patch("cogs.rules.rules_ingest.resolve_mime", side_effect=RulesError("bad type")):
            await cog.addsource.callback(cog, ctx, "G", file=f)
        f.read.assert_not_awaited()
        ctx.send.assert_awaited_with("bad type", allowed_mentions=ANY)

    async def test_rules_error_reported(self):
        cog = Rules(SimpleNamespace())
        ctx = _ctx()
        with patch("cogs.rules.database.run", new=AsyncMock(side_effect=RulesError("no such game"))):
            await cog.ask.callback(cog, ctx, "G", question="q")
        ctx.send.assert_awaited_with("no such game", allowed_mentions=ANY)

    async def test_rules_error_send_suppresses_mentions(self):
        cog = Rules(SimpleNamespace())
        ctx = _ctx()
        err = RulesError("no game @everyone <@&123>")
        with patch("cogs.rules.database.run", new=AsyncMock(side_effect=err)):
            await cog.ask.callback(cog, ctx, "@everyone", question="q")
        am = ctx.send.await_args.kwargs["allowed_mentions"]
        self.assertFalse(am.everyone)
        self.assertFalse(am.roles)
        self.assertFalse(am.users)
        self.assertFalse(am.replied_user)

    async def test_confirm_prompts_use_stored_name(self):
        cog = Rules(SimpleNamespace())
        ctx = _ctx()
        row = {"id": 1, "name": "Stored Name"}
        confirm = AsyncMock(return_value=False)
        cog._confirm = confirm
        with patch("cogs.rules.database.run", new=AsyncMock(side_effect=[row, []])),              patch("cogs.rules.rules_service.resolve_game", return_value=row):
            await cog.deletegame.callback(cog, ctx, "stored name")
        self.assertIn("Stored Name", confirm.await_args.args[1])
        self.assertNotIn("stored name", confirm.await_args.args[1])


class ErrorLoggingAndDeferTests(unittest.IsolatedAsyncioTestCase):
    async def test_rules_error_logged_as_warning(self):
        cog = Rules(SimpleNamespace())
        ctx = _ctx()
        ctx.command = SimpleNamespace(qualified_name="rules ask")
        with patch("cogs.rules.database.run", new=AsyncMock(side_effect=RulesError("no such game"))):
            with self.assertLogs("cogs.rules", level="WARNING") as cm:
                await cog.ask.callback(cog, ctx, "G", question="q")
        self.assertIn("rules ask", cm.output[0])
        self.assertIn("no such game", cm.output[0])

    async def test_addgame_defers_before_create(self):
        cog = Rules(SimpleNamespace())
        ctx = _ctx()
        order = []
        ctx.defer = AsyncMock(side_effect=lambda: order.append("defer"))

        async def create(*a):
            order.append("create")
            return {"name": "Catan"}
        with patch("cogs.rules.rules_service.create_game", new=create):
            await cog.addgame.callback(cog, ctx, name="Catan")
        self.assertEqual(order, ["defer", "create"])


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tree_registration(self):
        from cogs import rules
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        await rules.setup(bot)
        group = bot.tree.get_command("rules")
        names = {c.name for c in group.commands}
        self.assertEqual(names, {"games", "addgame", "addsource", "ask", "sources", "removesource", "deletegame"})
        for n in ("addsource", "ask", "sources", "removesource", "deletegame"):
            sub = group.get_command(n)
            param = next(p for p in sub.parameters if p.name == "game")
            self.assertIsNotNone(param.autocomplete)
        await bot.close()


if __name__ == "__main__":
    unittest.main()
