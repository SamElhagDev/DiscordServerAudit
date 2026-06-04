import asyncio
import dataclasses
import json
import logging
import re
import time

import aiohttp
import discord
from discord.ext import commands
from google.genai import types as genai_types

import config
from utils.gemini import get_client
from utils.permissions import has_admin_role

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Verdict colours & emojis
# ---------------------------------------------------------------------------

_VERDICT_STYLES = {
    "Mostly True":         {"color": 0x2ECC71, "emoji": "✅"},       # green, checkmark
    "Mixed":               {"color": 0xF1C40F, "emoji": "⚠️"},  # yellow, warning
    "Mostly False":        {"color": 0xE74C3C, "emoji": "❌"},       # red, cross
    "Unverifiable":        {"color": 0x95A5A6, "emoji": "❓"},       # grey, question
    "Not a Factual Claim": {"color": 0x95A5A6, "emoji": "\U0001F4AC"},   # grey, speech bubble
}
_DEFAULT_STYLE = {"color": 0x95A5A6, "emoji": "❓"}

_CLAIM_EMOJIS = {
    "True":            "\U0001F7E2",  # green circle
    "Partially True":  "\U0001F7E1",  # yellow circle
    "False":           "\U0001F534",  # red circle
    "Unverifiable":    "⚪",      # white circle
}

_SUPPORTED_IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "image/heic", "image/heif",   # iPhone photos
    "image/avif",                  # modern web format
    "image/bmp", "image/tiff",    # legacy formats
}


@dataclasses.dataclass
class ContentBundle:
    """All extractable content from a single Discord message."""
    text: str = ""
    images: list[tuple[bytes, str, str]] = dataclasses.field(default_factory=list)  # (data, mime_type, label)
    embed_text: str = ""
    reply_context: str | None = None

    @property
    def has_content(self) -> bool:
        return bool(self.text.strip() or self.images or self.embed_text.strip())


