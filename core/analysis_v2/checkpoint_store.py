"""Tail V3 Phase 1B immutable checkpoint generation primitive.

This module is deliberately independent of UI, workers and the formal analysis
pipeline.  A generation becomes readable only after its completion marker is
written.  ``os.replace`` makes the staging-directory promotion visible as one
rename operation, but cannot by itself promise durability through sudden power
loss; that is a Phase 12 fault-injection acceptance concern.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from .input_fingerprint import InputManifestError, canonical_json_bytes


CHECKPOINT_SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"
COMPLETION_MARKER_NAME = "completion.json"
_FINGERPRINT_KEYS = (
    "input_fingerprint", "parameter_fingerprint", "producer_fingerprint",
)
_PICKLE_SUFFIXES = (".pkl", ".pickle", ".pyc")


class CheckpointError(RuntimeError):
    """Base error for checkpoint creation and reading."""


class CheckpointConflictError(CheckpointError):
    """A generation ID already exists and must never be overwritten."""


class CheckpointValidationError(CheckpointError):
    """A checkpoint is corrupt, incomplete, unsupported or incompatible."""


def _corrupt(message: str) -> CheckpointValidationError:
    return CheckpointValidationError("CORRUPT: {}".format(message))


def _incompatible(message: str) -> CheckpointValidationError:
    return CheckpointValidationError("INCOMPATIBLE: {}".format(message))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CheckpointValidationError("CORRUPT: {} 必须是非空字符串".format(name))
    return value.strip()


def _require_fingerprint(value: Any, name: str) -> str:
    text = _require_text(value, name).lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise CheckpointValidationError("CORRUPT: {} 必须是 SHA256 十六进制摘要".format(name))
    return text


def _safe_component(value: Any, name: str) -> str:
    text = _require_text(value, name)
    if text in (".", "..") or any(char in text for char in ("/", "\\", ":")):
        raise ValueError("{} 必须是安全的单个路径组件".format(name))
    return text


def _safe_relative_path(value: Any) -> str:
    """Return the sole accepted on-disk relative path form: slash-separated."""
    if not isinstance(value, str):
        raise ValueError("payload relative_path 必须是字符串")
    text = value.strip().replace("\\", "/")
    if not text or text.startswith("/") or text.startswith("//"):
        raise ValueError("payload relative_path 必须是相对路径")
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("payload relative_path 不能包含空段、. 或 ..")
    # Covers drive-qualified names (including C:/...) and ADS-like paths.
    if any(":" in part for part in parts):
        raise ValueError("payload relative_path 不能包含 drive-qualified 路径")
    return "/".join(parts)


def _safe_generation_path(root: Path, relative_path: str) -> Path:
    candidate = (root / Path(*relative_path.split("/"))).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        raise _corrupt("payload 路径逃离 generation root：{}".format(relative_path))
    return candidate


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write one file with flush/fsync then same-directory ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="t.", suffix=".tmp", dir=str(path.parent)
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    # Phase 1A is the single canonical JSON/hash implementation.
    try:
        return canonical_json_bytes(value)
    except InputManifestError as error:
        raise ValueError("checkpoint JSON 不可 canonicalize：{}".format(error))


class CheckpointAttempt(object):
    """The sole mutable writer for one private staging directory."""

    def __init__(self, store: "CheckpointStore", checkpoint_id: str, stage: str,
                 field_id: str, fingerprints: Dict[str, str], attempt_id: str,
                 metadata: Optional[Mapping[str, Any]] = None) -> None:
        self.store = store
        self.checkpoint_id = checkpoint_id
        self.stage = stage
        self.field_id = field_id
        self.fingerprints = fingerprints
        self.attempt_id = attempt_id
        self.metadata = dict(metadata or {})
        self.staging_path = store.staging_root / attempt_id
        self._files = []  # type: Sequence[Dict[str, Any]]
        self._closed = False
        self.staging_path.mkdir(parents=True, exist_ok=False)

    def _ensure_open(self) -> None:
        if self._closed:
            raise CheckpointError("checkpoint attempt 已关闭，不允许继续写入")

    def add_bytes(self, role: str, relative_path: str, data: bytes,
                  failure_injector: Optional[Callable[[str], None]] = None) -> Path:
        """Write one payload.  Pickle-like payload names are explicitly refused."""
        self._ensure_open()
        role = _require_text(role, "payload role")
        relative_path = _safe_relative_path(relative_path)
        if relative_path in (MANIFEST_NAME, COMPLETION_MARKER_NAME):
            raise ValueError("payload relative_path 不能占用 checkpoint 保留文件名")
        if relative_path.lower().endswith(_PICKLE_SUFFIXES):
            raise ValueError("checkpoint payload 禁止 pickle/marshal 类文件")
        if not isinstance(data, bytes):
            raise TypeError("payload 必须是 bytes")
        if any(item["role"] == role for item in self._files):
            raise ValueError("payload role 必须唯一：{}".format(role))
        if any(item["relative_path"] == relative_path for item in self._files):
            raise ValueError("payload relative_path 必须唯一：{}".format(relative_path))
        path = _safe_generation_path(self.staging_path, relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(path, data)
        if failure_injector is not None:
            failure_injector("payload_written")
        self._files = list(self._files) + [{
            "role": role,
            "relative_path": relative_path,
            "byte_size": len(data),
            "sha256": _sha256_bytes(data),
        }]
        return path

    def add_json(self, role: str, relative_path: str, value: Mapping[str, Any]) -> Path:
        return self.add_bytes(role, relative_path, _json_bytes(value))

    def add_file_payload(self, source_path: Path, relative_path: str, role: str,
                         failure_injector: Optional[Callable[[str], None]] = None) -> Path:
        """Copy an existing payload byte-for-byte into this generation.

        This is intentionally a general CheckpointStore primitive: callers do
        not get a side-channel writer that could bypass payload hashing,
        relative-path validation, or the immutable-generation contract.
        """
        self._ensure_open()
        source = Path(source_path).resolve()
        if not source.is_file():
            raise FileNotFoundError("checkpoint source payload 不存在：{}".format(source))
        role = _require_text(role, "payload role")
        relative_path = _safe_relative_path(relative_path)
        if relative_path in (MANIFEST_NAME, COMPLETION_MARKER_NAME):
            raise ValueError("payload relative_path 不能占用 checkpoint 保留文件名")
        if relative_path.lower().endswith(_PICKLE_SUFFIXES):
            raise ValueError("checkpoint payload 禁止 pickle/marshal 类文件")
        if any(item["role"] == role for item in self._files):
            raise ValueError("payload role 必须唯一：{}".format(role))
        if any(item["relative_path"] == relative_path for item in self._files):
            raise ValueError("payload relative_path 必须唯一：{}".format(relative_path))
        target = _safe_generation_path(self.staging_path, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(str(source), str(target))
            # Windows does not permit fsync on a read-only file descriptor.
            # ``rb+`` does not change copied bytes and permits the durability
            # barrier required before the manifest records this payload.
            with target.open("rb+") as handle:
                os.fsync(handle.fileno())
        except Exception:
            if target.exists():
                target.unlink()
            raise
        if failure_injector is not None:
            failure_injector("file_payload_written")
        self._files = list(self._files) + [{
            "role": role,
            "relative_path": relative_path,
            "byte_size": target.stat().st_size,
            "sha256": _sha256_file(target),
        }]
        return target

    def _manifest(self) -> Dict[str, Any]:
        if not self._files:
            raise CheckpointError("checkpoint 至少需要一个 payload")
        files = sorted(self._files, key=lambda item: (item["relative_path"], item["role"]))
        manifest = {
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "checkpoint_id": self.checkpoint_id,
            "stage": self.stage,
            "field_id": self.field_id,
            "input_fingerprint": self.fingerprints["input_fingerprint"],
            "parameter_fingerprint": self.fingerprints["parameter_fingerprint"],
            "producer_fingerprint": self.fingerprints["producer_fingerprint"],
            "dependencies": [],
            "files": files,
        }
        if self.metadata:
            manifest["metadata"] = self.metadata
        return manifest

    def commit(self, failure_injector: Optional[Callable[[str], None]] = None) -> Path:
        """Validate staging, promote once, then publish the final marker."""
        self._ensure_open()
        injector = failure_injector or (lambda phase: None)
        try:
            manifest = self._manifest()
            manifest_bytes = _json_bytes(manifest)
            _atomic_write_bytes(self.staging_path / MANIFEST_NAME, manifest_bytes)
            injector("manifest_written")
            _validate_generation(self.staging_path, require_marker=False)
            injector("staging_validated")
            target = self.store.generations_root / self.checkpoint_id
            if target.exists():
                raise CheckpointConflictError("generation 已存在，拒绝覆盖：{}".format(target))
            # staging and generations are children of one root, so this is a
            # same-volume directory rename, not a copy-and-delete operation.
            # rename (rather than replace) preserves the no-overwrite contract.
            os.rename(str(self.staging_path), str(target))
            injector("promoted")
            marker = {
                "checkpoint_id": self.checkpoint_id,
                "status": "complete",
                "manifest_sha256": _sha256_bytes(manifest_bytes),
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
            }
            injector("before_marker")
            # Test hooks are deliberately before the final write: an exception
            # at this boundary must leave the promoted directory unreadable.
            injector("marker_writing")
            _atomic_write_bytes(target / COMPLETION_MARKER_NAME, _json_bytes(marker))
            self._closed = True
            return target
        except Exception:
            # Failed staging is intentionally retained for diagnosis.  A failed
            # post-promote generation has no marker and readers reject it.
            self._closed = True
            raise


class CheckpointStore(object):
    """Root owner for private staging attempts and immutable generations."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.staging_root = self.root / "staging"
        self.generations_root = self.root / "generations"
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self.generations_root.mkdir(parents=True, exist_ok=True)

    def begin(self, checkpoint_id: str, stage: str, field_id: str,
              input_fingerprint: str, parameter_fingerprint: str,
              producer_fingerprint: str, attempt_id: Optional[str] = None,
              metadata: Optional[Mapping[str, Any]] = None) -> CheckpointAttempt:
        checkpoint_id = _safe_component(checkpoint_id, "checkpoint_id")
        attempt_id = _safe_component(attempt_id or uuid.uuid4().hex, "attempt_id")
        fingerprints = {
            "input_fingerprint": _require_fingerprint(input_fingerprint, "input_fingerprint"),
            "parameter_fingerprint": _require_fingerprint(parameter_fingerprint, "parameter_fingerprint"),
            "producer_fingerprint": _require_fingerprint(producer_fingerprint, "producer_fingerprint"),
        }
        if (self.staging_root / attempt_id).exists():
            raise CheckpointConflictError("attempt staging 已存在：{}".format(attempt_id))
        if (self.generations_root / checkpoint_id).exists():
            raise CheckpointConflictError("generation 已存在，拒绝覆盖：{}".format(checkpoint_id))
        return CheckpointAttempt(self, checkpoint_id, _require_text(stage, "stage"),
                                 _require_text(field_id, "field_id"), fingerprints,
                                 attempt_id, metadata)

    def load_checkpoint(self, path: Path,
                        expected: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
        generation = Path(path).resolve()
        try:
            generation.relative_to(self.generations_root.resolve())
        except ValueError:
            raise CheckpointValidationError("CORRUPT: reader 只接受 generations 内的路径")
        return _validate_generation(generation, require_marker=True, expected=expected)


def _read_json(path: Path, label: str) -> Tuple[Dict[str, Any], bytes]:
    if not path.is_file():
        raise _corrupt("{} 不存在".format(label))
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise _corrupt("{} 无法读取：{}".format(label, error))
    if not isinstance(value, dict):
        raise _corrupt("{} 顶层必须是对象".format(label))
    try:
        if _json_bytes(value) != raw:
            raise _corrupt("{} 不是 Phase 1A canonical JSON".format(label))
    except ValueError as error:
        raise _corrupt("{} 不合法：{}".format(label, error))
    return value, raw


def _validate_generation(root: Path, require_marker: bool,
                         expected: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    manifest, manifest_bytes = _read_json(root / MANIFEST_NAME, "manifest")
    version = manifest.get("checkpoint_schema_version")
    if version is None:
        raise _corrupt("checkpoint_schema_version 缺失")
    if version != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointValidationError("UNSUPPORTED_SCHEMA: checkpoint schema_version={}".format(version))
    checkpoint_id = _safe_component(manifest.get("checkpoint_id"), "checkpoint_id")
    if require_marker and root.name != checkpoint_id:
        raise _corrupt("generation 目录名与 manifest checkpoint_id 不匹配")
    _require_text(manifest.get("stage"), "stage")
    _require_text(manifest.get("field_id"), "field_id")
    for key in _FINGERPRINT_KEYS:
        _require_fingerprint(manifest.get(key), key)
    if not isinstance(manifest.get("dependencies"), list):
        raise _corrupt("dependencies 必须是数组")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise _corrupt("files 必须是非空数组")
    normalized = []
    roles = set()
    paths = set()
    for item in files:
        if not isinstance(item, dict):
            raise _corrupt("files 项必须是对象")
        role = _require_text(item.get("role"), "payload role")
        try:
            relative_path = _safe_relative_path(item.get("relative_path"))
        except ValueError as error:
            raise _corrupt(str(error))
        byte_size = item.get("byte_size")
        if not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size < 0:
            raise _corrupt("payload byte_size 必须是非负整数")
        digest = _require_fingerprint(item.get("sha256"), "payload sha256")
        if role in roles or relative_path in paths:
            raise _corrupt("payload role 和 relative_path 必须唯一")
        roles.add(role)
        paths.add(relative_path)
        normalized.append({"role": role, "relative_path": relative_path,
                           "byte_size": byte_size, "sha256": digest})
        payload = _safe_generation_path(root, relative_path)
        if not payload.is_file():
            raise _corrupt("payload 不存在：{}".format(relative_path))
        if payload.stat().st_size != byte_size:
            raise _corrupt("payload byte_size 不匹配：{}".format(relative_path))
        if _sha256_file(payload) != digest:
            raise _corrupt("payload SHA256 不匹配：{}".format(relative_path))
    if normalized != sorted(normalized, key=lambda item: (item["relative_path"], item["role"])):
        raise _corrupt("files 必须按 relative_path、role canonical 排序")
    if require_marker:
        marker, marker_bytes = _read_json(root / COMPLETION_MARKER_NAME, "completion marker")
        if marker.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointValidationError("UNSUPPORTED_SCHEMA: completion marker schema_version={}".format(marker.get("schema_version")))
        if marker.get("checkpoint_id") != checkpoint_id or marker.get("status") != "complete":
            raise _corrupt("completion marker checkpoint_id 或 status 无效")
        if marker.get("manifest_sha256") != _sha256_bytes(manifest_bytes):
            raise _corrupt("completion marker manifest_sha256 不匹配")
        del marker_bytes
    if expected:
        for key, actual in expected.items():
            if key not in ("stage", "field_id") + _FINGERPRINT_KEYS:
                raise ValueError("不支持的 expected 字段：{}".format(key))
            if actual != manifest.get(key):
                raise _incompatible("{} 不匹配".format(key))
    return manifest
