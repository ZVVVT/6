"""Tail V3 Phase 1A input manifests and deterministic fingerprints.

This module deliberately has no UI, worker, checkpoint I/O, or scheduler
dependency.  Its JSON contracts describe business identity only; callers may
keep diagnostic paths beside a manifest, but diagnostics never enter a hash.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from .environment_snapshot import sha256_file


INPUT_MANIFEST_SCHEMA_VERSION = 1
FINGERPRINT_SCHEMA_VERSION = 1
SUPPORTED_INPUT_MANIFEST_SCHEMA_VERSIONS = frozenset((1,))
INPUT_ROLES = ("FITC", "TRITC", "Merge")
STAGE_HEAD = "head"
STAGE_TAIL_CORE = "tail_core"
STAGE_ASSOCIATION = "association"
STAGES = (STAGE_HEAD, STAGE_TAIL_CORE, STAGE_ASSOCIATION)
_PATH_KEYS = frozenset(("relative_path", "logical_reference"))


class InputManifestError(ValueError):
    """The manifest cannot safely represent a Phase 1A business identity."""


def _normalise_relative_path(value: str) -> str:
    text = str(value).strip().replace("\\", "/")
    if not text or text.startswith("/") or ":" in text.split("/", 1)[0]:
        raise InputManifestError("业务路径必须是非空相对路径：{}".format(value))
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise InputManifestError("业务路径不能包含空段、. 或 ..：{}".format(value))
    return "/".join(parts)


def _canonical_value(value: Any, key: Optional[str] = None) -> Any:
    """Convert JSON-compatible values to one deterministic, safe form."""
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InputManifestError("NaN 和 Inf 不能进入 canonical fingerprint")
        # JSON's representation is deterministic for a fixed Python version;
        # 17 significant digits preserve the IEEE-754 value cross-process.
        return {"__float__": format(value, ".17g")}
    if isinstance(value, str):
        return _normalise_relative_path(value) if key in _PATH_KEYS else value
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, Mapping):
        result = {}
        for child_key in sorted(value):
            if not isinstance(child_key, str):
                raise InputManifestError("JSON object key 必须是字符串")
            result[child_key] = _canonical_value(value[child_key], child_key)
        return result
    raise InputManifestError("不支持的 fingerprint 值类型：{}".format(type(value).__name__))


def canonical_json_bytes(value: Any) -> bytes:
    """UTF-8, sorted-key, compact JSON bytes used by every Phase 1A hash."""
    canonical = _canonical_value(value)
    return json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _require_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InputManifestError("{} 必须是非空字符串".format(key))
    return value.strip()


def _input_identity(record: Mapping[str, Any]) -> Dict[str, Any]:
    role = _require_text(record, "role")
    if role not in INPUT_ROLES:
        raise InputManifestError("不支持的输入 role：{}".format(role))
    location_key = "relative_path" if record.get("relative_path") else "logical_reference"
    location = _normalise_relative_path(_require_text(record, location_key))
    sha256 = _require_text(record, "sha256").lower()
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise InputManifestError("sha256 必须是小写或大写十六进制摘要")
    byte_size = record.get("byte_size")
    if not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size < 0:
        raise InputManifestError("byte_size 必须是非负整数")
    result = {"role": role, location_key: location, "sha256": sha256,
              "byte_size": byte_size}
    for key in ("shape", "dtype"):
        if key in record and record[key] is not None:
            result[key] = record[key]
    return result


def build_input_manifest(field_id: str, protein_key: str, protein_part: str,
                         image_width: int, image_height: int,
                         inputs: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Build and validate the full, portable field input manifest."""
    if not isinstance(image_width, int) or image_width <= 0:
        raise InputManifestError("image_width 必须为正整数")
    if not isinstance(image_height, int) or image_height <= 0:
        raise InputManifestError("image_height 必须为正整数")
    rows = [_input_identity(item) for item in inputs]
    if {row["role"] for row in rows} != set(INPUT_ROLES) or len(rows) != 3:
        raise InputManifestError("一个 field 必须恰好包含 FITC、TRITC、Merge 各一个输入")
    rows.sort(key=lambda item: INPUT_ROLES.index(item["role"]))
    manifest = {
        "schema_version": INPUT_MANIFEST_SCHEMA_VERSION,
        "field_id": _require_text({"field_id": field_id}, "field_id"),
        "protein_key": _require_text({"protein_key": protein_key}, "protein_key"),
        "protein_part": _require_text({"protein_part": protein_part}, "protein_part"),
        "image_width": image_width,
        "image_height": image_height,
        "inputs": rows,
    }
    validate_input_manifest(manifest)
    return manifest


