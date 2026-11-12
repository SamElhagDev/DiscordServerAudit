"""Rulebook copies on disk, /gamerules download, and the answer card buttons."""
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.rules import Rules
from utils import rules_files, rules_service
from utils import rules_views as rv
from utils.rules_store import Citation, RulesAnswer


class _TempRoot(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        p = patch("utils.rules_files.storage_root", return_value=self.root)
        p.start()
        self.addCleanup(p.stop)


def _src(i=7, name="Base Rules.pdf", file_path=None, origin="upload", ref="Base Rules.pdf", size=1024):
    return {"id": i, "display_name": name, "file_path": file_path, "origin": origin,
            "origin_ref": ref, "size_bytes": size}


def _inter(user_id=2, limit=10 * 1048576):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id), guild=SimpleNamespace(id=1, filesize_limit=limit),
        response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock(),
                                 defer=AsyncMock(), is_done=lambda: False),
        followup=SimpleNamespace(send=AsyncMock()))


class FileStoreTests(_TempRoot):
    def test_save_uses_ids_and_allowed_extension_only(self):
        rel = rules_files.save(3, 9, "../../evil name.PDF", b"data")
        self.assertEqual(rel, "3/9.pdf")
        with open(os.path.join(self.root, "3", "9.pdf"), "rb") as fh:
            self.assertEqual(fh.read(), b"data")
        self.assertEqual(rules_files.relative_path(1, 2, "x.exe"), "1/2.bin")

    def test_resolve_rejects_paths_outside_root(self):
        self.assertIsNone(rules_files.resolve("../outside.pdf"))
        self.assertIsNone(rules_files.resolve(None))

    def test_existing_path_and_delete(self):
        rel = rules_files.save(1, 1, "a.txt", b"x")
        self.assertIsNotNone(rules_files.existing_path(rel))
        rules_files.delete(rel)
        self.assertIsNone(rules_files.existing_path(rel))
        rules_files.delete(rel)  # already gone: no error

    def test_delete_game_dir(self):
        rules_files.save(5, 1, "a.txt", b"x")
        rules_files.save(5, 2, "b.txt", b"y")
        rules_files.delete_game_dir(5)
        self.assertFalse(os.path.exists(os.path.join(self.root, "5")))


class DownloadTargetTests(_TempRoot):
    def test_file_link_none(self):
        rel = rules_files.save(1, 7, "a.pdf", b"x")
        self.assertEqual(rules_service.download_target(_src(file_path=rel))[0], "file")
        self.assertEqual(rules_service.download_target(_src(origin="url", ref="https://x/r.pdf")),
                         ("link", "https://x/r.pdf"))
        self.assertEqual(rules_service.download_target(_src()), ("none", None))

    def test_missing_file_falls_back_to_link(self):
        src = _src(file_path="1/404.pdf", origin="url", ref="https://x")
        self.assertEqual(rules_service.download_target(src), ("link", "https://x"))

    def test_safe_download_filename(self):
        self.assertEqual(rv.download_filename("TI4: Rules/Ref.pdf", "/x/7.pdf"), "Ref.pdf")
        self.assertEqual(rv.download_filename('Living "Rules"', "/x/7.pdf"), "Living _Rules_.pdf")
        self.assertEqual(rv.download_filename("", "/x/7.docx"), "rulebook.docx")


