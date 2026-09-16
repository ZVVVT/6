import hashlib
import json
from pathlib import Path
from pathlib import PureWindowsPath

import pytest

from core.analysis_v2.checkpoint_store import (
    COMPLETION_MARKER_NAME, MANIFEST_NAME, CheckpointConflictError,
    CheckpointStore, CheckpointValidationError,
)
from core.analysis_v2 import checkpoint_store as checkpoint_module
from core.analysis_v2.input_fingerprint import canonical_json_bytes


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def begin(store, checkpoint_id="cp-1", attempt_id="attempt-1"):
    return store.begin(checkpoint_id, "tail_core", "视野一", digest("input"),
                       digest("parameters"), digest("producer"), attempt_id)


def complete(store, checkpoint_id="cp-1", attempt_id="attempt-1"):
    attempt = begin(store, checkpoint_id, attempt_id)
    attempt.add_bytes("objects", "payload\\对象.bin", b"\x00hello")
    attempt.add_json("metadata", "payload/meta.json", {"b": 2, "a": 1})
    return attempt.commit()


def test_atomic_write_uses_short_same_directory_temporary_name(tmp_path, monkeypatch):
    target = tmp_path / "labels" / "tail_objects_revision_labels.tif"
    original_mkstemp = checkpoint_module.tempfile.mkstemp
    captured = {}

    def capture_mkstemp(**kwargs):
        descriptor, temporary_name = original_mkstemp(**kwargs)
        captured.update(kwargs)
        captured["temporary_name"] = temporary_name
        return descriptor, temporary_name

    monkeypatch.setattr(checkpoint_module.tempfile, "mkstemp", capture_mkstemp)
    checkpoint_module._atomic_write_bytes(target, b"exact-bytes")

    temporary = Path(captured["temporary_name"])
    assert target.read_bytes() == b"exact-bytes"
    assert captured["dir"] == str(target.parent)
    assert temporary.parent == target.parent
    assert captured["prefix"] == "t."
    assert captured["suffix"] == ".tmp"
    assert target.name not in temporary.name
    assert temporary.name.startswith("t.")
    assert temporary.name.endswith(".tmp")
    assert len(temporary.name) == 14
    assert not temporary.exists()


def test_atomic_write_replaces_existing_target_bytes_exact(tmp_path):
    target = tmp_path / "payload.bin"
    target.write_bytes(b"old-value")
    checkpoint_module._atomic_write_bytes(target, b"\x00new-value\xff")
    assert target.read_bytes() == b"\x00new-value\xff"


def test_atomic_write_failure_cleans_temporary_file_and_creates_parent(tmp_path, monkeypatch):
    target = tmp_path / "missing-parent" / "payload.bin"
    original_mkstemp = checkpoint_module.tempfile.mkstemp
    captured = {}

    def capture_mkstemp(**kwargs):
        descriptor, temporary_name = original_mkstemp(**kwargs)
        captured["temporary_name"] = temporary_name
        return descriptor, temporary_name

    monkeypatch.setattr(checkpoint_module.tempfile, "mkstemp", capture_mkstemp)
    monkeypatch.setattr(checkpoint_module.os, "fsync", lambda descriptor: (_ for _ in ()).throw(OSError("fsync failed")))
    with pytest.raises(OSError, match="fsync failed"):
        checkpoint_module._atomic_write_bytes(target, b"bytes")
    assert target.parent.is_dir()
    assert not target.exists()
    assert not Path(captured["temporary_name"]).exists()


def test_client_path_budget_uses_short_temporary_basename_contract():
    target_name = "tail_objects_revision_labels.tif"
    target_text = (
        "F:\\" + ("x" * (251 - 4 - len(target_name))) + "\\" + target_name
    )
    target = PureWindowsPath(target_text)
    old_temporary_chars = len(str(target)) + 14
    new_temporary_name_chars = len("t.") + 8 + len(".tmp")
    new_temporary_chars = len(str(target.parent)) + 1 + new_temporary_name_chars

    assert len(str(target)) == 251
    assert old_temporary_chars == 265
    assert target.name == target_name
    assert target_name not in "t." + ("a" * 8) + ".tmp"
    assert new_temporary_name_chars == 14
    assert new_temporary_chars == 233
    assert new_temporary_chars < 260
    assert old_temporary_chars - new_temporary_chars == 32


