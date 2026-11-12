"""Tests for utils.rules_service against a temp SQLite DB and a fake store."""
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import config
import database
from database import connection
from utils import rules_service as svc
from utils.rules_errors import RulesError
from utils.rules_store import Citation, RulesAnswer


async def _inline_run(fn, *args, **kwargs):
    # Run DB calls on the test thread so the thread-local temp connection is used
    # (pool threads would keep a stale connection to an earlier test's file).
    return fn(*args, **kwargs)


class RulesServiceTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._orig_db_path = connection.DB_PATH
        connection.DB_PATH = self._db_path
        database.close_db()
        database.init_db()
        run_patch = patch("database.run", _inline_run)
        run_patch.start()
        self.addCleanup(run_patch.stop)
        self._files_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._files_dir, ignore_errors=True)
        root_patch = patch("utils.rules_files.storage_root", return_value=self._files_dir)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.fake = SimpleNamespace(
            create_store=AsyncMock(return_value="fileSearchStores/new"),
            upload_document=AsyncMock(return_value="fileSearchStores/new/documents/d1"),
            delete_document=AsyncMock(),
            delete_store=AsyncMock(),
            ask=AsyncMock(return_value="ANSWER"),
        )

    def tearDown(self):
        database.close_db()
        connection.DB_PATH = self._orig_db_path
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self._db_path + suffix)
            except OSError:
                pass

    async def _add(self, game, n=1):
        for i in range(n):
            await svc.add_source(game, b"x", f"f{i}.txt", "text/plain",
                                 "upload", f"f{i}.txt", 5, store=self.fake)

    async def test_create_game_creates_store_then_row(self):
        row = await svc.create_game(1, " Catan ", 99, store=self.fake)
        self.assertEqual(row["name"], "Catan")
        self.fake.create_store.assert_awaited_once()

    async def test_duplicate_checked_before_store_created(self):
        await svc.create_game(1, "Catan", 99, store=self.fake)
        self.fake.create_store.reset_mock()
        with self.assertRaises(RulesError):
            await svc.create_game(1, "catan", 99, store=self.fake)
        self.fake.create_store.assert_not_awaited()

    async def test_store_deleted_when_db_insert_fails(self):
        with patch("database.create_game", side_effect=RuntimeError("disk")):
            with self.assertRaises(RuntimeError):
                await svc.create_game(1, "Root", 99, store=self.fake)
        self.fake.delete_store.assert_awaited_once_with("fileSearchStores/new")

    async def test_race_duplicate_becomes_rules_error_and_store_deleted(self):
        with patch("database.create_game", side_effect=database.GameExistsError("Root")):
            with self.assertRaises(RulesError):
                await svc.create_game(1, "Root", 99, store=self.fake)
        self.fake.delete_store.assert_awaited_once_with("fileSearchStores/new")

    async def test_document_deleted_when_source_insert_fails(self):
        game = await svc.create_game(1, "Root", 99, store=self.fake)
        with patch("database.add_source", side_effect=RuntimeError("disk")):
            with self.assertRaises(RuntimeError):
                await self._add(game)
        self.fake.delete_document.assert_awaited_once_with("fileSearchStores/new/documents/d1")

    async def test_add_source_returns_count(self):
        game = await svc.create_game(1, "Root", 99, store=self.fake)
        r1 = await svc.add_source(game, b"x", "a.txt", "text/plain", "upload", "a", 5, store=self.fake)
        r2 = await svc.add_source(game, b"x", "b.txt", "text/plain", "upload", "b", 5, store=self.fake)
        self.assertEqual((r1, r2), (1, 2))

    async def test_ask_with_no_sources_raises(self):
        game = await svc.create_game(1, "Root", 99, store=self.fake)
        with self.assertRaises(RulesError) as cm:
            await svc.ask(game, "How?", store=self.fake)
        self.assertIn("has no rulebooks yet", cm.exception.user_message)
        self.fake.ask.assert_not_awaited()

    async def test_ask_passes_store_and_config(self):
        game = await svc.create_game(1, "Catan", 99, store=self.fake)
        await self._add(game)
        result = await svc.ask(game, "How many dice?", store=self.fake)
        self.assertEqual(result, "ANSWER")
        self.fake.ask.assert_awaited_once_with(
            "fileSearchStores/new", "Catan", "How many dice?",
            config.get("rules.model", "gemini-3.1-pro-preview"),
            config.get("rules.answer_timeout_seconds", 90),
            config.get("rules.max_citations", 3),
            thinking_level=config.get("rules.thinking_level", "low"))

    def _task_log(self, task_type):
        with connection.get_conn() as conn:
            return conn.execute(
                "SELECT * FROM bulk_task_log WHERE task_type = ?", (task_type,)).fetchall()

    async def test_remove_source_deletes_document_then_row(self):
        game = await svc.create_game(1, "Root", 99, store=self.fake)
        await self._add(game)
        src = database.list_sources(game["id"])[0]
        name = await svc.remove_source(game, src["id"], "tester#0", 1, store=self.fake)
        self.assertEqual(name, src["display_name"])
        self.fake.delete_document.assert_awaited_once_with(src["document_name"])
        self.assertEqual(list(database.list_sources(game["id"])), [])
        self.assertEqual(len(self._task_log("rules_remove_source")), 1)

    async def test_remove_source_keeps_row_when_gemini_fails(self):
        game = await svc.create_game(1, "Root", 99, store=self.fake)
        await self._add(game)
        src = database.list_sources(game["id"])[0]
        self.fake.delete_document.side_effect = RulesError("x")
        with self.assertRaises(RulesError):
            await svc.remove_source(game, src["id"], "t", 1, store=self.fake)
        self.assertEqual(len(database.list_sources(game["id"])), 1)

    async def test_remove_source_from_other_game_rejected(self):
        a = await svc.create_game(1, "A", 99, store=self.fake)
        b = await svc.create_game(1, "B", 99, store=self.fake)
        await self._add(a)
        src = database.list_sources(a["id"])[0]
        with self.assertRaises(RulesError) as cm:
            await svc.remove_source(b, src["id"], "t", 1, store=self.fake)
        self.assertIn("no longer in", cm.exception.user_message)
        self.fake.delete_document.assert_not_awaited()

    async def test_delete_game_deletes_store_then_rows(self):
        game = await svc.create_game(1, "Root", 99, store=self.fake)
        await self._add(game, 2)
        n = await svc.delete_game(game, "t", 1, store=self.fake)
        self.assertEqual(n, 2)
        self.fake.delete_store.assert_awaited_once_with("fileSearchStores/new")
        self.assertIsNone(database.get_game(1, "Root"))
        self.assertEqual(len(self._task_log("rules_delete_game")), 1)

    def test_resolve_game_unknown_raises(self):
        with self.assertRaises(RulesError) as cm:
            svc.resolve_game(1, "Nope")
        self.assertIn("No game named **Nope**", cm.exception.user_message)

    def test_resolve_game_found(self):
        database.create_game(1, "Catan", "s", 1)
        self.assertEqual(svc.resolve_game(1, "catan")["name"], "Catan")

    # --- local rulebook copies -------------------------------------------

    async def test_add_source_keeps_a_downloadable_copy(self):
        game = await svc.create_game(1, "Catan", 99, store=self.fake)
        await svc.add_source(game, b"%PDF-1", "Rules.pdf", "application/pdf",
                             "upload", "Rules.pdf", 5, store=self.fake)
        src = database.list_sources(game["id"])[0]
        self.assertEqual(src["file_path"], f"{game['id']}/{src['id']}.pdf")
        kind, path = svc.download_target(src)
        self.assertEqual(kind, "file")
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), b"%PDF-1")

    async def test_copy_failure_still_adds_rulebook(self):
        game = await svc.create_game(1, "Catan", 99, store=self.fake)
        with patch("utils.rules_files.save", side_effect=OSError("disk full")), \
                self.assertLogs("utils.rules_service", level="ERROR"):
            count = await svc.add_source(game, b"x", "a.txt", "text/plain", "upload", "a.txt", 5,
                                         store=self.fake)
        self.assertEqual(count, 1)
        self.assertIsNone(database.list_sources(game["id"])[0]["file_path"])

    async def test_remove_source_deletes_copy(self):
        game = await svc.create_game(1, "Catan", 99, store=self.fake)
        await self._add(game)
        src = database.list_sources(game["id"])[0]
        path = svc.download_target(src)[1]
        await svc.remove_source(game, src["id"], "t", 1, store=self.fake)
        self.assertFalse(os.path.exists(path))

    async def test_delete_game_deletes_its_folder(self):
        game = await svc.create_game(1, "Catan", 99, store=self.fake)
        await self._add(game, 2)
        await svc.delete_game(game, "t", 1, store=self.fake)
        self.assertFalse(os.path.exists(os.path.join(self._files_dir, str(game["id"]))))

    async def test_answer_citations_round_trip_and_are_guild_scoped(self):
        game = await svc.create_game(1, "Catan", 99, store=self.fake)
        answer = RulesAnswer("A", [Citation("Base", ["q1", "q2"])], True)
        aid = await svc.save_answer(1, game, "How?", answer)
        question, game_id, cites = await svc.load_answer(1, aid)
        self.assertEqual((question, game_id), ("How?", game["id"]))
        self.assertEqual(cites, [Citation("Base", ["q1", "q2"])])
        self.assertIsNone(await svc.load_answer(2, aid))

    def test_old_answers_pruned(self):
        old = database.save_answer(1, 1, "old", "[]")
        with database.connection.get_conn() as conn:
            conn.execute("UPDATE rules_answers SET created_at = '2000-01-01T00:00:00' WHERE id = ?", (old,))
        database.save_answer(1, 1, "new", "[]")
        self.assertIsNone(database.get_answer(1, old))

    def test_game_by_id_is_guild_scoped(self):
        gid = database.create_game(1, "Catan", "s", 1)
        self.assertEqual(database.get_game_by_id(1, gid)["name"], "Catan")
        self.assertIsNone(database.get_game_by_id(2, gid))
