# Phase 1 — Data Model: Semantic Context Retrieval

## New table: `message_embeddings`

Sidecar vector store, one row per embedded `message_context` row.

```sql
CREATE TABLE IF NOT EXISTS message_embeddings (
    message_context_id INTEGER PRIMARY KEY,   -- = message_context.id (1:1)
    model              TEXT    NOT NULL,       -- embedding model id, e.g. 'gemini-embedding-001'
    dim                INTEGER NOT NULL,       -- vector length, e.g. 768
    vector             BLOB    NOT NULL,       -- little-endian float32, L2-normalized
    created_at         TEXT    NOT NULL        -- ISO8601 UTC
);

-- Keep vectors in sync when context rows are pruned (mirrors message_context_fts triggers).
CREATE TRIGGER IF NOT EXISTS message_context_ad_emb
AFTER DELETE ON message_context BEGIN
    DELETE FROM message_embeddings WHERE message_context_id = old.id;
END;
```

**Notes**
- No FK constraint declared (consistent with the codebase not relying on `PRAGMA foreign_keys`);
  the delete trigger enforces referential cleanup instead.
- Created in the same `CREATE TABLE` batch as the other schema in `database.py`, guarded like
  `_init_message_context_fts()` (a small `_init_message_embeddings()` invoked from `init_db`).
  Because `message_context.id` already exists, adding this table is additive; existing rows become
  "pending" (no embedding row) and are picked up by the reconciler.

## Vector (de)serialization

- Store: `vec = normalize(np.asarray(values, dtype='<f4')); blob = vec.tobytes()`.
- Load:  `vec = np.frombuffer(blob, dtype='<f4')` (already normalized).
- L2-normalize: `v / (np.linalg.norm(v) or 1.0)`. Cosine(a,b) = `a @ b` for normalized a, b.

## New `database.py` functions

| Function | Purpose |
|----------|---------|
| `get_pending_context_rows(limit: int) -> list` | Rows in `message_context` with **no** `message_embeddings` row (`LEFT JOIN … WHERE me.message_context_id IS NULL`), oldest first, capped at `limit`. Returns id + content (+ guild for logging). |
| `upsert_embeddings(rows: list) -> int` | `INSERT OR REPLACE INTO message_embeddings(...)` for `(message_context_id, model, dim, vector_blob, created_at)` tuples, one transaction (`executemany`). Idempotent. Returns count written. |
| `load_all_embeddings(model: str, dim: int) -> tuple[list[int], bytes|list]` | All `(message_context_id, vector)` for the active `(model, dim)` only — mismatched rows are skipped (FR-9). Used to build the in-memory index at startup. |
| `count_embeddings(guild_id: int|None = None) -> int` | Embedded-vector count (for `/factcheck` status: embedded vs. `count_message_context` = pending remainder). |

Existing `count_message_context(guild_id)` gives the denominator; `pending = total − embedded`.

## New `utils/embeddings.py`

Thin, SDK-shape-agnostic wrapper over `utils/gemini.get_client()`.

| Function | Contract |
|----------|----------|
| `embed_available() -> bool` | `get_client() is not None` and `semantic.enabled` is true. |
| `async embed_documents(texts: list[str]) -> list[np.ndarray] | None` | Batch embed with `task_type=RETRIEVAL_DOCUMENT`, `output_dimensionality=dim`; L2-normalized float32. `None` on no-key/failure (logged). Order matches input. |
| `async embed_query(text: str) -> np.ndarray | None` | Single embed with `task_type=RETRIEVAL_QUERY`; L2-normalized float32. `None` on no-key/failure. |

Model and `dim` come from config; empty/blank inputs are filtered before the call.

## New `utils/vector_index.py`

In-memory cosine index + fusion, decoupled from Discord/DB specifics.

| Member | Contract |
|--------|----------|
| `VectorIndex(dim: int)` | Holds `ids: list[int]` and `matrix: np.ndarray[(N, dim), float32]`. |
| `.load(pairs)` | Replace contents from `(id, vector)` pairs (startup). |
| `.add(pairs)` | Append newly embedded `(id, vector)` pairs (reconciler). |
| `.remove(ids)` | Drop ids (prune sync); may be a lazy rebuild on cadence. |
| `.search(query_vec, k, min_sim, allowed_ids=None) -> list[tuple[int, float]]` | Top-K by `matrix @ query_vec`, filtered by `min_sim` and optional `allowed_ids` (lookback bound). Empty list if index empty. |
| `rrf_fuse(keyword_ids, semantic_ids, k=60) -> list[int]` | Reciprocal-rank fusion of two ranked id lists → fused id order. Module-level helper. |

## Config additions (`config.yaml`)

Under the existing `factcheck.context.history_relevance`, add a sibling `semantic` block:

```yaml
factcheck:
  context:
    # ... existing recency + history_relevance keys ...
    # semantic tier (embedding-based retrieval, fused with FTS/bm25)
    semantic:
      enabled: true               # Master toggle for semantic retrieval
      model: gemini-embedding-001 # Embedding model id
      dimensions: 768             # Output dimensionality (MRL-truncatable; re-embed to change)
      max_messages: 10            # Cap on semantic candidates before fusion
      lookback_days: 0            # Query-only reach for semantic tier. 0 = all embedded history
      min_similarity: 0.0         # Cosine floor. 0 = no floor
      query_context_messages: 5   # Recent msgs folded into the query embedding
      fusion_k: 60                # Reciprocal-rank-fusion constant
      # reconciler (background embedding of pending rows)
      embed_batch_size: 100       # Rows embedded per Gemini batch call
      embed_interval_seconds: 30  # Sweep cadence
      max_pending_per_sweep: 500  # Upper bound on rows processed per sweep (backfill throttle)
```

All keys documented with safe defaults (FR-7). `enabled: false` (or no key) → no embedding calls,
no vectors, fall back to recency + bm25 (FR-6/FR-8). `archive_max_messages` (existing) is the final
relevance-slot cap after fusion; `semantic.max_messages` bounds the semantic list pre-fusion.

## Relationships & lifecycle

- `message_context (1) —— (0..1) message_embeddings` via `message_context_id`. Delete cascades via
  trigger.
- A row's lifecycle: **captured** (message_context insert, 006 buffer) → **pending** (no embedding)
  → **embedded** (reconciler writes vector + index.add) → **pruned** (context delete → trigger
  deletes vector → index.remove).
- Model/dim change: rows under the old `(model, dim)` are skipped by `load_all_embeddings` and are
  effectively pending for the new config; the reconciler re-embeds them (`INSERT OR REPLACE`).
