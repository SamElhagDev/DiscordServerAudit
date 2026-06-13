import asyncio
import logging
import sqlite3
import datetime
import os
import threading
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DiscordServerAudit_DB_PATH", "bot.db")

# Connections are thread-local: the event loop thread keeps its own persistent
# connection (unchanged behaviour), and any worker thread spawned by run() gets
# its own. WAL mode lets these connections read concurrently with a single
# writer; the 10s busy timeout (sqlite3.connect's `timeout`) makes a blocked
# writer wait instead of raising "database is locked".
_local = threading.local()

# Bump when adding a new one-time data migration in _run_migrations().
_SCHEMA_VERSION = 1


def _ensure_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
        logger.debug("Opened thread-local DB connection to %s (thread=%s)",
                     DB_PATH, threading.current_thread().name)
    return conn


def close_db():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
        logger.info("Database connection closed (thread=%s)", threading.current_thread().name)


async def run(fn, *args, **kwargs):
    """Execute a synchronous DB function in a worker thread so it doesn't block
    the asyncio event loop. Each worker thread uses its own thread-local
    connection, so concurrent calls are safe under WAL mode.

    Usage: await database.run(database.log_message_event, guild_id, ...)
    """
    return await asyncio.to_thread(fn, *args, **kwargs)


def init_db():
    logger.info("Initialising database at: %s", DB_PATH)
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

                -- Indexes for stats tables
                CREATE INDEX IF NOT EXISTS idx_member_snapshots_guild_time
                    ON member_snapshots(guild_id, recorded_at);

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
        _run_migrations()
        logger.info("Database initialised successfully")
    except Exception:
        logger.critical("Failed to initialise database at %s", DB_PATH, exc_info=True)
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


@contextmanager
def get_conn():
    conn = _ensure_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        logger.error("Database transaction rolled back", exc_info=True)
        raise


def log_bulk_task(task_type: str, performed_by: str, guild_id: int, details: str):
    logger.debug("Bulk task logged: type=%r guild=%s user=%s", task_type, guild_id, performed_by)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO bulk_task_log (task_type, performed_by, guild_id, details, performed_at) VALUES (?, ?, ?, ?, ?)",
            (task_type, performed_by, guild_id, details, _now()),
        )


def start_audit_run(audit_type: str, guild_id: int, triggered_by: str = "scheduler") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO audit_runs (audit_type, run_at, guild_id, triggered_by) VALUES (?, ?, ?, ?)",
            (audit_type, _now(), guild_id, triggered_by),
        )
        run_id = cur.lastrowid
    logger.debug("Audit run started: id=%d type=%r guild=%s triggered_by=%r", run_id, audit_type, guild_id, triggered_by)
    return run_id


def add_finding(run_id: int, severity: str, category: str, description: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_findings (run_id, severity, category, description) VALUES (?, ?, ?, ?)",
            (run_id, severity, category, description),
        )


def finalize_audit_run(run_id: int, finding_count: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE audit_runs SET finding_count = ? WHERE id = ?",
            (finding_count, run_id),
        )
    logger.debug("Audit run %d finalised: %d findings persisted", run_id, finding_count)


def get_last_run(key: str):
    with get_conn() as conn:
        row = conn.execute("SELECT last_run FROM scheduler_state WHERE key = ?", (key,)).fetchone()
        if row:
            ts = datetime.datetime.fromisoformat(row["last_run"])
            # Rows written before the tz-aware migration are naive — treat as UTC
            # so arithmetic against tz-aware "now" doesn't raise.
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.timezone.utc)
            return ts
        return None


def set_last_run(key: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO scheduler_state (key, last_run) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET last_run = excluded.last_run",
            (key, _now()),
        )


def get_recent_findings(guild_id: int, audit_type: str, limit: int = 20):
    with get_conn() as conn:
        return conn.execute("""
            SELECT f.severity, f.category, f.description, r.run_at
            FROM audit_findings f
            JOIN audit_runs r ON f.run_id = r.id
            WHERE r.guild_id = ? AND r.audit_type = ?
            ORDER BY r.run_at DESC
            LIMIT ?
        """, (guild_id, audit_type, limit)).fetchall()


def _now() -> str:
    # No +00:00 suffix — SQLite strftime() can't parse it on all versions.
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _cutoff_datetime(days: int) -> str:
    """Return an ISO datetime string for use with raw event tables (recorded_at >= ?)."""
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# Keep old name as alias so external callers (cogs) don't break.
_days_ago = _cutoff_datetime


# ---------------------------------------------------------------------------
# Stats: write helpers (called from event handlers)
# ---------------------------------------------------------------------------

def log_message_event(guild_id: int, channel_id: int, user_id: int, word_count: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO message_events (guild_id, channel_id, user_id, recorded_at, word_count) VALUES (?, ?, ?, ?, ?)",
            (guild_id, channel_id, user_id, _now(), word_count),
        )


def clear_recent_events(guild_id: int, cutoff_iso: str):
    """Delete message_events and member_events at/after cutoff. Used by /scan
    before re-ingesting history so a re-scan doesn't double-count."""
    with get_conn() as conn:
        conn.execute("DELETE FROM message_events WHERE guild_id = ? AND recorded_at >= ?", (guild_id, cutoff_iso))
        conn.execute("DELETE FROM member_events WHERE guild_id = ? AND recorded_at >= ?", (guild_id, cutoff_iso))


def bulk_log_reactions_received(guild_id: int, reaction_counts: dict):
    """Upsert reactions_received counts keyed by (user_id, date). Used by /scan."""
    with get_conn() as conn:
        for (user_id, date), count in reaction_counts.items():
            conn.execute(
                "INSERT INTO user_activity_daily (guild_id, user_id, date, reactions_received) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(guild_id, user_id, date) DO UPDATE SET reactions_received = excluded.reactions_received",
                (guild_id, user_id, date, count),
            )


