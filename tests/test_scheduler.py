import datetime
import unittest
from unittest.mock import AsyncMock, patch

import discord

from utils.permissions import batch_embeds, build_findings_embeds
from utils.scheduler import RETRY_AFTER_FAILURE, IntervalScheduler

UTC = datetime.timezone.utc


class BatchEmbedsTests(unittest.TestCase):
    def test_batches_stay_under_discord_message_limits(self):
        findings = [{"severity": "warning", "category": "channel_overwrites",
                     "description": "x" * 900} for _ in range(60)]
        embeds = build_findings_embeds("Findings", findings)
        # Old code sent these 10 per message, well over the 6000-char total.
        self.assertGreater(sum(len(e) for e in embeds[:10]), 6000)
        batches = batch_embeds(embeds)
        for batch in batches:
            self.assertLessEqual(sum(len(e) for e in batch), 6000)
            self.assertLessEqual(len(batch), 10)
        self.assertEqual(sum(len(b) for b in batches), len(embeds))

    def test_small_embeds_share_a_message_up_to_ten(self):
        embeds = [discord.Embed(title="t") for _ in range(23)]
        self.assertEqual([len(b) for b in batch_embeds(embeds)], [10, 10, 3])

    def test_empty(self):
        self.assertEqual(batch_embeds([]), [])


class SchedulerRetryTests(unittest.IsolatedAsyncioTestCase):
    def _sched(self, coro, hours=168):
        s = IntervalScheduler(bot=None)
        s.register("security_audit_1", hours, coro)
        return s, s._tasks[0]

    async def test_failed_task_backs_off_instead_of_rerunning_every_tick(self):
        coro = AsyncMock(side_effect=RuntimeError("400 embed too large"))
        s, task = self._sched(coro)
        now = datetime.datetime(2026, 11, 9, 12, tzinfo=UTC)
        with patch("utils.scheduler.database.run", new=AsyncMock(return_value=None)) as run:
            await s._run_if_due(task, now)
            await s._run_if_due(task, now + datetime.timedelta(minutes=5))
            await s._run_if_due(task, now + datetime.timedelta(minutes=55))
            self.assertEqual(coro.await_count, 1)
            await s._run_if_due(task, now + RETRY_AFTER_FAILURE)
            self.assertEqual(coro.await_count, 2)
        # last_run is never recorded for a failed run
        self.assertNotIn("set_last_run", [c.args[0].__name__ for c in run.await_args_list])

    async def test_success_records_last_run_and_clears_backoff(self):
        coro = AsyncMock()
        s, task = self._sched(coro)
        task["retry_at"] = datetime.datetime(2026, 1, 1, tzinfo=UTC)
        with patch("utils.scheduler.database.run", new=AsyncMock(return_value=None)) as run:
            await s._run_if_due(task, datetime.datetime(2026, 11, 9, tzinfo=UTC))
        coro.assert_awaited_once()
        self.assertNotIn("retry_at", task)
        self.assertEqual(run.await_args_list[-1].args[0].__name__, "set_last_run")

    async def test_not_due_is_skipped(self):
        coro = AsyncMock()
        s, task = self._sched(coro)
        now = datetime.datetime(2026, 11, 9, tzinfo=UTC)
        with patch("utils.scheduler.database.run",
                   new=AsyncMock(return_value=now - datetime.timedelta(hours=1))):
            await s._run_if_due(task, now)
        coro.assert_not_awaited()

    async def test_backoff_capped_at_short_interval(self):
        coro = AsyncMock(side_effect=RuntimeError("boom"))
        s, task = self._sched(coro, hours=0.25)
        now = datetime.datetime(2026, 11, 9, tzinfo=UTC)
        with patch("utils.scheduler.database.run", new=AsyncMock(return_value=None)):
            await s._run_if_due(task, now)
        self.assertEqual(task["retry_at"], now + datetime.timedelta(minutes=15))


if __name__ == "__main__":
    unittest.main()
