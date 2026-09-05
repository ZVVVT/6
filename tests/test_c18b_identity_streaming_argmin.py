import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
C18B_DIR = PROJECT_ROOT / "tools" / "analysis_v2" / "c18b_score015"
sys.path.insert(0, str(C18B_DIR))
try:
    IDENTITY = importlib.import_module("identity_graph_v3")
finally:
    sys.path.remove(str(C18B_DIR))


def legacy_argmin(costs):
    return np.argmin(np.stack(costs), axis=0)


class C18BIdentityStreamingArgminTests(unittest.TestCase):
    def assert_streaming_matches_legacy(self, costs):
        expected = legacy_argmin(costs)
        actual = IDENTITY._streaming_argmin(iter(costs))
        self.assertEqual(actual.dtype, np.dtype(np.intp))
        self.assertTrue(np.array_equal(expected, actual))

    def test_random_costs_match_legacy_for_single_and_multiple_candidates(self):
        rng = np.random.RandomState(6023)
        for count in (1, 2, 7, 31):
            costs = [rng.uniform(-2, 4, (17, 23)).astype(np.float32)
                     for unused in range(count)]
            self.assert_streaming_matches_legacy(costs)

    def test_equal_cost_ties_choose_first_candidate(self):
        costs = [np.ones((4, 6), np.float32) for unused in range(5)]
        actual = IDENTITY._streaming_argmin(iter(costs))
        np.testing.assert_array_equal(actual, np.zeros((4, 6), np.intp))
        self.assert_streaming_matches_legacy(costs)

    def test_large_candidate_count_does_not_stack_cost_maps(self):
        costs = (np.full((3, 5), value, np.float32)
                 for value in range(432, 0, -1))
        with mock.patch.object(IDENTITY.np, "stack",
                               side_effect=AssertionError("must not stack costs")):
            actual = IDENTITY._streaming_argmin(costs)
        np.testing.assert_array_equal(actual, np.full((3, 5), 431, np.intp))

    def test_reconstruct_labels_exactly_match_legacy_winner(self):
        grown = np.ones((13, 19), dtype=np.uint16)
        fitc = np.arange(grown.size, dtype=np.float32).reshape(grown.shape) + 1
        groups = [[
            np.asarray([[1, 1], [2, 2], [3, 3]], dtype=np.int32),
            np.asarray([[9, 2], [9, 3], [10, 4]], dtype=np.int32),
            np.asarray([[16, 8], [15, 9], [14, 10]], dtype=np.int32),
        ]]
        membership = {"P1:F1": 3, "P1:F2": 1, "P1:F3": 2}
        captured = {}
        original = IDENTITY._streaming_argmin

        def compare_with_legacy(cost_maps):
            costs = list(cost_maps)
            expected = legacy_argmin(costs)
            actual = original(iter(costs))
            self.assertTrue(np.array_equal(expected, actual))
            captured["winner_equal"] = True
            return actual

        with mock.patch.object(IDENTITY, "_streaming_argmin",
                               side_effect=compare_with_legacy):
            streaming_labels = IDENTITY.reconstruct(
                grown, fitc, groups, membership, 3.0, 0.8
            )

        with mock.patch.object(IDENTITY, "_streaming_argmin",
                               side_effect=lambda maps: legacy_argmin(list(maps))):
            legacy_labels = IDENTITY.reconstruct(
                grown, fitc, groups, membership, 3.0, 0.8
            )
        self.assertTrue(captured["winner_equal"])
        self.assertTrue(np.array_equal(legacy_labels, streaming_labels))


if __name__ == "__main__":
    unittest.main()
