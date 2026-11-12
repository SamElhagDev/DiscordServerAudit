"""Embeds for the game-rules feature, in one consistent style.

Colours: gold = normal content, green = success, orange = needs attention or
confirmation, red = error, grey = nothing found / cancelled.
"""
import discord

from utils.rules_store import RulesAnswer

GOLD = discord.Color.dark_gold()
GREEN = discord.Color.green()
ORANGE = discord.Color.orange()
RED = discord.Color.red()
GREY = discord.Color.greyple()

_MAX_FIELDS = 25
_EMBED_TOTAL = 6000
_FIELD_VALUE = 1024
_DESC = 4096
_MIN_SNIPPET = 20
_MB = 1048576

# How a rulebook can be downloaded (see rules_service.download_target).
KIND_ICON = {"file": "📄", "link": "🔗", "none": "⚠️"}
KIND_LABEL = {"file": "ready to download", "link": "link", "none": "re-add to download"}


def _trunc(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _mb(size_bytes: int) -> str:
    mb = (size_bytes or 0) / _MB
    return f"{mb:.1f} MB" if mb >= 0.1 else "<0.1 MB"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _quote_block(snippets: list, budget: int) -> str:
    """Render a book's passages as "> " quote lines, as many as fit in `budget`.

    The first quote is shortened rather than dropped so every listed book shows
    some source wording; later quotes that don't fit are left out.
    """
    lines: list[str] = []
    used = 0
    for s in snippets:
        line = f"> {s}"
        cost = len(line) + (1 if lines else 0)
        if used + cost <= budget:
            lines.append(line)
            used += cost
        elif not lines:
            lines.append(_trunc(line, budget))
            break
        else:
            break
    return "\n".join(lines) or "​"


def _footer(game_name: str, asked_by: str | None) -> str:
    return _trunc(f"🎲 {game_name}" + (f" · asked by {asked_by}" if asked_by else ""), 2048)


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------

def build_answer_embed(game_name: str, question: str, answer: RulesAnswer,
                       asked_by: str | None = None) -> discord.Embed:
    """The public answer card: question, one answer, the rulebooks it came from.

    Quotes are not shown here; the card's "Show sources" button reveals them.
    """
    title = _trunc(f"❓ {question}", 256)
    footer = _footer(game_name, asked_by)
    if not answer.found:
        embed = discord.Embed(
            title=title, color=GREY,
            description=(f"🤷 The rulebooks for **{_trunc(game_name, 200)}** don't cover that.\n"
                         "Try rephrasing the question, or add a rulebook that does."))
        embed.set_footer(text=footer)
        return embed
    books = " · ".join(c.title for c in answer.citations)
    sources = _trunc(f"📚 {books}", _FIELD_VALUE) if books else ""
    room = _EMBED_TOTAL - len(title) - len(footer) - len(sources) - len("Sources")
    embed = discord.Embed(title=title, color=GOLD,
                          description=_trunc(answer.text, max(min(_DESC, room), 1)))
    if sources:
        embed.add_field(name="Sources", value=sources, inline=False)
    embed.set_footer(text=footer)
    return embed


def build_sources_detail_embed(game_name: str, question: str, citations: list) -> discord.Embed:
    """The quotes behind an answer, one field per rulebook (shown privately)."""
    title = "📖 Sources"
    author = _trunc(f"❓ {question}", 256)
    footer = _footer(game_name, None) + " · quoted from the uploaded rulebooks"
    footer = _trunc(footer, 2048)
    room = _EMBED_TOTAL - len(title) - len(author) - len(footer)
    embed = discord.Embed(title=title, color=GOLD)
    embed.set_author(name=author)
    if not citations:
        embed.description = "No quoted passages were saved for this answer."
    for c in citations[:_MAX_FIELDS]:
        name = _trunc(f"📄 {c.title}", 256)
        budget = min(_FIELD_VALUE, room - len(name))
        if budget < _MIN_SNIPPET:
            break
        value = _quote_block(c.snippets, budget)
        embed.add_field(name=name, value=value, inline=False)
        room -= len(name) + len(value)
    embed.set_footer(text=footer)
    return embed


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------

def build_games_embed(rows: list) -> discord.Embed:
    embed = discord.Embed(title="🎲 Game rules", color=GOLD)
    if not rows:
        embed.description = "No games yet. Add one with `/gamerules add`."
        return embed
    lines = [f"**{r['name']}** · {_plural(r['source_count'], 'rulebook')}" for r in rows]
    embed.description = _trunc("\n".join(lines), _DESC)
    embed.set_footer(text=f"{_plural(len(rows), 'game')} · /gamerules ask to ask a question · "
                          "/gamerules download to get a rulebook")
    return embed


def build_sources_embed(game_name: str, rows: list, kinds: dict | None = None) -> discord.Embed:
    """A game's rulebooks. `kinds` maps source id -> "file" | "link" | "none"."""
    embed = discord.Embed(title=_trunc(f"📚 {game_name}", 256), color=GOLD)
    if not rows:
        embed.description = "No rulebooks yet. Add one with `/gamerules addbook`."
        return embed
    kinds = kinds or {}
    lines = []
    for r in rows:
        kind = kinds.get(r["id"], "file")
        origin = "link" if r["origin"] == "url" else "upload"
        lines.append(f"{KIND_ICON[kind]} **{_trunc(r['display_name'], 150)}**\n"
                     f" {_mb(r['size_bytes'])} · {origin}"
                     + (f" · {KIND_LABEL[kind]}" if kind == "none" else ""))
    embed.description = _trunc("\n".join(lines), _DESC)
    total = sum(r["size_bytes"] or 0 for r in rows)
    embed.set_footer(text=f"{_plural(len(rows), 'rulebook')} · {_mb(total)} · "
                          "/gamerules download to get a copy")
    return embed


# ---------------------------------------------------------------------------
# Form results, confirmations, notices
# ---------------------------------------------------------------------------

def build_progress_embed(text: str) -> discord.Embed:
    return discord.Embed(description=_trunc(f"⏳ {text}", _DESC), color=GREY)


def build_submission_embed(result) -> discord.Embed:
    """Summary after the add / addbook forms (a rules_forms.SubmissionResult)."""
    ok = sum(1 for i in result.items if i.ok)
    failed = len(result.items) - ok
    color = GREEN if failed == 0 else (ORANGE if ok else RED)
    status = ("All added" if failed == 0 else
              f"{ok} added, {failed} failed" if ok else "Nothing was added")
    embed = discord.Embed(title=_trunc(f"📚 {result.game_name}", 256), color=color)
    lines = []
    for i in result.items:
        lines.append(f"✅ {i.label}" if i.ok else f"❌ {i.label}\n {i.detail}")
    text = "\n".join(lines) or "No files or links were submitted."
    if len(text) > _DESC:
        keep, used = [], 0
        for ln in lines:
            if used + len(ln) + 1 > _DESC - 40:
                break
            keep.append(ln)
            used += len(ln) + 1
        text = "\n".join(keep + [f"…and {len(lines) - len(keep)} more"])
    embed.description = text
    embed.set_footer(text=f"{status} · {_plural(result.total, 'rulebook')} in total")
    return embed


def success_embed(title: str, text: str = "") -> discord.Embed:
    return discord.Embed(title=_trunc(f"✅ {title}", 256), description=_trunc(text, _DESC) or None,
                         color=GREEN)


def removed_embed(title: str, text: str = "") -> discord.Embed:
    return discord.Embed(title=_trunc(f"🗑️ {title}", 256), description=_trunc(text, _DESC) or None,
                         color=GREEN)


def confirm_embed(title: str, text: str) -> discord.Embed:
    return discord.Embed(title=_trunc(f"⚠️ {title}", 256), description=_trunc(text, _DESC),
                         color=ORANGE)


def cancelled_embed(text: str = "Nothing was changed.") -> discord.Embed:
    return discord.Embed(title="Cancelled", description=text, color=GREY)


def error_embed(message: str) -> discord.Embed:
    """Error replies. Accepts RulesError text with or without its leading ❌."""
    text = (message or "Something went wrong.").removeprefix("❌").strip()
    return discord.Embed(description=_trunc(f"❌ {text}", _DESC), color=RED)


def info_embed(text: str) -> discord.Embed:
    return discord.Embed(description=_trunc(text, _DESC), color=GOLD)
