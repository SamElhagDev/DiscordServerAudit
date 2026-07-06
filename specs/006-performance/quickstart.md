# Quickstart — Implementation Guide: General Performance Improvements

Four phases, each independently verifiable. Every phase preserves behavior when its config
toggle is off. Run `flake8` after each phase.

## Phase A — Config + `WriteBuffer` utility

1. Add the `performance.*` block to `config.yaml` (see data-model.md), defaults enabling the
   optimized paths.
2. Create `utils/write_buffer.py` implementing `WriteBuffer` (see contract §1): `start`,
   `enqueue`, `flush` (single-transaction), `stop` (final flush). Lock-guarded pending swap;
   background interval task; structured logging with the buffer `name`; no bare excepts.

**Verify**: unit-exercise in a scratch script — enqueue 120 rows with `max_rows=50`,
`max_interval=2`; confirm 3 flushes (2 size-based + 1 final on `stop`), correct total count, and
that a raising `flush_fn` logs and does not crash the loop.

## Phase B — Wire the stats buffer

1. In `cogs/stats.py` `cog_load`: construct `self._events_buffer =
   WriteBuffer("message_events", lambda rows: database.run(database.bulk_log_message_events, rows), ...)`
   and `await self._events_buffer.start()` (guarded by `performance.batch_writes.enabled`).
2. In `on_message`: keep all gate checks; compute `word_count` and `recorded_at` as today, then
   `self._events_buffer.enqueue((guild_id, channel_id, user_id, recorded_at, word_count))`.
   If batching is disabled, take the existing `database.run(database.log_message_event, ...)` path.
3. In `cog_unload`: `await self._events_buffer.stop()`.

**Verify**: post a burst of messages; confirm rows land in `message_events` (count matches
sent), flush log lines appear, and disabling the toggle restores per-message writes.

## Phase C — Wire the fact-check context buffer + shutdown flush

1. In `cogs/fact_check.py` `cog_load`: construct `self._context_buffer =
   WriteBuffer("message_context", lambda rows: database.run(database.bulk_log_context_messages, rows, max_chars), ...)`
   and start it (guarded by the same toggle + `context.enabled`).
2. In `capture_context_message`: keep gate checks; build the row tuple and `enqueue`. Preserve
   the `_PRUNE_EVERY` prune cadence (drive it off enqueue/flush count). Disabled → existing path.
3. In `cog_unload`: `await self._context_buffer.stop()`.
4. In `bot.py` `close()`: before `close_orphaned_voice_sessions()`/`close_db()`, flush/stop all
   buffers (fetch cogs via `get_cog` and stop their buffers, or keep a registry on the bot). Log
   the flushed row count.

**Verify**: post messages, then stop the bot gracefully; confirm the final flush log shows the
expected count and no rows are lost (compare pre-stop enqueue count to DB rows). Confirm FTS5
search still returns newly-added context (triggers fired under `executemany`).

## Phase D — Fact-check concurrency + reconnect/health

1. In `_extract_content` (guard with `performance.fast_factcheck.enabled`): gather image
   attachment reads, sticker/thumbnail/embed downloads, and `_fetch_reply_context` concurrently;
   reassemble `images` in priority order and re-apply `max_images`. Disabled → sequential path.
2. Collapse the context-window queries (`_build_context_window`) so recency + relevance don't
   cost two serial thread hops (gather them). Confirm the resulting `ContextWindow` is unchanged.
3. In `utils/reconnect.py`: verify the bounded backoff is the class the reconnect path uses; log
   the confirmed cap; WARNING + stock fallback on mismatch.
4. Add the connection-health log line (`bot.latency`) on the existing scheduler cadence, guarded
   by `performance.health.log_latency`.

**Verify**: trigger a fact-check on a multi-image + reply message; confirm the pre-Gemini
elapsed (add a temporary timing log around `_extract_content` + context build) is lower than the
sequential baseline, and the verdict/sources are unchanged. Confirm the backoff-cap confirmation
line and a health-latency line appear in logs.

## Final validation

- **Parity matrix**: run with each toggle on and off; confirm recorded counts and fact-check
  verdicts match the pre-006 baseline (NFR-001).
- **Burst check**: send a burst; confirm flush count ≈ ⌈N/max_rows⌉ per table, not N (NFR-002).
- **Restart check**: graceful stop flushes with zero loss (NFR-004).
- **Lint**: flake8 clean; CI deploy workflow green before merging to `main`.
