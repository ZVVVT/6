"""Tail V3 Phase 3A head-independent TailCoreResult v1 contract.

This module only snapshots already-produced C18B artifacts after the extreme
fragment filter.  It never runs C18B, changes labels, or performs head/tail
association.
"""

import csv
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import tifffile


SCHEMA_VERSION = 1
RESULT_JSON_NAME = "tail_core_result.json"
BASELINE_LABEL_NAME = "06_final_tail_instances.tif"
FILTERED_LABEL_NAME = "07_extreme_fragment_filtered_labels.tif"
SUPPORTED_SCHEMA_VERSIONS = frozenset((SCHEMA_VERSION,))


class TailCoreResultError(ValueError):
    """TailCoreResult is incomplete or violates its frozen business contract."""


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_path(value):
    text = str(value).strip().replace("\\", "/")
    if not text or text.startswith("/") or ":" in text.split("/", 1)[0]:
        raise TailCoreResultError("标签路径必须是非空相对路径：{}".format(value))
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise TailCoreResultError("标签路径不能包含空段、. 或 ..：{}".format(value))
    return "/".join(parts)


def _read_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _integer(value, field):
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError):
        raise TailCoreResultError("{} 必须是整数：{}".format(field, value))
    if result <= 0:
        raise TailCoreResultError("{} 必须为正整数：{}".format(field, value))
    return result


def _id_list(labels):
    return sorted(int(value) for value in np.unique(labels) if int(value) > 0)


def _label_record(path, relative_path):
    labels = tifffile.imread(str(path))
    if labels.ndim != 2:
        raise TailCoreResultError("标签必须是二维 TIFF：{}".format(path))
    if not np.issubdtype(labels.dtype, np.integer):
        raise TailCoreResultError("标签 dtype 必须为整数：{}".format(path))
    if np.any(labels < 0):
        raise TailCoreResultError("标签不能包含负 ID：{}".format(path))
    return {
        "relative_path": _relative_path(relative_path),
        "sha256": _sha256(path),
        "shape": [int(value) for value in labels.shape],
        "dtype": str(labels.dtype),
        "positive_ids": _id_list(labels),
    }, labels


def _split_ids(value, field):
    text = str(value or "").strip()
    if not text:
        return []
    values = [_integer(item, field) for item in text.split(";")]
    if len(values) != len(set(values)):
        raise TailCoreResultError("{} 包含重复 ID".format(field))
    return values


def _finite_text(value, field):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        raise TailCoreResultError("{} 必须为数值：{}".format(field, value))
    if not math.isfinite(number):
        raise TailCoreResultError("{} 不能为 NaN 或 Inf".format(field))
    return text


def _fingerprints(input_fingerprint, parameter_fingerprint, producer_fingerprint):
    values = {
        "input": input_fingerprint,
        "parameter": parameter_fingerprint,
        "producer": producer_fingerprint,
    }
    for key, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise TailCoreResultError("{} fingerprint 必须是非空字符串".format(key))
    return values


