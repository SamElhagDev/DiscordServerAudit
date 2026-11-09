import discord

from utils.rules_store import RulesAnswer

_MAX_FIELDS = 25
_EMBED_TOTAL = 6000
_MIN_SNIPPET = 20


def _trunc(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_answer_embed(game_name: str, question: str, answer: RulesAnswer) -> discord.Embed:
    if not answer.found:
        return discord.Embed(
            description=_trunc(
                f"🤷 The uploaded rules for **{game_name}** don't cover that. "
                "Try rephrasing, or add a source that does.", 4096),
            color=discord.Color.greyple(),
        )
    title = _trunc(f"📖 {game_name}", 256)
    author = _trunc(f"Q: {question}", 256)
    footer = "Answers come only from this game's uploaded rulebooks."
    fixed = len(title) + len(author) + len(footer)
    desc = _trunc(answer.text, 4096)
    # Discord caps the whole embed at 6000 chars: keep the description, fit
    # citations into the remainder (drop from the end, then shorten), and only
    # shorten the description if even the fixed parts + description don't fit.
    desc = _trunc(desc, max(_EMBED_TOTAL - fixed, 1))
    room = _EMBED_TOTAL - fixed - len(desc)
    fields = []
    for i, c in enumerate(answer.citations[:_MAX_FIELDS], start=1):
        name = _trunc(f"Source {i}: {c.title}", 256)
        value = _trunc(c.snippet, 1024) or "​"
        if len(name) + len(value) > room:
            value = _trunc(value, room - len(name)) if room - len(name) >= _MIN_SNIPPET else None
            if value is None:
                break
        fields.append((name, value))
        room -= len(name) + len(value)
    embed = discord.Embed(title=title, description=desc, color=discord.Color.dark_gold())
    embed.set_author(name=author)
    for name, value in fields:
        embed.add_field(name=name, value=value, inline=False)
    embed.set_footer(text=footer)
    return embed


def build_games_embed(rows: list) -> discord.Embed:
    if not rows:
        desc = "No games yet. Add one with `/rules addgame`."
    else:
        desc = "\n".join(f"**{r['name']}** — {r['source_count']} source(s)" for r in rows)
    return discord.Embed(title="🎲 Games", description=_trunc(desc, 4096),
                         color=discord.Color.dark_gold())


def build_sources_embed(game_name: str, rows: list) -> discord.Embed:
    if not rows:
        desc = "No sources yet. Add one with `/rules addsource`."
    else:
        desc = "\n".join(
            f"`#{r['id']}` {r['display_name']} — {r['size_bytes'] / 1048576:.1f} MB ({r['origin']})"
            for r in rows)
    return discord.Embed(title=_trunc(f"📚 Sources for {game_name}", 256),
                         description=_trunc(desc, 4096), color=discord.Color.dark_gold())
