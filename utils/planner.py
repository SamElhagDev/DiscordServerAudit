import asyncio
import json
import re
import google.generativeai as genai

import config

_model = None

ACTIONS = [
    {
        "name": "bulk_delete",
        "description": "Delete the last N messages from a text channel (max 100 per call)",
        "parameters": {"channel_name": "Channel name without #", "count": "Number of messages (1-100)"},
        "is_destructive": True,
    },
    {
        "name": "prune_members",
        "description": "Kick members who have no roles and have been inactive for N days",
        "parameters": {"days": "Inactivity threshold in days (1-30, default 7)"},
        "is_destructive": True,
    },
    {
        "name": "bulk_role_add",
        "description": "Add a role to every member who does not already have it",
        "parameters": {"role_name": "Exact role name"},
        "is_destructive": False,
    },
    {
        "name": "bulk_role_remove",
        "description": "Remove a role from every member who currently has it",
        "parameters": {"role_name": "Exact role name"},
        "is_destructive": True,
    },
    {
        "name": "bulk_create_channels",
        "description": "Create multiple text channels inside a named category",
        "parameters": {
            "category_name": "Category name (created if missing)",
            "channel_names": "Comma-separated list of channel names",
        },
        "is_destructive": False,
    },
    {
        "name": "bulk_delete_channels",
        "description": "Delete all channels inside a named category",
        "parameters": {"category_name": "Category name"},
        "is_destructive": True,
    },
    {
        "name": "security_audit",
        "description": "Run a full security audit and post results to the audit channel",
        "parameters": {},
        "is_destructive": False,
    },
    {
        "name": "server_audit",
        "description": "Run a full server health audit and post results to the audit channel",
        "parameters": {},
        "is_destructive": False,
    },
    {
        "name": "find_inactive_channels",
        "description": "List text channels with no messages in the last N days",
        "parameters": {"days": "Inactivity threshold in days (default 14)"},
        "is_destructive": False,
    },
    {
        "name": "find_roleless_members",
        "description": "List members who have no roles assigned (only @everyone)",
        "parameters": {},
        "is_destructive": False,
    },
]


def _get_model():
    global _model
    if _model is None:
        key = config.get("gemini_key")
        if not key:
            return None
        genai.configure(api_key=key)
        _model = genai.GenerativeModel("gemini-2.0-flash")
    return _model


async def build_plan(query: str) -> dict | None:
    """Map a natural-language admin query to a single known action via Gemini."""
    model = _get_model()
    if not model:
        return None

    actions_text = json.dumps(
        [{"name": a["name"], "description": a["description"], "parameters": a["parameters"]} for a in ACTIONS],
        indent=2,
    )
    prompt = (
        f'A Discord server admin typed: "{query}"\n\n'
        f"Available actions:\n{actions_text}\n\n"
        "Pick the single best matching action and fill in its parameters from the query.\n"
        "Return ONLY a JSON object (no markdown fences, no explanation) with this exact shape:\n"
        '{"action":"<name>","parameters":{},"summary":"<one sentence describing what will happen>",'
        '"risks":"<one sentence about risks, or None if safe>","is_destructive":<true|false>}\n'
        'If nothing matches, use action "unknown".'
    )

    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        text = response.text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)
    except Exception:
        return None
