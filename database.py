import sqlite3
import datetime
from contextlib import contextmanager

import os
DB_PATH = os.environ.get("DB_PATH", "bot.db")

def init_db():
    """Initialize all tables."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS audit_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_type TEXT NOT NULL,         -- 'security' or 'server'
                run_at TEXT NOT NULL,
                guild_id INTEGER NOT NULL,
                finding_count INTEGER DEFAULT 0,
                triggered_by TEXT DEFAULT 'scheduler'  -- 'scheduler' or user id
            );

            CREATE TABLE IF NOT EXISTS audit_findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES audit_runs(id),
                severity TEXT NOT NULL,           -- 'critical', 'warning', 'info'
                category TEXT NOT NULL,           -- e.g. 'permissions', 'dead_channel'
                description TEXT NOT NULL,
                resolved INTEGER DEFAULT 0        -- 0 = open, 1 = resolved
            );

            CREATE TABLE IF NOT EXISTS bulk_task_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,          -- e.g. 'bulk_delete', 'bulk_role_assign'
                performed_by TEXT NOT NULL,       -- Discord user id
                guild_id INTEGER NOT NULL,
                details TEXT,                     -- JSON blob of task parameters
                performed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scheduler_state (
                key TEXT PRIMARY KEY,
                last_run TEXT NOT NULL
            );
        """)

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def log_bulk_task(task_type: str, performed_by: str, guild_id: int, details: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO bulk_task_log (task_type, performed_by, guild_id, details, performed_at) VALUES (?, ?, ?, ?, ?)",
            (task_type, performed_by, guild_id, details, _now())
        )

def start_audit_run(audit_type: str, guild_id: int, triggered_by: str = "scheduler") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO audit_runs (audit_type, run_at, guild_id, triggered_by) VALUES (?, ?, ?, ?)",
            (audit_type, _now(), guild_id, triggered_by)
        )
        return cur.lastrowid

def add_finding(run_id: int, severity: str, category: str, description: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_findings (run_id, severity, category, description) VALUES (?, ?, ?, ?)",
            (run_id, severity, category, description)
        )

def finalize_audit_run(run_id: int, finding_count: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE audit_runs SET finding_count = ? WHERE id = ?",
            (finding_count, run_id)
        )

def get_last_run(key: str):
    with get_conn() as conn:
        row = conn.execute("SELECT last_run FROM scheduler_state WHERE key = ?", (key,)).fetchone()
        if row:
            return datetime.datetime.fromisoformat(row["last_run"])
        return None

def set_last_run(key: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO scheduler_state (key, last_run) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET last_run = excluded.last_run",
            (key, _now())
        )

def get_recent_findings(guild_id: int, audit_type: str, limit: int = 20):
    with get_conn() as conn:
        return conn.execute("""
            SELECT f.severity, f.category, f.description, r.run_at
            FROM audit_findings f
            JOIN audit_runs r ON f.run_id = r.id
            WHERE r.guild_id = ? AND r.audit_type = ?
            ORDER BY r.run_at DESC
            LIMIT ?
        """, (guild_id, audit_type, limit)).fetchall()

def _now() -> str:
    return datetime.datetime.utcnow().isoformat()
