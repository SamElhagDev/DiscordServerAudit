# Multimodal Fact-Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the fact-check bot to analyze images, embeds, stickers, and video thumbnails alongside text as a single unified fact-check.

**Architecture:** Add a content extraction layer (`ContentBundle`) that gathers all message content (text, images, embed text, reply context) into a structured bundle. Replace the text-only prompt builder with a multimodal content parts builder that interleaves text and `Part.from_bytes()` image parts. Update `_call_gemini` to accept a list of mixed parts instead of a plain string.

**Tech Stack:** discord.py 2.3.2, google-genai (multimodal `Part.from_bytes`), aiohttp (image downloads), Python 3.11+ dataclasses

**Spec:** `specs/004-multimodal-factcheck/spec.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `cogs/fact_check.py` | Modify | All changes — new dataclass, new helpers, updated listener |
| `config.yaml` | Modify | Add 3 new factcheck config keys |

No new files. No database changes.

---

### Task 1: Add ContentBundle dataclass and _extract_embed_text helper

**Files:**
- Modify: `cogs/fact_check.py` (top of file, after imports)

These are pure data structures and a static text-extraction helper with no I/O.

- [ ] **Step 1: Add imports and ContentBundle dataclass**

Add at the top of `cogs/fact_check.py`, after the existing imports:

```python
import dataclasses
import aiohttp
from google.genai import types as genai_types
```

Then add the dataclass after the `_DEFAULT_STYLE` / `_CLAIM_EMOJIS` block (before the `class FactCheck` line):

```python
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
```

- [ ] **Step 2: Add _extract_embed_text static method**

Add inside the `FactCheck` class, in the `# Helpers` section, after the existing `_match_emoji` method:

```python
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
```

- [ ] **Step 3: Commit**

```
git add cogs/fact_check.py
git commit -m "feat(factcheck): add ContentBundle dataclass and embed text extractor"
```

---

### Task 2: Add _download_image helper

**Files:**
- Modify: `cogs/fact_check.py` (new async method in FactCheck class)

An async HTTP download helper that fetches image bytes from a URL with size and type guards.

- [ ] **Step 1: Add supported MIME types constant**

Add after the `_CLAIM_EMOJIS` dict (before `ContentBundle`):

```python
_SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
```

- [ ] **Step 2: Add _download_image method**

Add inside the `FactCheck` class, after `_extract_embed_text`:

```python
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
```

- [ ] **Step 3: Commit**

```
git add cogs/fact_check.py
git commit -m "feat(factcheck): add _download_image helper with size/type guards"
```

---

### Task 3: Add _fetch_reply_context and _extract_content

**Files:**
- Modify: `cogs/fact_check.py` (two new async methods in FactCheck class)

Extract reply-context resolution from the listener into its own method, then build the main content orchestrator.

- [ ] **Step 1: Add _fetch_reply_context method**

Add inside the `FactCheck` class, after `_download_image`:

```python
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
```

- [ ] **Step 2: Add _extract_content method**

Add directly after `_fetch_reply_context`:

```python
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
```

- [ ] **Step 3: Commit**

```
git add cogs/fact_check.py
git commit -m "feat(factcheck): add content extraction with image/embed/sticker support"
```

---

### Task 4: Replace _build_prompt with _build_content_parts and update _call_gemini

**Files:**
- Modify: `cogs/fact_check.py` (replace `_build_prompt`, update `_call_gemini`)

Replace the text-only prompt builder with a multimodal parts list builder, and update the Gemini call to accept it.

- [ ] **Step 1: Replace _build_prompt with _build_content_parts**

Delete the entire existing `_build_prompt` static method (lines 57-101) and replace it with:

```python
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
```

- [ ] **Step 2: Update _call_gemini signature**

Change `_call_gemini` to accept `contents: list` instead of `prompt: str`. Replace the existing method:

```python
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
```

Note: the only changes from the original are (a) parameter name `contents: list` instead of `prompt: str`, (b) default timeout bumped from 30 to 45, and (c) the `contents` variable name in the `generate_content` call.

- [ ] **Step 3: Commit**

```
git add cogs/fact_check.py
git commit -m "feat(factcheck): multimodal prompt builder and updated Gemini call"
```

---

### Task 5: Rewrite the listener to use the new extraction flow

**Files:**
- Modify: `cogs/fact_check.py` (`on_raw_reaction_add` method)

Replace the text-only extraction, prompt building, and reply-context logic in the listener with the new `_extract_content` → `_build_content_parts` → `_call_gemini` pipeline.

- [ ] **Step 1: Rewrite on_raw_reaction_add**

Replace the entire `on_raw_reaction_add` method with:

```python
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
```

- [ ] **Step 2: Commit**

```
git add cogs/fact_check.py
git commit -m "feat(factcheck): rewrite listener for multimodal content extraction"
```

---

### Task 6: Add config keys and update timeout default

**Files:**
- Modify: `config.yaml`

Add the three new factcheck config keys. The `timeout_seconds` default was bumped in code (Task 4) but the YAML value should also be updated so the user sees the new recommendation.

- [ ] **Step 1: Update config.yaml**

Replace the existing `factcheck:` block with:

```yaml
# Fact-check settings (emoji-triggered via reaction)
factcheck:
  enabled: true                    # Master toggle for fact-check feature
  emoji: "\U0001F50D"             # Emoji name or unicode char that triggers fact-check (magnifying glass)
  model: "gemini-3-flash-preview"  # Gemini model for fact-checking
  rate_limit: 5                    # Max fact-checks per user per hour
  cooldown_seconds: 300            # Seconds before same message can be re-checked
  timeout_seconds: 45              # Max seconds to wait for Gemini response (raised for multimodal)
  max_images: 4                    # Max images to include per fact-check
  max_image_bytes: 10485760        # Max size per image in bytes (10 MB)
  image_download_timeout: 5        # Seconds to wait per image download
```

- [ ] **Step 2: Commit**

```
git add config.yaml
git commit -m "feat(factcheck): add multimodal config keys and bump timeout to 45s"
```

---

### Task 7: Manual verification

No automated test infrastructure exists in this project. Verify the changes by running the bot and testing against real Discord messages.

- [ ] **Step 1: Syntax check**

Run: `python -m py_compile cogs/fact_check.py`
Expected: No output (clean compile)

- [ ] **Step 2: Start the bot**

Run: `python bot.py`
Expected: Bot starts without errors, logs show "Gemini client initialised"

- [ ] **Step 3: Test text-only fact-check (regression)**

In Discord, post a text message with a factual claim (e.g., "The Eiffel Tower is in Berlin"). React with the 🔍 emoji.
Expected: Bot replies with a fact-check verdict embed, same as before.

- [ ] **Step 4: Test image-only fact-check**

Post an image with no caption (e.g., a screenshot of a false headline). React with 🔍.
Expected: Bot replies with "Analyzing text and 1 image(s)..." then a verdict embed analyzing the image content.

- [ ] **Step 5: Test text + image fact-check**

Post a message with text and an attached image. React with 🔍.
Expected: Bot analyzes both together in one verdict.

- [ ] **Step 6: Test embed-only fact-check (link preview)**

Post a URL that generates a rich embed preview. React with 🔍.
Expected: Bot extracts embed text and returns a verdict.

- [ ] **Step 7: Test empty message (no content)**

React with 🔍 on a message that has no text, no images, no embeds (e.g., a message that only had content deleted).
Expected: Bot does nothing (no "Checking..." placeholder appears).

- [ ] **Step 8: Final commit**

```
git add -A
git commit -m "feat(factcheck): multimodal fact-check — images, embeds, stickers, video thumbnails"
```