def start_voice_session(guild_id: int, channel_id: int, user_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO voice_sessions (guild_id, channel_id, user_id, joined_at) VALUES (?, ?, ?, ?)",
            (guild_id, channel_id, user_id, _now()),
        )
    logger.debug("Voice session started: guild=%s user=%s channel=%s", guild_id, user_id, channel_id)


def end_voice_session(guild_id: int, user_id: int):
    now = _now()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, joined_at FROM voice_sessions WHERE guild_id = ? AND user_id = ? AND left_at IS NULL ORDER BY joined_at DESC LIMIT 1",
            (guild_id, user_id),
        ).fetchone()
        if row:
            joined = datetime.datetime.fromisoformat(row["joined_at"])
            left = datetime.datetime.fromisoformat(now)
            duration = int((left - joined).total_seconds())
            conn.execute(
                "UPDATE voice_sessions SET left_at = ?, duration_seconds = ? WHERE id = ?",
                (now, duration, row["id"]),
            )
            logger.debug("Voice session ended: guild=%s user=%s duration=%ds", guild_id, user_id, duration)


_MAX_ORPHAN_DURATION = 8 * 3600  # 8 hours — cap inflated durations from bot downtime


def close_orphaned_voice_sessions():
    now = _now()
    with get_conn() as conn:
        rows = conn.execute("SELECT id, joined_at FROM voice_sessions WHERE left_at IS NULL").fetchall()
        capped = 0
        for row in rows:
            joined = datetime.datetime.fromisoformat(row["joined_at"])
            left = datetime.datetime.fromisoformat(now)
            duration = int((left - joined).total_seconds())
            if duration > _MAX_ORPHAN_DURATION:
                capped += 1
                duration = _MAX_ORPHAN_DURATION
            conn.execute(
                "UPDATE voice_sessions SET left_at = ?, duration_seconds = ? WHERE id = ?",
                (now, duration, row["id"]),
            )
        if rows:
            logger.info("Closed %d orphaned voice sessions (%d capped at %ds)", len(rows), capped, _MAX_ORPHAN_DURATION)


def log_member_event(guild_id: int, user_id: int, event_type: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO member_events (guild_id, user_id, event_type, recorded_at) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, event_type, _now()),
        )
    logger.debug("Member event: guild=%s user=%s type=%s", guild_id, user_id, event_type)


def bulk_log_message_events(events: list[tuple]):
    logger.debug("Bulk inserting %d message events", len(events))
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO message_events (guild_id, channel_id, user_id, recorded_at, word_count) VALUES (?, ?, ?, ?, ?)",
            events,
        )


def bulk_log_member_events(events: list[tuple]):
    logger.debug("Bulk inserting %d member events", len(events))
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO member_events (guild_id, user_id, event_type, recorded_at) VALUES (?, ?, ?, ?)",
            events,
        )


def save_member_snapshot(guild_id: int, total: int, online: int, bots: int, boosts: int, tier: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO member_snapshots (guild_id, recorded_at, total_members, online_members, bot_count, boost_count, boost_tier) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, _now(), total, online, bots, boosts, tier),
        )
    logger.debug("Member snapshot: guild=%s total=%d online=%d bots=%d", guild_id, total, online, bots)


def increment_reaction(guild_id: int, reactor_id: int, author_id: int, date: str = None):
    d = date or _today()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO user_activity_daily (guild_id, user_id, date, reactions_given) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(guild_id, user_id, date) DO UPDATE SET reactions_given = reactions_given + 1",
            (guild_id, reactor_id, d),
        )
        if author_id and author_id != reactor_id:
            conn.execute(
                "INSERT INTO user_activity_daily (guild_id, user_id, date, reactions_received) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(guild_id, user_id, date) DO UPDATE SET reactions_received = reactions_received + 1",
                (guild_id, author_id, d),
            )


def decrement_reaction(guild_id: int, reactor_id: int, author_id: int, date: str = None):
    d = date or _today()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO user_activity_daily (guild_id, user_id, date, reactions_given) VALUES (?, ?, ?, 0) "
            "ON CONFLICT(guild_id, user_id, date) DO UPDATE SET reactions_given = MAX(0, reactions_given - 1)",
            (guild_id, reactor_id, d),
        )
        if author_id and author_id != reactor_id:
            conn.execute(
                "INSERT INTO user_activity_daily (guild_id, user_id, date, reactions_received) VALUES (?, ?, ?, 0) "
                "ON CONFLICT(guild_id, user_id, date) DO UPDATE SET reactions_received = MAX(0, reactions_received - 1)",
                (guild_id, author_id, d),
            )


# ---------------------------------------------------------------------------
# Stats: rollup & prune helpers (called from daily scheduler)
# ---------------------------------------------------------------------------

def rollup_user_activity(guild_id: int, date: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO user_activity_daily (guild_id, user_id, date, message_count, total_words, voice_minutes)
            SELECT me.guild_id, me.user_id, ?, COUNT(*), COALESCE(SUM(me.word_count), 0), 0
            FROM message_events me
            WHERE me.guild_id = ? AND DATE(me.recorded_at) = ?
            GROUP BY me.guild_id, me.user_id
            ON CONFLICT(guild_id, user_id, date) DO UPDATE
                SET message_count = excluded.message_count,
                    total_words = excluded.total_words
        """, (date, guild_id, date))

        conn.execute("""
            INSERT INTO user_activity_daily (guild_id, user_id, date, message_count, voice_minutes)
            SELECT vs.guild_id, vs.user_id, ?, 0, COALESCE(SUM(vs.duration_seconds) / 60, 0)
            FROM voice_sessions vs
            WHERE vs.guild_id = ? AND DATE(vs.joined_at) = ? AND vs.duration_seconds IS NOT NULL
            GROUP BY vs.guild_id, vs.user_id
            ON CONFLICT(guild_id, user_id, date) DO UPDATE
                SET voice_minutes = excluded.voice_minutes
        """, (date, guild_id, date))
    logger.debug("Rolled up user activity: guild=%s date=%s", guild_id, date)


def rollup_channel_activity(guild_id: int, date: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO channel_activity_daily (guild_id, channel_id, date, message_count, unique_users, total_words)
            SELECT guild_id, channel_id, ?, COUNT(*), COUNT(DISTINCT user_id), COALESCE(SUM(word_count), 0)
            FROM message_events
            WHERE guild_id = ? AND DATE(recorded_at) = ?
            GROUP BY guild_id, channel_id
            ON CONFLICT(guild_id, channel_id, date) DO UPDATE
                SET message_count = excluded.message_count,
                    unique_users = excluded.unique_users,
                    total_words = excluded.total_words
        """, (date, guild_id, date))
    logger.debug("Rolled up channel activity: guild=%s date=%s", guild_id, date)


