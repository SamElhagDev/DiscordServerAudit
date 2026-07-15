# Phase 0 — Research: Semantic Context Retrieval

All NEEDS CLARIFICATION from the plan's Technical Context are resolved below.

## A. Embedding model & API surface

**Decision**: Use the existing `google-genai` SDK's embedding endpoint,
`client.aio.models.embed_content(model=..., contents=[...], config=genai_types.EmbedContentConfig(...))`,
with `task_type="RETRIEVAL_DOCUMENT"` for stored messages and `task_type="RETRIEVAL_QUERY"` for
the query. Default model `gemini-embedding-001`; `output_dimensionality` configurable
(default 768). L2-normalize every vector on store and on query so cosine similarity reduces to a
dot product. Reuse the shared client from `utils/gemini.py:get_client()` (returns `None` when no
key → semantic disabled).

**Rationale**: The SDK and a shared, key-gated client already exist (`utils/gemini.py`), so no new
dependency or auth path. `embed_content` accepts a **list** of contents, giving batch embedding in
one call (cost/rate-limit efficient). Task-type conditioning materially improves retrieval quality
(asymmetric query/document embeddings). Gemini embeddings support Matryoshka (MRL) truncation, so
`output_dimensionality` is a config knob, not a schema change — but truncated vectors MUST be
re-normalized (which our normalize-on-store step already does).

**Alternatives considered**:
- *A local sentence-transformer model* — avoids API cost but adds a heavyweight dependency
  (torch) and CPU load on the Scheduled Task host; rejected for deployment weight.
- *Keyword-only improvements (better FTS queries)* — cheaper but does not close the lexical-gap
  recall miss that motivated the feature (see spec Symptom).

**To verify at implementation**: exact attribute path for returned vectors
(`response.embeddings[i].values`) and the precise `task_type` enum spelling in the installed SDK
version. Wrap in the `utils/embeddings.py` helper so the rest of the code is SDK-shape-agnostic.

## B. Vector storage

**Decision**: New sidecar table `message_embeddings(message_context_id PRIMARY KEY, model TEXT,
dim INTEGER, vector BLOB)`, one row per embedded `message_context` row, `vector` = little-endian
float32 bytes (`numpy.ndarray.astype('<f4').tobytes()` / `numpy.frombuffer`). A SQLite trigger on
`message_context` deletion removes the matching embedding row (mirrors the 005
`message_context_fts` sync-trigger pattern) so pruning never orphans vectors.

**Rationale**: A **sidecar** (not a column on `message_context`) keeps capture a plain, fast
insert (006 optimized this path), lets embedding happen asynchronously and optionally, and lets
the model/dim change without rewriting `message_context`. `PRIMARY KEY = message_context_id`
enforces one-vector-per-row and makes upserts idempotent (`INSERT OR REPLACE`). Storing `model`
and `dim` per row lets the loader ignore/skip vectors that don't match the active config (FR-9),
enabling safe re-embedding after a model change.

**Alternatives considered**:
- *Nullable `embedding` column on `message_context`* — simpler co-location but touches the hot
  write path and complicates model migration; rejected.
- *`sqlite-vec` / native ANN extension* — faster at large scale but adds a native binary to the
  Windows Scheduled Task deployment; unnecessary at single-server volume; rejected by non-goals.

## C. In-memory index & similarity search

**Decision**: Maintain an in-process index of `(ids: list[int], matrix: np.ndarray[float32])` for
the active `(model, dim)`. Load once at `cog_load` from `message_embeddings`; append rows as the
reconciler embeds; drop rows when pruning deletes them (or lazily reconcile on a cadence). Top-K
search = `matrix @ query_vec` (normalized → cosine), `argpartition` for top-K, filtered by
`min_similarity` and optionally a `lookback_days` id/time bound.

**Rationale**: At single-server scale (tens of thousands of messages) a dense matmul is
sub-millisecond and trivially correct — no ANN structure to tune or persist. Footprint is
`N × dim × 4 bytes` (e.g. 50k × 768 × 4 ≈ 150 MB; drop to `dim=256` ≈ 50 MB if needed), bounded
and configurable. Keeping the index in memory avoids a per-query blob read of the whole store.

**Alternatives considered**:
- *Query-time SQLite blob load + cosine* — no memory footprint but re-reads all vectors each
  fact-check; acceptable fallback if the index is disabled, but slower; kept only as a safety path.
- *ANN (hnswlib/FAISS)* — overkill and adds a dependency/native build; rejected.

## D. Query construction

**Decision**: Build the query text from the reacted-to message content **plus** its reply-target
content (when it is a reply) **plus** a bounded slice of the most recent same-channel context
(config `semantic.query_context_messages`, small). Embed that combined text as one
`RETRIEVAL_QUERY`.

**Rationale**: The reacted message alone is often a poor query (a reference like "is that true?"
or an image caption-less trigger). Enriching with the reply target and immediate context gives the
embedding real semantic signal and directly addresses the low-text-trigger miss. This mirrors how
`_fetch_reply_context` and the recency tier already gather nearby text.

## E. Fusion of keyword + semantic

**Decision**: Reciprocal-rank fusion (RRF). For each result list (bm25, semantic), a document at
rank `r` (1-based) contributes `1 / (k + r)` to its score; sum across lists; sort desc; take the
top `archive_max_messages` after deduping against recency + trigger. `k` configurable, default 60.

**Rationale**: RRF is rank-based, so it needs no score normalization between bm25 (negative
`bm25()` magnitudes) and cosine (0–1) — the two scales are incomparable, which makes score-level
fusion fragile. RRF is simple, robust, and the standard hybrid-retrieval default. It guarantees a
strong hit in either list surfaces, so keyword's exact-match strength is preserved (success
criterion) while semantic adds paraphrase/reference recall.

**Alternatives considered**:
- *Weighted score fusion* — needs per-signal normalization/tuning; deferred (spec Open Questions).
- *Interleaving* — loses the "agreed by both" boost RRF gives; weaker.

## F. Embedding pipeline (write path)

**Decision**: A background **reconciler** task (started/stopped in `cog_load`/`cog_unload`) that,
on an interval, selects up to `embed_batch_size` `message_context` rows with no matching
`message_embeddings` row (bounded per sweep by `max_pending_per_sweep`), embeds them in one
batch call, and upserts vectors. Capture is unchanged (006 buffer). Existing history is simply the
initial backlog of pending rows, drained over successive sweeps under the cap.

**Rationale**: Decoupling embedding from capture keeps the hot path fast (Constitution V / 006),
makes embedding retriable and idempotent (a failed batch leaves rows pending), and unifies
steady-state and backfill so there is no separate one-shot migration to babysit. Rate limiting is
inherent (bounded batch per interval).

**Alternatives considered**:
- *Embed inline in the capture buffer flush* — couples the write buffer to an external API and
  blocks flushes on network latency; rejected.
- *One-shot backfill command + inline steady-state* — duplicates logic and needs a second code
  path for new rows; rejected in favor of the single reconciler.

## G. Graceful degradation & parity

**Decision**: Every semantic step is guarded. No key (`get_client()` → None), `semantic.enabled:
false`, an empty/mismatched index, or any embedding/search exception → the relevance tier uses
bm25 only and the bot behaves exactly as 005/006. Semantic never raises into the fact-check path;
failures log and fall through.

**Rationale**: Constitution IV (Gemini optional/advisory) and the spec's backward-compatibility
requirement. The feature is strictly additive recall; it must never make a fact-check worse or
fail.
