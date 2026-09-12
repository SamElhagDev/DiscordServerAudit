"""Timestamp formatting and small shared computations.

Split out of the former single-file database.py; import via ``database.<name>``.
"""
import datetime
import logging

logger = logging.getLogger(__name__)


def _now() -> str:
    # No +00:00 suffix — SQLite strftime() can't parse it on all versions.
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _cutoff_datetime(days: int) -> str:
    """Return an ISO datetime string for use with raw event tables (recorded_at >= ?)."""
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


_days_ago = _cutoff_datetime


def _cutoff_date(days: int) -> str:
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")


def _gini(values: list[int]) -> float:
    """Gini coefficient for a list of non-negative ints. 0 = equal, 1 = maximally unequal."""
    if not values or len(values) <= 1:
        return 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    grand = sum(sorted_v)
    if grand == 0:
        return 0.0
    cum = sum((2 * (i + 1) - n - 1) * v for i, v in enumerate(sorted_v))
    return round(cum / (n * grand), 2)
