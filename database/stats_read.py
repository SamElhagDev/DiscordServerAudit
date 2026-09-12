"""Read-only analytics queries backing the stats commands.

Split out of the former single-file database.py; import via ``database.<name>``.
"""
import datetime
import logging
from .connection import get_conn
from .common import _today, _cutoff_datetime, _cutoff_date, _gini

logger = logging.getLogger(__name__)


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
