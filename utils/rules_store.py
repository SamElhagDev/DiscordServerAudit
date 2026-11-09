"""Gemini File Search wrapper: one store per game, rulebook-only answers."""
import asyncio
import logging
import os
import tempfile
import time
from dataclasses import dataclass

from google.genai import errors, types

from utils.gemini import get_client
from utils.rules_errors import RulesError
from utils.rules_ingest import ALLOWED_TYPES

logger = logging.getLogger(__name__)

NOT_FOUND_SENTINEL = "NOT_IN_RULES"
SNIPPET_MAX = 300
_NO_CLIENT = "❌ Gemini is not configured on this bot."
_EXT_BY_MIME = {mime: ext for ext, mime in ALLOWED_TYPES.items()}

SYSTEM_INSTRUCTION = (
    'You are a rules referee for the game "{game}". Answer ONLY from the passages '
    "retrieved from this game's uploaded rulebook sources. Do not use outside "
    "knowledge, other editions, house rules, or guesses. Quote or closely paraphrase "
    "the relevant rule and name the section or page when the passage shows it. If the "
    "retrieved passages do not answer the question, reply with exactly NOT_IN_RULES "
    "and nothing else. Keep answers under 250 words."
)


@dataclass
class Citation:
    title: str
    snippet: str


@dataclass
class RulesAnswer:
    text: str
    citations: list
    found: bool


def _client():
    client = get_client()
    if client is None:
        raise RulesError(_NO_CLIENT)
    return client


def build_config(store_name: str, game_name: str) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION.format(game=game_name),
        temperature=0,
        tools=[types.Tool(file_search=types.FileSearch(
            file_search_store_names=[store_name]))],
    )


def interpret_response(text, chunks, max_citations: int) -> RulesAnswer:
    if not text or not text.strip() or NOT_FOUND_SENTINEL in text or not chunks:
        return RulesAnswer(text="", citations=[], found=False)
    seen = set()
    citations = []
    for title, body in chunks:
        key = (title, body)
        if key in seen:
            continue
        seen.add(key)
        body = body or ""
        snippet = body if len(body) <= SNIPPET_MAX else body[:SNIPPET_MAX] + "…"
        citations.append(Citation(title=title or "Rulebook", snippet=snippet))
        if len(citations) >= max_citations:
            break
    return RulesAnswer(text=text.strip(), citations=citations, found=True)


async def create_store(display_name: str) -> str:
    client = _client()
    start = time.monotonic()
    store = await client.aio.file_search_stores.create(
        config={"display_name": display_name})
    logger.info("File Search store created: %s in %.2fs", store.name,
                time.monotonic() - start)
    return store.name


_ORPHAN_WINDOW_S = 1800.0
# Strong references so fire-and-forget cleanup tasks aren't garbage-collected.
_cleanup_tasks: set = set()


async def _cleanup_orphan(op, store_name: str, poll_seconds: float,
                          window_s: float = _ORPHAN_WINDOW_S) -> None:
    """Keep polling a timed-out indexing op; delete the document once it lands."""
    op_name = getattr(op, "name", None)
    try:
        deadline = time.monotonic() + window_s
        while not op.done:
            if time.monotonic() >= deadline:
                logger.error("Orphan cleanup gave up: op %s (store %s) still not "
                             "done after %.0fs; a document may be orphaned",
                             op_name, store_name, window_s)
                return
            await asyncio.sleep(poll_seconds)
            op = await _client().aio.operations.get(op)
        doc = getattr(getattr(op, "response", None), "document_name", None)
        if op.error or not doc:
            logger.warning("Timed-out op %s (store %s) finished without a "
                           "document (error=%s); nothing to delete",
                           op_name, store_name, op.error)
            return
        await delete_document(doc)
        logger.warning("Deleted orphaned document %s from store %s after "
                       "indexing timeout (op %s)", doc, store_name, op_name)
    except Exception:
        logger.exception("Orphan cleanup failed for op %s (store %s); a "
                         "document may be orphaned", op_name, store_name)


def _schedule_cleanup(op, store_name: str, poll_seconds: float) -> None:
    task = asyncio.get_running_loop().create_task(
        _cleanup_orphan(op, store_name, poll_seconds))
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_tasks.discard)


