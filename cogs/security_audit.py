import discord
from discord.ext import commands
from typing import Optional

import database
import config
from utils.permissions import has_admin_role, build_embed, severity_color
from utils.gemini import summarize_findings


DANGEROUS_PERMS = [
    ("administrator", "Administrator"),
    ("manage_guild", "Manage Server"),
    ("ban_members", "Ban Members"),
    ("kick_members", "Kick Members"),
    ("manage_roles", "Manage Roles"),
    ("manage_channels", "Manage Channels"),
    ("mention_everyone", "Mention Everyone"),
    ("manage_webhooks", "Manage Webhooks"),
    ("manage_messages", "Manage Messages"),
]


class SecurityAudit(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def run_audit(self, guild: discord.Guild, triggered_by: str = "scheduler") -> list[dict]:
        """
        Core audit logic. Returns list of findings and persists them to DB.
        """
        findings = []
        run_id = database.start_audit_run("security", guild.id, triggered_by)

        # --- 2FA Check ---
        if guild.mfa_level == discord.MFALevel.disabled:
            findings.append({"severity": "warning", "category": "2fa", "description": "Server does not require 2FA for moderators."})

        # --- Roles with Administrator ---
        admin_roles = [r for r in guild.roles if r.permissions.administrator and not r.is_default()]
        max_admin = config.get("security.max_admin_roles", 3)
        if len(admin_roles) > max_admin:
            findings.append({
                "severity": "critical",
                "category": "permissions",
                "description": f"{len(admin_roles)} roles have Administrator permission (threshold: {max_admin}): {', '.join(r.name for r in admin_roles)}"
            })
        elif admin_roles:
            findings.append({
                "severity": "info",
                "category": "permissions",
                "description": f"Roles with Administrator: {', '.join(r.name for r in admin_roles)}"
            })

        # --- @everyone permissions check ---
        everyone = guild.default_role
        for perm_attr, perm_name in DANGEROUS_PERMS:
            if getattr(everyone.permissions, perm_attr, False):
                findings.append({
                    "severity": "critical",
                    "category": "permissions",
                    "description": f"@everyone has dangerous permission: **{perm_name}**"
                })

        # --- Roles assigned to large % of members with dangerous perms ---
        for role in guild.roles:
            if role.is_default():
                continue
            dangerous = [name for attr, name in DANGEROUS_PERMS if getattr(role.permissions, attr, False)]
            if dangerous and len(role.members) > 0:
                pct = len(role.members) / max(guild.member_count, 1) * 100
                if pct > 50:
                    findings.append({
                        "severity": "warning",
                        "category": "permissions",
                        "description": f"Role **{role.name}** has {', '.join(dangerous)} and is assigned to {pct:.0f}% of members ({len(role.members)} members)."
                    })

        # --- Bot permission audit ---
        for member in guild.members:
            if not member.bot:
                continue
            top_role = member.top_role
            dangerous_bot_perms = [name for attr, name in DANGEROUS_PERMS if getattr(top_role.permissions, attr, False)]
            if dangerous_bot_perms:
                findings.append({
                    "severity": "warning",
                    "category": "bot_permissions",
                    "description": f"Bot **{member.name}** has sensitive permissions: {', '.join(dangerous_bot_perms)}"
                })

        # --- Channel permission overwrites ---
        for channel in guild.channels:
            for target, overwrite in channel.overwrites.items():
                ow_dict = dict(overwrite)
                for attr, name in DANGEROUS_PERMS:
                    if ow_dict.get(attr) is True:
                        label = target.name if hasattr(target, "name") else str(target)
                        findings.append({
                            "severity": "warning",
                            "category": "channel_overwrites",
                            "description": f"Channel **#{channel.name}** grants **{name}** to `{label}` via overwrite."
                        })

        # --- Roles with no members (clutter) ---
        empty_roles = [r for r in guild.roles if len(r.members) == 0 and not r.managed and not r.is_default()]
        if len(empty_roles) > 5:
            findings.append({
                "severity": "info",
                "category": "role_hygiene",
                "description": f"{len(empty_roles)} roles have zero members and may be unused: {', '.join(r.name for r in empty_roles[:10])}{'...' if len(empty_roles) > 10 else ''}"
            })

        # Persist findings
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
            await channel.send(embed=build_embed("🔒 Security Audit Complete", "No issues found.", discord.Color.green()))
            return

        criticals = [f for f in findings if f["severity"] == "critical"]
        warnings = [f for f in findings if f["severity"] == "warning"]
        infos = [f for f in findings if f["severity"] == "info"]

        summary = f"**{len(criticals)} critical** | **{len(warnings)} warnings** | **{len(infos)} info**"
        embed = build_embed("🔒 Security Audit Results", summary, discord.Color.red() if criticals else discord.Color.orange())
        embed.add_field(name="Triggered by", value=triggered_by, inline=True)

        await channel.send(embed=embed)

        for f in findings:
            color = severity_color(f["severity"])
            e = discord.Embed(
                title=f"[{f['severity'].upper()}] {f['category']}",
                description=f["description"],
                color=color
            )
            await channel.send(embed=e)

        ai_summary = await summarize_findings(findings, "security")
        if ai_summary:
            await channel.send(embed=build_embed("🤖 AI Action Plan", ai_summary, discord.Color.purple()))

    # -------------------------------------------------------------------------
    # Manual trigger command
    # -------------------------------------------------------------------------
    @commands.command(name="securityaudit")
    @has_admin_role()
    async def security_audit_cmd(self, ctx: commands.Context):
        """Manually trigger a security audit. Results posted to audit channel."""
        await ctx.send(embed=build_embed("🔍 Running Security Audit...", "This may take a moment.", discord.Color.blue()))
        findings = await self.run_audit(ctx.guild, triggered_by=str(ctx.author.id))
        await self.post_audit_results(ctx.guild, findings, triggered_by=ctx.author.mention)
        await ctx.send(embed=build_embed("✅ Done", f"Security audit complete. {len(findings)} findings posted to the audit channel.", discord.Color.green()))

    @commands.command(name="lastaudit")
    @has_admin_role()
    async def last_audit(self, ctx: commands.Context):
        """Show findings from the most recent security audit."""
        rows = database.get_recent_findings(ctx.guild.id, "security", limit=15)
        if not rows:
            await ctx.send(embed=build_embed("No Audit Data", "No security audit has been run yet.", discord.Color.greyple()))
            return

        lines = [f"[{r['severity'].upper()}] **{r['category']}**: {r['description']}" for r in rows]
        await ctx.send(embed=build_embed("🔒 Last Security Audit Findings", "\n\n".join(lines[:10]), discord.Color.orange()))


async def setup(bot):
    await bot.add_cog(SecurityAudit(bot))
