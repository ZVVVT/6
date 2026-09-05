import csv
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import tifffile

from core.analysis_v2.tail_calibration_service import build_tail_final_contract
from core.analysis_v2.tail_measurement_service import (
    _load_tail_objects_contract,
    collect_tail_measurement_fields,
    validate_tail_measurement_output,
)


def _write_workset(root: Path) -> Path:
    tifffile.imwrite(
        str(root / "TailWorksetLabels.tif"),
        np.asarray([[1, 1, 0], [3, 2, 2]], dtype=np.uint16),
    )
    payload = {
        "objects": [
            {
                "tail_object_id": 10,
                "workset_label_id": 1,
                "accepted": True,
                "association_status": "associated",
                "head_label_id": 7,
                "source": "fragment",
                "fragment_label_id": 5,
            },
            {
                "tail_object_id": 20,
                "workset_label_id": 2,
                "accepted": True,
                "association_status": "unresolved",
                "head_label_id": None,
                "source": "fragment",
                "fragment_label_id": 8,
            },
            {
                "tail_object_id": 30,
                "workset_label_id": 3,
                "accepted": False,
                "association_status": "unresolved",
                "head_label_id": None,
            },
        ]
    }
    (root / "tail_workset.json").write_text(json.dumps(payload), encoding="utf-8")
    heads = root / "heads.tif"
    tifffile.imwrite(
        str(heads), np.asarray([[0, 7, 7], [9, 0, 0]], dtype=np.uint16)
    )
    return heads


def test_workset_contract_decouples_tail_and_associated_counts(tmp_path):
    heads = _write_workset(tmp_path)
    result = build_tail_final_contract("field", tmp_path, heads)

    tail = tifffile.imread(result["tail_final_labels"])
    positive = tifffile.imread(result["tail_positive_head_labels"])
    payload = json.loads(Path(result["tail_final_objects"]).read_text(encoding="utf-8"))

    assert set(np.unique(tail)) == {0, 1, 2}
    assert set(np.unique(positive)) == {0, 1}
    assert tail[1, 0] == 0  # accepted=false 完全不进入正式标签
    assert payload["schema_version"] == 2
    assert payload["tail_object_count"] == 2
    assert payload["associated_object_count"] == 1
    assert payload["unresolved_object_count"] == 1
    assert payload["objects"][0]["head_label_id"] == 7
    assert payload["objects"][0]["fragment_label_id"] == 5
    assert payload["objects"][1]["tail_object_id"] == 2
    assert payload["objects"][1]["head_label_id"] is None
    assert payload["objects"][1]["association_status"] == "unresolved"


def test_legacy_all_associated_contract_is_read_without_migration(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "object_count": 2,
        "objects": [
            {"object_id": 1, "head_id": 7, "pixel_count": 4},
            {"object_id": 2, "head_id": 9, "pixel_count": 3},
        ],
    }), encoding="utf-8")

    contract = _load_tail_objects_contract(path)

    assert contract["tail_object_count"] == 2
    assert contract["associated_object_count"] == 2
    assert contract["unresolved_object_count"] == 0
    assert contract["associated_ids"] == [1, 2]


def test_new_schema_reads_89_tail_68_associated_21_unresolved(tmp_path):
    objects = []
    for tail_id in range(1, 90):
        associated = tail_id <= 68
        objects.append({
            "tail_object_id": tail_id,
            "head_label_id": tail_id if associated else None,
            "association_status": "associated" if associated else "unresolved",
            "pixel_count": 1,
            "source": "test",
            "fragment_label_id": tail_id + 1,
        })
    path = tmp_path / "new.json"
    path.write_text(json.dumps({
        "schema_version": 2,
        "tail_object_count": 89,
        "associated_object_count": 68,
        "unresolved_object_count": 21,
        "objects": objects,
    }), encoding="utf-8")

    contract = _load_tail_objects_contract(path)

    assert contract["tail_object_count"] == 89
    assert contract["associated_object_count"] == 68
    assert contract["unresolved_object_count"] == 21
    assert contract["unresolved_ids"] == list(range(69, 90))


