import sys
import types
import unittest

import numpy as np


if "torch" not in sys.modules:
    sys.modules["torch"] = types.ModuleType("torch")
if "cellpose" not in sys.modules:
    cellpose = types.ModuleType("cellpose")
    cellpose.models = types.ModuleType("cellpose.models")
    sys.modules["cellpose"] = cellpose
    sys.modules["cellpose.models"] = cellpose.models

from tools.analysis_v2.direct_cellpose_worker import normalize_label_connectivity
from core.analysis_v2.head_calibration_service import HeadCalibrationService


class HeadLabelConnectivityTests(unittest.TestCase):
    def test_connected_labels_are_unchanged(self):
        labels = np.zeros((12, 12), dtype=np.uint16)
        labels[1:4, 1:4] = 1
        labels[6:10, 7:11] = 2

        normalized, changes = normalize_label_connectivity(labels)

        self.assertTrue(np.array_equal(normalized, labels))
        self.assertEqual(changes, [])

    def test_customer_failure_shape_keeps_main_component_only(self):
        labels = np.zeros((40, 40), dtype=np.uint16)
        labels[1, 1] = 1
        labels[5:34, 3:38] = 203
        labels[34, 3:9] = 203
        labels[38, 38] = 203
        self.assertEqual(int(np.count_nonzero(labels == 203)), 1022)

        normalized, changes = normalize_label_connectivity(labels)

        self.assertEqual(int(np.count_nonzero(normalized == 203)), 1021)
        self.assertEqual(int(normalized[38, 38]), 0)
        self.assertEqual(np.unique(normalized[normalized > 0]).tolist(), [1, 203])
        self.assertEqual(
            int(np.unique(normalized[normalized > 0]).size),
            int(np.unique(labels[labels > 0]).size),
        )
        self.assertEqual(changes, [{
            "object_id": 203,
            "component_count": 2,
            "kept_pixels": 1021,
            "removed_pixels": 1,
        }])

    def test_multiple_abnormal_labels_are_normalized_independently(self):
        labels = np.zeros((14, 18), dtype=np.uint16)
        labels[1:4, 1:4] = 1
        labels[8, 1] = 1
        labels[5:9, 8:12] = 2
        labels[1:3, 15:17] = 2

        normalized, changes = normalize_label_connectivity(labels)

        self.assertEqual(int(np.count_nonzero(normalized == 1)), 9)
        self.assertEqual(int(np.count_nonzero(normalized == 2)), 16)
        self.assertEqual(len(changes), 2)

    def test_tied_largest_component_uses_top_left_scan_order(self):
        labels = np.zeros((9, 9), dtype=np.uint16)
        labels[1:3, 1:3] = 7
        labels[6:8, 6:8] = 7

        first, _changes = normalize_label_connectivity(labels)
        second, _changes = normalize_label_connectivity(labels)

        self.assertTrue(np.array_equal(first, second))
        self.assertEqual(int(np.count_nonzero(first[1:3, 1:3] == 7)), 4)
        self.assertEqual(int(np.count_nonzero(first[6:8, 6:8] == 7)), 0)

    def test_normalized_labels_pass_existing_final_validator(self):
        labels = np.zeros((10, 12), dtype=np.uint16)
        labels[1:4, 1:4] = 1
        labels[8, 1] = 1
        labels[5:9, 7:11] = 2

        normalized, _changes = normalize_label_connectivity(labels)

        HeadCalibrationService._validate_independent_objects(normalized)


if __name__ == "__main__":
    unittest.main()
