import asyncio
import datetime
import logging
from typing import Callable, Awaitable

import database
import config

logger = logging.getLogger(__name__)

# How long a failed task waits before it is tried again (capped at its interval).
RETRY_AFTER_FAILURE = datetime.timedelta(hours=1)


class IntervalScheduler:
    """
    Runs registered async tasks on a configurable hour-based interval.
    Persists last-run timestamps in SQLite so restarts don't reset the schedule.
    """

    def __init__(self, bot):
        self.bot = bot
        self._tasks: list[dict] = []
        self._running = False

    def register(self, key: str, interval_hours: float, coro_factory: Callable[[], Awaitable]):
        """
        Register a scheduled task.
        - key: unique string used to track last-run in DB (e.g. 'security_audit')
        - interval_hours: how often to run (from config)
        - coro_factory: a zero-arg async callable that performs the task
        """
        if any(t["key"] == key for t in self._tasks):
            logger.debug("Scheduled task %r already registered — skipping duplicate", key)
            return
        self._tasks.append({
            "key": key,
            "interval": datetime.timedelta(hours=interval_hours),
            "coro_factory": coro_factory,
        })
        logger.info(f"Registered scheduled task: {key} every {interval_hours}h")

    async def start(self):
        """Start the scheduler loop. Call this from bot's on_ready."""
        if self._running:
            logger.debug("Scheduler.start() called again — loop already running, ignoring")
            return
        self._running = True
        logger.info("Scheduler started.")
        while True:
            now = datetime.datetime.now(datetime.timezone.utc)
            for task in self._tasks:
                await self._run_if_due(task, now)

            await asyncio.sleep(60 * 5)  # Check every 5 minutes

    async def _run_if_due(self, task: dict, now: datetime.datetime) -> None:
        retry_at = task.get("retry_at")
        if retry_at is not None and now < retry_at:
            return
        last_run = await database.run(database.get_last_run, task["key"])
        if last_run is not None and (now - last_run) < task["interval"]:
            return
        logger.info(f"Running scheduled task: {task['key']}")
        try:
            await task["coro_factory"]()
        except Exception as e:
            # Without a back-off, a task that fails after posting part of its
            # output would re-run (and re-post) on every 5-minute tick.
            delay = min(task["interval"], RETRY_AFTER_FAILURE)
            task["retry_at"] = now + delay
            logger.error(f"Scheduled task {task['key']} failed: {e} — retrying in {delay}",
                         exc_info=True)
            return
        task.pop("retry_at", None)
        await database.run(database.set_last_run, task["key"])
