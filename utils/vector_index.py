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

    # Smallest buffer to jump to on the first growth, so tiny indexes don't reallocate
    # on every single batch.
    _MIN_CAPACITY = 64

    def __init__(self, dim: int):
        self._dim = int(dim)
        self._ids: list[int] = []
        # Backing buffer. Rows [0:len(self._ids)] are live; anything past that is
        # over-allocated slack and must never be searched.
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

    def _reserve(self, needed: int) -> None:
        """Grow the backing buffer to hold at least *needed* rows. Caller holds the lock."""
        capacity = self._matrix.shape[0]
        if needed <= capacity:
            return
        new_capacity = max(needed, capacity * 2, self._MIN_CAPACITY)
        grown = np.zeros((new_capacity, self._dim), dtype="<f4")
        live = len(self._ids)
        if live:
            grown[:live] = self._matrix[:live]
        self._matrix = grown

    def add(self, pairs) -> int:
        """Append new (id, vector) pairs; skip ids already present or wrong dim. Returns count added.

        Capacity doubles rather than reallocating per call. The reconciler drains a backfill
        through this method one batch at a time, so a copy-the-whole-matrix append would make
        the backfill cost O(N^2) in bytes moved.
        """
        with self._lock:
            live = len(self._ids)
            new_ids: list[int] = []
            new_vecs: list[np.ndarray] = []
            for mid, vec in pairs:
                mid = int(mid)
                if mid in self._pos:
                    continue
                v = np.asarray(vec, dtype="<f4")
                if v.shape[0] != self._dim:
                    continue
                # Recorded during collection so a duplicate id *within this batch* is skipped.
                self._pos[mid] = live + len(new_ids)
                new_ids.append(mid)
                new_vecs.append(v)
            if not new_ids:
                return 0
            self._reserve(live + len(new_ids))
            self._matrix[live:live + len(new_ids)] = np.vstack(new_vecs)
            self._ids.extend(new_ids)
            return len(new_ids)

    def search(self, query_vec, k: int, min_sim: float = 0.0):
        """Return up to k (id, cosine) desc for a normalized query vector. [] if empty/invalid.

        Runs synchronously on the caller's thread. At single-server scale the matmul costs a
        few milliseconds (~3 ms over 20k vectors at dim 768), so the fact-check path calls it
        directly; past roughly 50k vectors, move it to ``asyncio.to_thread`` as the index
        load already is.
        """
        if query_vec is None or k <= 0:
            return []
        with self._lock:
            live = len(self._ids)
            if not live:
                return []
            q = np.asarray(query_vec, dtype="<f4")
            if q.shape[0] != self._dim:
                return []
            # Slice to live rows: the buffer is over-allocated, and all-zero slack would
            # otherwise score 0.0 and pass a 0.0 similarity floor.
            sims = self._matrix[:live] @ q  # both normalized → cosine
            # O(N) partition + O(k log k) sort; a full argsort would be O(N log N).
            k = min(k, live)
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
