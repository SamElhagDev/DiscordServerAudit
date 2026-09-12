"""Fact-check message context store and its embedding sidecar.

Split out of the former single-file database.py; import via ``database.<name>``.
"""
import sqlite3
import logging
from .connection import get_conn, fts5_available
from .common import _now, _cutoff_datetime

logger = logging.getLogger(__name__)


def log_context_message(guild_id: int, channel_id: int, message_id: int,
                        user_id: int, author_name: str, content: str,
                        max_stored_chars: int = 2000, recorded_at: str | None = None):
    """Store message text for fact-check context (idempotent on message_id)."""
    if content and len(content) > max_stored_chars:
        content = content[:max_stored_chars]
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO message_context "
            "(guild_id, channel_id, message_id, user_id, author_name, content, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, message_id, user_id, author_name, content, recorded_at or _now()),
        )


def bulk_log_context_messages(rows: list, max_stored_chars: int = 2000):
    """Batch-store context messages in one transaction (for backfill).

    Each row: (guild_id, channel_id, message_id, user_id, author_name, content, recorded_at).
    """
    if not rows:
        return
    prepared = []
    for g, c, m, u, name, content, recorded_at in rows:
        if content and len(content) > max_stored_chars:
            content = content[:max_stored_chars]
        prepared.append((g, c, m, u, name, content, recorded_at or _now()))
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO message_context "
            "(guild_id, channel_id, message_id, user_id, author_name, content, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            prepared,
        )