def test_create_staging_promote_marker_reader_and_unicode_payload(tmp_path):
    store = CheckpointStore(tmp_path / "检查点")
    generation = complete(store)
    assert not (store.staging_root / "attempt-1").exists()
    assert generation == store.generations_root / "cp-1"
    manifest = store.load_checkpoint(generation)
    assert manifest["field_id"] == "视野一"
    assert (generation / "payload" / "对象.bin").read_bytes() == b"\x00hello"
    assert [row["relative_path"] for row in manifest["files"]] == ["payload/meta.json", "payload/对象.bin"]


def test_manifest_is_phase1a_canonical_and_stable_for_payload_order(tmp_path):
    first = CheckpointStore(tmp_path / "first")
    second = CheckpointStore(tmp_path / "second")
    a = begin(first)
    a.add_bytes("z", "z.bin", b"z")
    a.add_bytes("a", "a.bin", b"a")
    first_path = a.commit()
    b = begin(second)
    b.add_bytes("a", "a.bin", b"a")
    b.add_bytes("z", "z.bin", b"z")
    second_path = b.commit()
    first_manifest = (first_path / MANIFEST_NAME).read_bytes()
    second_manifest = (second_path / MANIFEST_NAME).read_bytes()
    assert first_manifest == second_manifest == canonical_json_bytes(json.loads(first_manifest.decode("utf-8")))


@pytest.mark.parametrize("file_name", [COMPLETION_MARKER_NAME, MANIFEST_NAME])
def test_missing_marker_or_manifest_is_invalid(tmp_path, file_name):
    store = CheckpointStore(tmp_path)
    generation = complete(store)
    (generation / file_name).unlink()
    with pytest.raises(CheckpointValidationError, match="CORRUPT"):
        store.load_checkpoint(generation)


def test_manifest_marker_and_payload_tampering_are_corrupt(tmp_path):
    store = CheckpointStore(tmp_path)
    generation = complete(store)
    (generation / MANIFEST_NAME).write_bytes(b"{}")
    with pytest.raises(CheckpointValidationError, match="CORRUPT"):
        store.load_checkpoint(generation)
    generation = complete(store, "cp-2", "attempt-2")
    marker = json.loads((generation / COMPLETION_MARKER_NAME).read_text(encoding="utf-8"))
    marker["manifest_sha256"] = digest("wrong")
    (generation / COMPLETION_MARKER_NAME).write_bytes(canonical_json_bytes(marker))
    with pytest.raises(CheckpointValidationError, match="CORRUPT"):
        store.load_checkpoint(generation)
    generation = complete(store, "cp-3", "attempt-3")
    (generation / "payload" / "对象.bin").write_bytes(b"changed")
    with pytest.raises(CheckpointValidationError, match="CORRUPT"):
        store.load_checkpoint(generation)


def test_payload_missing_and_byte_size_change_are_corrupt(tmp_path):
    store = CheckpointStore(tmp_path)
    generation = complete(store)
    (generation / "payload" / "对象.bin").unlink()
    with pytest.raises(CheckpointValidationError, match="CORRUPT"):
        store.load_checkpoint(generation)
    generation = complete(store, "cp-2", "attempt-2")
    (generation / "payload" / "对象.bin").write_bytes(b"\x00hello-more")
    with pytest.raises(CheckpointValidationError, match="byte_size"):
        store.load_checkpoint(generation)


@pytest.mark.parametrize("key", ["input_fingerprint", "parameter_fingerprint", "producer_fingerprint"])
def test_expected_fingerprint_mismatch_is_incompatible(tmp_path, key):
    store = CheckpointStore(tmp_path)
    generation = complete(store)
    with pytest.raises(CheckpointValidationError, match="INCOMPATIBLE.*{}".format(key)):
        store.load_checkpoint(generation, expected={key: digest("different")})


