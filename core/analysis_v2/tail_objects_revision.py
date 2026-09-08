"""Tail V3 Phase 4C TailObjects Revision 0 contract.

This module defines the automatic, editable tail-object state between the
immutable TailCore/Association artifacts and any editor or finalizer.  It does
not import, invoke, or replace either of those consumers.
"""

import copy
import json
import math
import os
from pathlib import Path

import numpy as np
import tifffile

from .input_fingerprint import canonical_json_bytes, fingerprint


SCHEMA_VERSION = 1
REVISION_JSON_NAME = "tail_objects_revision.json"
GEOMETRY_LABEL_NAME = "labels/tail_objects_revision_labels.tif"
ASSOCIATION_STATUSES = frozenset(("associated", "unresolved"))


class TailObjectsRevisionError(ValueError):
    """TailObjects Revision data is not a valid Revision 0 contract."""


def _positive_ids(labels):
    return sorted(int(value) for value in np.unique(labels) if int(value) > 0)


def _sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_path(value):
    text = str(value).strip().replace("\\", "/")
    if not text or text.startswith("/") or ":" in text.split("/", 1)[0]:
        raise TailObjectsRevisionError("geometry 路径必须是非空相对路径")
    if any(part in ("", ".", "..") for part in text.split("/")):
        raise TailObjectsRevisionError("geometry 路径不能含有 . 或 ..")
    return text


def _json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise TailObjectsRevisionError("JSON 不能包含 NaN 或 Inf")
    if isinstance(value, dict):
        for child in value.values():
            _json_safe(child)
    elif isinstance(value, list):
        for child in value:
            _json_safe(child)


def _require_sha(value, field):
    if not isinstance(value, str) or len(value) != 64 or any(
            char not in "0123456789abcdef" for char in value.lower()):
        raise TailObjectsRevisionError("{} 必须是 SHA256 字符串".format(field))
    return value.lower()


def _core_ids(tail_core_result, core_labels):
    labels = np.asarray(core_labels)
    if labels.ndim != 2 or not np.issubdtype(labels.dtype, np.integer) or np.any(labels < 0):
        raise TailObjectsRevisionError("TailCore 07 必须是二维非负整数标签")
    expected = tail_core_result.get("labels", {}).get("filtered_07", {}).get("positive_ids")
    ids = _positive_ids(labels)
    if ids != expected:
        raise TailObjectsRevisionError("TailCore 07 geometry 与 TailCoreResult identity 不一致")
    return ids


def _object_id(field_id, fragment_id):
    # A namespaced revision identity, deliberately not a numeric image label.
    return "auto:{}:fragment:{}".format(field_id, fragment_id)


def _revision_identity(result):
    semantic = {
        "schema_version": result["schema_version"],
        "field_id": result["field_id"],
        "revision_number": result["revision_number"],
        "parent_revision_id": result["parent_revision_id"],
        "origin": result["origin"],
        "upstream_identities": result["upstream_identities"],
        "objects": result["objects"],
        "geometry": result["geometry"],
    }
    return fingerprint(semantic)


def build_revision0(tail_core_result, association_result, core_filtered_07):
    """Build Revision 0 solely from TailCoreResult + AssociationResult + 07.

    Revision geometry uses the original surviving C18B IDs as geometry labels;
    each business object has a separate stable, namespaced ``object_id``.
    """
    core = copy.deepcopy(tail_core_result)
    association = copy.deepcopy(association_result)
    field_id = core.get("field_id")
    if not isinstance(field_id, str) or not field_id.strip() or association.get("field_id") != field_id:
        raise TailObjectsRevisionError("TailCoreResult 与 AssociationResult field_id 不一致")
    core_ids = _core_ids(core, core_filtered_07)
    decisions = association.get("tails")
    if not isinstance(decisions, list):
        raise TailObjectsRevisionError("AssociationResult tails 必须是数组")
    by_tail = dict((row.get("tail_id"), row) for row in decisions)
    if set(by_tail) != set(core_ids) or len(by_tail) != len(decisions):
        raise TailObjectsRevisionError("AssociationResult 未严格覆盖 TailCore 07")
    objects = []
    for fragment_id in core_ids:
        row = by_tail[fragment_id]
        objects.append({
            "object_id": _object_id(field_id, fragment_id),
            "source_fragment_id": fragment_id,
            "geometry_label_id": fragment_id,
            "association_status": row.get("association_status"),
            "associated_head_id": row.get("associated_head_id"),
            "association_source": row.get("source"),
            "association_reason": row.get("reason"),
            "origin": "automatic",
        })
    core_identity = fingerprint(core)
    association_identity = fingerprint(association)
    result = {
        "schema_version": SCHEMA_VERSION,
        "field_id": field_id,
        "revision_number": 0,
        "parent_revision_id": None,
        "origin": "automatic",
        "upstream_identities": {
            "tail_core_result": core_identity,
            "association_result": association_identity,
            # AssociationResult already binds HeadFinalLabels; do not duplicate
            # its dynamic artifact location here.
            "head_via_association": association.get("fingerprints", {}).get("head_final_labels"),
        },
        "geometry": {
            "relative_path": GEOMETRY_LABEL_NAME,
            "source": "TailCoreResult.filtered_07_exact_copy",
            "shape": [int(value) for value in np.asarray(core_filtered_07).shape],
            "dtype": str(np.asarray(core_filtered_07).dtype),
            "positive_ids": list(core_ids),
        },
        "objects": objects,
        "summary": {
            "tail_count": len(objects),
            "associated_count": sum(item["association_status"] == "associated" for item in objects),
            "unresolved_count": sum(item["association_status"] == "unresolved" for item in objects),
        },
    }
    result["revision_id"] = "tail-objects-r0-{}".format(_revision_identity(result))
    validate_revision0(result, tail_core_result=core, association_result=association,
                       geometry_labels=core_filtered_07)
    return result


