import copy
import json
from pathlib import Path

import numpy as np
import pytest
import tifffile

from core.analysis_v2.association_result import (
    AssociationResultError, adapter_association_output,
    build_association_result, head_artifact_identity,
    load_association_result, serialize_association_result,
    validate_association_result,
)


FINGERPRINTS = {
    "head_final_labels": "1" * 64,
    "tail_core_result": "2" * 64,
    "parameter": "3" * 64,
    "producer": "4" * 64,
}


def fixture():
    tails = [11, 22, 33]
    heads = [101, 202]
    decisions = [
        {"tail_id": 11, "association_status": "associated", "associated_head_id": 101,
         "source": "c18b_instance_centerline", "reason": "dilated_overlap_20px"},
        {"tail_id": 22, "association_status": "unresolved", "associated_head_id": None,
         "source": "automatic", "reason": "no_head_within_maximum_distance"},
        {"tail_id": 33, "association_status": "associated", "associated_head_id": 202,
         "source": "c18b_instance_centerline", "reason": "boundary_endpoint_distance"},
    ]
    return tails, heads, build_association_result("field", tails, heads, decisions, FINGERPRINTS)


def test_normal_build_json_roundtrip_and_no_pickle(tmp_path):
    tails, heads, result = fixture()
    path = serialize_association_result(result, tmp_path, tails, heads)
    loaded = load_association_result(tmp_path, tails, heads, "field", FINGERPRINTS)
    assert loaded == result
    assert json.loads(path.read_text(encoding="utf-8")) == result
    assert "pickle" not in path.read_text(encoding="utf-8").lower()
    assert not list(tmp_path.rglob("*.pkl"))


@pytest.mark.parametrize("mutate, message", [
    (lambda value: value.update(schema_version=99), "schema_version"),
    (lambda value: value.update(field_id="other"), "field_id"),
    (lambda value: value["fingerprints"].update(head_final_labels="a" * 64), "head_final_labels"),
    (lambda value: value["fingerprints"].update(tail_core_result="b" * 64), "tail_core_result"),
    (lambda value: value["fingerprints"].update(parameter="c" * 64), "parameter"),
    (lambda value: value["fingerprints"].update(producer="d" * 64), "producer"),
])
def test_schema_field_and_fingerprint_mismatches_rejected(mutate, message):
    tails, heads, result = fixture()
    changed = copy.deepcopy(result)
    mutate(changed)
    with pytest.raises(AssociationResultError, match=message):
        validate_association_result(changed, tails, heads, "field", FINGERPRINTS)


@pytest.mark.parametrize("mutate, message", [
    (lambda value: value["tails"].append(copy.deepcopy(value["tails"][0])), "duplicate tail_id"),
    (lambda value: value["tails"][0].update(associated_head_id=999), "不存在"),
    (lambda value: value["tails"][1].update(associated_head_id=101), "unresolved"),
    (lambda value: value["tails"].pop(), "coverage"),
    (lambda value: value["tails"][0].update(association_status="ambiguous"), "invalid"),
    (lambda value: value["tails"][0].update(reason=float("nan")), "NaN"),
])
def test_invalid_business_contract_rejected(mutate, message):
    tails, heads, result = fixture()
    changed = copy.deepcopy(result)
    mutate(changed)
    with pytest.raises(AssociationResultError, match=message):
        validate_association_result(changed, tails, heads)


def test_head_identity_binds_calibrated_artifact_not_raw_input(tmp_path):
    labels = np.array([[0, 9], [12, 0]], dtype=np.uint16)
    path = tmp_path / "field_HeadFinalLabels.tif"
    tifffile.imwrite(str(path), labels)
    original = head_artifact_identity(path)
    tifffile.imwrite(str(path), np.array([[0, 9], [13, 0]], dtype=np.uint16))
    changed = head_artifact_identity(path)
    assert original["fingerprint"] != changed["fingerprint"]
    assert original["identity"]["positive_ids"] == [9, 12]


TIER1 = (
    ("ZBFY023-C-1", "CASE20260908102941", "20260908_103450_acad3c", (77, 65, 12)),
    ("ZBFY020-C-1", "CASE20260908103925", "20260908_103952_a64637", (63, 54, 9)),
    ("ZBFY016-C-1", "CASE20260908104300", "20260908_104320_f4c8fc", (74, 68, 6)),
    ("ZBFY022-C-1", "CASE20260908104656", "20260908_104716_af7f2c", (104, 92, 12)),
)


@pytest.mark.parametrize("field_id, case_no, run_id, expected", TIER1)
def test_tier1_adapter_association_matches_final_object_oracle(
        field_id, case_no, run_id, expected):
    root = Path(__file__).resolve().parents[1]
    run = root / "workspace" / "cases" / case_no / "analysis_v2" / "protein3" / "runs" / run_id
    adapter_dir = run / "calibration" / "tail" / field_id
    core_path = run / "segmentation" / "c18b_score015" / field_id / (field_id + "_FITC") / "07_extreme_fragment_filtered_labels.tif"
    head_path = run / "calibration" / "head" / (field_id + "_HeadFinalLabels.tif")
    oracle_path = adapter_dir / (field_id + "_TailFinalObjects.json")
    if not all(path.is_file() for path in (core_path, head_path, oracle_path,
                                            adapter_dir / "manifest.json")):
        pytest.skip("local Tier1 formal run unavailable")
    tail_ids = sorted(int(value) for value in np.unique(tifffile.imread(str(core_path))) if value > 0)
    head_ids = sorted(int(value) for value in np.unique(tifffile.imread(str(head_path))) if value > 0)
    result = build_association_result(field_id, tail_ids, head_ids,
                                      adapter_association_output(adapter_dir, tail_ids), FINGERPRINTS)
    assert (result["summary"]["tail_count"], result["summary"]["associated_count"],
            result["summary"]["unresolved_count"]) == expected
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    expected_by_fragment = {
        int(row["fragment_label_id"]): (row["association_status"], row["head_label_id"])
        for row in oracle["objects"]
    }
    actual_by_tail = {
        row["tail_id"]: (row["association_status"], row["associated_head_id"])
        for row in result["tails"]
    }
    assert actual_by_tail == expected_by_fragment
