"""Interactive pieces of the game-rules feature.

- DownloadView: a private two-step picker (game, then rulebook) that sends the file.
- SourcesButton / DownloadButton: buttons on a posted answer. They are
  DynamicItems whose custom_id carries the answer/game id, so they keep working
  after the bot restarts (registered in the Rules cog's cog_load).
"""
import logging
import os
import re

import discord

import database
from utils import rules_embeds as emb
from utils import rules_service
from utils.permissions import member_has_user_role

logger = logging.getLogger(__name__)

_NO_MENTIONS = discord.AllowedMentions.none()
_NOT_ALLOWED = "You need the user role to use this."
_MAX_OPTIONS = 25
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _allowed(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and member_has_user_role(interaction.user, interaction.guild)


async def _private(interaction: discord.Interaction, embed: discord.Embed, **kwargs) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=_NO_MENTIONS, **kwargs)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True,
                                                allowed_mentions=_NO_MENTIONS, **kwargs)


def download_filename(display_name: str, path: str) -> str:
    """A safe attachment name: the rulebook's name, keeping the stored file's extension."""
    ext = os.path.splitext(path)[1]
    base = _UNSAFE.sub("_", os.path.splitext(os.path.basename(display_name or ""))[0]).strip(" .")
    return f"{(base or 'rulebook')[:100]}{ext}"


# ---------------------------------------------------------------------------
# Download picker
# ---------------------------------------------------------------------------