class DownloadViewTests(_TempRoot):
    GAMES = [{"id": 1, "name": "Catan", "source_count": 1}, {"id": 2, "name": "TI4", "source_count": 2}]

    async def test_for_guild_starts_with_game_dropdown_only(self):
        with patch("utils.rules_views.database.run", new=AsyncMock(return_value=self.GAMES)):
            view = await rv.DownloadView.for_guild(2, 1)
        self.assertEqual(len(view.children), 1)
        self.assertEqual([o.label for o in view.children[0].options], ["Catan", "TI4"])
        self.assertIn("Choose a game", view.intro_embed().description)

    async def test_no_games(self):
        with patch("utils.rules_views.database.run", new=AsyncMock(return_value=[])):
            view = await rv.DownloadView.for_guild(2, 1)
        self.assertEqual(view.children, [])
        self.assertIn("/gamerules add", view.intro_embed().description)

    async def test_choosing_game_adds_book_dropdown_with_states(self):
        rel = rules_files.save(2, 7, "a.pdf", b"x")
        books = [_src(7, "Living Rules", rel), _src(8, "FAQ", origin="url", ref="https://f"), _src(9, "Old")]
        with patch("utils.rules_views.database.run", new=AsyncMock(return_value=self.GAMES)):
            view = await rv.DownloadView.for_guild(2, 1)
        inter = _inter()
        with patch("utils.rules_views.database.run",
                   new=AsyncMock(side_effect=[self.GAMES[1], books])):
            await view.show_game(inter, 2)
        game_sel, book_sel = view.children
        self.assertTrue(next(o for o in game_sel.options if o.value == "2").default)
        self.assertEqual([(o.label, str(o.emoji)) for o in book_sel.options],
                         [("Living Rules", "📄"), ("FAQ", "🔗"), ("Old", "⚠️")])
        self.assertIn("ready to download", book_sel.options[0].description)
        embed = inter.response.edit_message.await_args.kwargs["embed"]
        self.assertIn("**TI4**", embed.description)

    async def _game_view(self, books):
        with patch("utils.rules_views.database.run", new=AsyncMock(return_value=books)):
            return await rv.DownloadView.for_game(2, 1, {"id": 2, "name": "TI4"})

    async def test_send_book_sends_private_file(self):
        rel = rules_files.save(2, 7, "a.pdf", b"%PDF")
        view = await self._game_view([_src(7, "Living Rules", rel)])
        inter = _inter()
        with patch("utils.rules_views.database.run", new=AsyncMock(return_value=_src(7, "Living Rules", rel))):
            await view.send_book(inter, 7)
        inter.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        kw = inter.followup.send.await_args.kwargs
        self.assertTrue(kw["ephemeral"])
        self.assertEqual(kw["file"].filename, "Living Rules.pdf")

    async def test_send_book_link_and_missing_copy(self):
        view = await self._game_view([])
        inter = _inter()
        with patch("utils.rules_views.database.run",
                   new=AsyncMock(return_value=_src(8, "FAQ", origin="url", ref="https://f/r.pdf"))):
            await view.send_book(inter, 8)
        self.assertIn("https://f/r.pdf", inter.response.send_message.await_args.kwargs["embed"].description)
        inter = _inter()
        with patch("utils.rules_views.database.run", new=AsyncMock(return_value=_src(9, "Old"))):
            await view.send_book(inter, 9)
        self.assertIn("Re-add it", inter.response.send_message.await_args.kwargs["embed"].description)

    async def test_send_book_over_upload_limit(self):
        rel = rules_files.save(2, 7, "a.pdf", b"x" * 2048)
        view = await self._game_view([])
        inter = _inter(limit=1024)
        with patch("utils.rules_views.database.run", new=AsyncMock(return_value=_src(7, "Big", rel))):
            await view.send_book(inter, 7)
        inter.followup.send.assert_not_awaited()
        self.assertIn("upload limit", inter.response.send_message.await_args.kwargs["embed"].description)

    async def test_only_owner_with_role_can_use(self):
        view = await self._game_view([])
        with patch("utils.rules_views.member_has_user_role", return_value=True):
            self.assertFalse(await view.interaction_check(_inter(user_id=99)))
            self.assertTrue(await view.interaction_check(_inter(user_id=2)))
        with patch("utils.rules_views.member_has_user_role", return_value=False):
            self.assertFalse(await view.interaction_check(_inter(user_id=2)))


class AnswerButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_answer_view_buttons_and_custom_ids(self):
        view = rv.answer_view(42, 3, True)
        self.assertIsNone(view.timeout)
        self.assertEqual([c.custom_id for c in view.children], ["gamerules:src:42", "gamerules:dl:3"])
        self.assertEqual([c.custom_id for c in rv.answer_view(None, 3, True).children], ["gamerules:dl:3"])

    async def test_sources_button_shows_saved_quotes_privately(self):
        btn = rv.SourcesButton(42)
        inter = _inter()
        saved = ("Retreat?", 3, [Citation("Living Rules", ["Retreat at the start of a round."])])
        with patch("utils.rules_views.member_has_user_role", return_value=True), \
                patch("utils.rules_views.rules_service.load_answer", new=AsyncMock(return_value=saved)), \
                patch("utils.rules_views.database.run", new=AsyncMock(return_value={"name": "TI4"})):
            await btn.callback(inter)
        kw = inter.response.send_message.await_args.kwargs
        self.assertTrue(kw["ephemeral"])
        self.assertEqual(kw["embed"].fields[0].name, "📄 Living Rules")

    async def test_sources_button_expired(self):
        inter = _inter()
        with patch("utils.rules_views.member_has_user_role", return_value=True), \
                patch("utils.rules_views.rules_service.load_answer", new=AsyncMock(return_value=None)):
            await rv.SourcesButton(1).callback(inter)
        self.assertIn("expired", inter.response.send_message.await_args.kwargs["embed"].description)

    async def test_buttons_require_user_role(self):
        for btn in (rv.SourcesButton(1), rv.DownloadButton(1)):
            inter = _inter()
            with patch("utils.rules_views.member_has_user_role", return_value=False):
                await btn.callback(inter)
            self.assertIn("user role", inter.response.send_message.await_args.kwargs["embed"].description)

    async def test_download_button_opens_picker_for_that_game(self):
        inter = _inter()
        game = {"id": 3, "name": "TI4"}
        with patch("utils.rules_views.member_has_user_role", return_value=True), \
                patch("utils.rules_views.database.run", new=AsyncMock(side_effect=[game, [_src()]])):
            await rv.DownloadButton(3).callback(inter)
        kw = inter.response.send_message.await_args.kwargs
        self.assertTrue(kw["ephemeral"])
        self.assertIsInstance(kw["view"], rv.DownloadView)
        self.assertEqual(len(kw["view"].children), 1)  # book dropdown only

    async def test_custom_id_templates_match(self):
        self.assertIsNotNone(rv.SourcesButton.__discord_ui_compiled_template__.fullmatch("gamerules:src:42"))
        self.assertIsNotNone(rv.DownloadButton.__discord_ui_compiled_template__.fullmatch("gamerules:dl:3"))


