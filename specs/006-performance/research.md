# Phase 0 — Research: General Performance Improvements

## R1. Per-message write batching

**Decision**: Introduce an in-process `WriteBuffer` (in `utils/write_buffer.py`) per target
table. Listeners `enqueue()` row tuples (non-blocking, lock-guarded list append). A single
`asyncio` background task flushes when the buffer reaches `max_rows` OR `max_interval_seconds`
elapse (whichever first), performing one `await database.run(bulk_helper, rows)` per flush. The
existing bulk helpers (`database.bulk_log_message_events`, `database.bulk_log_context_messages`)
already do `executemany` inside one transaction, so a flush is one thread hop + one commit/fsync
regardless of batch size. Buffers flush on `cog_unload` and on `bot.close()`.

**Rationale**: The measured hot path is `on_message`, which today fires two listeners
(`cogs/stats.py:390`, `cogs/fact_check.py:328`), each calling `database.run(...)`
(`asyncio.to_thread` → thread-pool hop → its own transaction → WAL fsync). That is 2 thread
dispatches and 2 commits **per message**. Batching turns 2N operations into ≈ 2·(N/batch_size),
and removes the per-message thread hop entirely (enqueue is pure in-memory).

**Alternatives considered**:
- *WAL/pragma tuning only* (e.g. `synchronous=NORMAL`): helps fsync cost but keeps 2 thread
  hops + 2 transactions per message. Complementary, not a replacement. (Note: WAL already on.)
- *Durable queue table*: write each message to a queue table, drain in batches. Reintroduces the
  exact per-message write we are removing. Rejected.
- *Single shared buffer for both tables*: couples the two cogs' concerns and complicates
  ownership (Principle I). Rejected in favor of one buffer per cog, sharing the `WriteBuffer`
  class.

**Durability**: Best-effort. A hard crash (not graceful shutdown) may lose up to one flush
interval of pending rows. Acceptable because both consumers are non-critical aggregates
(`message_events` feeds daily rollups; `message_context` is best-effort fact-check context) and
`message_context` is idempotent on `message_id`. Graceful shutdown flushes everything. This
trade-off is documented in `config.yaml`.

**Atomicity**: Each flush is a single `executemany` transaction — either the whole batch commits
or none of it does. No partial/duplicate batches. On flush failure the rows are logged and the
buffer decides retain-vs-drop (see contract) without silently succeeding.

## R2. Fact-check latency (concurrent local I/O)

**Decision**: In `cogs/fact_check.py`:
- `_extract_content`: gather image attachment reads, sticker downloads, video-thumbnail
  downloads, and embed-image downloads concurrently with `asyncio.gather`, then reassemble the
  `images` list in the original priority order and re-apply the `max_images` cap. Run
  `_fetch_reply_context` concurrently with the downloads (independent).
- Context window: collapse `get_recent_context` + `get_relevant_history` so they do not cost two
  separate serial thread hops — either run both under one `asyncio.gather`, or add a combined
  `database.run` wrapper. Preferred: gather, to keep each query function unchanged.
- Overlap the "Checking…" placeholder `message.reply(...)` with context assembly so the model
  prompt is ready sooner.

**Rationale**: The current `_extract_content` (`cogs/fact_check.py:232`) downloads sequentially
in a loop; with `max_images=4` and a 5s per-image timeout that is up to ~20s worst case before
Gemini is even called. `_build_context_window` issues two serial `database.run` calls. The
Gemini call dominates typical latency, but the local prep is pure overhead that can overlap.

**Alternatives considered**:
- *Reorder only (no concurrency)*: marginal. Rejected.
- *Cache context per channel*: correctness risk (staleness) for small gain. Out of scope.
- *Touch the Gemini call*: explicitly out of scope (model-side time).

**Constraint**: Bundle ordering (attachments > stickers > thumbnails > embeds) and the
`max_images` cap MUST be preserved after gathering.

## R3. Reconnect / stability

**Decision**: Keep the defensive backoff monkeypatch in `utils/reconnect.py`, but:
- Verify the patched `_BoundedExponentialBackoff` is the class the live reconnect path actually
  instantiates (assert the target attribute the client reads is the patched one), and log a
  single INFO confirming the cap is active; on any mismatch, log WARNING and leave stock backoff
  in place (identical to today's worst case).
- Add a periodic **connection-health** log line reporting `bot.latency` (gateway heartbeat) at a
  bounded cadence, so flapping is visible in post-mortems (Principle V).
- Guarantee buffered writes (R1) are independent of gateway state and are flushed on
  `bot.close()`, so reconnect/restart churn never drops recorded data.

**Rationale**: The current patch reassigns `discord.client.ExponentialBackoff`; if discord.py's
reconnect code path changes how it references the backoff, the patch could silently no-op. A
self-check + log makes that failure observable instead of silent. There is currently no latency
trend signal; `on_disconnect`/`on_resumed` only log transitions.

**Alternatives considered**:
- *Force-reconnect watchdog* (proactively close a stale socket): higher risk of fighting
  discord.py's own reconnect logic; defer unless health logs show a real need.
- *Replace monkeypatch with a discord.py client subclass hook*: larger blast radius; the
  existing defensive patch is adequate once verified. Rejected for now.

## Cross-cutting

- **Config**: add a `performance.*` block with independent toggles and defaults so every path
  degrades to today's behavior when off (FR-009). Defaults enable the optimized behavior.
- **Observability**: flush counts, shutdown-flush counts, backoff confirmation, and health
  latency all logged (Principle V). No bare excepts anywhere in new/changed code.
- **Parity validation**: compare recorded `message_events`/`message_context` counts and
  fact-check verdicts with batching/concurrency on vs. off over the same inputs.
