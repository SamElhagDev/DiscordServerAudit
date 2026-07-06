# Feature Specification: General Performance Improvements

**Feature Branch**: `main` (single-branch workflow) | **Feature Dir**: `006-performance`
**Date**: 2026-07-06
**Status**: Draft

## Overview

Improve the bot's general performance across three evidence-backed areas without changing
any user-visible behavior or feature surface:

1. **Per-message database overhead** — every guild message currently triggers two
   independent synchronous DB writes on two separate thread-pool hops (one per `on_message`
   listener), each with its own SQLite transaction and WAL `fsync`. Under sustained message
   volume this is the dominant recurring cost.
2. **Fact-check latency** — the reaction→verdict path performs image downloads sequentially,
   fetches reply context serially, and runs the two context-window queries as two separate
   thread hops, all before the Gemini call. Local prep time stacks on top of model time.
3. **Reconnect / stability** — the gateway reconnect backoff is applied via a monkeypatch of
   discord.py internals that can silently no-op if the library changes; there is no
   connection-health signal for post-mortems, and buffered work (introduced by area 1) must
   survive shutdown/reconnect churn without data loss.

This is a **performance and resilience refactor**. It is explicitly **not** a behavior change:
the same events are recorded, the same fact-check verdicts are produced, and the same config
surface is honored. Improvements MUST degrade gracefully to today's behavior when disabled.

## Clarifications

### Session 2026-07-06

- Q: Which performance areas are in scope? → A: Per-message DB overhead, Fact-check latency,
  Reconnect/stability. (Startup time was explicitly de-scoped.)
- Q: How much speckit ceremony? → A: Full feature — spec + plan + Phase 0/1 artifacts.

## User Scenarios & Testing

### Primary User Story

As the server operator, I want the bot to handle high message throughput and produce
fact-check results with lower latency, while staying connected reliably, so that the bot
feels responsive and does not fall behind or drop data during busy periods or network blips.

### Acceptance Scenarios

1. **Given** a burst of N messages arrives in a short window, **When** the stats and
   fact-check context listeners process them, **Then** the writes are coalesced into batched
   transactions (far fewer than 2N thread hops / fsyncs) and every non-excluded message is
   still persisted to `message_events` and `message_context`.
2. **Given** the bot is stopped gracefully (Scheduled Task restart / deploy), **When**
   shutdown runs, **Then** all buffered writes are flushed before the process exits — no
   in-flight message events or context rows are lost.
3. **Given** a fact-check is triggered on a message with multiple images and a reply,
   **When** content is gathered, **Then** image downloads and reply-context fetch run
   concurrently (not one-at-a-time) and the two context-window queries execute without adding
   a second serial thread hop, reducing pre-Gemini latency.
4. **Given** the Discord gateway drops, **When** the client reconnects, **Then** the capped
   backoff is confirmed active (logged), buffered writes are unaffected, and a connection-health
   log line records latency so operators can diagnose flapping.
5. **Given** any new performance path is disabled via config, **When** the bot runs, **Then**
   behavior is byte-for-byte equivalent to today (per-write path, serial downloads).

### Edge Cases

- Process crash (not graceful) between enqueue and flush: at most one flush interval of
  best-effort events may be lost. This is acceptable for aggregate stats and best-effort
  fact-check context, and MUST be documented. A single-transaction flush guarantees no
  partial/duplicated batch.
- `message_context` has `UNIQUE(message_id)` with `INSERT OR IGNORE`, so re-enqueue/replay is
  idempotent. `message_events` has no unique key; batching MUST NOT introduce duplicates
  (atomic single-transaction flush, no retry-after-partial-commit).
- Backfill / `/scan` paths already batch; they MUST continue to bypass the per-message buffer
  and keep their existing bulk-insert behavior.
- FTS5 sync triggers on `message_context` MUST continue to fire correctly under batched
  `executemany` inserts.

## Requirements

### Functional Requirements

- **FR-001**: The system MUST persist every non-excluded guild message to `message_events`
  (stats) and `message_context` (fact-check), identical to today's coverage.
- **FR-002**: The system MUST coalesce per-message writes into batched transactions, flushed
  on a bounded size threshold OR a bounded time interval, whichever comes first.
- **FR-003**: The per-message enqueue path MUST NOT dispatch a thread-pool hop per message
  (enqueue is an in-memory, non-blocking append).
- **FR-004**: The system MUST flush all buffered writes on graceful shutdown before closing
  the DB connection.
- **FR-005**: The fact-check content-gathering path MUST download images/stickers/thumbnails
  and fetch reply context concurrently rather than sequentially.
- **FR-006**: The fact-check context window MUST be assembled without adding a redundant serial
  thread hop (queries combined or run concurrently).
- **FR-007**: The reconnect backoff cap MUST verify it is actually applied to the live
  reconnect path and log the outcome; if it cannot be applied it MUST fall back to stock
  behavior (as today) with a WARNING.
- **FR-008**: The system MUST emit a periodic connection-health log line (gateway latency)
  at a bounded interval for observability.
- **FR-009**: Every new path MUST be individually toggleable via `config.yaml` and default to
  the optimized behavior, degrading to today's behavior when disabled.
- **FR-010**: All new/changed paths MUST preserve structured logging with guild context and
  MUST NOT introduce silent failures (no bare `except: pass`).

### Non-Functional Requirements

- **NFR-001 (Correctness parity)**: Recorded event counts and fact-check verdicts MUST match
  the pre-change behavior for equivalent input.
- **NFR-002 (Throughput)**: Under a burst of messages, thread-pool dispatches and `fsync`
  operations MUST scale sub-linearly with message count (batched), versus 2× per message today.
- **NFR-003 (Latency)**: For a multi-image fact-check, pre-Gemini local latency MUST be reduced
  relative to today (concurrent downloads); the Gemini call time itself is out of scope.
- **NFR-004 (No data loss on graceful stop)**: Zero buffered rows lost on graceful shutdown.
- **NFR-005 (Constitution)**: Changes MUST honor Principles I (cog-modular), II (admin gating —
  unchanged), V (observability). No new unguarded commands.

### Key Entities

- **Write buffer**: an in-process, bounded, time/size-flushed batch of pending row tuples for a
  single target table. No schema change; it batches existing INSERTs (`message_events`,
  `message_context`).
- **Connection-health signal**: a periodic observability record (log line) of gateway latency;
  no persistence required.

## Out of Scope

- Startup time (de-scoped by operator).
- Gemini model/prompt changes or model-side latency.
- Any change to stats semantics, fact-check verdict logic, retention/pruning policy, or the
  config feature surface beyond adding toggles.
- Schema/table changes (this feature reuses existing tables).

## Success Criteria

- Per-message thread-pool hops and fsyncs drop from 2N to ≈ (N / batch_size) under load.
- Multi-image fact-check pre-Gemini latency measurably lower (concurrent I/O).
- Graceful shutdown flushes buffers with a logged count; no lost rows.
- Reconnect backoff cap logs confirmation of application; connection-health line present.
- flake8 passes; manual Discord validation confirms behavior parity with toggles on and off.
