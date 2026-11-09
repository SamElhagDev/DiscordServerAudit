import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from utils import rules_forms
from utils.rules_errors import RulesError

GAME = {"id": 7, "name": "Catan", "guild_id": 1, "store_name": "s"}


def _file(name, size=10, data=b"x"):
    return SimpleNamespace(filename=name, size=size, read=AsyncMock(return_value=data))


class _Session:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *a):
        return False


def _patches(**over):
    ps = {
        "create": patch("utils.rules_forms.rules_service.create_game",
                        new=AsyncMock(return_value=GAME)),
        "add": patch("utils.rules_forms.rules_service.add_source",
                     new=AsyncMock(side_effect=[1, 2, 3, 4])),
        "check": patch("utils.rules_forms.rules_ingest.check_public_url"),
        "session": patch("utils.rules_forms.rules_ingest.create_fetch_session",
                         new=lambda t: _Session()),
        "fetch": patch("utils.rules_forms.rules_ingest.fetch_url",
                       new=AsyncMock(return_value=(b"d", "web.pdf", "application/pdf"))),
    }
    ps.update(over)
    return ps


class ProcessSubmissionTests(unittest.IsolatedAsyncioTestCase):
    def _start(self, **over):
        started = {}
        for k, p in _patches(**over).items():
            started[k] = p.start()
            self.addCleanup(p.stop)
        return SimpleNamespace(**started)

    async def test_invalid_name_creates_nothing(self):
        m = self._start()
        with self.assertRaises(RulesError):
            await rules_forms.process_submission(1, 2, game_name="   ", files=[_file("a.pdf")])
        m.create.assert_not_awaited()
        m.add.assert_not_awaited()

    async def test_game_create_failure_adds_nothing(self):
        m = self._start(create=patch("utils.rules_forms.rules_service.create_game",
                                     new=AsyncMock(side_effect=RulesError("dup"))))
        with self.assertRaises(RulesError):
            await rules_forms.process_submission(1, 2, game_name="Catan", files=[_file("a.pdf")])
        m.add.assert_not_awaited()

    async def test_mixed_results_per_file(self):
        m = self._start()
        bad_type = _file("a.exe")
        big = _file("big.pdf", size=999)
        ok = _file("ok.pdf")
        with patch("utils.rules_forms.config.get", return_value=100):
            res = await rules_forms.process_submission(
                1, 2, game_name="Catan", files=[bad_type, big, ok])
        self.assertEqual([i.ok for i in res.items], [False, False, True])
        bad_type.read.assert_not_awaited()
        big.read.assert_not_awaited()
        self.assertEqual(m.add.await_count, 1)
        text = res.render()
        self.assertIn("✅", text)
        self.assertIn("❌", text)
        self.assertIn("a.exe", text)
        self.assertEqual(res.total, 1)

    async def test_one_file_failing_does_not_stop_others(self):
        m = self._start(add=patch("utils.rules_forms.rules_service.add_source",
                                  new=AsyncMock(side_effect=[RulesError("index fail"), 1])))
        with patch("utils.rules_forms.config.get", return_value=100):
            res = await rules_forms.process_submission(
                1, 2, game=GAME, files=[_file("a.pdf"), _file("b.pdf")])
        self.assertEqual([i.ok for i in res.items], [False, True])
        self.assertIn("index fail", res.render())
        self.assertEqual(m.add.await_count, 2)

    async def test_unexpected_error_logged_and_isolated(self):
        self._start(add=patch("utils.rules_forms.rules_service.add_source",
                              new=AsyncMock(side_effect=[RuntimeError("boom"), 1])))
        with patch("utils.rules_forms.config.get", return_value=100), \
                self.assertLogs("utils.rules_forms", level="ERROR") as cm:
            res = await rules_forms.process_submission(
                1, 2, game=GAME, files=[_file("a.pdf"), _file("b.pdf")])
        self.assertEqual([i.ok for i in res.items], [False, True])
        self.assertIn("guild=1", cm.output[0])
        self.assertIn("user=2", cm.output[0])

    async def test_all_failed_on_existing_game_reports_current_total(self):
        self._start()
        with patch("utils.rules_forms.config.get", return_value=100), \
                patch("utils.rules_forms.database.run",
                      new=AsyncMock(return_value=[1, 2, 3])):
            res = await rules_forms.process_submission(
                1, 2, game=GAME, files=[_file("a.exe")])
        self.assertEqual(res.total, 3)

    async def test_url_check_timeout_becomes_rules_error(self):
        m = self._start()
        with patch("utils.rules_forms.asyncio.wait_for",
                   new=AsyncMock(side_effect=asyncio.TimeoutError)):
            with self.assertRaises(RulesError) as cm:
                await rules_forms.process_submission(
                    1, 2, game_name="Catan", files=[_file("a.pdf")], url="https://slow.test/x")
        self.assertIn("public http(s) links", cm.exception.user_message)
        m.create.assert_not_awaited()

    async def test_link_labels_consistent_and_short(self):
        long_path = "/" + "p" * 200 + ".pdf"
        self._start(fetch=patch("utils.rules_forms.rules_ingest.fetch_url",
                                new=AsyncMock(side_effect=RulesError("❌ nope"))))
        with patch("utils.rules_forms.database.run", new=AsyncMock(return_value=[])):
            res = await rules_forms.process_submission(
                1, 2, game=GAME, url="https://host.test" + long_path)
        label = res.items[0].label
        self.assertTrue(label.startswith("host.test/"))
        self.assertTrue(label.endswith(" (link)"))
        self.assertLessEqual(len(label), 80)
        self.assertIn("(link): nope", res.render())

    async def test_link_success_uses_fetched_filename(self):
        self._start()
        res = await rules_forms.process_submission(1, 2, game=GAME, url="https://x.test/")
        self.assertEqual(res.items[0].label, "web.pdf (link)")

    async def test_file_and_link_both_added(self):
        m = self._start()
        with patch("utils.rules_forms.config.get", return_value=100):
            res = await rules_forms.process_submission(
                1, 2, game=GAME, files=[_file("a.pdf")], url="https://x.test/web.pdf")
        self.assertEqual(m.add.await_count, 2)
        self.assertEqual([i.ok for i in res.items], [True, True])
        m.fetch.assert_awaited_once()
        self.assertEqual(res.total, 2)

    async def test_addsource_requires_file_or_link(self):
        m = self._start()
        with self.assertRaises(RulesError):
            await rules_forms.process_submission(1, 2, game=GAME, files=[], url="  ")
        m.add.assert_not_awaited()

    async def test_bad_url_rejected_before_anything_created(self):
        m = self._start(check=patch("utils.rules_forms.rules_ingest.check_public_url",
                                    side_effect=RulesError("private")))
        with self.assertRaises(RulesError):
            await rules_forms.process_submission(
                1, 2, game_name="Catan", files=[_file("a.pdf")], url="http://127.0.0.1")
        m.create.assert_not_awaited()
        m.add.assert_not_awaited()

    async def test_addgame_with_no_sources_ok(self):
        m = self._start()
        res = await rules_forms.process_submission(1, 2, game_name="Catan")
        m.create.assert_awaited_once()
        self.assertEqual(res.items, [])
        self.assertEqual(res.total, 0)
        self.assertIn("Catan", res.render())

    async def test_progress_messages(self):
        self._start()
        seen = []

        async def progress(t):
            seen.append(t)
        with patch("utils.rules_forms.config.get", return_value=100):
            await rules_forms.process_submission(
                1, 2, game=GAME, files=[_file("Codex.pdf"), _file("b.pdf"), _file("c.pdf")],
                progress=progress)
        self.assertIn("Indexing 1 of 3: Codex.pdf…", seen)
        self.assertIn("Indexing 3 of 3: c.pdf…", seen)

    async def test_render_truncated_to_2000(self):
        res = rules_forms.SubmissionResult(
            "G", [rules_forms.ItemResult(f"f{i}.pdf" + "x" * 50, False, "❌ " + "y" * 100)
                  for i in range(60)], 0)
        self.assertLessEqual(len(res.render()), 2000)


if __name__ == "__main__":
    unittest.main()
