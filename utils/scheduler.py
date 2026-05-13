import asyncio
import datetime
import logging
from typing import Callable, Awaitable

import database
import config

logger = logging.getLogger(__name__)


class IntervalScheduler:
    """
    Runs registered async tasks on a configurable hour-based interval.
    Persists last-run timestamps in SQLite so restarts don't reset the schedule.
    """

    def __init__(self, bot):
        self.bot = bot
        self._tasks: list[dict] = []

    def register(self, key: str, interval_hours: float, coro_factory: Callable[[], Awaitable]):
        """
        Register a scheduled task.
        - key: unique string used to track last-run in DB (e.g. 'security_audit')
        - interval_hours: how often to run (from config)
        - coro_factory: a zero-arg async callable that performs the task
        """
        self._tasks.append({
            "key": key,
            "interval": datetime.timedelta(hours=interval_hours),
            "coro_factory": coro_factory,
        })
        logger.info(f"Registered scheduled task: {key} every {interval_hours}h")

    async def start(self):
        """Start the scheduler loop. Call this from bot's on_ready."""
        logger.info("Scheduler started.")
        while True:
            now = datetime.datetime.utcnow()
            for task in self._tasks:
                last_run = database.get_last_run(task["key"])
                due = last_run is None or (now - last_run) >= task["interval"]
                if due:
                    logger.info(f"Running scheduled task: {task['key']}")
                    try:
                        await task["coro_factory"]()
                        database.set_last_run(task["key"])
                    except Exception as e:
                        logger.error(f"Scheduled task {task['key']} failed: {e}", exc_info=True)

            await asyncio.sleep(60 * 5)  # Check every 5 minutes
