"""Exponential backoff with a hard ceiling.

Used by background loops that poll an external API: on a run of consecutive failures the
retry interval doubles up to ``cap`` instead of hammering a dead endpoint at the normal
cadence, and snaps back to the base interval on the first success.

Separate from ``utils.reconnect``, which patches discord.py's own gateway backoff.
"""
import math

# Used when a caller passes a non-positive base interval (misconfiguration).
_FALLBACK_BASE = 30.0


def next_delay(base: float, failures: int, *, cap: float) -> float:
    """Seconds to wait before the next attempt after *failures* consecutive failures.

    ``failures=0`` (the healthy case) returns *base*; each additional failure doubles it,
    clamped to *cap*.
    """
    base = float(base) if base and base > 0 else _FALLBACK_BASE
    cap = float(cap) if cap and cap > 0 else base
    if not failures or failures <= 0:
        return min(base, cap)
    if cap <= base:
        return cap
    # Clamp the exponent before shifting. The result is capped regardless, so a large
    # `failures` would otherwise build a huge integer for a value we immediately discard.
    max_exp = math.ceil(math.log2(cap / base))
    exponent = min(int(failures), max_exp)
    return min(base * (2 ** exponent), cap)
