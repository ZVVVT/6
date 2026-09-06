import csv
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FILTER_PATH = (
    PROJECT_ROOT / "tools" / "analysis_v2" / "c18b_score015"
    / "extreme_fragment_filter.py"
)
SPEC = importlib.util.spec_from_file_location("c18b_extreme_filter", FILTER_PATH)
FILTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FILTER)


def write_csv(path, rows):
    fields = sorted(set().union(*(row.keys() for row in rows)))
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class C18BExtremeFragmentFilterTests(unittest.TestCase):
    def _fixture(self, directory):
        labels = np.zeros((12, 14), dtype=np.uint16)
        labels[1:3, 1:4] = 1
        labels[4:6, 2:6] = 2
        labels[7:10, 8:12] = 3
        labels[1:4, 10:13] = 4
        baseline_path = directory / FILTER.BASELINE_NAME
        cv2.imwrite(str(baseline_path), labels)
        write_csv(directory / "shadow_communities.csv", [
            {"dense_final_instance_id": 1, "identity_community_id": 11,
             "max_candidate_path_length": 79.999},
            {"dense_final_instance_id": 2, "identity_community_id": 12,
             "max_candidate_path_length": 80},
            {"dense_final_instance_id": 3, "identity_community_id": 13,
             "max_candidate_path_length": 165.267},
            {"dense_final_instance_id": 4, "identity_community_id": 14,
             "max_candidate_path_length": ""},
        ])
        write_csv(directory / "final_instance_diagnostics.csv", [
            {"final_instance_id": value,
             "identity_community_id": value + 10}
            for value in range(1, 5)
        ])
        return labels, baseline_path.read_bytes()

    def test_strict_threshold_missing_data_and_mask_invariants(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            baseline, baseline_bytes = self._fixture(directory)
            result = FILTER.apply_extreme_fragment_filter(directory)
            filtered = cv2.imread(
                str(directory / FILTER.FILTERED_NAME), cv2.IMREAD_UNCHANGED
            )
            self.assertEqual(result["removed_ids"], [1])
            self.assertFalse(np.any(filtered == 1))
            for final_id in (2, 3, 4):
                np.testing.assert_array_equal(
                    filtered == final_id, baseline == final_id
                )
            np.testing.assert_array_equal(baseline != filtered, baseline == 1)
            self.assertEqual(baseline_bytes,
                             (directory / FILTER.BASELINE_NAME).read_bytes())
            self.assertTrue((directory / FILTER.AUDIT_NAME).is_file())

    def test_unreliable_mapping_is_kept(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            baseline, _ = self._fixture(directory)
            write_csv(directory / "final_instance_diagnostics.csv", [
                {"final_instance_id": 1, "identity_community_id": 999}
            ])
            result = FILTER.apply_extreme_fragment_filter(directory)
            filtered = cv2.imread(
                str(directory / FILTER.FILTERED_NAME), cv2.IMREAD_UNCHANGED
            )
            self.assertEqual(result["removed_ids"], [])
            np.testing.assert_array_equal(filtered, baseline)

    def test_audit_has_one_row_per_final_instance(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self._fixture(directory)
            FILTER.apply_extreme_fragment_filter(directory)
            with (directory / FILTER.AUDIT_NAME).open(
                    "r", newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 4)
            self.assertEqual(set(rows[0]), set(FILTER.AUDIT_FIELDS))
            self.assertEqual(rows[0]["removed"], "true")
            self.assertEqual(rows[0]["reason"], FILTER.REMOVE_REASON)
            self.assertEqual(rows[1]["removed"], "false")

    def test_three_field_known_true_and_id45_regression(self):
        validation_root = PROJECT_ROOT / "workspace" / "c18b_shadow_validation"
        cases_root = PROJECT_ROOT / "workspace" / "cases"
        fixtures = {
            "ZBFY023-C-1": (
                94, 77,
                cases_root / "CASE20260901153519" / "analysis_v2"
                / "protein3" / "runs" / "20260901_170418_d385bb"
                / "segmentation" / "c18b_score015" / "ZBFY023-C-1"
                / "ZBFY023-C-1_FITC"
                / "final_instance_diagnostics_manual.csv",
            ),
            "JLJK61-C-1": (
                124, 66,
                cases_root / "CASE20260902092805" / "analysis_v2"
                / "protein3" / "runs" / "20260902_093824_3c44b7"
                / "segmentation" / "c18b_score015" / "JLJK61-C-1"
                / "JLJK61-C-1_FITC"
                / "final_instance_diagnostics_manual.csv",
            ),
            "ZBFY025-C-2": (
                93, 68,
                cases_root / "CASE20260902101630" / "analysis_v2"
                / "protein3" / "runs" / "20260902_101657_0f01d2"
                / "segmentation" / "c18b_score015" / "ZBFY025-C-2"
                / "ZBFY025-C-2_FITC"
                / "final_instance_diagnostics_manual.csv",
            ),
        }
        required = []
        for field_id, values in fixtures.items():
            source = validation_root / field_id
            required.extend([
                source / FILTER.BASELINE_NAME,
                source / "shadow_communities.csv",
                source / "final_instance_diagnostics.csv",
                values[2],
            ])
        if not all(path.is_file() for path in required):
            self.skipTest("三视野人工回归数据未安装")

        total_true = 0
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            for field_id, (baseline_count, filtered_count,
                           manual_path) in fixtures.items():
                source = validation_root / field_id
                target = temporary_root / field_id
                target.mkdir()
                for name in (
                    FILTER.BASELINE_NAME,
                    "shadow_communities.csv",
                    "final_instance_diagnostics.csv",
                ):
                    shutil.copy2(str(source / name), str(target / name))
                result = FILTER.apply_extreme_fragment_filter(target)
                self.assertEqual(result["baseline_final_count"], baseline_count)
                self.assertEqual(result["filtered_final_count"], filtered_count)
                with manual_path.open(
                        "r", newline="", encoding="utf-8-sig") as handle:
                    manual = list(csv.DictReader(handle))
                true_ids = {
                    int(row["final_instance_id"])
                    for row in manual
                    if row["manual_class"] == "TRUE_TAIL"
                }
                total_true += len(true_ids)
                self.assertTrue(true_ids.isdisjoint(result["removed_ids"]))
                if field_id == "ZBFY025-C-2":
                    self.assertNotIn(45, result["removed_ids"])
            self.assertEqual(total_true, 54)


class C18BExtremeFragmentWiringTests(unittest.TestCase):
    def test_editor_adapter_receives_filtered_labels(self):
        source = (PROJECT_ROOT / "core" / "analysis_v2"
                  / "c18b_execution.py").read_text(encoding="utf-8")
        prepare = source[source.index("def _prepare_c18b_editor_payload"):
                         source.index("def _run_c18b_workflow")]
        filter_call = prepare.index("extreme_fragment_filter.py")
        adapter_call = prepare.index('"--instances", str(filtered_instances_path)')
        self.assertLess(filter_call, adapter_call)
        self.assertIn("07_extreme_fragment_filtered_labels.tif", source)
        self.assertIn('"c18b_baseline_instances"', prepare)
        self.assertIn('"c18b_filtered_instances"', prepare)

    def test_tail_final_labels_and_measurement_contract_are_unchanged(self):
        calibration = (
            PROJECT_ROOT / "core" / "analysis_v2"
            / "tail_calibration_service.py"
        ).read_text(encoding="utf-8")
        measurement = (
            PROJECT_ROOT / "core" / "analysis_v2"
            / "tail_measurement_service.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"{}_TailFinalLabels.tif".format(field_id)', calibration)
        self.assertIn('suffix = "_TailFinalLabels.tif"', measurement)
        self.assertNotIn("07_extreme_fragment_filtered_labels", measurement)


if __name__ == "__main__":
    unittest.main()
