# Quickstart — Implementing Semantic Context Retrieval (007)

Five phases, each independently verifiable. Every phase preserves graceful degradation: with
`factcheck.context.semantic.enabled: false` the bot behaves exactly as 005/006.

## Phase 1 — Config + schema (no behavior change yet)

1. Add the `factcheck.context.semantic.*` block to `config.yaml` (see data-model.md), defaults as
   listed. Keep `enabled: true` but note nothing embeds until Phase 3 exists.
2. In `database.py`: add `_init_message_embeddings()` (table + delete trigger from data-model.md),
   call it from `init_db` next to `_init_message_context_fts()`. Add `get_pending_context_rows`,
   `upsert_embeddings`, `load_all_embeddings`, `count_embeddings`.

**Verify**: bot starts; `message_embeddings` table + trigger exist; deleting a `message_context`
row removes any matching embedding row; `count_embeddings()` returns 0; existing fact-check
behavior unchanged.

## Phase 2 — Embedding + index utils (isolated, testable)

1. `utils/embeddings.py`: `embed_available`, `embed_documents`, `embed_query` over
   `utils/gemini.get_client()`, with `task_type` + `output_dimensionality`, L2-normalize, `None`
   on no-key/failure.
2. `utils/vector_index.py`: `VectorIndex` (`load/add/remove/search`) + `rrf_fuse`.

**Verify**: unit-exercise offline — `embed_documents(["a","b"])` returns normalized vectors of the
configured dim (or `None` with no key); `VectorIndex.search` returns the nearest id for a known
vector; `rrf_fuse([1,2,3],[3,4,5])` ranks 3 highly (in both lists).

## Phase 3 — Reconciler + backfill (write path)

1. In `FactCheck`: build the `VectorIndex` and load it from `load_all_embeddings` in `cog_load`;
   start the reconciler task; cancel it in `cog_unload`.
2. Reconciler drains pending rows in bounded batches (contract §1), upserts vectors, `index.add`.

**Verify**: with a valid key, watch INFO logs drain existing history over successive sweeps;
`count_embeddings()` climbs toward `count_message_context()`; new live messages become embedded
within a sweep or two; killing the network mid-sweep leaves rows pending and they re-embed later
(no duplicates). With no key: reconciler no-ops, logs "unavailable", fact-checks still work.

## Phase 4 — Retrieval + fusion (read path)

1. `_build_semantic_query(message)` (contract §2).
2. Extend `_build_context_window` to embed the query (overlapped with the "Checking…" send),
   `index.search`, and `rrf_fuse` with the existing bm25 ids into the capped relevance slot
   (contract §5). Exclude recency + trigger ids.

**Verify**: react-to-factcheck a message whose relevant prior context is a **paraphrase** (no
shared keywords) — it now appears in the relevance context (previously absent). React to a
**low-text** trigger (image / "is this true?") — relevant history is retrieved via the enriched
query. Confirm a strong exact-keyword hit still appears (fusion didn't drop it). Toggle
`semantic.enabled: false` → identical output to pre-007.

## Phase 5 — Status surfacing + hardening

1. Extend `/factcheck` status with semantic enabled/model/dim + embedded-vs-pending counts
   (contract §6).
2. Walk the degradation matrix (contract §7): disabled, no key, forced query-embed failure, cold
   index, model/dim mismatch — each must fall back to bm25 + recency without error.

**Verify**: `/factcheck` shows accurate backfill progress; every degradation row behaves as
specified; flake8 clean; {semantic on/off} × {context on/off} matrix all sane.

## Rollout notes

- First run with a large existing `message_context` will backfill gradually under
  `max_pending_per_sweep` — expected, non-blocking. Watch `/factcheck` status for progress.
- To change model or `dimensions`: update config; the store re-embeds under the new `(model, dim)`
  automatically (old vectors skipped by the loader, re-embedded by the reconciler).
- Cost control: raise `embed_interval_seconds` / lower `embed_batch_size` to slow spend; set
  `semantic.enabled: false` to stop embedding entirely (existing vectors remain, unused).
