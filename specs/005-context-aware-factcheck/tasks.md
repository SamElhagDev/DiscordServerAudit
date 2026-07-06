---
description: "Task list for Context-Aware & Web-Grounded Fact-Check"
---

# Tasks: Context-Aware & Web-Grounded Fact-Check

**Input**: Design documents from `specs/005-context-aware-factcheck/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/fact_check_contract.md, quickstart.md

**Tests**: Automated tests are NOT included — the plan specifies manual Discord validation +
flake8 lint (consistent with prior fact-check features 002/004). Verification steps are folded
into each story's checkpoint rather than as separate test tasks.

**Organization**: Tasks are grouped by user story. US1 (web grounding) is fully independent of
the context-storage work and is the recommended MVP. US2 → US3 → US4 build on a shared storage
substrate created in the Foundational phase.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4; Setup/Foundational/Polish carry no story label
- All paths are repo-relative. The feature touches only 3 files:
  `cogs/fact_check.py`, `database.py`, `config.yaml` (no new cog — `FactCheck` is already
  registered in `bot.py:COGS`).

---

## Phase 1: Setup (Shared Configuration)

**Purpose**: Add all new config keys with documented, safe defaults before any code reads them.

- [X] T001 Add the `factcheck.grounding.*` block to `config.yaml` (keys: `enabled: true`,
  `max_sources: 5`, `require_source_for_negative: true`) with inline comments per data-model §5.
- [X] T002 Add the `factcheck.context.*` block to `config.yaml` — storage (`enabled: true`,
  `storage_retention_days: 0`, `max_messages_per_channel: 0`, `max_stored_chars: 2000`),
  recency (`recency_window_hours: 168`, `max_context_messages: 25`, `same_channel_limit: 15`),
  relevance (`history_relevance.enabled: true`, `lookback_days: 0`, `archive_max_messages: 10`,
  `min_score: 0.0`), and backfill (`backfill_messages_per_channel: 1000`,
  `backfill_channel_delay: 0.5`) — per data-model §5.

**Checkpoint**: `config.get(...)` returns the new keys with defaults; bot still starts; flake8 clean.

---

## Phase 2: Foundational (Shared Context Storage Substrate)

**Purpose**: The message store + capture path that US2, US3, and US4 all build on.

**⚠️ Blocking for US2/US3/US4** (US1 does NOT depend on this phase and may proceed in parallel).

- [X] T003 Add the `message_context` table + `idx_message_context_channel` and
  `idx_message_context_guild_time` indexes to the schema-init block in `database.py`
  (columns and `UNIQUE(message_id)` per data-model §1).
- [X] T004 Implement `log_context_message(...)` (idempotent `INSERT OR IGNORE`, truncate
  `content` to `max_stored_chars`) and `count_message_context(guild_id)` in `database.py`.
- [X] T005 Implement `prune_message_context(retention_cutoff_iso, max_per_channel)` in
  `database.py` — time-delete (skip when `storage_retention_days == 0`) + optional per-channel
  trim (skip when `max_per_channel == 0`); return rows deleted.
- [X] T006 Add the `on_message` listener to `FactCheck` in `cogs/fact_check.py` (contract C2):
  gate on `context.enabled`, guild-only, non-bot, non-empty stripped text; call
  `log_context_message`; run an amortized `prune_message_context` every Nth insert; wrap all of
  it so exceptions are logged and swallowed (never disrupt message flow). Do not call
  `process_commands`.

**Checkpoint**: Sending messages populates `message_context`; bots/empty messages skipped;
duplicates ignored; a finite `storage_retention_days` prunes old rows; listener errors never
break message flow.

---

## Phase 3: User Story 1 — Web-grounded, anti-denial fact-checks (Priority: P1) 🎯 MVP

**Goal**: Fact-checks verify claims against live Google Search instead of stale training data,
so real/recent articles are no longer dismissed as nonexistent; sources are cited; and an
unsourced "Mostly False" is downgraded rather than asserted.

**Independent Test**: React to (a) a claim citing a post-cutoff article → verified with a
"Sources" list, not "doesn't exist"; (b) a date-dependent claim → checked against searched
dates; (c) a fabricated source with no grounding → verdict shows "Unverifiable" (downgraded),
with a log line. No database/context work required for any of this.

**Dependencies**: Phase 1 only (grounding config). Independent of Phase 2.

- [X] T007 [US1] Extend `_call_gemini` in `cogs/fact_check.py` to accept `grounding: bool`,
  build `GenerateContentConfig(tools=[Tool(google_search=GoogleSearch())], temperature=0.2)`
  when enabled, pass `config=` to `generate_content`, and return
  `(result: dict | None, sources: list[GroundingSource])` (contract C4; keep the existing JSON
  text-parsing path — do NOT add `response_schema`).
- [X] T008 [US1] Add the `GroundingSource` dataclass and defensive parsing of
  `response.candidates[0].grounding_metadata.grounding_chunks[].web` (title/uri; missing
  attributes → empty list) in `cogs/fact_check.py` (data-model §4).
- [X] T009 [US1] Add the grounding instruction block to `_build_content_parts` in
  `cogs/fact_check.py` when grounding is on (FR-4a / research §A): training data may be stale;
  search to verify existence + dates before judging; prefer live results on conflict; treat
  unfamiliar references as things to look up; genuinely unfindable → Unverifiable, not False.
- [X] T010 [US1] Implement the negative-verdict guardrail in `cogs/fact_check.py` (contract
  C4a / FR-4b): when `grounding.enabled` and `grounding.require_source_for_negative`, if
  `verdict == "Mostly False"` and `sources == []`, downgrade to `"Unverifiable"`, append a note
  to `analysis`, and log the downgrade (guild, message_id, `Mostly False → Unverifiable`).
- [X] T011 [US1] Extend `_build_embed` in `cogs/fact_check.py` to accept `sources` and render a
  "Sources" field (`[title](uri)`, title→host fallback), capped at `grounding.max_sources`,
  only when ≥1 source; append " · web-grounded" to the footer when sources contributed
  (contract C1).
- [X] T012 [US1] Wire the reaction handler in `cogs/fact_check.py`: pass
  `grounding=config.get("factcheck.grounding.enabled", True)` into `_call_gemini`, apply the
  T010 guardrail to `(result, sources)` before `_build_embed`, and pass `sources` to the embed.
  Grounding failure degrades to a normal ungrounded verdict (never errors the check).

**Checkpoint**: US1 is independently shippable — grounded verdicts, citations, and the guardrail
all work with the context feature absent/disabled.

---

## Phase 4: User Story 2 — Conversational (recency) context (Priority: P2)

**Goal**: Verdicts understand references to recent messages ("that article above", "he said")
by injecting a recency-windowed, server-wide slice of prior conversation.

**Independent Test**: With the store populated, react to a message like "is that true?" that
references an earlier claim → the verdict resolves the reference using injected context.

**Dependencies**: Phase 2 (store + listener + `log_context_message`).

- [X] T013 [P] [US2] Add `ContextWindow` and `ContextMessage` (fields incl. `source:
  "recency" | "relevance"`) dataclasses in `cogs/fact_check.py` (data-model §2).
- [X] T014 [P] [US2] Implement `get_recent_context(guild_id, channel_id, same_channel_limit,
  total_limit, since_iso)` in `database.py` — merged same-channel + server-wide rows newer than
  `since_iso` (= now − `recency_window_hours`), newest first.
- [X] T015 [US2] Implement `_build_context_window(message)` tier-1 assembly in
  `cogs/fact_check.py` (contract C3, data-model §2): same-channel + server-wide recency merge,
  dedupe by `message_id`, exclude the triggering message, cap at `max_context_messages`.
  (Depends on T013, T014.)
- [X] T016 [US2] Implement `_format_context_block(window)` in `cogs/fact_check.py` — render the
  delimited "prior conversation" block with `[#channel] Author: text` lines; empty window → `""`
  (contract C3).
- [X] T017 [US2] Inject the context block in `_build_content_parts` (before the
  message-under-check text) and call `_build_context_window` from the reaction handler, gated on
  `context.enabled`. Instruct the model NOT to fact-check the context lines themselves.

**Checkpoint**: References to recent messages resolve; with `context.enabled: false` behavior
matches pre-US2.

---

## Phase 5: User Story 3 — Full-history relevance retrieval via FTS5 (Priority: P3)

**Goal**: A request can reference the most *relevant* messages from **all retained history**
(not just the recency window), at a bounded per-request cost, via SQLite FTS5 + bm25.

**Independent Test**: Post a message, later (outside the recency window, or with a short
`recency_window_hours`) make a claim sharing distinctive keywords → the old message surfaces in
a "possibly-related earlier messages" block. Per-request cost stays flat as history grows.

**Dependencies**: Phase 2 (base table) + US2 (`_build_context_window` / `_format_context_block`
to extend).

- [X] T018 [P] [US3] Add `fts5_available()` capability probe in `database.py` (one-time
  `CREATE VIRTUAL TABLE … USING fts5` in-memory check; cache result).
- [X] T019 [US3] Add the `message_context_fts` external-content FTS5 virtual table +
  `message_context_ai` / `message_context_ad` sync triggers to schema init in `database.py`,
  created only when `fts5_available()` (data-model §1b). (Depends on T003, T018.)
- [X] T020 [US3] Implement `get_relevant_history(guild_id, match_query, limit, exclude_ids,
  since_iso=None, min_score=0.0)` in `database.py` — FTS5 `MATCH` ordered by `bm25`, optional
  `since_iso` (from `lookback_days`) + bm25 floor, excluding `exclude_ids`; return `[]` when
  FTS5 unavailable. (Depends on T019.)
- [X] T021 [P] [US3] Implement `_history_query_terms(text)` in `cogs/fact_check.py` — deterministic
  FTS query from the checked message (lowercase, strip stopwords, keep quoted phrases, prefer
  capitalized/number/URL tokens); return `None` when nothing meaningful remains (contract C3).
- [X] T022 [US3] Extend `_build_context_window` in `cogs/fact_check.py` with the tier-2
  relevance step (data-model §2): gated on `history_relevance.enabled` AND `fts5_available()`;
  call `get_relevant_history` with `_history_query_terms`, dedupe against tier-1, cap at
  `archive_max_messages`; any failure/unavailability → recency-only, never errors.
  (Depends on T020, T021, T015.)
- [X] T023 [US3] Extend `_format_context_block` in `cogs/fact_check.py` to render a separate,
  clearly-labeled "possibly-related earlier messages" block for `source == "relevance"`
  entries (distinct from the recency block). (Depends on T016, T022.)

**Checkpoint**: Old-but-relevant messages surface; with FTS5 unavailable or
`history_relevance.enabled: false`, the check still runs on the recency tier without error.

---

## Phase 6: User Story 4 — Admin backfill / refresh (Priority: P4)

**Goal**: An admin can seed the store from existing channel history so context/relevance work
immediately (rather than only from new messages onward).

**Independent Test**: On an empty store, run `/factcheck refresh` → rows appear; a second run
inserts 0 duplicates; a finite `storage_retention_days` clips older imports.

**Dependencies**: Phase 2 (`log_context_message` + filters).

- [X] T024 [P] [US4] Add `latest_context_timestamp(guild_id, channel_id)` in `database.py`
  (optional backfill optimization; newest `recorded_at` per channel).
- [X] T025 [US4] Implement the `/factcheck refresh` admin command in `cogs/fact_check.py`
  (contract C6): `@has_admin_role()` + `@commands.guild_only()`; no-op reply when
  `context.enabled` is false; iterate readable text channels; `channel.history(...)` up to
  `backfill_messages_per_channel` (with `after=<now − storage_retention_days>` only when finite,
  no age bound at unlimited); reuse listener filters + `log_context_message`;
  `asyncio.sleep(backfill_channel_delay)` between channels; per-channel `Forbidden`/errors
  logged and skipped; reply with a channels-scanned / rows-inserted summary embed.

**Checkpoint**: Refresh seeds the store, is admin-gated, idempotent, and rate-limited.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Surface state, document, and verify graceful degradation across all combinations.

- [X] T026 Extend the `/factcheck` status command embed in `cogs/fact_check.py` (contract C5):
  context on/off + retention (`storage <N>d`/`forever`, `recency <H>h`), history relevance
  on/off/`unavailable` (+ lookback), store size via `count_message_context`, web grounding
  on/off + require-source guardrail on/off. (Depends on US1–US4.)
- [X] T027 [P] Update `README.md` + `config.yaml` docs: message-text storage, `storage_retention_days`
  defaults to **unlimited** (indefinite retention — privacy note) and how to set a finite value,
  the three retention dials, grounding, and the negative-verdict guardrail.
- [ ] T028 Run the graceful-degradation matrix (quickstart Phase 6): no `gemini_key`, grounding
  failure, empty store, FTS5 unavailable, `history_relevance`/`context` disabled → each still
  returns a normal verdict. Confirm both features off reproduce current behavior/latency.
- [ ] T029 `flake8` clean across `cogs/fact_check.py`, `database.py`; run the manual matrix
  {context on/off} × {grounding on/off} × {unlimited vs finite `storage_retention_days`}.

---

## Dependencies & Execution Order

- **Phase 1 (Setup)** → prerequisite for everything.
- **Phase 2 (Foundational)** → prerequisite for **US2, US3, US4**. **US1 does NOT depend on it.**
- **US1 (P1)** → needs only Phase 1. Ship as MVP.
- **US2 (P2)** → needs Phase 2.
- **US3 (P3)** → needs Phase 2 + US2 (extends `_build_context_window` / `_format_context_block`;
  FTS table depends on the base table T003).
- **US4 (P4)** → needs Phase 2.
- **Polish (T026)** → needs US1–US4 (reports all states). T027–T029 can run last.

### Story completion order (recommended)
Setup → Foundational → US1 (MVP) → US2 → US3 → US4 → Polish.
(US1 may be implemented before Foundational since it is independent.)

### Parallel opportunities
- After Phase 1: **US1 (Phase 3) can proceed in parallel with Phase 2 Foundational** — different
  concerns, US1 touches only the Gemini-call/embed paths.
- `[P]` cross-file pairs within a story (different files, no dependency):
  - US2: T013 (`cogs/fact_check.py` dataclasses) ⟂ T014 (`database.py`).
  - US3: T018 (`database.py` probe) ⟂ T021 (`cogs/fact_check.py` query terms).
  - US4: T024 (`database.py`) ⟂ US4 cog work.
  - Polish: T027 (docs) ⟂ code tasks.
- Most `cogs/fact_check.py` tasks within a story are sequential (same file).

## Implementation Strategy

- **MVP = US1 alone**: delivers the highest-value, most-requested fix (stop denying real/recent
  articles + citations + guardrail) with zero storage/privacy footprint. Fully shippable.
- **Increment 2 = US2**: conversational recency context (requires the store).
- **Increment 3 = US3**: full-history relevance (the "reference anything ever posted" capability).
- **Increment 4 = US4**: backfill so context/relevance are useful immediately.
- **Polish** last: status surfacing, docs (incl. the unlimited-retention privacy note), and the
  degradation matrix.

---

## Summary

- **Total tasks**: 29
- **Per phase**: Setup 2 · Foundational 4 · US1 6 · US2 5 · US3 6 · US4 2 · Polish 4
- **MVP**: US1 (T007–T012) — independent of all database/context work.
- **Files touched**: `cogs/fact_check.py`, `database.py`, `config.yaml` (+ `README.md` in polish).
  No new cog; `FactCheck` already registered in `bot.py:COGS`.
- **Tests**: manual Discord validation + flake8 (no automated test tasks, per plan).
