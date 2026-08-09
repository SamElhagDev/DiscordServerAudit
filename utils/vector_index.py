"""In-memory cosine vector index + reciprocal-rank fusion for fact-check retrieval.

Holds normalized float32 vectors keyed by ``message_context.id``. Search is a single NumPy
matmul (cosine, since vectors are pre-normalized), so per-query cost is independent of history
size at single-server scale.
"""
import logging
import threading

import numpy as np

logger = logging.getLogger(__name__)


class VectorIndex:
    """Dense in-memory index of (id, normalized-vector) pairs for top-k cosine search."""

    def __init__(self, dim: int):
        self._dim = int(dim)
        self._ids: list[int] = []
        self._matrix = np.zeros((0, self._dim), dtype="<f4")
        self._pos: dict[int, int] = {}
        self._lock = threading.Lock()

    @property
    def dim(self) -> int:
        return self._dim

    def __len__(self) -> int:
        return len(self._ids)

    def load(self, pairs) -> None:
        """Replace all contents from (id, vector) pairs (wrong-dim vectors skipped)."""
        ids: list[int] = []
        vecs: list[np.ndarray] = []
        for mid, vec in pairs:
            v = np.asarray(vec, dtype="<f4")
            if v.shape[0] != self._dim:
                continue
            ids.append(int(mid))
            vecs.append(v)
        matrix = np.vstack(vecs).astype("<f4") if vecs else np.zeros((0, self._dim), dtype="<f4")
        with self._lock:
            self._ids = ids
            self._matrix = matrix
            self._pos = {mid: i for i, mid in enumerate(ids)}
        logger.info("VectorIndex loaded | vectors=%d dim=%d", len(ids), self._dim)

    def add(self, pairs) -> int:
        """Append new (id, vector) pairs; skip ids already present or wrong dim. Returns count added."""
        with self._lock:
            new_ids: list[int] = []
            new_vecs: list[np.ndarray] = []
            for mid, vec in pairs:
                mid = int(mid)
                if mid in self._pos:
                    continue
                v = np.asarray(vec, dtype="<f4")
                if v.shape[0] != self._dim:
                    continue
                self._pos[mid] = len(self._ids) + len(new_ids)
                new_ids.append(mid)
                new_vecs.append(v)
            if new_ids:
                block = np.vstack(new_vecs).astype("<f4")
                self._matrix = np.vstack([self._matrix, block]) if len(self._ids) else block
                self._ids.extend(new_ids)
            return len(new_ids)

    def search(self, query_vec, k: int, min_sim: float = 0.0):
        """Return up to k (id, cosine) desc for a normalized query vector. [] if empty/invalid."""
        if query_vec is None or k <= 0:
            return []
        with self._lock:
            if not self._ids:
                return []
            q = np.asarray(query_vec, dtype="<f4")
            if q.shape[0] != self._dim:
                return []
            sims = self._matrix @ q  # both normalized → cosine
            # O(N) partition + O(k log k) sort; a full argsort would be O(N log N).
            k = min(k, sims.shape[0])
            top = np.argpartition(-sims, k - 1)[:k]
            top = top[np.argsort(-sims[top])]
            return [(self._ids[i], float(sims[i])) for i in top if sims[i] >= min_sim]


def rrf_fuse(keyword_ids, semantic_ids, k: int = 60) -> list:
    """Reciprocal-rank fusion of two ranked id lists → a single fused id order (desc).

    A doc at 1-based rank ``r`` contributes ``1 / (k + r)``; scores sum across lists. Being
    rank-based, it needs no score normalization between bm25 and cosine.
    """
    scores: dict[int, float] = {}
    first_seen: dict[int, int] = {}
    seq = 0
    for lst in (keyword_ids, semantic_ids):
        for rank, mid in enumerate(lst, start=1):
            mid = int(mid)
            if mid not in scores:
                scores[mid] = 0.0
                first_seen[mid] = seq
                seq += 1
            scores[mid] += 1.0 / (k + rank)
    return sorted(scores, key=lambda m: (-scores[m], first_seen[m]))
