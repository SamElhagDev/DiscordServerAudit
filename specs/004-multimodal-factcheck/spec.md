# Spec: Multimodal Fact-Check

**Date**: 2026-06-03 | **Feature**: 004-multimodal-factcheck
**Status**: Approved

## Summary

Expand the fact-check bot to analyze images, embeds, stickers, and video
thumbnails alongside text. All content in a single message is analyzed together
as one unified fact-check. Messages that previously required text to trigger
a check can now be triggered by any meaningful content (an image attachment,
a sticker, or a rich embed).

## Goals

- Fact-check image attachments (screenshots, infographics, memes, photos of text)
- Extract and analyze text from Discord embed previews (link cards)
- Analyze sticker images (PNG/APNG only; skip Lottie animations)
- Extract video thumbnails for visual analysis
- Treat all content in a message as a single analytical unit

## Non-Goals

- Full video analysis (only thumbnail/proxy image is used)
- Lottie sticker analysis (no raster image available)
- Separate per-content-type verdicts (one verdict per message)
- New database tables or schema changes
- Changes to the verdict format, embed styling, or abuse protection logic

## Architecture

### Content Extraction Flow

```
Reaction -> gate checks -> fetch message
  -> _extract_content(message) -> ContentBundle
    |-- text (message.content)
    |-- images [(bytes, mime_type, label)] -- max 4
    |     |-- attachments via attachment.read()
    |     |-- stickers via _download_image(url) [PNG/APNG only]
    |     |-- video thumbnails via _download_image(proxy_url)
    |     +-- embed images/thumbnails via _download_image(url)
    |-- embed_text (extracted from embed fields)
    +-- reply_context (from parent message)
  -> has_content check (replaces text-only check)
  -> send "Checking..." placeholder
  -> _build_content_parts(bundle) -> list[str | Part]
  -> _call_gemini(contents: list) -> dict | None
  -> build verdict embed -> edit reply
```

### Files Changed

Only `cogs/fact_check.py`. No database changes, no new files.

## Detailed Design

### 1. ContentBundle Dataclass

A dataclass to hold all extracted content from a message.

**Fields:**
- `text: str` — `message.content`, may be empty
- `images: list[tuple[bytes, str, str]]` — list of `(data, mime_type, label)`, max 4
- `embed_text: str` — concatenated text from embeds, may be empty
- `reply_context: str | None` — text from parent message if this is a reply

**Property:**
- `has_content: bool` — `True` if any of `text.strip()`, `images`, or `embed_text.strip()` is non-empty

### 2. `_extract_content(message) -> ContentBundle` (async)

Orchestrates all content gathering from a Discord message.

**Image sources (in priority order for the 4-image cap):**

1. **Image attachments** — `message.attachments` where `content_type` starts
   with `image/`. Downloaded via `attachment.read()` (discord.py built-in).
2. **Stickers** — `message.stickers` filtered to `PNG` and `APNG` formats only.
   Downloaded via `_download_image(sticker.url)`.
3. **Video thumbnails** — `message.attachments` where `content_type` starts
   with `video/`. Thumbnail fetched via `_download_image(attachment.proxy_url)`.
4. **Embed images** — `embed.image.url` or `embed.thumbnail.url` from
   `message.embeds`. Downloaded via `_download_image(url)`.

Images are collected in the above priority order and capped at 4 total.

**Embed text extraction:**
Concatenates from each embed: `title`, `description`, each field's
`name: value`, `footer.text`, `author.name`. Multiple embeds separated by
`---`. Returns empty string if nothing extracted.

**Reply context:**
Extracted from the existing inline logic in the listener (moved here).
Fetches `message.reference.resolved` or falls back to
`channel.fetch_message()`.

### 3. `_download_image(url, max_bytes) -> tuple[bytes, str] | None` (async)

Generic HTTP image download helper.

- Uses a single `aiohttp.ClientSession` created once per `_extract_content`
  call and shared across all image downloads in that call
- Checks `Content-Length` header before downloading; skips images over
  `max_bytes` (default 10 MB, configurable)
