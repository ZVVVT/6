import copy
import hashlib
import math

import pytest

from core.analysis_v2.input_fingerprint import (
    InputManifestError, build_input_manifest, canonical_json_bytes,
    parameter_fingerprint, producer_fingerprint, stage_input_fingerprint, stage_input_projection,
    validate_input_manifest,
)


def digest(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def manifest():
    return build_input_manifest("field-1", "protein3", "tail", 2048, 1024, [
        {"role": "Merge", "relative_path": "input\\field_Merge.tif", "sha256": digest("merge"), "byte_size": 5, "shape": [1024, 2048], "dtype": "uint8"},
        {"role": "FITC", "relative_path": "input\\field_FITC.tif", "sha256": digest("fitc"), "byte_size": 4, "shape": [1024, 2048], "dtype": "uint8"},
        {"role": "TRITC", "relative_path": "input\\field_TRITC.tif", "sha256": digest("tritc"), "byte_size": 5, "shape": [1024, 2048], "dtype": "uint8"},
    ])


def test_same_manifest_rebuild_has_same_fingerprint():
    assert stage_input_fingerprint(manifest(), "tail_core") == stage_input_fingerprint(manifest(), "tail_core")


def test_absolute_diagnostic_directory_is_not_business_identity():
    left = manifest()
    right = manifest()
    left["inputs"][0]["absolute_path"] = "C:\\old\\input\\field_FITC.tif"
    right["inputs"][0]["absolute_path"] = "D:\\new\\input\\field_FITC.tif"
    assert stage_input_fingerprint(left, "tail_core") == stage_input_fingerprint(right, "tail_core")


def test_fitc_change_only_invalidates_tail_core():
    changed = manifest()
    changed["inputs"][0]["sha256"] = digest("changed fitc")
    assert stage_input_fingerprint(changed, "tail_core") != stage_input_fingerprint(manifest(), "tail_core")
    assert stage_input_fingerprint(changed, "head") == stage_input_fingerprint(manifest(), "head")


def test_tritc_change_only_invalidates_head():
    changed = manifest()
    changed["inputs"][1]["sha256"] = digest("changed tritc")
    assert stage_input_fingerprint(changed, "head") != stage_input_fingerprint(manifest(), "head")
    assert stage_input_fingerprint(changed, "tail_core") == stage_input_fingerprint(manifest(), "tail_core")


def test_merge_is_described_but_has_no_current_head_or_tail_core_dependency():
    changed = manifest()
    changed["inputs"][2]["sha256"] = digest("changed merge")
    for stage in ("head", "tail_core", "association"):
        assert stage_input_fingerprint(changed, stage) == stage_input_fingerprint(manifest(), stage)


def test_association_uses_only_explicit_upstream_artifact_fingerprints():
    base = manifest()
    left = stage_input_projection(base, "association", {"head_final_labels": "a", "tail_core_result": "b"})
    right = stage_input_projection(base, "association", {"tail_core_result": "b", "head_final_labels": "a"})
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert stage_input_fingerprint(base, "association", {"head_final_labels": "changed", "tail_core_result": "b"}) != stage_input_fingerprint(base, "association", {"head_final_labels": "a", "tail_core_result": "b"})


def test_field_identity_and_parameter_order_are_deterministic():
    changed = manifest()
    changed["field_id"] = "field-2"
    assert stage_input_fingerprint(changed, "tail_core") != stage_input_fingerprint(manifest(), "tail_core")
    assert parameter_fingerprint("tail_core", {"b": 2, "a": {"x": 1}}) == parameter_fingerprint("tail_core", {"a": {"x": 1}, "b": 2})


def test_parameter_change_and_json_key_order_change_hash_as_designed():
    assert parameter_fingerprint("tail_core", {"threshold": 0.15}) != parameter_fingerprint("tail_core", {"threshold": 0.16})
    assert canonical_json_bytes({"b": 2, "a": 1}) == canonical_json_bytes({"a": 1, "b": 2})


def test_producer_identity_is_stage_scoped_not_git_scoped():
    left = producer_fingerprint("tail_core", "tail-core-v3", resources={"runner": digest("source")})
    reordered = producer_fingerprint("tail_core", "tail-core-v3", resources={"runner": digest("source")})
    changed = producer_fingerprint("tail_core", "tail-core-v4", resources={"runner": digest("source")})
    assert left == reordered
    assert left != changed


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nan_and_inf_are_rejected(value):
    with pytest.raises(InputManifestError, match="NaN 和 Inf"):
        parameter_fingerprint("tail_core", {"value": value})


def test_windows_and_posix_relative_paths_are_identical():
    left = manifest()
    right = manifest()
    right["inputs"][0]["relative_path"] = "input/field_FITC.tif"
    assert stage_input_fingerprint(left, "tail_core") == stage_input_fingerprint(right, "tail_core")


def test_unsupported_schema_is_rejected():
    unsupported = copy.deepcopy(manifest())
    unsupported["schema_version"] = 99
    with pytest.raises(InputManifestError, match="不支持"):
        validate_input_manifest(unsupported)
