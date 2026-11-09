import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
import database
from utils import rules_forms, rules_service
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


_MAX_FILES = 10
_NOT_ALLOWED = "❌ You need the user role to use this form."


def _files_field() -> discord.ui.Label:
    return discord.ui.Label(
        text="Rulebook files",
        description=".pdf .txt .md .docx, up to 10 files (optional)",
        component=discord.ui.FileUpload(required=False, min_values=0, max_values=_MAX_FILES))


def _link_field() -> discord.ui.Label:
    return discord.ui.Label(
        text="Rulebook link",
        description="Public http(s) link to a rulebook (optional)",
        component=discord.ui.TextInput(required=False, max_length=500,
                                       placeholder="https://example.com/rules.pdf"))


async def _handle_submit(interaction: discord.Interaction, *, deferred: bool = False,
                         **kwargs) -> None:
    """Shared on_submit body: role re-check, defer, progress, final report."""
    guild = interaction.guild
    if guild is None or not member_has_user_role(interaction.user, guild):
        await interaction.response.send_message(
            _NOT_ALLOWED, ephemeral=True, allowed_mentions=_NO_MENTIONS)
        return
    if not deferred:
        await interaction.response.defer(thinking=True)
    msg = await interaction.followup.send(
        "Checking your submission…", wait=True, allowed_mentions=_NO_MENTIONS)

    async def progress(text: str) -> None:
        await msg.edit(content=text, allowed_mentions=_NO_MENTIONS)

    try:
        result = await rules_forms.process_submission(
            guild.id, interaction.user.id, progress=progress, **kwargs)
        text = result.render()
    except RulesError as e:
        logger.warning("Rules form failed | guild=%s | user=%s | %s",
                       guild.id, interaction.user.id, e.user_message)
        text = e.user_message
    except Exception:
        logger.error("Rules form error | guild=%s | user=%s", guild.id, interaction.user.id,
                     exc_info=True)
        text = "❌ Something went wrong adding that. The error has been logged."
    await msg.edit(content=text, allowed_mentions=_NO_MENTIONS)


async def _form_error(interaction: discord.Interaction, error: Exception) -> None:
    logger.error("Rules form crashed | guild=%s | user=%s: %s",
                 getattr(interaction.guild, "id", None),
                 getattr(interaction.user, "id", None), error, exc_info=error)
    text = "❌ Something went wrong. Check the logs."
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=_NO_MENTIONS)
    else:
        await interaction.response.send_message(text, ephemeral=True,
                                                allowed_mentions=_NO_MENTIONS)


class AddGameModal(discord.ui.Modal, title="Add a game"):
    def __init__(self):
        super().__init__(timeout=900)
        self.name = discord.ui.Label(
            text="Game name",
            component=discord.ui.TextInput(required=True, min_length=1, max_length=100))
        self.files = _files_field()
        self.link = _link_field()
        for item in (self.name, self.files, self.link):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _handle_submit(
            interaction, game_name=self.name.component.value,
            files=list(self.files.component.values), url=self.link.component.value)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await _form_error(interaction, error)


class AddSourceModal(discord.ui.Modal, title="Add rulebooks to a game"):
    def __init__(self, game_names: list):
        super().__init__(timeout=900)
        options = [discord.SelectOption(label=n[:100], value=n[:100]) for n in game_names[:25]]
        self.game = discord.ui.Label(
            text="Game", component=discord.ui.Select(placeholder="Choose a game", options=options))
        self.files = _files_field()
        self.link = _link_field()
        for item in (self.game, self.files, self.link):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or not member_has_user_role(interaction.user, guild):
            await interaction.response.send_message(
                _NOT_ALLOWED, ephemeral=True, allowed_mentions=_NO_MENTIONS)
            return
        values = self.game.component.values
        if not values:
            await interaction.response.send_message(
                "❌ Choose a game.", ephemeral=True, allowed_mentions=_NO_MENTIONS)
            return
        await interaction.response.defer(thinking=True)
        try:
            row = await database.run(rules_service.resolve_game, guild.id, values[0])
        except RulesError as e:
            await interaction.followup.send(e.user_message, allowed_mentions=_NO_MENTIONS)
            return
        await _handle_submit(
            interaction, deferred=True, game=row, files=list(self.files.component.values),
            url=self.link.component.value)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await _form_error(interaction, error)


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

    @rules.command(name="addgame", description="Create a game and add its rulebooks (opens a form).")
    @commands.guild_only()
    @has_user_role()
    async def addgame(self, ctx):
        if ctx.interaction is None:
            await ctx.send("Use `/rules addgame` — it opens a form.", allowed_mentions=_NO_MENTIONS)
            return
        await ctx.interaction.response.send_modal(AddGameModal())

    @rules.command(name="addsource", description="Add rulebooks to a game (opens a form).")
    @commands.guild_only()
    @has_user_role()
    async def addsource(self, ctx):
        if ctx.interaction is None:
            await ctx.send("Use `/rules addsource` — it opens a form.", allowed_mentions=_NO_MENTIONS)
            return
        try:
            rows = await database.run(database.list_games, ctx.guild.id)
        except Exception:
            logger.error("Listing games failed | guild=%s | user=%s",
                         ctx.guild.id, ctx.author.id, exc_info=True)
            raise
        if not rows:
            await ctx.send("No games yet. Add one with `/rules addgame`.",
                           allowed_mentions=_NO_MENTIONS, ephemeral=True)
            return
        await ctx.interaction.response.send_modal(AddSourceModal([r["name"] for r in rows]))

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

    @ask.autocomplete("game")
    async def _ask_game_ac(self, interaction: discord.Interaction, current: str):
        return await self.game_autocomplete(interaction, current)

    @sources.autocomplete("game")
    async def _sources_game_ac(self, interaction: discord.Interaction, current: str):
        return await self.game_autocomplete(interaction, current)


async def setup(bot):
    await bot.add_cog(Rules(bot))
