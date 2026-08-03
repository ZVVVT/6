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


def _create_valid_tail_output(root: Path, field_count: int = 2) -> None:
    image_rows = []
    object_rows = []
    object_number = 1

    for image_number in range(1, field_count + 1):
        sperm_count = 10 + image_number
        positive_count = 2
        image_rows.append({
            "ImageNumber": image_number,
            "Count_G_objects": positive_count,
            "Count_R_objects": sperm_count,
            "Count_R_colocalized": positive_count,
            "Math_ColocalizationRate": positive_count / sperm_count,
        })
        for mean_value in (50.0, 75.0):
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


class TailResultPublisherTests(unittest.TestCase):
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
