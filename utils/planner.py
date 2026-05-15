import json
import logging
import re
import time
from google import genai

import config

logger = logging.getLogger(__name__)
_client = None

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
    {
        "name": "find_members_with_role",
        "description": "List all members who currently have a specific role",
        "parameters": {"role_name": "Exact role name"},
        "is_destructive": False,
    },
    {
        "name": "kick_member",
        "description": "Kick a single member from the server by their username or display name",
        "parameters": {"member_name": "Username or display name of the member to kick", "reason": "Optional reason for the kick"},
        "is_destructive": True,
    },
    {
        "name": "ban_member",
        "description": "Ban a single member from the server by their username or display name",
        "parameters": {"member_name": "Username or display name of the member to ban", "reason": "Optional reason for the ban"},
        "is_destructive": True,
    },
    {
        "name": "set_slowmode",
        "description": "Set the slowmode delay on a text channel to throttle how often members can send messages",
        "parameters": {"channel_name": "Channel name without #", "seconds": "Slowmode delay in seconds (0 to disable, max 21600)"},
        "is_destructive": False,
    },
    {
        "name": "lock_channel",
        "description": "Lock a text channel so @everyone cannot send messages (useful during incidents or announcements)",
        "parameters": {"channel_name": "Channel name without #"},
        "is_destructive": False,
    },
    {
        "name": "unlock_channel",
        "description": "Unlock a previously locked text channel, restoring @everyone send permissions",
        "parameters": {"channel_name": "Channel name without #"},
        "is_destructive": False,
    },
    {
        "name": "rename_channel",
        "description": "Rename an existing text channel",
        "parameters": {"channel_name": "Current channel name without #", "new_name": "New channel name without #"},
        "is_destructive": False,
    },
    {
        "name": "set_channel_topic",
        "description": "Set or update the topic/description shown at the top of a text channel",
        "parameters": {"channel_name": "Channel name without #", "topic": "New topic text"},
        "is_destructive": False,
    },
    {
        "name": "delete_user_messages",
        "description": "Delete all recent messages from a specific user in one channel or across all channels",
        "parameters": {
            "member_name": "Username or display name of the member whose messages to delete",
            "channel_name": "Channel name without # — omit or leave blank to search all channels",
            "scan_limit": "How many recent messages to scan per channel (default 500, max 1000)",
        },
        "is_destructive": True,
    },
]


def _get_client():
    global _client
    if _client is None:
        key = config.get("gemini_key")
        if not key:
            logger.warning("gemini_key not configured — natural language planning unavailable")
            return None
        try:
            _client = genai.Client(api_key=key)
            logger.info("Gemini client (planner) initialised")
        except Exception:
            logger.error("Failed to initialise Gemini planner client", exc_info=True)
    return _client


async def build_plan(query: str) -> dict | None:
    """
    Map a natural-language admin query to one or more chained actions via Gemini.

    Returns a dict with shape:
    {
        "steps": [
            {
                "action": "<name>",
                "parameters": {},
                "summary": "<one sentence>",
                "risks": "<one sentence or null>",
                "is_destructive": true|false
            },
            ...
        ],
        "overall_summary": "<one sentence describing the full chain>",
        "is_destructive": true|false   # true if ANY step is destructive
    }
    Returns None on Gemini error.
    Steps list contains a single entry with action "unknown" if nothing matches.
    """
    client = _get_client()
    if not client:
        return None

    actions_text = json.dumps(
        [{"name": a["name"], "description": a["description"], "parameters": a["parameters"]} for a in ACTIONS],
        indent=2,
    )
    prompt = (
        f'A Discord server admin typed: "{query}"\n\n'
        f"Available actions:\n{actions_text}\n\n"
        "Map the request to one or more actions from the list above, executed in order.\n"
        "Return ONLY a JSON object (no markdown fences, no explanation) with this exact shape:\n"
        "{\n"
        '  "steps": [\n'
        '    {\n'
        '      "action": "<name>",\n'
        '      "parameters": {},\n'
        '      "summary": "<one sentence describing this step>",\n'
        '      "risks": "<one sentence about risks, or null if safe>",\n'
        '      "is_destructive": true\n'
        "    }\n"
        "  ],\n"
        '  "overall_summary": "<one sentence describing the full plan>",\n'
        '  "is_destructive": true\n'
        "}\n"
        'If nothing matches any action, return a single step with action "unknown".'
    )

    logger.debug("Sending plan request to Gemini | query=%r", query)
    t0 = time.perf_counter()
    try:
        response = await client.aio.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=prompt,
        )
        elapsed = time.perf_counter() - t0
        text = response.text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        logger.debug("Gemini raw response (%.2fs): %s", elapsed, text)
        return json.loads(text)
    except Exception as e:
        elapsed = time.perf_counter() - t0
        logger.error("build_plan failed after %.2fs | query=%r | error=%s", elapsed, query, e, exc_info=True)
        return None