class _GameSelect(discord.ui.Select):
    def __init__(self, games: list, selected_id: int | None):
        options = [discord.SelectOption(
            label=g["name"][:100], value=str(g["id"]), emoji="🎲",
            description=f"{g['source_count']} rulebook(s)"[:100],
            default=g["id"] == selected_id) for g in games[:_MAX_OPTIONS]]
        super().__init__(placeholder="Choose a game", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.show_game(interaction, int(self.values[0]))


class _BookSelect(discord.ui.Select):
    def __init__(self, books: list, kinds: dict):
        options = []
        for b in books[:_MAX_OPTIONS]:
            kind = kinds.get(b["id"], "none")
            options.append(discord.SelectOption(
                label=b["display_name"][:100], value=str(b["id"]), emoji=emb.KIND_ICON[kind],
                description=f"{emb._mb(b['size_bytes'])} · {emb.KIND_LABEL[kind]}"[:100]))
        super().__init__(placeholder="Choose a rulebook to download", options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.send_book(interaction, int(self.values[0]))


class DownloadView(discord.ui.View):
    """Private picker owned by one user. Build with `for_guild` or `for_game`."""

    def __init__(self, owner_id: int, guild_id: int, games: list | None = None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.games = games  # None when opened for a single game (answer button)
        self.game = None

    # -- construction -------------------------------------------------------

    @classmethod
    async def for_guild(cls, owner_id: int, guild_id: int):
        games = await database.run(database.list_games, guild_id)
        view = cls(owner_id, guild_id, list(games))
        if games:
            view.add_item(_GameSelect(view.games, None))
        return view

    @classmethod
    async def for_game(cls, owner_id: int, guild_id: int, game):
        view = cls(owner_id, guild_id, None)
        await view._load_game(game)
        return view

    def intro_embed(self) -> discord.Embed:
        if self.games is not None and not self.games:
            return emb.info_embed("No games yet. Add one with `/gamerules add`.")
        if self.game is None:
            text = "Choose a game, then the rulebook you want."
            if len(self.games) > _MAX_OPTIONS:
                text += f"\nShowing the first {_MAX_OPTIONS} of {len(self.games)} games."
            return discord.Embed(title="📥 Download a rulebook", description=text, color=emb.GOLD)
        if not self.books:
            return emb.info_embed(f"**{self.game['name']}** has no rulebooks yet. "
                                  "Add one with `/gamerules addbook`.")
        text = f"Choose a rulebook from **{self.game['name']}**."
        if len(self.books) > _MAX_OPTIONS:
            text += f"\nShowing the first {_MAX_OPTIONS} of {len(self.books)}."
        return discord.Embed(title="📥 Download a rulebook", description=text, color=emb.GOLD)

    async def _load_game(self, game) -> None:
        self.game = game
        self.books = list(await database.run(database.list_sources, game["id"]))
        self.kinds = {b["id"]: rules_service.download_target(b)[0] for b in self.books}
        self.clear_items()
        if self.games is not None:
            self.add_item(_GameSelect(self.games, game["id"]))
        if self.books:
            self.add_item(_BookSelect(self.books, self.kinds))

    # -- interaction --------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or not _allowed(interaction):
            await _private(interaction, emb.error_embed(_NOT_ALLOWED))
            return False
        return True

    async def show_game(self, interaction: discord.Interaction, game_id: int) -> None:
        game = await database.run(database.get_game_by_id, self.guild_id, game_id)
        if game is None:
            await _private(interaction, emb.error_embed("That game no longer exists."))
            return
        await self._load_game(game)
        await interaction.response.edit_message(embed=self.intro_embed(), view=self)

    async def send_book(self, interaction: discord.Interaction, source_id: int) -> None:
        src = await database.run(database.get_source, self.game["id"], source_id)
        if src is None:
            await _private(interaction, emb.error_embed(
                f"That rulebook is no longer in **{self.game['name']}**."))
            return
        kind, target = rules_service.download_target(src)
        logger.info("Rulebook download | guild=%s | user=%s | game_id=%s | source_id=%s | kind=%s",
                    self.guild_id, interaction.user.id, self.game["id"], source_id, kind)
        if kind == "link":
            await _private(interaction, emb.info_embed(
                f"🔗 **{src['display_name']}** was added from a link:\n{target}"))
            return
        if kind == "none":
            await _private(interaction, emb.error_embed(
                f"**{src['display_name']}** was added before the bot kept copies, so there is "
                "no file to send. Re-add it with `/gamerules addbook` to make it downloadable."))
            return
        size = os.path.getsize(target)
        limit = getattr(interaction.guild, "filesize_limit", 10 * 1048576)
        if size > limit:
            await _private(interaction, emb.error_embed(
                f"**{src['display_name']}** is {emb._mb(size)}, over this server's "
                f"{emb._mb(limit)} upload limit, so Discord won't accept it."))
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        file = discord.File(target, filename=download_filename(src["display_name"], target))
        await interaction.followup.send(
            embed=emb.success_embed("Here's your rulebook", f"**{src['display_name']}** · "
                                    f"{self.game['name']} · {emb._mb(size)}"),
            file=file, ephemeral=True, allowed_mentions=_NO_MENTIONS)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        logger.error("Download picker error | guild=%s | user=%s", self.guild_id,
                     interaction.user.id, exc_info=error)
        await _private(interaction, emb.error_embed("Something went wrong. The error has been logged."))


# ---------------------------------------------------------------------------
# Persistent answer buttons
# ---------------------------------------------------------------------------

class SourcesButton(discord.ui.DynamicItem[discord.ui.Button], template=r"gamerules:src:(?P<aid>\d+)"):
    def __init__(self, answer_id: int):
        super().__init__(discord.ui.Button(
            label="Show sources", emoji="📖", style=discord.ButtonStyle.secondary,
            custom_id=f"gamerules:src:{answer_id}"))
        self.answer_id = answer_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["aid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _allowed(interaction):
            await _private(interaction, emb.error_embed(_NOT_ALLOWED))
            return
        saved = await rules_service.load_answer(interaction.guild.id, self.answer_id)
        if saved is None:
            await _private(interaction, emb.error_embed(
                "The sources for this answer have expired. Ask the question again."))
            return
        question, game_id, citations = saved
        game = await database.run(database.get_game_by_id, interaction.guild.id, game_id)
        name = game["name"] if game else "Deleted game"
        await _private(interaction, emb.build_sources_detail_embed(name, question, citations))


class DownloadButton(discord.ui.DynamicItem[discord.ui.Button], template=r"gamerules:dl:(?P<gid>\d+)"):
    def __init__(self, game_id: int):
        super().__init__(discord.ui.Button(
            label="Download rulebook", emoji="📥", style=discord.ButtonStyle.secondary,
            custom_id=f"gamerules:dl:{game_id}"))
        self.game_id = game_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["gid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _allowed(interaction):
            await _private(interaction, emb.error_embed(_NOT_ALLOWED))
            return
        game = await database.run(database.get_game_by_id, interaction.guild.id, self.game_id)
        if game is None:
            await _private(interaction, emb.error_embed("That game no longer exists."))
            return
        view = await DownloadView.for_game(interaction.user.id, interaction.guild.id, game)
        await _private(interaction, view.intro_embed(), view=view)


def answer_view(answer_id: int | None, game_id: int, has_sources: bool) -> discord.ui.View | None:
    """Buttons under a posted answer (persistent; no timeout)."""
    view = discord.ui.View(timeout=None)
    if answer_id is not None and has_sources:
        view.add_item(SourcesButton(answer_id))
    view.add_item(DownloadButton(game_id))
    return view
