"""Tests for the batched write buffer (feature 006).

Uses a real async sink rather than a mock, so the assertions are about rows that actually
reached the flush function.
"""
import asyncio
import unittest

from utils.write_buffer import WriteBuffer


class _Sink:
    """Records the batches handed to it; can be told to fail."""

    def __init__(self, fail: bool = False):
        self.batches: list[list] = []
        self.fail = fail

    async def __call__(self, batch):
        if self.fail:
            raise RuntimeError("flush failed")
        self.batches.append(list(batch))

    @property
    def rows(self) -> list:
        return [row for batch in self.batches for row in batch]


class FlushTests(unittest.IsolatedAsyncioTestCase):
    async def test_flush_writes_pending_rows_and_returns_the_count(self):
        sink = _Sink()
        buf = WriteBuffer("t", sink, max_rows=10, max_interval=60)
        buf.enqueue("a")
        buf.enqueue("b")

        self.assertEqual(await buf.flush(), 2)
        self.assertEqual(sink.rows, ["a", "b"])

    async def test_a_flush_writes_everything_in_a_single_batch(self):
        sink = _Sink()
        buf = WriteBuffer("t", sink, max_rows=10, max_interval=60)
        for row in ("a", "b", "c"):
            buf.enqueue(row)

        await buf.flush()

        self.assertEqual(sink.batches, [["a", "b", "c"]])

    async def test_flushing_an_empty_buffer_writes_nothing(self):
        sink = _Sink()
        buf = WriteBuffer("t", sink, max_rows=10, max_interval=60)

        self.assertEqual(await buf.flush(), 0)
        self.assertEqual(sink.batches, [])

    async def test_a_failed_flush_drops_the_batch_rather_than_growing(self):
        sink = _Sink(fail=True)
        buf = WriteBuffer("t", sink, max_rows=10, max_interval=60)
        buf.enqueue("a")

        with self.assertLogs("utils.write_buffer", level="ERROR"):
            self.assertEqual(await buf.flush(), 0)

        # Nothing left pending: the batch was dropped, not requeued.
        self.assertEqual(await buf.flush(), 0)

    async def test_flushing_twice_does_not_rewrite_the_same_rows(self):
        sink = _Sink()
        buf = WriteBuffer("t", sink, max_rows=10, max_interval=60)
        buf.enqueue("a")

        await buf.flush()
        await buf.flush()

        self.assertEqual(sink.rows, ["a"])


class BackgroundLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_reaching_max_rows_triggers_a_flush(self):
        sink = _Sink()
        buf = WriteBuffer("t", sink, max_rows=3, max_interval=60)
        await buf.start()
        try:
            for row in ("a", "b", "c"):
                buf.enqueue(row)
            await asyncio.sleep(0.1)

            self.assertEqual(sink.rows, ["a", "b", "c"])
        finally:
            await buf.stop()

    async def test_the_interval_flushes_a_partial_batch(self):
        sink = _Sink()
        buf = WriteBuffer("t", sink, max_rows=100, max_interval=0.05)
        await buf.start()
        try:
            buf.enqueue("a")
            await asyncio.sleep(0.25)

            self.assertEqual(sink.rows, ["a"])
        finally:
            await buf.stop()

    async def test_stop_performs_a_final_flush(self):
        sink = _Sink()
        buf = WriteBuffer("t", sink, max_rows=100, max_interval=60)
        await buf.start()
        buf.enqueue("a")

        self.assertEqual(await buf.stop(), 1)
        self.assertEqual(sink.rows, ["a"])

    async def test_stop_is_idempotent(self):
        sink = _Sink()
        buf = WriteBuffer("t", sink, max_rows=100, max_interval=60)
        await buf.start()
        buf.enqueue("a")

        await buf.stop()

        self.assertEqual(await buf.stop(), 0)
        self.assertEqual(sink.rows, ["a"])

    async def test_stopping_a_buffer_that_never_started_is_a_no_op(self):
        sink = _Sink()
        buf = WriteBuffer("t", sink, max_rows=100, max_interval=60)

        self.assertEqual(await buf.stop(), 0)

    async def test_start_is_idempotent(self):
        sink = _Sink()
        buf = WriteBuffer("t", sink, max_rows=100, max_interval=60)
        await buf.start()
        await buf.start()
        try:
            buf.enqueue("a")
            self.assertEqual(await buf.flush(), 1)
        finally:
            await buf.stop()


class ConfigValidationTests(unittest.TestCase):
    def test_a_non_positive_max_rows_falls_back_to_the_default(self):
        with self.assertLogs("utils.write_buffer", level="WARNING"):
            buf = WriteBuffer("t", _Sink(), max_rows=0, max_interval=2.0)

        self.assertEqual(buf.max_rows, 50)

    def test_a_non_positive_interval_falls_back_to_the_default(self):
        with self.assertLogs("utils.write_buffer", level="WARNING"):
            buf = WriteBuffer("t", _Sink(), max_rows=50, max_interval=0)

        self.assertEqual(buf.max_interval, 2.0)

    def test_valid_settings_are_kept_as_given(self):
        buf = WriteBuffer("t", _Sink(), max_rows=7, max_interval=1.5)

        self.assertEqual((buf.max_rows, buf.max_interval), (7, 1.5))


if __name__ == "__main__":
    unittest.main()
