"""Orchestration for game rules: create game, add source, ask.

Sits between the DB, input validation, and the Gemini File Search wrapper. The
cog stays presentation-only. ``store`` is injectable so tests need no network.
"""
import asyncio
import json
import logging
import sqlite3
from dataclasses import asdict

import config
import database
from utils import rules_files, rules_store
from utils.rules_errors import RulesError
from utils.rules_ingest import validate_game_name, validate_question

logger = logging.getLogger(__name__)


def _duplicate(name: str) -> RulesError:
    return RulesError(f"❌ A game named **{name}** already exists.")


def resolve_game(guild_id: int, raw_name: str) -> sqlite3.Row:
    name = " ".join((raw_name or "").split())
    game = database.get_game(guild_id, name)
    if game is None:
        raise RulesError(f"❌ No game named **{name}**. See `/gamerules list`.")
    return game


async def create_game(guild_id: int, raw_name: str, user_id: int, *,
                      store=rules_store) -> sqlite3.Row:
    name = validate_game_name(raw_name)
    if await database.run(database.get_game, guild_id, name) is not None:
        raise _duplicate(name)

    store_name = await store.create_store(f"{guild_id}:{name}")
    try:
        game_id = await database.run(database.create_game, guild_id, name, store_name, user_id)
    except Exception as exc:
        logger.error("Game insert failed guild=%s user=%s name=%r: %s",
                     guild_id, user_id, name, exc, exc_info=True)
        try:
            await store.delete_store(store_name)
        except Exception:
            logger.error("Failed to clean up store %s guild=%s user=%s",
                         store_name, guild_id, user_id, exc_info=True)
        if isinstance(exc, database.GameExistsError):
            raise _duplicate(name) from exc
        raise
    logger.info("Game created guild_id=%s game_id=%s user_id=%s name=%r",
                guild_id, game_id, user_id, name)
    return await database.run(database.get_game, guild_id, name)


async def add_source(game: sqlite3.Row, data: bytes, filename: str, mime: str,
                     origin: str, origin_ref: str, user_id: int, *,
                     store=rules_store) -> int:
    document_name = await store.upload_document(
        game["store_name"], data, filename, mime,
        config.get("rules.index_timeout_seconds", 300))
    try:
        source_id = await database.run(
            database.add_source, game["id"], filename, origin, origin_ref,
            document_name, len(data), user_id)
    except Exception as exc:
        logger.error("Source insert failed guild_id=%s game_id=%s user_id=%s: %s",
                     game["guild_id"], game["id"], user_id, exc, exc_info=True)
        try:
            await store.delete_document(document_name)
        except Exception:
            logger.error("Failed to clean up document %s guild_id=%s game_id=%s user_id=%s",
                         document_name, game["guild_id"], game["id"], user_id, exc_info=True)
        raise
    # Keep a local copy for /gamerules download. A failure here only costs the
    # download: the rulebook is already indexed and answers still use it.
    try:
        rel = await asyncio.to_thread(rules_files.save, game["id"], source_id, filename, data)
        await database.run(database.set_source_file, source_id, rel)
    except Exception:
        logger.error("Saving rulebook copy failed guild_id=%s game_id=%s source_id=%s",
                     game["guild_id"], game["id"], source_id, exc_info=True)
    count = len(await database.run(database.list_sources, game["id"]))
    logger.info("Source added guild_id=%s game_id=%s user_id=%s source_id=%s count=%s",
                game["guild_id"], game["id"], user_id, source_id, count)
    return count


async def ask(game: sqlite3.Row, raw_question: str, *, store=rules_store):
    question = validate_question(raw_question, config.get("rules.max_question_chars", 500))
    sources = await database.run(database.list_sources, game["id"])
    if not sources:
        raise RulesError(
            f"❌ **{game['name']}** has no rulebooks yet. Add one with `/gamerules addbook`.")
    logger.info("Rules question guild_id=%s game_id=%s sources=%s",
                game["guild_id"], game["id"], len(sources))
    return await store.ask(
        game["store_name"], game["name"], question,
        config.get("rules.model", "gemini-3.1-pro-preview"),
        config.get("rules.answer_timeout_seconds", 90),
        config.get("rules.max_citations", 3),
        thinking_level=config.get("rules.thinking_level", "low"))


async def remove_source(game: sqlite3.Row, source_id: int, actor: str, guild_id: int, *,
                        store=rules_store) -> str:
    src = await database.run(database.get_source, game["id"], source_id)
    if src is None:
        raise RulesError(f"❌ That rulebook is no longer in **{game['name']}** — it may already have been removed.")
    # Gemini first: if it fails the row stays so the user can retry.
    await store.delete_document(src["document_name"])
    await database.run(database.delete_source, source_id)
    await asyncio.to_thread(rules_files.delete, src["file_path"])
    await database.run(database.log_bulk_task, "rules_remove_source", actor, guild_id,
                       f"game={game['name']} source=#{source_id} {src['display_name']}")
    logger.info("Source removed guild_id=%s game_id=%s source_id=%s", guild_id, game["id"], source_id)
    return src["display_name"]


async def delete_game(game: sqlite3.Row, actor: str, guild_id: int, *,
                      store=rules_store) -> int:
    await store.delete_store(game["store_name"])
    n = await database.run(database.delete_game, game["id"])
    await asyncio.to_thread(rules_files.delete_game_dir, game["id"])
    await database.run(database.log_bulk_task, "rules_delete_game", actor, guild_id,
                       f"game={game['name']} sources={n}")
    logger.info("Game deleted guild_id=%s game_id=%s sources=%s", guild_id, game["id"], n)
    return n


async def save_answer(guild_id: int, game: sqlite3.Row, question: str, answer) -> int | None:
    """Persist an answer's citations for its "Show sources" button.

    Returns the answer id, or None if saving failed (the answer is still posted,
    just without the button).
    """
    try:
        payload = json.dumps([asdict(c) for c in answer.citations])
        return await database.run(database.save_answer, guild_id, game["id"], question, payload)
    except Exception:
        logger.error("Saving answer citations failed guild_id=%s game_id=%s",
                     guild_id, game["id"], exc_info=True)
        return None


async def load_answer(guild_id: int, answer_id: int):
    """(question, game_id, [Citation]) for a saved answer, or None if it expired."""
    row = await database.run(database.get_answer, guild_id, answer_id)
    if row is None:
        return None
    cites = [rules_store.Citation(**c) for c in json.loads(row["citations"])]
    return row["question"], row["game_id"], cites


def download_target(src) -> tuple[str, str | None]:
    """How a rulebook can be handed back: ("file", path), ("link", url) or ("none", None)."""
    path = rules_files.existing_path(src["file_path"])
    if path:
        return "file", path
    if src["origin"] == "url" and src["origin_ref"]:
        return "link", src["origin_ref"]
    return "none", None
