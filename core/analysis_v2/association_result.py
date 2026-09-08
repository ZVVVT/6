"""Tail V3 Phase 4A automatic AssociationResult v1 contract.

This module is deliberately detached from the active runner.  It records the
already-produced adapter association decision before any editor workset or
TailFinal object is created.  It neither changes pixels nor imports the editor
or finalizer.
"""

import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import tifffile

from .environment_snapshot import sha256_file
from .input_fingerprint import canonical_json_bytes, fingerprint


SCHEMA_VERSION = 1
RESULT_JSON_NAME = "association_result.json"
SUPPORTED_SCHEMA_VERSIONS = frozenset((SCHEMA_VERSION,))
ASSOCIATION_STATUSES = frozenset(("associated", "unresolved"))


class AssociationResultError(ValueError):
    """AssociationResult violates the frozen automatic-association contract."""


def _positive_ids(labels):
    return sorted(int(value) for value in np.unique(labels) if int(value) > 0)


def _require_fingerprint(value, name):
    if not isinstance(value, str) or len(value) != 64:
        raise AssociationResultError("{} fingerprint 必须是 SHA256 字符串".format(name))
    if any(char not in "0123456789abcdef" for char in value.lower()):
        raise AssociationResultError("{} fingerprint 必须是 SHA256 字符串".format(name))
    return value.lower()


def head_artifact_identity(head_final_labels_path):
    """Return the stable business identity of the calibrated Head artifact."""
    path = Path(head_final_labels_path)
    labels = tifffile.imread(str(path))
    if labels.ndim != 2 or not np.issubdtype(labels.dtype, np.integer):
        raise AssociationResultError("HeadFinalLabels 必须是二维整数标签图")
    if np.any(labels < 0):
        raise AssociationResultError("HeadFinalLabels 不能有负 ID")
    identity = {
        "artifact": "HeadFinalLabels",
        "sha256": sha256_file(path),
        "shape": [int(value) for value in labels.shape],
        "dtype": str(labels.dtype),
        "positive_ids": _positive_ids(labels),
    }
    return {"identity": identity, "fingerprint": fingerprint(identity)}


def tail_core_artifact_fingerprint(checkpoint_manifest_path):
    """Use the immutable TailCore checkpoint manifest SHA as artifact identity."""
    return sha256_file(Path(checkpoint_manifest_path))


def association_parameter_fingerprint(dilation_radius_px, maximum_distance_px):
    """Fingerprint only the two current adapter parameters that affect matches."""
    return fingerprint({
        "stage": "association",
        "contract": "c18b_tail_editor_adapter_v1",
        "dilation_radius_px": int(dilation_radius_px),
        "maximum_distance_px": float(maximum_distance_px),
    })


def association_producer_fingerprint(adapter_source_path, builder_source_path=None):
    """Bind the adapter association implementation and this builder only."""
    adapter = Path(adapter_source_path)
    builder = Path(builder_source_path or __file__)
    return fingerprint({
        "stage": "association",
        "algorithm_version": "association-result-v1",
        "resources": {
            "c18b_tail_editor_adapter.py": sha256_file(adapter),
            "association_result.py": sha256_file(builder),
        },
    })


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AssociationResultError("无法读取 Association adapter JSON {}: {}".format(path, error))
    if not isinstance(value, dict):
        raise AssociationResultError("Association adapter JSON 顶层必须是对象：{}".format(path))
    return value


def adapter_association_output(adapter_dir, tail_ids):
    """Extract automatic decisions from adapter output, never TailFinalObjects.

    ``global_results`` is the actual selected association output.  Unselected
    filtered IDs remain automatic unresolved tails.  The adapter's explicit
    unmatched/skipped reasons are preserved when available.
    """
    directory = Path(adapter_dir)
    globals_rows = list(_read_json(directory / "global_results.json").get("results") or [])
    candidates = list(_read_json(directory / "unassigned_tail_candidates.json").get("candidates") or [])
    manifest = _read_json(directory / "manifest.json")
    selected = {}
    for row in globals_rows:
        if row.get("status") != "auto_confirmed_unique":
            continue
        head_id = int(row.get("head_id") or 0)
        candidate = row.get("selected_candidate") or {}
        for raw_tail_id in candidate.get("selected_fragment_ids") or []:
            tail_id = int(raw_tail_id)
            if tail_id in selected:
                raise AssociationResultError("adapter 将一个 tail 关联到多个 head：{}".format(tail_id))
            selected[tail_id] = {
                "associated_head_id": head_id,
                "source": str(candidate.get("source") or "c18b_instance_centerline"),
                "reason": str(candidate.get("matching_method") or "automatic_match"),
            }
    unresolved = {}
    for row in candidates:
        unresolved[int(row.get("fragment_label_id") or 0)] = str(
            row.get("association_failure_reason") or "unresolved"
        )
    for row in (manifest.get("matching") or {}).get("skipped_matches") or []:
        unresolved[int(row.get("c18b_instance_id") or 0)] = str(
            row.get("reason") or "association_output_not_usable"
        )
    known_tail_ids = set(int(value) for value in tail_ids)
    unknown_selected = set(selected) - known_tail_ids
    unknown_unresolved = set(unresolved) - known_tail_ids
    if unknown_selected or unknown_unresolved:
        raise AssociationResultError(
            "adapter 引用了不存在于 TailCore 07 的 tail：{}".format(
                sorted(unknown_selected | unknown_unresolved)))
    result = []
    for tail_id in sorted(known_tail_ids):
        if tail_id in selected:
            item = selected[tail_id]
            result.append({"tail_id": tail_id, "association_status": "associated",
                           "associated_head_id": item["associated_head_id"],
                           "source": item["source"], "reason": item["reason"]})
        else:
            result.append({"tail_id": tail_id, "association_status": "unresolved",
                           "associated_head_id": None, "source": "automatic",
                           "reason": unresolved.get(tail_id, "not_selected_by_assignment")})
    return result