def prune_old_events(days: int):
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        r1 = conn.execute("DELETE FROM message_events WHERE recorded_at < ?", (cutoff,))
        r2 = conn.execute("DELETE FROM voice_sessions WHERE joined_at < ? AND left_at IS NOT NULL", (cutoff,))
        logger.info("Pruned old events: %d messages, %d voice sessions (older than %d days)",
                     r1.rowcount, r2.rowcount, days)


# ---------------------------------------------------------------------------
# Stats: read helpers (called from stats commands)
# ---------------------------------------------------------------------------

def get_server_stats_summary(guild_id: int, days: int) -> dict:
    cutoff = _cutoff_datetime(days)
    cutoff_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        # Use the rolled-up table so total_messages uses the same source/cutoff as
        # get_top_channels / get_top_users — message_events is pruned independently
        # and uses a datetime cutoff, which creates an unavoidable mismatch.
        msgs = conn.execute(
            "SELECT COALESCE(SUM(message_count), 0) as c FROM user_activity_daily WHERE guild_id = ? AND date >= ?",
            (guild_id, cutoff_date),
        ).fetchone()["c"]
        voice = conn.execute(
            "SELECT COALESCE(SUM(duration_seconds), 0) as s FROM voice_sessions WHERE guild_id = ? AND joined_at >= ? AND duration_seconds IS NOT NULL",
            (guild_id, cutoff),
        ).fetchone()["s"]
        users = conn.execute(
            "SELECT COUNT(DISTINCT user_id) as c FROM user_activity_daily WHERE guild_id = ? AND date >= ?",
            (guild_id, cutoff_date),
        ).fetchone()["c"]
        channels = conn.execute(
            "SELECT COUNT(DISTINCT channel_id) as c FROM channel_activity_daily WHERE guild_id = ? AND date >= ?",
            (guild_id, cutoff_date),
        ).fetchone()["c"]
        reactions = conn.execute(
            "SELECT COALESCE(SUM(reactions_given), 0) as c FROM user_activity_daily WHERE guild_id = ? AND date >= ?",
            (guild_id, cutoff_date),
        ).fetchone()["c"]
    return {
        "messages": msgs,
        "voice_seconds": voice,
        "active_users": users,
        "active_channels": channels,
        "reactions": reactions,
    }


def get_top_users(guild_id: int, days: int, limit: int = 5):
    cutoff_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        return conn.execute(
            "SELECT user_id, SUM(message_count) as total FROM user_activity_daily "
            "WHERE guild_id = ? AND date >= ? GROUP BY user_id ORDER BY total DESC LIMIT ?",
            (guild_id, cutoff_date, limit),
        ).fetchall()


def get_top_channels(guild_id: int, days: int, limit: int = 5):
    cutoff_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        return conn.execute(
            "SELECT channel_id, SUM(message_count) as total FROM channel_activity_daily "
            "WHERE guild_id = ? AND date >= ? GROUP BY channel_id ORDER BY total DESC LIMIT ?",
            (guild_id, cutoff_date, limit),
        ).fetchall()


def get_peak_hours(guild_id: int, days: int):
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        return conn.execute(
            "SELECT CAST(substr(recorded_at, 12, 2) AS INTEGER) as hour, COUNT(*) as count "
            "FROM message_events WHERE guild_id = ? AND recorded_at >= ? "
            "GROUP BY hour ORDER BY count DESC",
            (guild_id, cutoff),
        ).fetchall()


def get_daily_activity(guild_id: int, days: int):
    cutoff_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        return conn.execute(
            "SELECT date, SUM(message_count) as messages, SUM(voice_minutes) as voice "
            "FROM user_activity_daily WHERE guild_id = ? AND date >= ? "
            "GROUP BY date ORDER BY date",
            (guild_id, cutoff_date),
        ).fetchall()


