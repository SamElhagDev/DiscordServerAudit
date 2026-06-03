import logging
import time
from google import genai

import config

logger = logging.getLogger(__name__)
_client = None


def get_client():
    """Lazily build and cache a single shared genai client (None if no key)."""
    global _client
    if _client is None:
        key = config.get("gemini_key")
        if not key:
            logger.debug("gemini_key not set — Gemini features disabled")
            return None
        try:
            _client = genai.Client(api_key=key)
            logger.info("Gemini client initialised")
        except Exception:
            logger.error("Failed to initialise Gemini client", exc_info=True)
    return _client


async def summarize_findings(findings: list[dict], audit_type: str) -> str | None:
    """Return an AI-generated action plan for the given audit findings, or None if Gemini is not configured."""
    client = get_client()
    if not client or not findings:
        return None

    lines = "\n".join(
        f"[{f['severity'].upper()}] {f['category']}: {f['description']}"
        for f in findings
    )
    prompt = (
        f"You are a Discord server advisor. Given these {audit_type} audit findings, "
        "write a concise prioritized action plan for the server admin. "
        "3-5 bullet points, specific and actionable, under 300 words.\n\n"
        f"Findings:\n{lines}"
    )

    logger.debug("Requesting Gemini summary | audit_type=%r findings=%d", audit_type, len(findings))
    t0 = time.perf_counter()
    try:
        model = config.get("gemini.model", "gemini-3-flash-preview")
        response = await client.aio.models.generate_content(
            model=model,
            contents=prompt,
        )
        elapsed = time.perf_counter() - t0
        summary = response.text
        logger.info(
            "Gemini summary generated | audit_type=%r findings=%d length=%d chars elapsed=%.2fs",
            audit_type, len(findings), len(summary), elapsed,
        )
        return summary
    except Exception:
        elapsed = time.perf_counter() - t0
        logger.error("Gemini summarise call failed after %.2fs", elapsed, exc_info=True)
        return None
