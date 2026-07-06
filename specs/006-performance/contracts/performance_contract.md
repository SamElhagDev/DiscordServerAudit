# Contract: General Performance Improvements

Internal (code-facing) contracts. No Discord-facing command or embed changes. No user-visible
behavior change when toggles are at their defaults or disabled.

## 1. `utils/write_buffer.py` — `WriteBuffer`

```python
class WriteBuffer:
    def __init__(self, name: str, flush_fn: Callable[[list], Awaitable[None]],
                 *, max_rows: int = 50, max_interval: float = 2.0): ...

    async def start(self) -> None:
        """Idempotent. Launch the background interval-flush task."""

    def enqueue(self, row: tuple) -> None:
        """Non-blocking, no thread hop. Appends row; may trigger a size-based flush.
        MUST NOT await or block the event loop."""

    async def flush(self) -> int:
        """Atomically drain pending rows and persist them in ONE transaction via flush_fn.
        Returns the number of rows flushed. Logs name + count. On failure: log (never silent),
        drop the batch, return 0-or-raise-per-policy (see data-model)."""

    async def stop(self) -> None:
        """Idempotent. Cancel the task and perform a final flush."""
```

**Guarantees**
- `enqueue` never dispatches a thread-pool hop (removes the per-message `database.run`).
- `flush` performs exactly one `flush_fn` call (one DB transaction) per invocation.
- `stop`/final-flush is called on `cog_unload` and via `bot.close()`.

## 2. `cogs/stats.py` — `on_message`

**Before**: `await database.run(database.log_message_event, guild_id, channel_id, user_id, word_count)`
per message.

**After (batching enabled)**: `self._events_buffer.enqueue((guild_id, channel_id, user_id, _now, word_count))`.
All existing gate checks (`_scanning`, `stats.enabled`, guild present, bot/exclusion filters)
run **before** enqueue, unchanged. `recorded_at` is computed at enqueue time to preserve
timestamp fidelity (do not defer it to flush time).

**After (batching disabled)**: identical to today's per-message `database.run` path.

Buffer lifecycle: created in `cog_load`, `start()`ed there; `stop()`ed in `cog_unload`.

## 3. `cogs/fact_check.py` — `capture_context_message`

**Before**: `await database.run(database.log_context_message, ...)` per message, plus the
periodic prune counter.

**After (batching enabled)**: enqueue the row tuple to a `message_context` buffer. The
`_PRUNE_EVERY` prune trigger MUST still fire (drive it off enqueue count, or off flush count —
either is acceptable as long as pruning cadence is preserved). Gate checks
(`context.enabled`, guild present, non-bot, non-empty content, `_context_excluded`) run before
enqueue, unchanged. Content truncation (`max_stored_chars`) still applies (the bulk helper
already truncates).

**After (batching disabled)**: identical to today's per-message path.

## 4. `cogs/fact_check.py` — `_extract_content` (fast_factcheck enabled)

**Contract**: image attachment reads, sticker downloads, video-thumbnail downloads, and
embed-image downloads are issued concurrently (`asyncio.gather`), and `_fetch_reply_context`
runs concurrently with them. The returned `ContentBundle` MUST be identical to the sequential
version:
- `images` preserves the priority order attachments → stickers → thumbnails → embeds.
- The `max_images` cap is applied after gathering (respect the same cap count).
- Oversized/failed images are skipped with the same WARNING logging as today.

**fast_factcheck disabled**: sequential path verbatim.

## 5. `cogs/fact_check.py` — context window (fast_factcheck enabled)

**Contract**: `get_recent_context` and `get_relevant_history` are resolved without adding a
second serial thread hop (gather the two `database.run` calls, or one combined wrapper). The
resulting `ContextWindow` (recency + relevance selection, ordering, de-dup by `seen_ids`) MUST
be identical to today. FTS availability / query-term gating unchanged.

## 6. `bot.py` — `close()`

**Contract**: on graceful shutdown, all registered write buffers are flushed/stopped **before**
`database.close_orphaned_voice_sessions()` and `database.close_db()`. A count of flushed rows is
logged. No buffered row is lost on graceful stop (NFR-004).

## 7. `utils/reconnect.py` — `cap_reconnect_backoff`

**Contract**: after installing the bounded backoff, verify it is the class the live reconnect
path uses and log an INFO confirming the active cap (`max retry delay ≈ Ns`). On any failure or
mismatch, log a WARNING and leave stock backoff in place — behavior identical to today's worst
case (never breaks startup).

## 8. Connection-health watchdog (`health.log_latency`)

**Contract**: on the existing scheduler cadence, emit one log line with `bot.latency` (gateway
heartbeat, seconds). WARNING if latency is unavailable/`nan` or exceeds a sane threshold; INFO/
DEBUG otherwise. Purely observational — no reconnect action, no persistence.

## Non-functional acceptance

- **Parity**: with all toggles off, recorded counts and verdicts are byte-for-byte equivalent to
  pre-006 (NFR-001).
- **Throughput**: under a burst of N messages, thread hops + fsyncs ≈ 2·⌈N/max_rows⌉ instead of
  2N (NFR-002).
- **Latency**: multi-image fact-check pre-Gemini wall-time lower than sequential (NFR-003).
- **Durability**: zero buffered rows lost on graceful shutdown (NFR-004).
- **Lint**: flake8 clean.
