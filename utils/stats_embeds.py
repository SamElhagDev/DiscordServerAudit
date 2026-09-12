"""Presentation helpers for the stats dashboards.

Pure formatting and scoring functions extracted from cogs/stats.py — bar charts, sparklines,
heatmaps, trend arrows, health scoring, and UTC->Eastern display conversion. No cog state, so
each one is independently testable.
"""
import datetime
import json
import logging
import urllib.parse

import aiohttp
import discord

logger = logging.getLogger(__name__)


try:
    import zoneinfo
    EASTERN = zoneinfo.ZoneInfo("America/New_York")
except Exception:
    EASTERN = None


def et_offset() -> tuple[int, str]:
    """Return (utc_offset_hours, abbreviation) for Eastern Time right now.
    Used as fallback when tzdata is not installed."""
    now = datetime.datetime.now(datetime.timezone.utc)
    year = now.year
    # DST starts 2nd Sunday in March at 07:00 UTC (= 02:00 EST)
    mar1 = datetime.date(year, 3, 1)
    first_sun_mar = mar1 + datetime.timedelta(days=(6 - mar1.weekday()) % 7)
    dst_start = datetime.datetime(year, 3, first_sun_mar.day + 7, 7, 0, 0, tzinfo=datetime.timezone.utc)
    # DST ends 1st Sunday in November at 06:00 UTC (= 02:00 EDT)
    nov1 = datetime.date(year, 11, 1)
    first_sun_nov = nov1 + datetime.timedelta(days=(6 - nov1.weekday()) % 7)
    dst_end = datetime.datetime(year, 11, first_sun_nov.day, 6, 0, 0, tzinfo=datetime.timezone.utc)
    if dst_start <= now < dst_end:
        return -4, "EDT"
    return -5, "EST"


