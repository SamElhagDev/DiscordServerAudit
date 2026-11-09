import discord
from discord.ext import commands


# Per-cog accent colours so each section is visually distinct
_COG_COLORS = {
    "Admin":           discord.Color.blurple(),
    "BulkTasks":       discord.Color.gold(),
    "Messaging":       discord.Color.og_blurple(),
    "Info":            discord.Color.dark_teal(),
    "SecurityAudit":   discord.Color.red(),
    "ServerAudit":     discord.Color.green(),
    "Stats":           discord.Color.blue(),
    "NaturalLanguage": discord.Color.purple(),
    "FactCheck":       discord.Color.teal(),
    "Rules":           discord.Color.dark_gold(),
}
_DEFAULT_COLOR = discord.Color.blue()

# Friendly display names for cogs
_COG_LABELS = {
    "Admin":           "⚙️  Admin Utilities",
    "BulkTasks":       "🔨  Bulk Operations",
    "Messaging":       "📨  Messaging & Actions",
    "Info":            "🔎  Info & Inspection",
    "SecurityAudit":   "🔒  Security Audit",
    "ServerAudit":     "📊  Server Audit",
    "Stats":           "📈  Stats & Analytics",
    "NaturalLanguage": "💬  Natural Language",
    "FactCheck":       "🔍  Fact-Check",
    "Rules":           "📖  Game Rules",
}

# Short descriptions shown in the overview embed
_COG_DESCRIPTIONS = {
    "Admin":           "Runtime config inspection and bot utilities.",
    "BulkTasks":       "Mass-manage messages, members, roles and channels.",
    "Messaging":       "Send messages, embeds, DMs, react, pin, lock channels, timeout/kick/ban members, manage roles, invites, and voice.",
    "Info":            "Inspect and list channels, roles, bans, invites, emojis, admins, avatars, and member counts.",
    "SecurityAudit":   "Scan for dangerous permissions, 2FA gaps, and bot overreach.",
    "ServerAudit":     "Detect dead channels, missing onboarding, and branding gaps.",
    "Stats":           "Server activity dashboards, user stats, voice tracking, leaderboards, user comparisons, server health pulse, and history scanning.",
    "NaturalLanguage": "Describe admin tasks in plain English and execute them with AI.",
    "FactCheck":       "React with an emoji to fact-check any message using AI.",
    "Rules":           "Upload rulebooks per game and ask rules questions answered only from them (user role).",
}


# Footer note on who may run a cog's commands (default: admin role)
_COG_ACCESS = {
    "Rules": "🎲  Requires the user role",
}
_DEFAULT_ACCESS = "⚠️  Requires admin role"


def _usage(prefix: str, cmd: commands.Command) -> str:
    """Build a usage string from a command's signature."""
    if cmd.signature:
        return f"`{prefix}{cmd.qualified_name} {cmd.signature}`"
    return f"`{prefix}{cmd.qualified_name}`"


def _summary(cmd: commands.Command) -> str:
    """Docstring if present, else the slash-command description."""
    return cmd.help or cmd.description or "No description."


def _invoke_prefix(prefix: str, cmd: commands.Command) -> str:
    """Hybrid groups are shown as slash commands (their forms are slash-only)."""
    root = cmd.root_parent or cmd
    return "/" if isinstance(root, commands.HybridGroup) else prefix


