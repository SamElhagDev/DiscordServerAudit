---
description: "Task list for General Performance Improvements (006-performance)"
---

# Tasks: General Performance Improvements

**Input**: Design documents from `/specs/006-performance/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: No automated test tasks. The spec mandates **manual Discord validation** + flake8
lint (Testing section of plan.md); there is no existing test suite in the repo. Verification is
captured as explicit validation tasks in the Polish phase and per-story Independent Test criteria.

**Organization**: Tasks are grouped by user story. The three focus areas from the spec map to
three independently deliverable/testable stories:

- **US1 (P1)** — Per-message write batching 🎯 MVP
- **US2 (P2)** — Fact-check latency (concurrent local I/O)
- **US3 (P3)** — Reconnect / stability

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story the task belongs to (US1/US2/US3)
- Exact file paths are included in each description

## Path Conventions

Single-project Discord bot at repository root: `cogs/`, `utils/`, `bot.py`, `database.py`,
`config.yaml`. Paths below are repo-relative.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm baseline before touching hot paths.

- [X] T001 [P] Confirm no new runtime dependencies are required (only stdlib `asyncio`; `aiohttp` and `discord.py` already in use) and record a clean `flake8` baseline on `cogs/`, `utils/`, `bot.py`, `database.py` before changes.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared config surface every story reads for its toggle.

**⚠️ CRITICAL**: All three stories honor `performance.*` toggles, so this block lands first.

- [X] T002 Add the `performance:` config block to `config.yaml` with three sub-blocks and defaults: `batch_writes` (`enabled: true`, `max_rows: 50`, `max_interval_seconds: 2`, plus the best-effort-durability comment), `fast_factcheck` (`enabled: true`), and `health` (`log_latency: true`). Match keys/defaults in specs/006-performance/data-model.md.

**Checkpoint**: Config toggles exist and resolve to optimized defaults — stories can begin.

---

## Phase 3: User Story 1 - Per-message write batching (Priority: P1) 🎯 MVP

**Goal**: Coalesce the two per-message DB writes (`message_events` + `message_context`) into
bounded, size/time-flushed batches so per-message thread hops and fsyncs drop from 2N to
≈ 2⌈N/max_rows⌉, with zero loss on graceful shutdown.

**Independent Test**: Post a burst of messages; confirm every non-excluded message lands in
`message_events` and `message_context` (counts match), flush log lines appear, a graceful stop
logs a final flush count with no lost rows, and setting `performance.batch_writes.enabled: false`
restores the exact per-message write path.

### Implementation for User Story 1

- [X] T003 [P] [US1] Create `WriteBuffer` in `utils/write_buffer.py`: `start()`/`enqueue(row)`/`flush()->int`/`stop()` per contracts §1 — lock-guarded pending list, background interval-flush task (`max_rows` OR `max_interval`), single-transaction flush via an injected async `flush_fn`, structured logging with the buffer `name`, drop-batch-with-log on flush failure (no bare excepts).
- [X] T004 [US1] Add a buffer registry to `AdminBot` in `bot.py` (e.g. `self._write_buffers`): a register hook plus a flush-all/stop-all helper, and call it inside `AdminBot.close()` **before** `database.close_orphaned_voice_sessions()` and `database.close_db()`, logging total rows flushed (depends on T003).
- [X] T005 [US1] In `cogs/stats.py` `cog_load`: when `performance.batch_writes.enabled`, construct + `start()` a `message_events` `WriteBuffer` targeting `lambda rows: database.run(database.bulk_log_message_events, rows)`, and register it on the bot; `stop()` it in `cog_unload` (depends on T003, T004).
- [X] T006 [US1] Update `cogs/stats.py` `on_message`: keep all existing gate checks; compute `word_count` and `recorded_at` as today, then `enqueue((guild_id, channel_id, user_id, recorded_at, word_count))` when batching is enabled, else take the existing `database.run(database.log_message_event, ...)` path verbatim (depends on T005).
- [X] T007 [US1] In `cogs/fact_check.py` `cog_load`: when `performance.batch_writes.enabled` AND `factcheck.context.enabled`, construct + `start()` a `message_context` `WriteBuffer` targeting `lambda rows: database.run(database.bulk_log_context_messages, rows, max_chars)`, and register it on the bot; `stop()` it in `cog_unload` (depends on T003, T004).
- [X] T008 [US1] Update `cogs/fact_check.py` `capture_context_message`: keep all gate checks; build the row tuple `(guild_id, channel_id, message_id, user_id, author_name, content, recorded_at)` and `enqueue` when batching is enabled, else take the existing per-message path; preserve the `_PRUNE_EVERY` prune cadence by driving it off enqueue/flush count (depends on T007).

**Checkpoint**: US1 fully functional — batched writes with graceful-shutdown flush, toggle restores old path.

---

## Phase 4: User Story 2 - Fact-check latency (Priority: P2)

**Goal**: Cut pre-Gemini local latency by running image/reply I/O and the two context-window
queries concurrently instead of serially. Gemini call is untouched.

**Independent Test**: Trigger a fact-check on a message with multiple images + a reply; confirm
(via a temporary timing log around `_extract_content` + context build) the pre-Gemini wall-time
is lower than the sequential baseline, the resulting verdict/sources/`ContentBundle`/`ContextWindow`
are unchanged, and `performance.fast_factcheck.enabled: false` restores the sequential path.

### Implementation for User Story 2

- [X] T009 [P] [US2] Refactor `_extract_content` in `cogs/fact_check.py` to issue image-attachment reads and sticker/video-thumbnail/embed-image downloads concurrently with `asyncio.gather`, then reassemble `images` in the original priority order (attachments → stickers → thumbnails → embeds) and re-apply the `max_images` cap; preserve today's oversized/failed-image skip + WARNING logging; gate on `performance.fast_factcheck.enabled`, else keep the sequential loop.
- [X] T010 [US2] In `_extract_content` (`cogs/fact_check.py`), run `_fetch_reply_context` concurrently with the download gather so the returned `ContentBundle` is identical but assembled in one overlapped await (depends on T009).
- [X] T011 [P] [US2] Collapse the recency + relevance queries in `_build_context_window` (`cogs/fact_check.py`) so they no longer cost two serial thread hops — gather the `get_recent_context` and `get_relevant_history` `database.run` calls — keeping the resulting `ContextWindow` (selection, ordering, `seen_ids` de-dup, FTS gating) byte-for-byte identical; gate on `performance.fast_factcheck.enabled`.

**Checkpoint**: US1 and US2 both work independently; fact-check latency reduced with parity.

---

## Phase 5: User Story 3 - Reconnect / stability (Priority: P3)

**Goal**: Make the backoff cap verifiable (not a silent no-op), add a gateway-latency signal for
post-mortems, and rely on US1's shutdown-flush for data safety across reconnect churn.

**Independent Test**: On startup, logs show a confirmation of the active backoff cap (or a WARNING
+ stock fallback); a connection-health line with `bot.latency` appears on the scheduler cadence;
disabling `performance.health.log_latency` suppresses the health line.

### Implementation for User Story 3

- [X] T012 [P] [US3] Harden `cap_reconnect_backoff` in `utils/reconnect.py`: after installing `_BoundedExponentialBackoff`, verify it is the class the live reconnect path actually uses and log an INFO confirming the active cap (`max retry delay ≈ Ns`); on any mismatch/failure, log a WARNING and leave stock backoff in place (identical to today's worst case — never breaks startup).
- [X] T013 [US3] Add a connection-health watchdog registered in `bot.py` `on_ready` on the existing `IntervalScheduler` cadence: log one line with `bot.latency` (gateway heartbeat, seconds), WARNING if unavailable/`nan` or above a sane threshold, INFO/DEBUG otherwise; gate on `performance.health.log_latency`; purely observational (no reconnect action, no persistence) (depends on T002).

**Checkpoint**: All three stories independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Behavior-parity validation and lint (the feature's whole point is no behavior change).

- [ ] T014 [P] Parity validation: run the matrix {`batch_writes` on/off} × {`fast_factcheck` on/off}; confirm recorded `message_events`/`message_context` counts and fact-check verdicts/sources match the pre-006 baseline (NFR-001).
- [ ] T015 Burst throughput check: send a burst of N messages; confirm flush count ≈ ⌈N/`max_rows`⌉ per table (not N) via flush log lines (NFR-002).
- [ ] T016 Graceful-restart durability check: enqueue messages, stop the bot gracefully, confirm the final flush count equals pending rows and zero rows are lost; confirm FTS5 search returns newly-added context (triggers fired under `executemany`) (NFR-004).
- [X] T017 [P] Run `flake8` clean across `utils/write_buffer.py`, `cogs/stats.py`, `cogs/fact_check.py`, `utils/reconnect.py`, `bot.py`, `config.yaml`-adjacent code (Constitution).
- [ ] T018 Run the specs/006-performance/quickstart.md end-to-end validation and confirm CI deploy workflow is green before merging to `main`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup — provides the shared `performance.*` toggles read by all stories.
- **User Stories (Phase 3–5)**: All depend on Foundational (Phase 2). US1/US2/US3 are otherwise independent and can proceed in parallel or in priority order.
- **Polish (Phase 6)**: Depends on the stories being validated (T014–T016 exercise US1+US2; T017–T018 are cross-cutting).

### User Story Dependencies

- **US1 (P1)**: After Foundational. Self-contained (WriteBuffer + wiring + shutdown flush). No dependency on US2/US3.
- **US2 (P2)**: After Foundational. Touches different code paths (`_extract_content`, `_build_context_window`) than US1's write path — fully independent.
- **US3 (P3)**: After Foundational. `reconnect.py` + health watchdog are independent; the "buffers survive churn" claim is strongest once US1 exists but the backoff/health work is observable on its own.

### Within Each User Story

- US1: T003 (WriteBuffer) → T004 (bot registry/close flush) → then stats wiring (T005→T006) and fact-check wiring (T007→T008) can proceed in parallel with each other.
- US2: T009 → T010 (same function); T011 is independent of T009/T010.
- US3: T012 and T013 are independent (T013 needs the config from T002).

### Parallel Opportunities

- T003 [P] (new file) can start as soon as Foundational is done.
- Once T003 + T004 land, the stats branch (T005–T006) and the fact-check-context branch (T007–T008) touch different files and run in parallel.
- US2's T009/T011 and US3's T012 touch different files from US1 — different developers could run US1, US2, US3 concurrently after Phase 2.
- Polish T014 and T017 are [P] (different concerns/files).

---

## Parallel Example: after Foundational (Phase 2)

```text
# Different developers, different files, all post-Foundational:
Dev A (US1): T003 utils/write_buffer.py → T004 bot.py → T005/T006 cogs/stats.py ∥ T007/T008 cogs/fact_check.py
Dev B (US2): T009/T010 cogs/fact_check.py (_extract_content) ∥ T011 (_build_context_window)
Dev C (US3): T012 utils/reconnect.py ∥ T013 bot.py (health watchdog)
```

> Note: US1 and US2 both edit `cogs/fact_check.py` (different functions). If run concurrently,
> coordinate to avoid merge conflicts, or sequence US1's fact-check wiring before US2's edits.

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → Phase 2 Foundational (config).
2. Phase 3 US1: batching + shutdown flush.
3. **STOP and VALIDATE**: burst + graceful-restart checks (T015, T016); confirm parity with toggle off.
4. Deploy — this alone delivers the biggest "general performance" win.

### Incremental Delivery

1. Foundation → US1 (MVP, deploy) → US2 (latency, deploy) → US3 (stability, deploy).
2. Each story is independently toggleable and reverts to today's behavior when off, so each can ship without waiting on the others.

---

## Notes

- [P] = different files, no incomplete-task dependency.
- No automated tests exist in this repo; validation is manual Discord + flake8 (per spec). Do not fabricate a test suite.
- Every changed path must keep structured, guild-scoped logging and avoid bare `except: pass` (Constitution V).
- Backfill / `/scan` bulk ingestion must continue to bypass the per-message buffer.
- Commit after each task or logical group; stop at any checkpoint to validate a story independently.
