# Implementation Plan: General Performance Improvements

**Branch**: `main` (single-branch workflow) | **Date**: 2026-07-06 | **Spec**: specs/006-performance/spec.md
**Input**: Feature specification from `specs/006-performance/spec.md`

## Summary

Reduce the bot's steady-state cost and improve responsiveness in three evidence-backed areas,
with **no behavior change** and every path individually toggleable:

1. **Per-message write batching** — replace the two per-message `database.run()` calls
   (`cogs/stats.py:390` → `log_message_event`, `cogs/fact_check.py:328` → `log_context_message`)
   with an in-process, bounded, time/size-flushed **write buffer** (`utils/write_buffer.py`)
   that coalesces rows into a single `executemany` transaction per flush. Enqueue becomes a
   non-blocking in-memory append (no thread hop per message); flush does one thread hop + one
   commit/`fsync` per batch. Buffers flush on shutdown (`bot.close()`), so graceful restarts
   lose nothing.
2. **Fact-check latency** — in `cogs/fact_check.py`, parallelize `_extract_content`
   image/sticker/thumbnail downloads and the `_fetch_reply_context` call with `asyncio.gather`,
   and collapse the two context-window queries (`get_recent_context` + `get_relevant_history`)
   so they don't add a second serial thread hop. Overlap the "Checking…" placeholder send with
   context assembly. The Gemini call itself is unchanged.
3. **Reconnect / stability** — harden `utils/reconnect.py` so the backoff cap verifies it is
   applied to the *live* reconnect path (log confirmation; stock fallback on failure, as today),
   add a periodic **connection-health** log line (`bot.latency`), and guarantee buffered writes
   survive gateway churn and graceful shutdown.

All changes are confined to `utils/write_buffer.py` (new), `cogs/stats.py`, `cogs/fact_check.py`,
`utils/reconnect.py`, `bot.py`, `database.py` (thin enqueue helpers), and `config.yaml`.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: discord.py (commands ext; `Intents.message_content`, `voice_states`),
`aiohttp` (image downloads), `google-genai` (unchanged), sqlite3 (stdlib, WAL).
**Storage**: SQLite via `database.py`. **No schema changes.** Reuses `message_events` and
`message_context` (+ its FTS5 triggers). Batching uses `executemany` in one transaction;
FTS5 sync triggers fire per row as today.
**Testing**: Manual Discord validation across {batching on/off} and {fast-factcheck on/off};
burst-load sanity check; graceful-restart flush check; flake8 lint in CI.
**Target Platform**: Windows (Scheduled Task deployment via GitHub Actions).
**Project Type**: Discord bot (cog-modular).
**Performance Goals**: Per-message thread hops + fsyncs drop from 2N to ≈ N/batch_size under
load; multi-image fact-check pre-Gemini latency reduced via concurrent I/O; zero buffered-row
loss on graceful shutdown.
**Constraints**: No behavior change; correctness parity on recorded counts and verdicts; SQLite
only; single-transaction flush (atomic, no partial/duplicate batch); backfill/`/scan` bulk paths
must keep bypassing the per-message buffer; no bare excepts; guild-scoped structured logging.
**Scale/Scope**: Single-server bot. Buffer sizes/intervals are small and configurable
(e.g. flush at 50 rows or 2s). Best-effort durability: a hard crash may drop ≤1 flush interval
of events — acceptable for aggregate stats and best-effort context, documented in `config.yaml`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Cog-Modular Architecture | PASS | Batching logic is a reusable `utils/write_buffer.py`; each cog owns its buffer via `cog_load`/`cog_unload` and enqueues through a `database.py` helper. No cross-cog imports; no new cog needed. |
| II. Admin Role Gating (NON-NEGOTIABLE) | PASS | No new commands. Listeners and the flush task are event/background handlers, consistent with existing listeners. Existing `/factcheck` keeps `@has_admin_role()`. |
| III. Audit-First Design | N/A | No audit-domain changes. |
| IV. AI-Augmented Recommendations (Gemini) | PASS | Gemini call is unchanged and stays advisory/optional/gracefully degraded. Latency work only reorders local I/O around it. |
| V. Observability & Structured Logging | PASS | Buffer flush counts, shutdown-flush counts, backoff-cap confirmation, and connection-health latency all log at INFO/WARNING with guild context where applicable. Flush/enqueue failures logged, never silently swallowed. |

**Post-Design Re-Check**: PASS. No violations. Introducing an in-process write buffer is a new
runtime responsibility, not a principle conflict — it is toggleable, bounded, flushed on
shutdown, documented in `config.yaml`, and preserves exact write coverage.

