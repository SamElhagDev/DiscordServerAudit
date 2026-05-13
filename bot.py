import asyncio
import logging
import discord
from discord.ext import commands

import config
import database
from utils.scheduler import IntervalScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

COGS = [
    "cogs.bulk_tasks",
    "cogs.security_audit",
    "cogs.server_audit",
]


class AdminBot(commands.Bot):
    def __init__(self):
        cfg = config.load_config()
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True

        super().__init__(
            command_prefix=cfg["bot"].get("prefix", "!"),
            intents=intents,
            help_command=commands.DefaultHelpCommand()
        )
        self.scheduler = IntervalScheduler(self)

    async def setup_hook(self):
        database.init_db()
        for cog in COGS:
            await self.load_extension(cog)
            logger.info(f"Loaded cog: {cog}")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Admin role: {config.get('admin_role')}")

        # Register scheduled tasks
        security_cog = self.get_cog("SecurityAudit")
        server_cog = self.get_cog("ServerAudit")

        security_interval = config.get("intervals.security_audit", 24)
        server_interval = config.get("intervals.server_audit", 168)

        for guild in self.guilds:
            self.scheduler.register(
                key=f"security_audit_{guild.id}",
                interval_hours=security_interval,
                coro_factory=lambda g=guild: self._run_security_audit(g)
            )
            self.scheduler.register(
                key=f"server_audit_{guild.id}",
                interval_hours=server_interval,
                coro_factory=lambda g=guild: self._run_server_audit(g)
            )

        asyncio.create_task(self.scheduler.start())

    async def _run_security_audit(self, guild: discord.Guild):
        cog = self.get_cog("SecurityAudit")
        if cog:
            findings = await cog.run_audit(guild, triggered_by="scheduler")
            await cog.post_audit_results(guild, findings)

    async def _run_server_audit(self, guild: discord.Guild):
        cog = self.get_cog("ServerAudit")
        if cog:
            findings = await cog.run_audit(guild, triggered_by="scheduler")
            await cog.post_audit_results(guild, findings)

    async def on_command_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CheckFailure):
            # Already handled in the check itself
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing argument: `{error.param.name}`. Use `!help {ctx.command}` for usage.")
        elif isinstance(error, commands.BadArgument):
            await ctx.send(f"❌ Bad argument: {error}. Use `!help {ctx.command}` for usage.")
        else:
            logger.error(f"Unhandled command error in {ctx.command}: {error}", exc_info=error)
            await ctx.send(f"❌ An unexpected error occurred: {error}")


async def main():
    cfg = config.load_config()
    token = cfg["bot"]["token"]
    if not token or token == "YOUR_BOT_TOKEN_HERE":
        raise ValueError("Bot token is not set in config.yaml")

    bot = AdminBot()
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