def build_tail_core_result(c18b_dir, field_id, input_fingerprint,
                           parameter_fingerprint, producer_fingerprint):
    """Extract v1 only from the completed, fragment-filtered C18B directory."""
    directory = Path(c18b_dir).resolve()
    if not isinstance(field_id, str) or not field_id.strip():
        raise TailCoreResultError("field_id 必须是非空字符串")
    required = {
        "baseline": directory / BASELINE_LABEL_NAME,
        "filtered": directory / FILTERED_LABEL_NAME,
        "communities": directory / "shadow_communities.csv",
        "finals": directory / "final_instance_diagnostics.csv",
        "candidates": directory / "candidate_diagnostics.csv",
        "audit": directory / "extreme_fragment_filter.csv",
    }
    missing = [str(path) for path in required.values() if not path.is_file()]
    if missing:
        raise TailCoreResultError("C18B fragment filter 产物不完整：{}".format("; ".join(missing)))
    baseline_record, baseline = _label_record(required["baseline"], BASELINE_LABEL_NAME)
    filtered_record, filtered = _label_record(required["filtered"], FILTERED_LABEL_NAME)
    if baseline.shape != filtered.shape:
        raise TailCoreResultError("06 与 07 标签 shape 不一致")
    if baseline.dtype != filtered.dtype:
        raise TailCoreResultError("06 与 07 标签 dtype 不一致")

    communities = []
    for row in _read_rows(required["communities"]):
        communities.append({
            "final_instance_id": _integer(row.get("dense_final_instance_id"), "dense_final_instance_id"),
            "identity_community_id": _integer(row.get("identity_community_id"), "identity_community_id"),
            "parent_group_ids": _split_ids(row.get("parent_group_id"), "parent_group_id"),
            "source_candidate_ids": _split_ids(row.get("source_candidate_ids"), "source_candidate_ids"),
        })
    objects = []
    for row in _read_rows(required["finals"]):
        objects.append({
            "final_instance_id": _integer(row.get("final_instance_id"), "final_instance_id"),
            "identity_community_id": _integer(row.get("identity_community_id"), "identity_community_id"),
            "source_candidate_ids": _split_ids(row.get("source_candidate_ids"), "source_candidate_ids"),
            "source_candidate_count": _integer(row.get("source_candidate_count"), "source_candidate_count"),
            "merged_candidate_count": _integer(row.get("merged_candidate_count"), "merged_candidate_count"),
        })
    memberships = []
    for row in _read_rows(required["candidates"]):
        if str(row.get("validation_passed", "")).strip().lower() != "true":
            continue
        memberships.append({
            "candidate_id": _integer(row.get("candidate_id"), "candidate_id"),
            "final_instance_id": _integer(row.get("final_instance_id"), "candidate final_instance_id"),
            "parent_group_id": _integer(row.get("merged_candidate_id"), "merged_candidate_id"),
        })
    audit = []
    for row in _read_rows(required["audit"]):
        removed = str(row.get("removed", "")).strip().lower() == "true"
        audit.append({
            "final_instance_id": _integer(row.get("final_instance_id"), "audit final_instance_id"),
            "identity_community_id": _integer(row.get("identity_community_id"), "audit identity_community_id"),
            "max_candidate_path_length": _finite_text(row.get("max_candidate_path_length"), "max_candidate_path_length"),
            "removed": removed,
            "reason": str(row.get("reason") or "").strip(),
        })
    result = {
        "schema_version": SCHEMA_VERSION,
        "field_id": field_id.strip(),
        "fingerprints": _fingerprints(input_fingerprint, parameter_fingerprint, producer_fingerprint),
        "labels": {"baseline_06": baseline_record, "filtered_07": filtered_record},
        "communities": sorted(communities, key=lambda row: row["final_instance_id"]),
        "objects": sorted(objects, key=lambda row: row["final_instance_id"]),
        "candidate_memberships": sorted(memberships, key=lambda row: row["candidate_id"]),
        "fragment_filter_audit": sorted(audit, key=lambda row: row["final_instance_id"]),
    }
    validate_tail_core_result(result, arrays={"baseline_06": baseline, "filtered_07": filtered})
    return result


def _require_json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise TailCoreResultError("JSON 不能包含 NaN 或 Inf")
    if isinstance(value, dict):
        for item in value.values():
            _require_json_safe(item)
    elif isinstance(value, list):
        for item in value:
            _require_json_safe(item)