## Project Structure

### Documentation (this feature)

```text
specs/006-performance/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: batching / concurrency / reconnect decisions
├── data-model.md        # Phase 1: write-buffer contract + config keys (no schema change)
├── quickstart.md        # Phase 1: 4-phase implementation guide
└── contracts/
    └── performance_contract.md  # WriteBuffer API + changed function contracts
```

### Source Code (repository root)

```text
utils/
└── write_buffer.py      # NEW: generic bounded, time/size-flushed batch writer (start/stop/enqueue/flush)

cogs/
├── stats.py             # on_message enqueues to a message_events buffer (was database.run per msg);
│                        #   buffer started/stopped in cog_load/cog_unload
└── fact_check.py        # capture_context_message enqueues to a message_context buffer;
                         #   _extract_content parallelizes downloads + reply fetch (asyncio.gather);
                         #   context-window queries collapsed to avoid a 2nd serial thread hop

utils/
└── reconnect.py         # verify+log backoff cap application; stock fallback unchanged

bot.py                   # flush all buffers in close(); register connection-health watchdog task

database.py              # thin enqueue-target helpers (bulk insert already exists:
                         #   bulk_log_message_events, bulk_log_context_messages)

config.yaml              # + performance.* block (batching + fast_factcheck + health toggles)
```

**Structure Decision**: The batch writer is a small reusable utility (not a cog) so both the
stats and fact-check cogs can use it without coupling (Principle I). Each cog remains
self-contained: it constructs and owns its buffer, targeting an existing `database.py` bulk
helper. `bot.py` orchestrates shutdown flush because it owns the lifecycle.

## Complexity Tracking

> Filled because this feature adds an in-process write buffer — a new runtime durability
> surface worth justifying.

| Addition | Why Needed | Simpler Alternative Rejected Because |
|----------|------------|--------------------------------------|
| `utils/write_buffer.py` batch writer | Coalesce 2N per-message thread hops + fsyncs into batched transactions | Keeping per-message `database.run` is the current cost being removed; WAL/tuning alone doesn't remove the 2 thread hops + 2 commits per message |
| In-memory pending buffer (best-effort durability) | Non-blocking enqueue; amortized flush | A durable queue (extra table/WAL row per message) reintroduces the per-message write we're eliminating; buffer + shutdown-flush covers graceful stops, and a crash losing ≤1 interval of aggregate stats is acceptable and documented |
| Concurrent image/reply fetch in `_extract_content` | Cut serial network latency before Gemini | Sequential loop is the current cost; ordering of images in the bundle is preserved by gathering then reassembling in original order |
| Connection-health watchdog log line | Post-mortem signal for gateway flapping | Relying only on `on_disconnect`/`on_resumed` gives no latency trend; a bounded periodic log is cheap observability (Principle V) |

## Phase 0 — Outline & Research

Complete. See [research.md](./research.md). Resolved:

- **Batching model**: in-process `WriteBuffer` per target table; flush on `max_rows` OR
  `max_interval_seconds` (whichever first) via a single `asyncio` background task doing one
  `database.run(bulk_helper, rows)` per flush. Enqueue is a lock-guarded list append. Flush on
  `cog_unload` and `bot.close()`. Best-effort durability documented.
- **Concurrency**: `asyncio.gather` for `_extract_content` downloads + `_fetch_reply_context`,
  preserving bundle order; collapse the recency+relevance queries so they cost one thread hop.
- **Reconnect**: keep the defensive monkeypatch but assert the patched class is what the
  reconnect loop actually instantiates, log the confirmed cap, and fall back to stock on
  mismatch (as today). Add a `bot.latency` health log on the existing scheduler cadence.

## Phase 1 — Design & Contracts

Complete. Artifacts:
- [data-model.md](./data-model.md) — `WriteBuffer` state/behavior, config keys, no schema change,
  durability + parity notes.
- [contracts/performance_contract.md](./contracts/performance_contract.md) — `WriteBuffer` API,
  changed `_extract_content` / context-window contracts, `close()` flush contract, reconnect
  verification contract.
- [quickstart.md](./quickstart.md) — 4-phase implementation guide with per-phase verification.

Agent context: `CLAUDE.md` plan pointer updated to this file.

## Phase 2 — Next step

Run `/speckit-tasks` to generate `tasks.md` (dependency-ordered) from these artifacts. Suggested
ordering mirrors quickstart: config + `WriteBuffer` utility → wire stats buffer → wire
fact-check context buffer + shutdown flush → fact-check concurrency → reconnect verification +
health watchdog → parity/burst/restart validation.
