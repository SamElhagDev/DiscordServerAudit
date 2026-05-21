import json
import logging
import urllib.parse
import datetime

import discord
from discord.ext import commands

import config
import database
from utils.permissions import has_admin_role

try:
    import zoneinfo
    _ET = zoneinfo.ZoneInfo("America/New_York")
except Exception:
    _ET = None


def _et_offset() -> tuple[int, str]:
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


def _utc_hour_to_et(hour: int) -> str:
    """Convert a UTC hour (0-23) to an ET display string, e.g. '09:00 EDT'."""
    if _ET is not None:
        today = datetime.date.today()
        utc_dt = datetime.datetime(today.year, today.month, today.day, hour, 0, 0,
                                   tzinfo=datetime.timezone.utc)
        et_dt = utc_dt.astimezone(_ET)
        offset_h = int(et_dt.utcoffset().total_seconds() // 3600)
        abbr = "EST" if offset_h == -5 else "EDT"
        return f"{et_dt.hour:02d}:00 {abbr}"
    offset, abbr = _et_offset()
    return f"{(hour + offset) % 24:02d}:00 {abbr}"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Visual constants
# ---------------------------------------------------------------------------
COLOR_POSITIVE = 0x2ECC71
COLOR_NEUTRAL = 0x3498DB
COLOR_NEGATIVE = 0xE74C3C
COLOR_VOICE = 0x9B59B6
COLOR_GEMINI = 0xF39C12

CHART_BG = "rgb(47,49,54)"
CHART_TEXT = "rgb(255,255,255)"
CHART_GRID = "rgba(255,255,255,0.1)"


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _build_bar_chart(items: list[tuple], max_width: int = 20) -> str:
    if not items:
        return "```\nNo data\n```"
    max_val = max(v for _, v in items)
    lines = []
    label_width = max(len(str(label)) for label, _ in items)
    for i, (label, value) in enumerate(items):
        bar_len = int((value / max_val) * max_width) if max_val else 0
        bar = "█" * bar_len + "░" * (max_width - bar_len)
        lines.append(f"#{i+1:<2} {str(label):<{label_width}}  {bar} {value:,}")
    return "```\n" + "\n".join(lines) + "\n```"


def _build_quickchart_url(chart_config: dict) -> str:
    cfg = json.dumps(chart_config, separators=(",", ":"))
    encoded = urllib.parse.quote(cfg, safe="")
    return f"https://quickchart.io/chart?c={encoded}&w=500&h=300&bkg={urllib.parse.quote(CHART_BG)}"


async def _chart_url(chart_config: dict) -> str:
    url = _build_quickchart_url(chart_config)
    if len(url) <= 2048:
        return url
    import aiohttp
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


def _trend_indicator(current: float, previous: float) -> str:
    if previous == 0:
        return "\U0001f4c8 ↑ new" if current > 0 else "➡️ ─ 0%"
    pct = ((current - previous) / previous) * 100
    if pct > 0:
        return f"\U0001f4c8 ↑ {pct:.1f}%"
    elif pct < 0:
        return f"\U0001f4c9 ↓ {abs(pct):.1f}%"
    return "➡️ ─ 0%"


def _format_duration(minutes: int) -> str:
    return f"{minutes}m"


def _format_seconds(seconds: int) -> str:
    return _format_duration(seconds // 60)


def _embed_color_for_trend(current: float, previous: float) -> int:
    if current > previous:
        return COLOR_POSITIVE
    elif current < previous:
        return COLOR_NEGATIVE
    return COLOR_NEUTRAL


def _sparkline(values: list[int], width: int = 14) -> str:
    if not values:
        return ""
    bars = "▁▂▃▄▅▆▇█"
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1
    recent = values[-width:]
    return "".join(bars[min(int((v - mn) / rng * 7), 7)] for v in recent)


# ---------------------------------------------------------------------------
# Expanded-stats utility functions
# ---------------------------------------------------------------------------

def _gini_coefficient(values: list[int]) -> float:
    """Compute Gini coefficient for a distribution. 0 = perfectly equal, 1 = maximally unequal."""
    if not values or all(v == 0 for v in values):
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    cumulative = sum((2 * (i + 1) - n - 1) * v for i, v in enumerate(sorted_vals))
    return cumulative / (n * sum(sorted_vals)) if sum(sorted_vals) else 0.0


def _compute_streak(daily_rows: list[dict]) -> tuple[int, int]:
    """Compute (current_streak, longest_streak) from daily activity rows.

    Each row must have a 'date' key (YYYY-MM-DD string) and a 'message_count' key.
    Returns (0, 0) if no active days found.
    """
    if not daily_rows:
        return 0, 0
    active_dates = sorted(
        {r["date"] for r in daily_rows if r.get("message_count", 0) > 0}
    )
    if not active_dates:
        return 0, 0

    today = datetime.date.today().isoformat()
    longest = 1
    current = 1
    for i in range(1, len(active_dates)):
        prev = datetime.date.fromisoformat(active_dates[i - 1])
        curr = datetime.date.fromisoformat(active_dates[i])
        if (curr - prev).days == 1:
            current += 1
            longest = max(longest, current)
        else:
            current = 1

    # current_streak = streak length that includes today (or yesterday)
    last_active = datetime.date.fromisoformat(active_dates[-1])
    today_dt = datetime.date.today()
    if (today_dt - last_active).days > 1:
        current_streak = 0  # streak broken
    else:
        # Walk backwards from end to count the current run
        current_streak = 1
        for i in range(len(active_dates) - 1, 0, -1):
            prev = datetime.date.fromisoformat(active_dates[i - 1])
            curr = datetime.date.fromisoformat(active_dates[i])
            if (curr - prev).days == 1:
                current_streak += 1
            else:
                break

    return current_streak, max(longest, current_streak)


def _consistency_score(daily_counts: list[int]) -> float:
    """Score 0-100 where 100 = perfectly even daily activity (zero variance).

    Formula: 100 × (1 - std_dev/mean) clamped to [0, 100].
    """
    if not daily_counts or len(daily_counts) < 2:
        return 0.0
    mean = sum(daily_counts) / len(daily_counts)
    if mean == 0:
        return 0.0
    variance = sum((x - mean) ** 2 for x in daily_counts) / len(daily_counts)
    std_dev = variance ** 0.5
    return max(0.0, min(100.0, 100.0 * (1 - std_dev / mean)))


def _composite_health_score(metrics: dict) -> int:
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


def _health_label(score: int) -> str:
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


def _day_name(day_num: int) -> str:
    """Map SQLite strftime('%w') integer to abbreviated day name.

    strftime('%w') returns 0 = Sunday … 6 = Saturday.
    """
    return ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")[day_num % 7]


def _build_heatmap_bar(hours_data: list, width: int = 24) -> str:
    """Build a 24-char heatmap bar from hourly data using block characters.

    *hours_data* is a list of dicts with 'hour' (0-23) and 'count' keys.
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
    return "".join(blocks[min(int(counts[h] / mx * 8), 8)] for h in range(width))


# ---------------------------------------------------------------------------
# Stats Cog
# ---------------------------------------------------------------------------

class Stats(commands.Cog):
    """Comprehensive server stats logging and dashboards."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._scanning = False
        self._scheduler_registered = False

    async def cog_load(self):
        database.close_orphaned_voice_sessions()
        logger.info("Stats cog loaded — orphaned voice sessions closed")

    @commands.Cog.listener()
    async def on_ready(self):
        if self._scheduler_registered:
            return
        self._scheduler_registered = True

        for guild in self.bot.guilds:
            snap_interval = config.get("stats.snapshot_interval_hours", 1)
            self.bot.scheduler.register(
                key=f"stats_snapshot_{guild.id}",
                interval_hours=snap_interval,
                coro_factory=lambda g=guild: self._take_snapshot(g),
            )
            self.bot.scheduler.register(
                key=f"stats_rollup_{guild.id}",
                interval_hours=24,
                coro_factory=lambda g=guild: self._run_rollup(g),
            )
            logger.info("Registered stats scheduler tasks for guild %r (ID=%s)", guild.name, guild.id)

            # Open sessions for members already in voice when the bot connects.
            # on_voice_state_update does not fire for pre-existing voice state on startup.
            if not config.get("stats.enabled", True):
                continue
            exclude_bots = config.get("stats.exclude_bots", True)
            resumed = 0
            for vc in guild.voice_channels:
                for member in vc.members:
                    if member.bot and exclude_bots:
                        continue
                    database.start_voice_session(guild.id, vc.id, member.id)
                    resumed += 1
            if resumed:
                logger.info("Opened %d voice sessions for members already in voice at startup (guild %r)", resumed, guild.name)

    # ------------------------------------------------------------------
    # Scheduler coroutines
    # ------------------------------------------------------------------

    async def _take_snapshot(self, guild: discord.Guild):
        if not config.get("stats.enabled", True):
            return
        try:
            total = guild.member_count or 0
            bots = sum(1 for m in guild.members if m.bot)
            online = sum(1 for m in guild.members if m.status != discord.Status.offline)
            database.save_member_snapshot(
                guild.id, total, online, bots,
                guild.premium_subscription_count or 0,
                guild.premium_tier,
            )
            logger.debug("Snapshot taken: guild=%s total=%d online=%d bots=%d", guild.id, total, online, bots)
        except Exception:
            logger.error("Failed to take member snapshot for guild %s", guild.id, exc_info=True)

    async def _run_rollup(self, guild: discord.Guild):
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            yesterday = (now - datetime.timedelta(days=1)).date()

            last_run = database.get_last_run(f"stats_rollup_{guild.id}")
            if last_run:
                # Use last_run.date() without +1: set_last_run stores the execution timestamp
                # (e.g. May 17), not the last processed date (May 16). Adding 1 would jump to
                # May 18 and permanently skip May 17's raw data.
                first_missed = last_run.date()
            else:
                first_missed = yesterday

            if first_missed < yesterday:
                first_missed = max(first_missed, (now - datetime.timedelta(days=90)).date())

            dates_to_roll = []
            d = first_missed
            while d <= yesterday:
                dates_to_roll.append(d.strftime("%Y-%m-%d"))
                d += datetime.timedelta(days=1)

            for date_str in dates_to_roll:
                database.rollup_user_activity(guild.id, date_str)
                database.rollup_channel_activity(guild.id, date_str)

            retention = config.get("stats.retention_days", 30)
            database.prune_old_events(retention)
            logger.info(
                "Daily rollup complete for guild %r: dates=%d (%s..%s), retention=%dd",
                guild.name, len(dates_to_roll),
                dates_to_roll[0] if dates_to_roll else "none",
                dates_to_roll[-1] if dates_to_roll else "none",
                retention,
            )
        except Exception:
            logger.error("Daily rollup failed for guild %s", guild.id, exc_info=True)

    # ------------------------------------------------------------------
    # Event listeners
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if self._scanning:
            return
        if not config.get("stats.enabled", True):
            return
        if message.guild is None:
            return
        if message.author.bot and config.get("stats.exclude_bots", True):
            return
        excluded_channels = config.get("stats.excluded_channels", [])
        if message.channel.id in excluded_channels:
            return
        excluded_users = config.get("stats.excluded_users", [])
        if message.author.id in excluded_users:
            return
        try:
            word_count = len(message.content.split()) if message.content else 0
            database.log_message_event(message.guild.id, message.channel.id, message.author.id, word_count)
        except Exception:
            logger.error("Failed to log message event: guild=%s channel=%s user=%s", message.guild.id, message.channel.id, message.author.id, exc_info=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if not config.get("stats.enabled", True):
            return
        if member.bot and config.get("stats.exclude_bots", True):
            return
        try:
            if before.channel is None and after.channel is not None:
                logger.debug("Voice join: user=%s guild=%s channel=%s", member.id, member.guild.id, after.channel.id)
                database.start_voice_session(member.guild.id, after.channel.id, member.id)
            elif before.channel is not None and after.channel is None:
                logger.debug("Voice leave: user=%s guild=%s channel=%s", member.id, member.guild.id, before.channel.id)
                database.end_voice_session(member.guild.id, member.id)
            elif before.channel is not None and after.channel is not None and before.channel.id != after.channel.id:
                logger.debug("Voice move: user=%s guild=%s %s→%s", member.id, member.guild.id, before.channel.id, after.channel.id)
                database.end_voice_session(member.guild.id, member.id)
                database.start_voice_session(member.guild.id, after.channel.id, member.id)
        except Exception:
            logger.error("Failed to log voice state update: user=%s guild=%s", member.id, member.guild.id, exc_info=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not self._scanning and config.get("stats.enabled", True):
            try:
                database.log_member_event(member.guild.id, member.id, "join")
            except Exception:
                logger.error("Failed to log member join: user=%s guild=%s", member.id, member.guild.id, exc_info=True)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if not self._scanning and config.get("stats.enabled", True):
            try:
                database.log_member_event(member.guild.id, member.id, "leave")
            except Exception:
                logger.error("Failed to log member remove: user=%s guild=%s", member.id, member.guild.id, exc_info=True)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        if config.get("stats.enabled", True):
            try:
                database.log_member_event(guild.id, user.id, "ban")
                logger.info("Member banned: user=%s guild=%s", user.id, guild.id)
            except Exception:
                logger.error("Failed to log member ban: user=%s guild=%s", user.id, guild.id, exc_info=True)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        if config.get("stats.enabled", True):
            try:
                database.log_member_event(guild.id, user.id, "unban")
                logger.info("Member unbanned: user=%s guild=%s", user.id, guild.id)
            except Exception:
                logger.error("Failed to log member unban: user=%s guild=%s", user.id, guild.id, exc_info=True)

    async def _resolve_message_author(self, payload: discord.RawReactionActionEvent) -> int | None:
        author_id = getattr(payload, "message_author_id", None)
        if author_id:
            return author_id
        cached = discord.utils.get(self.bot.cached_messages, id=payload.message_id)
        if cached:
            return cached.author.id
        try:
            channel = self.bot.get_channel(payload.channel_id)
            if channel:
                msg = await channel.fetch_message(payload.message_id)
                return msg.author.id
        except discord.NotFound:
            logger.debug("Message %s not found during author resolution", payload.message_id)
        except discord.Forbidden:
            logger.debug("No permission to fetch message %s in channel %s", payload.message_id, payload.channel_id)
        except Exception:
            logger.warning("Failed to resolve message author: msg=%s channel=%s", payload.message_id, payload.channel_id, exc_info=True)
        return None

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not config.get("stats.enabled", True) or not config.get("stats.track_reactions", True):
            return
        if payload.guild_id is None:
            return
        if payload.member and payload.member.bot and config.get("stats.exclude_bots", True):
            return
        try:
            author_id = await self._resolve_message_author(payload)
            database.increment_reaction(payload.guild_id, payload.user_id, author_id)
        except Exception:
            logger.error("Failed to log reaction add: guild=%s user=%s msg=%s", payload.guild_id, payload.user_id, payload.message_id, exc_info=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if not config.get("stats.enabled", True) or not config.get("stats.track_reactions", True):
            return
        if payload.guild_id is None:
            return
        try:
            author_id = await self._resolve_message_author(payload)
            database.decrement_reaction(payload.guild_id, payload.user_id, author_id)
        except Exception:
            logger.error("Failed to log reaction remove: guild=%s user=%s msg=%s", payload.guild_id, payload.user_id, payload.message_id, exc_info=True)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="stats", description="View server activity dashboard")
    @commands.guild_only()
    async def stats_cmd(self, ctx: commands.Context, days: int = 7):
        days = max(1, days)
        summary = database.get_server_stats_summary(ctx.guild.id, days)
        top_users = database.get_top_users(ctx.guild.id, days)
        top_channels = database.get_top_channels(ctx.guild.id, days)
        daily = database.get_daily_activity(ctx.guild.id, days)

        if summary["messages"] == 0 and summary["voice_seconds"] == 0:
            embed = discord.Embed(
                title="\U0001f4ca Server Stats",
                description="\U0001f4ca No stats data collected yet. The bot is now tracking activity — check back in a day!",
                color=COLOR_NEUTRAL,
            )
            await ctx.send(embed=embed)
            return

        voice_hours = summary["voice_seconds"] / 3600
        half_days = days // 2
        prev_summary = database.get_server_stats_summary(ctx.guild.id, half_days) if half_days > 0 else {"messages": 0}
        trend = _trend_indicator(summary["messages"], prev_summary["messages"])

        # Fetch expanded metrics
        word_stats = database.get_server_word_stats(ctx.guild.id, days)
        wk_split = database.get_weekday_weekend_split(ctx.guild.id, days)
        dau_wau_mau = database.get_dau_wau_mau(ctx.guild.id, days)
        velocity = database.get_message_velocity(ctx.guild.id, days)
        diversity = database.get_activity_diversity(ctx.guild.id, days)
        growth_trends = database.get_channel_growth_trends(ctx.guild.id, days)

        msgs_per_day = summary["messages"] / max(days, 1)
        wk_total = wk_split["weekday_msgs"] + wk_split["weekend_msgs"]
        if wk_total > 0:
            wk_pct = f"{wk_split['weekday_msgs'] * 100 // wk_total}% / {wk_split['weekend_msgs'] * 100 // wk_total}%"
        else:
            wk_pct = "N/A"

        # Embed 1: Overview (enhanced)
        e1 = discord.Embed(
            title=f"\U0001f4ca Server Stats — Last {days} Days",
            description=f"Your server had **{summary['messages']:,} messages** across **{summary['active_users']} active users** this period.",
            color=COLOR_NEUTRAL,
        )
        if ctx.guild.icon:
            e1.set_thumbnail(url=ctx.guild.icon.url)
        e1.add_field(name="\U0001f4ac Messages", value=f"{summary['messages']:,}", inline=True)
        e1.add_field(name="\U0001f3a4 Voice Hours", value=f"{voice_hours:.1f}h", inline=True)
        e1.add_field(name="\U0001f465 Active Users", value=str(summary["active_users"]), inline=True)
        e1.add_field(name="\U0001f4cc Active Channels", value=str(summary["active_channels"]), inline=True)
        e1.add_field(name="\U0001f504 Reactions", value=f"{summary['reactions']:,}", inline=True)
        e1.add_field(name="\U0001f4c8 Trend", value=trend, inline=True)
        e1.add_field(name="\U0001f4dd Avg Msg Length", value=f"{word_stats['avg_words_per_msg']:.1f} words", inline=True)
        e1.add_field(name="\U0001f4ca Msgs/Day Avg", value=f"{msgs_per_day:,.0f}/day", inline=True)
        e1.add_field(name="\U0001f4c5 Wkday/Wkend", value=wk_pct, inline=True)
        e1.set_footer(text=f"{days}-day window")

        # Embed 2: Top Users
        user_items = []
        for row in top_users:
            member = ctx.guild.get_member(row["user_id"])
            name = member.display_name if member else f"User {row['user_id']}"
            user_items.append((name, row["total"]))
        e2 = discord.Embed(title="\U0001f3c6 Most Active Users", description=_build_bar_chart(user_items), color=COLOR_NEUTRAL)

        # Embed 3: Top Channels
        chan_items = []
        for row in top_channels:
            ch = ctx.guild.get_channel(row["channel_id"])
            name = f"#{ch.name}" if ch else f"#{row['channel_id']}"
            chan_items.append((name, row["total"]))
        e3 = discord.Embed(title="\U0001f4cc Most Active Channels", description=_build_bar_chart(chan_items), color=COLOR_NEUTRAL)

        # Embed 4: Activity Chart
        dates = [r["date"][-5:] for r in daily]
        msgs = [r["messages"] for r in daily]
        voice = [r["voice"] for r in daily]
        chart_cfg = {
            "type": "line",
            "data": {
                "labels": dates,
                "datasets": [
                    {"label": "Messages", "data": msgs, "borderColor": "rgb(52,152,219)", "backgroundColor": "rgba(52,152,219,0.2)", "fill": True, "yAxisID": "y"},
                    {"label": "Voice (min)", "data": voice, "borderColor": "rgb(155,89,182)", "fill": False, "yAxisID": "y1"},
                ],
            },
            "options": {
                "scales": {
                    "yAxes": [
                        {"id": "y", "position": "left", "ticks": {"fontColor": CHART_TEXT}, "gridLines": {"color": CHART_GRID}},
                        {"id": "y1", "position": "right", "ticks": {"fontColor": CHART_TEXT}, "gridLines": {"drawOnChartArea": False}},
                    ],
                    "xAxes": [{"ticks": {"fontColor": CHART_TEXT}, "gridLines": {"color": CHART_GRID}}],
                },
                "legend": {"labels": {"fontColor": CHART_TEXT}},
            },
        }
        e4 = discord.Embed(title="\U0001f4c8 Daily Activity", color=COLOR_NEUTRAL)
        e4.set_image(url=await _chart_url(chart_cfg))

        # Embed 5: Server Health
        dau_mau_ratio = dau_wau_mau["dau_mau"]
        dau_mau_label = "healthy" if dau_mau_ratio >= 0.2 else "low"
        gini = diversity["gini"]
        gini_label = "well distributed" if gini < 0.5 else ("moderate" if gini < 0.7 else "concentrated")
        vel_trend = _trend_indicator(velocity["current_rate"], velocity["prior_rate"])

        e5 = discord.Embed(title="\U0001f3e5 Server Health Snapshot", color=COLOR_NEUTRAL)
        e5.add_field(
            name="\U0001f465 DAU / WAU / MAU",
            value=f"{dau_wau_mau['dau']} / {dau_wau_mau['wau']} / {dau_wau_mau['mau']}",
            inline=True,
        )
        e5.add_field(
            name="\U0001f4ca DAU/MAU Ratio",
            value=f"{dau_mau_ratio * 100:.0f}% ({dau_mau_label})",
            inline=True,
        )
        e5.add_field(
            name="⚡ Message Velocity",
            value=f"{velocity['current_rate']:.1f}/hr ({vel_trend})",
            inline=True,
        )
        e5.add_field(
            name="\U0001f4c8 Channel Diversity",
            value=f"{gini:.2f} Gini ({gini_label})",
            inline=True,
        )

        # Top growing channels
        growing = [c for c in growth_trends if c["change_pct"] > 0][:2]
        declining = [c for c in growth_trends if c["change_pct"] < 0][:2]
        if growing:
            grow_text = "  •  ".join(
                f"<#{c['channel_id']}> ↑ {c['change_pct']:.0f}%" for c in growing
            )
        else:
            grow_text = "None"
        if declining:
            dec_text = "  •  ".join(
                f"<#{c['channel_id']}> ↓ {abs(c['change_pct']):.0f}%" for c in declining
            )
        else:
            dec_text = "None"

        e5.add_field(name="\U0001f4cc Top Growing Channels", value=grow_text, inline=False)
        e5.add_field(name="\U0001f4c9 Top Declining Channels", value=dec_text, inline=False)
        e5.set_footer(text=f"Based on {days}-day analysis")

        await ctx.send(embeds=[e1, e2, e3, e4, e5])

    @commands.hybrid_command(name="userstats", description="View activity profile for a user")
    @commands.guild_only()
    async def userstats_cmd(self, ctx: commands.Context, member: discord.Member, days: int = 30):
        days = max(1, days)
        data = database.get_user_stats(ctx.guild.id, member.id, days)

        if data["message_count"] == 0 and data["voice_minutes"] == 0:
            embed = discord.Embed(
                title=f"\U0001f4ca Stats for {member.display_name}",
                description=f"No activity recorded for {member.display_name} in the last {days} days.",
                color=COLOR_NEUTRAL,
            )
            await ctx.send(embed=embed)
            return

        # Build a zero-filled date series for the full requested window.
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        all_dates = [
            (now_utc - datetime.timedelta(days=days - i)).strftime("%Y-%m-%d")
            for i in range(days + 1)
        ]
        daily_by_date = {d.get("date", ""): d.get("message_count", 0) for d in data["daily"]}
        full_daily_msgs = [daily_by_date.get(d, 0) for d in all_dates]

        # Determine actual data coverage for display.
        first_data_date = data["daily"][0]["date"] if data["daily"] else None
        if first_data_date:
            first_dt = datetime.datetime.strptime(first_data_date, "%Y-%m-%d")
            days_available = (now_utc.date() - first_dt.date()).days + 1
        else:
            days_available = 0
        actual_span = max(days_available, 1)

        avg_msgs = data["message_count"] / actual_span
        half = days // 2
        prev = database.get_user_stats(ctx.guild.id, member.id, half) if half > 0 else {"message_count": 0}
        color = _embed_color_for_trend(data["message_count"], prev["message_count"])

        top_ch = ctx.guild.get_channel(data["top_channel_id"]) if data["top_channel_id"] else None
        top_ch_name = f"#{top_ch.name}" if top_ch else "N/A"

        voice_sessions_raw = database.get_voice_leaderboard(ctx.guild.id, days, limit=100)
        user_voice = [r for r in voice_sessions_raw if r["user_id"] == member.id]
        avg_session = 0
        if user_voice and data["voice_minutes"] > 0:
            with database.get_conn() as conn:
                sess_count = conn.execute(
                    "SELECT COUNT(*) as c FROM voice_sessions WHERE guild_id = ? AND user_id = ? AND joined_at >= ? AND duration_seconds IS NOT NULL",
                    (ctx.guild.id, member.id, database._days_ago(days)),
                ).fetchone()["c"]
            avg_session = data["voice_minutes"] // max(sess_count, 1)

        data_range_note = (
            f"Data from {first_data_date} — run `!scan {days}` for full history"
            if first_data_date and days_available < days
            else f"{days}-day window"
        )

        # Fetch expanded user metrics
        u_words = database.get_user_word_stats(ctx.guild.id, member.id, days)
        u_streaks = database.get_user_streaks(ctx.guild.id, member.id, days)
        u_rank = database.get_user_rank(ctx.guild.id, member.id, days)
        u_hours = database.get_user_active_hours(ctx.guild.id, member.id, days)
        u_consistency = database.get_user_consistency(ctx.guild.id, member.id, days)
        u_wk = database.get_user_weekday_split(ctx.guild.id, member.id, days)
        u_dormancy = database.get_user_dormancy(ctx.guild.id, member.id)
        u_engagement = database.get_user_engagement_ratios(ctx.guild.id, member.id, days)

        # Embed 1: Profile (enhanced)
        e1 = discord.Embed(
            title=f"\U0001f4ca Stats for {member.display_name}",
            description=f"Activity summary for the last **{days} days**",
            color=color,
        )
        e1.set_thumbnail(url=member.display_avatar.url)
        e1.add_field(name="\U0001f4ac Messages Sent", value=f"{data['message_count']:,}", inline=True)
        e1.add_field(name="\U0001f3a4 Voice Time", value=_format_duration(data["voice_minutes"]), inline=True)
        e1.add_field(name="\U0001f504 Reactions Given", value=f"{data['reactions_given']:,}", inline=True)
        e1.add_field(name="\U0001f4cc Top Channel", value=top_ch_name, inline=True)
        e1.add_field(name="\U0001f4c5 Daily Average", value=f"{avg_msgs:.1f} msgs/day", inline=True)
        e1.add_field(name="\U0001f4c8 Trend", value=_trend_indicator(data["message_count"], prev["message_count"]), inline=True)
        e1.add_field(name="\U0001f504 Reactions Received", value=f"{data['reactions_received']:,}", inline=True)
        e1.add_field(name="⏱️ Avg Voice Session", value=_format_duration(avg_session), inline=True)
        e1.add_field(name="\U0001f4dd Avg Msg Length", value=f"{u_words['avg_words']:.1f} words", inline=True)
        e1.add_field(name="\U0001f3c6 Server Rank", value=f"#{u_rank['msg_rank']} of {u_rank['total_users']}", inline=True)
        e1.add_field(name="\U0001f525 Current Streak", value=f"{u_streaks['current']} days", inline=True)
        e1.add_field(name="\U0001f4ca Longest Streak", value=f"{u_streaks['longest']} days", inline=True)
        e1.set_footer(text=data_range_note)

        # Embed 2: Sparkline + chart — use zero-filled series so gaps show as 0 bars
        chart_dates = all_dates[-14:]
        vals = full_daily_msgs[-14:]
        spark = _sparkline(full_daily_msgs)
        peak_val = max(full_daily_msgs) if full_daily_msgs else 0
        quiet_val = min(d for d in full_daily_msgs if d > 0) if any(full_daily_msgs) else 0
        desc = f"```\nMessages per day (last 14 days):\n{spark}\nAvg: {avg_msgs:.1f}/day | Peak: {peak_val} | Quiet: {quiet_val}\n```"

        dates = [d[-5:] for d in chart_dates]
        chart_cfg = {
            "type": "bar",
            "data": {"labels": dates, "datasets": [{"label": "Messages", "data": vals, "backgroundColor": "rgba(52,152,219,0.7)"}]},
            "options": {"legend": {"labels": {"fontColor": CHART_TEXT}}, "scales": {"yAxes": [{"ticks": {"fontColor": CHART_TEXT, "beginAtZero": True}, "gridLines": {"color": CHART_GRID}}], "xAxes": [{"ticks": {"fontColor": CHART_TEXT}, "gridLines": {"color": CHART_GRID}}]}},
        }
        e2 = discord.Embed(title="\U0001f4c8 Daily Activity", description=desc, color=color)
        e2.set_image(url=await _chart_url(chart_cfg))

        # Embed 3: Channel breakdown
        total_msgs = sum(v for _, v in data["channel_breakdown"])
        chan_items = []
        other_count = 0
        for i, (ch_id, count) in enumerate(data["channel_breakdown"]):
            if i < 5:
                ch = ctx.guild.get_channel(ch_id)
                name = f"#{ch.name}" if ch else f"#{ch_id}"
                pct = (count / total_msgs * 100) if total_msgs else 0
                chan_items.append((f"{name} ({pct:.0f}%)", count))
            else:
                other_count += count
        if other_count > 0:
            pct = (other_count / total_msgs * 100) if total_msgs else 0
            chan_items.append((f"other ({pct:.0f}%)", other_count))
        e3 = discord.Embed(title="\U0001f4cc Channel Activity", description=_build_bar_chart(chan_items), color=color)

        # Embed 4: Activity Profile
        # Determine most active hour
        peak_hour_entry = max(u_hours, key=lambda h: h["count"]) if u_hours else {"hour": 0, "count": 0}
        peak_hour_str = _utc_hour_to_et(peak_hour_entry["hour"]) if peak_hour_entry["count"] > 0 else "N/A"

        # Weekday/weekend split
        u_wk_total = u_wk["weekday"] + u_wk["weekend"]
        if u_wk_total > 0:
            u_wk_pct = f"{u_wk['weekday'] * 100 // u_wk_total}% / {u_wk['weekend'] * 100 // u_wk_total}%"
        else:
            u_wk_pct = "N/A"

        # Consistency label
        c_score = u_consistency["score"]
        if c_score >= 80:
            c_label = "very consistent"
        elif c_score >= 60:
            c_label = "steady contributor"
        elif c_score >= 40:
            c_label = "moderate"
        elif c_score >= 20:
            c_label = "sporadic"
        else:
            c_label = "irregular"

        # Dormancy
        dorm_days = u_dormancy["days_since_last"]
        dorm_text = "active today" if dorm_days == 0 else f"{dorm_days} day{'s' if dorm_days != 1 else ''}"

        # Active day percentage
        active_pct = (u_streaks["active_days"] / max(u_streaks["total_days"], 1)) * 100

        e4 = discord.Embed(title="\U0001f4ca Activity Profile", color=COLOR_NEUTRAL)
        e4.add_field(name="⏰ Most Active Hour", value=peak_hour_str, inline=True)
        e4.add_field(name="\U0001f4c5 Weekday / Weekend", value=u_wk_pct, inline=True)
        e4.add_field(name="\U0001f3af Consistency Score", value=f"{c_score:.0f}/100 ({c_label})", inline=True)
        e4.add_field(name="\U0001f4a4 Days Since Last Msg", value=dorm_text, inline=True)
        e4.add_field(name="❤️ Engagement Ratio", value=f"{u_engagement['reaction_per_msg']:.2f} reactions/msg sent", inline=True)
        e4.add_field(name="\U0001f4c8 Active Day %", value=f"{active_pct:.0f}% of days since join", inline=True)

        # Hourly heatmap bar
        heatmap = _build_heatmap_bar(u_hours)
        e4.add_field(
            name="⏰ Hourly Activity",
            value=f"```\n{heatmap}\n00    06    12    18    23\n```",
            inline=False,
        )
        e4.set_footer(text=f"{days}-day window")

        await ctx.send(embeds=[e1, e2, e3, e4])

    @commands.hybrid_command(name="channelstats", description="View activity report for a channel")
    @commands.guild_only()
    async def channelstats_cmd(self, ctx: commands.Context, channel: discord.TextChannel, days: int = 30):
        days = max(1, days)
        data = database.get_channel_stats(ctx.guild.id, channel.id, days)

        if data["message_count"] == 0:
            embed = discord.Embed(
                title=f"\U0001f4cc Stats for #{channel.name}",
                description=f"No activity recorded for #{channel.name} in the last {days} days.",
                color=COLOR_NEUTRAL,
            )
            await ctx.send(embed=embed)
            return

        avg_msgs = data["message_count"] / days if days else 0
        half = days // 2
        prev = database.get_channel_stats(ctx.guild.id, channel.id, half) if half > 0 else {"message_count": 0}
        color = _embed_color_for_trend(data["message_count"], prev["message_count"])
        peak_hour = _utc_hour_to_et(data["peak_hour"]) if data["peak_hour"] is not None else "N/A"

        daily_msgs = [d.get("message_count", 0) for d in data["daily"]]
        busiest_day = "N/A"
        if data["daily"]:
            best = max(data["daily"], key=lambda d: d.get("message_count", 0))
            try:
                dt = datetime.datetime.strptime(best["date"], "%Y-%m-%d")
                busiest_day = dt.strftime("%A")
            except (ValueError, KeyError):
                pass

        # Fetch expanded channel metrics
        ch_words = database.get_channel_word_stats(ctx.guild.id, channel.id, days)
        ch_density = database.get_channel_density(ctx.guild.id, channel.id, days)
        ch_concentration = database.get_channel_user_concentration(ctx.guild.id, channel.id, days)
        ch_heatmap = database.get_channel_hourly_heatmap(ctx.guild.id, channel.id, days)
        ch_wk = database.get_channel_weekday_split(ctx.guild.id, channel.id, days)
        ch_growth = database.get_channel_growth(ctx.guild.id, channel.id, days)

        # Embed 1: Overview (enhanced)
        e1 = discord.Embed(
            title=f"\U0001f4cc Stats for #{channel.name}",
            description=f"Activity summary for the last **{days} days**",
            color=color,
        )
        if ctx.guild.icon:
            e1.set_thumbnail(url=ctx.guild.icon.url)
        e1.add_field(name="\U0001f4ac Total Messages", value=f"{data['message_count']:,}", inline=True)
        e1.add_field(name="\U0001f465 Unique Users", value=str(data["unique_users"]), inline=True)
        e1.add_field(name="\U0001f4c5 Daily Average", value=f"{avg_msgs:.1f} msgs/day", inline=True)
        e1.add_field(name="\U0001f550 Peak Hour", value=peak_hour, inline=True)
        e1.add_field(name="\U0001f4c8 Trend", value=_trend_indicator(data["message_count"], prev["message_count"]), inline=True)
        e1.add_field(name="\U0001f525 Busiest Day", value=busiest_day, inline=True)
        e1.add_field(name="\U0001f4dd Avg Words/Msg", value=f"{ch_words['avg_words']:.1f} words", inline=True)
        e1.add_field(name="\U0001f4ac Msgs/User", value=f"{ch_density['msgs_per_user']:.1f} msgs/user", inline=True)
        e1.add_field(name="\U0001f4ca Top-3 Share", value=f"{ch_concentration['top3_share']:.0f}%", inline=True)

        # Embed 2: Top contributors
        total_chan = sum(v for _, v in data["top_users"])
        user_items = []
        remaining = 0
        for i, (uid, count) in enumerate(data["top_users"]):
            if i < 5:
                m = ctx.guild.get_member(uid)
                name = m.display_name if m else f"User {uid}"
                pct = (count / total_chan * 100) if total_chan else 0
                user_items.append((f"{name} ({pct:.0f}%)", count))
            else:
                remaining += count
        e2 = discord.Embed(title="\U0001f3c6 Top Contributors", description=_build_bar_chart(user_items), color=color)
        if remaining > 0:
            others = len(data["top_users"]) - 5
            e2.set_footer(text=f"{others} other user(s) contributed {remaining:,} messages")

        # Embed 3: Activity trend chart
        dates = [d.get("date", "")[-5:] for d in data["daily"]]
        vals = daily_msgs
        chart_cfg = {
            "type": "bar",
            "data": {"labels": dates, "datasets": [{"label": "Messages", "data": vals, "backgroundColor": "rgba(52,152,219,0.7)"}]},
            "options": {"legend": {"labels": {"fontColor": CHART_TEXT}}, "scales": {"yAxes": [{"ticks": {"fontColor": CHART_TEXT, "beginAtZero": True}, "gridLines": {"color": CHART_GRID}}], "xAxes": [{"ticks": {"fontColor": CHART_TEXT}, "gridLines": {"color": CHART_GRID}}]}},
        }
        e3 = discord.Embed(title="\U0001f4c8 Daily Message Volume", color=color)
        e3.set_image(url=await _chart_url(chart_cfg))

        # Embed 4: Channel Profile
        ch_wk_total = ch_wk["weekday"] + ch_wk["weekend"]
        if ch_wk_total > 0:
            ch_wk_pct = f"{ch_wk['weekday'] * 100 // ch_wk_total}% / {ch_wk['weekend'] * 100 // ch_wk_total}%"
        else:
            ch_wk_pct = "N/A"

        ch_gini = ch_concentration["gini"]
        gini_label = "well distributed" if ch_gini < 0.4 else ("moderately diverse" if ch_gini < 0.6 else "concentrated")
        ch_growth_text = _trend_indicator(ch_growth["current"], ch_growth["previous"])

        e4 = discord.Embed(title="\U0001f4ca Channel Profile", color=COLOR_NEUTRAL)
        e4.add_field(name="\U0001f4c5 Weekday / Weekend", value=ch_wk_pct, inline=True)
        e4.add_field(name="\U0001f4c8 Growth vs Prior", value=ch_growth_text, inline=True)
        e4.add_field(name="\U0001f4ca User Concentration", value=f"{ch_gini:.2f} Gini ({gini_label})", inline=True)

        ch_heatmap_bar = _build_heatmap_bar(ch_heatmap)
        e4.add_field(
            name="⏰ Hourly Activity",
            value=f"```\n{ch_heatmap_bar}\n00    06    12    18    23\n```",
            inline=False,
        )
        e4.set_footer(text=f"{days}-day window")

        await ctx.send(embeds=[e1, e2, e3, e4])

    @commands.hybrid_command(name="voicestats", description="View voice activity dashboard")
    @commands.guild_only()
    async def voicestats_cmd(self, ctx: commands.Context, days: int = 7):
        days = max(1, days)
        voice_users = database.get_voice_leaderboard(ctx.guild.id, days)
        voice_channels = database.get_voice_channel_stats(ctx.guild.id, days)
        daily = database.get_daily_activity(ctx.guild.id, days)

        total_seconds = sum(r["total"] for r in voice_channels) if voice_channels else 0
        total_minutes = total_seconds // 60
        unique_users = len(voice_users)

        if total_seconds == 0:
            embed = discord.Embed(
                title="\U0001f3a4 Voice Stats",
                description="\U0001f3a4 No voice data collected yet. Join a voice channel to start tracking!",
                color=COLOR_VOICE,
            )
            await ctx.send(embed=embed)
            return

        with database.get_conn() as conn:
            sess_count = conn.execute(
                "SELECT COUNT(*) as c FROM voice_sessions WHERE guild_id = ? AND joined_at >= ? AND duration_seconds IS NOT NULL",
                (ctx.guild.id, database._days_ago(days)),
            ).fetchone()["c"]
        avg_session = total_minutes // max(sess_count, 1)

        currently_in = sum(len(vc.members) for vc in ctx.guild.voice_channels)

        # Fetch expanded voice metrics
        v_dist = database.get_voice_session_distribution(ctx.guild.id, days)
        v_dow = database.get_voice_day_of_week(ctx.guild.id, days)
        v_peak = database.get_voice_peak_hours(ctx.guild.id, days)

        # Determine busiest day and peak hour from expanded data
        busiest_dow = max(v_dow, key=lambda d: d["sessions"]) if v_dow else {"day": 0, "sessions": 0}
        busiest_day_name = _day_name(busiest_dow["day"]) if busiest_dow["sessions"] > 0 else "N/A"
        peak_voice_hour = max(v_peak, key=lambda h: h["sessions"]) if v_peak else {"hour": 0, "sessions": 0}
        peak_hour_str = _utc_hour_to_et(peak_voice_hour["hour"]) if peak_voice_hour["sessions"] > 0 else "N/A"

        # Embed 1: Overview (enhanced)
        e1 = discord.Embed(
            title=f"\U0001f3a4 Voice Stats — Last {days} Days",
            description=f"**{_format_seconds(total_seconds)}** of voice activity across **{unique_users}** users.",
            color=COLOR_VOICE,
        )
        if ctx.guild.icon:
            e1.set_thumbnail(url=ctx.guild.icon.url)
        e1.add_field(name="⏱️ Total Time", value=_format_seconds(total_seconds), inline=True)
        e1.add_field(name="\U0001f465 Unique Users", value=str(unique_users), inline=True)
        e1.add_field(name="\U0001f4ca Sessions", value=f"{sess_count:,}", inline=True)
        e1.add_field(name="⏱️ Avg Session", value=_format_duration(avg_session), inline=True)
        e1.add_field(name="⏱️ Median Session", value=_format_duration(v_dist["median"]), inline=True)
        e1.add_field(name="\U0001f3c6 Longest Session", value=_format_duration(v_dist["max"]), inline=True)
        e1.add_field(name="\U0001f7e2 Currently In", value=str(currently_in), inline=True)
        e1.add_field(name="\U0001f525 Peak Hour", value=peak_hour_str, inline=True)
        e1.add_field(name="\U0001f4c5 Busiest Day", value=busiest_day_name, inline=True)

        # Embed 2: User leaderboard
        user_items = []
        for row in voice_users:
            m = ctx.guild.get_member(row["user_id"])
            name = m.display_name if m else f"User {row['user_id']}"
            user_items.append((name, row["total"]))
        user_chart = _build_bar_chart([(n, v) for n, v in user_items[:5]])
        lines = user_chart.strip("`\n").split("\n")
        formatted_lines = []
        for i, (name, mins) in enumerate(user_items[:5]):
            formatted_lines.append(f"#{i+1:<2} {name:<16}  {_format_duration(mins)}")
        e2 = discord.Embed(
            title="\U0001f3c6 Voice Leaderboard",
            description="```\n" + "\n".join(formatted_lines) + "\n```" if formatted_lines else "No data",
            color=COLOR_VOICE,
        )

        # Embed 3: Channel usage
        chan_items = []
        for row in voice_channels:
            ch = ctx.guild.get_channel(row["channel_id"])
            name = f"\U0001f50a {ch.name}" if ch else f"\U0001f50a {row['channel_id']}"
            chan_items.append((name, row["total"] // 60))
        formatted_ch = []
        for i, (name, mins) in enumerate(chan_items[:5]):
            formatted_ch.append(f"#{i+1:<2} {name:<20}  {_format_duration(mins)}")
        e3 = discord.Embed(
            title="\U0001f4cc Channel Usage",
            description="```\n" + "\n".join(formatted_ch) + "\n```" if formatted_ch else "No data",
            color=COLOR_VOICE,
        )

        # Embed 4: Daily voice trend
        dates = [r["date"][-5:] for r in daily]
        voice_mins = [r["voice"] for r in daily]
        chart_cfg = {
            "type": "line",
            "data": {"labels": dates, "datasets": [{"label": "Voice (min)", "data": voice_mins, "borderColor": "rgb(155,89,182)", "backgroundColor": "rgba(155,89,182,0.2)", "fill": True}]},
            "options": {"legend": {"labels": {"fontColor": CHART_TEXT}}, "scales": {"yAxes": [{"ticks": {"fontColor": CHART_TEXT, "beginAtZero": True}, "gridLines": {"color": CHART_GRID}}], "xAxes": [{"ticks": {"fontColor": CHART_TEXT}, "gridLines": {"color": CHART_GRID}}]}},
        }
        e4 = discord.Embed(title="\U0001f4c8 Daily Voice Hours", color=COLOR_VOICE)
        e4.set_image(url=await _chart_url(chart_cfg))

        # Embed 5: Session Analysis
        buckets = v_dist.get("buckets", {})
        bucket_labels = ["<5m", "5-15m", "15-30m", "30-60m", "1-2h", "2h+"]
        bucket_keys = ["under_5", "5_15", "15_30", "30_60", "60_120", "over_120"]
        bucket_counts = [buckets.get(k, 0) for k in bucket_keys]
        total_sessions = sum(bucket_counts) or 1

        # Build histogram lines
        max_bucket = max(bucket_counts) if bucket_counts else 1
        hist_lines = []
        for label, count in zip(bucket_labels, bucket_counts):
            bar_len = int((count / max_bucket) * 20) if max_bucket else 0
            bar = "█" * bar_len + "░" * (20 - bar_len)
            pct = count * 100 // total_sessions
            hist_lines.append(f"  {label:<6} {bar} {count:>3} ({pct}%)")

        # Day-of-week session bars
        dow_bars = []
        dow_max = max((d["sessions"] for d in v_dow), default=1) or 1
        blocks = "░▁▂▃▄▅▆▇█"
        for d in v_dow:
            idx = min(int(d["sessions"] / dow_max * 8), 8)
            dow_bars.append(f"{_day_name(d['day'])} {blocks[idx]}")

        desc = (
            "**Session Length Distribution:**\n```\n"
            + "\n".join(hist_lines)
            + "\n```\n**Sessions by Day of Week:**\n```\n  "
            + "  ".join(dow_bars)
            + "\n```"
        )
        e5 = discord.Embed(title="\U0001f4ca Session Analysis", description=desc, color=COLOR_VOICE)
        e5.set_footer(text=f"{days}-day window")

        await ctx.send(embeds=[e1, e2, e3, e4, e5])

    @commands.hybrid_command(name="growth", description="View member growth trends")
    @commands.guild_only()
    async def growth_cmd(self, ctx: commands.Context, days: int = 30):
        days = max(1, days)
        snapshots = database.get_member_growth(ctx.guild.id, days)
        events = database.get_member_events_summary(ctx.guild.id, days)

        if not snapshots:
            embed = discord.Embed(
                title="\U0001f465 Member Growth",
                description="\U0001f465 No growth data yet — member snapshots begin after the first hour of tracking.",
                color=COLOR_NEUTRAL,
            )
            await ctx.send(embed=embed)
            return

        current = ctx.guild.member_count or 0
        earliest = snapshots[0]["total_members"]
        net = current - earliest
        pct = (net / earliest * 100) if earliest else 0
        color = _embed_color_for_trend(current, earliest)
        joins = events["joins"]
        leaves = events["leaves"]
        retention = ((joins - leaves) / joins * 100) if joins > 0 else 0

        # Fetch expanded growth metrics
        churn = database.get_churn_metrics(ctx.guild.id, days)
        join_dow = database.get_join_day_distribution(ctx.guild.id, days)

        # Average member tenure from current guild members' join dates
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        tenures = []
        for m in ctx.guild.members:
            if m.joined_at:
                tenures.append((now_utc - m.joined_at).days)
        avg_tenure = sum(tenures) // len(tenures) if tenures else 0

        # Embed 1: Summary (enhanced)
        e1 = discord.Embed(
            title=f"\U0001f465 Member Growth — Last {days} Days",
            description=f"Your server {'grew' if net >= 0 else 'shrank'} by **{'+' if net >= 0 else ''}{net} members** ({pct:+.1f}%).",
            color=color,
        )
        if ctx.guild.icon:
            e1.set_thumbnail(url=ctx.guild.icon.url)
        e1.add_field(name="\U0001f465 Current", value=f"{current:,}", inline=True)
        e1.add_field(name=f"\U0001f465 {days}d Ago", value=f"{earliest:,}", inline=True)
        e1.add_field(name="\U0001f4c8 Net Change", value=f"{'+' if net >= 0 else ''}{net} ({pct:+.1f}%)", inline=True)
        e1.add_field(name="✅ Joins", value=str(joins), inline=True)
        e1.add_field(name="❌ Leaves", value=str(leaves), inline=True)
        e1.add_field(name="\U0001f4ca Retention", value=f"{retention:.1f}%", inline=True)
        e1.add_field(name="\U0001f4c9 Churn Rate", value=f"{churn['churn_rate']:.1f}%", inline=True)
        e1.add_field(name="\U0001f528 Ban Rate", value=f"{churn['ban_rate']:.1f}% of leaves", inline=True)
        e1.add_field(name="\U0001f465 Avg Tenure", value=f"{avg_tenure} days", inline=True)

        # Embed 2: Growth chart
        dates = [r["recorded_at"][:10][-5:] for r in snapshots]
        members = [r["total_members"] for r in snapshots]
        # Thin out data points for chart readability
        step = max(1, len(dates) // 30)
        chart_dates = dates[::step]
        chart_members = members[::step]
        min_y = min(chart_members) - 5 if chart_members else 0
        chart_cfg = {
            "type": "line",
            "data": {"labels": chart_dates, "datasets": [{"label": "Members", "data": chart_members, "borderColor": "rgb(46,204,113)", "backgroundColor": "rgba(46,204,113,0.2)", "fill": True}]},
            "options": {"legend": {"labels": {"fontColor": CHART_TEXT}}, "scales": {"yAxes": [{"ticks": {"fontColor": CHART_TEXT, "min": min_y}, "gridLines": {"color": CHART_GRID}}], "xAxes": [{"ticks": {"fontColor": CHART_TEXT}, "gridLines": {"color": CHART_GRID}}]}},
        }
        e2 = discord.Embed(title="\U0001f4c8 Member Count Over Time", color=color)
        e2.set_image(url=await _chart_url(chart_cfg))

        # Embed 3: Daily breakdown table
        table_lines = ["Date          Joins  Leaves  Net", "─" * 38]
        for row in events["daily"]:
            d = row["date"]
            j = row["joins"]
            lv = row["leaves"]
            n = j - lv
            sign = "+" if n >= 0 else ""
            table_lines.append(f"{d}    {j:>3}     {lv:>3}   {sign}{n:>3}")
        total_net = joins - leaves
        table_lines.append("─" * 38)
        table_lines.append(f"{'Total':<14}{joins:>3}     {leaves:>3}   {'+' if total_net >= 0 else ''}{total_net:>3}")
        e3 = discord.Embed(
            title="\U0001f4c5 Daily Activity (Last 7 Days)",
            description="```\n" + "\n".join(table_lines) + "\n```",
            color=color,
        )

        # Embed 4: Joins vs Leaves chart
        evt_dates = [r["date"][-5:] for r in events["daily"]]
        evt_joins = [r["joins"] for r in events["daily"]]
        evt_leaves = [r["leaves"] for r in events["daily"]]
        chart_cfg2 = {
            "type": "bar",
            "data": {"labels": evt_dates, "datasets": [
                {"label": "Joins", "data": evt_joins, "backgroundColor": "rgba(46,204,113,0.7)"},
                {"label": "Leaves", "data": evt_leaves, "backgroundColor": "rgba(231,76,60,0.7)"},
            ]},
            "options": {"legend": {"labels": {"fontColor": CHART_TEXT}}, "scales": {"yAxes": [{"ticks": {"fontColor": CHART_TEXT, "beginAtZero": True}, "gridLines": {"color": CHART_GRID}}], "xAxes": [{"ticks": {"fontColor": CHART_TEXT}, "gridLines": {"color": CHART_GRID}}]}},
        }
        e4 = discord.Embed(title="\U0001f4ca Joins vs Leaves", color=color)
        e4.set_image(url=await _chart_url(chart_cfg2))

        # Embed 5: Member Lifecycle
        # Busiest join day
        busiest_join = max(join_dow, key=lambda d: d["count"]) if join_dow else {"day": 0, "count": 0}
        busiest_join_name = _day_name(busiest_join["day"]) if busiest_join["count"] > 0 else "N/A"

        # New accounts (created < 7 days before joining) — from live guild data
        new_accounts = sum(
            1 for m in ctx.guild.members
            if m.joined_at and m.created_at
            and (m.joined_at - m.created_at).days < 7
        )

        # Online ratio
        online_count = sum(
            1 for m in ctx.guild.members
            if m.status != discord.Status.offline
        )
        online_ratio = (online_count / current * 100) if current else 0

        # Joins by day-of-week bars
        blocks = "░▁▂▃▄▅▆▇█"
        dow_max = max((d["count"] for d in join_dow), default=1) or 1
        dow_bars = []
        for d in join_dow:
            idx = min(int(d["count"] / dow_max * 8), 8)
            dow_bars.append(f"{_day_name(d['day'])} {blocks[idx]}")

        e5 = discord.Embed(title="\U0001f4ca Member Lifecycle", color=COLOR_NEUTRAL)
        e5.add_field(name="\U0001f4c5 Busiest Join Day", value=busiest_join_name, inline=True)
        e5.add_field(name="\U0001f476 New Accounts (<7d)", value=f"{new_accounts} members", inline=True)
        e5.add_field(name="\U0001f4ca Online Ratio (avg)", value=f"{online_ratio:.0f}% of members online", inline=True)
        e5.add_field(
            name="Joins by Day of Week",
            value="```\n  " + "  ".join(dow_bars) + "\n```",
            inline=False,
        )
        e5.set_footer(text=f"{days}-day window")

        await ctx.send(embeds=[e1, e2, e3, e4, e5])

    @commands.hybrid_command(name="peakhours", description="Show full message activity distribution by hour (UTC)")
    @commands.guild_only()
    async def peakhours_cmd(self, ctx: commands.Context, days: int = 30):
        """Show all 24 hours ranked by message count so you can verify peak-hour data."""
        days = max(1, days)
        rows = database.get_peak_hours(ctx.guild.id, days)
        if not rows:
            await ctx.send("No message data for that period.")
            return

        total = sum(r["count"] for r in rows)
        by_hour = {r["hour"]: r["count"] for r in rows}

        # Fetch expanded peakhours metrics
        wk_hours = database.get_hourly_weekday_weekend(ctx.guild.id, days)
        per_channel = database.get_per_channel_peak_hours(ctx.guild.id, days, limit=5)

        # Determine overall peak and quietest
        peak_h = max(range(24), key=lambda h: by_hour.get(h, 0))
        quiet_h = min(range(24), key=lambda h: by_hour.get(h, 0))

        # Weekday and weekend peaks
        wk_by_hour = {e["hour"]: e["count"] for e in wk_hours.get("weekday", [])}
        we_by_hour = {e["hour"]: e["count"] for e in wk_hours.get("weekend", [])}
        wk_peak = max(range(24), key=lambda h: wk_by_hour.get(h, 0)) if wk_by_hour else 0
        we_peak = max(range(24), key=lambda h: we_by_hour.get(h, 0)) if we_by_hour else 0

        # Volume change vs prior period
        half = days // 2
        prev_rows = database.get_peak_hours(ctx.guild.id, half) if half > 0 else []
        prev_total = sum(r["count"] for r in prev_rows) if prev_rows else 0
        vol_trend = _trend_indicator(total, prev_total)

        lines = []
        for h in range(24):
            count = by_hour.get(h, 0)
            pct = (count / total * 100) if total else 0
            bar = "█" * int(pct / 2)
            et_label = _utc_hour_to_et(h)
            lines.append(f"{et_label:<11}  {bar:<50} {count:>5} ({pct:4.1f}%)")

        # Embed 1: Hourly Distribution (enhanced)
        e1 = discord.Embed(
            title=f"Peak Hours — Last {days} Days (Eastern Time)",
            description=f"```\n{''.join(f'{l}{chr(10)}' for l in lines)}```",
            color=COLOR_NEUTRAL,
        )
        e1.add_field(name="\U0001f4ca Peak Hour", value=_utc_hour_to_et(peak_h), inline=True)
        e1.add_field(name="\U0001f4c9 Quietest Hour", value=_utc_hour_to_et(quiet_h), inline=True)
        e1.add_field(name="\U0001f4c8 Volume Change", value=vol_trend, inline=True)
        e1.add_field(name="\U0001f4c5 Weekday Peak", value=_utc_hour_to_et(wk_peak), inline=True)
        e1.add_field(name="\U0001f4c5 Weekend Peak", value=_utc_hour_to_et(we_peak), inline=True)
        e1.set_footer(text=f"Total messages in window: {total:,}")

        # Embed 2: Channel Breakdown
        chan_lines = []
        if per_channel:
            ch_max = max(c["total_msgs"] for c in per_channel) or 1
            for c in per_channel:
                ch = ctx.guild.get_channel(c["channel_id"])
                ch_name = f"#{ch.name}" if ch else f"#{c['channel_id']}"
                bar_len = int((c["total_msgs"] / ch_max) * 22)
                bar = "█" * bar_len + "░" * (22 - bar_len)
                peak_str = _utc_hour_to_et(c["peak_hour"])
                chan_lines.append(f"  {ch_name:<14} {bar}  Peak: {peak_str}")

        # Weekday vs weekend heatmap comparison
        wk_heatmap = _build_heatmap_bar(wk_hours.get("weekday", []))
        we_heatmap = _build_heatmap_bar(wk_hours.get("weekend", []))

        desc_parts = []
        if chan_lines:
            desc_parts.append("**Top 5 Channels by Activity:**\n```\n" + "\n".join(chan_lines) + "\n```")
        desc_parts.append(
            f"**Weekday vs Weekend:**\n```\nWkday {wk_heatmap}\nWkend {we_heatmap}\n00    06    12    18    23\n```"
        )

        e2 = discord.Embed(title="\U0001f4ca Peak Hours by Channel", description="\n".join(desc_parts), color=COLOR_NEUTRAL)
        e2.set_footer(text=f"{days}-day window")

        await ctx.send(embeds=[e1, e2])

    # ------------------------------------------------------------------
    # New commands
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="serverpulse", description="Quick server health check (admin only)")
    @commands.guild_only()
    @has_admin_role()
    async def serverpulse_cmd(self, ctx: commands.Context):
        """Single-embed snapshot of server health combining the most important metrics."""
        guild = ctx.guild
        gid = guild.id

        # Core data
        summary_7d = database.get_server_stats_summary(gid, 7)
        summary_1d = database.get_server_stats_summary(gid, 1)
        dau_wau_mau = database.get_dau_wau_mau(gid, 30)
        word_stats = database.get_server_word_stats(gid, 7)
        velocity = database.get_message_velocity(gid, 7)
        churn = database.get_churn_metrics(gid, 30)

        avg_msgs_7d = summary_7d["messages"] / 7 if summary_7d["messages"] else 0
        msgs_today = summary_1d["messages"]

        # Voice users currently in VC
        voice_now = sum(len(vc.members) for vc in guild.voice_channels)
        # Average voice users per day (approximate from total voice seconds)
        voice_users_7d = summary_7d.get("active_users", 0)

        # Member change today
        snap_today = database.get_member_growth(gid, 1)
        if snap_today and len(snap_today) >= 2:
            member_change = snap_today[-1]["total_members"] - snap_today[0]["total_members"]
        else:
            member_change = 0

        # Top channel today
        top_channels_1d = database.get_top_channels(gid, 1)
        if top_channels_1d:
            top_ch = guild.get_channel(top_channels_1d[0]["channel_id"])
            top_ch_name = f"#{top_ch.name}" if top_ch else "N/A"
            top_ch_msgs = top_channels_1d[0]["total"]
        else:
            top_ch_name = "N/A"
            top_ch_msgs = 0

        # Online members
        online = sum(1 for m in guild.members if m.status != discord.Status.offline)
        total_members = guild.member_count or 1

        # Engagement metrics
        reaction_per_msg = (summary_7d["reactions"] / summary_7d["messages"]) if summary_7d["messages"] else 0
        voice_text_ratio = (summary_7d["voice_seconds"] / 3600) / max(summary_7d["messages"] / 100, 1) if summary_7d["messages"] else 0

        # Health score
        health = _composite_health_score({
            "dau_mau": dau_wau_mau["dau_mau"],
            "reaction_per_msg": reaction_per_msg,
            "churn_rate": churn["churn_rate"] / 100 if churn["churn_rate"] else 0,
            "voice_rate": voice_now / total_members if total_members else 0,
            "net_growth": member_change,
        })
        label = _health_label(health)

        # Color based on health
        if health >= 61:
            color = COLOR_POSITIVE
        elif health >= 41:
            color = COLOR_NEUTRAL
        else:
            color = COLOR_NEGATIVE

        embed = discord.Embed(title="\U0001f493 Server Pulse", color=color)
        embed.add_field(name="\U0001f3e5 Health Score", value=f"**{health}/100** ({label})", inline=False)

        # Today vs 7d Avg
        today_vs = (
            f"\U0001f4ac Messages: {msgs_today} vs {avg_msgs_7d:.0f}/day avg "
            f"({_trend_indicator(msgs_today, avg_msgs_7d)})\n"
            f"\U0001f3a4 Voice Users: {voice_now} vs {voice_users_7d} avg\n"
            f"\U0001f465 Member Change: {'+' if member_change >= 0 else ''}{member_change} today"
        )
        embed.add_field(name="\U0001f4ca Today vs 7d Avg", value=today_vs, inline=False)

        # Right Now
        right_now = (
            f"\U0001f4cc Top Channel: {top_ch_name} ({top_ch_msgs} msgs today)\n"
            f"\U0001f3a4 In Voice: {voice_now} members\n"
            f"\U0001f7e2 Online: {online} / {total_members} members"
        )
        embed.add_field(name="\U0001f525 Right Now", value=right_now, inline=False)

        # Engagement
        engagement = (
            f"DAU/MAU: {dau_wau_mau['dau_mau'] * 100:.0f}%  •  "
            f"Avg Msg Length: {word_stats['avg_words_per_msg']:.1f} words\n"
            f"Reactions/Msg: {reaction_per_msg:.2f}  •  "
            f"Voice/Text Ratio: {voice_text_ratio:.1f}"
        )
        embed.add_field(name="\U0001f4ca Engagement", value=engagement, inline=False)

        embed.set_footer(text=f"Snapshot at {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="leaderboard", description="Multi-category leaderboard")
    @commands.guild_only()
    async def leaderboard_cmd(self, ctx: commands.Context, days: int = 7, category: str = "messages"):
        """Show top 10 users in a selected category."""
        days = max(1, days)
        valid_categories = ("messages", "voice", "streaks", "engagement", "social")
        category = category.lower()
        if category not in valid_categories:
            await ctx.send(f"Invalid category. Choose from: {', '.join(valid_categories)}")
            return

        entries = database.get_leaderboard(ctx.guild.id, days, category, limit=10)
        if not entries:
            await ctx.send(f"No data for **{category}** in the last {days} days.")
            return

        # Format value based on category
        def fmt_value(val, cat):
            if cat == "voice":
                return _format_duration(int(val))
            if cat == "engagement":
                return f"{val:.2f}"
            if cat == "streaks":
                return f"{int(val)}d"
            return f"{int(val):,}"

        # Build bar chart
        max_val = max(e["value"] for e in entries) if entries else 1
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, entry in enumerate(entries):
            member = ctx.guild.get_member(entry["user_id"])
            name = member.display_name if member else f"User {entry['user_id']}"
            bar_len = int((entry["value"] / max_val) * 20) if max_val else 0
            bar = "█" * bar_len + "░" * (20 - bar_len)
            prefix = medals[i] if i < 3 else f"#{i+1}"
            val_str = fmt_value(entry["value"], category)
            lines.append(f"{prefix} {name:<16} {bar} {val_str}")

        category_labels = {
            "messages": "Messages",
            "voice": "Voice Time",
            "streaks": "Streaks",
            "engagement": "Engagement",
            "social": "Social (Reactions Given)",
        }

        embed = discord.Embed(
            title=f"\U0001f3c6 Leaderboard — {category_labels[category]} (Last {days} Days)",
            description="```\n" + "\n".join(lines) + "\n```",
            color=COLOR_NEUTRAL,
        )
        embed.set_footer(text=f"Categories: {' • '.join(valid_categories)}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="activity", description="Compare two users side-by-side")
    @commands.guild_only()
    async def activity_cmd(self, ctx: commands.Context, user1: discord.Member, user2: discord.Member, days: int = 30):
        """Head-to-head comparison of two users' activity metrics."""
        days = max(1, days)
        gid = ctx.guild.id

        # Fetch data for both users
        d1 = database.get_user_stats(gid, user1.id, days)
        d2 = database.get_user_stats(gid, user2.id, days)

        if d1["message_count"] == 0 and d2["message_count"] == 0:
            await ctx.send(f"No activity data for either user in the last {days} days.")
            return

        w1 = database.get_user_word_stats(gid, user1.id, days)
        w2 = database.get_user_word_stats(gid, user2.id, days)
        s1 = database.get_user_streaks(gid, user1.id, days)
        s2 = database.get_user_streaks(gid, user2.id, days)
        c1 = database.get_user_consistency(gid, user1.id, days)
        c2 = database.get_user_consistency(gid, user2.id, days)
        e1_data = database.get_user_engagement_ratios(gid, user1.id, days)
        e2_data = database.get_user_engagement_ratios(gid, user2.id, days)

        top_ch1 = ctx.guild.get_channel(d1["top_channel_id"]) if d1["top_channel_id"] else None
        top_ch2 = ctx.guild.get_channel(d2["top_channel_id"]) if d2["top_channel_id"] else None
        ch1_name = f"#{top_ch1.name}" if top_ch1 else "N/A"
        ch2_name = f"#{top_ch2.name}" if top_ch2 else "N/A"

        # Build comparison rows
        def compare_row(label, v1, v2, fmt="{:,}"):
            s1_str = fmt.format(v1) if isinstance(v1, (int, float)) else str(v1)
            s2_str = fmt.format(v2) if isinstance(v2, (int, float)) else str(v2)
            indicator = "◄" if v1 > v2 else ("►" if v2 > v1 else "=")
            return f"{label:<20} {s1_str:>8}  {indicator}  {s2_str:<8}"

        rows = [
            compare_row("\U0001f4ac Messages", d1["message_count"], d2["message_count"]),
            compare_row("\U0001f3a4 Voice", d1["voice_minutes"], d2["voice_minutes"], "{:,}m"),
            compare_row("\U0001f504 Reactions Given", d1["reactions_given"], d2["reactions_given"]),
            compare_row("\U0001f504 Reactions Recv", d1["reactions_received"], d2["reactions_received"]),
            compare_row("\U0001f4dd Avg Words", w1["avg_words"], w2["avg_words"], "{:.1f}"),
            compare_row("\U0001f525 Current Streak", s1["current"], s2["current"], "{}d"),
            compare_row("\U0001f3af Consistency", c1["score"], c2["score"], "{:.0f}"),
            f"{'📌 Top Channel':<20} {ch1_name:>8}  —  {ch2_name:<8}",
        ]

        # Embed 1: Head-to-Head
        header = f"{'':20} {user1.display_name:>8}     {user2.display_name:<8}"
        desc = "```\n" + header + "\n" + "─" * len(header) + "\n" + "\n".join(rows) + "\n```"
        embed1 = discord.Embed(
            title=f"⚔️ {user1.display_name} vs {user2.display_name} — Last {days} Days",
            description=desc,
            color=COLOR_NEUTRAL,
        )
        embed1.set_footer(text=f"{days}-day comparison")

        # Embed 2: Daily Overlay Chart
        daily1 = {d["date"]: d["message_count"] for d in d1.get("daily", [])}
        daily2 = {d["date"]: d["message_count"] for d in d2.get("daily", [])}
        all_dates_set = sorted(set(list(daily1.keys()) + list(daily2.keys())))[-30:]
        labels = [d[-5:] for d in all_dates_set]
        vals1 = [daily1.get(d, 0) for d in all_dates_set]
        vals2 = [daily2.get(d, 0) for d in all_dates_set]

        chart_cfg = {
            "type": "line",
            "data": {
                "labels": labels,
                "datasets": [
                    {
                        "label": user1.display_name,
                        "data": vals1,
                        "borderColor": "rgb(52,152,219)",
                        "backgroundColor": "rgba(52,152,219,0.1)",
                        "fill": False,
                    },
                    {
                        "label": user2.display_name,
                        "data": vals2,
                        "borderColor": "rgb(231,76,60)",
                        "backgroundColor": "rgba(231,76,60,0.1)",
                        "fill": False,
                    },
                ],
            },
            "options": {
                "legend": {"labels": {"fontColor": CHART_TEXT}},
                "scales": {
                    "yAxes": [{"ticks": {"fontColor": CHART_TEXT, "beginAtZero": True}, "gridLines": {"color": CHART_GRID}}],
                    "xAxes": [{"ticks": {"fontColor": CHART_TEXT}, "gridLines": {"color": CHART_GRID}}],
                },
            },
        }
        embed2 = discord.Embed(title="\U0001f4c8 Daily Messages Overlay", color=COLOR_NEUTRAL)
        embed2.set_image(url=await _chart_url(chart_cfg))

        await ctx.send(embeds=[embed1, embed2])

    @commands.hybrid_command(name="insights", description="AI-powered server trend analysis")
    @commands.guild_only()
    async def insights_cmd(self, ctx: commands.Context, days: int = 7):
        days = max(1, days)
        summary = database.get_server_stats_summary(ctx.guild.id, days)

        if summary["messages"] == 0 and summary["voice_seconds"] == 0:
            embed = discord.Embed(
                title="\U0001f916 AI Insights",
                description="\U0001f916 Not enough data to generate meaningful insights. Try again after the bot has been collecting data for a few days.",
                color=COLOR_NEUTRAL,
            )
            await ctx.send(embed=embed)
            return

        top_users = database.get_top_users(ctx.guild.id, days)
        top_channels = database.get_top_channels(ctx.guild.id, days)
        events = database.get_member_events_summary(ctx.guild.id, days)
        peak_hours = database.get_peak_hours(ctx.guild.id, days)

        stats_dict = {
            "period_days": days,
            "total_messages": summary["messages"],
            "total_voice_hours": round(summary["voice_seconds"] / 3600, 1),
            "active_users": summary["active_users"],
            "active_channels": summary["active_channels"],
            "total_reactions": summary["reactions"],
            "member_joins": events["joins"],
            "member_leaves": events["leaves"],
            "top_channels": [
                {"name": ctx.guild.get_channel(r["channel_id"]).name if ctx.guild.get_channel(r["channel_id"]) else str(r["channel_id"]), "messages": r["total"]}
                for r in top_channels
            ],
            "peak_hours": [{"hour": r["hour"], "count": r["count"]} for r in peak_hours[:3]],
        }

        from utils.gemini import get_client
        client = get_client()
        if not client:
            embed = discord.Embed(
                title="\U0001f916 AI Insights",
                description="\U0001f916 Gemini API key not configured. Set `gemini_key` in config.yaml to enable AI insights.\n\nYour stats are still available via `/stats`, `/growth`, and other commands.",
                color=COLOR_NEGATIVE,
            )
            await ctx.send(embed=embed)
            return

        await ctx.defer()

        import json as _json
        import time
        prompt = (
            "You are a Discord server community analyst. Analyze these server activity stats and provide:\n"
            "1. An overall activity assessment (2-3 sentences)\n"
            "2. Notable patterns or anomalies\n"
            "3. User engagement observations\n"
            "4. Channel health assessment\n"
            "5. 3-5 specific, actionable recommendations for improving server health\n\n"
            "Important context: all peak_hours values are in UTC. "
            "This server's users are in Eastern Time (ET). "
            "When referencing peak hours, convert to ET by subtracting 4 hours (EDT, Mar–Nov) "
            "or 5 hours (EST, Nov–Mar). For example UTC 14 = 10:00 EDT or 09:00 EST. "
            "Always express times as ET, not UTC.\n\n"
            "Be concise, specific, and use the actual numbers. Under 500 words.\n\n"
            f"Stats:\n{_json.dumps(stats_dict, indent=2)}"
        )

        t0 = time.perf_counter()
        try:
            response = await client.aio.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=prompt,
            )
            analysis = response.text
            elapsed = time.perf_counter() - t0
            logger.info("Gemini insights generated in %.2fs for guild %s", elapsed, ctx.guild.id)
        except Exception:
            elapsed = time.perf_counter() - t0
            logger.error("Gemini insights failed after %.2fs", elapsed, exc_info=True)
            embed = discord.Embed(
                title="\U0001f916 AI Insights",
                description="\U0001f916 Could not generate insights — Gemini API error. Your stats data is still available via other commands.",
                color=COLOR_NEGATIVE,
            )
            await ctx.send(embed=embed)
            return

        voice_hours = round(summary["voice_seconds"] / 3600, 1)

        # Embed 1: AI Analysis
        e1 = discord.Embed(
            title=f"\U0001f916 AI Insights — Last {days} Days",
            description=analysis[:4096],
            color=COLOR_GEMINI,
        )
        if ctx.guild.icon:
            e1.set_thumbnail(url=ctx.guild.icon.url)
        e1.set_footer(text=f"Powered by Gemini • {summary['messages']:,} messages, {voice_hours}h voice, {events['joins']+events['leaves']} member events")

        # Embed 2: Data Summary
        e2 = discord.Embed(title="\U0001f4ca Data Summary", color=COLOR_GEMINI)
        e2.add_field(name="\U0001f4ac Messages", value=f"{summary['messages']:,}", inline=True)
        e2.add_field(name="\U0001f3a4 Voice Hours", value=f"{voice_hours}h", inline=True)
        e2.add_field(name="\U0001f465 Active Users", value=str(summary["active_users"]), inline=True)
        e2.add_field(name="\U0001f4c8 Growth", value=f"+{events['joins']} / -{events['leaves']}", inline=True)
        peak = max(peak_hours, key=lambda r: r["count"])["hour"] if peak_hours else "N/A"
        e2.add_field(name="\U0001f525 Peak Hour", value=_utc_hour_to_et(peak) if isinstance(peak, int) else peak, inline=True)
        e2.add_field(name="\U0001f504 Reactions", value=f"{summary['reactions']:,}", inline=True)

        await ctx.send(embeds=[e1, e2])


    @commands.hybrid_command(name="dbcheck", description="Diagnose all stats tables for duplicates and consistency")
    @commands.guild_only()
    async def dbcheck_cmd(self, ctx: commands.Context):
        """Check all stats tables for duplicate rows and compare raw vs rolled-up totals."""
        guild_id = ctx.guild.id

        def _dupe_count(conn, table, cols, guild_id):
            col_list = ", ".join(cols)
            return conn.execute(
                f"SELECT COALESCE(SUM(n - 1), 0) as c FROM ("
                f"  SELECT COUNT(*) as n FROM {table} WHERE guild_id = ?"
                f"  GROUP BY {col_list} HAVING COUNT(*) > 1"
                f")", (guild_id,)
            ).fetchone()["c"]

        with database.get_conn() as conn:
            msg_total = conn.execute(
                "SELECT COUNT(*) as c FROM message_events WHERE guild_id = ?", (guild_id,)
            ).fetchone()["c"]
            msg_dupes = _dupe_count(conn, "message_events",
                                    ["channel_id", "user_id", "recorded_at"], guild_id)

            voice_total = conn.execute(
                "SELECT COUNT(*) as c FROM voice_sessions WHERE guild_id = ?", (guild_id,)
            ).fetchone()["c"]
            voice_dupes = _dupe_count(conn, "voice_sessions",
                                      ["channel_id", "user_id", "joined_at"], guild_id)

            member_total = conn.execute(
                "SELECT COUNT(*) as c FROM member_events WHERE guild_id = ?", (guild_id,)
            ).fetchone()["c"]
            member_dupes = _dupe_count(conn, "member_events",
                                       ["user_id", "event_type", "recorded_at"], guild_id)

            rolled_user_msgs = conn.execute(
                "SELECT COALESCE(SUM(message_count), 0) as c FROM user_activity_daily WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()["c"]
            rolled_chan_msgs = conn.execute(
                "SELECT COALESCE(SUM(message_count), 0) as c FROM channel_activity_daily WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()["c"]
            rolled_voice = conn.execute(
                "SELECT COALESCE(SUM(voice_minutes), 0) as c FROM user_activity_daily WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()["c"]
            raw_voice = conn.execute(
                "SELECT COALESCE(SUM(duration_seconds), 0) / 60 as c FROM voice_sessions "
                "WHERE guild_id = ? AND duration_seconds IS NOT NULL", (guild_id,),
            ).fetchone()["c"]

            all_channels = conn.execute(
                "SELECT channel_id, COUNT(*) as c FROM message_events WHERE guild_id = ?"
                " GROUP BY channel_id ORDER BY c DESC",
                (guild_id,),
            ).fetchall()

        total_dupes = msg_dupes + voice_dupes + member_dupes
        header_lines = [
            "**Raw Tables (duplicate extra rows):**",
            f"  message_events: {msg_total:,} rows ({msg_dupes:,} dupes)",
            f"  voice_sessions: {voice_total:,} rows ({voice_dupes:,} dupes)",
            f"  member_events: {member_total:,} rows ({member_dupes:,} dupes)",
            "",
            "**Rolled-up Totals:**",
            f"  user_activity_daily msgs: {rolled_user_msgs:,}",
            f"  channel_activity_daily msgs: {rolled_chan_msgs:,}",
            f"  user_activity_daily voice: {rolled_voice:,}m",
            f"  voice_sessions raw total: {raw_voice:,}m",
            "",
            f"**All channels — {len(all_channels)} (raw events):**",
        ]
        channel_lines = []
        for row in all_channels:
            ch = ctx.guild.get_channel(row["channel_id"])
            name = f"#{ch.name}" if ch else str(row["channel_id"])
            channel_lines.append(f"  {name}: {row['c']:,}")

        color = COLOR_POSITIVE if total_dupes == 0 else COLOR_NEGATIVE
        footer = (
            f"Found {total_dupes:,} total duplicate rows — run !dbclean to remove them"
            if total_dupes > 0 else "No duplicates found — all tables clean"
        )

        description = "\n".join(header_lines + channel_lines)
        if len(description) <= 4096:
            embed = discord.Embed(title="Database Consistency Check", description=description, color=color)
            embed.set_footer(text=footer)
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(title="Database Consistency Check", description="\n".join(header_lines), color=color)
            await ctx.send(embed=embed)
            chunk: list[str] = []
            chunk_len = 0
            page = 1
            for line in channel_lines:
                if chunk_len + len(line) + 1 > 4096:
                    page += 1
                    await ctx.send(embed=discord.Embed(description="\n".join(chunk), color=color))
                    chunk = []
                    chunk_len = 0
                chunk.append(line)
                chunk_len += len(line) + 1
            if chunk:
                e = discord.Embed(description="\n".join(chunk), color=color)
                e.set_footer(text=footer)
                await ctx.send(embed=e)

    @commands.hybrid_command(name="dbclean", description="Remove duplicate rows from all stats tables")
    @commands.guild_only()
    async def dbclean_cmd(self, ctx: commands.Context):
        """Delete duplicate rows from message_events, voice_sessions, and member_events."""
        guild_id = ctx.guild.id

        def _dedup(conn, table, cols, guild_id):
            col_list = ", ".join(cols)
            return conn.execute(
                f"DELETE FROM {table} WHERE guild_id = ? AND rowid NOT IN ("
                f"  SELECT MIN(rowid) FROM {table} WHERE guild_id = ?"
                f"  GROUP BY {col_list}"
                f")", (guild_id, guild_id),
            ).rowcount

        with database.get_conn() as conn:
            msg_del = _dedup(conn, "message_events",
                             ["channel_id", "user_id", "recorded_at"], guild_id)
            voice_del = _dedup(conn, "voice_sessions",
                               ["channel_id", "user_id", "joined_at"], guild_id)
            member_del = _dedup(conn, "member_events",
                                ["user_id", "event_type", "recorded_at"], guild_id)

        total = msg_del + voice_del + member_del
        if total:
            lines = []
            if msg_del:
                lines.append(f"message_events: {msg_del:,}")
            if voice_del:
                lines.append(f"voice_sessions: {voice_del:,}")
            if member_del:
                lines.append(f"member_events: {member_del:,}")
            embed = discord.Embed(
                title="Database Cleaned",
                description=f"Removed **{total:,}** duplicate rows:\n" + "\n".join(lines),
                color=COLOR_POSITIVE,
            )
        else:
            embed = discord.Embed(
                title="Database Clean",
                description="No duplicates found — all tables are clean.",
                color=COLOR_POSITIVE,
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="scan", description="Backfill stats database with server message history")
    @commands.guild_only()
    async def scan_cmd(self, ctx: commands.Context, days: int = 30):
        days = max(1, min(days, 365))

        if self._scanning:
            embed = discord.Embed(
                title="\U0001f50d Scan In Progress",
                description="A scan is already running. Please wait for it to complete.",
                color=COLOR_NEGATIVE,
            )
            await ctx.send(embed=embed)
            return

        self._scanning = True
        try:
            await self._run_scan(ctx, days)
        finally:
            self._scanning = False

    async def _run_scan(self, ctx: commands.Context, days: int):
        await ctx.defer()
        guild = ctx.guild
        # Round to midnight so every day in the window is a full 24 hours.
        # Without this the oldest day is partial (hours 0..scan_hour-1 missing),
        # which biases peak-hour counts toward whichever hour the scan was run.
        cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
        logger.info("Scan starting: guild=%r (ID=%s) days=%d cutoff=%s", guild.name, guild.id, days, cutoff_iso)

        embed = discord.Embed(
            title="\U0001f50d Scanning Server History",
            description=f"Scanning the last **{days} days** of message history...\nThis may take a while for large servers.",
            color=COLOR_NEUTRAL,
        )
        embed.add_field(name="Status", value="Starting...", inline=False)
        progress_msg = await ctx.send(embed=embed)

        with database.get_conn() as conn:
            conn.execute(
                "DELETE FROM message_events WHERE guild_id = ? AND recorded_at >= ?",
                (guild.id, cutoff_iso),
            )
            conn.execute(
                "DELETE FROM member_events WHERE guild_id = ? AND recorded_at >= ?",
                (guild.id, cutoff_iso),
            )

        text_channels = [
            ch for ch in (*guild.text_channels, *guild.voice_channels, *guild.stage_channels)
            if ch.permissions_for(guild.me).read_message_history
        ]
        excluded_channels = config.get("stats.excluded_channels", [])
        excluded_users = config.get("stats.excluded_users", [])
        exclude_bots = config.get("stats.exclude_bots", True)

        total_messages = 0
        total_reactions = 0
        total_channels = 0
        skipped_channels = 0
        batch = []
        reaction_counts = {}
        BATCH_SIZE = 500

        for i, channel in enumerate(text_channels):
            if channel.id in excluded_channels:
                skipped_channels += 1
                continue
            try:
                async for message in channel.history(limit=None, after=cutoff, oldest_first=True):
                    if message.author.bot and exclude_bots:
                        continue
                    if message.author.id in excluded_users:
                        continue
                    word_count = len(message.content.split()) if message.content else 0
                    created_utc = message.created_at.astimezone(datetime.timezone.utc)
                    batch.append((guild.id, channel.id, message.author.id, created_utc.strftime("%Y-%m-%dT%H:%M:%S"), word_count))

                    if message.reactions:
                        r_count = sum(r.count for r in message.reactions)
                        date_key = created_utc.strftime("%Y-%m-%d")
                        key = (message.author.id, date_key)
                        reaction_counts[key] = reaction_counts.get(key, 0) + r_count
                        total_reactions += r_count

                    if len(batch) >= BATCH_SIZE:
                        database.bulk_log_message_events(batch)
                        total_messages += len(batch)
                        batch.clear()

                total_channels += 1
            except discord.Forbidden:
                logger.debug("Scan: no access to #%s (ID=%s)", channel.name, channel.id)
                skipped_channels += 1
                continue
            except Exception:
                logger.error("Scan: error scanning channel #%s (ID=%s)", channel.name, channel.id, exc_info=True)
                skipped_channels += 1
                continue

            if (i + 1) % 5 == 0 or i == len(text_channels) - 1:
                embed.set_field_at(
                    0, name="Status",
                    value=f"\U0001f4c2 Channels: {total_channels}/{len(text_channels)} | \U0001f4ac Messages: {total_messages:,}",
                    inline=False,
                )
                try:
                    await progress_msg.edit(embed=embed)
                except discord.HTTPException:
                    pass

        if batch:
            database.bulk_log_message_events(batch)
            total_messages += len(batch)
            batch.clear()

        embed.set_field_at(0, name="Status", value="\U0001f465 Processing members...", inline=False)
        try:
            await progress_msg.edit(embed=embed)
        except discord.HTTPException:
            pass

        database.save_member_snapshot(
            guild.id,
            guild.member_count or 0,
            sum(1 for m in guild.members if m.status != discord.Status.offline),
            sum(1 for m in guild.members if m.bot),
            guild.premium_subscription_count or 0,
            guild.premium_tier,
        )

        member_events_batch = []
        for member in guild.members:
            if member.joined_at and member.joined_at >= cutoff:
                joined_utc = member.joined_at.astimezone(datetime.timezone.utc)
                member_events_batch.append((guild.id, member.id, "join", joined_utc.strftime("%Y-%m-%dT%H:%M:%S")))
        if member_events_batch:
            database.bulk_log_member_events(member_events_batch)
        member_joins = len(member_events_batch)

        if reaction_counts:
            with database.get_conn() as conn:
                for (user_id, date), count in reaction_counts.items():
                    conn.execute(
                        "INSERT INTO user_activity_daily (guild_id, user_id, date, reactions_received) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(guild_id, user_id, date) DO UPDATE SET reactions_received = excluded.reactions_received",
                        (guild.id, user_id, date, count),
                    )

        embed.set_field_at(0, name="Status", value="\U0001f4ca Running daily rollups...", inline=False)
        try:
            await progress_msg.edit(embed=embed)
        except discord.HTTPException:
            pass

        now = datetime.datetime.now(datetime.timezone.utc)
        for d in range(days + 1):
            date_str = (now - datetime.timedelta(days=d)).strftime("%Y-%m-%d")
            database.rollup_user_activity(guild.id, date_str)
            database.rollup_channel_activity(guild.id, date_str)

        result = discord.Embed(
            title="✅ Scan Complete",
            description=f"Successfully scanned **{days} days** of server history.",
            color=COLOR_POSITIVE,
        )
        result.add_field(name="\U0001f4ac Messages Logged", value=f"{total_messages:,}", inline=True)
        result.add_field(name="\U0001f4c2 Channels Scanned", value=str(total_channels), inline=True)
        result.add_field(name="⏭️ Channels Skipped", value=str(skipped_channels), inline=True)
        result.add_field(name="\U0001f504 Reactions Found", value=f"{total_reactions:,}", inline=True)
        result.add_field(name="\U0001f465 Member Joins Logged", value=str(member_joins), inline=True)
        result.add_field(name="\U0001f4f8 Snapshot Taken", value="Yes", inline=True)
        result.set_footer(text="Stats commands now have historical data! Try /stats")

        await progress_msg.edit(embed=result)
        logger.info(
            "Scan complete: guild=%s days=%d messages=%d channels=%d members=%d",
            guild.name, days, total_messages, total_channels, member_joins,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Stats(bot))
