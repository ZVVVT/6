from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from core.analysis_v2.tail_result_publisher import (
    TailResultPublishError,
    stage_tail_measurement_output,
    validate_tail_result_directory,
)


def _write_csv(path: Path, fieldnames, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _create_valid_tail_output(
    root: Path,
    field_count: int = 2,
    tail_count: int = 2,
    associated_count: int = 2,
) -> None:
    image_rows = []
    object_rows = []
    object_number = 1

    for image_number in range(1, field_count + 1):
        sperm_count = max(100, associated_count + 1) + image_number
        positive_count = associated_count
        image_rows.append({
            "ImageNumber": image_number,
            "Count_G_objects": tail_count,
            "Count_R_objects": sperm_count,
            "Count_R_colocalized": positive_count,
            "Math_ColocalizationRate": positive_count / sperm_count,
        })
        for object_index in range(tail_count):
            mean_value = 50.0 + object_index
            object_rows.append({
                "ImageNumber": image_number,
                "ObjectNumber": object_number,
                "AreaShape_Area": 100,
                "Math_MeanIntensity255": mean_value,
            })
            object_number += 1

        stem = "field{}".format(image_number)
        for name in (
            "{}_G_G_objects_OrigOverlay.png".format(stem),
            "{}_R_R_objects_OrigOverlay.png".format(stem),
            "{}_G_G_colocalized_OrigOverlay.png".format(stem),
        ):
            (root / name).write_bytes(b"overlay-" + name.encode("ascii"))

    _write_csv(
        root / "Image.csv",
        [
            "ImageNumber",
            "Count_G_objects",
            "Count_R_objects",
            "Count_R_colocalized",
            "Math_ColocalizationRate",
        ],
        image_rows,
    )
    _write_csv(
        root / "G_objects.csv",
        [
            "ImageNumber",
            "ObjectNumber",
            "AreaShape_Area",
            "Math_MeanIntensity255",
        ],
        object_rows,
    )


def _measurement_contract(
    tail_count: int,
    associated_count: int,
    unresolved_count=None,
):
    field = {
        "field_id": "field1",
        "image_number": 1,
        "tail_object_count": tail_count,
        "associated_object_count": associated_count,
    }
    if unresolved_count is not None:
        field["unresolved_object_count"] = unresolved_count
    return {"fields": [field]}


class TailResultPublisherTests(unittest.TestCase):
    def _validate_counts(self, tail_count, associated_count, unresolved_count=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _create_valid_tail_output(
                root,
                field_count=1,
                tail_count=tail_count,
                associated_count=associated_count,
            )
            return validate_tail_result_directory(
                root,
                expected_field_count=1,
                measurement_contract=_measurement_contract(
                    tail_count, associated_count, unresolved_count
                ),
            )

    def test_all_unresolved_counts_are_valid(self):
        result = self._validate_counts(12, 0, 12)
        self.assertTrue(result.get("success"))
        self.assertEqual(result["total"]["positive_count"], 0)
        self.assertEqual(result["rows"][0]["g_objects_count"], 12)
        self.assertEqual(result.get("warnings"), [])

    def test_no_tail_objects_is_rejected(self):
        with self.assertRaises(TailResultPublishError):
            self._validate_counts(0, 0, 0)

    def test_77_tail_65_associated_12_unresolved_is_valid(self):
        self.assertTrue(self._validate_counts(77, 65, 12).get("success"))

    def test_all_unresolved_output_can_be_staged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidate"
            target = root / "cp_output" / "protein3"
            source.mkdir()
            _create_valid_tail_output(
                source, field_count=1, tail_count=12, associated_count=0
            )
            publication = stage_tail_measurement_output(
                source,
                target,
                expected_field_count=1,
                measurement_contract=_measurement_contract(12, 0, 12),
            )
            self.assertTrue(publication.summary.get("success"))
            self.assertEqual(publication.summary["total"]["positive_count"], 0)
            self.assertEqual(
                (target / "Image.csv").read_bytes(),
                (source / "Image.csv").read_bytes(),
            )

    def test_77_tail_64_associated_13_unresolved_is_valid(self):
        self.assertTrue(self._validate_counts(77, 64, 13).get("success"))

    def test_89_tail_68_associated_21_unresolved_is_valid(self):
        self.assertTrue(self._validate_counts(89, 68, 21).get("success"))

    def test_all_associated_counts_are_valid(self):
        self.assertTrue(self._validate_counts(68, 68, 0).get("success"))

    def test_g_count_must_match_tail_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _create_valid_tail_output(root, field_count=1, tail_count=76,
                                      associated_count=64)
            with self.assertRaisesRegex(TailResultPublishError, "tail_object_count"):
                validate_tail_result_directory(
                    root,
                    measurement_contract=_measurement_contract(77, 64, 13),
                )

    def test_colocalized_count_must_match_associated_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _create_valid_tail_output(root, field_count=1, tail_count=77,
                                      associated_count=63)
            with self.assertRaisesRegex(TailResultPublishError,
                                        "associated_object_count"):
                validate_tail_result_directory(
                    root,
                    measurement_contract=_measurement_contract(77, 64, 13),
                )

    def test_associated_count_cannot_exceed_tail_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _create_valid_tail_output(root, field_count=1, tail_count=77,
                                      associated_count=64)
            with self.assertRaisesRegex(TailResultPublishError, "大于"):
                validate_tail_result_directory(
                    root,
                    measurement_contract=_measurement_contract(77, 78),
                )

    def test_unresolved_count_must_match_count_difference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _create_valid_tail_output(root, field_count=1, tail_count=77,
                                      associated_count=64)
            with self.assertRaisesRegex(TailResultPublishError,
                                        "unresolved_object_count"):
                validate_tail_result_directory(
                    root,
                    measurement_contract=_measurement_contract(77, 64, 12),
                )

    def test_legacy_payload_without_contract_remains_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _create_valid_tail_output(root, field_count=1, tail_count=68,
                                      associated_count=68)
            self.assertTrue(validate_tail_result_directory(root).get("success"))

    def test_g_and_colocalized_difference_alone_is_not_an_error(self):
        result = self._validate_counts(77, 64)
        self.assertEqual(result.get("warnings"), [])

    def test_validate_head_equivalent_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _create_valid_tail_output(root, field_count=2)
            result = validate_tail_result_directory(
                root,
                expected_field_count=2,
            )
            self.assertTrue(result.get("success"))
            self.assertEqual(
                result.get("calculation_mode"),
                "head_equivalent",
            )
            self.assertEqual(
                (result.get("total") or {}).get("positive_count"),
                4,
            )

    def test_stage_and_rollback_restores_old_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidate"
            target = root / "cp_output" / "protein3"
            source.mkdir(parents=True)
            target.mkdir(parents=True)
            (target / "old_result.txt").write_text(
                "old",
                encoding="utf-8",
            )
            _create_valid_tail_output(source, field_count=2)

            publication = stage_tail_measurement_output(
                source,
                target,
                expected_field_count=2,
            )
            self.assertTrue((target / "Image.csv").is_file())
            self.assertFalse((target / "old_result.txt").exists())

            publication.rollback()
            self.assertTrue((target / "old_result.txt").is_file())
            self.assertFalse((target / "Image.csv").exists())

    def test_stage_and_commit_removes_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidate"
            target = root / "cp_output" / "protein3"
            source.mkdir(parents=True)
            target.mkdir(parents=True)
            (target / "old_result.txt").write_text(
                "old",
                encoding="utf-8",
            )
            _create_valid_tail_output(source, field_count=2)

            publication = stage_tail_measurement_output(
                source,
                target,
                expected_field_count=2,
            )
            backup = publication.backup_dir
            self.assertTrue(backup.exists())
            warning = publication.commit()
            self.assertEqual(warning, "")
            self.assertFalse(backup.exists())
            self.assertTrue((target / "Image.csv").is_file())

    def test_missing_overlay_is_rejected_before_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidate"
            target = root / "cp_output" / "protein3"
            source.mkdir(parents=True)
            target.mkdir(parents=True)
            (target / "old_result.txt").write_text(
                "old",
                encoding="utf-8",
            )
            _create_valid_tail_output(source, field_count=2)
            next(source.glob("*_G_G_colocalized_OrigOverlay.png")).unlink()

            with self.assertRaises(TailResultPublishError):
                stage_tail_measurement_output(
                    source,
                    target,
                    expected_field_count=2,
                )

            self.assertTrue((target / "old_result.txt").is_file())
            self.assertFalse((target / "Image.csv").exists())


if __name__ == "__main__":
    unittest.main()
