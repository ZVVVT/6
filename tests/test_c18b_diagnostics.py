from __future__ import annotations

import sys
import csv
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
C18B_DIR = PROJECT_ROOT / "tools" / "analysis_v2" / "c18b_score015"
sys.path.insert(0, str(C18B_DIR))
try:
    from analyze_shadow_gate import (
        boundary_review_overlay,
        boundary_review_rows,
        generate_boundary_review,
        sweep,
    )
    from combine_final_instance_diagnostics import combine
    from extreme_fragment_filter_ab import (
        EXPERIMENT_NAME,
        REMOVAL_CSV_NAME,
        apply_extreme_fragment_filter,
    )
    from run_pipeline import (
        candidate_diagnostic_rows,
        final_instance_diagnostic_rows,
        final_instance_id_overlay,
        shadow_community_rows,
        shadow_group_rows,
        write_csv,
    )
finally:
    sys.path.remove(str(C18B_DIR))


class C18BCandidateDiagnosticTests(unittest.TestCase):
    def _write_extreme_filter_fixture(self, directory):
        import cv2
        labels = np.zeros((8, 10), dtype=np.uint16)
        labels[1:3, 1:4] = 1
        labels[4:7, 5:9] = 2
        cv2.imwrite(str(directory / "06_final_tail_instances.tif"), labels)
        write_csv(directory / "shadow_communities.csv", [
            {"identity_community_id": 1, "dense_final_instance_id": 1,
             "max_candidate_path_length": 79.999,
             "source_candidate_ids": "10"},
            {"identity_community_id": 2, "dense_final_instance_id": 2,
             "max_candidate_path_length": 80.0,
             "source_candidate_ids": "20"},
        ])
        write_csv(directory / "final_instance_diagnostics.csv", [
            {"final_instance_id": 1, "identity_community_id": 1,
             "pixel_area": 6, "integrated_fitc": 12, "manual_class": ""},
            {"final_instance_id": 2, "identity_community_id": 2,
             "pixel_area": 12, "integrated_fitc": 24, "manual_class": ""},
        ])
        write_csv(directory / "manual.csv", [
            {"final_instance_id": 1, "manual_class": "FALSE_FRAGMENT"},
            {"final_instance_id": 2, "manual_class": "TRUE_TAIL"},
        ])
        return labels

    def test_extreme_filter_defaults_off_and_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            labels = self._write_extreme_filter_fixture(directory)
            result = apply_extreme_fragment_filter(directory)
            self.assertFalse(result["enabled"])
            self.assertFalse((directory / EXPERIMENT_NAME).exists())
            self.assertFalse((directory / REMOVAL_CSV_NAME).exists())
            import cv2
            np.testing.assert_array_equal(cv2.imread(
                str(directory / "06_final_tail_instances.tif"),
                cv2.IMREAD_UNCHANGED), labels)

    def test_extreme_filter_only_zeros_lt_80_and_preserves_survivors(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            labels = self._write_extreme_filter_fixture(directory)
            baseline_bytes = (directory / "06_final_tail_instances.tif").read_bytes()
            result = apply_extreme_fragment_filter(
                directory, directory / "manual.csv", True
            )
            import cv2
            experimental = cv2.imread(str(directory / EXPERIMENT_NAME),
                                      cv2.IMREAD_UNCHANGED)
            self.assertEqual(result["removed_ids"], [1])
            self.assertEqual(result["false_removed"], 1)
            self.assertEqual(result["true_removed"], 0)
            self.assertFalse(np.any(experimental == 1))
            np.testing.assert_array_equal(experimental == 2, labels == 2)
            changed = labels != experimental
            np.testing.assert_array_equal(changed, labels == 1)
            self.assertEqual(
                (directory / "06_final_tail_instances.tif").read_bytes(),
                baseline_bytes
            )
            with (directory / REMOVAL_CSV_NAME).open(
                    "r", newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["remove_reason"],
                             "community_max_path_lt_80")

    def test_extreme_filter_does_not_rerun_identity_or_separation(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self._write_extreme_filter_fixture(directory)
            import run_pipeline
            with mock.patch.object(run_pipeline, "cluster") as identity_graph, \
                    mock.patch.object(run_pipeline, "identity_reconstruct") as separation:
                apply_extreme_fragment_filter(directory, None, True)
            identity_graph.assert_not_called()
            separation.assert_not_called()

    def test_shadow_snapshots_are_complete_read_only_and_unfiltered(self):
        paths = [
            np.asarray([[1, 1], [2, 1], [3, 1]], dtype=np.int32),
            np.asarray([[3, 2], [4, 2]], dtype=np.int32),
            np.asarray([[7, 7], [7, 8]], dtype=np.int32),
        ]
        rows = [
            {"candidate_id": 10, "length": 2.0},
            {"candidate_id": 11, "length": 1.0},
            {"candidate_id": 12, "length": 3.5},
        ]
        merged = [[0, 1], [2]]
        membership = {"P1:F1": 1, "P1:F2": 1, "P2:F1": 2}
        communities = [["P1:F1", "P1:F2"], ["P2:F1"]]
        paths_before = [path.copy() for path in paths]
        rows_before = [dict(row) for row in rows]

        groups = shadow_group_rows(rows, paths, merged)
        community_rows = shadow_community_rows(
            rows, merged, membership, communities
        )

        self.assertEqual(len(groups), len(merged))
        self.assertEqual(len(community_rows), len(communities))
        self.assertEqual(groups[0]["source_candidate_ids"], "10;11")
        self.assertEqual(groups[0]["max_candidate_path_length"], 2.0)
        self.assertEqual(groups[0]["total_candidate_path_length"], 3.0)
        self.assertEqual(community_rows[0]["source_candidate_ids"], "10;11")
        self.assertEqual(community_rows[0]["community_member_node_count"], 2)
        self.assertEqual(community_rows[1]["dense_final_instance_id"], 2)
        self.assertEqual(rows, rows_before)
        for path, before in zip(paths, paths_before):
            np.testing.assert_array_equal(path, before)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            write_csv(output / "shadow_groups.csv", groups)
            write_csv(output / "shadow_communities.csv", community_rows)
            with (output / "shadow_groups.csv").open(
                    "r", newline="", encoding="utf-8-sig") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), len(merged))
            with (output / "shadow_communities.csv").open(
                    "r", newline="", encoding="utf-8-sig") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))),
                                 len(communities))

    def test_shadow_threshold_sweep_is_offline_and_does_not_filter(self):
        group_rows = [
            {"source_candidate_ids": "10;11",
             "max_candidate_path_length": "160"},
            {"source_candidate_ids": "12",
             "max_candidate_path_length": "180"},
        ]
        community_rows = [
            {"dense_final_instance_id": "1",
             "max_candidate_path_length": "160"},
            {"dense_final_instance_id": "2",
             "max_candidate_path_length": "180"},
        ]
        manual = [
            {"final_instance_id": "1", "manual_class": "TRUE_TAIL"},
            {"final_instance_id": "2", "manual_class": "FALSE_FRAGMENT"},
        ]
        candidates = [
            {"candidate_id": "10", "final_instance_id": "1"},
            {"candidate_id": "11", "final_instance_id": "1"},
            {"candidate_id": "12", "final_instance_id": "2"},
        ]
        originals = ([dict(row) for row in group_rows],
                     [dict(row) for row in community_rows],
                     [dict(row) for row in manual],
                     [dict(row) for row in candidates])

        result = sweep(group_rows, community_rows, manual, candidates,
                       thresholds=(170, 200))

        self.assertEqual(len(result), 4)
        self.assertEqual(result[0]["true_tail_hit_count"], 1)
        self.assertEqual(result[0]["false_fragment_hit_count"], 0)
        self.assertEqual(result[1]["false_fragment_hit_count"], 1)
        self.assertEqual((group_rows, community_rows, manual, candidates),
                         originals)

    def test_boundary_review_range_mapping_manual_and_identity_split(self):
        communities = [
            {"dense_final_instance_id": "1", "identity_community_id": "11",
             "max_candidate_path_length": "129.9", "region_parent_id": "7",
             "parent_group_id": "7", "source_candidate_count": "1"},
            {"dense_final_instance_id": "2", "identity_community_id": "12",
             "max_candidate_path_length": "150", "min_candidate_path_length": "40",
             "total_candidate_path_length": "190", "mean_candidate_path_length": "95",
             "region_parent_id": "8", "parent_group_id": "8",
             "source_candidate_count": "2", "source_candidate_ids": "20;21",
             "community_member_node_count": "2"},
            {"dense_final_instance_id": "3", "identity_community_id": "13",
             "max_candidate_path_length": "220", "region_parent_id": "8;9",
             "parent_group_id": "8;9", "source_candidate_count": "1"},
            {"dense_final_instance_id": "4", "identity_community_id": "14",
             "max_candidate_path_length": "220.1", "region_parent_id": "10",
             "parent_group_id": "10", "source_candidate_count": "1"},
        ]
        finals = [{"final_instance_id": str(value)} for value in range(1, 5)]
        manual = [{"final_instance_id": "2", "manual_class": "TRUE_TAIL",
                   "manual_note": "keep"}]

        rows = boundary_review_rows(communities, finals, manual,
                                    "CASE1", "RUN1", "field")

        self.assertEqual([row["dense_final_instance_id"] for row in rows],
                         ["2", "3"])
        self.assertEqual(rows[0]["review_priority"], "HIGH")
        self.assertEqual(rows[1]["review_priority"], "MEDIUM")
        self.assertEqual(rows[0]["manual_class"], "TRUE_TAIL")
        self.assertEqual(rows[1]["manual_class"], "")
        self.assertTrue(rows[0]["is_multi_candidate"])
        self.assertTrue(rows[1]["is_multi_parent"])
        self.assertEqual(rows[0]["parent_final_instance_count"], 2)
        self.assertTrue(rows[0]["is_identity_split"])
        self.assertEqual(rows[0]["identity_community_id"], "12")

    def test_boundary_overlay_and_package_do_not_modify_labels(self):
        labels = np.zeros((40, 50), dtype=np.uint16)
        labels[5:15, 5:15] = 1
        labels[20:35, 30:45] = 2
        before = labels.copy()
        rows = [{"dense_final_instance_id": "2", "review_priority": "HIGH"}]
        overlay = boundary_review_overlay(labels, rows)
        self.assertEqual(overlay.shape, (40, 50, 3))
        np.testing.assert_array_equal(labels, before)

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            write_csv(directory / "shadow_groups.csv", [{
                "source_candidate_ids": "1", "max_candidate_path_length": 175
            }])
            write_csv(directory / "shadow_communities.csv", [{
                "dense_final_instance_id": 2, "identity_community_id": 2,
                "max_candidate_path_length": 175,
                "min_candidate_path_length": 175,
                "total_candidate_path_length": 175,
                "mean_candidate_path_length": 175,
                "source_candidate_count": 1, "source_candidate_ids": "1",
                "parent_group_id": 1, "region_parent_id": 1,
                "community_member_node_count": 1,
            }])
            write_csv(directory / "final_instance_diagnostics.csv", [{
                "final_instance_id": 2, "manual_class": "", "manual_note": ""
            }])
            write_csv(directory / "candidate_diagnostics.csv", [{
                "candidate_id": 1, "final_instance_id": 2
            }])
            cv2_path = directory / "06_final_tail_instances.tif"
            import cv2
            cv2.imwrite(str(cv2_path), labels)
            label_bytes = cv2_path.read_bytes()

            package_rows, elapsed, _ = generate_boundary_review(directory)

            self.assertEqual(len(package_rows), 1)
            self.assertEqual(package_rows[0]["manual_class"], "")
            self.assertEqual(cv2_path.read_bytes(), label_bytes)
            self.assertGreaterEqual(elapsed, 0)
            self.assertTrue((directory / "shadow_boundary_review.csv").is_file())
            self.assertTrue((directory / "shadow_boundary_review_overlay.png").is_file())
            self.assertIn("ID 2", (directory / "shadow_boundary_review.txt").read_text(
                encoding="utf-8"))

    def test_diagnostics_are_read_only_and_report_validation_outcome(self):
        fitc = np.arange(36, dtype=np.float32).reshape(6, 6)
        paths = [
            np.asarray([[1, 1], [2, 1], [3, 1]], dtype=np.int32),
            np.asarray([[1, 3], [2, 3]], dtype=np.int32),
        ]
        rows = [
            {
                "candidate_id": 1,
                "length": 2.0,
                "intensity_score": 0.8,
                "width_score": 0.7,
                "curvature_score": 0.9,
                "final_score": 0.8,
            },
            {
                "candidate_id": 2,
                "length": 1.0,
                "intensity_score": 0.1,
                "width_score": 0.2,
                "curvature_score": 0.3,
                "final_score": 0.1,
            },
        ]
        fitc_before = fitc.copy()
        paths_before = [path.copy() for path in paths]

        result = candidate_diagnostic_rows(
            rows, paths, fitc, 0.15, [[0]], {0: 1}, {"P1:F1": 1}
        )

        self.assertEqual(result[0]["validation_reject_reason"], "accepted")
        self.assertEqual(result[0]["final_instance_id"], 1)
        self.assertEqual(result[1]["validation_reject_reason"], "score_low")
        self.assertEqual(result[1]["final_instance_id"], "")
        self.assertEqual(result[0]["skeleton_pixels"], 3)
        self.assertEqual(result[0]["tortuosity"], 1.0)
        np.testing.assert_array_equal(fitc, fitc_before)
        for path, before in zip(paths, paths_before):
            np.testing.assert_array_equal(path, before)

    def test_final_diagnostics_are_complete_and_read_only(self):
        labels = np.zeros((8, 9), dtype=np.uint16)
        labels[1:4, 1:6] = 1
        labels[5:8, 7:9] = 2
        fitc = np.arange(72, dtype=np.float32).reshape(8, 9)
        candidate_rows = [
            {
                "candidate_id": 10,
                "validation_passed": True,
                "merged_candidate_id": 1,
                "score": 0.2,
                "main_path_length": 4.0,
            },
            {
                "candidate_id": 11,
                "validation_passed": True,
                "merged_candidate_id": 1,
                "score": 0.4,
                "main_path_length": 6.0,
            },
            {
                "candidate_id": 12,
                "validation_passed": True,
                "merged_candidate_id": 2,
                "score": 0.3,
                "main_path_length": 3.0,
            },
        ]
        merged = [[0, 1], [2]]
        membership = {"P1:F1": 1, "P1:F2": 1, "P2:F1": 2}
        labels_before = labels.copy()
        fitc_before = fitc.copy()

        result, mapping = final_instance_diagnostic_rows(
            labels, fitc, candidate_rows, merged, membership
        )

        self.assertEqual(mapping, {1: 1, 2: 2})
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["source_candidate_count"], 2)
        self.assertEqual(result[0]["source_candidate_ids"], "10;11")
        self.assertEqual(result[0]["merged_candidate_count"], 1)
        self.assertEqual(result[0]["manual_class"], "")
        self.assertEqual(result[0]["manual_note"], "")
        self.assertTrue(result[1]["touches_image_border"])
        expected_fields = {
            "final_instance_id", "pixel_area", "bbox_width", "bbox_height",
            "bbox_aspect_ratio", "skeleton_pixels", "skeleton_length",
            "main_path_length", "euclidean_end_distance", "tortuosity",
            "mean_fitc", "integrated_fitc", "source_candidate_count",
            "source_candidate_ids", "merged_candidate_count",
            "identity_community_id", "source_candidate_min_score",
            "source_candidate_max_score", "source_candidate_mean_score",
            "source_candidate_min_path_length",
            "source_candidate_max_path_length",
            "source_candidate_total_path_length", "touches_image_border",
            "manual_class", "manual_note",
        }
        self.assertEqual(set(result[0]), expected_fields)
        np.testing.assert_array_equal(labels, labels_before)
        np.testing.assert_array_equal(fitc, fitc_before)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "final_instance_diagnostics.csv"
            write_csv(output, result)
            self.assertTrue(output.is_file())
            with output.open("r", newline="", encoding="utf-8-sig") as handle:
                written = list(csv.DictReader(handle))
            self.assertEqual(len(written), int(labels.max()))
            self.assertEqual(written[0]["manual_class"], "")
            self.assertEqual(written[0]["manual_note"], "")

    def test_overlay_does_not_modify_final_labels(self):
        labels = np.zeros((10, 10), dtype=np.uint16)
        labels[2:8, 3:7] = 1
        fitc = np.ones((10, 10), dtype=np.float32)
        before = labels.copy()
        overlay = final_instance_id_overlay(fitc, labels)
        self.assertEqual(overlay.shape, (10, 10, 3))
        np.testing.assert_array_equal(labels, before)

    def test_combiner_preserves_manual_labels_and_summarizes_classes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directories = [root / "field1", root / "field2"]
            for directory in directories:
                directory.mkdir()
            rows = [
                {"final_instance_id": 1, "pixel_area": 10,
                 "manual_class": "TRUE_TAIL", "manual_note": "ok"},
                {"final_instance_id": 2, "pixel_area": 2,
                 "manual_class": "FALSE_FRAGMENT", "manual_note": "short"},
            ]
            write_csv(directories[0] / "final_instance_diagnostics.csv", rows)
            write_csv(directories[1] / "final_instance_diagnostics.csv", [
                {"final_instance_id": 1, "pixel_area": 4,
                 "manual_class": "UNCERTAIN", "manual_note": ""}
            ])
            output = root / "combined_final_instance_diagnostics.csv"

            combined, summary = combine(directories, output)

            self.assertEqual(len(combined), 3)
            self.assertEqual(combined[0]["manual_note"], "ok")
            counts = {
                row["manual_class"]: row["count"] for row in summary
                if row["metric"] == "count"
            }
            self.assertEqual(counts, {
                "TRUE_TAIL": 1, "FALSE_FRAGMENT": 1, "UNCERTAIN": 1
            })
            with output.open("r", newline="", encoding="utf-8-sig") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 3)


if __name__ == "__main__":
    unittest.main()