class AskCommandTests(unittest.IsolatedAsyncioTestCase):
    def _ctx(self):
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1), author=SimpleNamespace(id=2, display_name="Sam"),
                              send=AsyncMock(), defer=AsyncMock(), command=None)
        return ctx

    async def test_found_answer_posts_card_with_buttons(self):
        cog = Rules(SimpleNamespace())
        ctx = self._ctx()
        row = {"id": 3, "name": "TI4"}
        answer = RulesAnswer("Yes.", [Citation("Living Rules", ["q"])], True)
        with patch("cogs.rules.database.run", new=AsyncMock(side_effect=[row, [_src()]])), \
                patch("cogs.rules.rules_service.ask", new=AsyncMock(return_value=answer)), \
                patch("cogs.rules.rules_service.save_answer", new=AsyncMock(return_value=42)) as save:
            await cog.ask.callback(cog, ctx, "ti4", question="Retreat?")
        save.assert_awaited_once()
        kw = ctx.send.await_args.kwargs
        self.assertEqual(kw["embed"].title, "❓ Retreat?")
        self.assertEqual(kw["embed"].footer.text, "🎲 TI4 · asked by Sam")
        self.assertEqual([c.custom_id for c in kw["view"].children], ["gamerules:src:42", "gamerules:dl:3"])

    async def test_not_found_answer_has_no_buttons(self):
        cog = Rules(SimpleNamespace())
        ctx = self._ctx()
        with patch("cogs.rules.database.run", new=AsyncMock(side_effect=[{"id": 3, "name": "TI4"}, [_src()]])), \
                patch("cogs.rules.rules_service.ask", new=AsyncMock(return_value=RulesAnswer("", [], False))), \
                patch("cogs.rules.rules_service.save_answer", new=AsyncMock()) as save:
            await cog.ask.callback(cog, ctx, "ti4", question="Q")
        save.assert_not_awaited()
        self.assertIsNone(ctx.send.await_args.kwargs["view"])

    async def test_game_without_rulebooks_errors_privately_before_defer(self):
        cog = Rules(SimpleNamespace())
        ctx = self._ctx()
        with patch("cogs.rules.database.run", new=AsyncMock(side_effect=[{"id": 3, "name": "TI4"}, []])):
            await cog.ask.callback(cog, ctx, "ti4", question="Q")
        ctx.defer.assert_not_awaited()
        kw = ctx.send.await_args.kwargs
        self.assertTrue(kw["ephemeral"])
        self.assertIn("no rulebooks yet", kw["embed"].description)


class DownloadCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_opens_private_picker(self):
        cog = Rules(SimpleNamespace())
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1), author=SimpleNamespace(id=2),
                              interaction=object(), send=AsyncMock())
        with patch("utils.rules_views.database.run",
                   new=AsyncMock(return_value=[{"id": 1, "name": "Catan", "source_count": 1}])):
            await cog.download.callback(cog, ctx)
        kw = ctx.send.await_args.kwargs
        self.assertTrue(kw["ephemeral"])
        self.assertIsInstance(kw["view"], rv.DownloadView)

    async def test_prefix_fallback(self):
        cog = Rules(SimpleNamespace())
        ctx = SimpleNamespace(interaction=None, send=AsyncMock())
        await cog.download.callback(cog, ctx)
        self.assertIn("/gamerules download", ctx.send.await_args.args[0])

    async def test_cog_registers_persistent_buttons(self):
        bot = SimpleNamespace(add_dynamic_items=lambda *items: setattr(bot, "items", items))
        await Rules(bot).cog_load()
        self.assertEqual(bot.items, (rv.SourcesButton, rv.DownloadButton))


if __name__ == "__main__":
    unittest.main()
