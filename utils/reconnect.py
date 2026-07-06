"""Gateway reconnect resilience tweaks.

The patch is intentionally defensive: if discord.py's internals ever change shape,
it logs a warning and leaves the stock backoff in place rather than breaking
startup. Worst case is therefore identical to today's behaviour.

"""

import logging

import discord.backoff
import discord.client

logger = logging.getLogger(__name__)


def cap_reconnect_backoff(max_exp: int = 5) -> None:
    """Cap discord.py's gateway reconnect backoff to ~``2**max_exp`` seconds.
    """
    try:
        base_cls = discord.backoff.ExponentialBackoff

        # The reconnect loop looks up ExponentialBackoff on discord.client. If a
        # future refactor stops importing it there, patching would silently no-op,
        # so require it to exist first (else fall back to stock backoff).
        if not hasattr(discord.client, "ExponentialBackoff"):
            raise RuntimeError(
                "discord.client has no ExponentialBackoff to patch — "
                "reconnect path may have changed"
            )

        class _BoundedExponentialBackoff(base_cls):
            def __init__(self, base: int = 1, *, integral: bool = False):
                super().__init__(base, integral=integral)
                self._max = max_exp

        # Verify the cap took effect: override present, and the exponent stays
        # bounded after many delay() calls (base caps growth at self._max).
        probe = _BoundedExponentialBackoff()
        if getattr(probe, "_max", None) != max_exp:
            raise RuntimeError("backoff cap attribute did not take effect")
        for _ in range(max_exp + 20):
            probe.delay()
        if probe._exp > max_exp:
            raise RuntimeError(
                f"backoff exponent {probe._exp} exceeded cap {max_exp}"
            )

        discord.client.ExponentialBackoff = _BoundedExponentialBackoff
        logger.info(
            "Reconnect backoff cap verified and active: max retry delay ≈ %ds "
            "(exponent capped at %d)", 2 ** max_exp, max_exp,
        )
    except Exception:
        logger.warning(
            "Could not cap reconnect backoff — discord.py internals may have "
            "changed; using default backoff.",
            exc_info=True,
        )