def test_unknown_schema_is_rejected(tmp_path):
    store = CheckpointStore(tmp_path)
    generation = complete(store)
    manifest = json.loads((generation / MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["checkpoint_schema_version"] = 99
    (generation / MANIFEST_NAME).write_bytes(canonical_json_bytes(manifest))
    marker = json.loads((generation / COMPLETION_MARKER_NAME).read_text(encoding="utf-8"))
    marker["manifest_sha256"] = hashlib.sha256((generation / MANIFEST_NAME).read_bytes()).hexdigest()
    (generation / COMPLETION_MARKER_NAME).write_bytes(canonical_json_bytes(marker))
    with pytest.raises(CheckpointValidationError, match="UNSUPPORTED_SCHEMA"):
        store.load_checkpoint(generation)


def test_existing_generation_and_closed_attempt_cannot_be_overwritten(tmp_path):
    store = CheckpointStore(tmp_path)
    complete(store)
    with pytest.raises(CheckpointConflictError):
        begin(store, "cp-1", "attempt-2")
    attempt = begin(store, "cp-2", "attempt-3")
    attempt.add_bytes("one", "one.bin", b"one")
    attempt.commit()
    with pytest.raises(Exception, match="已关闭"):
        attempt.add_bytes("two", "two.bin", b"two")
    with pytest.raises(Exception, match="已关闭"):
        attempt.commit()


@pytest.mark.parametrize("relative_path", ["C:\\bad.bin", "\\\\server\\share\\bad.bin", "../outside", "a\\..\\..\\outside", "a/../bad", "one.pkl"])
def test_unsafe_or_pickle_payload_paths_are_rejected(tmp_path, relative_path):
    attempt = begin(CheckpointStore(tmp_path))
    with pytest.raises(ValueError):
        attempt.add_bytes("role", relative_path, b"x")


def test_nan_and_inf_cannot_enter_checkpoint_json(tmp_path):
    attempt = begin(CheckpointStore(tmp_path))
    with pytest.raises(ValueError, match="NaN 和 Inf"):
        attempt.add_json("meta", "meta.json", {"value": float("nan")})
    bad_metadata = CheckpointStore(tmp_path / "other").begin(
            "cp", "tail_core", "field", digest("i"), digest("p"), digest("r"),
            metadata={"value": float("inf")},
        )
    bad_metadata.add_json("meta", "meta.json", {"ok": True})
    with pytest.raises(ValueError, match="NaN 和 Inf"):
        bad_metadata.commit()


def test_payload_write_failure_leaves_only_ignored_staging(tmp_path):
    store = CheckpointStore(tmp_path)
    attempt = begin(store)
    with pytest.raises(RuntimeError, match="payload"):
        attempt.add_bytes("one", "one.bin", b"one", lambda phase: (_ for _ in ()).throw(RuntimeError(phase)))
    assert (attempt.staging_path / "one.bin").is_file()
    assert not (store.generations_root / "cp-1").exists()
    with pytest.raises(CheckpointValidationError, match="generations"):
        store.load_checkpoint(attempt.staging_path)


@pytest.mark.parametrize("failure_phase", ["manifest_written", "staging_validated", "promoted", "before_marker", "marker_writing"])
def test_failure_injection_never_makes_partial_result_readable(tmp_path, failure_phase):
    store = CheckpointStore(tmp_path)
    attempt = begin(store)
    attempt.add_bytes("one", "one.bin", b"one")

    def inject(phase):
        if phase == failure_phase:
            raise RuntimeError("injected {}".format(phase))

    with pytest.raises(RuntimeError, match="injected"):
        attempt.commit(inject)
    generation = store.generations_root / "cp-1"
    if generation.exists():
        with pytest.raises(CheckpointValidationError):
            store.load_checkpoint(generation)
    else:
        assert (store.staging_root / "attempt-1").exists()


def test_marker_write_exception_leaves_promoted_generation_invalid(tmp_path, monkeypatch):
    store = CheckpointStore(tmp_path)
    attempt = begin(store)
    attempt.add_bytes("one", "one.bin", b"one")
    original = checkpoint_module._atomic_write_bytes

    def fail_only_marker(path, data):
        if Path(path).name == COMPLETION_MARKER_NAME:
            raise OSError("marker write failed")
        return original(path, data)

    monkeypatch.setattr(checkpoint_module, "_atomic_write_bytes", fail_only_marker)
    with pytest.raises(OSError, match="marker write failed"):
        attempt.commit()
    with pytest.raises(CheckpointValidationError, match="CORRUPT"):
        store.load_checkpoint(store.generations_root / "cp-1")


def test_reader_rejects_staging_and_expected_stage_or_field_mismatch(tmp_path):
    store = CheckpointStore(tmp_path)
    attempt = begin(store)
    attempt.add_bytes("one", "one.bin", b"one")
    with pytest.raises(CheckpointValidationError, match="generations"):
        store.load_checkpoint(attempt.staging_path)
    generation = attempt.commit()
    with pytest.raises(CheckpointValidationError, match="INCOMPATIBLE.*stage"):
        store.load_checkpoint(generation, expected={"stage": "head"})
    with pytest.raises(CheckpointValidationError, match="INCOMPATIBLE.*field_id"):
        store.load_checkpoint(generation, expected={"field_id": "other"})
