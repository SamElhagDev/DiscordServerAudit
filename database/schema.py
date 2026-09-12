"""Table/index/trigger creation and one-time data migrations.

Split out of the former single-file database.py; import via ``database.<name>``.
"""
import sqlite3
import logging
from .connection import get_conn, fts5_available
from . import connection

logger = logging.getLogger(__name__)


_SCHEMA_VERSION = 1


def _init_message_context_fts():
    """FTS5 index + sync triggers for message_context (no-op if FTS5 missing)."""
    if not fts5_available():
        return
    with get_conn() as conn:
        existed = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='message_context_fts'"
        ).fetchone() is not None
        conn.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS message_context_fts USING fts5(
                content,
                content='message_context',
                content_rowid='id',
                tokenize='porter unicode61'
            );

            CREATE TRIGGER IF NOT EXISTS message_context_ai
            AFTER INSERT ON message_context BEGIN
                INSERT INTO message_context_fts(rowid, content) VALUES (new.id, new.content);
            END;

            CREATE TRIGGER IF NOT EXISTS message_context_ad
            AFTER DELETE ON message_context BEGIN
                INSERT INTO message_context_fts(message_context_fts, rowid, content)
                    VALUES('delete', old.id, old.content);
            END;
        """)
        # First creation with pre-existing rows (e.g. FTS enabled later): backfill the index.
        if not existed:
            base = conn.execute("SELECT COUNT(*) FROM message_context").fetchone()[0]
            if base:
                conn.execute("INSERT INTO message_context_fts(message_context_fts) VALUES('rebuild')")
                logger.info("Built message_context FTS index from %d existing rows", base)
    logger.debug("message_context FTS5 index + triggers ready")


def _init_message_embeddings():
    """Sidecar vector store for message_context + delete-sync trigger (feature 007)."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS message_embeddings (
                message_context_id INTEGER PRIMARY KEY,
                model TEXT NOT NULL,
                dim INTEGER NOT NULL,
                vector BLOB NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TRIGGER IF NOT EXISTS message_context_ad_emb
            AFTER DELETE ON message_context BEGIN
                DELETE FROM message_embeddings WHERE message_context_id = old.id;
            END;
        """)
    logger.debug("message_embeddings table + delete trigger ready")


def init_db():
    logger.info("Initialising database at: %s", connection.DB_PATH)
    try:
        with get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS audit_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    audit_type TEXT NOT NULL,
                    run_at TEXT NOT NULL,
                    guild_id INTEGER NOT NULL,
                    finding_count INTEGER DEFAULT 0,
                    triggered_by TEXT DEFAULT 'scheduler'
                );

                CREATE TABLE IF NOT EXISTS audit_findings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES audit_runs(id),
                    severity TEXT NOT NULL,
                    category TEXT NOT NULL,
                    description TEXT NOT NULL,
                    resolved INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS bulk_task_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT NOT NULL,
                    performed_by TEXT NOT NULL,
                    guild_id INTEGER NOT NULL,
                    details TEXT,
                    performed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scheduler_state (
                    key TEXT PRIMARY KEY,
                    last_run TEXT NOT NULL
                );

                -- Stats: periodic guild membership snapshots
                CREATE TABLE IF NOT EXISTS member_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    total_members INTEGER NOT NULL,
                    online_members INTEGER DEFAULT 0,
                    bot_count INTEGER DEFAULT 0,
                    boost_count INTEGER DEFAULT 0,
                    boost_tier INTEGER DEFAULT 0
                );

                -- Stats: individual message metadata (raw, bounded retention)
                CREATE TABLE IF NOT EXISTS message_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    word_count INTEGER DEFAULT 0
                );

                -- Stats: voice channel sessions (raw, bounded retention)
                CREATE TABLE IF NOT EXISTS voice_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    joined_at TEXT NOT NULL,
                    left_at TEXT DEFAULT NULL,
                    duration_seconds INTEGER DEFAULT NULL
                );

                -- Stats: member join/leave/ban/unban events
                CREATE TABLE IF NOT EXISTS member_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );

                -- Stats: daily per-user aggregated activity
                CREATE TABLE IF NOT EXISTS user_activity_daily (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    message_count INTEGER DEFAULT 0,
                    voice_minutes INTEGER DEFAULT 0,
                    reactions_given INTEGER DEFAULT 0,
                    reactions_received INTEGER DEFAULT 0,
                    UNIQUE(guild_id, user_id, date)
                );

                -- Stats: daily per-channel aggregated activity
                CREATE TABLE IF NOT EXISTS channel_activity_daily (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    message_count INTEGER DEFAULT 0,
                    unique_users INTEGER DEFAULT 0,
                    UNIQUE(guild_id, channel_id, date)
                );

                -- Fact-check: message text store for context/relevance retrieval
                CREATE TABLE IF NOT EXISTS message_context (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    author_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(message_id)
                );

                -- Indexes for stats tables
                CREATE INDEX IF NOT EXISTS idx_member_snapshots_guild_time
                    ON member_snapshots(guild_id, recorded_at);

                CREATE INDEX IF NOT EXISTS idx_message_context_channel
                    ON message_context(guild_id, channel_id, recorded_at);
                CREATE INDEX IF NOT EXISTS idx_message_context_guild_time
                    ON message_context(guild_id, recorded_at);

                CREATE INDEX IF NOT EXISTS idx_message_events_guild_time
                    ON message_events(guild_id, recorded_at);
                CREATE INDEX IF NOT EXISTS idx_message_events_user
                    ON message_events(guild_id, user_id, recorded_at);
                CREATE INDEX IF NOT EXISTS idx_message_events_channel
                    ON message_events(guild_id, channel_id, recorded_at);

                CREATE INDEX IF NOT EXISTS idx_voice_sessions_guild_time
                    ON voice_sessions(guild_id, joined_at);
                CREATE INDEX IF NOT EXISTS idx_voice_sessions_user
                    ON voice_sessions(guild_id, user_id, joined_at);
                CREATE INDEX IF NOT EXISTS idx_voice_sessions_open
                    ON voice_sessions(guild_id, left_at);

                CREATE INDEX IF NOT EXISTS idx_member_events_guild_time
                    ON member_events(guild_id, recorded_at);
                CREATE INDEX IF NOT EXISTS idx_member_events_type
                    ON member_events(guild_id, event_type, recorded_at);

                CREATE INDEX IF NOT EXISTS idx_user_activity_guild_date
                    ON user_activity_daily(guild_id, date);
                CREATE INDEX IF NOT EXISTS idx_user_activity_user_date
                    ON user_activity_daily(guild_id, user_id, date);

                CREATE INDEX IF NOT EXISTS idx_channel_activity_guild_date
                    ON channel_activity_daily(guild_id, date);
                CREATE INDEX IF NOT EXISTS idx_channel_activity_channel_date
                    ON channel_activity_daily(guild_id, channel_id, date);
            """)
        logger.info("Database schema ready")
        _init_message_context_fts()
        _init_message_embeddings()
        _run_migrations()
        logger.info("Database initialised successfully")
    except Exception:
        logger.critical("Failed to initialise database at %s", connection.DB_PATH, exc_info=True)
        raise