def utc_hour_to_et(hour: int) -> str:
    """Convert a UTC hour (0-23) to an ET display string, e.g. '09:00 EDT'."""
    if EASTERN is not None:
        today = datetime.date.today()
        utc_dt = datetime.datetime(today.year, today.month, today.day, hour, 0, 0,
                                   tzinfo=datetime.timezone.utc)
        et_dt = utc_dt.astimezone(EASTERN)
        offset_h = int(et_dt.utcoffset().total_seconds() // 3600)
        abbr = "EST" if offset_h == -5 else "EDT"
        return f"{et_dt.hour:02d}:00 {abbr}"
    offset, abbr = et_offset()
    return f"{(hour + offset) % 24:02d}:00 {abbr}"


COLOR_POSITIVE = 0x2ECC71


COLOR_NEUTRAL = 0x3498DB


COLOR_NEGATIVE = 0xE74C3C


COLOR_VOICE = 0x9B59B6


COLOR_GEMINI = 0xF39C12


CHART_BG = "rgb(47,49,54)"


CHART_TEXT = "rgb(255,255,255)"


CHART_GRID = "rgba(255,255,255,0.1)"


def build_bar_chart(items: list[tuple], max_width: int = 12, show_pct: bool = False) -> str:
    if not items:
        return "```\nNo data\n```"
    max_val = max(v for _, v in items)
    total = sum(v for _, v in items) if show_pct else 0
    lines = []
    max_label = 12
    for i, (label, value) in enumerate(items):
        bar_len = int((value / max_val) * max_width) if max_val else 0
        bar = "█" * bar_len + "░" * (max_width - bar_len)
        name = str(label)[:max_label]
        if show_pct and total:
            pct = value * 100 // total
            lines.append(f"{name:<{max_label}} {bar} {pct:>2}%")
        else:
            lines.append(f"{name:<{max_label}} {bar} {value:,}")
    return "```\n" + "\n".join(lines) + "\n```"


def build_quickchart_url(chart_config: dict) -> str:
    cfg = json.dumps(chart_config, separators=(",", ":"))
    encoded = urllib.parse.quote(cfg, safe="")
    return f"https://quickchart.io/chart?c={encoded}&w=500&h=300&bkg={urllib.parse.quote(CHART_BG)}"


async def chart_url(chart_config: dict) -> str:
    url = build_quickchart_url(chart_config)
    if len(url) <= 2048:
        return url
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://quickchart.io/chart/create",
                json={"chart": chart_config, "width": 500, "height": 300, "backgroundColor": CHART_BG},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("url", "")
    except Exception:
        logger.warning("Failed to create short chart URL", exc_info=True)
    return url


def trend_indicator(current: float, previous: float) -> str:
    if previous == 0:
        return "\U0001f4c8 ↑ new" if current > 0 else "➡️ ─ 0%"
    pct = ((current - previous) / previous) * 100
    if pct > 0:
        return f"\U0001f4c8 ↑ {pct:.1f}%"
    elif pct < 0:
        return f"\U0001f4c9 ↓ {abs(pct):.1f}%"
    return "➡️ ─ 0%"


def format_duration(minutes: int) -> str:
    if minutes <= 0:
        return "—"
    if minutes >= 60:
        h, m = divmod(minutes, 60)
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{minutes}m"


def format_seconds(seconds: int) -> str:
    return format_duration(seconds // 60)


def embed_color_for_trend(current: float, previous: float) -> int:
    if current > previous:
        return COLOR_POSITIVE
    elif current < previous:
        return COLOR_NEGATIVE
    return COLOR_NEUTRAL


def sparkline(values: list[int], width: int = 14) -> str:
    if not values:
        return ""
    bars = "▁▂▃▄▅▆▇█"
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1
    recent = values[-width:]
    return "".join(bars[min(int((v - mn) / rng * 7), 7)] for v in recent)


def gini_coefficient(values: list[int]) -> float:
    """Compute Gini coefficient for a distribution. 0 = perfectly equal, 1 = maximally unequal."""
    if not values or all(v == 0 for v in values):
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    cumulative = sum((2 * (i + 1) - n - 1) * v for i, v in enumerate(sorted_vals))
    return cumulative / (n * sum(sorted_vals)) if sum(sorted_vals) else 0.0


def composite_health_score(metrics: dict) -> int:
    """Compute server health score 0-100 from five 0-20 components.

    Expected keys in *metrics*:
      dau_mau      – float 0-1 (DAU/MAU ratio)
      reaction_per_msg – float (reactions per message)
      churn_rate   – float 0-1
      voice_rate   – float 0-1 (voice participants / total members)
      net_growth   – int  (net member change)
    """
    # 1. Activity: DAU/MAU ratio × 20
    activity = min(20, metrics.get("dau_mau", 0) * 20)
    # 2. Engagement: reactions per message, normalised (0.5 = max)
    rpm = metrics.get("reaction_per_msg", 0)
    engagement = min(20, (rpm / 0.5) * 20)
    # 3. Retention: (1 - churn_rate) × 20
    retention = min(20, (1 - metrics.get("churn_rate", 0)) * 20)
    # 4. Voice participation × 20
    voice = min(20, metrics.get("voice_rate", 0) * 20)
    # 5. Growth direction
    net = metrics.get("net_growth", 0)
    growth = 20 if net > 0 else (10 if net == 0 else 5)

    return int(round(activity + engagement + retention + voice + growth))


def health_label(score: int) -> str:
    """Return a human-readable label for a health score 0-100."""
    if score <= 20:
        return "Critical"
    if score <= 40:
        return "Needs Attention"
    if score <= 60:
        return "Average"
    if score <= 80:
        return "Healthy"
    return "Thriving"


def weekday_weekend_label(weekday: int, weekend: int) -> str:
    """Format a weekday/weekend split as 'X% / Y%', or 'N/A' if no data."""
    total = weekday + weekend
    if total == 0:
        return "N/A"
    return f"{weekday * 100 // total}% / {weekend * 100 // total}%"


def day_name(day_num: int) -> str:
    """Map SQLite strftime('%w') integer to abbreviated day name.

    strftime('%w') returns 0 = Sunday … 6 = Saturday.
    """
    return ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")[day_num % 7]


def build_heatmap_bar(hours_data: list, width: int = 24, offset: int = 0) -> str:
    """Build a 24-char heatmap bar from hourly data using block characters.

    *hours_data* is a list of dicts with 'hour' (0-23) and 'count' keys.
    *offset* rotates the bar so position 0 represents local hour 0
    (pass ``et_offset()[0]`` to convert UTC → ET).
    Returns a string like '░░░░░░▁▂▃▅▆██████▇▆▅▃▂▁░'.
    """
    blocks = "░▁▂▃▄▅▆▇█"
    counts = [0] * 24
    for entry in hours_data:
        h = entry.get("hour", 0)
        if 0 <= h < 24:
            counts[h] = entry.get("count", 0)
    mx = max(counts) if counts else 0
    if mx == 0:
        return "░" * width
    return "".join(blocks[min(int(counts[(h - offset) % 24] / mx * 8), 8)] for h in range(width))


def count_online_members(guild, bot) -> int | None:
    """Number of non-offline members, or None if the presences intent is disabled.

    Without that privileged intent every member reports as offline, so a raw count
    would be a misleading 0. Callers should show "N/A" when this returns None.
    """
    if not bot.intents.presences:
        return None
    return sum(1 for m in guild.members if m.status != discord.Status.offline)