def validate_tail_core_result(result, root_dir=None, expected_field_id=None,
                              expected_fingerprints=None, arrays=None):
    """Strictly validate schema, labels, mappings and filter audit."""
    if not isinstance(result, dict):
        raise TailCoreResultError("TailCoreResult 顶层必须是对象")
    _require_json_safe(result)
    if result.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        raise TailCoreResultError("不支持的 TailCoreResult schema_version：{}".format(result.get("schema_version")))
    field_id = result.get("field_id")
    if not isinstance(field_id, str) or not field_id.strip():
        raise TailCoreResultError("field_id 必须是非空字符串")
    if expected_field_id is not None and field_id != expected_field_id:
        raise TailCoreResultError("field_id 不匹配")
    fingerprints = result.get("fingerprints")
    if not isinstance(fingerprints, dict):
        raise TailCoreResultError("fingerprints 必须是对象")
    _fingerprints(fingerprints.get("input"), fingerprints.get("parameter"), fingerprints.get("producer"))
    for key, value in dict(expected_fingerprints or {}).items():
        if fingerprints.get(key) != value:
            raise TailCoreResultError("{} fingerprint 不匹配".format(key))
    labels = result.get("labels")
    if not isinstance(labels, dict):
        raise TailCoreResultError("labels 必须是对象")
    loaded = dict(arrays or {})
    for key in ("baseline_06", "filtered_07"):
        record = labels.get(key)
        if not isinstance(record, dict):
            raise TailCoreResultError("缺少标签记录：{}".format(key))
        relative = _relative_path(record.get("relative_path"))
        if key not in loaded:
            if root_dir is None:
                raise TailCoreResultError("验证 TIFF 需要 root_dir")
            path = Path(root_dir) / relative
            if not path.is_file():
                raise TailCoreResultError("标签 TIFF 不存在：{}".format(path))
            if _sha256(path) != record.get("sha256"):
                raise TailCoreResultError("标签 TIFF SHA256 不匹配：{}".format(key))
            loaded[key] = tifffile.imread(str(path))
        image = loaded[key]
        if image.ndim != 2 or list(image.shape) != record.get("shape") or str(image.dtype) != record.get("dtype"):
            raise TailCoreResultError("标签 TIFF shape 或 dtype 不匹配：{}".format(key))
        if not np.issubdtype(image.dtype, np.integer) or np.any(image < 0):
            raise TailCoreResultError("标签 TIFF 必须为非负整数：{}".format(key))
        if _id_list(image) != record.get("positive_ids"):
            raise TailCoreResultError("标签 ID 不匹配：{}".format(key))
    baseline_ids = set(labels["baseline_06"]["positive_ids"])
    surviving_ids = set(labels["filtered_07"]["positive_ids"])
    if not surviving_ids.issubset(baseline_ids):
        raise TailCoreResultError("07 包含不存在于 06 的 ID")
    if loaded["baseline_06"].shape != loaded["filtered_07"].shape or loaded["baseline_06"].dtype != loaded["filtered_07"].dtype:
        raise TailCoreResultError("06/07 shape 或 dtype 不一致")
    if any(not np.array_equal(loaded["baseline_06"] == item, loaded["filtered_07"] == item) for item in surviving_ids):
        raise TailCoreResultError("surviving ID 像素必须与 06 完全一致")
    objects = result.get("objects")
    communities = result.get("communities")
    memberships = result.get("candidate_memberships")
    audit = result.get("fragment_filter_audit")
    if not all(isinstance(value, list) for value in (objects, communities, memberships, audit)):
        raise TailCoreResultError("业务 records 必须是数组")
    object_ids = [row.get("final_instance_id") for row in objects]
    community_ids = [row.get("final_instance_id") for row in communities]
    audit_ids = [row.get("final_instance_id") for row in audit]
    removed_ids = set(row["final_instance_id"] for row in audit if row.get("removed") is True)
    audit_surviving = set(row["final_instance_id"] for row in audit if row.get("removed") is False)
    if removed_ids & audit_surviving:
        raise TailCoreResultError("removed/surviving audit 不完整或重叠")
    if (len(object_ids) != len(set(object_ids)) or
            len(community_ids) != len(set(community_ids)) or
            len(audit_ids) != len(set(audit_ids))):
        raise TailCoreResultError("duplicate identity")
    if set(object_ids) != baseline_ids or set(community_ids) != baseline_ids or set(audit_ids) != baseline_ids:
        raise TailCoreResultError("mapping 或 audit 未覆盖全部 06 ID")
    object_by_id = dict((row["final_instance_id"], row) for row in objects)
    community_by_id = dict((row["final_instance_id"], row) for row in communities)
    for final_id in baseline_ids:
        if object_by_id[final_id].get("identity_community_id") != community_by_id[final_id].get("identity_community_id"):
            raise TailCoreResultError("object/community identity mapping 不一致")
    membership_ids = [row.get("candidate_id") for row in memberships]
    if len(membership_ids) != len(set(membership_ids)):
        raise TailCoreResultError("duplicate candidate identity")
    if any(row.get("final_instance_id") not in baseline_ids for row in memberships):
        raise TailCoreResultError("membership 引用了不存在的 final ID")
    if removed_ids | audit_surviving != baseline_ids:
        raise TailCoreResultError("removed/surviving audit 不完整或重叠")
    if audit_surviving != surviving_ids or removed_ids != baseline_ids - surviving_ids:
        raise TailCoreResultError("fragment filter audit 与 07 不一致")
    return True


def serialize_tail_core_result(result, output_dir, arrays=None):
    """Write JSON + TIFF only, with normalized relative paths and no pickle."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    arrays = dict(arrays or {})
    for key in ("baseline_06", "filtered_07"):
        if key not in arrays:
            raise TailCoreResultError("serialize 需要 {} 数组".format(key))
    stored = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
    for key in ("baseline_06", "filtered_07"):
        relative = "labels/{}".format(BASELINE_LABEL_NAME if key == "baseline_06" else FILTERED_LABEL_NAME)
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(str(path), arrays[key])
        record, _ = _label_record(path, relative)
        stored["labels"][key] = record
    validate_tail_core_result(stored, root_dir=output)
    json_path = output / RESULT_JSON_NAME
    temporary = Path(str(json_path) + ".tmp")
    temporary.write_text(json.dumps(stored, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(json_path))
    return json_path


def load_tail_core_result(output_dir, expected_field_id=None, expected_fingerprints=None):
    """Load and strictly validate a serialized TailCoreResult v1."""
    root = Path(output_dir)
    path = root / RESULT_JSON_NAME
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise TailCoreResultError("无法读取 TailCoreResult JSON：{}".format(error))
    validate_tail_core_result(result, root, expected_field_id, expected_fingerprints)
    return result
