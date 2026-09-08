import copy
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import tifffile

from core.analysis_v2.tail_core_result import (
    TailCoreResultError,
    build_tail_core_result,
    load_tail_core_result,
    serialize_tail_core_result,
    validate_tail_core_result,
)
FINGERPRINTS = dict((key, hashlib.sha256(key.encode("ascii")).hexdigest())
                    for key in ("input", "parameter", "producer"))


def _csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fixture_dir(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    baseline = np.array([[0, 1, 1], [2, 2, 0]], dtype=np.uint16)
    filtered = np.array([[0, 1, 1], [0, 0, 0]], dtype=np.uint16)
    tifffile.imwrite(str(tmp_path / "06_final_tail_instances.tif"), baseline)
    tifffile.imwrite(str(tmp_path / "07_extreme_fragment_filtered_labels.tif"), filtered)
    _csv(tmp_path / "shadow_communities.csv", [
        "dense_final_instance_id", "identity_community_id", "parent_group_id", "source_candidate_ids"], [
        {"dense_final_instance_id": 1, "identity_community_id": 11, "parent_group_id": 3, "source_candidate_ids": 7},
        {"dense_final_instance_id": 2, "identity_community_id": 12, "parent_group_id": 4, "source_candidate_ids": 8},
    ])
    _csv(tmp_path / "final_instance_diagnostics.csv", [
        "final_instance_id", "identity_community_id", "source_candidate_ids", "source_candidate_count", "merged_candidate_count"], [
        {"final_instance_id": 1, "identity_community_id": 11, "source_candidate_ids": 7, "source_candidate_count": 1, "merged_candidate_count": 1},
        {"final_instance_id": 2, "identity_community_id": 12, "source_candidate_ids": 8, "source_candidate_count": 1, "merged_candidate_count": 1},
    ])
    _csv(tmp_path / "candidate_diagnostics.csv", [
        "candidate_id", "validation_passed", "final_instance_id", "merged_candidate_id"], [
        {"candidate_id": 7, "validation_passed": "True", "final_instance_id": 1, "merged_candidate_id": 3},
        {"candidate_id": 8, "validation_passed": "True", "final_instance_id": 2, "merged_candidate_id": 4},
    ])
    _csv(tmp_path / "extreme_fragment_filter.csv", [
        "final_instance_id", "identity_community_id", "max_candidate_path_length", "removed", "reason"], [
        {"final_instance_id": 1, "identity_community_id": 11, "max_candidate_path_length": 120, "removed": "false", "reason": ""},
        {"final_instance_id": 2, "identity_community_id": 12, "max_candidate_path_length": 20, "removed": "true", "reason": "community_max_path_lt_80"},
    ])
    return tmp_path


def build(directory):
    return build_tail_core_result(directory, "field-1", FINGERPRINTS["input"],
                                  FINGERPRINTS["parameter"], FINGERPRINTS["producer"])


def arrays(directory):
    return {
        "baseline_06": tifffile.imread(str(directory / "06_final_tail_instances.tif")),
        "filtered_07": tifffile.imread(str(directory / "07_extreme_fragment_filtered_labels.tif")),
    }


def test_normal_build_extracts_only_head_independent_records(tmp_path):
    result = build(fixture_dir(tmp_path))
    assert result["labels"]["baseline_06"]["positive_ids"] == [1, 2]
    assert result["labels"]["filtered_07"]["positive_ids"] == [1]
    assert result["objects"][0]["identity_community_id"] == 11
    assert "head_id" not in json.dumps(result)
    assert "association" not in json.dumps(result)


def test_serialize_reload_is_strictly_equal(tmp_path):
    source = fixture_dir(tmp_path / "source")
    result = build(source)
    target = tmp_path / "saved"
    serialize_tail_core_result(result, target, arrays(source))
    assert load_tail_core_result(target, "field-1", FINGERPRINTS) == json.loads(
        (target / "tail_core_result.json").read_text(encoding="utf-8"))


def test_schema_rejected(tmp_path):
    result = build(fixture_dir(tmp_path))
    result["schema_version"] = 99
    with pytest.raises(TailCoreResultError, match="不支持"):
        validate_tail_core_result(result, arrays=arrays(tmp_path))


def test_field_and_all_fingerprint_mismatches_rejected(tmp_path):
    result = build(fixture_dir(tmp_path))
    with pytest.raises(TailCoreResultError, match="field_id"):
        validate_tail_core_result(result, expected_field_id="other", arrays=arrays(tmp_path))
    for key in FINGERPRINTS:
        with pytest.raises(TailCoreResultError, match=key):
            validate_tail_core_result(result, expected_fingerprints={key: "changed"}, arrays=arrays(tmp_path))


def test_shape_and_dtype_mismatch_rejected(tmp_path):
    result = build(fixture_dir(tmp_path))
    broken = arrays(tmp_path)
    broken["filtered_07"] = broken["filtered_07"].astype(np.uint8)
    with pytest.raises(TailCoreResultError, match="shape 或 dtype"):
        validate_tail_core_result(result, arrays=broken)
    broken = arrays(tmp_path)
    broken["filtered_07"] = broken["filtered_07"][:, :2]
    with pytest.raises(TailCoreResultError, match="shape 或 dtype"):
        validate_tail_core_result(result, arrays=broken)


def test_mapping_missing_duplicate_and_unknown_reference_rejected(tmp_path):
    result = build(fixture_dir(tmp_path))
    cases = []
    missing = copy.deepcopy(result); missing["objects"].pop(); cases.append((missing, "覆盖"))
    duplicate = copy.deepcopy(result); duplicate["objects"].append(copy.deepcopy(duplicate["objects"][0])); cases.append((duplicate, "duplicate"))
    unknown = copy.deepcopy(result); unknown["candidate_memberships"][0]["final_instance_id"] = 99; cases.append((unknown, "不存在"))
    for broken, message in cases:
        with pytest.raises(TailCoreResultError, match=message):
            validate_tail_core_result(broken, arrays=arrays(tmp_path))


def test_removed_surviving_overlap_rejected(tmp_path):
    result = build(fixture_dir(tmp_path))
    row = copy.deepcopy(result["fragment_filter_audit"][1])
    row["removed"] = False
    result["fragment_filter_audit"].append(row)
    with pytest.raises(TailCoreResultError, match="不完整或重叠"):
        validate_tail_core_result(result, arrays=arrays(tmp_path))


def test_unknown_surviving_id_rejected(tmp_path):
    result = build(fixture_dir(tmp_path))
    result["labels"]["filtered_07"]["positive_ids"] = [1, 99]
    broken = arrays(tmp_path)
    broken["filtered_07"][1, 0] = 99
    with pytest.raises(TailCoreResultError, match="07 包含"):
        validate_tail_core_result(result, arrays=broken)


def test_loader_rejects_missing_tiff(tmp_path):
    source = fixture_dir(tmp_path / "source")
    target = tmp_path / "saved"
    serialize_tail_core_result(build(source), target, arrays(source))
    (target / "labels" / "07_extreme_fragment_filtered_labels.tif").unlink()
    with pytest.raises(TailCoreResultError, match="不存在"):
        load_tail_core_result(target)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_json_nan_inf_rejected(tmp_path, value):
    result = build(fixture_dir(tmp_path))
    result["diagnostic_metadata"] = {"invalid": value}
    with pytest.raises(TailCoreResultError, match="NaN 或 Inf"):
        validate_tail_core_result(result, arrays=arrays(tmp_path))


def test_windows_path_normalisation_and_no_pickle(tmp_path):
    source = fixture_dir(tmp_path / "source")
    result = build(source)
    target = tmp_path / "saved"
    serialize_tail_core_result(result, target, arrays(source))
    payload = (target / "tail_core_result.json").read_text(encoding="utf-8")
    assert "labels/06_final_tail_instances.tif" in payload
    assert "pickle" not in payload.lower()
    assert not list(target.rglob("*.pkl"))


@pytest.mark.parametrize("field_id,case_no,run_id,expected", [
    ("ZBFY023-C-1", "CASE20260908102941", "20260908_103450_acad3c", (94, 77, 17)),
    ("ZBFY020-C-1", "CASE20260908103925", "20260908_103952_a64637", (83, 63, 20)),
    ("ZBFY016-C-1", "CASE20260908104300", "20260908_104320_f4c8fc", (107, 74, 33)),
    ("ZBFY022-C-1", "CASE20260908104656", "20260908_104716_af7f2c", (118, 104, 14)),
])
def test_tier1_fragment_filter_count_contract(field_id, case_no, run_id, expected):
    root = Path(__file__).resolve().parents[1]
    directory = root / "workspace" / "cases" / case_no / "analysis_v2" / "protein3" / "runs" / run_id / "segmentation" / "c18b_score015" / field_id / (field_id + "_FITC")
    if not directory.is_dir():
        pytest.skip("local Tier1 formal run unavailable")
    result = build_tail_core_result(directory, field_id, FINGERPRINTS["input"], FINGERPRINTS["parameter"], FINGERPRINTS["producer"])
    baseline = len(result["labels"]["baseline_06"]["positive_ids"])
    surviving = len(result["labels"]["filtered_07"]["positive_ids"])
    assert (baseline, surviving, baseline - surviving) == expected
