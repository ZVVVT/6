import copy
import json

import numpy as np
import pytest
import tifffile
from pathlib import Path

from core.analysis_v2.association_result import adapter_association_output, build_association_result
from core.analysis_v2.tail_core_result import build_tail_core_result
from core.analysis_v2.tail_objects_revision import (
    GEOMETRY_LABEL_NAME, REVISION_JSON_NAME, TailObjectsRevisionError,
    build_revision0, load_revision0, serialize_revision0, validate_revision0,
)


ASSOCIATION_FINGERPRINTS = {
    "head_final_labels": "1" * 64, "tail_core_result": "2" * 64,
    "parameter": "3" * 64, "producer": "4" * 64,
}


def fixture():
    labels = np.array([[0, 7, 7, 0], [11, 0, 22, 22]], dtype=np.uint16)
    core = {
        "schema_version": 1, "field_id": "field-A",
        "fingerprints": {"input": "a", "parameter": "b", "producer": "c"},
        "labels": {"filtered_07": {"positive_ids": [7, 11, 22]}},
    }
    decisions = [
        {"tail_id": 7, "association_status": "associated", "associated_head_id": 101,
         "source": "auto", "reason": "match"},
        {"tail_id": 11, "association_status": "unresolved", "associated_head_id": None,
         "source": "automatic", "reason": "no_match"},
        {"tail_id": 22, "association_status": "associated", "associated_head_id": 202,
         "source": "auto", "reason": "match"},
    ]
    association = build_association_result("field-A", [7, 11, 22], [101, 202],
                                           decisions, ASSOCIATION_FINGERPRINTS)
    return core, association, labels


def revision():
    core, association, labels = fixture()
    return core, association, labels, build_revision0(core, association, labels)


def test_build_contract_has_full_automatic_state_and_exact_geometry():
    core, association, labels, result = revision()
    assert result["revision_number"] == 0
    assert result["parent_revision_id"] is None
    assert result["origin"] == "automatic"
    assert result["summary"] == {"tail_count": 3, "associated_count": 2, "unresolved_count": 1}
    assert [row["source_fragment_id"] for row in result["objects"]] == [7, 11, 22]
    assert all(row["object_id"] != str(row["source_fragment_id"]) for row in result["objects"])
    assert result["objects"][1]["associated_head_id"] is None
    assert result["objects"][1]["geometry_label_id"] == 11
    assert "fluorescence" not in json.dumps(result)
    assert "manual_" not in json.dumps(result)
    validate_revision0(result, core, association, labels)


def test_roundtrip_deterministic_fingerprint_and_no_pickle(tmp_path):
    core, association, labels, result = revision()
    first = tmp_path / "one"; second = tmp_path / "two"
    serialize_revision0(result, first, core, association, labels)
    serialize_revision0(result, second, core, association, labels)
    loaded = load_revision0(first, core, association, "field-A")
    assert loaded == load_revision0(second, core, association, "field-A")
    assert (first / REVISION_JSON_NAME).read_bytes() == (second / REVISION_JSON_NAME).read_bytes()
    assert np.array_equal(tifffile.imread(str(first / GEOMETRY_LABEL_NAME)), labels)
    assert "pickle" not in (first / REVISION_JSON_NAME).read_text(encoding="utf-8").lower()
    assert not list(tmp_path.rglob("*.pkl"))


@pytest.mark.parametrize("mutate, message", [
    (lambda value: value.update(schema_version=99), "schema_version"),
    (lambda value: value.update(field_id="wrong"), "field_id"),
    (lambda value: value.update(revision_number=1), "revision_number"),
    (lambda value: value.update(parent_revision_id="revision-x"), "parent_revision_id"),
    (lambda value: value.update(origin="manual"), "origin"),
    (lambda value: value["objects"].append(copy.deepcopy(value["objects"][0])), "duplicate object_id"),
    (lambda value: value["objects"][1].update(source_fragment_id=7), "duplicate source_fragment_id"),
    (lambda value: value["objects"].pop(), "未完整覆盖"),
    (lambda value: value["objects"][0].update(source_fragment_id=99), "不存在于 TailCore"),
    (lambda value: value["objects"][0].update(associated_head_id=202), "mismatch"),
    (lambda value: value["objects"][1].update(associated_head_id=101), "unresolved"),
    (lambda value: value["objects"][0].update(associated_head_id=None), "associated tail"),
    (lambda value: value["objects"][0].update(association_status="unresolved"), "unresolved"),
    (lambda value: value["summary"].update(tail_count=99), "summary"),
    (lambda value: value["objects"][0].update(geometry_label_id=99), "geometry identity"),
    (lambda value: value["objects"][0].update(association_reason=float("nan")), "NaN"),
])
def test_validator_rejects_contract_breaks(mutate, message):
    core, association, labels, result = revision()
    broken = copy.deepcopy(result)
    mutate(broken)
    with pytest.raises(TailObjectsRevisionError, match=message):
        validate_revision0(broken, core, association, labels)


