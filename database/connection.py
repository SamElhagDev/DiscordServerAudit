"""Thread-local SQLite connections, WAL setup, and the async worker-thread hop.

Split out of the former single-file database.py; import via ``database.<name>``.
"""
import asyncio
import os
import sqlite3
import threading
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


DB_PATH = os.environ.get("DiscordServerAudit_DB_PATH", "bot.db")


_local = threading.local()


def _ensure_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
        logger.debug("Opened thread-local DB connection to %s (thread=%s)",
                     DB_PATH, threading.current_thread().name)
    return conn


def close_db():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
        logger.info("Database connection closed (thread=%s)", threading.current_thread().name)


async def run(fn, *args, **kwargs):
    """Execute a synchronous DB function in a worker thread so it doesn't block
    the asyncio event loop. Each worker thread uses its own thread-local
    connection, so concurrent calls are safe under WAL mode.

    Usage: await database.run(database.log_message_event, guild_id, ...)
    """
    return await asyncio.to_thread(fn, *args, **kwargs)


_fts5_available = None


def fts5_available() -> bool:
    """True if this SQLite build supports FTS5 (cached probe)."""
    global _fts5_available
    if _fts5_available is None:
        try:
            probe = sqlite3.connect(":memory:")
            probe.execute("CREATE VIRTUAL TABLE _fts_probe USING fts5(x)")
            probe.close()
            _fts5_available = True
            logger.info("SQLite FTS5 available — fact-check relevance tier enabled")
        except Exception:
            _fts5_available = False
            logger.warning("SQLite FTS5 unavailable — fact-check relevance tier disabled")
    return _fts5_available


@contextmanager
def get_conn():
    conn = _ensure_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        logger.error("Database transaction rolled back", exc_info=True)
        raise
