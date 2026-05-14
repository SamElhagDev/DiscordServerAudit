import discord
from discord.ext import commands

import config
from utils.permissions import has_admin_role, build_embed

# Secrets that must never appear in Discord output
_REDACTED = {"bot.token", "token"}


class Admin(commands.Cog):
    """General admin utility commands."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="config")
    @has_admin_role()
    async def show_config(self, ctx: commands.Context):
        """Show the current effective runtime configuration."""
        cfg = config.load_config()

        lines = [
            f"**Prefix**: `{cfg.get('bot', {}).get('prefix', '!')}`",
            f"**Admin Role**: `{cfg.get('admin_role', 'NOT SET')}`",
            f"**Log Channel**: `{cfg.get('log_channel_id', 0)}`",
            f"**Audit Channel**: `{cfg.get('audit_channel_id', 0)}`",
            "",
            "**Scheduler Intervals**",
            f"  Security Audit: `{config.get('intervals.security_audit', 24)}h`",
            f"  Server Audit: `{config.get('intervals.server_audit', 168)}h`",
            "",
            "**Security Thresholds**",
            f"  Max Admin Roles: `{config.get('security.max_admin_roles', 3)}`",
            f"  Inactive Channel Days: `{config.get('server.inactive_channel_days', 14)}`",
            f"  Max Empty Channels: `{config.get('server.max_empty_channels', 5)}`",
        ]

        await ctx.send(embed=build_embed(
            "⚙️ Runtime Configuration",
            "\n".join(lines),
            discord.Color.blue()
        ))


async def setup(bot):
    await bot.add_cog(Admin(bot))
