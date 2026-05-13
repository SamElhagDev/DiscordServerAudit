import discord
from discord.ext import commands
import datetime

import database
import config
from utils.permissions import has_admin_role, build_embed, severity_color


class ServerAudit(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def run_audit(self, guild: discord.Guild, triggered_by: str = "scheduler") -> list[dict]:
        findings = []
        run_id = database.start_audit_run("server", guild.id, triggered_by)
        now = datetime.datetime.utcnow()
        inactive_days = config.get("server.inactive_channel_days", 14)
        expected_channels = config.get("server.min_onboarding_channels", ["welcome", "rules", "announcements"])

        # --- Dead channels (no recent messages) ---
        dead_channels = []
        for channel in guild.text_channels:
            try:
                last_msg = [msg async for msg in channel.history(limit=1)]
                if not last_msg:
                    dead_channels.append((channel, None))
                else:
                    delta = now - last_msg[0].created_at.replace(tzinfo=None)
                    if delta.days >= inactive_days:
                        dead_channels.append((channel, delta.days))
            except discord.Forbidden:
                pass

        if dead_channels:
            names = ", ".join(
                f"#{c.name} ({d}d ago)" if d else f"#{c.name} (no messages)"
                for c, d in dead_channels[:10]
            )
            findings.append({
                "severity": "info",
                "category": "dead_channels",
                "description": f"{len(dead_channels)} channels with no activity in {inactive_days}+ days: {names}"
            })

        # --- Channels missing descriptions/topics ---
        no_topic = [c for c in guild.text_channels if not c.topic]
        max_empty = config.get("server.max_empty_channels", 5)
        if len(no_topic) > max_empty:
            findings.append({
                "severity": "info",
                "category": "channel_descriptions",
                "description": f"{len(no_topic)} channels have no topic/description set. Consider adding topics to improve discoverability."
            })

        # --- Onboarding channel check ---
        channel_names_lower = [c.name.lower() for c in guild.channels]
        missing_onboarding = []
        for expected in expected_channels:
            if not any(expected.lower() in name for name in channel_names_lower):
                missing_onboarding.append(expected)

        if missing_onboarding:
            findings.append({
                "severity": "warning",
                "category": "onboarding",
                "description": f"Missing recommended onboarding channels: {', '.join(missing_onboarding)}. New members may have a poor first experience."
            })

        # --- Role hierarchy issues ---
        bot_member = guild.me
        for role in guild.roles:
            if role >= bot_member.top_role and not role.is_default():
                findings.append({
                    "severity": "warning",
                    "category": "role_hierarchy",
                    "description": f"Role **{role.name}** is at or above the bot's highest role. The bot cannot manage members with this role."
                })

        # --- Too many channels overall ---
        total_channels = len(guild.channels)
        if total_channels > 50:
            findings.append({
                "severity": "info",
                "category": "channel_bloat",
                "description": f"Server has {total_channels} channels. Consider archiving or consolidating inactive ones to reduce clutter."
            })

        # --- Server icon / description missing ---
        if not guild.icon:
            findings.append({
                "severity": "info",
                "category": "branding",
                "description": "Server has no icon set. Adding one improves recognition and professionalism."
            })

        if not guild.description:
            findings.append({
                "severity": "info",
                "category": "branding",
                "description": "Server has no description. A description helps discovery and sets expectations for new members."
            })

        # --- Verification level ---
        if guild.verification_level == discord.VerificationLevel.none:
            findings.append({
                "severity": "warning",
                "category": "verification",
                "description": "Server verification level is set to **None**. Consider setting it to at least Low to reduce spam/bot joins."
            })

        # --- No system channel for welcome messages ---
        if not guild.system_channel:
            findings.append({
                "severity": "info",
                "category": "onboarding",
                "description": "No system channel configured. Consider setting one for join/leave notifications."
            })

        # --- Category-less channels ---
        no_category = [c for c in guild.text_channels if c.category is None]
        if len(no_category) > 3:
            findings.append({
                "severity": "info",
                "category": "organization",
                "description": f"{len(no_category)} text channels are not in any category: {', '.join('#' + c.name for c in no_category[:8])}. Organizing channels into categories improves navigation."
            })

        # Persist
        for f in findings:
            database.add_finding(run_id, f["severity"], f["category"], f["description"])
        database.finalize_audit_run(run_id, len(findings))

        return findings

    async def post_audit_results(self, guild: discord.Guild, findings: list[dict], triggered_by: str = "scheduler"):
        audit_channel_id = config.get("audit_channel_id", 0)
        channel = guild.get_channel(audit_channel_id)
        if not channel:
            return

        if not findings:
            await channel.send(embed=build_embed("📊 Server Audit Complete", "No recommendations — server looks well organized!", discord.Color.green()))
            return

        summary = f"**{len(findings)} recommendations** for improving your server."
        embed = build_embed("📊 Server Audit Recommendations", summary, discord.Color.blue())
        embed.add_field(name="Triggered by", value=triggered_by, inline=True)
        await channel.send(embed=embed)

        for f in findings:
            e = discord.Embed(
                title=f"[{f['severity'].upper()}] {f['category'].replace('_', ' ').title()}",
                description=f["description"],
                color=severity_color(f["severity"])
            )
            await channel.send(embed=e)

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
