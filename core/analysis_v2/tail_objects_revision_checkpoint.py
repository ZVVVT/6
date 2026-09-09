"""Immutable TailObjects Revision 0 checkpoints (Tail V3 Phase 4D1)."""

from io import BytesIO
from pathlib import Path
import time

import tifffile

from .association_result import load_association_result
from .checkpoint_store import CheckpointStore
from .environment_snapshot import sha256_file
from .input_fingerprint import (
    STAGE_TAIL_OBJECTS_REVISION, canonical_json_bytes, fingerprint,
)
from .tail_core_result import load_tail_core_result
from .tail_objects_revision import (
    GEOMETRY_LABEL_NAME, REVISION_JSON_NAME, build_revision0, load_revision0,
)


CHECKPOINT_ID_PREFIX = "tail_objects_revision_0000"
ALGORITHM_VERSION = "tail-v3-tail-objects-revision-checkpoint-v1"
REVISION_CONTRACT = "tail-objects-revision-v1"


class TailObjectsRevisionCheckpointError(RuntimeError):
    """Revision 0 checkpoint creation or validation failed."""


def _manifest_identity(checkpoint, name):
    generation = Path(checkpoint.get("generation", "")).resolve()
    manifest = generation / "manifest.json"
    if not manifest.is_file():
        raise TailObjectsRevisionCheckpointError(
            "{} immutable manifest 不存在：{}".format(name, manifest))
    return sha256_file(manifest)


def _checkpoint_root(task_root, field_id):
    return (Path(task_root) / "checkpoints" / "tail_objects" / str(field_id)
            / "revisions" / "revision_0000")


def _expected_fingerprints(project_root, tail_core_checkpoint,
                           association_checkpoint):
    upstream = {
        "tail_core_artifact": _manifest_identity(tail_core_checkpoint, "TailCore"),
        "association_artifact": _manifest_identity(association_checkpoint, "Association"),
    }
    parameter_contract = {
        "revision_contract": REVISION_CONTRACT,
        "geometry_mode": "TailCoreResult.filtered_07_exact_copy",
        "automatic_revision": 0,
    }
    root = Path(project_root)
    resources = {}
    for path in (Path(__file__), Path(__file__).with_name("tail_objects_revision.py")):
        if not path.is_file():
            raise TailObjectsRevisionCheckpointError("Revision producer 缺失：{}".format(path))
        resources[str(path.relative_to(root)).replace("\\", "/")] = sha256_file(path)
    return upstream, {
        "input_fingerprint": fingerprint({
            "stage": STAGE_TAIL_OBJECTS_REVISION,
            "tail_core_artifact_identity": upstream["tail_core_artifact"],
            "association_artifact_identity": upstream["association_artifact"],
        }),
        "parameter_fingerprint": fingerprint({
            "stage": STAGE_TAIL_OBJECTS_REVISION,
            "effective_parameters": parameter_contract,
        }),
        "producer_fingerprint": fingerprint({
            "stage": STAGE_TAIL_OBJECTS_REVISION,
            "algorithm_version": ALGORITHM_VERSION, "resources": resources,
        }),
    }


def _load_upstreams(tail_core_checkpoint, association_checkpoint, field_id):
    core_generation = Path(tail_core_checkpoint["generation"])
    association_generation = Path(association_checkpoint["generation"])
    core_manifest = CheckpointStore(core_generation.parent.parent).load_checkpoint(core_generation)
    CheckpointStore(association_generation.parent.parent).load_checkpoint(association_generation)
    core = load_tail_core_result(core_generation, expected_field_id=field_id)
    tail_ids = core["labels"]["filtered_07"]["positive_ids"]
    supplied = association_checkpoint.get("association_result") or {}
    head_ids = [row["associated_head_id"] for row in supplied.get("tails", [])
                if row.get("association_status") == "associated"]
    association = load_association_result(
        association_generation, tail_ids, head_ids, expected_field_id=field_id,
        expected_fingerprints=supplied.get("fingerprints"),
    )
    files = dict((item["role"], item) for item in core_manifest["files"])
    filtered = core_generation / files["filtered_07"]["relative_path"]
    if not filtered.is_file():
        raise TailObjectsRevisionCheckpointError("TailCore 07 payload 不存在：{}".format(filtered))
    return core, association, filtered


def _tiff_bytes(labels):
    buffer = BytesIO()
    tifffile.imwrite(buffer, labels)
    return buffer.getvalue()


def write_and_verify_revision0_checkpoint(task_root, project_root, field_id,
                                          tail_core_checkpoint,
                                          association_checkpoint,
                                          failure_injector=None):
    """Build, stage, validate, commit, reload and validate Revision 0 once."""
    started = time.perf_counter()
    field_id = str(field_id)
    checkpoint_root = _checkpoint_root(task_root, field_id)
    generation = None
    inject = failure_injector or (lambda phase: None)
    try:
        upstream, expected = _expected_fingerprints(
            project_root, tail_core_checkpoint, association_checkpoint)
        core, association, filtered_path = _load_upstreams(
            tail_core_checkpoint, association_checkpoint, field_id)
        labels = tifffile.imread(str(filtered_path))
        result = build_revision0(core, association, labels)
        store = CheckpointStore(checkpoint_root)
        attempt = store.begin(
            CHECKPOINT_ID_PREFIX, STAGE_TAIL_OBJECTS_REVISION, field_id,
            expected["input_fingerprint"], expected["parameter_fingerprint"],
            expected["producer_fingerprint"], metadata={
                "revision_number": 0, "revision_id": result["revision_id"],
                "upstream_artifact_identities": upstream,
            })
        label_bytes = _tiff_bytes(labels)
        # The JSON itself records the SHA of these independent checkpoint labels.
        import hashlib
        stored = dict(result)
        stored["geometry"] = dict(result["geometry"])
        stored["geometry"]["sha256"] = hashlib.sha256(label_bytes).hexdigest()
        stored["revision_id"] = result["revision_id"]
        attempt.add_bytes("tail_objects_revision", REVISION_JSON_NAME,
                          canonical_json_bytes(stored))
        inject("json_written")
        attempt.add_bytes("revision_geometry_labels", GEOMETRY_LABEL_NAME, label_bytes)
        inject("tiff_written")
        generation = attempt.commit(failure_injector=inject)
        manifest = store.load_checkpoint(generation, expected=dict(
            stage=STAGE_TAIL_OBJECTS_REVISION, field_id=field_id, **expected))
        reloaded = load_revision0(generation, core, association, expected_field_id=field_id)
        if reloaded["revision_id"] != result["revision_id"]:
            raise TailObjectsRevisionCheckpointError("revision_id reload 不一致")
        payloads = dict((item["role"], item) for item in manifest["files"])
        return {
            "field_id": field_id, "checkpoint_root": str(checkpoint_root),
            "generation": str(generation), "checkpoint_manifest": manifest,
            "tail_objects_revision": reloaded,
            "tail_objects_revision_checkpoint_seconds": time.perf_counter() - started,
            "payload_bytes": {
                "tail_objects_revision": payloads["tail_objects_revision"]["byte_size"],
                "revision_geometry_labels": payloads["revision_geometry_labels"]["byte_size"],
            },
        }
    except Exception as error:
        raise TailObjectsRevisionCheckpointError(
            "field={} stage=tail_objects_revision revision_number=0 checkpoint_path={} cause={}".format(
                field_id, generation or checkpoint_root, error)) from error
