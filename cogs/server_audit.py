import datetime
import logging
import time
import discord
from discord.ext import commands

import database
import config
from utils.permissions import has_admin_role, build_embed, build_findings_embeds
from utils.gemini import summarize_findings

logger = logging.getLogger(__name__)


class ServerAudit(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def run_audit(self, guild: discord.Guild, triggered_by: str = "scheduler") -> list[dict]:
        t0 = time.perf_counter()
        logger.info(
            "Server audit started | guild=%r (ID=%s) triggered_by=%r",
            guild.name, guild.id, triggered_by,
        )

        findings = []
        run_id = database.start_audit_run("server", guild.id, triggered_by)
        now = datetime.datetime.now(datetime.timezone.utc)
        inactive_days = config.get("server.inactive_channel_days", 14)
        expected_channels = config.get("server.min_onboarding_channels", ["welcome", "rules", "announcements"])

        # --- Dead channels (no recent messages) ---
        dead_channels = []
        scanned = 0
        for channel in guild.text_channels:
            scanned += 1
            try:
                last_msg = [msg async for msg in channel.history(limit=1)]
                if not last_msg:
                    dead_channels.append((channel, None))
                else:
                    delta = now - last_msg[0].created_at
                    if delta.days >= inactive_days:
                        dead_channels.append((channel, delta.days))
            except discord.Forbidden:
                logger.debug("No access to channel history for #%s (ID=%s)", channel.name, channel.id)

        logger.debug("Dead channel scan complete | scanned=%d dead=%d", scanned, len(dead_channels))
        if dead_channels:
            names = ", ".join(
                f"#{c.name} ({d}d ago)" if d else f"#{c.name} (no messages)"
                for c, d in dead_channels[:10]
            )
            f = {
                "severity": "info",
                "category": "dead_channels",
                "description": f"{len(dead_channels)} channels with no activity in {inactive_days}+ days: {names}",
            }
            findings.append(f)
            logger.debug("Finding: [INFO] dead_channels — %s", f["description"])

        # --- Channels missing descriptions/topics ---
        no_topic = [c for c in guild.text_channels if not c.topic]
        max_empty = config.get("server.max_empty_channels", 5)
        if len(no_topic) > max_empty:
            f = {
                "severity": "info",
                "category": "channel_descriptions",
                "description": f"{len(no_topic)} channels have no topic/description set. Consider adding topics to improve discoverability.",
            }
            findings.append(f)
            logger.debug("Finding: [INFO] channel_descriptions — %d channels without topic", len(no_topic))

        # --- Onboarding channel check ---
        channel_names_lower = [c.name.lower() for c in guild.channels]
        missing_onboarding = [
            expected for expected in expected_channels
            if not any(expected.lower() in name for name in channel_names_lower)
        ]
        if missing_onboarding:
            f = {
                "severity": "warning",
                "category": "onboarding",
                "description": f"Missing recommended onboarding channels: {', '.join(missing_onboarding)}. New members may have a poor first experience.",
            }
            findings.append(f)
            logger.debug("Finding: [WARNING] onboarding — missing: %s", missing_onboarding)

        # --- Role hierarchy issues ---
        bot_member = guild.me
        hierarchy_issues = 0
        for role in guild.roles:
            if role >= bot_member.top_role and not role.is_default():
                f = {
                    "severity": "warning",
                    "category": "role_hierarchy",
                    "description": f"Role **{role.name}** is at or above the bot's highest role. The bot cannot manage members with this role.",
                }
                findings.append(f)
                hierarchy_issues += 1
                logger.debug("Finding: [WARNING] role_hierarchy — role %r at/above bot top role", role.name)

        logger.debug("Role hierarchy audit complete | issues=%d", hierarchy_issues)

        # --- Too many channels overall ---
        total_channels = len(guild.channels)
        if total_channels > 50:
            f = {
                "severity": "info",
                "category": "channel_bloat",
                "description": f"Server has {total_channels} channels. Consider archiving or consolidating inactive ones to reduce clutter.",
            }
            findings.append(f)
            logger.debug("Finding: [INFO] channel_bloat — total=%d", total_channels)

        # --- Server icon / description missing ---
        if not guild.icon:
            f = {"severity": "info", "category": "branding", "description": "Server has no icon set. Adding one improves recognition and professionalism."}
            findings.append(f)
            logger.debug("Finding: [INFO] branding — no server icon")

        if not guild.description:
            f = {"severity": "info", "category": "branding", "description": "Server has no description. A description helps discovery and sets expectations for new members."}
            findings.append(f)
            logger.debug("Finding: [INFO] branding — no server description")

        # --- Verification level ---
        if guild.verification_level == discord.VerificationLevel.none:
            f = {
                "severity": "warning",
                "category": "verification",
                "description": "Server verification level is set to **None**. Consider setting it to at least Low to reduce spam/bot joins.",
            }
            findings.append(f)
            logger.debug("Finding: [WARNING] verification — level is None")

        # --- No system channel for welcome messages ---
        if not guild.system_channel:
            f = {"severity": "info", "category": "onboarding", "description": "No system channel configured. Consider setting one for join/leave notifications."}
            findings.append(f)
            logger.debug("Finding: [INFO] onboarding — no system channel")

        # --- Category-less channels ---
        no_category = [c for c in guild.text_channels if c.category is None]
        if len(no_category) > 3:
            f = {
                "severity": "info",
                "category": "organization",
                "description": f"{len(no_category)} text channels are not in any category: {', '.join('#' + c.name for c in no_category[:8])}. Organizing channels into categories improves navigation.",
            }
            findings.append(f)
            logger.debug("Finding: [INFO] organization — %d uncategorised channels", len(no_category))

        # Persist
        for f in findings:
            database.add_finding(run_id, f["severity"], f["category"], f["description"])
        database.finalize_audit_run(run_id, len(findings))

        elapsed = time.perf_counter() - t0
        warnings = sum(1 for f in findings if f["severity"] == "warning")
        infos    = sum(1 for f in findings if f["severity"] == "info")
        logger.info(
            "Server audit complete | guild=%r (ID=%s) run_id=%d findings=%d "
            "(warning=%d info=%d) elapsed=%.2fs",
            guild.name, guild.id, run_id, len(findings), warnings, infos, elapsed,
        )
        return findings

    async def post_audit_results(self, guild: discord.Guild, findings: list[dict], triggered_by: str = "scheduler"):
        audit_channel_id = config.get("audit_channel_id", 0)
        channel = guild.get_channel(audit_channel_id)

        if not channel:
            logger.warning(
                "post_audit_results: audit channel not found | audit_channel_id=%s guild=%r (ID=%s)",
                audit_channel_id, guild.name, guild.id,
            )
            return

        logger.info(
            "Posting server audit results | guild=%r (ID=%s) findings=%d channel=#%s",
            guild.name, guild.id, len(findings), channel.name,
        )

        if not findings:
            await channel.send(embed=build_embed("📊 Server Audit Complete", "No recommendations — server looks well organized!", discord.Color.green()))
            return

        summary = f"**{len(findings)} recommendations** for improving your server."
        embed = build_embed("📊 Server Audit Recommendations", summary, discord.Color.blue())
        embed.add_field(name="Triggered by", value=triggered_by, inline=True)
        await channel.send(embed=embed)

        detail_embeds = build_findings_embeds("📊 Server Audit — recommendations", findings)
        for i in range(0, len(detail_embeds), 10):
            await channel.send(embeds=detail_embeds[i:i + 10])

        ai_summary = await summarize_findings(findings, "server health")
        if ai_summary:
            logger.debug("Posting Gemini AI summary for server audit | guild=%r (ID=%s)", guild.name, guild.id)
            await channel.send(embed=build_embed("🤖 AI Action Plan", ai_summary, discord.Color.purple()))

    @commands.command(name="serveraudit")
    @has_admin_role()
    async def server_audit_cmd(self, ctx: commands.Context):
        """Manually trigger a server audit. Results posted to audit channel."""
        await ctx.send(embed=build_embed("🔍 Running Server Audit...", "Analyzing your server...", discord.Color.blue()))
        findings = await self.run_audit(ctx.guild, triggered_by=str(ctx.author.id))
        await self.post_audit_results(ctx.guild, findings, triggered_by=ctx.author.mention)
        await ctx.send(embed=build_embed("✅ Done", f"Server audit complete. {len(findings)} recommendations posted.", discord.Color.green()))

    @commands.command(name="lastserveraudit")
    @has_admin_role()
    async def last_server_audit(self, ctx: commands.Context):
        """Show findings from the most recent server audit."""
        rows = database.get_recent_findings(ctx.guild.id, "server", limit=15)
        if not rows:
            await ctx.send(embed=build_embed("No Audit Data", "No server audit has been run yet.", discord.Color.greyple()))
            return

        lines = [f"[{r['severity'].upper()}] **{r['category']}**: {r['description']}" for r in rows]
        await ctx.send(embed=build_embed("📊 Last Server Audit Findings", "\n\n".join(lines[:10]), discord.Color.blue()))


async def setup(bot):
    await bot.add_cog(ServerAudit(bot))