def describe_input_file(role: str, path: Path, task_root: Optional[Path] = None,
                        logical_reference: Optional[str] = None,
                        shape: Optional[Iterable[int]] = None,
                        dtype: Optional[str] = None) -> Dict[str, Any]:
    """Create an input record.  ``absolute_path`` is diagnostic-only."""
    source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError("输入文件不存在：{}".format(source))
    record = {"role": role, "sha256": sha256_file(source),
              "byte_size": source.stat().st_size, "absolute_path": str(source)}
    if logical_reference:
        record["logical_reference"] = logical_reference
    elif task_root is not None:
        record["relative_path"] = source.relative_to(Path(task_root).resolve()).as_posix()
    else:
        raise InputManifestError("必须提供 task_root 或 logical_reference")
    if shape is not None:
        record["shape"] = list(shape)
    if dtype is not None:
        record["dtype"] = str(dtype)
    return record


def validate_input_manifest(manifest: Mapping[str, Any]) -> None:
    if not isinstance(manifest, Mapping):
        raise InputManifestError("Input Manifest 顶层必须是对象")
    version = manifest.get("schema_version")
    if version not in SUPPORTED_INPUT_MANIFEST_SCHEMA_VERSIONS:
        raise InputManifestError("不支持的 Input Manifest schema_version：{}".format(version))
    required = ("field_id", "protein_key", "protein_part", "image_width",
                "image_height", "inputs")
    for key in required:
        if key not in manifest:
            raise InputManifestError("Input Manifest 缺少 {}".format(key))
    _require_text(manifest, "field_id")
    _require_text(manifest, "protein_key")
    _require_text(manifest, "protein_part")
    for key in ("image_width", "image_height"):
        value = manifest[key]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise InputManifestError("{} 必须为正整数".format(key))
    rows = manifest["inputs"]
    if not isinstance(rows, list):
        raise InputManifestError("inputs 必须是数组")
    identities = [_input_identity(row) for row in rows]
    if {row["role"] for row in identities} != set(INPUT_ROLES) or len(identities) != 3:
        raise InputManifestError("一个 field 必须恰好包含 FITC、TRITC、Merge 各一个输入")


def stage_input_projection(manifest: Mapping[str, Any], stage: str,
                           upstream_fingerprints: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Return only raw inputs actually consumed by a Tail V3 stage.

    Head segmentation consumes TRITC; C18B Tail Core consumes FITC; association
    consumes no raw channel and instead receives Head/Tail artifacts explicitly.
    """
    validate_input_manifest(manifest)
    if stage not in STAGES:
        raise InputManifestError("不支持的 stage：{}".format(stage))
    roles = {STAGE_HEAD: ("TRITC",), STAGE_TAIL_CORE: ("FITC",),
             STAGE_ASSOCIATION: ()}[stage]
    # Rebuild each record from its business fields, so a caller may retain an
    # absolute diagnostic path in the in-memory manifest without polluting ID.
    records = dict((item["role"], _input_identity(item)) for item in manifest["inputs"])
    projection = {
        "schema_version": FINGERPRINT_SCHEMA_VERSION,
        "stage": stage,
        "field_id": manifest["field_id"],
        "protein_key": manifest["protein_key"],
        "protein_part": manifest["protein_part"],
        "image_width": manifest["image_width"],
        "image_height": manifest["image_height"],
        "inputs": [records[role] for role in roles],
    }
    if upstream_fingerprints:
        projection["upstream_fingerprints"] = dict(upstream_fingerprints)
    return projection


def stage_input_fingerprint(manifest: Mapping[str, Any], stage: str,
                            upstream_fingerprints: Optional[Mapping[str, str]] = None) -> str:
    return fingerprint(stage_input_projection(manifest, stage, upstream_fingerprints))


def parameter_fingerprint(stage: str, effective_parameters: Mapping[str, Any]) -> str:
    if stage not in STAGES:
        raise InputManifestError("不支持的 stage：{}".format(stage))
    if not isinstance(effective_parameters, Mapping):
        raise InputManifestError("effective_parameters 必须是对象")
    return fingerprint({"schema_version": FINGERPRINT_SCHEMA_VERSION, "stage": stage,
                        "effective_parameters": effective_parameters})


def producer_fingerprint(stage: str, algorithm_version: str,
                         producer_schema_version: int = 1,
                         resources: Optional[Mapping[str, str]] = None) -> str:
    if stage not in STAGES:
        raise InputManifestError("不支持的 stage：{}".format(stage))
    identity = {"schema_version": FINGERPRINT_SCHEMA_VERSION, "stage": stage,
                "algorithm_version": _require_text({"algorithm_version": algorithm_version}, "algorithm_version"),
                "producer_schema_version": producer_schema_version,
                "resources": dict(resources or {})}
    return fingerprint(identity)
