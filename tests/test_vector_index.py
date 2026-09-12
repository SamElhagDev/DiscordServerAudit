"""Tests for the in-memory cosine index and reciprocal-rank fusion.

Vectors are unit basis vectors so every expected cosine is exactly 1.0 or 0.0.
"""
import unittest

import numpy as np

from utils.vector_index import VectorIndex, rrf_fuse


def _unit(dim: int, axis: int) -> np.ndarray:
    v = np.zeros(dim, dtype="<f4")
    v[axis] = 1.0
    return v


class VectorIndexLoadTests(unittest.TestCase):
    def test_load_populates_ids_and_length(self):
        index = VectorIndex(3)

        index.load([(1, _unit(3, 0)), (2, _unit(3, 1))])

        self.assertEqual(len(index), 2)

    def test_load_replaces_previous_contents(self):
        index = VectorIndex(3)
        index.load([(1, _unit(3, 0)), (2, _unit(3, 1))])

        index.load([(9, _unit(3, 2))])

        self.assertEqual(len(index), 1)
        self.assertEqual(index.search(_unit(3, 2), 5), [(9, 1.0)])

    def test_load_skips_vectors_of_the_wrong_dimension(self):
        index = VectorIndex(3)

        index.load([(1, _unit(3, 0)), (2, _unit(4, 0))])

        self.assertEqual(len(index), 1)

    def test_load_of_nothing_leaves_an_empty_index(self):
        index = VectorIndex(3)

        index.load([])

        self.assertEqual(len(index), 0)
        self.assertEqual(index.search(_unit(3, 0), 5), [])


class VectorIndexAddTests(unittest.TestCase):
    def test_add_returns_the_number_of_new_vectors(self):
        index = VectorIndex(3)

        self.assertEqual(index.add([(1, _unit(3, 0)), (2, _unit(3, 1))]), 2)
        self.assertEqual(len(index), 2)

    def test_add_skips_ids_already_present(self):
        index = VectorIndex(3)
        index.add([(1, _unit(3, 0))])

        self.assertEqual(index.add([(1, _unit(3, 1)), (2, _unit(3, 1))]), 1)
        self.assertEqual(len(index), 2)

    def test_add_skips_vectors_of_the_wrong_dimension(self):
        index = VectorIndex(3)

        self.assertEqual(index.add([(1, _unit(4, 0))]), 0)
        self.assertEqual(len(index), 0)

    def test_add_into_an_empty_index_is_searchable(self):
        index = VectorIndex(3)

        index.add([(7, _unit(3, 0))])

        self.assertEqual(index.search(_unit(3, 0), 1), [(7, 1.0)])

    def test_vectors_stay_searchable_across_many_small_adds(self):
        """Growth bookkeeping must not drop or reorder earlier batches."""
        index = VectorIndex(3)
        for i in range(20):
            index.add([(i, _unit(3, i % 3))])

        self.assertEqual(len(index), 20)
        # A 0.5 floor admits only the exact axis-1 matches; orthogonal vectors score 0.0,
        # which the default floor of 0.0 would let through.
        hits = index.search(_unit(3, 1), 20, 0.5)
        # Axis-1 vectors are ids 1, 4, 7, ... — every one of them, and nothing else.
        self.assertEqual(sorted(mid for mid, _ in hits), [i for i in range(20) if i % 3 == 1])

    def test_load_after_add_still_replaces_everything(self):
        index = VectorIndex(3)
        index.add([(1, _unit(3, 0)), (2, _unit(3, 1))])

        index.load([(5, _unit(3, 2))])

        self.assertEqual(len(index), 1)
        self.assertEqual(index.search(_unit(3, 0), 5, -1.0), [(5, 0.0)])


class VectorIndexSearchTests(unittest.TestCase):
    def setUp(self):
        self.index = VectorIndex(3)
        self.index.load([(1, _unit(3, 0)), (2, _unit(3, 1)), (3, _unit(3, 2))])

    def test_search_ranks_by_cosine_descending(self):
        query = np.asarray([0.8, 0.6, 0.0], dtype="<f4")

        hits = self.index.search(query, 3, -1.0)

        self.assertEqual([mid for mid, _ in hits], [1, 2, 3])

    def test_search_caps_results_at_k(self):
        self.assertEqual(len(self.index.search(_unit(3, 0), 2, -1.0)), 2)

    def test_search_with_k_larger_than_the_index_returns_everything(self):
        self.assertEqual(len(self.index.search(_unit(3, 0), 99, -1.0)), 3)

    def test_search_applies_the_similarity_floor(self):
        hits = self.index.search(_unit(3, 0), 3, 0.5)

        self.assertEqual(hits, [(1, 1.0)])

    def test_search_rejects_a_query_of_the_wrong_dimension(self):
        self.assertEqual(self.index.search(_unit(4, 0), 3), [])

    def test_search_of_an_empty_index_is_empty(self):
        self.assertEqual(VectorIndex(3).search(_unit(3, 0), 3), [])

    def test_search_with_no_query_is_empty(self):
        self.assertEqual(self.index.search(None, 3), [])

    def test_search_with_non_positive_k_is_empty(self):
        self.assertEqual(self.index.search(_unit(3, 0), 0), [])

    def test_over_allocated_slack_rows_never_outrank_a_live_vector(self):
        """The index over-allocates its buffer; unused rows must not enter the ranking.

        Slack rows are all-zero, so they score exactly 0.0 — which beats any live vector with
        a *negative* cosine. A search that ran over the whole buffer instead of the live rows
        would rank a slack row second here and index self._ids out of range.
        """
        index = VectorIndex(3)
        opposite = np.asarray([-1.0, 0.0, 0.0], dtype="<f4")
        index.add([(1, _unit(3, 0))])       # cosine +1.0 with the query
        index.add([(2, opposite)])          # cosine -1.0 — ranks below all-zero slack

        hits = index.search(_unit(3, 0), 2, -1.0)

        self.assertEqual([mid for mid, _ in hits], [1, 2])
        self.assertAlmostEqual(hits[1][1], -1.0, places=5)


class RrfFuseTests(unittest.TestCase):
    def test_a_document_ranked_well_in_both_lists_wins(self):
        self.assertEqual(rrf_fuse([3, 1, 2], [3, 2, 1], k=60)[0], 3)

    def test_disjoint_lists_interleave_by_rank(self):
        self.assertEqual(rrf_fuse([1, 2], [3, 4], k=60), [1, 3, 2, 4])

    def test_an_empty_list_leaves_the_other_order_intact(self):
        self.assertEqual(rrf_fuse([1, 2, 3], [], k=60), [1, 2, 3])

    def test_two_empty_lists_fuse_to_nothing(self):
        self.assertEqual(rrf_fuse([], [], k=60), [])

    def test_ties_keep_first_seen_order(self):
        # Same rank in each list -> identical scores; keyword list is seen first.
        self.assertEqual(rrf_fuse([1], [2], k=60), [1, 2])

    def test_ids_are_returned_once_even_when_in_both_lists(self):
        self.assertEqual(sorted(rrf_fuse([1, 2], [2, 1], k=60)), [1, 2])


if __name__ == "__main__":
    unittest.main()
