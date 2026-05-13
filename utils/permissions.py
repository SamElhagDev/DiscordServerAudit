import discord
from discord.ext import commands
from functools import wraps
import config


def has_admin_role():
    """
    Check decorator that restricts commands to members with the configured admin role.
    No exceptions — bot owner included.
    """
    async def predicate(ctx: commands.Context) -> bool:
        role_name = config.get("admin_role")
        if not role_name:
            await ctx.send("❌ `admin_role` is not configured in config.yaml.")
            return False

        member_roles = [r.name for r in ctx.author.roles]
        if role_name not in member_roles:
            await ctx.send(
                f"❌ You need the **{role_name}** role to use this command.",
                ephemeral=True
            )
            return False
        return True

    return commands.check(predicate)


def admin_role_check(func):
    """
    Decorator version for use outside of command context (e.g. interaction handlers).
    """
    @wraps(func)
    async def wrapper(self, ctx, *args, **kwargs):
        role_name = config.get("admin_role")
        member_roles = [r.name for r in ctx.author.roles]
        if role_name not in member_roles:
            await ctx.send(
                f"❌ You need the **{role_name}** role to use this command.",
                ephemeral=True
            )
            return
        return await func(self, ctx, *args, **kwargs)
    return wrapper


def build_embed(title: str, description: str, color: discord.Color = discord.Color.blue()) -> discord.Embed:
    """Shared embed builder for consistent formatting."""
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="Admin Bot")
    return embed


def severity_color(severity: str) -> discord.Color:
    return {
        "critical": discord.Color.red(),
        "warning": discord.Color.orange(),
        "info": discord.Color.blue(),
    }.get(severity, discord.Color.greyple())
