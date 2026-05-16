import logging
import discord
from discord.ext import commands

import config

logger = logging.getLogger(__name__)


def has_admin_role():
    """
    Check decorator that restricts commands to members with the configured admin role.
    No exceptions — bot owner included.
    """
    async def predicate(ctx: commands.Context) -> bool:
        cmd_name = ctx.command.qualified_name if ctx.command else "unknown"
        role_name = config.get("admin_role")
        if not role_name:
            logger.error(
                "admin_role is not configured — all commands will be denied | cmd=%r guild=%r (ID=%s)",
                cmd_name,
                ctx.guild.name if ctx.guild else "DM",
                ctx.guild.id if ctx.guild else "N/A",
            )
            await ctx.send("❌ `admin_role` is not configured in config.yaml.")
            return False

        # Admin commands are guild-only — ctx.author is a plain User in DMs (no roles).
        if ctx.guild is None:
            logger.warning("Permission denied | cmd=%r | DM context — admin commands are guild-only", cmd_name)
            return False

        # Resolve the configured name to actual Role object(s) in THIS guild.
        # Role names are not unique; matching by name string allows privilege
        # escalation, so resolve to a Role and compare by identity (ID) instead.
        matching = [r for r in ctx.guild.roles if r.name == role_name]

        if not matching:
            logger.error(
                "admin_role %r does not exist in guild %r (ID=%s) — denying %r",
                role_name, ctx.guild.name, ctx.guild.id, cmd_name,
            )
            await ctx.send(f"❌ The configured admin role **{role_name}** does not exist in this server.")
            return False

        if len(matching) > 1:
            # Ambiguous gate = fail closed. Guessing a role here is the exact
            # escalation vector this check exists to prevent.
            logger.error(
                "Multiple roles named %r in guild %r (ID=%s) — admin gate is ambiguous, denying %r",
                role_name, ctx.guild.name, ctx.guild.id, cmd_name,
            )
            await ctx.send(
                f"❌ Multiple roles are named **{role_name}** in this server. "
                "An owner must remove or rename the duplicate before admin commands will work."
            )
            return False

        admin_role = matching[0]
        if admin_role not in ctx.author.roles:  # Role.__eq__ compares by ID
            logger.warning(
                "Permission denied | cmd=%r | user=%s (ID=%s) | guild=%r (ID=%s) | missing role %r (ID=%s)",
                cmd_name,
                ctx.author, ctx.author.id,
                ctx.guild.name, ctx.guild.id,
                role_name, admin_role.id,
            )
            await ctx.send(
                f"❌ You need the **{role_name}** role to use this command.",
                ephemeral=True,
            )
            return False

        return True

    return commands.check(predicate)


def build_embed(title: str, description: str, color: discord.Color = discord.Color.blue()) -> discord.Embed:
    """Shared embed builder for consistent formatting."""
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="Admin Bot")
    return embed


def severity_color(severity: str) -> discord.Color:
    return {
        "critical": discord.Color.red(),
        "warning":  discord.Color.orange(),
        "info":     discord.Color.blue(),
    }.get(severity, discord.Color.greyple())


_SEVERITY_RANK = {"critical": 3, "warning": 2, "info": 1}


def build_findings_embeds(title: str, findings: list[dict], max_fields: int = 10) -> list[discord.Embed]:
    """
    Pack audit findings into as few embeds as possible instead of one message
    per finding. Respects Discord limits (25 fields / 6000 chars per embed) and
    colours each embed by its most severe finding.
    """
    embeds: list[discord.Embed] = []
    chunk: list[dict] = []
    chunk_chars = 0

    def flush():
        nonlocal chunk, chunk_chars
        if not chunk:
            return
        top = max(chunk, key=lambda f: _SEVERITY_RANK.get(f["severity"], 0))["severity"]
        embed = discord.Embed(title=title, color=severity_color(top))
        for f in chunk:
            name = f"[{f['severity'].upper()}] {f['category'].replace('_', ' ').title()}"
            embed.add_field(name=name[:256], value=(f["description"] or "—")[:1024], inline=False)
        embed.set_footer(text="Admin Bot")
        embeds.append(embed)
        chunk = []
        chunk_chars = 0

    for f in findings:
        cost = len(f.get("description", "")) + len(f.get("category", "")) + 48
        if chunk and (len(chunk) >= max_fields or chunk_chars + cost > 5500):
            flush()
        chunk.append(f)
        chunk_chars += cost
    flush()
    return embeds