def test_validator_rejects_duplicate_automatic_head_assignment():
    core, association, labels, result = revision()
    # Keep association consistent, then create the prohibited one-head/two-tail upstream fact.
    changed_association = copy.deepcopy(association)
    changed_association["tails"][2]["associated_head_id"] = 101
    changed_association["summary"]["associated_count"] = 2
    changed = build_revision0(core, association, labels)
    changed["objects"][2]["associated_head_id"] = 101
    changed["upstream_identities"]["association_result"] = __import__(
        "core.analysis_v2.input_fingerprint", fromlist=["fingerprint"]).fingerprint(changed_association)
    changed["upstream_identities"]["head_via_association"] = changed_association["fingerprints"]["head_final_labels"]
    changed["revision_id"] = "bad"
    with pytest.raises(TailObjectsRevisionError, match="一个 head"):
        validate_revision0(changed, core, changed_association, labels)


def test_loader_rejects_geometry_orphan_and_missing_geometry(tmp_path):
    core, association, labels, result = revision()
    serialize_revision0(result, tmp_path, core, association, labels)
    geometry = tifffile.imread(str(tmp_path / GEOMETRY_LABEL_NAME))
    geometry[0, 0] = 99
    tifffile.imwrite(str(tmp_path / GEOMETRY_LABEL_NAME), geometry)
    with pytest.raises(TailObjectsRevisionError):
        load_revision0(tmp_path, core, association)
    (tmp_path / GEOMETRY_LABEL_NAME).unlink()
    with pytest.raises(TailObjectsRevisionError, match="不存在"):
        load_revision0(tmp_path, core, association)


@pytest.mark.parametrize("field_id, case_no, run_id, expected", [
    ("ZBFY023-C-1", "CASE20260908102941", "20260908_103450_acad3c", (77, 65, 12)),
    ("ZBFY020-C-1", "CASE20260908103925", "20260908_103952_a64637", (63, 54, 9)),
    ("ZBFY016-C-1", "CASE20260908104300", "20260908_104320_f4c8fc", (74, 68, 6)),
    ("ZBFY022-C-1", "CASE20260908104656", "20260908_104716_af7f2c", (104, 92, 12)),
])
def test_tier1_revision0_per_object_oracle(field_id, case_no, run_id, expected, tmp_path):
    root = Path(__file__).resolve().parents[1]
    run = root / "workspace" / "cases" / case_no / "analysis_v2" / "protein3" / "runs" / run_id
    core_dir = run / "segmentation" / "c18b_score015" / field_id / (field_id + "_FITC")
    tail_dir = run / "calibration" / "tail" / field_id
    head_path = run / "calibration" / "head" / (field_id + "_HeadFinalLabels.tif")
    oracle_path = tail_dir / (field_id + "_TailFinalObjects.json")
    if not all(path.is_file() for path in (core_dir / "07_extreme_fragment_filtered_labels.tif",
                                            tail_dir / "manifest.json", head_path, oracle_path)):
        pytest.skip("local Tier1 formal run unavailable")
    core = build_tail_core_result(core_dir, field_id, "a", "b", "c")
    labels = tifffile.imread(str(core_dir / "07_extreme_fragment_filtered_labels.tif"))
    tail_ids = core["labels"]["filtered_07"]["positive_ids"]
    head_ids = [int(value) for value in np.unique(tifffile.imread(str(head_path))) if value > 0]
    association = build_association_result(field_id, tail_ids, head_ids,
                                           adapter_association_output(tail_dir, tail_ids),
                                           ASSOCIATION_FINGERPRINTS)
    result = build_revision0(core, association, labels)
    serialize_revision0(result, tmp_path, core, association, labels)
    assert tuple(result["summary"][key] for key in
                 ("tail_count", "associated_count", "unresolved_count")) == expected
    assert np.array_equal(tifffile.imread(str(tmp_path / GEOMETRY_LABEL_NAME)), labels)
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    expected_by_fragment = {int(row["fragment_label_id"]): (
        row["association_status"], row["head_label_id"], int(row["pixel_count"]))
        for row in oracle["objects"]}
    actual_by_fragment = {row["source_fragment_id"]: (
        row["association_status"], row["associated_head_id"],
        int(np.count_nonzero(labels == row["geometry_label_id"])))
        for row in result["objects"]}
    assert actual_by_fragment == expected_by_fragment