def count_message_context(guild_id: int) -> int:
    """Number of stored context rows for a guild."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM message_context WHERE guild_id = ?", (guild_id,)
        ).fetchone()
    return row["c"] if row else 0


def prune_message_context(retention_days: int, max_per_channel: int = 0) -> int:
    """Prune message_context (0 = skip). Returns rows deleted; FTS stays in sync via triggers."""
    deleted = 0
    with get_conn() as conn:
        if retention_days and retention_days > 0:
            cutoff = _cutoff_datetime(retention_days)
            deleted += conn.execute(
                "DELETE FROM message_context WHERE recorded_at < ?", (cutoff,)
            ).rowcount
        if max_per_channel and max_per_channel > 0:
            # Keep the newest N per channel. ROW_NUMBER() ranks each channel's rows newest-first
            # in one pass; the previous correlated-count form re-scanned the channel per row,
            # which is quadratic on a large store. (Window functions: SQLite 3.25+, 2018.)
            deleted += conn.execute(
                "DELETE FROM message_context WHERE id IN ("
                "  SELECT id FROM ("
                "    SELECT id, ROW_NUMBER() OVER ("
                "      PARTITION BY guild_id, channel_id"
                "      ORDER BY recorded_at DESC, id DESC"
                "    ) AS rn"
                "    FROM message_context"
                "  ) WHERE rn > ?"
                ")",
                (max_per_channel,),
            ).rowcount
    if deleted:
        logger.info("Pruned %d message_context rows (retention_days=%d, max_per_channel=%d)",
                    deleted, retention_days, max_per_channel)
    return deleted


def get_recent_context(guild_id: int, channel_id: int, same_channel_limit: int,
                       total_limit: int, since_iso: str) -> list:
    """Recency tier: recent rows since *since_iso* (same-channel first, then server-wide)."""
    with get_conn() as conn:
        same = conn.execute(
            "SELECT * FROM message_context "
            "WHERE guild_id = ? AND channel_id = ? AND recorded_at >= ? "
            "ORDER BY recorded_at DESC, id DESC LIMIT ?",
            (guild_id, channel_id, since_iso, same_channel_limit),
        ).fetchall()
        wide = conn.execute(
            "SELECT * FROM message_context "
            "WHERE guild_id = ? AND recorded_at >= ? "
            "ORDER BY recorded_at DESC, id DESC LIMIT ?",
            (guild_id, since_iso, total_limit),
        ).fetchall()
    return list(same) + list(wide)


def get_relevant_history(guild_id: int, match_query: str, limit: int,
                         exclude_ids: set | None = None,
                         since_iso: str | None = None, min_score: float = 0.0) -> list:
    """Relevance tier: FTS5 MATCH over history, ranked by bm25. [] if FTS5/query unavailable."""
    if not match_query or not fts5_available():
        return []
    # Over-fetch so exclude_ids/min_score filtering can still fill `limit`.
    fetch = max(limit * 3, limit + (len(exclude_ids) if exclude_ids else 0))
    sql = (
        "SELECT mc.*, bm25(message_context_fts) AS score "
        "FROM message_context_fts "
        "JOIN message_context mc ON mc.id = message_context_fts.rowid "
        "WHERE message_context_fts MATCH ? AND mc.guild_id = ?"
    )
    params: list = [match_query, guild_id]
    if since_iso:
        sql += " AND mc.recorded_at >= ?"
        params.append(since_iso)
    sql += " ORDER BY score LIMIT ?"
    params.append(fetch)
    try:
        with get_conn() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
    except sqlite3.OperationalError:
        logger.debug("FTS5 MATCH query rejected: %r", match_query, exc_info=True)
        return []
    exclude_ids = exclude_ids or set()
    out = []
    for r in rows:
        if r["message_id"] in exclude_ids:
            continue
        # bm25() is negative; more-negative = more relevant.
        if min_score and (-r["score"]) < min_score:
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out


def get_two_tier_context(guild_id: int, channel_id: int, same_channel_limit: int,
                         total_limit: int, since_iso: str, trigger_id: int,
                         match_query: str | None, arch_max: int,
                         rel_since: str | None, min_score: float):
    """Recency + relevance fetch in one thread hop (vs. two separate calls).

    Runs recency, derives the seen-id set (trigger + deduped/capped recency ids),
    then runs relevance excluding those ids. Returns (recency_rows,
    relevance_rows); recency_rows are deduped and capped to *total_limit* in fetch
    order. Matches the original two-call path exactly.
    """
    rows = get_recent_context(guild_id, channel_id, same_channel_limit, total_limit, since_iso)
    seen = {trigger_id}
    recency_rows = []
    for r in rows:
        mid = r["message_id"]
        if mid in seen:
            continue
        seen.add(mid)
        recency_rows.append(r)
        if len(recency_rows) >= total_limit:
            break
    relevance_rows = []
    if match_query:
        relevance_rows = get_relevant_history(
            guild_id, match_query, arch_max, seen, rel_since, min_score,
        )
    return recency_rows, relevance_rows


def get_pending_context_rows(limit: int, model: str, dim: int) -> list:
    """message_context rows lacking a vector for *(model, dim)* (oldest first), capped at *limit*.

    Scoped to the active model/dim on purpose: ``load_all_embeddings`` only loads vectors
    matching them, so a row embedded under a superseded model is unusable and must count as
    pending again. Without that, changing ``semantic.model`` or ``semantic.dimensions`` would
    leave the index permanently empty with nothing queued to refill it.
    """
    if not limit or limit <= 0:
        return []
    with get_conn() as conn:
        return list(conn.execute(
            "SELECT mc.id, mc.content "
            "FROM message_context mc "
            "LEFT JOIN message_embeddings me "
            "  ON me.message_context_id = mc.id AND me.model = ? AND me.dim = ? "
            "WHERE me.message_context_id IS NULL AND TRIM(mc.content) <> '' "
            "ORDER BY mc.id ASC LIMIT ?",
            (model, dim, limit),
        ).fetchall())


def upsert_embeddings(rows: list) -> int:
    """Insert/replace vectors. Each row: (message_context_id, model, dim, vector_blob).

    created_at is stamped here; idempotent via INSERT OR REPLACE on the primary key.
    """
    if not rows:
        return 0
    now = _now()
    prepared = [(mcid, model, dim, blob, now) for (mcid, model, dim, blob) in rows]
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO message_embeddings "
            "(message_context_id, model, dim, vector, created_at) VALUES (?, ?, ?, ?, ?)",
            prepared,
        )
    return len(prepared)


def load_all_embeddings(model: str, dim: int) -> list:
    """(message_context_id, vector_blob) rows for the active (model, dim) only."""
    with get_conn() as conn:
        return list(conn.execute(
            "SELECT message_context_id, vector FROM message_embeddings "
            "WHERE model = ? AND dim = ?",
            (model, dim),
        ).fetchall())


def get_context_messages_by_ids(ids: list, guild_id: int) -> list:
    """message_context rows for the given row ids, scoped to *guild_id* (order not guaranteed).

    *guild_id* is required, not optional: the vector index is global across guilds, so an id
    coming back from a similarity search carries no guild of its own. Filtering here is what
    keeps the semantic tier to the same guild boundary the bm25 tier already enforces.
    """
    ids = [int(i) for i in ids]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with get_conn() as conn:
        return list(conn.execute(
            f"SELECT * FROM message_context WHERE guild_id = ? AND id IN ({placeholders})",
            [guild_id, *ids],
        ).fetchall())


def count_embeddings(guild_id: int | None, model: str, dim: int) -> int:
    """Number of usable stored vectors for *(model, dim)*, optionally scoped to a guild.

    Model/dim scoped to match ``get_pending_context_rows``, so /factcheck reports the vectors
    the index can actually load rather than every row ever embedded under any model.
    """
    with get_conn() as conn:
        if guild_id is None:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM message_embeddings WHERE model = ? AND dim = ?",
                (model, dim),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM message_embeddings me "
                "JOIN message_context mc ON mc.id = me.message_context_id "
                "WHERE mc.guild_id = ? AND me.model = ? AND me.dim = ?",
                (guild_id, model, dim),
            ).fetchone()
    return row["c"] if row else 0
