"""Orchestration for the /rules addgame and /rules addsource forms.

Plain async logic with simple inputs (attachment-like objects exposing
``.filename/.size/.read()`` and a url string) so it is testable without Discord.
The modal classes in cogs/rules.py stay thin wrappers around this.
"""
import asyncio
import logging
from urllib.parse import urlparse
from dataclasses import dataclass, field

import config
import database
from utils import rules_ingest, rules_service
from utils.rules_errors import RulesError

logger = logging.getLogger(__name__)

MAX_MESSAGE = 2000


@dataclass
class ItemResult:
    label: str
    ok: bool
    detail: str = ""


@dataclass
class SubmissionResult:
    game_name: str
    items: list = field(default_factory=list)
    total: int = 0

    def render(self) -> str:
        head = f"📚 **{self.game_name}**"
        lines = []
        for i in self.items:
            lines.append(f"✅ {i.label}" if i.ok else f"❌ {i.label}: {i.detail}")
        tail = f"Total sources for **{self.game_name}**: {self.total}"
        text = "\n".join([head, *lines, tail])
        if len(text) <= MAX_MESSAGE:
            return text
        keep = []
        budget = MAX_MESSAGE - len(head) - len(tail) - 40
        for ln in lines:
            if budget - len(ln) - 1 < 0:
                break
            keep.append(ln)
            budget -= len(ln) + 1
        omitted = len(lines) - len(keep)
        return "\n".join([head, *keep, f"…and {omitted} more", tail])[:MAX_MESSAGE]


def _link_label(url: str, filename: str | None = None) -> str:
    """Short label for a link item: the fetched filename, else host + path."""
    if filename:
        body = filename
    else:
        parsed = urlparse(url)
        body = (parsed.hostname or "") + parsed.path
    return body[:73] + " (link)"


def _reason(e: RulesError) -> str:
    return e.user_message.lstrip("❌ ").strip()


async def _noop(_text: str) -> None:
    return None


async def process_submission(guild_id: int, user_id: int, *, game_name: str | None = None,
                             game=None, files=(), url: str | None = None,
                             progress=None) -> SubmissionResult:
    """Validate, optionally create the game, then add every file and the link.

    Pass ``game_name`` (addgame) or an existing ``game`` row (addsource).
    Raises RulesError for problems that mean nothing was added; per-item
    failures are returned in the result instead.
    """
    progress = progress or _noop
    url = (url or "").strip() or None
    files = list(files or [])
    max_bytes = config.get("rules.max_source_bytes", 52428800)
    timeout_s = config.get("rules.url_fetch_timeout_seconds", 20)

    if game is None:
        name = rules_ingest.validate_game_name(game_name)
    elif not files and not url:
        raise RulesError("❌ Attach at least one file or paste a link.")
    else:
        name = game["name"]

    if url:
        # Fails fast on private/invalid URLs before anything is created.
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, rules_ingest.check_public_url, url), timeout_s + 5)
        except asyncio.TimeoutError:
            logger.warning("URL check timed out | guild=%s | user=%s | %s",
                           guild_id, user_id, url)
            raise RulesError("❌ Only public http(s) links are allowed.")

    # Per-file type/size from metadata, before any bytes are read.
    plan = []
    for f in files:
        try:
            mime = rules_ingest.resolve_mime(f.filename, f.size, max_bytes)
            plan.append((f.filename, f, mime, None))
        except RulesError as e:
            plan.append((f.filename, f, None, e))
    if url:
        plan.append((_link_label(url), None, None, None))

    created = game is None
    if created:
        game = await rules_service.create_game(guild_id, name, user_id)
        name = game["name"]

    result = SubmissionResult(name, [], 0)
    n = len(plan)
    for idx, (label, f, mime, err) in enumerate(plan, 1):
        if err is not None:
            result.items.append(ItemResult(label, False, _reason(err)))
            continue
        await progress(f"Indexing {idx} of {n}: {label}…")
        shown = label
        try:
            if f is not None:
                data = await f.read()
                mime = rules_ingest.resolve_mime(f.filename, len(data), max_bytes)
                filename, origin, ref = f.filename, "upload", f.filename
            else:
                async with rules_ingest.create_fetch_session(timeout_s) as session:
                    data, filename, mime = await rules_ingest.fetch_url(
                        session, url, max_bytes, timeout_s)
                origin, ref = "url", url
                shown = _link_label(url, filename)
            result.total = await rules_service.add_source(
                game, data, filename, mime, origin, ref, user_id)
            result.items.append(ItemResult(shown, True))
        except RulesError as e:
            logger.warning("Rules form item failed | guild=%s | user=%s | %s | %s",
                           guild_id, user_id, label, e.user_message)
            result.items.append(ItemResult(shown, False, _reason(e)))
        except Exception as e:
            logger.error("Rules form item error | guild=%s | user=%s | %s: %s",
                         guild_id, user_id, label, e, exc_info=True)
            result.items.append(ItemResult(shown, False, "unexpected error, see logs"))
    if not result.total and not created:
        result.total = len(await database.run(database.list_sources, game["id"]))
    return result
