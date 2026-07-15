# Spec: Semantic (Embedding-Based) Context Retrieval

**Date**: 2026-07-14 | **Feature**: 007-semantic-context
**Status**: Draft

## Summary

The 005 fact-check context system retrieves prior messages with two tiers: a **recency**
tier (recent messages) and a **relevance** tier that ranks retained history with SQLite
**FTS5/bm25** — pure keyword matching. Keyword matching can only surface a message that shares
literal words with the trigger, so it **misses older messages** that are relevant but phrased
differently, and it degrades to noise when the reacted-to message has little text (an image, a
bare link, "is this true?").

This feature adds a **semantic vector tier**: every stored message is embedded with Gemini's
embedding model, and at fact-check time the relevant history is found by **meaning** (cosine
similarity) as well as by keyword. The two signals are **fused** (reciprocal-rank fusion) so
the relevance slot draws on both. The result is higher recall of old-but-relevant context
without losing keyword's edge on exact names/URLs/IDs. Semantic retrieval is purely additive
and toggleable: with it off (or unavailable), the bot behaves exactly as 005/006 do today.

## Goals

- Embed every captured `message_context` row with a Gemini embedding model, stored locally as
  a vector so history can be searched by semantic similarity.
- Generate embeddings **in batches** off the hot path (the 006 write buffer keeps capture a
  plain insert); a single background reconciler embeds "pending" rows, which unifies steady-state
  embedding and one-time backfill of existing history under one rate-limited mechanism.
- At fact-check time, run a semantic similarity search alongside the existing bm25 search and
  **fuse** the two ranked lists into the relevance tier (deduped against recency + trigger).
- Build the semantic query from more than the reacted message alone — include its reply target
  and a small slice of recent conversation — so low-text triggers still retrieve good context.
- Keep per-check retrieval fast (an in-memory vector index; one matmul per query) and bounded.
- Remain fully backward compatible and gracefully degraded: no API key, embedding failure,
  empty index, or `semantic.enabled: false` MUST fall back to today's recency + bm25 behavior.

## Non-Goals

- Cross-encoder / LLM re-ranking of retrieved candidates (semantic + fusion only for v1).
- External or native vector stores (`sqlite-vec`, FAISS, a vector DB). Vectors are SQLite blobs
  scored with NumPy in-process — no native extension to deploy.
- Embedding attachments, images, or media (text only, consistent with 005).
- Changing the reaction trigger, verdict taxonomy, grounding behavior, or the recency tier.
- Replacing the bm25 relevance tier — it is kept as a complementary signal and as the fallback
  path when embeddings are unavailable.
- A user-facing semantic-search UI over history. Vectors exist solely to feed fact-check context.

## User-Visible Behavior

- Reacting with the fact-check emoji works exactly as before. Verdicts now additionally resolve
  references to earlier messages that are **worded differently** from the trigger, and low-text
  triggers (images, "is that true?") retrieve better context.
- `/factcheck` status reports whether semantic retrieval is enabled and available, the embedding
  model, and how much of the context store has been embedded (embedded / pending counts).
- No change to the verdict embed, the "Sources" section, or the context prompt format.

## Functional Requirements

- **FR-1**: A new `message_embeddings` store MUST hold one vector per embedded `message_context`
  row, keyed to `message_context.id`, recording the embedding model and dimensionality. It MUST
  stay in sync with `message_context` deletion (pruning) so no orphan vectors accumulate.
- **FR-2**: Embedding generation MUST run **off the message hot path**. Capture stays a plain
  buffered insert (006); a background reconciler selects `message_context` rows lacking an
  embedding and embeds them in **bounded batches** via a single Gemini embedding call per batch.
- **FR-2a**: The reconciler MUST be idempotent and self-healing — a row with no vector is
  "pending"; a failed batch leaves its rows pending for a later sweep; re-running MUST NOT create
  duplicate vectors. Existing history (all pending at rollout) MUST drain under a configurable
  per-sweep cap so backfill and steady-state share one mechanism.
- **FR-3**: At fact-check time, when semantic retrieval is enabled and available, the bot MUST
  embed a **query** built from the reacted-to message plus (when present) its reply target and a
  bounded slice of recent same-channel context, then retrieve the top-K most similar stored
  messages by cosine similarity.