def get_user_stats(guild_id: int, user_id: int, days: int) -> dict:
    cutoff_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        totals = conn.execute(
            "SELECT COALESCE(SUM(message_count), 0) as msgs, COALESCE(SUM(voice_minutes), 0) as voice, "
            "COALESCE(SUM(reactions_given), 0) as rg, COALESCE(SUM(reactions_received), 0) as rr "
            "FROM user_activity_daily WHERE guild_id = ? AND user_id = ? AND date >= ?",
            (guild_id, user_id, cutoff_date),
        ).fetchone()

        # Completed sessions whose date hasn't been rolled up into user_activity_daily yet.
        unrolled = conn.execute(
            "SELECT COALESCE(SUM(duration_seconds), 0) / 60 as extra "
            "FROM voice_sessions "
            "WHERE guild_id = ? AND user_id = ? AND joined_at >= ? "
            "  AND duration_seconds IS NOT NULL "
            "  AND DATE(joined_at) NOT IN ("
            "    SELECT date FROM user_activity_daily WHERE guild_id = ? AND user_id = ?"
            "  )",
            (guild_id, user_id, _cutoff_datetime(days), guild_id, user_id),
        ).fetchone()

        # Currently-active sessions (left_at IS NULL means user is still in voice).
        # julianday arithmetic gives elapsed seconds since joining.
        active = conn.execute(
            "SELECT COALESCE(SUM(CAST((julianday('now') - julianday(joined_at)) * 86400 AS INTEGER)), 0) / 60 as mins "
            "FROM voice_sessions "
            "WHERE guild_id = ? AND user_id = ? AND left_at IS NULL",
            (guild_id, user_id),
        ).fetchone()

        total_voice = totals["voice"] + (unrolled["extra"] or 0) + (active["mins"] or 0)
        top_channel = conn.execute(
            "SELECT channel_id, COUNT(*) as c FROM message_events "
            "WHERE guild_id = ? AND user_id = ? AND recorded_at >= ? "
            "GROUP BY channel_id ORDER BY c DESC LIMIT 1",
            (guild_id, user_id, _cutoff_datetime(days)),
        ).fetchone()
        daily = conn.execute(
            "SELECT date, message_count, voice_minutes FROM user_activity_daily "
            "WHERE guild_id = ? AND user_id = ? AND date >= ? ORDER BY date",
            (guild_id, user_id, cutoff_date),
        ).fetchall()
        channel_breakdown = conn.execute(
            "SELECT channel_id, COUNT(*) as c FROM message_events "
            "WHERE guild_id = ? AND user_id = ? AND recorded_at >= ? "
            "GROUP BY channel_id ORDER BY c DESC LIMIT 6",
            (guild_id, user_id, _cutoff_datetime(days)),
        ).fetchall()
    return {
        "message_count": totals["msgs"],
        "voice_minutes": total_voice,
        "reactions_given": totals["rg"],
        "reactions_received": totals["rr"],
        "top_channel_id": top_channel["channel_id"] if top_channel else None,
        "daily": [dict(r) for r in daily],
        "channel_breakdown": [(r["channel_id"], r["c"]) for r in channel_breakdown],
    }


def get_channel_stats(guild_id: int, channel_id: int, days: int) -> dict:
    cutoff_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        totals = conn.execute(
            "SELECT COALESCE(SUM(message_count), 0) as msgs, COALESCE(SUM(unique_users), 0) as users "
            "FROM channel_activity_daily WHERE guild_id = ? AND channel_id = ? AND date >= ?",
            (guild_id, channel_id, cutoff_date),
        ).fetchone()
        unique = conn.execute(
            "SELECT COUNT(DISTINCT user_id) as c FROM message_events "
            "WHERE guild_id = ? AND channel_id = ? AND recorded_at >= ?",
            (guild_id, channel_id, cutoff),
        ).fetchone()["c"]
        top_users = conn.execute(
            "SELECT user_id, COUNT(*) as c FROM message_events "
            "WHERE guild_id = ? AND channel_id = ? AND recorded_at >= ? "
            "GROUP BY user_id ORDER BY c DESC LIMIT 6",
            (guild_id, channel_id, cutoff),
        ).fetchall()
        daily = conn.execute(
            "SELECT date, message_count, unique_users FROM channel_activity_daily "
            "WHERE guild_id = ? AND channel_id = ? AND date >= ? ORDER BY date",
            (guild_id, channel_id, cutoff_date),
        ).fetchall()
        peak_hours = conn.execute(
            "SELECT CAST(substr(recorded_at, 12, 2) AS INTEGER) as hour, COUNT(*) as count "
            "FROM message_events WHERE guild_id = ? AND channel_id = ? AND recorded_at >= ? "
            "GROUP BY hour ORDER BY count DESC LIMIT 1",
            (guild_id, channel_id, cutoff),
        ).fetchone()
    return {
        "message_count": totals["msgs"],
        "unique_users": unique,
        "top_users": [(r["user_id"], r["c"]) for r in top_users],
        "daily": [dict(r) for r in daily],
        "peak_hour": peak_hours["hour"] if peak_hours else None,
    }


def get_voice_leaderboard(guild_id: int, days: int, limit: int = 5):
    cutoff_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        return conn.execute(
            "SELECT user_id, SUM(voice_minutes) as total FROM user_activity_daily "
            "WHERE guild_id = ? AND date >= ? AND voice_minutes > 0 "
            "GROUP BY user_id ORDER BY total DESC LIMIT ?",
            (guild_id, cutoff_date, limit),
        ).fetchall()


def get_voice_channel_stats(guild_id: int, days: int, limit: int = 5):
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        return conn.execute(
            "SELECT channel_id, SUM(duration_seconds) as total FROM voice_sessions "
            "WHERE guild_id = ? AND joined_at >= ? AND duration_seconds IS NOT NULL "
            "GROUP BY channel_id ORDER BY total DESC LIMIT ?",
            (guild_id, cutoff, limit),
        ).fetchall()


def get_member_growth(guild_id: int, days: int):
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        return conn.execute(
            "SELECT recorded_at, total_members FROM member_snapshots "
            "WHERE guild_id = ? AND recorded_at >= ? ORDER BY recorded_at",
            (guild_id, cutoff),
        ).fetchall()


def get_member_events_summary(guild_id: int, days: int) -> dict:
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        joins = conn.execute(
            "SELECT COUNT(*) as c FROM member_events WHERE guild_id = ? AND event_type = 'join' AND recorded_at >= ?",
            (guild_id, cutoff),
        ).fetchone()["c"]
        leaves = conn.execute(
            "SELECT COUNT(*) as c FROM member_events WHERE guild_id = ? AND event_type = 'leave' AND recorded_at >= ?",
            (guild_id, cutoff),
        ).fetchone()["c"]
        daily = conn.execute(
            "SELECT DATE(recorded_at) as date, "
            "SUM(CASE WHEN event_type = 'join' THEN 1 ELSE 0 END) as joins, "
            "SUM(CASE WHEN event_type = 'leave' THEN 1 ELSE 0 END) as leaves "
            "FROM member_events WHERE guild_id = ? AND recorded_at >= ? "
            "GROUP BY date ORDER BY date DESC LIMIT 7",
            (guild_id, cutoff),
        ).fetchall()
    return {
        "joins": joins,
        "leaves": leaves,
        "daily": [dict(r) for r in daily],
    }


# ---------------------------------------------------------------------------
# Expanded stats: helper
# ---------------------------------------------------------------------------

