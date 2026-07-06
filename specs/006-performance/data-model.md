# Phase 1 — Data Model: General Performance Improvements

**No database schema changes.** This feature reuses `message_events` and `message_context`
(and its FTS5 triggers) exactly as they are today. The only new "entity" is a runtime,
in-process construct.

## Runtime entity: `WriteBuffer`

A generic, bounded, time/size-flushed batch writer for a single target table. Lives in
`utils/write_buffer.py`. Not persisted.

### State

| Field | Type | Meaning |
|-------|------|---------|
| `name` | `str` | Label for logging (e.g. `"message_events"`). |
| `flush_fn` | `Callable[[list], Awaitable]` | Async callable that persists a batch (wraps `database.run(bulk_helper, rows)`). |
| `max_rows` | `int` | Flush when pending reaches this count. |
| `max_interval` | `float` | Flush at least this often (seconds), even if under `max_rows`. |
| `_pending` | `list[tuple]` | In-memory rows awaiting flush. |
| `_lock` | `asyncio.Lock` | Guards `_pending` swap during flush. |
| `_task` | `asyncio.Task \| None` | Background interval-flush task. |
| `_started` | `bool` | Lifecycle guard. |

### Behavior / invariants

- **`enqueue(row)`**: append to `_pending` (non-blocking, no thread hop). If `len(_pending) >=
  max_rows`, schedule/trigger a flush. MUST NOT block the event loop.
- **Flush**: atomically swap `_pending` → local `batch` (empty the buffer under `_lock`), then
  `await flush_fn(batch)` performing ONE transaction. Logs `name` + row count at DEBUG/INFO.
- **On flush failure**: log at WARNING/ERROR with `name` and count (never silent). Policy:
  drop the failed batch (best-effort) to avoid unbounded growth; the loss is logged. (Documented
  trade-off; `message_context` is idempotent so a later capture may re-cover it.)
- **`start()` / `stop()`**: idempotent; `stop()` performs a final flush. Called from
  `cog_load`/`cog_unload`; `bot.close()` also flushes/stops all buffers before `database.close_db()`.
- **Ordering**: FIFO within a table. Cross-table ordering is not guaranteed and not required.
- **Idempotency**: relies on the target bulk helper. `bulk_log_context_messages` uses
  `INSERT OR IGNORE` on `UNIQUE(message_id)`; `bulk_log_message_events` is append-only (atomic
  batch prevents duplicates).

### Relationship to existing helpers

- `message_events` buffer → `database.bulk_log_message_events(rows)` (exists,
  `database.py:546`). Row shape: `(guild_id, channel_id, user_id, recorded_at, word_count)`.
- `message_context` buffer → `database.bulk_log_context_messages(rows, max_stored_chars)`
  (exists, `database.py:678`). Row shape:
  `(guild_id, channel_id, message_id, user_id, author_name, content, recorded_at)`.

Both bulk helpers already run inside a single `get_conn()` transaction, so no `database.py`
transaction changes are required — only thin call-site wiring.

## Config keys (added to `config.yaml`)

```yaml
# Performance & resilience tuning. Each path degrades to pre-006 behavior when disabled.
performance:
  # Per-message write batching (stats message_events + fact-check message_context).
  # When disabled, listeners fall back to per-message writes (today's behavior).
  batch_writes:
    enabled: true
    max_rows: 50            # Flush when this many rows are pending
    max_interval_seconds: 2 # ...or at least this often, whichever comes first
    # NOTE: best-effort durability — a hard crash may drop up to one interval of
    # pending rows. Graceful shutdown always flushes. Set enabled:false for strict
    # per-message durability.

  # Fact-check local-I/O concurrency (parallel image/reply fetch + context queries).
  fast_factcheck:
    enabled: true

  # Gateway connection-health observability.
  health:
    log_latency: true       # Periodic bot.latency log line
    # cadence reuses the existing scheduler tick; no separate interval needed
```

### Parity notes

- With `performance.batch_writes.enabled: false`, `on_message` MUST take the current
  per-message `database.run` path verbatim (byte-for-byte behavior).
- With `performance.fast_factcheck.enabled: false`, `_extract_content` and the context window
  MUST take the current sequential path verbatim.
- Defaults are the optimized paths (FR-009), so an operator who does nothing gets the speedups.

## Validation rules

- `max_rows >= 1`, `max_interval_seconds > 0`; invalid values fall back to safe defaults with a
  logged WARNING (no crash).
- Backfill / `/scan` bulk ingestion MUST bypass the buffer entirely (it already batches).
- Excluded channels/users and bot-author filtering happen **before** enqueue, exactly as today.