- 5-second timeout per download (configurable)
- Filters to supported MIME types: `image/jpeg`, `image/png`, `image/gif`,
  `image/webp`
- Returns `(bytes, content_type)` on success, `None` on any failure
- Logs warnings for skipped/failed downloads

### 4. `_build_content_parts(bundle) -> list` (replaces `_build_prompt`)

Builds a multimodal content list for the Gemini API.

**Part order:**
1. Instruction text (updated for multimodal awareness)
2. Reply context text (if present)
3. Message text (if present), labeled `"Message text:"`
4. Embed text (if present), labeled `"Embedded content (link previews):"`
5. Image parts via `google.genai.types.Part.from_bytes(data=data, mime_type=mime_type)`, each preceded by a text label like `"Attached image 1 (filename.png):"`
6. Closing instruction with JSON format specification

**Prompt changes from current text-only version:**
- Instructions updated to tell Gemini it may receive images and embedded
  content alongside text
- New instruction: "If the content includes images, analyze visible text,
  charts, data, and visual claims in the images alongside any message text"
- Each content piece labeled with its source for clarity
- JSON output schema unchanged (verdict, confidence, analysis, claims)

### 5. `_call_gemini(contents: list) -> dict | None` (signature change)

Changes from accepting `prompt: str` to `contents: list`. The list is passed
directly to `client.aio.models.generate_content(model=model, contents=contents)`.
All existing error handling (timeout, JSON parse, general exception) unchanged.

### 6. `_extract_embed_text(embeds) -> str` (static)

Pulls text from a list of `discord.Embed` objects.

For each embed, extracts (if present):
- `embed.title`
- `embed.description`
- `embed.author.name`
- Each field: `field.name: field.value`
- `embed.footer.text`

Multiple embeds separated by `---`. Returns empty string if no text found.

### 7. `_fetch_reply_context(message) -> str | None` (async)

Extracted from current inline logic in `on_raw_reaction_add`. Resolves the
parent message for replies and returns its text content, or `None`.

### 8. Listener Changes (`on_raw_reaction_add`)

**Removed:**
```python
text = message.content
if not text or not text.strip():
    return
```

**Replaced with:**
```python
bundle = await self._extract_content(message)
if not bundle.has_content:
    return
```

**Placeholder update:**
```python
description = "Analyzing claims -- this usually takes a few seconds."
if bundle.images:
    description = f"Analyzing text and {len(bundle.images)} image(s) -- this may take a moment."
```

**Call update:**
```python
contents = self._build_content_parts(bundle)
result = await self._call_gemini(contents)
```

Reply context resolution is removed from the listener (moved into
`_extract_content`).

## Error Handling

All errors degrade gracefully. No error prevents the fact-check from
attempting with whatever content remains.

| Failure | Behavior |
|---------|----------|
| Single image download fails (timeout, 404, network) | Log warning, skip that image, continue |
| All images fail to download | Fall back to text + embed-text only |
| Image exceeds max size | Skip with log warning, continue with others |
| Unsupported MIME type (SVG, Lottie) | Skip silently |
| Gemini rejects multimodal payload | Catch, log, return error embed |
| No usable content after extraction | Return silently (no placeholder sent) |
| `attachment.read()` raises `HTTPException` | Catch, skip that attachment |

**Key principle:** The "Checking..." placeholder is only sent AFTER confirming
the bundle has content. This avoids showing a placeholder for messages that
end up having nothing to analyze.

## Configuration

New keys under the existing `factcheck:` block in `config.yaml` (all optional
with code defaults):

```yaml
factcheck:
  max_images: 4                  # Max images per fact-check
  max_image_bytes: 10485760      # Max size per image (10 MB)
  image_download_timeout: 5      # Seconds per image download
```

**Default change:** `factcheck.timeout_seconds` default bumped from 30 to 45
to account for larger multimodal payloads. Existing explicit config values
are not overwritten.

## What Stays the Same

- Verdict JSON schema (verdict, confidence, analysis, claims)
- Verdict embed styling and colors
- Abuse protection (cooldown per message, rate limit per user)
- Admin `/factcheck` command
- Emoji trigger mechanism
- Session check counter