def _cutoff_date(days: int) -> str:
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")


def _gini(values: list[int]) -> float:
    """Gini coefficient for a list of non-negative ints. 0 = equal, 1 = maximally unequal."""
    if not values or len(values) <= 1:
        return 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    grand = sum(sorted_v)
    if grand == 0:
        return 0.0
    cum = sum((2 * (i + 1) - n - 1) * v for i, v in enumerate(sorted_v))
    return round(cum / (n * grand), 2)


# ---------------------------------------------------------------------------
# Expanded stats: server-level queries
# ---------------------------------------------------------------------------

def get_dau_wau_mau(guild_id: int, days: int) -> dict:
    today = _today()
    week_ago = _cutoff_date(7)
    month_ago = _cutoff_date(30)
    with get_conn() as conn:
        dau = conn.execute(
            "SELECT COUNT(DISTINCT user_id) as c FROM user_activity_daily WHERE guild_id = ? AND date = ?",
            (guild_id, today),
        ).fetchone()["c"]
        wau = conn.execute(
            "SELECT COUNT(DISTINCT user_id) as c FROM user_activity_daily WHERE guild_id = ? AND date >= ?",
            (guild_id, week_ago),
        ).fetchone()["c"]
        mau = conn.execute(
            "SELECT COUNT(DISTINCT user_id) as c FROM user_activity_daily WHERE guild_id = ? AND date >= ?",
            (guild_id, month_ago),
        ).fetchone()["c"]
    return {
        "dau": dau, "wau": wau, "mau": mau,
        "dau_wau": round(dau / wau, 2) if wau else 0,
        "dau_mau": round(dau / mau, 2) if mau else 0,
    }


def get_server_word_stats(guild_id: int, days: int) -> dict:
    cutoff = _cutoff_date(days)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(total_words), 0) as tw, COALESCE(SUM(message_count), 0) as mc "
            "FROM user_activity_daily WHERE guild_id = ? AND date >= ?",
            (guild_id, cutoff),
        ).fetchone()
    total_words = row["tw"]
    msg_count = row["mc"]
    return {
        "total_words": total_words,
        "avg_words_per_msg": round(total_words / msg_count, 1) if msg_count else 0,
    }


def get_weekday_weekend_split(guild_id: int, days: int) -> dict:
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT CAST(strftime('%w', recorded_at) AS INTEGER) as dow, COUNT(*) as c "
            "FROM message_events WHERE guild_id = ? AND recorded_at >= ? GROUP BY dow",
            (guild_id, cutoff),
        ).fetchall()
    weekday = sum(r["c"] for r in rows if r["dow"] not in (0, 6))
    weekend = sum(r["c"] for r in rows if r["dow"] in (0, 6))
    total = weekday + weekend
    return {
        "weekday_msgs": weekday, "weekend_msgs": weekend,
        "ratio": round(weekday / total * 100, 1) if total else 0,
    }


def get_channel_growth_trends(guild_id: int, days: int) -> list:
    cutoff = _cutoff_date(days)
    half = days // 2
    mid = _cutoff_date(half) if half > 0 else cutoff
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT channel_id, "
            "SUM(CASE WHEN date >= ? THEN message_count ELSE 0 END) as current_msgs, "
            "SUM(CASE WHEN date < ? AND date >= ? THEN message_count ELSE 0 END) as prior_msgs "
            "FROM channel_activity_daily WHERE guild_id = ? AND date >= ? "
            "GROUP BY channel_id",
            (mid, mid, cutoff, guild_id, cutoff),
        ).fetchall()
    result = []
    for r in rows:
        cur, prev = r["current_msgs"], r["prior_msgs"]
        pct = round((cur - prev) / prev * 100, 1) if prev else (100.0 if cur > 0 else 0)
        result.append({"channel_id": r["channel_id"], "current": cur, "previous": prev, "change_pct": pct})
    result.sort(key=lambda x: x["change_pct"], reverse=True)
    return result


def get_activity_diversity(guild_id: int, days: int) -> dict:
    cutoff = _cutoff_date(days)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT channel_id, SUM(message_count) as total "
            "FROM channel_activity_daily WHERE guild_id = ? AND date >= ? "
            "GROUP BY channel_id ORDER BY total DESC",
            (guild_id, cutoff),
        ).fetchall()
    values = [r["total"] for r in rows if r["total"] > 0]
    if not values:
        return {"gini": 0, "top3_share": 0}
    grand = sum(values)
    top3 = sum(values[:3])
    return {
        "gini": _gini(values),
        "top3_share": round(top3 / grand * 100, 1) if grand else 0,
    }


def get_message_velocity(guild_id: int, days: int) -> dict:
    half = max(days // 2, 1)
    cutoff = _cutoff_date(days)
    mid = _cutoff_date(half)
    with get_conn() as conn:
        current = conn.execute(
            "SELECT COALESCE(SUM(message_count), 0) as c FROM user_activity_daily WHERE guild_id = ? AND date >= ?",
            (guild_id, mid),
        ).fetchone()["c"]
        prior = conn.execute(
            "SELECT COALESCE(SUM(message_count), 0) as c FROM user_activity_daily WHERE guild_id = ? AND date >= ? AND date < ?",
            (guild_id, cutoff, mid),
        ).fetchone()["c"]
    cur_hours = max(half * 24, 1)
    prior_hours = max((days - half) * 24, 1)
    cur_rate = round(current / cur_hours, 1)
    prior_rate = round(prior / prior_hours, 1)
    change = round((cur_rate - prior_rate) / prior_rate * 100, 1) if prior_rate else (100.0 if cur_rate > 0 else 0)
    return {"current_rate": cur_rate, "prior_rate": prior_rate, "change_pct": change}


# ---------------------------------------------------------------------------
# Expanded stats: user-level queries
# ---------------------------------------------------------------------------

def get_user_word_stats(guild_id: int, user_id: int, days: int) -> dict:
    cutoff = _cutoff_date(days)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(total_words), 0) as tw, COALESCE(SUM(message_count), 0) as mc "
            "FROM user_activity_daily WHERE guild_id = ? AND user_id = ? AND date >= ?",
            (guild_id, user_id, cutoff),
        ).fetchone()
    tw, mc = row["tw"], row["mc"]
    return {"total_words": tw, "avg_words": round(tw / mc, 1) if mc else 0}


