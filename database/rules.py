"""Game rules Q&A persistence: games (one File Search store each) and their sources.

Import via ``database.<name>``.
"""
import sqlite3
from .connection import get_conn
from .common import _now


class GameExistsError(Exception):
    """A game with this (case/whitespace-insensitive) name already exists in the guild."""


def game_name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def create_game(guild_id: int, name: str, store_name: str, created_by: int) -> int:
    display = " ".join(name.split())
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO rules_games "
                "(guild_id, name, name_key, store_name, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, display, game_name_key(name), store_name, created_by, _now()),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError as exc:
        raise GameExistsError(display) from exc


def get_game(guild_id: int, name: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM rules_games WHERE guild_id = ? AND name_key = ?",
            (guild_id, game_name_key(name)),
        ).fetchone()


def list_games(guild_id: int) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT g.*, (SELECT COUNT(*) FROM rules_sources s WHERE s.game_id = g.id) "
            "AS source_count FROM rules_games g WHERE g.guild_id = ? ORDER BY g.name_key",
            (guild_id,),
        ).fetchall()


def search_game_names(guild_id: int, query: str, limit: int = 25) -> list[str]:
    key = game_name_key(query)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name, name_key FROM rules_games WHERE guild_id = ? ORDER BY name_key",
            (guild_id,),
        ).fetchall()
    return [r["name"] for r in rows if key in r["name_key"]][:limit]


def add_source(game_id: int, display_name: str, origin: str, origin_ref: str,
               document_name: str, size_bytes: int, added_by: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO rules_sources (game_id, display_name, origin, origin_ref, "
            "document_name, size_bytes, added_by, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (game_id, display_name, origin, origin_ref, document_name, size_bytes,
             added_by, _now()),
        )
        return cur.lastrowid


def list_sources(game_id: int) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM rules_sources WHERE game_id = ? ORDER BY id", (game_id,)
        ).fetchall()


def get_source(game_id: int, source_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM rules_sources WHERE id = ? AND game_id = ?", (source_id, game_id)
        ).fetchone()


def delete_source(source_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute("DELETE FROM rules_sources WHERE id = ?", (source_id,)).rowcount > 0


def delete_game(game_id: int) -> int:
    with get_conn() as conn:
        removed = conn.execute(
            "DELETE FROM rules_sources WHERE game_id = ?", (game_id,)
        ).rowcount
        conn.execute("DELETE FROM rules_games WHERE id = ?", (game_id,))
        return removed