def test_measurement_field_collection_accepts_positive_label_subset(
    tmp_path, monkeypatch
):
    input_dir = tmp_path / "input"
    head_dir = tmp_path / "head"
    tail_dir = tmp_path / "tail"
    for directory in (input_dir, head_dir, tail_dir):
        directory.mkdir()
    field = "field"
    image = np.zeros((2, 3), dtype=np.uint8)
    tifffile.imwrite(str(input_dir / "field_FITC.tif"), image)
    tifffile.imwrite(str(input_dir / "field_TRITC.tif"), image)
    tifffile.imwrite(
        str(head_dir / "field_HeadFinalLabels.tif"),
        np.asarray([[7, 7, 0], [9, 0, 0]], dtype=np.uint16),
    )
    tifffile.imwrite(
        str(tail_dir / "field_TailFinalLabels.tif"),
        np.asarray([[1, 1, 0], [0, 2, 2]], dtype=np.uint16),
    )
    tifffile.imwrite(
        str(tail_dir / "field_TailPositiveHeadLabels.tif"),
        np.asarray([[1, 1, 0], [0, 0, 0]], dtype=np.uint16),
    )
    (tail_dir / "field_TailFinalObjects.json").write_text(json.dumps({
        "schema_version": 2,
        "tail_object_count": 2,
        "associated_object_count": 1,
        "unresolved_object_count": 1,
        "objects": [
            {"tail_object_id": 1, "head_label_id": 7,
             "association_status": "associated"},
            {"tail_object_id": 2, "head_label_id": None,
             "association_status": "unresolved", "fragment_label_id": 5},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(
        "core.analysis_v2.tail_measurement_service.task_paths_from_root",
        lambda root: SimpleNamespace(
            input_dir=input_dir,
            calibration_head_dir=head_dir,
            calibration_tail_dir=tail_dir,
        ),
    )

    fields = collect_tail_measurement_fields(tmp_path)

    assert fields[0]["tail_object_count"] == 2
    assert fields[0]["associated_object_count"] == 1
    assert fields[0]["unresolved_object_count"] == 1


def _write_csv(path, fieldnames, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_measurement_output_accepts_g_count_different_from_colocalized(
    tmp_path, monkeypatch
):
    _write_csv(tmp_path / "Image.csv", [
        "ImageNumber", "Count_G_objects", "Count_R_objects",
        "Count_R_colocalized", "Math_ColocalizationRate",
    ], [{
        "ImageNumber": 1, "Count_G_objects": 2, "Count_R_objects": 4,
        "Count_R_colocalized": 1, "Math_ColocalizationRate": 0.25,
    }])
    _write_csv(tmp_path / "G_objects.csv", [
        "ImageNumber", "ObjectNumber", "AreaShape_Area",
        "Math_MeanIntensity255",
    ], [
        {"ImageNumber": 1, "ObjectNumber": 1, "AreaShape_Area": 4,
         "Math_MeanIntensity255": 10},
        {"ImageNumber": 1, "ObjectNumber": 2, "AreaShape_Area": 3,
         "Math_MeanIntensity255": 30},
    ])
    for name in (
        "field_G_G_objects_OrigOverlay.png",
        "field_R_R_objects_OrigOverlay.png",
        "field_G_G_colocalized_OrigOverlay.png",
    ):
        (tmp_path / name).write_bytes(b"png")

    class ParserStub:
        def __init__(self, *args, **kwargs):
            pass

        def parse_image_summary(self, protein_part):
            return {
                "success": True,
                "calculation_mode": "head_equivalent",
                "warnings": [
                    "视野 1 的 Count_G_objects=2 与 "
                    "Count_R_colocalized=1 不一致。"
                ],
                "total": {
                    "sperm_count": 4,
                    "positive_count": 1,
                    "mean_intensity_raw": 10,
                    "rate_fraction": 0.25,
                },
            }

    monkeypatch.setattr(
        "core.analysis_v2.tail_measurement_service.ResultParser", ParserStub
    )
    result = validate_tail_measurement_output(tmp_path, [{
        "field_id": "field",
        "expected_object_count": 2,
        "tail_object_count": 2,
        "associated_object_count": 1,
    }])

    assert result["tail_object_count"] == 2
    assert result["associated_object_count"] == 1
    assert result["fields"][0]["positive_count"] == 1