def get_user_active_hours(guild_id: int, user_id: int, days: int) -> list:
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT CAST(substr(recorded_at, 12, 2) AS INTEGER) as hour, COUNT(*) as count "
            "FROM message_events WHERE guild_id = ? AND user_id = ? AND recorded_at >= ? "
            "GROUP BY hour",
            (guild_id, user_id, cutoff),
        ).fetchall()
    by_hour = {r["hour"]: r["count"] for r in rows}
    return [{"hour": h, "count": by_hour.get(h, 0)} for h in range(24)]


def get_user_streaks(guild_id: int, user_id: int, days: int) -> dict:
    cutoff = _cutoff_date(days)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, message_count FROM user_activity_daily "
            "WHERE guild_id = ? AND user_id = ? AND date >= ? ORDER BY date",
            (guild_id, user_id, cutoff),
        ).fetchall()
    if not rows:
        return {"current": 0, "longest": 0, "active_days": 0, "total_days": days}
    active_dates = set()
    for r in rows:
        if r["message_count"] > 0:
            active_dates.add(r["date"])
    # Compute streaks by iterating calendar days
    today = datetime.datetime.now(datetime.timezone.utc).date()
    current_streak = 0
    longest_streak = 0
    streak = 0
    d = datetime.datetime.strptime(cutoff, "%Y-%m-%d").date()
    while d <= today:
        ds = d.strftime("%Y-%m-%d")
        if ds in active_dates:
            streak += 1
            longest_streak = max(longest_streak, streak)
        else:
            streak = 0
        d += datetime.timedelta(days=1)
    # Current streak: count back from today, or from yesterday if today has no
    # activity yet — the day isn't over, and today's rollup may not have run.
    current_streak = 0
    d = today
    if d.strftime("%Y-%m-%d") not in active_dates:
        d -= datetime.timedelta(days=1)
    while d.strftime("%Y-%m-%d") in active_dates:
        current_streak += 1
        d -= datetime.timedelta(days=1)
    return {
        "current": current_streak, "longest": longest_streak,
        "active_days": len(active_dates), "total_days": days,
    }


def get_user_weekday_split(guild_id: int, user_id: int, days: int) -> dict:
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT CAST(strftime('%w', recorded_at) AS INTEGER) as dow, COUNT(*) as c "
            "FROM message_events WHERE guild_id = ? AND user_id = ? AND recorded_at >= ? GROUP BY dow",
            (guild_id, user_id, cutoff),
        ).fetchall()
    weekday = sum(r["c"] for r in rows if r["dow"] not in (0, 6))
    weekend = sum(r["c"] for r in rows if r["dow"] in (0, 6))
    return {"weekday": weekday, "weekend": weekend}


def get_user_rank(guild_id: int, user_id: int, days: int) -> dict:
    cutoff = _cutoff_date(days)
    with get_conn() as conn:
        msg_leaders = conn.execute(
            "SELECT user_id FROM user_activity_daily WHERE guild_id = ? AND date >= ? "
            "GROUP BY user_id ORDER BY SUM(message_count) DESC",
            (guild_id, cutoff),
        ).fetchall()
        voice_leaders = conn.execute(
            "SELECT user_id FROM user_activity_daily WHERE guild_id = ? AND date >= ? "
            "GROUP BY user_id ORDER BY SUM(voice_minutes) DESC",
            (guild_id, cutoff),
        ).fetchall()
    msg_rank = next((i + 1 for i, r in enumerate(msg_leaders) if r["user_id"] == user_id), 0)
    voice_rank = next((i + 1 for i, r in enumerate(voice_leaders) if r["user_id"] == user_id), 0)
    return {"msg_rank": msg_rank, "voice_rank": voice_rank, "total_users": len(msg_leaders)}


def get_user_dormancy(guild_id: int, user_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(date) as last_date FROM user_activity_daily "
            "WHERE guild_id = ? AND user_id = ? AND message_count > 0",
            (guild_id, user_id),
        ).fetchone()
    if row and row["last_date"]:
        last = datetime.datetime.strptime(row["last_date"], "%Y-%m-%d").date()
        today = datetime.datetime.now(datetime.timezone.utc).date()
        return {"days_since_last": (today - last).days, "last_date": row["last_date"]}
    return {"days_since_last": -1, "last_date": None}


def get_user_engagement_ratios(guild_id: int, user_id: int, days: int) -> dict:
    cutoff = _cutoff_date(days)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(message_count), 0) as mc, COALESCE(SUM(reactions_given), 0) as rg, "
            "COALESCE(SUM(reactions_received), 0) as rr "
            "FROM user_activity_daily WHERE guild_id = ? AND user_id = ? AND date >= ?",
            (guild_id, user_id, cutoff),
        ).fetchone()
    mc = row["mc"]
    return {
        "reaction_per_msg": round(row["rg"] / mc, 2) if mc else 0,
        "received_per_msg": round(row["rr"] / mc, 2) if mc else 0,
    }


# ---------------------------------------------------------------------------
# Expanded stats: channel-level queries
# ---------------------------------------------------------------------------

def get_channel_word_stats(guild_id: int, channel_id: int, days: int) -> dict:
    cutoff = _cutoff_date(days)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(total_words), 0) as tw, COALESCE(SUM(message_count), 0) as mc "
            "FROM channel_activity_daily WHERE guild_id = ? AND channel_id = ? AND date >= ?",
            (guild_id, channel_id, cutoff),
        ).fetchone()
    tw, mc = row["tw"], row["mc"]
    return {"total_words": tw, "avg_words": round(tw / mc, 1) if mc else 0}


