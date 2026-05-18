import json
import logging
import urllib.parse
import datetime

import discord
from discord.ext import commands

import config
import database

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
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m"


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

    async def cog_load(self):
        database.close_orphaned_voice_sessions()
        logger.info("Stats cog loaded — orphaned voice sessions closed")

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

    # ------------------------------------------------------------------
    # Scheduler coroutines (T037-T038)
    # ------------------------------------------------------------------

    async def _take_snapshot(self, guild: discord.Guild):
        if not config.get("stats.enabled", True):
            return
        total = guild.member_count or 0
        bots = sum(1 for m in guild.members if m.bot)
        online = sum(1 for m in guild.members if m.status != discord.Status.offline)
        database.save_member_snapshot(
            guild.id, total, online, bots,
            guild.premium_subscription_count or 0,
            guild.premium_tier,
        )

    async def _run_rollup(self, guild: discord.Guild):
        yesterday = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        database.rollup_user_activity(guild.id, yesterday)
        database.rollup_channel_activity(guild.id, yesterday)
        retention = config.get("stats.retention_days", 30)
        database.prune_old_events(retention)
        logger.info("Daily rollup complete for guild %r: date=%s, retention=%dd", guild.name, yesterday, retention)

    # ------------------------------------------------------------------
    # Event listeners (T032-T036)
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
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
        word_count = len(message.content.split()) if message.content else 0
        database.log_message_event(message.guild.id, message.channel.id, message.author.id, word_count)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if not config.get("stats.enabled", True):
            return
        if member.bot and config.get("stats.exclude_bots", True):
            return
        if before.channel is None and after.channel is not None:
            database.start_voice_session(member.guild.id, after.channel.id, member.id)
        elif before.channel is not None and after.channel is None:
            database.end_voice_session(member.guild.id, member.id)
        elif before.channel is not None and after.channel is not None and before.channel.id != after.channel.id:
            database.end_voice_session(member.guild.id, member.id)
            database.start_voice_session(member.guild.id, after.channel.id, member.id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if config.get("stats.enabled", True):
            database.log_member_event(member.guild.id, member.id, "join")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if config.get("stats.enabled", True):
            database.log_member_event(member.guild.id, member.id, "leave")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        if config.get("stats.enabled", True):
            database.log_member_event(guild.id, user.id, "ban")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        if config.get("stats.enabled", True):
            database.log_member_event(guild.id, user.id, "unban")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not config.get("stats.enabled", True) or not config.get("stats.track_reactions", True):
            return
        if payload.guild_id is None:
            return
        if payload.member and payload.member.bot and config.get("stats.exclude_bots", True):
            return
        author_id = getattr(payload, "message_author_id", None)
        database.increment_reaction(payload.guild_id, payload.user_id, author_id)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if not config.get("stats.enabled", True) or not config.get("stats.track_reactions", True):
            return
        if payload.guild_id is None:
            return
        author_id = getattr(payload, "message_author_id", None)
        database.decrement_reaction(payload.guild_id, payload.user_id, author_id)

    # ------------------------------------------------------------------
    # Commands (T039-T043, T045)
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="stats", description="View server activity dashboard")
    @commands.guild_only()
    async def stats_cmd(self, ctx: commands.Context, days: int = 7):
        days = max(1, min(days, 90))
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
                    "y": {"position": "left", "ticks": {"fontColor": CHART_TEXT}, "gridLines": {"color": CHART_GRID}},
                    "y1": {"position": "right", "ticks": {"fontColor": CHART_TEXT}, "gridLines": {"drawOnChartArea": False}},
                },
                "legend": {"labels": {"fontColor": CHART_TEXT}},
            },
        }
        e4 = discord.Embed(title="\U0001f4c8 Daily Activity", color=COLOR_NEUTRAL)
        e4.set_image(url=_build_quickchart_url(chart_cfg))

        await ctx.send(embeds=[e1, e2, e3, e4])

    @commands.hybrid_command(name="userstats", description="View activity profile for a user")
    @commands.guild_only()
    async def userstats_cmd(self, ctx: commands.Context, member: discord.Member, days: int = 30):
        days = max(1, min(days, 90))
        data = database.get_user_stats(ctx.guild.id, member.id, days)

        if data["message_count"] == 0 and data["voice_minutes"] == 0:
            embed = discord.Embed(
                title=f"\U0001f4ca Stats for {member.display_name}",
                description=f"No activity recorded for {member.display_name} in the last {days} days.",
                color=COLOR_NEUTRAL,
            )
            await ctx.send(embed=embed)
            return

        daily_msgs = [d.get("message_count", 0) for d in data["daily"]]
        avg_msgs = data["message_count"] / days if days else 0
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

        # Embed 2: Sparkline + chart
        spark = _sparkline(daily_msgs)
        peak_val = max(daily_msgs) if daily_msgs else 0
        quiet_val = min(daily_msgs) if daily_msgs else 0
        desc = f"```\nMessages per day (last {min(len(daily_msgs), 14)} days):\n{spark}\nAvg: {avg_msgs:.1f} | Peak: {peak_val} | Quiet: {quiet_val}\n```"

        dates = [d.get("date", "")[-5:] for d in data["daily"]][-14:]
        vals = daily_msgs[-14:]
        chart_cfg = {
            "type": "bar",
            "data": {"labels": dates, "datasets": [{"label": "Messages", "data": vals, "backgroundColor": "rgba(52,152,219,0.7)"}]},
            "options": {"legend": {"labels": {"fontColor": CHART_TEXT}}, "scales": {"yAxes": [{"ticks": {"fontColor": CHART_TEXT, "beginAtZero": True}, "gridLines": {"color": CHART_GRID}}], "xAxes": [{"ticks": {"fontColor": CHART_TEXT}, "gridLines": {"color": CHART_GRID}}]}},
        }
        e2 = discord.Embed(title="\U0001f4c8 Daily Activity", description=desc, color=color)
        e2.set_image(url=_build_quickchart_url(chart_cfg))

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
        days = max(1, min(days, 90))
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
        peak_hour = f"{data['peak_hour']}:00 UTC" if data["peak_hour"] is not None else "N/A"

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
        e3.set_image(url=_build_quickchart_url(chart_cfg))

        await ctx.send(embeds=[e1, e2, e3])

    @commands.hybrid_command(name="voicestats", description="View voice activity dashboard")
    @commands.guild_only()
    async def voicestats_cmd(self, ctx: commands.Context, days: int = 7):
        days = max(1, min(days, 90))
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
        e4.set_image(url=_build_quickchart_url(chart_cfg))

        await ctx.send(embeds=[e1, e2, e3, e4])

    @commands.hybrid_command(name="growth", description="View member growth trends")
    @commands.guild_only()
    async def growth_cmd(self, ctx: commands.Context, days: int = 30):
        days = max(1, min(days, 365))
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
        e2.set_image(url=_build_quickchart_url(chart_cfg))

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
        e4.set_image(url=_build_quickchart_url(chart_cfg2))

        await ctx.send(embeds=[e1, e2, e3, e4])

    @commands.hybrid_command(name="insights", description="AI-powered server trend analysis")
    @commands.guild_only()
    async def insights_cmd(self, ctx: commands.Context, days: int = 7):
        days = max(1, min(days, 90))
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
        e2.add_field(name="\U0001f525 Peak Hour", value=f"{peak}:00 UTC" if isinstance(peak, int) else peak, inline=True)
        e2.add_field(name="\U0001f504 Reactions", value=f"{summary['reactions']:,}", inline=True)

        await ctx.send(embeds=[e1, e2])


async def setup(bot: commands.Bot):
    await bot.add_cog(Stats(bot))