def validate_revision0(result, tail_core_result, association_result, geometry_labels=None,
                       root_dir=None, expected_field_id=None):
    """Strictly validate Revision 0, its exact geometry, and both upstreams."""
    _json_safe(result)
    if not isinstance(result, dict) or result.get("schema_version") != SCHEMA_VERSION:
        raise TailObjectsRevisionError("不支持的 TailObjects Revision schema_version")
    field_id = result.get("field_id")
    if not isinstance(field_id, str) or not field_id.strip():
        raise TailObjectsRevisionError("field_id 必须是非空字符串")
    if expected_field_id is not None and field_id != expected_field_id:
        raise TailObjectsRevisionError("field_id 不匹配")
    if tail_core_result.get("field_id") != field_id or association_result.get("field_id") != field_id:
        raise TailObjectsRevisionError("upstream field_id 不一致")
    if result.get("revision_number") != 0:
        raise TailObjectsRevisionError("Revision 0 的 revision_number 必须为 0")
    if result.get("parent_revision_id") is not None:
        raise TailObjectsRevisionError("Revision 0 的 parent_revision_id 必须为 null")
    if result.get("origin") != "automatic":
        raise TailObjectsRevisionError("Revision 0 的 origin 必须为 automatic")
    upstream = result.get("upstream_identities")
    if not isinstance(upstream, dict):
        raise TailObjectsRevisionError("upstream_identities 必须是对象")
    if upstream.get("tail_core_result") != fingerprint(tail_core_result):
        raise TailObjectsRevisionError("TailCore upstream identity 不匹配")
    if upstream.get("association_result") != fingerprint(association_result):
        raise TailObjectsRevisionError("Association upstream identity 不匹配")
    expected_head = association_result.get("fingerprints", {}).get("head_final_labels")
    if upstream.get("head_via_association") != expected_head:
        raise TailObjectsRevisionError("Head upstream identity 不匹配")
    core_ids = _core_ids(tail_core_result, geometry_labels) if geometry_labels is not None else list(
        tail_core_result.get("labels", {}).get("filtered_07", {}).get("positive_ids") or [])
    geometry = result.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("source") != "TailCoreResult.filtered_07_exact_copy":
        raise TailObjectsRevisionError("geometry contract 不正确")
    relative = _relative_path(geometry.get("relative_path"))
    labels = geometry_labels
    if labels is None:
        if root_dir is None:
            raise TailObjectsRevisionError("验证 geometry TIFF 需要 root_dir")
        path = Path(root_dir) / relative
        if not path.is_file():
            raise TailObjectsRevisionError("Revision geometry TIFF 不存在")
        labels = tifffile.imread(str(path))
        if geometry.get("sha256") != _sha256(path):
            raise TailObjectsRevisionError("Revision geometry TIFF SHA256 不匹配")
    labels = np.asarray(labels)
    if (labels.ndim != 2 or list(labels.shape) != geometry.get("shape") or
            str(labels.dtype) != geometry.get("dtype") or _positive_ids(labels) != geometry.get("positive_ids")):
        raise TailObjectsRevisionError("geometry TIFF shape、dtype 或 ID 不匹配")
    if geometry.get("positive_ids") != core_ids:
        raise TailObjectsRevisionError("Revision geometry ID 与 TailCore 07 不一致")
    if geometry_labels is not None and not np.array_equal(labels, np.asarray(geometry_labels)):
        raise TailObjectsRevisionError("Revision 0 geometry 必须与 TailCore 07 array_equal")
    rows = result.get("objects")
    if not isinstance(rows, list):
        raise TailObjectsRevisionError("objects 必须是数组")
    decision_by_id = dict((row.get("tail_id"), row) for row in association_result.get("tails") or [])
    object_ids, source_ids, geometry_ids, used_heads = set(), set(), set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise TailObjectsRevisionError("object 必须是对象")
        object_id = row.get("object_id")
        source_id = row.get("source_fragment_id")
        label_id = row.get("geometry_label_id")
        if not isinstance(object_id, str) or not object_id or object_id in object_ids:
            raise TailObjectsRevisionError("duplicate object_id")
        if (not isinstance(source_id, int) or isinstance(source_id, bool) or source_id in source_ids):
            raise TailObjectsRevisionError("duplicate source_fragment_id")
        if source_id not in core_ids:
            raise TailObjectsRevisionError("source_fragment_id 不存在于 TailCore 07")
        if object_id != _object_id(field_id, source_id):
            raise TailObjectsRevisionError("automatic object_id 稳定规则不匹配")
        if label_id != source_id or label_id in geometry_ids:
            raise TailObjectsRevisionError("object geometry identity 不匹配")
        if row.get("origin") != "automatic":
            raise TailObjectsRevisionError("Revision 0 object origin 必须为 automatic")
        status, head = row.get("association_status"), row.get("associated_head_id")
        if status not in ASSOCIATION_STATUSES:
            raise TailObjectsRevisionError("invalid association_status")
        if status == "unresolved" and head is not None:
            raise TailObjectsRevisionError("unresolved tail 不允许 associated_head_id")
        if status == "associated" and not isinstance(head, int):
            raise TailObjectsRevisionError("associated tail 必须有 associated_head_id")
        decision = decision_by_id.get(source_id)
        if decision is None or any(row.get(left) != decision.get(right) for left, right in (
                ("association_status", "association_status"), ("associated_head_id", "associated_head_id"),
                ("association_source", "source"), ("association_reason", "reason"))):
            raise TailObjectsRevisionError("AssociationResult association mismatch")
        if status == "associated":
            if head in used_heads:
                raise TailObjectsRevisionError("一个 head 只能关联一个自动 object")
            used_heads.add(head)
        object_ids.add(object_id); source_ids.add(source_id); geometry_ids.add(label_id)
    if source_ids != set(core_ids):
        raise TailObjectsRevisionError("objects 未完整覆盖 TailCore 07")
    if geometry_ids != set(_positive_ids(labels)):
        raise TailObjectsRevisionError("存在 orphan geometry ID 或 object 无 geometry")
    expected_summary = {"tail_count": len(rows), "associated_count": len(used_heads),
                        "unresolved_count": len(rows) - len(used_heads)}
    if result.get("summary") != expected_summary:
        raise TailObjectsRevisionError("summary 不一致")
    canonical_id = "tail-objects-r0-{}".format(_revision_identity(result))
    if result.get("revision_id") != canonical_id:
        raise TailObjectsRevisionError("revision_id 稳定规则不匹配")
    return True


