import asyncio
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.genai import errors

from utils import rules_store as rs
from utils.rules_errors import RulesError


def _fake_client():
    return SimpleNamespace(aio=SimpleNamespace(
        file_search_stores=SimpleNamespace(
            create=AsyncMock(),
            delete=AsyncMock(),
            upload_to_file_search_store=AsyncMock(),
            documents=SimpleNamespace(delete=AsyncMock()),
        ),
        operations=SimpleNamespace(get=AsyncMock()),
        models=SimpleNamespace(generate_content=AsyncMock()),
    ))


class InterpretTests(unittest.TestCase):
    def test_text_without_chunks_is_not_found(self):
        a = rs.interpret_response("Robber moves on a 7.", [], 3)
        self.assertFalse(a.found)
        self.assertEqual(a.citations, [])

    def test_sentinel_is_not_found(self):
        self.assertFalse(rs.interpret_response("NOT_IN_RULES", [("Rules", "x")], 3).found)

    def test_empty_text_is_not_found(self):
        self.assertFalse(rs.interpret_response("  ", [("A", "x")], 3).found)

    def test_citations_deduped_capped_truncated(self):
        chunks = [("A", "x" * 400), ("A", "x" * 400), ("B", "y"), ("C", "z"), ("D", "w")]
        a = rs.interpret_response("ans", chunks, 3)
        self.assertTrue(a.found)
        self.assertEqual([c.title for c in a.citations], ["A", "B", "C"])
        self.assertEqual(len(a.citations[0].snippet), 301)

    def test_empty_title_defaults(self):
        a = rs.interpret_response("ans", [("", "x")], 3)
        self.assertEqual(a.citations[0].title, "Rulebook")

    def test_build_config_restricts_to_store(self):
        cfg = rs.build_config("fileSearchStores/abc", "Catan")
        self.assertEqual(cfg.tools[0].file_search.file_search_store_names,
                         ["fileSearchStores/abc"])
        self.assertIn('"Catan"', cfg.system_instruction)
        self.assertEqual(cfg.temperature, 0)
        self.assertIsNone(cfg.thinking_config)

    def test_build_config_sets_thinking_level(self):
        cfg = rs.build_config("fileSearchStores/abc", "Catan", "low")
        self.assertEqual(cfg.thinking_config.thinking_level, rs.types.ThinkingLevel.LOW)
        self.assertIsNone(rs.build_config("s", "g", "").thinking_config)


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_polls_until_done_and_cleans_temp_file(self):
        c = _fake_client()
        seen = {}

        async def upload(**kw):
            seen.update(kw)
            seen["existed"] = os.path.exists(kw["file"])
            return SimpleNamespace(done=False, error=None)

        c.aio.file_search_stores.upload_to_file_search_store.side_effect = upload
        c.aio.operations.get.side_effect = [
            SimpleNamespace(done=False, error=None),
            SimpleNamespace(done=True, error=None, response=SimpleNamespace(
                document_name="fileSearchStores/abc/documents/d1")),
        ]
        with patch("utils.rules_store.get_client", return_value=c):
            name = await rs.upload_document(
                "fileSearchStores/abc", b"data", "../evil.pdf", "application/pdf",
                timeout_s=5, poll_seconds=0)
        self.assertEqual(name, "fileSearchStores/abc/documents/d1")
        self.assertTrue(seen["existed"])
        self.assertFalse(os.path.exists(seen["file"]))
        self.assertTrue(seen["file"].endswith(".pdf"))
        self.assertEqual(seen["config"]["display_name"], "../evil.pdf")

    async def test_upload_times_out_and_cleanup_deletes_document(self):
        c = _fake_client()
        pending = SimpleNamespace(name="op1", done=False, error=None)
        finished = SimpleNamespace(
            name="op1", done=True, error=None,
            response=SimpleNamespace(document_name="fileSearchStores/s/documents/d9"))
        c.aio.file_search_stores.upload_to_file_search_store.return_value = pending
        # Polls during the upload window stay pending; later polls (cleanup) finish.
        calls = {"n": 0}

        async def get(op):
            calls["n"] += 1
            return pending if time.monotonic() < t_done[0] else finished

        t_done = [0]
        c.aio.operations.get.side_effect = get
        with patch("utils.rules_store.get_client", return_value=c):
            t_done[0] = time.monotonic() + 0.15
            with self.assertLogs("utils.rules_store", level="ERROR") as logs:
                with self.assertRaises(RulesError) as cm:
                    await rs.upload_document("s", b"d", "a.txt", "text/plain",
                                             timeout_s=0.05, poll_seconds=0.01)
            self.assertIn("timed out", cm.exception.user_message)
            self.assertTrue(any("op1" in m and "store s" in m for m in logs.output))
            self.assertEqual(len(rs._cleanup_tasks), 1)
            await asyncio.gather(*list(rs._cleanup_tasks))
        c.aio.file_search_stores.documents.delete.assert_awaited_once_with(
            name="fileSearchStores/s/documents/d9", config={"force": True})
        self.assertEqual(len(rs._cleanup_tasks), 0)

    async def test_cleanup_logs_error_when_window_elapses(self):
        c = _fake_client()
        pending = SimpleNamespace(name="op2", done=False, error=None)
        c.aio.operations.get.return_value = pending
        with patch("utils.rules_store.get_client", return_value=c):
            with self.assertLogs("utils.rules_store", level="ERROR") as logs:
                await rs._cleanup_orphan(pending, "s", 0.01, window_s=0.03)
        self.assertTrue(any("op2" in m for m in logs.output))
        c.aio.file_search_stores.documents.delete.assert_not_awaited()

    async def test_upload_done_without_response_raises(self):
        c = _fake_client()
        c.aio.file_search_stores.upload_to_file_search_store.return_value =             SimpleNamespace(name="op3", done=True, error=None, response=None)
        with patch("utils.rules_store.get_client", return_value=c):
            with self.assertLogs("utils.rules_store", level="ERROR"):
                with self.assertRaises(RulesError) as cm:
                    await rs.upload_document("s", b"d", "a.txt", "text/plain",
                                             timeout_s=1)
        self.assertIn("couldn't index", cm.exception.user_message)

    async def test_upload_operation_error(self):
        c = _fake_client()
        c.aio.file_search_stores.upload_to_file_search_store.return_value = \
            SimpleNamespace(done=True, error={"code": 3})
        with patch("utils.rules_store.get_client", return_value=c):
            with self.assertRaises(RulesError) as cm:
                await rs.upload_document("s", b"d", "a.txt", "text/plain", timeout_s=1)
        self.assertIn("couldn't index", cm.exception.user_message)

    async def test_ask_without_client_raises(self):
        with patch("utils.rules_store.get_client", return_value=None):
            with self.assertRaises(RulesError) as cm:
                await rs.ask("s", "g", "q", "m", 1, 3)
        self.assertIn("not configured", cm.exception.user_message)

    async def test_ask_parses_chunks(self):
        c = _fake_client()
        chunk = SimpleNamespace(retrieved_context=SimpleNamespace(title="T", text="body"))
        resp = SimpleNamespace(text="Answer", candidates=[SimpleNamespace(
            grounding_metadata=SimpleNamespace(grounding_chunks=[chunk, SimpleNamespace(retrieved_context=None)]))])
        c.aio.models.generate_content.return_value = resp
        with patch("utils.rules_store.get_client", return_value=c):
            a = await rs.ask("s", "g", "q", "m", 5, 3)
        self.assertTrue(a.found)
        self.assertEqual(a.citations[0].title, "T")

    async def test_ask_without_grounding_metadata(self):
        c = _fake_client()
        c.aio.models.generate_content.return_value = SimpleNamespace(
            text="Answer", candidates=[SimpleNamespace(grounding_metadata=None)])
        with patch("utils.rules_store.get_client", return_value=c):
            a = await rs.ask("s", "g", "q", "m", 5, 3)
        self.assertFalse(a.found)

    async def test_ask_failure_wrapped(self):
        c = _fake_client()
        c.aio.models.generate_content.side_effect = ValueError("boom")
        with patch("utils.rules_store.get_client", return_value=c):
            with self.assertRaises(RulesError) as cm:
                await rs.ask("s", "g", "q", "m", 5, 3)
        self.assertIn("failed", cm.exception.user_message)

    async def test_delete_document_tolerates_404(self):
        c = _fake_client()
        c.aio.file_search_stores.documents.delete.side_effect = errors.ClientError(404, {})
        with patch("utils.rules_store.get_client", return_value=c):
            await rs.delete_document("fileSearchStores/a/documents/b")

    async def test_delete_store_raises_other_errors(self):
        c = _fake_client()
        c.aio.file_search_stores.delete.side_effect = errors.ClientError(500, {})
        with patch("utils.rules_store.get_client", return_value=c):
            with self.assertRaises(errors.ClientError):
                await rs.delete_store("fileSearchStores/a")

    async def test_create_store(self):
        c = _fake_client()
        c.aio.file_search_stores.create.return_value = SimpleNamespace(name="fileSearchStores/x")
        with patch("utils.rules_store.get_client", return_value=c):
            self.assertEqual(await rs.create_store("Catan"), "fileSearchStores/x")
