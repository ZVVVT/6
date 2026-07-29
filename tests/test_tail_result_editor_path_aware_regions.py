from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from tools.analysis_v2.tail_legacy.tail_result_editor_v2_2 import (
    build_path_aware_region_labels,
)


def make_record(head_id, fragment_ids, points_xy):
    return SimpleNamespace(
        head_id=head_id,
        selected_fragment_ids=list(fragment_ids),
        selected_points_xy=np.asarray(points_xy, dtype=np.float32),
    )


class PathAwareTailRegionTests(unittest.TestCase):
    def test_unique_fragment_remains_pixel_identical(self):
        fragments = np.zeros((12, 14), dtype=np.uint32)
        fragments[2:10, 3:12] = 7
        record = make_record(11, [7], [(4, 5), (10, 5)])

        labels, conflicts = build_path_aware_region_labels(
            fragments,
            [record],
        )

        self.assertTrue(np.array_equal(labels == 11, fragments == 7))
        self.assertEqual(int(np.count_nonzero(labels == 11)), 72)
        self.assertEqual(conflicts, [])

    def test_shared_fragment_is_split_without_overlap(self):
        fragments = np.zeros((15, 15), dtype=np.uint32)
        fragments[2:13, 2:13] = 5
        records = [
            make_record(21, [5], [(3, 4), (11, 4)]),
            make_record(22, [5], [(3, 10), (11, 10)]),
        ]

        labels, conflicts = build_path_aware_region_labels(
            fragments,
            records,
        )

        head_21 = labels == 21
        head_22 = labels == 22
        self.assertGreater(int(np.count_nonzero(head_21)), 0)
        self.assertGreater(int(np.count_nonzero(head_22)), 0)
        self.assertFalse(np.any(head_21 & head_22))
        self.assertFalse(np.any((labels > 0) & (fragments != 5)))
        self.assertEqual(conflicts, [])

    def test_restored_state_paths_are_split_on_save(self):
        fragments = np.zeros((15, 15), dtype=np.uint32)
        fragments[2:13, 2:13] = 9
        state = {
            "version": "tail_result_editor_v2_2",
            "records": [
                {
                    "head_id": 31,
                    "selected_fragment_ids": [9],
                    "selected_points_xy": [[3, 4], [11, 4]],
                },
                {
                    "head_id": 32,
                    "selected_fragment_ids": [9],
                    "selected_points_xy": [[3, 10], [11, 10]],
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_path = Path(temporary_directory) / "editor_state_v2_2.json"
            state_path.write_text(
                json.dumps(state),
                encoding="utf-8",
            )
            restored = json.loads(state_path.read_text(encoding="utf-8"))

        records = [
            make_record(
                item["head_id"],
                item["selected_fragment_ids"],
                item["selected_points_xy"],
            )
            for item in restored["records"]
        ]
        labels, conflicts = build_path_aware_region_labels(
            fragments,
            records,
        )

        self.assertGreater(int(np.count_nonzero(labels == 31)), 0)
        self.assertGreater(int(np.count_nonzero(labels == 32)), 0)
        self.assertLess(
            int(np.count_nonzero(labels == 31)),
            int(np.count_nonzero(fragments == 9)),
        )
        self.assertLess(
            int(np.count_nonzero(labels == 32)),
            int(np.count_nonzero(fragments == 9)),
        )
        self.assertEqual(conflicts, [])


if __name__ == "__main__":
    unittest.main()
