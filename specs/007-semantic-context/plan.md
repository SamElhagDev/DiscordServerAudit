# Implementation Plan: Semantic (Embedding-Based) Context Retrieval

**Branch**: `main` (single-branch workflow) | **Date**: 2026-07-14 | **Spec**: specs/007-semantic-context/spec.md
**Input**: Feature specification from `specs/007-semantic-context/spec.md`

## Summary

Add a **semantic vector tier** to the 005 fact-check context system and **fuse** it with the
existing FTS5/bm25 keyword tier so the bot reaches relevant older messages even when they share
no literal words with the trigger (the "misses older messages" recall gap). Every stored
`message_context` row is embedded with a Gemini embedding model; vectors are kept as float32
SQLite blobs in a new `message_embeddings` sidecar table and mirrored into an in-memory NumPy
index for fast cosine search. Embedding happens **off the hot path** via a background reconciler
that drains "pending" (un-embedded) rows in bounded batches — the same mechanism handles
steady-state and the one-time backfill of existing history. At fact-check time the bot embeds an
**enriched query** (reacted message + reply target + a slice of recent context), runs a cosine
similarity search, and **reciprocal-rank-fuses** it with the bm25 list into the existing relevance
slot. All changes are confined to `cogs/fact_check.py`, `database.py`, `config.yaml`, and new
`utils/` helpers. Semantic retrieval is independently toggleable and degrades to today's
recency + bm25 behavior when disabled or unavailable.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: discord.py (`Intents.message_content`), `google-genai` (existing —
`client.aio.models.embed_content` for embeddings + `genai.types.EmbedContentConfig`), `numpy`
(new: vector math for the in-memory index + cosine), sqlite3 (stdlib), aiohttp.
**Storage**: SQLite via `database.py`. New table `message_embeddings` (float32 vector BLOB keyed
to `message_context.id`, with `model` + `dim`), plus a delete trigger mirroring the existing
`message_context_fts` sync pattern so vectors are removed when context rows are pruned. **No
changes to `message_context` or existing tables.** Vectors are L2-normalized on store so cosine
similarity is a single dot product.
**Testing**: Manual Discord validation across {semantic on/off} × {context on/off}; paraphrase and
low-text-trigger recall checks; backfill-progress check via `/factcheck` status; graceful
degradation with no key / forced embedding failure; flake8 lint in CI.
**Target Platform**: Windows (Scheduled Task deployment via GitHub Actions).
**Project Type**: Discord bot (cog-modular).
**Performance Goals**: Query-time similarity search is one NumPy matmul over the in-memory index
(milliseconds at single-server scale); query embedding adds one round-trip, overlapped with the
"Checking…" placeholder. Embedding write path stays off the message hot path (reconciler batches).
Per-request retrieval cost is independent of total history size.
**Constraints**: SQLite only, no native extensions (no `sqlite-vec`); vectors as blobs + NumPy.
Keep prompt-based JSON parsing and the existing context prompt format unchanged (semantic only
changes *which* rows fill the relevance slot). Bounded embedding spend (batch + per-sweep cap).
No behavior change when disabled.
**Scale/Scope**: Single-server bot. In-memory index footprint `N × dim × 4 bytes` (default
`dim=768`, tunable). Reconciler drains pending rows at `embed_batch_size` per sweep on an
interval. Semantic tier output capped at `archive_max_messages` (shared with bm25 via fusion).
Backfill of existing history is the reconciler draining all initially-pending rows.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Cog-Modular Architecture | PASS | Semantic retrieval is a natural extension of the existing `FactCheck` cog (as 005/006 were), not a distinct domain. Logic stays in `FactCheck`; vector math/embedding client live in reusable `utils/` helpers; persistence via `database.py`. No new cog, no cross-cog imports. |
| II. Admin Role Gating (NON-NEGOTIABLE) | PASS | No new user commands. The reconciler is a background task and the reaction path is an event handler (consistent with 005's listener). Existing `/factcheck` keeps `@has_admin_role()`. |
| III. Audit-First Design | N/A | No audit-domain changes. |
| IV. AI-Augmented Recommendations (Gemini) | PASS | Embeddings are advisory/optional and gracefully degraded (no key / failure / disabled all non-fatal). Gemini makes no destructive or security decisions; it only ranks which prior messages to show as context. Model/enabled configured in `config.yaml`; spend bounded by batching. |
| V. Observability & Structured Logging | PASS | Reconciler, query embedding, similarity search, and fusion log at INFO/WARNING/ERROR with guild context; no bare excepts. Embedding failures are logged and swallowed to protect message + fact-check flow. |

**Post-Design Re-Check**: PASS. No violations. The new persistent surface is *derived* vectors
over already-stored text (no new user-text responsibility beyond 005); it is toggleable,
deletion-synced with the context store, documented in `config.yaml`, and additive (fallback to
today's behavior). See Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/007-semantic-context/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: embedding model, storage, fusion, index decisions
├── data-model.md        # Phase 1: message_embeddings schema + query/index functions + config
├── quickstart.md        # Phase 1: 5-phase implementation guide
└── contracts/
    └── semantic_context_contract.md  # embedding pipeline + retrieval/fusion + status contracts
```

### Source Code (repository root)

```text
utils/
├── embeddings.py        # NEW: Gemini embedding calls (batch docs + single query),
│                        #   task_type + output_dimensionality config, L2-normalize;
│                        #   graceful None on no-key/failure
└── vector_index.py      # NEW: in-memory (ids, matrix) index — load from message_embeddings,
                         #   add/remove on reconcile/prune, top-K cosine (one matmul);
                         #   RRF fusion helper (keyword list + semantic list)

cogs/
└── fact_check.py        # + embed-pending reconciler background task (cog_load/cog_unload);
                         #   _build_semantic_query (reacted + reply + recent slice);
                         #   _build_context_window extended to fuse bm25 + semantic;
                         #   /factcheck status shows semantic enabled/model/embedded-vs-pending

database.py              # + message_embeddings table + delete-sync trigger; helpers:
                         #   get_pending_context_rows / upsert_embeddings /
                         #   load_all_embeddings / count_embeddings / delete path kept in sync

config.yaml              # + factcheck.context.semantic.* block (enabled, model, dimensions,
                         #   max_messages, lookback_days, min_similarity, fusion.k, reconciler knobs)
```

**Structure Decision**: Single-cog change, consistent with 005/006. Embedding I/O and vector
math are extracted into small reusable `utils/` modules (mirroring `utils/gemini.py` and
`utils/write_buffer.py`) so the cog stays focused and the pieces are testable in isolation
(Principle I). All SQL stays in `database.py`.

## Complexity Tracking

> Filled because this feature adds an embedding API dependency and an in-memory vector index —
> new runtime surfaces worth justifying.

| Addition | Why Needed | Simpler Alternative Rejected Because |
|----------|------------|--------------------------------------|
| `message_embeddings` vector store | Persist one vector per message so semantic search is possible without re-embedding history every query | Re-embedding candidates per fact-check multiplies API cost and latency; not embedding at all leaves the keyword-recall gap unsolved |
| Background embed reconciler | Keep embedding off the message hot path (006) and unify steady-state + backfill idempotently | Embedding inline in `on_message` reintroduces a per-message network hop and couples capture to an external API; a separate one-shot backfill script duplicates logic and misses new rows |
| In-memory NumPy index | Sub-millisecond cosine search independent of history size | Loading all vectors from SQLite per query adds blob-read cost every fact-check; a native vector extension (`sqlite-vec`) adds a binary to the Windows Scheduled Task deployment (rejected by non-goals) |
| Hybrid fusion (RRF) over bm25 + semantic | Complementary recall (keyword nails exact tokens; vectors nail paraphrase/reference) beats either alone and preserves the bm25 fallback | Replacing bm25 loses exact-match strength (names/URLs/IDs); semantic-rerank-over-bm25 re-introduces the lexical-gap miss because no-keyword-overlap rows never enter the candidate set |

## Phase 0 — Outline & Research

Complete. See [research.md](./research.md). Resolved:
- **Embedding model/API**: `google-genai` `client.aio.models.embed_content` with
  `EmbedContentConfig(task_type=…, output_dimensionality=…)`; `RETRIEVAL_DOCUMENT` for stored
  rows, `RETRIEVAL_QUERY` for the query; L2-normalize (cosine = dot). Default model
  `gemini-embedding-001`, `dim=768`.
- **Storage**: sidecar `message_embeddings` keyed to `message_context.id`, float32 blob + model +
  dim; delete trigger for prune-sync (mirrors 005's FTS triggers). No hot-path change.
- **Pipeline**: background reconciler drains pending (un-embedded) rows in bounded batches; same
  path backfills existing history. Idempotent, rate-limited, self-healing.
- **Retrieval + fusion**: in-memory NumPy index, top-K cosine; enriched query embedding; RRF with
  the existing bm25 list into the capped relevance slot. Graceful fallback to bm25/recency.

## Phase 1 — Design & Contracts

Complete. Artifacts:
- [data-model.md](./data-model.md) — `message_embeddings` schema + trigger, blob (de)serialization,
  new `database.py` functions, `utils` index/embedding contracts, config keys.
- [contracts/semantic_context_contract.md](./contracts/semantic_context_contract.md) — reconciler,
  query builder, similarity search, fusion, extended `_build_context_window`, `/factcheck` status.
- [quickstart.md](./quickstart.md) — 5-phase implementation guide with per-phase verification.

Agent context: `CLAUDE.md` plan pointer updated to this file.

## Phase 2 — Next step

Run `/speckit-tasks` to generate `tasks.md` (dependency-ordered) from these artifacts. Suggested
ordering mirrors quickstart: config + schema/trigger → embedding + index utils → reconciler
(backfill drains here) → query builder + similarity + fusion in `_build_context_window` →
status surfacing + graceful-degradation/parity validation.
