"""Stats write path: message/voice/member events, reactions, and rollups.

Split out of the former single-file database.py; import via ``database.<name>``.
"""
import datetime
import logging
from .connection import get_conn
from .common import _now, _today, _cutoff_datetime

logger = logging.getLogger(__name__)


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