- **FR-4**: The semantic result list and the existing bm25 result list MUST be **fused** with
  reciprocal-rank fusion into a single relevance list, capped at
  `factcheck.context.history_relevance.archive_max_messages`, and deduped against the recency
  tier and the trigger message. The fused list feeds the existing relevance section of the
  prompt unchanged (FR-3/FR-3a of 005 still hold: retrieval is server-wide; the verdict is about
  the reacted-to message only, and context lines MUST NOT be fact-checked).
- **FR-5**: Semantic similarity search MUST be compute-effective and bounded: an in-memory vector
  index (loaded at startup, kept in sync as the reconciler adds and pruning removes vectors),
  one similarity pass per query, capped candidate/output sizes, optionally bounded by
  `semantic.lookback_days` and floored by `semantic.min_similarity`. Per-request cost MUST be
  independent of total history size.
- **FR-6**: All new behavior MUST degrade gracefully. Missing API key, embedding call failure,
  an empty or partially-built index, `semantic.enabled: false`, or a model/dimension mismatch
  MUST NOT break a fact-check — the bot falls back to recency + bm25 exactly as today.
- **FR-7**: All new config keys MUST live under `factcheck.context.semantic.*` in `config.yaml`
  with safe defaults and MUST be independently toggleable. Semantic retrieval MUST be disableable
  without disabling the rest of the context system.
- **FR-8**: Vector generation MUST be gated by both `factcheck.context.enabled` (no stored text →
  nothing to embed) and `factcheck.context.semantic.enabled`. When semantic is disabled, no
  embedding calls are made and no vectors are written.
- **FR-9**: The embedding model and target dimensionality MUST be configurable. A change in model
  or dimension MUST be detected (rows recorded under a different model/dim are treated as pending
  / ignored by the index) so the store can be re-embedded safely without serving mismatched
  vectors.
- **FR-10**: `/factcheck` status MUST surface semantic state: enabled/available, model,
  dimensions, and embedded-vs-pending counts, so an admin can see backfill progress.
- **FR-11**: Observability — the reconciler (batch size, latency, success/failure counts), the
  query embedding, similarity search, and fusion MUST log at INFO/WARNING/ERROR with guild
  context. No bare excepts; embedding failures are logged and swallowed to protect message flow
  and fact-check flow.

## Privacy & Constraints

- No new user-text surface: embeddings are derived from text already stored by 005 under the same
  `storage_retention_days` horizon. Deleting a `message_context` row MUST delete its vector.
- Adds an external API dependency for embeddings (Gemini). It MUST remain optional and advisory —
  the bot fully functions without it (Constitution IV).
- SQLite only; vectors are stored as float32 blobs. No native extensions, no window functions.
- Embedding spend is bounded by batching + the per-sweep cap. Query latency adds one embedding
  round-trip, overlapped with the "Checking…" placeholder send (006 pattern).
- Memory: the in-memory index is `N_messages × dimensions × 4 bytes`. Dimensionality is
  configurable (default 768; can truncate lower) to bound footprint.

## Success Criteria

- A fact-check whose relevant prior message is a **paraphrase/synonym** of the trigger (no shared
  keywords) now retrieves that message via the semantic tier.
- Reacting to a **low-text trigger** (image, bare link, "is this true?") retrieves relevant
  history via the enriched query embedding, where bm25 previously returned little or nothing.
- Fusion never *loses* a strong keyword hit: an exact name/URL/ID that bm25 ranks first still
  appears in the fused relevance list.
- With `semantic.enabled: false`, behavior and latency match the current (005/006) bot exactly.
- Backfill of existing history completes over time under the per-sweep cap without blocking
  capture or fact-checks, and `/factcheck` status shows pending → embedded progress.

## Open Questions

- **Default `enabled`**: proposed **true** (consistent with `factcheck.context.enabled: true`),
  accepting steady-state embedding cost. An admin who is cost-cautious sets it false. (Resolved in
  favor of `true` unless the user prefers opt-in.)
- **Default dimensionality**: proposed **768** (balance of recall vs. memory/storage); Gemini
  embeddings are MRL-truncatable, so this can change without a schema change (re-embed).
- **Fusion weighting**: v1 uses plain reciprocal-rank fusion (equal weight, `k=60`). A tunable
  keyword/semantic weight is a future option, not v1.
