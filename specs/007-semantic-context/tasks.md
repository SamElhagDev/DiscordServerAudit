---
description: "Task list for feature 007 — Semantic (Embedding-Based) Context Retrieval"
---

# Tasks: Semantic (Embedding-Based) Context Retrieval

**Input**: Design documents from `specs/007-semantic-context/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: OMITTED. Per spec §Testing this feature uses **manual Discord validation**, not TDD.
No automated test tasks are generated (consistent with features 005/006).

**Organization**: Tasks are grouped by user story (derived from spec Success Criteria) so each is
independently implementable and testable. Semantic retrieval is a single pipeline, so US1 is the
substantive MVP; US2/US3 are incremental enhancements.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story the task belongs to (US1/US2/US3); Setup/Foundational/Polish have none
- Exact file paths are included in each description

## Path Conventions

Single-project Discord bot; source at repository root (`cogs/`, `utils/`, `database.py`,
`config.yaml`) per plan.md structure.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependencies and config scaffolding — no behavior change yet.

- [X] T001 Add `numpy` to `requirements.txt` (new dependency for vector math / in-memory index)
- [X] T002 [P] Add the `factcheck.context.semantic.*` block to `config.yaml` with documented safe defaults per [data-model.md](./data-model.md) (`enabled`, `model`, `dimensions`, `max_messages`, `lookback_days`, `min_similarity`, `query_context_messages`, `fusion_k`, `embed_batch_size`, `embed_interval_seconds`, `max_pending_per_sweep`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Storage, DB access, embedding I/O, and vector math that ALL user stories depend on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Add the `message_embeddings` table + `message_context_ad_emb` delete trigger and an `_init_message_embeddings()` initializer in `database.py`, invoked from `init_db` next to `_init_message_context_fts()` (schema per [data-model.md](./data-model.md))
- [X] T004 Implement DB helpers in `database.py` — `get_pending_context_rows(limit)`, `upsert_embeddings(rows)`, `load_all_embeddings(model, dim)`, `count_embeddings(guild_id=None)` (blob float32 (de)serialization; `INSERT OR REPLACE`; skip mismatched `(model, dim)`) — depends on T003
- [X] T005 [P] Create `utils/embeddings.py` — `embed_available()`, `async embed_documents(texts)`, `async embed_query(text)` over `utils/gemini.get_client()` using `EmbedContentConfig(task_type=…, output_dimensionality=…)`, L2-normalized float32, `None` on no-key/failure (contract §; research §A)
- [X] T006 [P] Create `utils/vector_index.py` — `VectorIndex(dim)` with `load/add/remove/search(query_vec, k, min_sim, allowed_ids)` (numpy matmul cosine) plus module-level `rrf_fuse(keyword_ids, semantic_ids, k)` (research §C/§E)

**Checkpoint**: Table + trigger exist and prune-sync; utils exercisable offline; fact-check behavior still unchanged (nothing embeds yet).

---

## Phase 3: User Story 1 — Semantic recall of differently-worded history (Priority: P1) 🎯 MVP

**Goal**: A fact-check retrieves a relevant older message even when it shares **no keywords** with the trigger, by embedding the store and fusing semantic hits with bm25.

**Independent Test**: React-to-factcheck a message whose relevant prior context is a paraphrase (no shared words) → it appears in the relevance context (absent pre-007). Set `semantic.enabled: false` → output identical to today.

- [X] T007 [US1] Add `_vector_index` and `_reconciler_task` fields in `FactCheck.__init__`; construct `VectorIndex(dimensions)` and populate it via `load_all_embeddings` in `cog_load`; tear down in `cog_unload` (alongside the existing `_context_buffer` lifecycle) in `cogs/fact_check.py` — depends on T004, T006
- [X] T008 [US1] Implement the embed-pending reconciler background task in `cogs/fact_check.py` (contract §1): on `embed_interval_seconds`, drain pending rows in `embed_batch_size` batches bounded by `max_pending_per_sweep` → `embed_documents` → `upsert_embeddings` → `index.add`; start/cancel in `cog_load`/`cog_unload`; INFO per sweep, WARNING on batch failure; existing history backfills here — depends on T007, T005
- [X] T009 [US1] Extend `_build_context_window` in `cogs/fact_check.py` (contract §5) to add the semantic tier: query = reacted `message.content`; `embed_query` (overlapped with the "Checking…" send) → `index.search` → `rrf_fuse` with the existing bm25 ids → cap at `history_relevance.archive_max_messages`, excluding recency + trigger; render fused rows in the unchanged relevance section — depends on T006, T008
- [X] T010 [US1] Guard every semantic step in `cogs/fact_check.py` for graceful fallback (no key / `semantic.enabled: false` / cold index / query-embed failure → recency + bm25 exactly as today); log and swallow, no bare excepts (contract §7) — depends on T009

**Checkpoint**: Paraphrase recall works; semantic toggle cleanly reverts to 005/006 behavior. MVP is shippable here.

---

## Phase 4: User Story 2 — Robust retrieval for low-text triggers (Priority: P2)

**Goal**: Reacting to an image, a bare link, or "is this true?" still retrieves relevant history via an enriched query embedding.

**Independent Test**: React to a low-text trigger whose relevant context lives in a reply target / recent messages → relevant history is retrieved (where US1's bare-trigger query returned little).

- [X] T011 [US2] Implement `_build_semantic_query(message)` in `cogs/fact_check.py` (contract §2): reacted content + reply-target content (if a reply) + up to `semantic.query_context_messages` recent same-channel lines; return `None` if the combined text is empty — depends on T009
- [X] T012 [US2] Use `_build_semantic_query` as the query source in `_build_context_window` (replacing the bare reacted-text query from T009); `None` → skip the semantic tier for that check, in `cogs/fact_check.py` — depends on T011

**Checkpoint**: US1 + US2 both work; low-text triggers now retrieve good context.

---

## Phase 5: User Story 3 — Backfill & semantic visibility in status (Priority: P3)

**Goal**: `/factcheck` status shows semantic state and backfill progress so an admin can monitor it.

**Independent Test**: Run `/factcheck` → status shows semantic enabled/available, model, dim, and embedded-vs-pending counts that climb as the reconciler drains.

- [X] T013 [US3] Extend the `/factcheck` status output in `cogs/fact_check.py` (contract §6) with semantic lines: enabled/available, `model` + `dim`, and `embedded=count_embeddings / total=count_message_context (pending=…)` — depends on T004 (reads vectors written by US1)

**Checkpoint**: All three stories independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Validation, parity, and documentation across the feature.

- [ ] T014 [P] Walk the full degradation matrix (contract §7) in a live/dev guild — disabled, no key, forced reconciler-embed failure, forced query-embed failure, cold index, model/dim mismatch — confirming recency + bm25 fallback with no errors in each case
- [ ] T015 [P] Parity + performance check: {semantic on/off} × {context on/off} matrix produces sane verdicts; confirm the generative prompt caps (`max_context_messages`, `archive_max_messages`) are unchanged so generative token cost stays flat; confirm query embedding overlaps the "Checking…" send
- [X] T016 [P] Run `flake8` clean over new/changed files (`utils/embeddings.py`, `utils/vector_index.py`, `database.py`, `cogs/fact_check.py`)
- [ ] T017 Finalize `config.yaml` comments for the `semantic.*` block including model-migration (change model/dim → auto re-embed) and cost notes (embedding is a separate cheaper meter; caps unchanged); run [quickstart.md](./quickstart.md) validation end-to-end

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies — start immediately.
- **Foundational (Phase 2)**: after Setup — BLOCKS all user stories.
- **User Stories (Phase 3–5)**: all require Foundational. US1 is the MVP; US2 depends on US1's query path; US3 depends only on the Foundational DB helpers (T004) but is most meaningful once US1 is embedding.
- **Polish (Phase 6)**: after the desired stories are complete.

### User Story Dependencies

- **US1 (P1)**: after Foundational. The substantive pipeline (embed → search → fuse).
- **US2 (P2)**: after US1 (enriches the query built in T009).
- **US3 (P3)**: after Foundational T004; independent of US1/US2 logic, but its counts only move once US1's reconciler runs.

### Within Each Story

- Lifecycle wiring (T007) before the reconciler (T008) before retrieval/fusion (T009) before hardening (T010).
- US2 query builder (T011) before wiring it in (T012).

### Parallel Opportunities

- Setup: T002 [P] runs alongside T001 (different files).
- Foundational: T005 [P] and T006 [P] (new files) run in parallel with each other and with the `database.py` work (T003→T004, which are sequential — same file).
- Polish: T014/T015/T016 [P] are independent validation passes.

---

## Parallel Example: Foundational Phase

```text
# After T003→T004 (database.py, sequential), launch the two new util modules together:
Task: "Create utils/embeddings.py (embed_available/embed_documents/embed_query)"   # T005
Task: "Create utils/vector_index.py (VectorIndex + rrf_fuse)"                       # T006
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → 2. Phase 2 Foundational (CRITICAL) → 3. Phase 3 US1 →
4. **STOP and VALIDATE**: paraphrase recall works, toggle-off reverts to 005/006 →
5. Ship the MVP.

### Incremental Delivery

1. Setup + Foundational → foundation ready.
2. US1 → validate → ship (MVP: semantic recall).
3. US2 → validate → ship (low-text trigger robustness).
4. US3 → validate → ship (status/backfill visibility).
5. Polish → degradation/parity/lint/docs.

---

## Notes

- [P] = different files, no dependencies. `database.py` tasks (T003, T004) are same-file → sequential.
- Tests omitted by design — validate manually per quickstart (spec §Testing).
- Backfill is not a separate task: it is the reconciler (T008) draining the initial pending backlog under `max_pending_per_sweep`.
- Cost posture is preserved: the generative prompt caps are untouched (T015 verifies), so semantic retrieval improves *which* messages are shown, not *how many* — embedding spend is the only new (cheaper-meter) cost.
- Commit after each task or logical group; stop at any checkpoint to validate a story independently.
