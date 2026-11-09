import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
import database
from utils import rules_ingest, rules_service
from utils.permissions import build_embed, has_user_role, member_has_user_role
from utils.rules_embeds import build_answer_embed, build_games_embed, build_sources_embed
from utils.rules_errors import RulesError

logger = logging.getLogger(__name__)

_NO_MENTIONS = discord.AllowedMentions.none()


def _log_rules_error(ctx, e: RulesError) -> None:
    logger.warning(
        "Rules command failed | cmd=%s | guild=%s | user=%s | %s",
        ctx.command.qualified_name if ctx.command else "unknown",
        getattr(ctx.guild, "id", None), getattr(ctx.author, "id", None), e.user_message)


class Rules(commands.Cog):
    """Per-game rulebook uploads and rules Q&A (user role only)."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        if config.get("rules.enabled", True):
            return True
        await ctx.send("❌ Game rules are disabled in config.yaml.",
                           allowed_mentions=_NO_MENTIONS)
        return False

    async def game_autocomplete(self, interaction: discord.Interaction, current: str):
        """Shared game-name autocomplete (user-role-only, max 25 choices).

        Reusable: attach with `@<cmd>.autocomplete("game")` on any command.
        """
        guild = interaction.guild
        if guild is None or not member_has_user_role(interaction.user, guild):
            return []
        names = await database.run(database.search_game_names, guild.id, current, 25)
        return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names[:25]]

    @commands.hybrid_group(name="rules", fallback="games",
                           description="List the games that have rulebooks.")
    @commands.guild_only()
    @has_user_role()
    async def rules(self, ctx):
        try:
            rows = await database.run(database.list_games, ctx.guild.id)
            await ctx.send(allowed_mentions=_NO_MENTIONS, embed=build_games_embed(rows))
        except RulesError as e:
            _log_rules_error(ctx, e)
            await ctx.send(e.user_message, allowed_mentions=_NO_MENTIONS)

    @rules.command(name="addgame", description="Create a game to hold rulebooks.")
    @commands.guild_only()
    @has_user_role()
    async def addgame(self, ctx, *, name: str):
        try:
            await ctx.defer()
            row = await rules_service.create_game(ctx.guild.id, name, ctx.author.id)
            await ctx.send(
                f"✅ Game **{row['name']}** created. Add rulebooks with `/rules addsource`.",
                allowed_mentions=_NO_MENTIONS)
        except RulesError as e:
            _log_rules_error(ctx, e)
            await ctx.send(e.user_message, allowed_mentions=_NO_MENTIONS)

    @rules.command(name="addsource", description="Add a rulebook (file or public URL) to a game.")
    @commands.guild_only()
    @has_user_role()
    async def addsource(self, ctx, game: str, file: discord.Attachment | None = None,
                        url: str | None = None):
        if file is not None and url:
            await ctx.send("❌ Provide either a file or a url, not both.",
                           allowed_mentions=_NO_MENTIONS)
            return
        if file is None and not url:
            await ctx.send("❌ Provide a file attachment or a url.",
                           allowed_mentions=_NO_MENTIONS)
            return
        try:
            await ctx.defer()
            row = await database.run(rules_service.resolve_game, ctx.guild.id, game)
            max_bytes = config.get("rules.max_source_bytes", 52428800)
            if file is not None:
                # Validate type/size from metadata before downloading the bytes.
                rules_ingest.resolve_mime(file.filename, file.size, max_bytes)
                data = await file.read()
                filename = file.filename
                mime = rules_ingest.resolve_mime(filename, len(data), max_bytes)
                origin, origin_ref = "upload", filename
            else:
                timeout_s = config.get("rules.url_fetch_timeout_seconds", 20)
                # fetch_url runs check_public_url off-loop on every hop.
                async with rules_ingest.create_fetch_session(timeout_s) as session:
                    data, filename, mime = await rules_ingest.fetch_url(
                        session, url, max_bytes, timeout_s)
                origin, origin_ref = "url", url
            total = await rules_service.add_source(
                row, data, filename, mime, origin, origin_ref, ctx.author.id)
            embed = discord.Embed(title=f"📚 Source added to {row['name']}"[:256],
                                  color=discord.Color.dark_gold())
            embed.add_field(name="File", value=filename[:1024])
            embed.add_field(name="Size (MB)", value=f"{len(data) / 1048576:.1f}")
            embed.add_field(name="Total sources", value=str(total))
            await ctx.send(allowed_mentions=_NO_MENTIONS, embed=embed)
        except RulesError as e:
            _log_rules_error(ctx, e)
            await ctx.send(e.user_message, allowed_mentions=_NO_MENTIONS)

    @rules.command(name="ask", description="Ask a rules question answered from a game's rulebooks.")
    @commands.guild_only()
    @has_user_role()
    async def ask(self, ctx, game: str, *, question: str):
        try:
            await ctx.defer()
            row = await database.run(rules_service.resolve_game, ctx.guild.id, game)
            answer = await rules_service.ask(row, question)
            await ctx.send(allowed_mentions=_NO_MENTIONS, embed=build_answer_embed(row["name"], question, answer))
        except RulesError as e:
            _log_rules_error(ctx, e)
            await ctx.send(e.user_message, allowed_mentions=_NO_MENTIONS)

    @rules.command(name="sources", description="List a game's uploaded sources.")
    @commands.guild_only()
    @has_user_role()
    async def sources(self, ctx, game: str):
        try:
            row = await database.run(rules_service.resolve_game, ctx.guild.id, game)
            rows = await database.run(database.list_sources, row["id"])
            await ctx.send(allowed_mentions=_NO_MENTIONS, embed=build_sources_embed(row["name"], rows))
        except RulesError as e:
            _log_rules_error(ctx, e)
            await ctx.send(e.user_message, allowed_mentions=_NO_MENTIONS)

    async def _confirm(self, ctx, prompt: str) -> bool:
        """React-to-confirm (30 s, author only). Edits the prompt to Cancelled otherwise."""
        msg = await ctx.send(allowed_mentions=_NO_MENTIONS, embed=build_embed("⚠️ Confirm", prompt, discord.Color.orange()))
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        def check(reaction, user):
            return (user == ctx.author and str(reaction.emoji) in ("✅", "❌")
                    and reaction.message.id == msg.id)

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            await msg.edit(allowed_mentions=_NO_MENTIONS, embed=build_embed("Cancelled", "Timed out.", discord.Color.greyple()))
            return False
        if str(reaction.emoji) == "❌":
            await msg.edit(allowed_mentions=_NO_MENTIONS, embed=build_embed("Cancelled", "Cancelled.", discord.Color.greyple()))
            return False
        return True

    @rules.command(name="removesource", description="Remove a source from a game.")
    @commands.guild_only()
    @has_user_role()
    async def removesource(self, ctx, game: str, source_id: int):
        try:
            await ctx.defer()
            row = await database.run(rules_service.resolve_game, ctx.guild.id, game)
            if not await self._confirm(
                    ctx, f"Remove source `#{source_id}` from **{game}**?\nReact ✅ to confirm or ❌ to cancel."):
                return
            name = await rules_service.remove_source(row, source_id, str(ctx.author), ctx.guild.id)
            await ctx.send(f"🗑️ Removed **{name}** from **{row['name']}**.",
                           allowed_mentions=_NO_MENTIONS)
        except RulesError as e:
            _log_rules_error(ctx, e)
            await ctx.send(e.user_message, allowed_mentions=_NO_MENTIONS)

    @rules.command(name="deletegame", description="Delete a game and all its sources.")
    @commands.guild_only()
    @has_user_role()
    async def deletegame(self, ctx, game: str):
        try:
            await ctx.defer()
            row = await database.run(rules_service.resolve_game, ctx.guild.id, game)
            sources = await database.run(database.list_sources, row["id"])
            if not await self._confirm(
                    ctx, f"Delete **{row['name']}** and all {len(sources)} of its sources? "
                         "This cannot be undone.\nReact ✅ to confirm or ❌ to cancel."):
                return
            n = await rules_service.delete_game(row, str(ctx.author), ctx.guild.id)
            await ctx.send(f"🗑️ Deleted **{row['name']}** ({n} sources).",
                           allowed_mentions=_NO_MENTIONS)
        except RulesError as e:
            _log_rules_error(ctx, e)
            await ctx.send(e.user_message, allowed_mentions=_NO_MENTIONS)

    @removesource.autocomplete("game")
    async def _removesource_game_ac(self, interaction: discord.Interaction, current: str):
        return await self.game_autocomplete(interaction, current)

    @deletegame.autocomplete("game")
    async def _deletegame_game_ac(self, interaction: discord.Interaction, current: str):
        return await self.game_autocomplete(interaction, current)

    @addsource.autocomplete("game")
    async def _addsource_game_ac(self, interaction: discord.Interaction, current: str):
        return await self.game_autocomplete(interaction, current)

    @ask.autocomplete("game")
    async def _ask_game_ac(self, interaction: discord.Interaction, current: str):
        return await self.game_autocomplete(interaction, current)

    @sources.autocomplete("game")
    async def _sources_game_ac(self, interaction: discord.Interaction, current: str):
        return await self.game_autocomplete(interaction, current)


async def setup(bot):
    await bot.add_cog(Rules(bot))
