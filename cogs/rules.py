import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
import database
from utils import rules_embeds as emb
from utils import rules_forms, rules_service
from utils.permissions import has_user_role, member_has_user_role
from utils.rules_errors import RulesError
from utils.rules_views import DownloadButton, DownloadView, SourcesButton, answer_view

logger = logging.getLogger(__name__)

_NO_MENTIONS = discord.AllowedMentions.none()


def _log_rules_error(ctx, e: RulesError) -> None:
    logger.warning(
        "Rules command failed | cmd=%s | guild=%s | user=%s | %s",
        ctx.command.qualified_name if ctx.command else "unknown",
        getattr(ctx.guild, "id", None), getattr(ctx.author, "id", None), e.user_message)


_MAX_FILES = 10
_NOT_ALLOWED = "❌ You need the user role to use this form."


async def _reply_error(ctx, message: str) -> None:
    """Errors are private on slash commands (ephemeral is ignored for prefix ones)."""
    await ctx.send(embed=emb.error_embed(message), ephemeral=True, allowed_mentions=_NO_MENTIONS)


async def _inter_error(interaction: discord.Interaction, message: str) -> None:
    embed = emb.error_embed(message)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=_NO_MENTIONS)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True,
                                                allowed_mentions=_NO_MENTIONS)


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
        await _inter_error(interaction, _NOT_ALLOWED)
        return
    if not deferred:
        await interaction.response.defer(thinking=True)
    msg = await interaction.followup.send(
        embed=emb.build_progress_embed("Checking your submission…"), wait=True,
        allowed_mentions=_NO_MENTIONS)

    async def progress(text: str) -> None:
        await msg.edit(embed=emb.build_progress_embed(text), allowed_mentions=_NO_MENTIONS)

    try:
        result = await rules_forms.process_submission(
            guild.id, interaction.user.id, progress=progress, **kwargs)
        embed = emb.build_submission_embed(result)
    except RulesError as e:
        logger.warning("Rules form failed | guild=%s | user=%s | %s",
                       guild.id, interaction.user.id, e.user_message)
        embed = emb.error_embed(e.user_message)
    except Exception:
        logger.error("Rules form error | guild=%s | user=%s", guild.id, interaction.user.id,
                     exc_info=True)
        embed = emb.error_embed("Something went wrong adding that. The error has been logged.")
    await msg.edit(content=None, embed=embed, allowed_mentions=_NO_MENTIONS)


async def _form_error(interaction: discord.Interaction, error: Exception) -> None:
    logger.error("Rules form crashed | guild=%s | user=%s: %s",
                 getattr(interaction.guild, "id", None),
                 getattr(interaction.user, "id", None), error, exc_info=error)
    await _inter_error(interaction, "Something went wrong. The error has been logged.")


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
            await _inter_error(interaction, _NOT_ALLOWED)
            return
        values = self.game.component.values
        if not values:
            await _inter_error(interaction, "Choose a game.")
            return
        await interaction.response.defer(thinking=True)
        try:
            row = await database.run(rules_service.resolve_game, guild.id, values[0])
        except RulesError as e:
            await interaction.followup.send(embed=emb.error_embed(e.user_message),
                                            allowed_mentions=_NO_MENTIONS)
            return
        await _handle_submit(
            interaction, deferred=True, game=row, files=list(self.files.component.values),
            url=self.link.component.value)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await _form_error(interaction, error)


def _book_option(src) -> discord.SelectOption:
    origin = "link" if src["origin"] == "url" else "upload"
    return discord.SelectOption(
        label=src["display_name"][:100], value=str(src["id"]), emoji="📄",
        description=f"{emb._mb(src['size_bytes'])} · {origin}"[:100])


class RemoveBookModal(discord.ui.Modal):
    """Pick one of a game's rulebooks from a dropdown; submitting removes it."""

    def __init__(self, game, sources: list):
        super().__init__(title="Remove a rulebook", timeout=900)
        self.game_row = game
        extra = len(sources) - 25
        self.book = discord.ui.Label(
            text=f"Rulebook to remove from {game['name']}"[:45],
            description=(f"Showing the first 25 of {len(sources)}." if extra > 0
                         else "It is removed as soon as you submit."),
            component=discord.ui.Select(placeholder="Choose a rulebook",
                                        options=[_book_option(s) for s in sources[:25]]))
        self.add_item(self.book)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or not member_has_user_role(interaction.user, guild):
            await _inter_error(interaction, _NOT_ALLOWED)
            return
        values = self.book.component.values
        if not values:
            await _inter_error(interaction, "Choose a rulebook.")
            return
        await interaction.response.defer(thinking=True)
        try:
            name = await rules_service.remove_source(
                self.game_row, int(values[0]), str(interaction.user), guild.id)
            embed = emb.removed_embed("Rulebook removed",
                                      f"**{name}** was removed from **{self.game_row['name']}**.")
        except RulesError as e:
            logger.warning("Remove rulebook failed | guild=%s | user=%s | %s",
                           guild.id, interaction.user.id, e.user_message)
            embed = emb.error_embed(e.user_message)
        await interaction.followup.send(embed=embed, allowed_mentions=_NO_MENTIONS)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await _form_error(interaction, error)


