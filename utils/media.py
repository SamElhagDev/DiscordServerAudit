"""Helpers for turning images (avatars, uploads) into Discord custom emojis."""
import re

import discord

# Discord rejects custom-emoji images larger than 256 KB.
EMOJI_MAX_BYTES = 256 * 1024


def sanitize_emoji_name(name: str) -> str:
    """Coerce arbitrary text into a valid emoji name (2-32 chars, alphanumeric + underscore)."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", name or "")[:32]
    if len(cleaned) < 2:
        cleaned = (cleaned + "emoji")[:32]
    return cleaned


async def avatar_emoji_bytes(member: discord.Member) -> bytes | None:
    """
    Read a member's avatar as bytes small enough to be a custom emoji (<256 KB).

    Tries the native avatar at 128px first (preserves animation for Nitro avatars),
    then falls back to progressively smaller static PNGs. Returns None if even the
    smallest rendering exceeds the limit.
    """
    attempts = [
        member.display_avatar.with_size(128),
        member.display_avatar.replace(format="png", size=128),
        member.display_avatar.replace(format="png", size=64),
        member.display_avatar.replace(format="png", size=32),
    ]
    for asset in attempts:
        try:
            data = await asset.read()
        except discord.HTTPException:
            continue
        if len(data) <= EMOJI_MAX_BYTES:
            return data
    return None
