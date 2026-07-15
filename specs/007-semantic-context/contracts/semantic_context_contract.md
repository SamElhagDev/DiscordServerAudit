# Contracts: Semantic Context Retrieval

Internal function/behavior contracts for feature 007. No Discord-facing command surface changes
except the extended `/factcheck` status output.

## 1. Embedding reconciler (background task)

**Where**: `cogs/fact_check.py`, started in `cog_load`, cancelled in `cog_unload` (alongside the
006 write buffer lifecycle).

```
loop every semantic.embed_interval_seconds while enabled + available:
    processed = 0
    while processed < semantic.max_pending_per_sweep:
        rows = await database.run(get_pending_context_rows, semantic.embed_batch_size)
        if not rows: break
        vecs = await embeddings.embed_documents([r.content for r in rows])
        if vecs is None: break            # no key / failure → try again next sweep
        await database.run(upsert_embeddings, zip(row_ids, model, dim, blobs, now))
        index.add(zip(row_ids, vecs))
        processed += len(rows)
    log INFO count + latency (or WARNING on failure)
```

**Guarantees**: idempotent (`INSERT OR REPLACE`), off the message hot path, bounded per sweep,
self-healing (failed/empty batch leaves rows pending), backfill = draining the initial pending
backlog. Never raises into the bot loop; all exceptions logged and swallowed.

## 2. Query builder — `_build_semantic_query(message) -> str | None`

- Returns combined text = reacted `message.content` + reply-target content (if a reply) + up to
  `semantic.query_context_messages` most recent same-channel context lines.
- Returns `None` if the combined text is empty (→ semantic tier skipped for this check).
- Pure/bounded; reuses existing reply-context and recency gathering.

## 3. Similarity search — `VectorIndex.search(query_vec, k, min_sim, allowed_ids) -> [(id, sim)]`

- `k = semantic.max_messages`; `min_sim = semantic.min_similarity`; `allowed_ids` = ids within
  `semantic.lookback_days` when set, else `None` (all).
- One matmul over the in-memory matrix; returns ≤ k `(message_context_id, cosine)` desc.
- Empty index or `query_vec is None` → `[]`.

## 4. Fusion — `rrf_fuse(keyword_ids, semantic_ids, k) -> [id]`

- Inputs are the bm25 result ids (existing `get_relevant_history` order) and the semantic search
  ids, both already excluding recency + trigger.
- Output is a fused id ordering; the cog maps ids back to rows and caps at
  `history_relevance.archive_max_messages` for the relevance slot.
- If one input is empty, output = the other (so semantic-off or bm25-empty both degrade cleanly).

## 5. Extended `_build_context_window(message) -> ContextWindow`

Unchanged for recency. Relevance slot now:

```
bm25_ids     = existing FTS path (unchanged; may be [])
sem_ids      = []
if semantic.enabled and embed_available():
    q = _build_semantic_query(message)
    qv = await embeddings.embed_query(q)          # overlapped with "Checking…" send
    sem_ids = index.search(qv, ...) ids           # excluding recency + trigger
fused = rrf_fuse(bm25_ids, sem_ids, semantic.fusion_k)[:archive_max_messages]
relevance = rows for fused ids, rendered exactly as today (source="relevance")
```

**Guarantees**: prompt/embed format unchanged (only *which* rows fill the relevance slot changes);
FR-3a preserved (verdict about the reacted message only; context not fact-checked); any semantic
error → `sem_ids = []` → behaves as today (recency + bm25).

## 6. `/factcheck` status — additional lines

When context is enabled, append semantic state:
- `Semantic retrieval: enabled | disabled | unavailable (no key)`
- `  model=<model> dim=<dimensions>`
- `  embedded=<count_embeddings> / total=<count_message_context> (pending=<total−embedded>)`

Surfaces backfill progress (FR-10). No new command; extends the existing admin-gated status.

## 7. Degradation matrix (must all be non-fatal)

| Condition | Behavior |
|-----------|----------|
| `semantic.enabled: false` | No reconciler, no query embed; relevance = bm25 only (today). |
| No Gemini key | `embed_available()` false; same as disabled; status shows "unavailable". |
| Embedding call fails (reconciler) | Rows stay pending; retried next sweep; logged WARNING. |
| Embedding call fails (query) | `sem_ids=[]`; relevance = bm25 only for that check; logged. |
| Empty / cold index | `search()` → `[]`; relevance = bm25 only. |
| Model/dim mismatch in store | Mismatched rows skipped on load; treated as pending; re-embedded. |
| `numpy` import/index error | Semantic disabled for the session; logged ERROR; bm25 + recency serve. |

## 8. Observability (Principle V)

- Reconciler: INFO per sweep (`embedded=N latency=..s pending≈M guild-agnostic`), WARNING on
  batch failure.
- Query path: DEBUG on semantic hit counts; WARNING on query-embed failure.
- Fusion: DEBUG with `bm25=n sem=m fused=k`.
- No bare excepts anywhere in the new paths.