async def upload_document(store_name: str, data: bytes, display_name: str,
                          mime_type: str, timeout_s: float,
                          poll_seconds: float = 2.0) -> str:
    client = _client()
    start = time.monotonic()
    # Suffix comes from the MIME type, never from the user-supplied filename.
    suffix = _EXT_BY_MIME.get(mime_type, "")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    path = tmp.name

    def _write():
        with tmp:
            tmp.write(data)

    state = {"op": None}
    try:
        await asyncio.to_thread(_write)

        async def _run():
            op = await client.aio.file_search_stores.upload_to_file_search_store(
                file=path, file_search_store_name=store_name,
                config={"display_name": display_name, "mime_type": mime_type})
            state["op"] = op
            while not op.done:
                await asyncio.sleep(poll_seconds)
                op = await client.aio.operations.get(op)
                state["op"] = op
            return op

        try:
            op = await asyncio.wait_for(_run(), timeout=timeout_s)
        except asyncio.TimeoutError:
            pending = state["op"]
            logger.error("Indexing timed out for store %s after %.2fs (op %s); "
                         "scheduling orphan cleanup", store_name,
                         time.monotonic() - start, getattr(pending, "name", None))
            if pending is not None:
                _schedule_cleanup(pending, store_name, poll_seconds)
            raise RulesError(
                "❌ Indexing that source timed out. Try again, or try a smaller file.")
        if op.error:
            logger.error("Indexing failed for store %s: %s", store_name, op.error)
            raise RulesError("❌ Gemini couldn't index that file.")
        document_name = getattr(getattr(op, "response", None), "document_name", None)
        if not document_name:
            logger.error("Indexing op %s for store %s finished with no document",
                         getattr(op, "name", None), store_name)
            raise RulesError("❌ Gemini couldn't index that file.")
        logger.info("File Search upload to %s done in %.2fs", store_name,
                    time.monotonic() - start)
        return document_name
    except RulesError:
        raise
    except Exception:
        logger.exception("Upload to store %s failed", store_name)
        raise
    finally:
        try:
            os.remove(path)
        except OSError:
            logger.warning("Could not remove temp file %s", path, exc_info=True)


def _is_404(exc: Exception) -> bool:
    return isinstance(exc, errors.ClientError) and exc.code == 404


async def delete_document(document_name: str) -> None:
    client = _client()
    try:
        await client.aio.file_search_stores.documents.delete(
            name=document_name, config={"force": True})
    except errors.ClientError as e:
        if not _is_404(e):
            raise
        logger.info("Document %s already gone (404)", document_name)


async def delete_store(store_name: str) -> None:
    client = _client()
    try:
        await client.aio.file_search_stores.delete(
            name=store_name, config={"force": True})
    except errors.ClientError as e:
        if not _is_404(e):
            raise
        logger.info("Store %s already gone (404)", store_name)


def _extract_chunks(response) -> list:
    chunks = []
    candidates = getattr(response, "candidates", None) or []
    meta = getattr(candidates[0], "grounding_metadata", None) if candidates else None
    for gc in (getattr(meta, "grounding_chunks", None) or []):
        ctx = getattr(gc, "retrieved_context", None)
        if ctx is None:
            continue
        chunks.append((getattr(ctx, "title", None) or "", getattr(ctx, "text", None) or ""))
    return chunks


async def ask(store_name: str, game_name: str, question: str, model: str,
              timeout_s: float, max_citations: int) -> RulesAnswer:
    client = _client()
    start = time.monotonic()
    try:
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=model, contents=question,
                config=build_config(store_name, game_name)),
            timeout=timeout_s)
    except asyncio.TimeoutError:
        logger.warning("Rules lookup timed out for %s after %.2fs", store_name,
                       time.monotonic() - start)
        raise RulesError("❌ The rules lookup timed out. Try again.")
    except Exception:
        logger.exception("Rules lookup failed for %s", store_name)
        raise RulesError("❌ The rules lookup failed. The error has been logged.")
    answer = interpret_response(getattr(response, "text", None),
                                _extract_chunks(response), max_citations)
    logger.info("Rules lookup store=%s elapsed=%.2fs citations=%d found=%s",
                store_name, time.monotonic() - start, len(answer.citations),
                answer.found)
    return answer