def serialize_revision0(result, output_dir, tail_core_result, association_result,
                        geometry_labels):
    """Write deterministic JSON + independent immutable labels TIFF only."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stored = copy.deepcopy(result)
    labels = np.asarray(geometry_labels)
    path = output / GEOMETRY_LABEL_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), labels)
    stored["geometry"]["relative_path"] = GEOMETRY_LABEL_NAME
    stored["geometry"]["sha256"] = _sha256(path)
    stored["geometry"]["shape"] = [int(value) for value in labels.shape]
    stored["geometry"]["dtype"] = str(labels.dtype)
    stored["geometry"]["positive_ids"] = _positive_ids(labels)
    stored["revision_id"] = "tail-objects-r0-{}".format(_revision_identity(stored))
    validate_revision0(stored, tail_core_result, association_result, root_dir=output)
    json_path = output / REVISION_JSON_NAME
    temporary = Path(str(json_path) + ".tmp")
    temporary.write_bytes(canonical_json_bytes(stored) + b"\n")
    os.replace(str(temporary), str(json_path))
    return json_path


def load_revision0(output_dir, tail_core_result, association_result, expected_field_id=None):
    root = Path(output_dir)
    try:
        result = json.loads((root / REVISION_JSON_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise TailObjectsRevisionError("无法读取 TailObjects Revision JSON：{}".format(error))
    validate_revision0(result, tail_core_result, association_result, root_dir=root,
                       expected_field_id=expected_field_id)
    return result