class Rules(commands.Cog):
    """Per-game rulebook uploads and rules Q&A (user role only)."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        # Answer buttons carry their ids in custom_id, so they work after a restart.
        self.bot.add_dynamic_items(SourcesButton, DownloadButton)
        logger.info("Registered persistent game-rules answer buttons")

    async def cog_unload(self):
        self.bot.remove_dynamic_items(SourcesButton, DownloadButton)

    async def cog_check(self, ctx):
        if config.get("rules.enabled", True):
            return True
        await ctx.send(embed=emb.error_embed("Game rules are disabled in config.yaml."),
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

    @commands.hybrid_group(name="gamerules", fallback="list",
                           description="List the games that have rulebooks.")
    @commands.guild_only()
    @has_user_role()
    async def gamerules(self, ctx):
        try:
            rows = await database.run(database.list_games, ctx.guild.id)
            await ctx.send(allowed_mentions=_NO_MENTIONS, embed=emb.build_games_embed(rows))
        except RulesError as e:
            _log_rules_error(ctx, e)
            await _reply_error(ctx, e.user_message)

    @gamerules.command(name="ask", description="Ask a rules question, answered from the game's rulebooks.")
    @commands.guild_only()
    @has_user_role()
    @app_commands.describe(game="The game to ask about", question="Your rules question")
    async def ask(self, ctx, game: str, *, question: str):
        # Cheap checks first, so a typo'd game gets a private error, not a public one.
        try:
            row = await database.run(rules_service.resolve_game, ctx.guild.id, game)
            if not await database.run(database.list_sources, row["id"]):
                raise RulesError(f"❌ **{row['name']}** has no rulebooks yet. "
                                 "Add one with `/gamerules addbook`.")
        except RulesError as e:
            _log_rules_error(ctx, e)
            await _reply_error(ctx, e.user_message)
            return
        try:
            await ctx.defer()
            answer = await rules_service.ask(row, question)
        except RulesError as e:
            _log_rules_error(ctx, e)
            await ctx.send(embed=emb.error_embed(e.user_message), allowed_mentions=_NO_MENTIONS)
            return
        embed = emb.build_answer_embed(row["name"], question, answer,
                                       asked_by=getattr(ctx.author, "display_name", None))
        view = None
        if answer.found:
            answer_id = await rules_service.save_answer(ctx.guild.id, row, question, answer)
            view = answer_view(answer_id, row["id"], bool(answer.citations))
        await ctx.send(embed=embed, view=view, allowed_mentions=_NO_MENTIONS)

    @gamerules.command(name="add", description="Add a new game and its rulebooks (opens a form).")
    @commands.guild_only()
    @has_user_role()
    async def add(self, ctx):
        if ctx.interaction is None:
            await ctx.send("Use `/gamerules add` — it opens a form.", allowed_mentions=_NO_MENTIONS)
            return
        await ctx.interaction.response.send_modal(AddGameModal())

    @gamerules.command(name="addbook", description="Add rulebooks to an existing game (opens a form).")
    @commands.guild_only()
    @has_user_role()
    async def addbook(self, ctx):
        if ctx.interaction is None:
            await ctx.send("Use `/gamerules addbook` — it opens a form.", allowed_mentions=_NO_MENTIONS)
            return
        try:
            rows = await database.run(database.list_games, ctx.guild.id)
        except Exception:
            logger.error("Listing games failed | guild=%s | user=%s",
                         ctx.guild.id, ctx.author.id, exc_info=True)
            raise
        if not rows:
            await ctx.send("No games yet. Add one with `/gamerules add`.",
                           allowed_mentions=_NO_MENTIONS, ephemeral=True)
            return
        await ctx.interaction.response.send_modal(AddSourceModal([r["name"] for r in rows]))

    @gamerules.command(name="books", description="List a game's rulebooks.")
    @commands.guild_only()
    @has_user_role()
    @app_commands.describe(game="The game whose rulebooks to list")
    async def books(self, ctx, game: str):
        try:
            row = await database.run(rules_service.resolve_game, ctx.guild.id, game)
            rows = await database.run(database.list_sources, row["id"])
            kinds = {r["id"]: rules_service.download_target(r)[0] for r in rows}
            await ctx.send(allowed_mentions=_NO_MENTIONS,
                           embed=emb.build_sources_embed(row["name"], rows, kinds))
        except RulesError as e:
            _log_rules_error(ctx, e)
            await _reply_error(ctx, e.user_message)

    @gamerules.command(name="download", description="Download a rulebook (pick a game, then a book).")
    @commands.guild_only()
    @has_user_role()
    async def download(self, ctx):
        if ctx.interaction is None:
            await ctx.send("Use `/gamerules download` — it opens a private picker.",
                           allowed_mentions=_NO_MENTIONS)
            return
        view = await DownloadView.for_guild(ctx.author.id, ctx.guild.id)
        await ctx.send(embed=view.intro_embed(), view=view, ephemeral=True,
                       allowed_mentions=_NO_MENTIONS)

    async def _confirm(self, ctx, title: str, prompt: str) -> bool:
        """React-to-confirm (30 s, author only). Edits the prompt to Cancelled otherwise."""
        msg = await ctx.send(allowed_mentions=_NO_MENTIONS, embed=emb.confirm_embed(
            title, f"{prompt}\n\nReact ✅ to confirm or ❌ to cancel (30 seconds)."))
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        def check(reaction, user):
            return (user == ctx.author and str(reaction.emoji) in ("✅", "❌")
                    and reaction.message.id == msg.id)

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            await msg.edit(allowed_mentions=_NO_MENTIONS,
                           embed=emb.cancelled_embed("Timed out. Nothing was changed."))
            return False
        if str(reaction.emoji) == "❌":
            await msg.edit(allowed_mentions=_NO_MENTIONS, embed=emb.cancelled_embed())
            return False
        return True

    @gamerules.command(name="removebook", description="Remove a rulebook from a game (opens a form).")
    @commands.guild_only()
    @has_user_role()
    @app_commands.describe(game="The game the rulebook belongs to")
    async def removebook(self, ctx, game: str):
        if ctx.interaction is None:
            await ctx.send("Use `/gamerules removebook` — it opens a form.",
                           allowed_mentions=_NO_MENTIONS)
            return
        # No defer: a modal must be the first response to the interaction.
        try:
            row = await database.run(rules_service.resolve_game, ctx.guild.id, game)
            sources = await database.run(database.list_sources, row["id"])
        except RulesError as e:
            _log_rules_error(ctx, e)
            await _reply_error(ctx, e.user_message)
            return
        if not sources:
            await _reply_error(ctx, f"**{row['name']}** has no rulebooks to remove.")
            return
        await ctx.interaction.response.send_modal(RemoveBookModal(row, sources))

    @gamerules.command(name="delete", description="Delete a game and all its rulebooks.")
    @commands.guild_only()
    @has_user_role()
    @app_commands.describe(game="The game to delete")
    async def delete(self, ctx, game: str):
        try:
            row = await database.run(rules_service.resolve_game, ctx.guild.id, game)
        except RulesError as e:
            _log_rules_error(ctx, e)
            await _reply_error(ctx, e.user_message)
            return
        try:
            await ctx.defer()
            sources = await database.run(database.list_sources, row["id"])
            if not await self._confirm(
                    ctx, f"Delete {row['name']}?",
                    f"This deletes **{row['name']}** and all "
                    f"{emb._plural(len(sources), 'rulebook')}. It cannot be undone."):
                return
            n = await rules_service.delete_game(row, str(ctx.author), ctx.guild.id)
            await ctx.send(allowed_mentions=_NO_MENTIONS, embed=emb.removed_embed(
                "Game deleted", f"**{row['name']}** and {emb._plural(n, 'rulebook')} were deleted."))
        except RulesError as e:
            _log_rules_error(ctx, e)
            await ctx.send(embed=emb.error_embed(e.user_message), allowed_mentions=_NO_MENTIONS)

    @ask.autocomplete("game")
    async def _ask_game_ac(self, interaction: discord.Interaction, current: str):
        return await self.game_autocomplete(interaction, current)

    @books.autocomplete("game")
    async def _books_game_ac(self, interaction: discord.Interaction, current: str):
        return await self.game_autocomplete(interaction, current)

    @removebook.autocomplete("game")
    async def _removebook_game_ac(self, interaction: discord.Interaction, current: str):
        return await self.game_autocomplete(interaction, current)

    @delete.autocomplete("game")
    async def _delete_game_ac(self, interaction: discord.Interaction, current: str):
        return await self.game_autocomplete(interaction, current)


async def setup(bot):
    await bot.add_cog(Rules(bot))
