import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from cogs.rules import AddGameModal, AddSourceModal, Rules
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
        with patch("cogs.rules.database.run", new=AsyncMock(side_effect=[row, []])), \
                patch("cogs.rules.rules_service.resolve_game", return_value=row):
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


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tree_registration(self):
        from cogs import rules
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        await rules.setup(bot)
        group = bot.tree.get_command("rules")
        names = {c.name for c in group.commands}
        self.assertEqual(names, {"games", "addgame", "addsource", "ask", "sources", "removesource", "deletegame"})
        for n in ("addgame", "addsource"):
            self.assertEqual(list(group.get_command(n).parameters), [])
        for n in ("ask", "sources", "removesource", "deletegame"):
            sub = group.get_command(n)
            param = next(p for p in sub.parameters if p.name == "game")
            self.assertIsNotNone(param.autocomplete)
        await bot.close()


def _slash_ctx():
    ctx = _ctx()
    ctx.interaction = SimpleNamespace(response=SimpleNamespace(send_modal=AsyncMock()))
    return ctx


def _inter(msg=None):
    msg = msg or SimpleNamespace(edit=AsyncMock())
    return SimpleNamespace(
        guild=SimpleNamespace(id=1), user=SimpleNamespace(id=2),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock(return_value=msg)), msg=msg)


class FormCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_addgame_prefix_fallback(self):
        cog = Rules(SimpleNamespace())
        ctx = _ctx()
        ctx.interaction = None
        await cog.addgame.callback(cog, ctx)
        ctx.send.assert_awaited_once_with("Use `/rules addgame` — it opens a form.",
                                          allowed_mentions=ANY)

    async def test_addsource_prefix_fallback(self):
        cog = Rules(SimpleNamespace())
        ctx = _ctx()
        ctx.interaction = None
        await cog.addsource.callback(cog, ctx)
        ctx.send.assert_awaited_once_with("Use `/rules addsource` — it opens a form.",
                                          allowed_mentions=ANY)

    async def test_addgame_opens_modal(self):
        cog = Rules(SimpleNamespace())
        ctx = _slash_ctx()
        await cog.addgame.callback(cog, ctx)
        modal = ctx.interaction.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, AddGameModal)
        self.assertLessEqual(len(modal.title), 45)

    async def test_addsource_no_games_replies_ephemeral(self):
        cog = Rules(SimpleNamespace())
        ctx = _slash_ctx()
        with patch("cogs.rules.database.run", new=AsyncMock(return_value=[])):
            await cog.addsource.callback(cog, ctx)
        ctx.interaction.response.send_modal.assert_not_awaited()
        ctx.send.assert_awaited_once_with("No games yet. Add one with `/rules addgame`.",
                                          allowed_mentions=ANY, ephemeral=True)

    async def test_addsource_opens_modal_with_dropdown_capped_at_25(self):
        cog = Rules(SimpleNamespace())
        ctx = _slash_ctx()
        rows = [{"name": f"G{i}"} for i in range(40)]
        with patch("cogs.rules.database.run", new=AsyncMock(return_value=rows)):
            await cog.addsource.callback(cog, ctx)
        modal = ctx.interaction.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, AddSourceModal)
        self.assertEqual(len(modal.game.component.options), 25)
        self.assertLessEqual(len(modal.children), 5)


