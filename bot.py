import asyncio
import logging
import logging.handlers
import os
import time

import discord
from discord.ext import commands


# ---------------------------------------------------------------------------
# Logging — configured first so every module's logger picks up the handlers
# ---------------------------------------------------------------------------

def _setup_logging() -> str:
    """
    Console: INFO+  (human-readable at a glance)
    Rotating file: DEBUG+  (full trace for post-mortem)
    Third-party noise (discord.py, google, urllib3) suppressed to WARNING.
    Returns the path of the log file for startup reporting.
    """
    log_dir = os.path.join(
        os.environ.get("DiscordServerAudit_APP_ROOT", os.path.dirname(os.path.abspath(__file__))),
        "logs",
    )
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "bot.log")

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(logging.INFO)

    rotating = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    rotating.setFormatter(fmt)
    rotating.setLevel(logging.DEBUG)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(rotating)

    for quiet in (
        "discord", "discord.http", "discord.gateway", "discord.client",
        "google", "urllib3", "asyncio", "charset_normalizer",
    ):
        logging.getLogger(quiet).setLevel(logging.WARNING)

    return log_path


_LOG_PATH = _setup_logging()

# Project imports after logging is ready
import config  # noqa: E402
import database
from utils.scheduler import IntervalScheduler
from utils.help import RichHelpCommand

logger = logging.getLogger(__name__)

COGS = [
    "cogs.admin",
    "cogs.bulk_tasks",
    "cogs.security_audit",
    "cogs.server_audit",
    "cogs.natural_language",
    "cogs.stats",
]


class AdminBot(commands.Bot):
    def __init__(self):
        cfg = config.load_config()
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.voice_states = True

        super().__init__(
            command_prefix=cfg.get("bot", {}).get("prefix", "!"),
            intents=intents,
            help_command=RichHelpCommand(),
        )
        self.scheduler = IntervalScheduler(self)
        self._start_time = time.monotonic()
        self._scheduler_started = False

    async def setup_hook(self):
        database.init_db()
        for cog in COGS:
            try:
                await self.load_extension(cog)
                logger.info("Loaded cog: %s", cog)
            except Exception as e:
                logger.error("Failed to load cog %s: %s", cog, e, exc_info=True)
        synced = await self.tree.sync()
        logger.info("Synced %d app commands", len(synced))

    async def on_ready(self):
        elapsed = time.monotonic() - self._start_time
        guild_count = len(self.guilds)
        member_count = sum((g.member_count or 0) for g in self.guilds)

        logger.info("=" * 60)
        logger.info("Bot ready: %s (ID=%s)", self.user, self.user.id)
        logger.info("Guilds: %d | Members: %d | Startup: %.2fs", guild_count, member_count, elapsed)
        logger.info("Prefix: %r | Admin role: %r", self.command_prefix, config.get("admin_role"))
        logger.info(
            "Audit channel: %s | Log channel: %s",
            config.get("audit_channel_id"), config.get("log_channel_id"),
        )
        logger.info(
            "Security audit interval: %sh | Server audit interval: %sh",
            config.get("intervals.security_audit", 24),
            config.get("intervals.server_audit", 168),
        )
        logger.info("Gemini: %s", "configured" if config.get("gemini_key") else "not configured")
        logger.info("Log file: %s", _LOG_PATH)
        logger.info("=" * 60)

        if self._scheduler_started:
            logger.debug("on_ready fired again (reconnect) — scheduler already running, skipping registration")
            return
        self._scheduler_started = True

        security_interval = config.get("intervals.security_audit", 24)
        server_interval = config.get("intervals.server_audit", 168)

        for guild in self.guilds:
            logger.debug(
                "Registering scheduler tasks for guild %r (ID=%s, members=%d)",
                guild.name, guild.id, guild.member_count or 0,
            )
            self.scheduler.register(
                key=f"security_audit_{guild.id}",
                interval_hours=security_interval,
                coro_factory=lambda g=guild: self._run_security_audit(g),
            )
            self.scheduler.register(
                key=f"server_audit_{guild.id}",
                interval_hours=server_interval,
                coro_factory=lambda g=guild: self._run_server_audit(g),
            )

        asyncio.create_task(self.scheduler.start())

    async def on_guild_join(self, guild: discord.Guild):
        logger.info(
            "Joined guild: %r (ID=%s, members=%d, owner=%s)",
            guild.name, guild.id, guild.member_count, guild.owner,
        )

    async def on_guild_remove(self, guild: discord.Guild):
        logger.info("Removed from guild: %r (ID=%s)", guild.name, guild.id)

    async def on_command(self, ctx: commands.Context):
        logger.info(
            "CMD %r | user=%s (ID=%s) | guild=%r (ID=%s) | channel=#%s",
            ctx.command.qualified_name,
            ctx.author, ctx.author.id,
            ctx.guild.name if ctx.guild else "DM",
            ctx.guild.id if ctx.guild else "N/A",
            getattr(ctx.channel, "name", "DM"),
        )

    async def on_command_completion(self, ctx: commands.Context):
        logger.debug("CMD completed: %r | user=%s (ID=%s)", ctx.command.qualified_name, ctx.author, ctx.author.id)

    def _audit_channel_in_guild(self, guild: discord.Guild) -> bool:
        """A scheduled audit only makes sense if its results channel lives in this guild."""
        audit_channel_id = config.get("audit_channel_id", 0)
        if not audit_channel_id or guild.get_channel(audit_channel_id) is None:
            logger.info(
                "Skipping scheduled audit for guild %r (ID=%s) — configured audit channel %s "
                "is not in this guild",
                guild.name, guild.id, audit_channel_id,
            )
            return False
        return True

    async def _run_security_audit(self, guild: discord.Guild):
        if not self._audit_channel_in_guild(guild):
            return
        cog = self.get_cog("SecurityAudit")
        if cog:
            findings = await cog.run_audit(guild, triggered_by="scheduler")
            await cog.post_audit_results(guild, findings)

    async def _run_server_audit(self, guild: discord.Guild):
        if not self._audit_channel_in_guild(guild):
            return
        cog = self.get_cog("ServerAudit")
        if cog:
            findings = await cog.run_audit(guild, triggered_by="scheduler")
            await cog.post_audit_results(guild, findings)

    async def on_command_error(self, ctx: commands.Context, error):
        if isinstance(error, (commands.CheckFailure, commands.CommandNotFound)):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            logger.warning(
                "Missing argument %r | cmd=%r | user=%s (ID=%s)",
                error.param.name, ctx.command.qualified_name, ctx.author, ctx.author.id,
            )
            await ctx.send(f"❌ Missing argument: `{error.param.name}`. Use `!help {ctx.command}` for usage.")
        elif isinstance(error, commands.BadArgument):
            logger.warning(
                "Bad argument | cmd=%r | user=%s (ID=%s) | error=%s",
                ctx.command.qualified_name, ctx.author, ctx.author.id, error,
            )
            await ctx.send(f"❌ Bad argument: {error}. Use `!help {ctx.command}` for usage.")
        else:
            logger.error(
                "Unhandled error | cmd=%r | user=%s (ID=%s) | guild=%s | error=%s",
                ctx.command, ctx.author, ctx.author.id,
                ctx.guild.id if ctx.guild else "DM",
                error, exc_info=error,
            )
            await ctx.send(f"❌ An unexpected error occurred: {error}")


async def main():
    token = os.environ.get("DiscordServerAudit_TOKEN")
    if not token:
        raise ValueError("DiscordServerAudit_TOKEN environment variable is not set")

    logger.info("Starting bot process...")
    bot = AdminBot()
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
