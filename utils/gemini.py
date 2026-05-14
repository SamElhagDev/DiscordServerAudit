import asyncio
import google.generativeai as genai

import config

_model = None


def _get_model():
    global _model
    if _model is None:
        key = config.get("gemini_key")
        if not key:
            return None
        genai.configure(api_key=key)
        _model = genai.GenerativeModel("gemini-2.0-flash")
    return _model


async def summarize_findings(findings: list[dict], audit_type: str) -> str | None:
    """Return an AI-generated action plan for the given audit findings, or None if Gemini is not configured."""
    model = _get_model()
    if not model or not findings:
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

    try:
        response = await asyncio.to_thread(_model.generate_content, prompt)
        return response.text
    except Exception:
        return None