class ModalSubmitTests(unittest.IsolatedAsyncioTestCase):
    def _gamemodal(self, name="Catan", files=(), link=""):
        m = AddGameModal()
        m.name.component._value = name
        m.files.component._values = list(files)
        m.link.component._value = link
        return m

    async def test_non_user_role_rejected(self):
        inter = _inter()
        with patch("cogs.rules.member_has_user_role", return_value=False), \
                patch("cogs.rules.rules_forms.process_submission", new=AsyncMock()) as proc:
            await self._gamemodal().on_submit(inter)
        proc.assert_not_awaited()
        inter.response.send_message.assert_awaited_once()
        self.assertTrue(inter.response.send_message.await_args.kwargs["ephemeral"])

    async def test_addgame_submit_passes_inputs_and_reports(self):
        inter = _inter()
        f = SimpleNamespace(filename="a.pdf", size=1)
        res = SimpleNamespace(render=lambda: "DONE")
        with patch("cogs.rules.member_has_user_role", return_value=True), \
                patch("cogs.rules.rules_forms.process_submission",
                      new=AsyncMock(return_value=res)) as proc:
            await self._gamemodal(files=[f], link="https://x").on_submit(inter)
        inter.response.defer.assert_awaited_once()
        kw = proc.await_args.kwargs
        self.assertEqual((kw["game_name"], kw["files"], kw["url"]),
                         ("Catan", [f], "https://x"))
        self.assertEqual(inter.msg.edit.await_args.kwargs["content"], "DONE")
        self.assertIn("allowed_mentions", inter.msg.edit.await_args.kwargs)

    async def test_submit_error_shown(self):
        inter = _inter()
        with patch("cogs.rules.member_has_user_role", return_value=True), \
                patch("cogs.rules.rules_forms.process_submission",
                      new=AsyncMock(side_effect=RulesError("dup"))):
            await self._gamemodal().on_submit(inter)
        self.assertEqual(inter.msg.edit.await_args.kwargs["content"], "dup")

    async def test_unexpected_error_edits_progress_message_and_logs(self):
        inter = _inter()
        add = AsyncMock()
        with patch("cogs.rules.member_has_user_role", return_value=True), \
                patch("utils.rules_forms.rules_service.create_game",
                      new=AsyncMock(side_effect=RuntimeError("gemini down"))), \
                patch("utils.rules_forms.rules_service.add_source", new=add), \
                self.assertLogs("cogs.rules", level="ERROR") as cm:
            await self._gamemodal().on_submit(inter)
        add.assert_not_awaited()
        edit = inter.msg.edit.await_args.kwargs
        self.assertEqual(edit["content"],
                         "❌ Something went wrong adding that. The error has been logged.")
        self.assertFalse(edit["allowed_mentions"].everyone)
        self.assertIn("guild=1", cm.output[0])
        self.assertIn("user=2", cm.output[0])

    async def test_addsource_no_game_selected(self):
        inter = _inter()
        m = AddSourceModal(["Catan"])
        m.game.component._values = []
        with patch("cogs.rules.member_has_user_role", return_value=True), \
                patch("cogs.rules.database.run", new=AsyncMock()) as run:
            await m.on_submit(inter)
        run.assert_not_awaited()
        self.assertIn("Choose a game", inter.response.send_message.await_args.args[0])

    async def test_addsource_resolve_game_error_shown_after_defer(self):
        inter = _inter()
        m = AddSourceModal(["Catan"])
        m.game.component._values = ["Catan"]
        with patch("cogs.rules.member_has_user_role", return_value=True), \
                patch("cogs.rules.database.run",
                      new=AsyncMock(side_effect=RulesError("no such game"))), \
                patch("cogs.rules.rules_forms.process_submission", new=AsyncMock()) as proc:
            await m.on_submit(inter)
        inter.response.defer.assert_awaited_once()
        self.assertEqual(inter.followup.send.await_args.args[0], "no such game")
        proc.assert_not_awaited()

    async def test_addsource_defers_once_before_resolve(self):
        inter = _inter()
        order = []
        inter.response.defer = AsyncMock(side_effect=lambda **k: order.append("defer"))

        async def run(fn, *a):
            order.append("resolve")
            return {"id": 1, "name": "Catan"}
        m = AddSourceModal(["Catan"])
        m.game.component._values = ["Catan"]
        with patch("cogs.rules.member_has_user_role", return_value=True), \
                patch("cogs.rules.database.run", new=run), \
                patch("cogs.rules.rules_forms.process_submission",
                      new=AsyncMock(return_value=SimpleNamespace(render=lambda: "ok"))):
            await m.on_submit(inter)
        self.assertEqual(order, ["defer", "resolve"])

    async def test_addsource_non_user_role_rejected(self):
        inter = _inter()
        m = AddSourceModal(["Catan"])
        m.game.component._values = ["Catan"]
        with patch("cogs.rules.member_has_user_role", return_value=False), \
                patch("cogs.rules.database.run", new=AsyncMock()) as run, \
                patch("cogs.rules.rules_forms.process_submission", new=AsyncMock()) as proc:
            await m.on_submit(inter)
        run.assert_not_awaited()
        proc.assert_not_awaited()
        self.assertTrue(inter.response.send_message.await_args.kwargs["ephemeral"])

    async def test_addsource_submit_resolves_selected_game(self):
        inter = _inter()
        m = AddSourceModal(["Catan", "Risk"])
        m.game.component._values = ["Risk"]
        m.link.component._value = "https://x"
        res = SimpleNamespace(render=lambda: "DONE")
        row = {"id": 1, "name": "Risk"}
        with patch("cogs.rules.member_has_user_role", return_value=True), \
                patch("cogs.rules.database.run", new=AsyncMock(return_value=row)), \
                patch("cogs.rules.rules_forms.process_submission",
                      new=AsyncMock(return_value=res)) as proc:
            await m.on_submit(inter)
        self.assertEqual(proc.await_args.kwargs["game"], row)


if __name__ == "__main__":
    unittest.main()
