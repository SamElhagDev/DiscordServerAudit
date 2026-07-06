"""Batched write buffer: coalesces per-row DB writes into size/time-flushed batches.

Enqueue is a non-blocking append; a background task flushes on ``max_rows`` or
``max_interval``, whichever comes first, in one transaction per flush. Best-effort
durability — a hard crash may drop up to one interval of pending rows; graceful
shutdown flushes via ``stop()``.
"""

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class WriteBuffer:
    """Size/time-flushed batch writer for one target.

    ``flush_fn`` persists a batch in one transaction (usually
    ``database.run(bulk_helper, rows)``). ``name`` labels log lines.
    """

    def __init__(
        self,
        name: str,
        flush_fn: Callable[[list], Awaitable],
        *,
        max_rows: int = 50,
        max_interval: float = 2.0,
    ):
        self.name = name
        self._flush_fn = flush_fn
        self.max_rows = max_rows if (max_rows and max_rows >= 1) else 50
        self.max_interval = max_interval if (max_interval and max_interval > 0) else 2.0
        if self.max_rows != max_rows or self.max_interval != max_interval:
            logger.warning(
                "WriteBuffer[%s] invalid config (max_rows=%r, max_interval=%r) — "
                "using max_rows=%d, max_interval=%.1fs",
                name, max_rows, max_interval, self.max_rows, self.max_interval,
            )
        self._pending: list = []
        self._lock = asyncio.Lock()
        self._flush_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._started = False

    async def start(self) -> None:
        """Start the background flush task (idempotent)."""
        if self._started:
            return
        self._started = True
        self._flush_event.clear()
        self._task = asyncio.create_task(self._run())
        logger.info(
            "WriteBuffer[%s] started (max_rows=%d, max_interval=%.1fs)",
            self.name, self.max_rows, self.max_interval,
        )

    def enqueue(self, row) -> None:
        """Append a row; signal a flush once ``max_rows`` is reached. Non-blocking."""
        self._pending.append(row)
        if len(self._pending) >= self.max_rows:
            self._flush_event.set()

    async def flush(self) -> int:
        """Write pending rows in one transaction. Returns rows written.

        A failed batch is logged and dropped so the buffer can't grow unbounded.
        """
        async with self._lock:
            if not self._pending:
                return 0
            batch = self._pending
            self._pending = []
            self._flush_event.clear()
            try:
                await self._flush_fn(batch)
                logger.debug("WriteBuffer[%s] flushed %d rows", self.name, len(batch))
                return len(batch)
            except Exception:
                logger.error(
                    "WriteBuffer[%s] flush failed — dropping %d rows",
                    self.name, len(batch), exc_info=True,
                )
                return 0

    async def stop(self) -> int:
        """Cancel the task and do a final flush (idempotent). Returns rows written."""
        if not self._started:
            return 0
        self._started = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        flushed = await self.flush()
        logger.info(
            "WriteBuffer[%s] stopped — final flush wrote %d rows", self.name, flushed,
        )
        return flushed

    async def _run(self) -> None:
        """Flush on the size signal or the interval timeout, whichever comes first."""
        while True:
            try:
                await asyncio.wait_for(self._flush_event.wait(), timeout=self.max_interval)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
            try:
                await self.flush()
            except Exception:
                logger.error("WriteBuffer[%s] flush loop error", self.name, exc_info=True)
