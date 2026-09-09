"""Strict final-output ROI metric differentials for P1B."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
C18B_DIR = PROJECT_ROOT / "tools" / "analysis_v2" / "c18b_score015"
sys.path.insert(0, str(C18B_DIR))
try:
    from graph_constrained_instance_separation import _mask_shape_metrics
    from run_pipeline import _final_instance_metrics
finally:
    sys.path.remove(str(C18B_DIR))


def _old_metrics(final, fitc):
    metrics = []
    for iid in range(1, int(final.max()) + 1):
        mask = final == iid
        length, branches = _mask_shape_metrics(mask)
        metrics.append({"instance_id": iid, "area": int(mask.sum()),
                        "FITC_integrated": float(fitc[mask].sum()),
                        "skeleton_length": length, "branch_points": branches})
    return metrics


class C18BFinalOutputRoiTests(unittest.TestCase):
    def assert_metrics_equal(self, final, fitc):
        self.assertEqual(_old_metrics(final, fitc),
                         _final_instance_metrics(final, fitc))

    def test_synthetic_metrics_match_full_frame_at_all_edges(self):
        labels = np.zeros((25, 31), dtype=np.uint16)
        # Single pixel, line, curve, branch, multi-component, all edges and
        # corners, near-full image, plus a different adjacent label.
        labels[1:-1, 1:-1] = 14
        labels[12, 15] = 1
        labels[10, 4:10] = 2
        labels[4:9, 14] = 3
        labels[8, 15:19] = 3
        labels[14, 21:26] = 4
        labels[15:19, 23] = 4
        labels[2, 2] = labels[5, 5] = 5
        labels[0, 8:13] = 6
        labels[-1, 8:13] = 7
        labels[7:12, 0] = 8
        labels[7:12, -1] = 9
        labels[0, 0] = 10
        labels[0, -1] = 11
        labels[-1, 0] = 12
        labels[-1, -1] = 13
        labels[2, 2] = 5
        labels[2:5, 6:9] = 15
        labels[2:5, 9:12] = 16
        fitc = (np.arange(labels.size, dtype=np.float32).reshape(labels.shape)
                / np.float32(7.0))
        self.assert_metrics_equal(labels, fitc)

    def test_real_tier1_023_and_022_all_instances_match_full_frame(self):
        cases = (
            ("CASE20260908102941", "20260908_103450_acad3c", "ZBFY023-C-1"),
            ("CASE20260908104656", "20260908_104716_af7f2c", "ZBFY022-C-1"),
        )
        for case, run, field in cases:
            root = (PROJECT_ROOT / "workspace" / "cases" / case / "analysis_v2" /
                    "protein3" / "runs" / run)
            labels_path = (root / "segmentation" / "c18b_score015" / field /
                           (field + "_FITC") / "06_final_tail_instances.tif")
            fitc_path = root / "input" / (field + "_FITC.tif")
            labels = cv2.imread(str(labels_path), cv2.IMREAD_UNCHANGED)
            fitc = cv2.imread(str(fitc_path), cv2.IMREAD_UNCHANGED)
            self.assertIsNotNone(labels, str(labels_path))
            self.assertIsNotNone(fitc, str(fitc_path))
            self.assertEqual(int(labels.max()), len(_old_metrics(labels, fitc)))
            self.assert_metrics_equal(labels, fitc)


if __name__ == "__main__":
    unittest.main()
