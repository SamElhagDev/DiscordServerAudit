"""Gemini embedding helpers for fact-check semantic context retrieval.

Thin wrapper over the shared client in ``utils.gemini``. Every call returns ``None`` rather than
raising on no-key/failure, so callers degrade to keyword + recency retrieval.
"""
import logging

import numpy as np
from google.genai import types as genai_types

import config
from utils.gemini import get_client

logger = logging.getLogger(__name__)


def model_name() -> str:
    """Active embedding model. Stored vectors are keyed by it, so callers must not inline it."""
    return config.get("factcheck.context.semantic.model", "gemini-embedding-001")


def dimensions() -> int:
    """Active vector width. Likewise keyed on — see model_name()."""
    return int(config.get("factcheck.context.semantic.dimensions", 768))


def semantic_enabled() -> bool:
    return bool(config.get("factcheck.context.semantic.enabled", True))


def embed_available() -> bool:
    """True if semantic retrieval is enabled AND a Gemini client is configured."""
    return semantic_enabled() and get_client() is not None


def _normalize(values) -> np.ndarray:
    """L2-normalize to a float32 vector so cosine similarity reduces to a dot product."""
    v = np.asarray(values, dtype="<f4")
    norm = float(np.linalg.norm(v))
    if norm:
        v = v / norm
    return v.astype("<f4")


async def _embed(texts: list[str], task_type: str) -> list[np.ndarray] | None:
    client = get_client()
    if client is None or not texts:
        return None
    try:
        resp = await client.aio.models.embed_content(
            model=model_name(),
            contents=texts,
            config=genai_types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=dimensions(),
            ),
        )
    except Exception:
        logger.warning("Embedding call failed | task=%s n=%d", task_type, len(texts), exc_info=True)
        return None
    embeddings = getattr(resp, "embeddings", None)
    if not embeddings or len(embeddings) != len(texts):
        got = None if embeddings is None else len(embeddings)
        logger.warning("Embedding response malformed | got=%s want=%d", got, len(texts))
        return None
    out: list[np.ndarray] = []
    for e in embeddings:
        values = getattr(e, "values", None)
        if values is None:
            logger.warning("Embedding entry missing values")
            return None
        out.append(_normalize(values))
    return out


async def embed_documents(texts: list[str]) -> list[np.ndarray] | None:
    """Batch-embed stored messages (RETRIEVAL_DOCUMENT). Order matches input. None on failure."""
    return await _embed(texts, "RETRIEVAL_DOCUMENT")


async def embed_query(text: str) -> np.ndarray | None:
    """Embed a single query (RETRIEVAL_QUERY). None on no-key/failure/empty."""
    if not text or not text.strip():
        return None
    result = await _embed([text], "RETRIEVAL_QUERY")
    return result[0] if result else None
