import json
import logging
import re
import time

import config
from utils.gemini import get_client

logger = logging.getLogger(__name__)

ACTIONS = [
    {
        "name": "bulk_delete",
        "description": "Delete messages from a text channel. Can delete a specific number or all messages.",
        "parameters": {"channel_name": "Channel name, mention, or ID", "count": "Number of messages to delete, or \"all\" to delete everything"},
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
    # ── Member management ──────────────────────────────────────────────────────
    {
        "name": "rename_member",
        "description": "Change a member's server nickname (display name within this server only)",
        "parameters": {"member_name": "Current username or display name", "nickname": "New nickname to set"},
        "is_destructive": False,
    },
    {
        "name": "clear_nickname",
        "description": "Reset a member's server nickname back to their default Discord username",
        "parameters": {"member_name": "Username or display name"},
        "is_destructive": False,
    },
    {
        "name": "timeout_member",
        "description": "Temporarily mute/timeout a member so they cannot send messages, react, or join voice for N minutes",
        "parameters": {
            "member_name": "Username or display name",
            "duration_minutes": "How long to timeout in minutes (max 40320 = 28 days)",
            "reason": "Optional reason",
        },
        "is_destructive": True,
    },
    {
        "name": "remove_timeout",
        "description": "Remove an active timeout from a member, restoring their ability to interact",
        "parameters": {"member_name": "Username or display name"},
        "is_destructive": False,
    },
    {
        "name": "unban_member",
        "description": "Unban a previously banned member so they can rejoin the server",
        "parameters": {"member_name": "Exact username of the banned member"},
        "is_destructive": False,
    },
    {
        "name": "member_info",
        "description": "Show detailed info about a member: join date, account age, roles, timeout status",
        "parameters": {"member_name": "Username or display name"},
        "is_destructive": False,
    },
    {
        "name": "find_new_members",
        "description": "List members who joined the server in the last N days (useful for monitoring growth or raids)",
        "parameters": {"days": "How many days back to look (default 7)"},
        "is_destructive": False,
    },
    {
        "name": "find_new_accounts",
        "description": "List members whose Discord accounts are less than N days old — useful for detecting raid bot accounts",
        "parameters": {"days": "Maximum account age in days (default 7)"},
        "is_destructive": False,
    },
    {
        "name": "move_to_voice",
        "description": "Move a member from their current voice channel to a different voice channel",
        "parameters": {"member_name": "Username or display name", "voice_channel_name": "Target voice channel name"},
        "is_destructive": False,
    },
    {
        "name": "disconnect_from_voice",
        "description": "Disconnect a member from whatever voice channel they are currently in",
        "parameters": {"member_name": "Username or display name"},
        "is_destructive": False,
    },
    # ── Role management ────────────────────────────────────────────────────────
    {
        "name": "assign_role",
        "description": "Assign a specific role to a specific member",
        "parameters": {"member_name": "Username or display name", "role_name": "Exact role name"},
        "is_destructive": False,
    },
    {
        "name": "remove_role",
        "description": "Remove a specific role from a specific member",
        "parameters": {"member_name": "Username or display name", "role_name": "Exact role name"},
        "is_destructive": False,
    },
    {
        "name": "create_role",
        "description": "Create a new server role with an optional hex color",
        "parameters": {"role_name": "Name for the new role", "color": "Hex color code e.g. #ff0000 (optional)"},
        "is_destructive": False,
    },
    {
        "name": "delete_role",
        "description": "Permanently delete a role from the server",
        "parameters": {"role_name": "Exact role name to delete"},
        "is_destructive": True,
    },
    {
        "name": "rename_role",
        "description": "Rename an existing role",
        "parameters": {"role_name": "Current role name", "new_name": "New role name"},
        "is_destructive": False,
    },
    {
        "name": "list_roles",
        "description": "List all server roles with their member counts",
        "parameters": {},
        "is_destructive": False,
    },
    # ── Channel management ─────────────────────────────────────────────────────
    {
        "name": "create_channel",
        "description": "Create a single new text channel, optionally inside a category",
        "parameters": {"channel_name": "Channel name without #", "category_name": "Category name (optional)"},
        "is_destructive": False,
    },
    {
        "name": "create_voice_channel",
        "description": "Create a new voice channel, optionally inside a category",
        "parameters": {"channel_name": "Voice channel name", "category_name": "Category name (optional)"},
        "is_destructive": False,
    },
    {
        "name": "create_category",
        "description": "Create a new channel category",
        "parameters": {"category_name": "Name for the new category"},
        "is_destructive": False,
    },
    {
        "name": "delete_channel",
        "description": "Delete a single text or voice channel",
        "parameters": {"channel_name": "Channel name without #"},
        "is_destructive": True,
    },
    {
        "name": "move_channel",
        "description": "Move a channel into a different category",
        "parameters": {"channel_name": "Channel name without #", "category_name": "Target category name"},
        "is_destructive": False,
    },
    {
        "name": "set_channel_nsfw",
        "description": "Mark or unmark a text channel as NSFW (age-restricted)",
        "parameters": {"channel_name": "Channel name without #", "enabled": "true to mark NSFW, false to unmark"},
        "is_destructive": False,
    },
    {
        "name": "slowmode_all_channels",
        "description": "Apply the same slowmode delay to every text channel in the server at once",
        "parameters": {"seconds": "Delay in seconds (0 to disable everywhere, max 21600)"},
        "is_destructive": False,
    },
    # ── Server management ──────────────────────────────────────────────────────
    {
        "name": "delete_invites",
        "description": "Delete all active server invites — essential first response to a raid",
        "parameters": {},
        "is_destructive": True,
    },
    {
        "name": "list_bans",
        "description": "Show all currently banned members",
        "parameters": {},
        "is_destructive": False,
    },
    {
        "name": "server_info",
        "description": "Show a summary of server statistics: member count, channel count, role count, boost level, creation date",
        "parameters": {},
        "is_destructive": False,
    },
    {
        "name": "mass_timeout",
        "description": "Timeout all members who have no roles assigned — fast raid containment",
        "parameters": {"duration_minutes": "Timeout duration in minutes (default 60)"},
        "is_destructive": True,
    },
]


# Serialized once at import — the action catalogue is static.
_ACTIONS_TEXT = json.dumps(
    [{"name": a["name"], "description": a["description"], "parameters": a["parameters"]} for a in ACTIONS],
    indent=2,
)


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
    client = get_client()
    if not client:
        return None

    prompt = (
        f'A Discord server admin typed: "{query}"\n\n'
        f"Available actions:\n{_ACTIONS_TEXT}\n\n"
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
            model=config.get("gemini.model", "gemini-3-flash-preview"),
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
