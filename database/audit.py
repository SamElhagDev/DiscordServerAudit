"""Audit runs, findings, bulk-task log, and scheduler state.

Split out of the former single-file database.py; import via ``database.<name>``.
"""
import datetime
import logging
from .connection import get_conn
from .common import _now

logger = logging.getLogger(__name__)


def log_bulk_task(task_type: str, performed_by: str, guild_id: int, details: str):
    logger.debug("Bulk task logged: type=%r guild=%s user=%s", task_type, guild_id, performed_by)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO bulk_task_log (task_type, performed_by, guild_id, details, performed_at) VALUES (?, ?, ?, ?, ?)",
            (task_type, performed_by, guild_id, details, _now()),
        )


def start_audit_run(audit_type: str, guild_id: int, triggered_by: str = "scheduler") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO audit_runs (audit_type, run_at, guild_id, triggered_by) VALUES (?, ?, ?, ?)",
            (audit_type, _now(), guild_id, triggered_by),
        )
        run_id = cur.lastrowid
    logger.debug("Audit run started: id=%d type=%r guild=%s triggered_by=%r", run_id, audit_type, guild_id, triggered_by)
    return run_id


def add_finding(run_id: int, severity: str, category: str, description: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_findings (run_id, severity, category, description) VALUES (?, ?, ?, ?)",
            (run_id, severity, category, description),
        )


def finalize_audit_run(run_id: int, finding_count: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE audit_runs SET finding_count = ? WHERE id = ?",
            (finding_count, run_id),
        )
    logger.debug("Audit run %d finalised: %d findings persisted", run_id, finding_count)


def get_last_run(key: str):
    with get_conn() as conn:
        row = conn.execute("SELECT last_run FROM scheduler_state WHERE key = ?", (key,)).fetchone()
        if row:
            ts = datetime.datetime.fromisoformat(row["last_run"])
            # Rows written before the tz-aware migration are naive — treat as UTC
            # so arithmetic against tz-aware "now" doesn't raise.
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.timezone.utc)
            return ts
        return None


def set_last_run(key: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO scheduler_state (key, last_run) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET last_run = excluded.last_run",
            (key, _now()),
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
