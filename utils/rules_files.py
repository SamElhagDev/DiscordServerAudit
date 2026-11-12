"""Local copies of uploaded rulebooks, so /gamerules download can return them.

Gemini File Search keeps only an index, not a downloadable file, so the bot
saves its own copy. Paths on disk are built from numeric ids and an allowed
extension only (never the uploader's filename), and are stored relative to the
storage root so the folder can be moved.
"""
import logging
import os
import shutil

import config
from utils.rules_ingest import ALLOWED_TYPES

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def storage_root() -> str:
    root = config.get("rules.storage_dir", "rulebooks") or "rulebooks"
    if not os.path.isabs(root):
        base = os.environ.get("DiscordServerAudit_APP_ROOT", _PROJECT_ROOT)
        root = os.path.join(base, root)
    return os.path.abspath(root)


def _ext(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    return ext if ext in ALLOWED_TYPES else ".bin"


def relative_path(game_id: int, source_id: int, filename: str) -> str:
    return f"{int(game_id)}/{int(source_id)}{_ext(filename)}"


def resolve(rel: str | None) -> str | None:
    """Absolute path for a stored relative path, or None if it escapes the root."""
    if not rel:
        return None
    root = storage_root()
    path = os.path.abspath(os.path.join(root, rel))
    if os.path.commonpath([root, path]) != root:
        logger.error("Rulebook path escapes storage root: %r", rel)
        return None
    return path


def save(game_id: int, source_id: int, filename: str, data: bytes) -> str:
    """Write the file atomically; returns the relative path to store in the DB."""
    rel = relative_path(game_id, source_id, filename)
    path = resolve(rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    logger.info("Rulebook saved game_id=%s source_id=%s bytes=%d path=%s",
                game_id, source_id, len(data), rel)
    return rel


def existing_path(rel: str | None) -> str | None:
    path = resolve(rel)
    return path if path and os.path.isfile(path) else None


def delete(rel: str | None) -> None:
    path = resolve(rel)
    if not path:
        return
    try:
        os.remove(path)
        logger.info("Rulebook file deleted path=%s", rel)
    except FileNotFoundError:
        logger.debug("Rulebook file already gone path=%s", rel)
    except OSError:
        logger.error("Failed to delete rulebook file path=%s", rel, exc_info=True)


def delete_game_dir(game_id: int) -> None:
    path = resolve(str(int(game_id)))
    if not path or not os.path.isdir(path):
        return
    try:
        shutil.rmtree(path)
        logger.info("Rulebook folder deleted game_id=%s", game_id)
    except OSError:
        logger.error("Failed to delete rulebook folder game_id=%s", game_id, exc_info=True)