def get_channel_hourly_heatmap(guild_id: int, channel_id: int, days: int) -> list:
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT CAST(substr(recorded_at, 12, 2) AS INTEGER) as hour, COUNT(*) as count "
            "FROM message_events WHERE guild_id = ? AND channel_id = ? AND recorded_at >= ? "
            "GROUP BY hour",
            (guild_id, channel_id, cutoff),
        ).fetchall()
    by_hour = {r["hour"]: r["count"] for r in rows}
    return [{"hour": h, "count": by_hour.get(h, 0)} for h in range(24)]


def get_channel_user_concentration(guild_id: int, channel_id: int, days: int) -> dict:
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, COUNT(*) as c FROM message_events "
            "WHERE guild_id = ? AND channel_id = ? AND recorded_at >= ? "
            "GROUP BY user_id ORDER BY c DESC",
            (guild_id, channel_id, cutoff),
        ).fetchall()
    values = [r["c"] for r in rows]
    if not values:
        return {"top3_share": 0, "gini": 0}
    grand = sum(values)
    top3 = sum(values[:3])
    return {
        "top3_share": round(top3 / grand * 100, 1) if grand else 0,
        "gini": _gini(values),
    }


def get_channel_weekday_split(guild_id: int, channel_id: int, days: int) -> dict:
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT CAST(strftime('%w', recorded_at) AS INTEGER) as dow, COUNT(*) as c "
            "FROM message_events WHERE guild_id = ? AND channel_id = ? AND recorded_at >= ? GROUP BY dow",
            (guild_id, channel_id, cutoff),
        ).fetchall()
    weekday = sum(r["c"] for r in rows if r["dow"] not in (0, 6))
    weekend = sum(r["c"] for r in rows if r["dow"] in (0, 6))
    return {"weekday": weekday, "weekend": weekend}


def get_channel_density(guild_id: int, channel_id: int, days: int) -> dict:
    cutoff = _cutoff_date(days)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(message_count), 0) as mc, COALESCE(SUM(unique_users), 0) as uu "
            "FROM channel_activity_daily WHERE guild_id = ? AND channel_id = ? AND date >= ?",
            (guild_id, channel_id, cutoff),
        ).fetchone()
    mc, uu = row["mc"], row["uu"]
    return {"msgs_per_user": round(mc / uu, 1) if uu else 0}


def get_channel_growth(guild_id: int, channel_id: int, days: int) -> dict:
    half = max(days // 2, 1)
    cutoff = _cutoff_date(days)
    mid = _cutoff_date(half)
    with get_conn() as conn:
        current = conn.execute(
            "SELECT COALESCE(SUM(message_count), 0) as c FROM channel_activity_daily WHERE guild_id = ? AND channel_id = ? AND date >= ?",
            (guild_id, channel_id, mid),
        ).fetchone()["c"]
        prior = conn.execute(
            "SELECT COALESCE(SUM(message_count), 0) as c FROM channel_activity_daily WHERE guild_id = ? AND channel_id = ? AND date >= ? AND date < ?",
            (guild_id, channel_id, cutoff, mid),
        ).fetchone()["c"]
    change = round((current - prior) / prior * 100, 1) if prior else (100.0 if current > 0 else 0)
    return {"current": current, "previous": prior, "change_pct": change}


# ---------------------------------------------------------------------------
# Expanded stats: voice-level queries
# ---------------------------------------------------------------------------

def get_voice_session_distribution(guild_id: int, days: int) -> dict:
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT duration_seconds FROM voice_sessions "
            "WHERE guild_id = ? AND joined_at >= ? AND duration_seconds IS NOT NULL "
            "ORDER BY duration_seconds",
            (guild_id, cutoff),
        ).fetchall()
    if not rows:
        return {"median": 0, "p25": 0, "p75": 0, "max": 0, "count": 0, "buckets": {}}
    durations = [r["duration_seconds"] for r in rows]
    n = len(durations)
    buckets = {"<5m": 0, "5-15m": 0, "15-30m": 0, "30-60m": 0, "1-2h": 0, "2h+": 0}
    for d in durations:
        m = d / 60
        if m < 5:
            buckets["<5m"] += 1
        elif m < 15:
            buckets["5-15m"] += 1
        elif m < 30:
            buckets["15-30m"] += 1
        elif m < 60:
            buckets["30-60m"] += 1
        elif m < 120:
            buckets["1-2h"] += 1
        else:
            buckets["2h+"] += 1
    return {
        "median": durations[n // 2],
        "p25": durations[n // 4],
        "p75": durations[3 * n // 4],
        "max": durations[-1],
        "count": n,
        "buckets": buckets,
    }


def get_voice_day_of_week(guild_id: int, days: int) -> list:
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT CAST(strftime('%w', joined_at) AS INTEGER) as dow, "
            "COUNT(*) as sessions, COALESCE(SUM(duration_seconds), 0) / 60 as minutes "
            "FROM voice_sessions WHERE guild_id = ? AND joined_at >= ? AND duration_seconds IS NOT NULL "
            "GROUP BY dow",
            (guild_id, cutoff),
        ).fetchall()
    by_dow = {r["dow"]: {"sessions": r["sessions"], "minutes": r["minutes"]} for r in rows}
    return [{"day": d, "sessions": by_dow.get(d, {}).get("sessions", 0),
             "minutes": by_dow.get(d, {}).get("minutes", 0)} for d in range(7)]


def get_voice_peak_hours(guild_id: int, days: int) -> list:
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT CAST(substr(joined_at, 12, 2) AS INTEGER) as hour, COUNT(*) as sessions "
            "FROM voice_sessions WHERE guild_id = ? AND joined_at >= ? AND duration_seconds IS NOT NULL "
            "GROUP BY hour",
            (guild_id, cutoff),
        ).fetchall()
    by_hour = {r["hour"]: r["sessions"] for r in rows}
    return [{"hour": h, "sessions": by_hour.get(h, 0)} for h in range(24)]


def get_user_voice_session_count(guild_id: int, user_id: int, days: int) -> int:
    """Return the number of completed voice sessions for a user in the given window."""
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) as c FROM voice_sessions "
            "WHERE guild_id = ? AND user_id = ? AND joined_at >= ? AND duration_seconds IS NOT NULL",
            (guild_id, user_id, cutoff),
        ).fetchone()["c"]


