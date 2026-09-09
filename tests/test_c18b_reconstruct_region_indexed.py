import importlib
import json
import os
import sys
import unittest
from collections import defaultdict
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
C18B_DIR = PROJECT_ROOT / "tools" / "analysis_v2" / "c18b_score015"
sys.path.insert(0, str(C18B_DIR))
try:
    GEOMETRY = importlib.import_module("graph_constrained_instance_separation")
    IDENTITY = importlib.import_module("identity_graph_v3")
finally:
    sys.path.remove(str(C18B_DIR))


def old_reconstruct(grown, fitc, groups, membership, intensity_weight, direction_weight):
    """Frozen pre-P0C-B reference, intentionally test-only."""
    final = np.zeros_like(grown, np.uint16)
    coordinate_grid = np.indices(fitc.shape, dtype=np.float32)
    for parent_id, group in enumerate(groups, 1):
        region = grown == parent_id
        by_community = defaultdict(list)
        for fragment, path in enumerate(group, 1):
            by_community[membership["P{}:F{}".format(parent_id, fragment)]].append(path)
        ids = sorted(by_community)
        if len(ids) == 1:
            final[region] = ids[0]
            continue
        costs = []
        for cid in ids:
            geoms = [GEOMETRY._path_geometry(path, fitc.shape, coordinate_grid)
                     for path in by_community[cid]]
            distances = np.stack([g[0] for g in geoms])
            nearest = np.argmin(distances, axis=0)
            distance = np.min(distances, axis=0)
            direction = np.take_along_axis(
                np.stack([g[1] for g in geoms]), nearest[None], axis=0)[0]
            seed_mask = np.maximum.reduce([g[2] for g in geoms]) > 0
            level = max(float(np.median(fitc[seed_mask])), 1.0)
            intensity = np.abs(fitc-level) / level
            costs.append(distance + intensity_weight*intensity*np.maximum(distance, 1) +
                         direction_weight*direction*np.maximum(distance, 1))
        winner = np.argmin(np.stack(costs), axis=0)
        for k, cid in enumerate(ids):
            final[region & (winner == k)] = cid
    dense = np.zeros_like(final)
    for new, old in enumerate(np.unique(final[final > 0]), 1):
        dense[final == old] = new
    return dense


class C18BRegionIndexedReconstructionTests(unittest.TestCase):
    def assert_geometry_equal(self, path, shape, region):
        grid = np.indices(shape, dtype=np.float32)
        old_distance, old_align, unused = GEOMETRY._path_geometry(path, shape, grid)
        ys, xs = np.nonzero(region)
        distance, align, unused = GEOMETRY._path_geometry_region(path, shape, ys, xs)
        np.testing.assert_array_equal(old_distance[ys, xs], distance)
        np.testing.assert_array_equal(old_align[ys, xs], align)

    def test_region_geometry_and_final_labels_match_frozen_reference(self):
        shape = (17, 23)
        grown = np.zeros(shape, np.uint16)
        grown[0:9, 0:10] = 1       # small, edge-touching parent
        grown[7:17, 8:23] = 2      # large parent
        fitc = np.arange(np.prod(shape), dtype=np.float32).reshape(shape) + 1
        groups = [[
            np.asarray([[0, 0], [2, 2], [4, 4]], np.int32),
            np.asarray([[8, 1], [7, 3], [6, 5]], np.int32),
        ], [
            np.asarray([[9, 8], [11, 10], [13, 12]], np.int32),
            np.asarray([[20, 9], [18, 11], [16, 13]], np.int32),
            np.asarray([[12, 15]], np.int32),
        ]]
        membership = {"P1:F1": 2, "P1:F2": 1,
                      "P2:F1": 3, "P2:F2": 1, "P2:F3": 2}
        for parent_id, group in enumerate(groups, 1):
            region = grown == parent_id
            for path in group:
                self.assert_geometry_equal(path, shape, region)
        expected = old_reconstruct(grown, fitc, groups, membership, 3.0, 0.8)
        actual = IDENTITY.reconstruct(grown, fitc, groups, membership, 3.0, 0.8)
        np.testing.assert_array_equal(expected, actual)

    def test_tie_keeps_first_sorted_community_and_single_community_is_unchanged(self):
        grown = np.ones((7, 11), np.uint16)
        fitc = np.full(grown.shape, 50, np.float32)
        path = np.asarray([[2, 3], [3, 3]], np.int32)
        groups = [[path, path.copy()]]
        membership = {"P1:F1": 9, "P1:F2": 4}
        expected = old_reconstruct(grown, fitc, groups, membership, 3.0, 0.8)
        actual = IDENTITY.reconstruct(grown, fitc, groups, membership, 3.0, 0.8)
        np.testing.assert_array_equal(expected, actual)
        self.assertTrue(np.all(actual == 1))
        single = IDENTITY.reconstruct(grown, fitc, [[path]], {"P1:F1": 4}, 3.0, 0.8)
        self.assertTrue(np.all(single == 1))

    @unittest.skipUnless(os.environ.get("C18B_REAL_DIFF"),
                         "set C18B_REAL_DIFF=1 to run the selected real field")
    def test_selected_real_field_matches_old_reference(self):
        """Run one real C18B field and compare its captured reconstruct inputs."""
        field = os.environ.get("C18B_REAL_FIELD", "ZBFY023-C-1")
        case = os.environ.get("C18B_REAL_CASE", "CASE20260908102941")
        run = os.environ.get("C18B_REAL_RUN", "20260908_103450_acad3c")
        sys.path.insert(0, str(C18B_DIR))
        try:
            pipeline = importlib.import_module("run_pipeline")
        finally:
            sys.path.remove(str(C18B_DIR))
        original = pipeline.identity_reconstruct
        observed = {}
        compare_reference = os.environ.get("C18B_REAL_REFERENCE", "1") == "1"

        def compare(grown, fitc, groups, membership, intensity_weight, direction_weight):
            actual = original(grown, fitc, groups, membership,
                              intensity_weight, direction_weight)
            if compare_reference:
                expected = old_reconstruct(grown, fitc, groups, membership,
                                           intensity_weight, direction_weight)
                np.testing.assert_array_equal(expected, actual)
            observed["pixels"] = [int(np.count_nonzero(grown == parent_id))
                                  for parent_id in range(1, len(groups) + 1)]
            return actual

        pipeline.identity_reconstruct = compare
        try:
            cfg = json.loads((C18B_DIR / "config" / "frozen_parameters.json").read_text(
                encoding="utf-8"))
            input_path = (PROJECT_ROOT / "workspace" / "cases" / case / "analysis_v2" /
                          "protein3" / "runs" / run / "input" / (field + "_FITC.tif"))
            output_name = os.environ.get("C18B_REAL_OUTPUT", "p0c_b_" + field)
            pipeline.run_one(input_path, PROJECT_ROOT / "workspace" / output_name, cfg)
        finally:
            pipeline.identity_reconstruct = original
        print("[P0C_B_DIFFERENTIAL] field={} parent_pixels={}".format(
            field, observed["pixels"]))


if __name__ == "__main__":
    unittest.main()