class FactCheck(commands.Cog):
    """React with an emoji to fact-check any message using AI."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Abuse protection caches (populated in Phase 4 tasks)
        self._checked_messages: dict[int, float] = {}   # message_id → monotonic time
        self._user_limits: dict[int, list[float]] = {}   # user_id → [monotonic timestamps]
        self._session_check_count: int = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _match_emoji(self, emoji: discord.PartialEmoji) -> bool:
        """Return True if *emoji* matches the configured fact-check trigger."""
        configured = config.get("factcheck.emoji", "\U0001F50D")
        # Custom emoji: compare by name.  Unicode emoji: compare the char.
        return emoji.name == configured

    @staticmethod
    def _extract_embed_text(embeds: list[discord.Embed]) -> str:
        """Pull readable text from a list of Discord embeds."""
        parts = []
        for embed in embeds:
            lines = []
            if embed.title:
                lines.append(embed.title)
            if embed.author and embed.author.name:
                lines.append(f"Author: {embed.author.name}")
            if embed.description:
                lines.append(embed.description)
            for field in embed.fields:
                lines.append(f"{field.name}: {field.value}")
            if embed.footer and embed.footer.text:
                lines.append(embed.footer.text)
            if lines:
                parts.append("\n".join(lines))
        return "\n---\n".join(parts)

    async def _download_image(
        self,
        session: aiohttp.ClientSession,
        url: str,
        max_bytes: int | None = None,
        timeout_seconds: int | None = None,
    ) -> tuple[bytes, str] | None:
        """Download an image from *url*. Returns (bytes, content_type) or None."""
        if max_bytes is None:
            max_bytes = config.get("factcheck.max_image_bytes", 10_485_760)
        if timeout_seconds is None:
            timeout_seconds = config.get("factcheck.image_download_timeout", 5)
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout_seconds)
            ) as resp:
                if resp.status != 200:
                    logger.warning("Image download failed: HTTP %d for %s", resp.status, url)
                    return None
                content_type = resp.content_type or ""
                if content_type not in _SUPPORTED_IMAGE_TYPES:
                    logger.debug("Skipping unsupported image type %s from %s", content_type, url)
                    return None
                content_length = resp.content_length
                if content_length and content_length > max_bytes:
                    logger.warning(
                        "Skipping oversized image: %d bytes (limit %d) from %s",
                        content_length, max_bytes, url,
                    )
                    return None
                data = await resp.read()
                if len(data) > max_bytes:
                    logger.warning(
                        "Image exceeded size limit after download: %d bytes from %s",
                        len(data), url,
                    )
                    return None
                return data, content_type
        except asyncio.TimeoutError:
            logger.warning("Image download timed out after %ds: %s", timeout_seconds, url)
            return None
        except Exception:
            logger.warning("Image download failed for %s", url, exc_info=True)
            return None

    async def _fetch_reply_context(self, message: discord.Message) -> str | None:
        """Return the text content of the message this one replies to, or None."""
        if not message.reference or not message.reference.message_id:
            return None
        try:
            parent = message.reference.resolved
            if not isinstance(parent, discord.Message):
                channel = message.channel
                parent = await channel.fetch_message(message.reference.message_id)
            if parent and parent.content and parent.content.strip():
                logger.info(
                    "Fact-check includes reply context | parent_msg=%s len=%d",
                    parent.id, len(parent.content),
                )
                return parent.content
        except (discord.NotFound, discord.Forbidden):
            logger.debug(
                "Could not fetch parent message %s — skipping reply context",
                message.reference.message_id,
            )
        except Exception:
            logger.debug("Error fetching parent message for reply context", exc_info=True)
        return None

    async def _extract_content(self, message: discord.Message) -> ContentBundle:
        """Gather all content from *message* into a ContentBundle."""
        max_images = config.get("factcheck.max_images", 4)
        images: list[tuple[bytes, str, str]] = []

        async with aiohttp.ClientSession() as session:
            # 1. Image attachments (highest priority)
            for att in message.attachments:
                if len(images) >= max_images:
                    break
                ct = att.content_type or ""
                if ct.startswith("image/") and ct in _SUPPORTED_IMAGE_TYPES:
                    try:
                        data = await att.read()
                        max_bytes = config.get("factcheck.max_image_bytes", 10_485_760)
                        if len(data) <= max_bytes:
                            images.append((data, ct, att.filename or "attachment"))
                        else:
                            logger.warning("Skipping oversized attachment: %d bytes", len(data))
                    except Exception:
                        logger.warning("Failed to read attachment %s", att.filename, exc_info=True)

            # 2. Stickers (PNG/APNG only)
            for sticker in message.stickers:
                if len(images) >= max_images:
                    break
                if sticker.format in (
                    discord.StickerFormatType.png,
                    discord.StickerFormatType.apng,
                ):
                    result = await self._download_image(session, str(sticker.url))
                    if result:
                        images.append((*result, sticker.name))

            # 3. Video thumbnails
            for att in message.attachments:
                if len(images) >= max_images:
                    break
                ct = att.content_type or ""
                if ct.startswith("video/") and att.proxy_url:
                    result = await self._download_image(session, att.proxy_url)
                    if result:
                        images.append((*result, f"{att.filename or 'video'} (thumbnail)"))

            # 4. Embed images / thumbnails (lowest priority)
            for embed in message.embeds:
                if len(images) >= max_images:
                    break
                img_url = None
                label = "embed image"
                if embed.image and embed.image.url:
                    img_url = embed.image.url
                elif embed.thumbnail and embed.thumbnail.url:
                    img_url = embed.thumbnail.url
                    label = "embed thumbnail"
                if img_url:
                    result = await self._download_image(session, img_url)
                    if result:
                        images.append((*result, label))

        embed_text = self._extract_embed_text(message.embeds)
        reply_context = await self._fetch_reply_context(message)

        return ContentBundle(
            text=message.content or "",
            images=images,
            embed_text=embed_text,
            reply_context=reply_context,
        )

    @staticmethod
    def _build_content_parts(bundle: ContentBundle) -> list:
        """Build a multimodal content parts list for the Gemini API."""
        parts: list = []

        # 1. Instructions
        instructions = (
            "You are an expert fact-checker providing detailed, educational analysis.\n"
            "Analyze the following Discord message. The message may include text,\n"
            "images, and embedded link previews. Analyze ALL content together as a\n"
            "single unit of communication.\n\n"
        )
        if bundle.images:
            instructions += (
                "If the content includes images, analyze visible text, charts,\n"
                "data, infographics, and visual claims in the images alongside any\n"
                "message text.\n\n"
            )

        # 2. Reply context
        if bundle.reply_context:
            instructions += (
                "This message is a reply to the following original message. Use it as\n"
                "context to understand what claims are being made or responded to.\n\n"
                f'Original message being replied to:\n"{bundle.reply_context}"\n\n'
                "---\n\n"
            )

        instructions += (
            "1. Identify each discrete factual claim in the message.\n"
            "2. For each claim:\n"
            "   - State the claim clearly\n"
            "   - Assess whether it is True, False, Partially True, or Unverifiable\n"
            "   - Provide a brief explanation of WHY (cite the correct fact, the common\n"
            "     misconception, or why it cannot be verified)\n"
            "3. Provide an overall verdict: one of \"Mostly True\", \"Mixed\",\n"
            "   \"Mostly False\", \"Unverifiable\", or \"Not a Factual Claim\".\n"
            "4. Write a detailed analysis paragraph (4-6 sentences) that explains\n"
            "   the key findings, provides important context or nuance, and notes\n"
            "   any caveats. Be educational — help the reader understand the topic\n"
            "   better, not just whether the claim is right or wrong.\n"
            "5. Rate your overall confidence: \"High\", \"Medium\", or \"Low\".\n"
            "6. If the message contains only opinions, jokes, or subjective statements,\n"
            "   use verdict \"Not a Factual Claim\" and explain why it is not checkable.\n\n"
            "Respond in this exact JSON format (no markdown fences):\n"
            "{\n"
            '  "verdict": "<one of the five verdict options>",\n'
            '  "confidence": "<High/Medium/Low>",\n'
            '  "analysis": "<4-6 sentence detailed analysis with context and nuance>",\n'
            '  "claims": [\n'
            "    {\n"
            '      "claim": "<extracted claim>",\n'
            '      "assessment": "<True/False/Partially True/Unverifiable>",\n'
            '      "explanation": "<1-2 sentence explanation with the correct fact or reasoning>"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
        )

        # 3. Message text
        if bundle.text.strip():
            instructions += f'Message text:\n"{bundle.text}"\n\n'

        # 4. Embed text
        if bundle.embed_text.strip():
            instructions += f"Embedded content (link previews):\n{bundle.embed_text}\n\n"

        parts.append(instructions)

        # 5. Image parts
        for i, (data, mime_type, label) in enumerate(bundle.images, 1):
            parts.append(f"Attached image {i} ({label}):")
            parts.append(genai_types.Part.from_bytes(data=data, mime_type=mime_type))

        return parts

    async def _call_gemini(self, contents: list) -> dict | None:
        """Send *contents* to the configured Gemini model, return parsed dict or None."""
        client = get_client()
        if not client:
            return None

        model = config.get("factcheck.model", "gemini-3-flash-preview")
        timeout = config.get("factcheck.timeout_seconds", 45)
        t0 = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(model=model, contents=contents),
                timeout=timeout,
            )
            elapsed = time.perf_counter() - t0
            raw = response.text.strip()
            # Strip markdown fences if present
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            result = json.loads(raw)
            logger.info(
                "Fact-check Gemini response | model=%s elapsed=%.2fs verdict=%s confidence=%s claims=%d",
                model, elapsed, result.get("verdict"), result.get("confidence"), len(result.get("claims", [])),
            )
            return result
        except asyncio.TimeoutError:
            elapsed = time.perf_counter() - t0
            logger.warning("Fact-check Gemini call timed out after %.2fs (limit=%ds)", elapsed, timeout)
            return None
        except json.JSONDecodeError as e:
            elapsed = time.perf_counter() - t0
            logger.error("Fact-check Gemini JSON parse failed after %.2fs: %s", elapsed, e)
            return None
        except Exception:
            elapsed = time.perf_counter() - t0
            logger.error("Fact-check Gemini call failed after %.2fs", elapsed, exc_info=True)
            return None

    @staticmethod
    def _build_embed(result: dict) -> discord.Embed:
        """Build a rich verdict embed from a parsed Gemini response."""
        verdict = result.get("verdict", "Unverifiable")
        style = _VERDICT_STYLES.get(verdict, _DEFAULT_STYLE)
        confidence = result.get("confidence", "Unknown")
        analysis = result.get("analysis", "No analysis available.")
        claims = result.get("claims", [])

        embed = discord.Embed(
            title=f"{style['emoji']}  Fact-Check: {verdict}",
            description=analysis,
            color=style["color"],
        )

        # Claims breakdown
        if claims:
            claim_lines = []
            for c in claims:
                assessment = c.get("assessment", "Unverifiable")
                emoji = _CLAIM_EMOJIS.get(assessment, "⚪")
                claim_text = c.get("claim", "Unknown claim")
                explanation = c.get("explanation", "")
                claim_lines.append(f"{emoji} **{assessment}**\n> {claim_text}\n{explanation}")
            # Discord embed fields have a 1024-char limit — split if needed
            claims_text = "\n\n".join(claim_lines)
            if len(claims_text) <= 1024:
                embed.add_field(name="Claims Breakdown", value=claims_text, inline=False)
            else:
                # Split across multiple fields
                chunk = ""
                part = 1
                for line in claim_lines:
                    if len(chunk) + len(line) + 2 > 1024:
                        embed.add_field(
                            name=f"Claims Breakdown ({part})" if part > 1 else "Claims Breakdown",
                            value=chunk.strip(), inline=False,
                        )
                        chunk = ""
                        part += 1
                    chunk += line + "\n\n"
                if chunk.strip():
                    embed.add_field(
                        name=f"Claims Breakdown ({part})" if part > 1 else "Claims Breakdown",
                        value=chunk.strip(), inline=False,
                    )

        # Details field
        embed.add_field(
            name="Details",
            value=f"**Confidence:** {confidence}\n**Claims checked:** {len(claims)}",
            inline=False,
        )

        embed.set_footer(text="AI-generated — verify important claims independently | Powered by Gemini")
        return embed

    # ------------------------------------------------------------------
    # Abuse protection helpers
    # ------------------------------------------------------------------

    def _is_on_cooldown(self, message_id: int) -> bool:
        """Return True if *message_id* was recently fact-checked."""
        cooldown = config.get("factcheck.cooldown_seconds", 300)
        now = time.monotonic()
        checked_at = self._checked_messages.get(message_id)
        if checked_at is not None and (now - checked_at) < cooldown:
            return True
        # Lazy eviction of expired entries
        expired = [mid for mid, t in self._checked_messages.items() if (now - t) >= cooldown]
        for mid in expired:
            del self._checked_messages[mid]
        return False

    def _is_rate_limited(self, user_id: int) -> bool:
        """Return True if *user_id* has exceeded the hourly fact-check limit."""
        limit = config.get("factcheck.rate_limit", 5)
        now = time.monotonic()
        window = 3600.0  # 1 hour
        timestamps = self._user_limits.get(user_id, [])
        # Prune entries outside the window
        timestamps = [t for t in timestamps if (now - t) < window]
        self._user_limits[user_id] = timestamps
        return len(timestamps) >= limit

    def _record_check(self, message_id: int, user_id: int):
        """Record a successful fact-check for cooldown and rate-limit tracking."""
        now = time.monotonic()
        self._checked_messages[message_id] = now
        self._user_limits.setdefault(user_id, []).append(now)
        self._session_check_count += 1

    # ------------------------------------------------------------------
    # Listener
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # Gate checks
        if not config.get("factcheck.enabled", True):
            return
        if payload.guild_id is None:
            return
        if payload.member and payload.member.bot:
            return
        if not self._match_emoji(payload.emoji):
            return

        # Silent bail if Gemini is not configured (no "Checking..." message)
        if get_client() is None:
            logger.debug("Fact-check skipped — Gemini not configured | guild=%s", payload.guild_id)
            return

        # Cooldown check (per message)
        if self._is_on_cooldown(payload.message_id):
            logger.debug(
                "Fact-check cooldown hit | guild=%s msg=%s user=%s",
                payload.guild_id, payload.message_id, payload.user_id,
            )
            return

        # Rate limit check (per user)
        if self._is_rate_limited(payload.user_id):
            logger.info(
                "Fact-check rate limit hit | guild=%s user=%s count=%d",
                payload.guild_id, payload.user_id,
                len(self._user_limits.get(payload.user_id, [])),
            )
            try:
                channel = self.bot.get_channel(payload.channel_id)
                if channel:
                    msg = await channel.send(
                        f"⚠️ <@{payload.user_id}> You've reached the fact-check limit. "
                        "Please try again later.",
                    )
                    await asyncio.sleep(10)
                    await msg.delete()
            except Exception:
                logger.debug("Failed to send/delete rate-limit notice", exc_info=True)
            return

        # Fetch the message
        channel = self.bot.get_channel(payload.channel_id)
        if not channel:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden):
            return
        except Exception:
            logger.error("Failed to fetch message %s for fact-check", payload.message_id, exc_info=True)
            return

        # Extract all content (text, images, embeds, reply context)
        bundle = await self._extract_content(message)
        if not bundle.has_content:
            return

        logger.info(
            "Fact-check triggered | guild=%s channel=#%s user=%s msg=%s "
            "text_len=%d images=%d embed_text_len=%d has_reply_context=%s",
            payload.guild_id, getattr(channel, "name", "?"),
            payload.user_id, payload.message_id,
            len(bundle.text), len(bundle.images),
            len(bundle.embed_text), bundle.reply_context is not None,
        )

        # Send "Checking..." placeholder
        description = "Analyzing claims — this usually takes a few seconds."
        if bundle.images:
            description = f"Analyzing text and {len(bundle.images)} image(s) — this may take a moment."
        checking_embed = discord.Embed(
            title="\U0001F50D  Checking...",
            description=description,
            color=0x95A5A6,
        )
        try:
            reply = await message.reply(embed=checking_embed, mention_author=False)
        except Exception:
            logger.error("Failed to send checking placeholder for msg %s", payload.message_id, exc_info=True)
            return

        # Call Gemini
        t0 = time.perf_counter()
        contents = self._build_content_parts(bundle)
        result = await self._call_gemini(contents)
        elapsed = time.perf_counter() - t0

        if result is None:
            # Edit to error embed
            error_embed = discord.Embed(
                title="❌  Fact-Check Failed",
                description="Could not complete the fact-check. The AI service may be unavailable or timed out.",
                color=0xE74C3C,
            )
            error_embed.set_footer(text="Try again in a moment")
            try:
                await reply.edit(embed=error_embed)
            except Exception:
                logger.debug("Failed to edit reply to error embed", exc_info=True)
            return

        # Success — build verdict embed and edit
        embed = self._build_embed(result)
        try:
            await reply.edit(embed=embed)
        except Exception:
            logger.error("Failed to edit fact-check reply for msg %s", payload.message_id, exc_info=True)
            return

        # Record for cooldown + rate limiting
        self._record_check(payload.message_id, payload.user_id)

        logger.info(
            "Fact-check complete | guild=%s msg=%s verdict=%s confidence=%s elapsed=%.2fs",
            payload.guild_id, payload.message_id,
            result.get("verdict"), result.get("confidence"), elapsed,
        )

    # ------------------------------------------------------------------
    # Admin command
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="factcheck", description="Show fact-check configuration and session stats")
    @commands.guild_only()
    @has_admin_role()
    async def factcheck_cmd(self, ctx: commands.Context):
        """Show fact-check configuration and usage stats for the current session."""
        enabled = config.get("factcheck.enabled", True)
        emoji = config.get("factcheck.emoji", "\U0001F50D")
        model = config.get("factcheck.model", "gemini-3-flash-preview")
        rate_limit = config.get("factcheck.rate_limit", 5)
        cooldown = config.get("factcheck.cooldown_seconds", 300)
        timeout = config.get("factcheck.timeout_seconds", 30)

        embed = discord.Embed(
            title="\U0001F50D  Fact-Check Status",
            color=discord.Color.teal(),
        )
        embed.add_field(name="Enabled", value="Yes" if enabled else "No", inline=True)
        embed.add_field(name="Emoji", value=emoji, inline=True)
        embed.add_field(name="Model", value=f"`{model}`", inline=True)
        embed.add_field(name="Rate limit", value=f"{rate_limit}/hour per user", inline=True)
        embed.add_field(name="Cooldown", value=f"{cooldown}s per message", inline=True)
        embed.add_field(name="Timeout", value=f"{timeout}s", inline=True)
        embed.add_field(name="Checks this session", value=str(self._session_check_count), inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(FactCheck(bot))