def _migrate_add_total_words():
    """Add total_words column to rollup tables and backfill from raw events."""
    with get_conn() as conn:
        for table in ("user_activity_daily", "channel_activity_daily"):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN total_words INTEGER DEFAULT 0")
                logger.info("Added total_words column to %s", table)
            except sqlite3.OperationalError:
                pass  # Column already exists

        fixed = conn.execute("""
            UPDATE user_activity_daily SET total_words = COALESCE((
                SELECT SUM(me.word_count) FROM message_events me
                WHERE me.guild_id = user_activity_daily.guild_id
                AND me.user_id = user_activity_daily.user_id
                AND DATE(me.recorded_at) = user_activity_daily.date
            ), 0)
            WHERE total_words = 0 AND message_count > 0
        """).rowcount
        if fixed:
            logger.info("Backfilled total_words for %d user_activity_daily rows", fixed)

        fixed = conn.execute("""
            UPDATE channel_activity_daily SET total_words = COALESCE((
                SELECT SUM(me.word_count) FROM message_events me
                WHERE me.guild_id = channel_activity_daily.guild_id
                AND me.channel_id = channel_activity_daily.channel_id
                AND DATE(me.recorded_at) = channel_activity_daily.date
            ), 0)
            WHERE total_words = 0 AND message_count > 0
        """).rowcount
        if fixed:
            logger.info("Backfilled total_words for %d channel_activity_daily rows", fixed)


def _migrate_timestamps():
    """Strip +00:00 / Z suffixes so SQLite strftime() can parse all timestamps."""
    _TIMESTAMP_COLS = [
        ("message_events",   "recorded_at"),
        ("voice_sessions",   "joined_at"),
        ("voice_sessions",   "left_at"),
        ("member_events",    "recorded_at"),
        ("member_snapshots", "recorded_at"),
        ("audit_runs",       "run_at"),
        ("bulk_task_log",    "performed_at"),
        ("scheduler_state",  "last_run"),
    ]
    total_fixed = 0
    with get_conn() as conn:
        for table, col in _TIMESTAMP_COLS:
            cur = conn.execute(
                f"UPDATE {table} SET {col} = substr({col}, 1, 19) "
                f"WHERE {col} LIKE '%+%' OR {col} LIKE '%Z'"
            )
            if cur.rowcount:
                total_fixed += cur.rowcount
                logger.info("Migrated %d rows in %s.%s (stripped tz suffix)", cur.rowcount, table, col)
    if total_fixed:
        logger.info("Timestamp migration complete: %d rows updated", total_fixed)
    else:
        logger.debug("Timestamp migration: nothing to update")


def _run_migrations():
    """Run one-time data migrations, gated by PRAGMA user_version.

    Without this gate the migrations re-scan every (growing) table on every
    startup. They are idempotent, so an already-migrated database just bumps its
    version once and skips them on subsequent boots.
    """
    with get_conn() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= _SCHEMA_VERSION:
        logger.debug("Schema migrations up to date (user_version=%d) — skipping", version)
        return
    logger.info("Running schema migrations (user_version %d -> %d)", version, _SCHEMA_VERSION)
    _migrate_add_total_words()
    _migrate_timestamps()
    with get_conn() as conn:
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
    logger.info("Schema migrations complete (user_version=%d)", _SCHEMA_VERSION)
