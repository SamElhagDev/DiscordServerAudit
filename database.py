import logging
import sqlite3
import datetime
import os
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DiscordServerAudit_DB_PATH", "bot.db")
_conn: sqlite3.Connection | None = None


def _ensure_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, timeout=10)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        logger.debug("Opened persistent database connection to %s", DB_PATH)
    return _conn


def close_db():
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
        logger.info("Database connection closed")


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
        logger.info("Database initialised successfully")
    except Exception:
        logger.critical("Failed to initialise database at %s", DB_PATH, exc_info=True)
        raise


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
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _days_ago(days: int) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Stats: write helpers (called from event handlers)
# ---------------------------------------------------------------------------

def log_message_event(guild_id: int, channel_id: int, user_id: int, word_count: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO message_events (guild_id, channel_id, user_id, recorded_at, word_count) VALUES (?, ?, ?, ?, ?)",
            (guild_id, channel_id, user_id, _now(), word_count),
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
            INSERT INTO user_activity_daily (guild_id, user_id, date, message_count, voice_minutes)
            SELECT me.guild_id, me.user_id, ?, COUNT(*), 0
            FROM message_events me
            WHERE me.guild_id = ? AND DATE(me.recorded_at) = ?
            GROUP BY me.guild_id, me.user_id
            ON CONFLICT(guild_id, user_id, date) DO UPDATE
                SET message_count = excluded.message_count
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
            INSERT INTO channel_activity_daily (guild_id, channel_id, date, message_count, unique_users)
            SELECT guild_id, channel_id, ?, COUNT(*), COUNT(DISTINCT user_id)
            FROM message_events
            WHERE guild_id = ? AND DATE(recorded_at) = ?
            GROUP BY guild_id, channel_id
            ON CONFLICT(guild_id, channel_id, date) DO UPDATE
                SET message_count = excluded.message_count,
                    unique_users = excluded.unique_users
        """, (date, guild_id, date))
    logger.debug("Rolled up channel activity: guild=%s date=%s", guild_id, date)


def prune_old_events(days: int):
    cutoff = _days_ago(days)
    with get_conn() as conn:
        r1 = conn.execute("DELETE FROM message_events WHERE recorded_at < ?", (cutoff,))
        r2 = conn.execute("DELETE FROM voice_sessions WHERE joined_at < ? AND left_at IS NOT NULL", (cutoff,))
        logger.info("Pruned old events: %d messages, %d voice sessions (older than %d days)",
                     r1.rowcount, r2.rowcount, days)


# ---------------------------------------------------------------------------
# Stats: read helpers (called from stats commands)
# ---------------------------------------------------------------------------

def get_server_stats_summary(guild_id: int, days: int) -> dict:
    cutoff = _days_ago(days)
    cutoff_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        msgs = conn.execute(
            "SELECT COUNT(*) as c FROM message_events WHERE guild_id = ? AND recorded_at >= ?",
            (guild_id, cutoff),
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
    cutoff = _days_ago(days)
    with get_conn() as conn:
        return conn.execute(
            "SELECT CAST(strftime('%%H', recorded_at) AS INTEGER) as hour, COUNT(*) as count "
            "FROM message_events WHERE guild_id = ? AND recorded_at >= ? "
            "GROUP BY hour ORDER BY hour",
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
            (guild_id, user_id, _days_ago(days), guild_id, user_id),
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
            (guild_id, user_id, _days_ago(days)),
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
            (guild_id, user_id, _days_ago(days)),
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
    cutoff = _days_ago(days)
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
            "SELECT CAST(strftime('%%H', recorded_at) AS INTEGER) as hour, COUNT(*) as count "
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
    cutoff = _days_ago(days)
    with get_conn() as conn:
        return conn.execute(
            "SELECT channel_id, SUM(duration_seconds) as total FROM voice_sessions "
            "WHERE guild_id = ? AND joined_at >= ? AND duration_seconds IS NOT NULL "
            "GROUP BY channel_id ORDER BY total DESC LIMIT ?",
            (guild_id, cutoff, limit),
        ).fetchall()


def get_member_growth(guild_id: int, days: int):
    cutoff = _days_ago(days)
    with get_conn() as conn:
        return conn.execute(
            "SELECT recorded_at, total_members FROM member_snapshots "
            "WHERE guild_id = ? AND recorded_at >= ? ORDER BY recorded_at",
            (guild_id, cutoff),
        ).fetchall()


def get_member_events_summary(guild_id: int, days: int) -> dict:
    cutoff = _days_ago(days)
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