def build_association_result(field_id, tail_ids, head_ids, decisions, fingerprints):
    """Build v1 from automatic Association output and immutable upstream IDs."""
    tails = [dict(row) for row in decisions]
    result = {
        "schema_version": SCHEMA_VERSION,
        "field_id": str(field_id),
        "fingerprints": dict(fingerprints),
        "tails": sorted(tails, key=lambda row: int(row.get("tail_id") or 0)),
        "summary": {},
    }
    result["summary"] = {
        "tail_count": len(tails),
        "associated_count": sum(row.get("association_status") == "associated" for row in tails),
        "unresolved_count": sum(row.get("association_status") == "unresolved" for row in tails),
    }
    validate_association_result(result, tail_ids=tail_ids, head_ids=head_ids)
    return result


def _json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise AssociationResultError("JSON 不能包含 NaN 或 Inf")
    if isinstance(value, dict):
        for item in value.values():
            _json_safe(item)
    elif isinstance(value, list):
        for item in value:
            _json_safe(item)


def validate_association_result(result, tail_ids, head_ids, expected_field_id=None,
                                expected_fingerprints=None):
    _json_safe(result)
    if not isinstance(result, dict) or result.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        raise AssociationResultError("不支持的 AssociationResult schema_version")
    if not isinstance(result.get("field_id"), str) or not result["field_id"].strip():
        raise AssociationResultError("field_id 必须是非空字符串")
    if expected_field_id is not None and result["field_id"] != expected_field_id:
        raise AssociationResultError("field_id 不匹配")
    fingerprints = result.get("fingerprints")
    if not isinstance(fingerprints, dict):
        raise AssociationResultError("fingerprints 必须是对象")
    for key in ("head_final_labels", "tail_core_result", "parameter", "producer"):
        _require_fingerprint(fingerprints.get(key), key)
    for key, expected in dict(expected_fingerprints or {}).items():
        if fingerprints.get(key) != expected:
            raise AssociationResultError("{} fingerprint 不匹配".format(key))
    expected_tails = set(int(value) for value in tail_ids)
    known_heads = set(int(value) for value in head_ids)
    rows = result.get("tails")
    if not isinstance(rows, list):
        raise AssociationResultError("tails 必须是数组")
    seen_tails, used_heads = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise AssociationResultError("tail record 必须是对象")
        tail_id = row.get("tail_id")
        if not isinstance(tail_id, int) or isinstance(tail_id, bool) or tail_id <= 0:
            raise AssociationResultError("tail_id 必须是正整数")
        if tail_id in seen_tails:
            raise AssociationResultError("duplicate tail_id")
        seen_tails.add(tail_id)
        status = row.get("association_status")
        if status not in ASSOCIATION_STATUSES:
            raise AssociationResultError("invalid association_status")
        head_id = row.get("associated_head_id")
        if status == "associated":
            if not isinstance(head_id, int) or isinstance(head_id, bool) or head_id not in known_heads:
                raise AssociationResultError("associated_head_id 不存在于 HeadFinalLabels")
            if head_id in used_heads:
                raise AssociationResultError("一个 head 只能关联一个自动 tail")
            used_heads.add(head_id)
        elif head_id is not None:
            raise AssociationResultError("unresolved tail 不允许 associated_head_id")
        for key in ("source", "reason"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise AssociationResultError("{} 必须是非空字符串".format(key))
    if seen_tails != expected_tails:
        raise AssociationResultError("tail coverage 与 TailCore 07 不一致")
    summary = result.get("summary")
    if not isinstance(summary, dict):
        raise AssociationResultError("summary 必须是对象")
    expected_summary = {"tail_count": len(rows), "associated_count": len(used_heads),
                        "unresolved_count": len(rows) - len(used_heads)}
    if summary != expected_summary:
        raise AssociationResultError("summary 不一致")
    return True


def serialize_association_result(result, output_dir, tail_ids, head_ids):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stored = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
    validate_association_result(stored, tail_ids, head_ids)
    path = output / RESULT_JSON_NAME
    temporary = Path(str(path) + ".tmp")
    temporary.write_bytes(canonical_json_bytes(stored) + b"\n")
    os.replace(str(temporary), str(path))
    return path


def load_association_result(output_dir, tail_ids, head_ids, expected_field_id=None,
                            expected_fingerprints=None):
    path = Path(output_dir) / RESULT_JSON_NAME
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AssociationResultError("无法读取 AssociationResult JSON：{}".format(error))
    validate_association_result(result, tail_ids, head_ids, expected_field_id,
                                expected_fingerprints)
    return result