# ---------------------------------------------------------------------------
# Expanded stats: growth-level queries
# ---------------------------------------------------------------------------

def get_churn_metrics(guild_id: int, days: int) -> dict:
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        leaves = conn.execute(
            "SELECT COUNT(*) as c FROM member_events WHERE guild_id = ? AND event_type = 'leave' AND recorded_at >= ?",
            (guild_id, cutoff),
        ).fetchone()["c"]
        bans = conn.execute(
            "SELECT COUNT(*) as c FROM member_events WHERE guild_id = ? AND event_type = 'ban' AND recorded_at >= ?",
            (guild_id, cutoff),
        ).fetchone()["c"]
        snap = conn.execute(
            "SELECT total_members FROM member_snapshots WHERE guild_id = ? ORDER BY recorded_at DESC LIMIT 1",
            (guild_id,),
        ).fetchone()
    total = snap["total_members"] if snap else 0
    departures = leaves + bans
    return {
        "churn_rate": round(departures / total * 100, 1) if total else 0,
        "ban_rate": round(bans / departures * 100, 1) if departures else 0,
        "turnover": departures,
    }


def get_join_day_distribution(guild_id: int, days: int) -> list:
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT CAST(strftime('%w', recorded_at) AS INTEGER) as dow, COUNT(*) as c "
            "FROM member_events WHERE guild_id = ? AND event_type = 'join' AND recorded_at >= ? "
            "GROUP BY dow",
            (guild_id, cutoff),
        ).fetchall()
    by_dow = {r["dow"]: r["c"] for r in rows}
    return [{"day": d, "count": by_dow.get(d, 0)} for d in range(7)]


# ---------------------------------------------------------------------------
# Expanded stats: peakhours queries
# ---------------------------------------------------------------------------

def get_per_channel_peak_hours(guild_id: int, days: int, limit: int = 5) -> list:
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        # Get top channels by message count
        channels = conn.execute(
            "SELECT channel_id, COUNT(*) as total_msgs FROM message_events "
            "WHERE guild_id = ? AND recorded_at >= ? "
            "GROUP BY channel_id ORDER BY total_msgs DESC LIMIT ?",
            (guild_id, cutoff, limit),
        ).fetchall()
        result = []
        for ch in channels:
            peak = conn.execute(
                "SELECT CAST(substr(recorded_at, 12, 2) AS INTEGER) as hour, COUNT(*) as c "
                "FROM message_events WHERE guild_id = ? AND channel_id = ? AND recorded_at >= ? "
                "GROUP BY hour ORDER BY c DESC LIMIT 1",
                (guild_id, ch["channel_id"], cutoff),
            ).fetchone()
            result.append({
                "channel_id": ch["channel_id"],
                "total_msgs": ch["total_msgs"],
                "peak_hour": peak["hour"] if peak else 0,
            })
    return result


def get_hourly_weekday_weekend(guild_id: int, days: int) -> dict:
    cutoff = _cutoff_datetime(days)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT CAST(substr(recorded_at, 12, 2) AS INTEGER) as hour, "
            "CAST(strftime('%w', recorded_at) AS INTEGER) as dow, COUNT(*) as c "
            "FROM message_events WHERE guild_id = ? AND recorded_at >= ? "
            "GROUP BY hour, dow",
            (guild_id, cutoff),
        ).fetchall()
    weekday_hours = {h: 0 for h in range(24)}
    weekend_hours = {h: 0 for h in range(24)}
    for r in rows:
        if r["dow"] in (0, 6):
            weekend_hours[r["hour"]] += r["c"]
        else:
            weekday_hours[r["hour"]] += r["c"]
    return {
        "weekday": [{"hour": h, "count": weekday_hours[h]} for h in range(24)],
        "weekend": [{"hour": h, "count": weekend_hours[h]} for h in range(24)],
    }


# ---------------------------------------------------------------------------
# Expanded stats: leaderboard query
# ---------------------------------------------------------------------------

def get_leaderboard(guild_id: int, days: int, category: str, limit: int = 10) -> list:
    cutoff = _cutoff_date(days)
    with get_conn() as conn:
        if category == "messages":
            rows = conn.execute(
                "SELECT user_id, SUM(message_count) as value FROM user_activity_daily "
                "WHERE guild_id = ? AND date >= ? GROUP BY user_id ORDER BY value DESC LIMIT ?",
                (guild_id, cutoff, limit),
            ).fetchall()
        elif category == "voice":
            rows = conn.execute(
                "SELECT user_id, SUM(voice_minutes) as value FROM user_activity_daily "
                "WHERE guild_id = ? AND date >= ? GROUP BY user_id ORDER BY value DESC LIMIT ?",
                (guild_id, cutoff, limit),
            ).fetchall()
        elif category == "social":
            rows = conn.execute(
                "SELECT user_id, SUM(reactions_given) as value FROM user_activity_daily "
                "WHERE guild_id = ? AND date >= ? GROUP BY user_id ORDER BY value DESC LIMIT ?",
                (guild_id, cutoff, limit),
            ).fetchall()
        elif category == "engagement":
            rows = conn.execute(
                "SELECT user_id, CASE WHEN SUM(message_count) > 0 "
                "THEN ROUND(CAST(SUM(reactions_received) AS FLOAT) / SUM(message_count), 2) ELSE 0 END as value "
                "FROM user_activity_daily WHERE guild_id = ? AND date >= ? "
                "GROUP BY user_id HAVING SUM(message_count) > 0 ORDER BY value DESC LIMIT ?",
                (guild_id, cutoff, limit),
            ).fetchall()
        else:
            rows = []
    return [{"user_id": r["user_id"], "value": r["value"]} for r in rows]