class RichHelpCommand(commands.HelpCommand):
    """
    Embed-based help command.

    !help                 → overview of all cogs + command list
    !help <cog/category>  → detail view for one cog
    !help <command>       → full detail for one command
    """

    # ------------------------------------------------------------------
    # Overview  (!help)
    # ------------------------------------------------------------------
    async def send_bot_help(self, mapping):
        prefix = self.context.clean_prefix
        embed = discord.Embed(
            title="📖  Server Audit Bot — Help",
            description=(
                f"Use `{prefix}help <command>` for detailed usage on any command.\n"
                f"All commands require the configured **admin role**.\n​"
            ),
            color=discord.Color.blurple(),
        )

        for cog, cmds in mapping.items():
            visible = await self.filter_commands(cmds, sort=True)
            if not visible:
                continue

            cog_name = cog.qualified_name if cog else "Uncategorised"
            label = _COG_LABELS.get(cog_name, f"📂  {cog_name}")
            description = _COG_DESCRIPTIONS.get(cog_name, "")
            cmd_list = "  ".join(f"`{_invoke_prefix(prefix, c)}{c.name}`" for c in visible)

            embed.add_field(
                name=label,
                value=f"{description}\n{cmd_list}" if description else cmd_list,
                inline=False,
            )

        from bot import __version__
        embed.set_footer(text=f"v{__version__}  •  Prefix: {prefix}  •  Scheduled audits run automatically")
        await self.get_destination().send(embed=embed)

    # ------------------------------------------------------------------
    # Single cog  (!help BulkTasks)
    # ------------------------------------------------------------------
    async def send_cog_help(self, cog: commands.Cog):
        prefix = self.context.clean_prefix
        cog_name = cog.qualified_name
        color = _COG_COLORS.get(cog_name, _DEFAULT_COLOR)
        label = _COG_LABELS.get(cog_name, cog_name)

        embed = discord.Embed(
            title=label,
            description=_COG_DESCRIPTIONS.get(cog_name, cog.description or ""),
            color=color,
        )

        visible = await self.filter_commands(cog.get_commands(), sort=True)
        for cmd in visible:
            embed.add_field(
                name=_usage(_invoke_prefix(prefix, cmd), cmd),
                value=_summary(cmd),
                inline=False,
            )

        embed.set_footer(text=f"Use {prefix}help <command> for full detail")
        await self.get_destination().send(embed=embed)

    # ------------------------------------------------------------------
    # Command group  (!help gamerules)
    # ------------------------------------------------------------------
    async def send_group_help(self, group: commands.Group):
        prefix = self.context.clean_prefix
        shown = _invoke_prefix(prefix, group)
        cog_name = group.cog.qualified_name if group.cog else ""

        embed = discord.Embed(
            title=_COG_LABELS.get(cog_name, f"Command: {shown}{group.qualified_name}"),
            description=_COG_DESCRIPTIONS.get(cog_name, _summary(group)),
            color=_COG_COLORS.get(cog_name, _DEFAULT_COLOR),
        )

        # A hybrid group's fallback runs the group itself; list it first under its slash name.
        fallback = getattr(group, "fallback", None)
        if fallback:
            embed.add_field(
                name=f"`{shown}{group.qualified_name} {fallback}`",
                value=_summary(group), inline=False,
            )

        visible = await self.filter_commands(group.commands, sort=False)
        for cmd in visible:
            embed.add_field(name=_usage(shown, cmd), value=_summary(cmd), inline=False)

        embed.set_footer(text=_COG_ACCESS.get(cog_name, _DEFAULT_ACCESS))
        await self.get_destination().send(embed=embed)

    # ------------------------------------------------------------------
    # Single command  (!help bulkdelete)
    # ------------------------------------------------------------------
    async def send_command_help(self, cmd: commands.Command):
        prefix = self.context.clean_prefix
        cog_name = cmd.cog.qualified_name if cmd.cog else ""
        color = _COG_COLORS.get(cog_name, _DEFAULT_COLOR)

        shown = _invoke_prefix(prefix, cmd)
        usage = _usage(shown, cmd)

        embed = discord.Embed(
            title=f"Command: {shown}{cmd.qualified_name}",
            description=_summary(cmd),
            color=color,
        )
        embed.add_field(name="Usage", value=usage, inline=False)

        if cmd.aliases:
            embed.add_field(
                name="Aliases",
                value="  ".join(f"`{a}`" for a in cmd.aliases),
                inline=False,
            )

        if cog_name:
            embed.add_field(
                name="Category",
                value=_COG_LABELS.get(cog_name, cog_name),
                inline=True,
            )

        embed.set_footer(text=_COG_ACCESS.get(cog_name, _DEFAULT_ACCESS))
        await self.get_destination().send(embed=embed)

    # ------------------------------------------------------------------
    # Unknown command
    # ------------------------------------------------------------------
    async def send_error_message(self, error: str):
        prefix = self.context.clean_prefix
        embed = discord.Embed(
            title="❌  Command not found",
            description=f"{error}\n\nUse `{prefix}help` to see all available commands.",
            color=discord.Color.red(),
        )
        await self.get_destination().send(embed=embed)
