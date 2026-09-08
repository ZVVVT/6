"""Immutable AssociationResult checkpoints for Tail V3 Phase 4B.

This module records the already-computed adapter decision.  It deliberately
does not recover, reuse, or materialize Association data for any consumer.
"""

import json
import time
from pathlib import Path

from .association_result import (
    RESULT_JSON_NAME, AssociationResultError, adapter_association_output,
    association_parameter_fingerprint, association_producer_fingerprint,
    build_association_result, head_artifact_identity, load_association_result,
)
from .checkpoint_store import CheckpointStore
from .environment_snapshot import sha256_file
from .input_fingerprint import STAGE_ASSOCIATION, canonical_json_bytes, fingerprint


CHECKPOINT_ID = "association_result"
ALGORITHM_VERSION = "tail-v3-association-checkpoint-v1"


class AssociationCheckpointError(RuntimeError):
    """Association checkpoint writing or reloading failed."""


def _tail_ids(tail_core_checkpoint):
    result = dict(tail_core_checkpoint.get("tail_core_result") or {})
    try:
        values = result["labels"]["filtered_07"]["positive_ids"]
        return sorted(int(value) for value in values)
    except (KeyError, TypeError, ValueError) as error:
        raise AssociationCheckpointError("TailCoreResult 缺少 07 surviving IDs：{}".format(error))


def _expected_fingerprints(project_root, head_labels_path, tail_core_checkpoint,
                           dilation_radius, maximum_distance):
    head = head_artifact_identity(head_labels_path)
    manifest_path = Path(tail_core_checkpoint["generation"]) / "manifest.json"
    if not manifest_path.is_file():
        raise AssociationCheckpointError("TailCore immutable manifest 不存在：{}".format(manifest_path))
    tail_core = sha256_file(manifest_path)
    parameter = association_parameter_fingerprint(dilation_radius, maximum_distance)
    producer = association_producer_fingerprint(
        Path(project_root) / "tools" / "analysis_v2" / "c18b_tail_editor_adapter.py")
    result = {"head_final_labels": head["fingerprint"], "tail_core_result": tail_core,
              "parameter": parameter, "producer": producer}
    # CheckpointStore has three generic slots.  The stage input identity binds
    # both upstream artifacts; AssociationResult retains all four explicitly.
    store_expected = {
        "input_fingerprint": fingerprint({"stage": STAGE_ASSOCIATION,
                                            "head_artifact_fingerprint": result["head_final_labels"],
                                            "tail_core_artifact_fingerprint": result["tail_core_result"]}),
        "parameter_fingerprint": parameter,
        "producer_fingerprint": producer,
    }
    return result, store_expected, sorted(int(value) for value in head["identity"]["positive_ids"])


def _adapter_effective_parameters(adapter_dir):
    """Read the values actually used by this adapter invocation."""
    path = Path(adapter_dir) / "manifest.json"
    try:
        matching = json.loads(path.read_text(encoding="utf-8")).get("matching") or {}
        return int(matching["dilation_radius_px"]), float(matching["maximum_distance_px"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise AssociationCheckpointError("adapter matching parameters 不可用：{}".format(error))


def write_and_verify_association_checkpoint(task_root, project_root, field_id,
                                            adapter_dir, head_labels_path,
                                            tail_core_checkpoint,
                                            dilation_radius=20,
                                            maximum_distance=80.0):
    """Build from adapter output, commit one immutable generation, reload it."""
    started = time.perf_counter()
    field_id = str(field_id)
    checkpoint_root = Path(task_root) / "checkpoints" / "association" / field_id
    generation = None
    try:
        tail_ids = _tail_ids(tail_core_checkpoint)
        dilation_radius, maximum_distance = _adapter_effective_parameters(adapter_dir)
        result_fingerprints, expected, head_ids = _expected_fingerprints(
            project_root, head_labels_path, tail_core_checkpoint,
            dilation_radius, maximum_distance)
        result = build_association_result(
            field_id, tail_ids, head_ids,
            adapter_association_output(adapter_dir, tail_ids), result_fingerprints)
        store = CheckpointStore(checkpoint_root)
        attempt = store.begin(
            CHECKPOINT_ID, STAGE_ASSOCIATION, field_id,
            expected["input_fingerprint"], expected["parameter_fingerprint"],
            expected["producer_fingerprint"], metadata={"origin": {"mode": "computed"},
                                                         "algorithm_version": ALGORITHM_VERSION})
        attempt.add_bytes("association_result", RESULT_JSON_NAME,
                          canonical_json_bytes(result))
        generation = attempt.commit()
        manifest = store.load_checkpoint(generation, expected={
            "stage": STAGE_ASSOCIATION, "field_id": field_id,
            "input_fingerprint": expected["input_fingerprint"],
            "parameter_fingerprint": expected["parameter_fingerprint"],
            "producer_fingerprint": expected["producer_fingerprint"],
        })
        reloaded = load_association_result(
            generation, tail_ids, head_ids, expected_field_id=field_id,
            expected_fingerprints=result_fingerprints)
        if canonical_json_bytes(reloaded) != canonical_json_bytes(result):
            raise AssociationCheckpointError("AssociationResult round-trip 不一致")
        payloads = dict((item["role"], item) for item in manifest["files"])
        return {"field_id": field_id, "checkpoint_root": str(checkpoint_root),
                "generation": str(generation), "checkpoint_manifest": manifest,
                "association_result": reloaded,
                "association_checkpoint_seconds": time.perf_counter() - started,
                "payload_bytes": {"association_result": payloads["association_result"]["byte_size"]}}
    except Exception as error:
        raise AssociationCheckpointError(
            "field={} stage=association checkpoint_path={} cause={}".format(
                field_id, generation or checkpoint_root, error)) from error
