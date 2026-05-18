import json
import logging
import urllib.parse
import datetime
import zoneinfo

import discord
from discord.ext import commands

import config
import database

_ET = zoneinfo.ZoneInfo("America/New_York")


def _utc_hour_to_et(hour: int) -> str:
    """Convert a UTC hour (0-23) to an ET display string, e.g. '09:00 EDT'."""
    today = datetime.date.today()
    utc_dt = datetime.datetime(today.year, today.month, today.day, hour, 0, 0,
                               tzinfo=datetime.timezone.utc)
    et_dt = utc_dt.astimezone(_ET)
    offset_h = int(et_dt.utcoffset().total_seconds() // 3600)
    abbr = "EST" if offset_h == -5 else "EDT"
    return f"{et_dt.hour:02d}:00 {abbr}"

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
# Utility functions (T027-T031)
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
# Stats Cog (T026, T032-T038, T039-T043, T045)
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
    # Scheduler coroutines (T037-T038)
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
    # Event listeners (T032-T036)
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
    # Commands (T039-T043, T045)
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

        # Embed 1: Overview
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

        await ctx.send(embeds=[e1, e2, e3, e4])

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

        # Embed 1: Profile
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

        await ctx.send(embeds=[e1, e2, e3])

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

        # Embed 1: Overview
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

        await ctx.send(embeds=[e1, e2, e3])

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

        # Embed 1: Overview
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
        e1.add_field(name="\U0001f525 Peak Concurrent", value="—", inline=True)
        e1.add_field(name="\U0001f7e2 Currently In", value=str(currently_in), inline=True)

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

        await ctx.send(embeds=[e1, e2, e3, e4])

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

        # Embed 1: Summary
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

        await ctx.send(embeds=[e1, e2, e3, e4])

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
        lines = []
        for h in range(24):
            count = by_hour.get(h, 0)
            pct = (count / total * 100) if total else 0
            bar = "█" * int(pct / 2)
            et_label = _utc_hour_to_et(h)
            lines.append(f"{et_label:<11}  {bar:<50} {count:>5} ({pct:4.1f}%)")

        embed = discord.Embed(
            title=f"Peak Hours — Last {days} Days (Eastern Time)",
            description=f"```\n{''.join(f'{l}{chr(10)}' for l in lines)}```",
            color=COLOR_NEUTRAL,
        )
        embed.set_footer(text=f"Total messages in window: {total:,}")
        await ctx.send(embed=embed)

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
