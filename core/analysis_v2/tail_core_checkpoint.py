"""Formal immutable TailCoreResult checkpoints for Tail V3 Phase 3B."""

import json
import time
from pathlib import Path

from .checkpoint_store import CheckpointStore
from .environment_snapshot import sha256_file
from .input_fingerprint import (
    STAGE_TAIL_CORE, canonical_json_bytes, parameter_fingerprint,
    producer_fingerprint, stage_input_fingerprint, validate_input_manifest,
)
from .tail_core_result import (
    BASELINE_LABEL_NAME, FILTERED_LABEL_NAME, RESULT_JSON_NAME,
    build_tail_core_result, load_tail_core_result,
)


CHECKPOINT_ID = "tail_core_result"
ALGORITHM_VERSION = "tail-v3-c18b-tail-core-checkpoint-v1"


class TailCoreCheckpointError(RuntimeError):
    """A Tail Core checkpoint could not be written or reloaded safely."""


def _input_manifest(task_root, field_id):
    path = (Path(task_root) / "checkpoints" / "input" / str(field_id)
            / "generations" / "input_manifest" / "input_manifest.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        validate_input_manifest(payload)
    except Exception as error:
        raise TailCoreCheckpointError(
            "field={} stage=tail_core input manifest={} cause={}".format(
                field_id, path, error
            )
        ) from error
    return payload


def _effective_parameters(project_root, candidate_path_mode):
    config_path = (Path(project_root) / "tools" / "analysis_v2" / "c18b_score015"
                   / "config" / "frozen_parameters.json")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise TailCoreCheckpointError(
            "无法读取 C18B frozen parameters：{}".format(error)
        ) from error
    if not isinstance(config, dict):
        raise TailCoreCheckpointError("C18B frozen parameters 顶层必须是对象")
    return {"c18b_frozen_parameters": config,
            "candidate_path_mode": str(candidate_path_mode)}


def _producer_resources(project_root):
    root = Path(project_root)
    c18b = root / "tools" / "analysis_v2" / "c18b_score015"
    paths = (
        root / "tools" / "analysis_v2" / "c18b_score015_adapter.py",
        c18b / "run_pipeline.py", c18b / "fitc_processing.py",
        c18b / "candidate_scoring.py", c18b / "candidate_validation.py",
        c18b / "candidate_merging.py", c18b / "graph_seeded_region_growing.py",
        c18b / "identity_graph_v3.py",
        c18b / "graph_constrained_instance_separation.py",
        c18b / "extreme_fragment_filter.py", Path(__file__).with_name("tail_core_result.py"),
    )
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise TailCoreCheckpointError("Tail Core producer resource 缺失：{}".format(
            "; ".join(missing)))
    return dict((str(path.relative_to(root)).replace("\\", "/"), sha256_file(path))
                for path in paths)


def write_and_verify_tail_core_checkpoint(task_root, project_root, c18b_dir,
                                           field_id, candidate_path_mode):
    """Build, commit, immediately reload, and strictly validate one generation."""
    started = time.perf_counter()
    field_id = str(field_id)
    checkpoint_root = Path(task_root) / "checkpoints" / "tail_core" / field_id
    generation = None
    try:
        input_manifest = _input_manifest(task_root, field_id)
        input_value = stage_input_fingerprint(input_manifest, STAGE_TAIL_CORE)
        parameter_value = parameter_fingerprint(
            STAGE_TAIL_CORE, _effective_parameters(project_root, candidate_path_mode))
        producer_value = producer_fingerprint(
            STAGE_TAIL_CORE, ALGORITHM_VERSION, resources=_producer_resources(project_root))
        result = build_tail_core_result(
            c18b_dir, field_id, input_value, parameter_value, producer_value)
        # Phase 3A records are business data; only their generation-local
        # relative locations change after byte-for-byte payload copying.
        result["labels"]["baseline_06"]["relative_path"] = (
            "labels/" + BASELINE_LABEL_NAME)
        result["labels"]["filtered_07"]["relative_path"] = (
            "labels/" + FILTERED_LABEL_NAME)
        source_root = Path(c18b_dir)
        baseline = source_root / BASELINE_LABEL_NAME
        filtered = source_root / FILTERED_LABEL_NAME
        store = CheckpointStore(checkpoint_root)
        attempt = store.begin(CHECKPOINT_ID, STAGE_TAIL_CORE, field_id,
                              input_value, parameter_value, producer_value)
        attempt.add_file_payload(baseline, "labels/" + BASELINE_LABEL_NAME,
                                 "baseline_06")
        attempt.add_file_payload(filtered, "labels/" + FILTERED_LABEL_NAME,
                                 "filtered_07")
        attempt.add_bytes("tail_core_result", RESULT_JSON_NAME,
                          canonical_json_bytes(result))
        generation = attempt.commit()
        expected = {"stage": STAGE_TAIL_CORE, "field_id": field_id,
                    "input_fingerprint": input_value,
                    "parameter_fingerprint": parameter_value,
                    "producer_fingerprint": producer_value}
        manifest = store.load_checkpoint(generation, expected=expected)
        reloaded = load_tail_core_result(
            generation, expected_field_id=field_id,
            expected_fingerprints={"input": input_value, "parameter": parameter_value,
                                   "producer": producer_value})
        if canonical_json_bytes(reloaded) != canonical_json_bytes(result):
            raise TailCoreCheckpointError("TailCoreResult round-trip 不一致")
        payloads = dict((item["role"], item) for item in manifest["files"])
        return {"field_id": field_id, "checkpoint_root": str(checkpoint_root),
                "generation": str(generation), "checkpoint_manifest": manifest,
                "tail_core_result": reloaded,
                "tail_core_checkpoint_seconds": time.perf_counter() - started,
                "payload_bytes": {"baseline_06": payloads["baseline_06"]["byte_size"],
                                  "filtered_07": payloads["filtered_07"]["byte_size"]}}
    except Exception as error:
        raise TailCoreCheckpointError(
            "field={} stage=tail_core checkpoint_path={} cause={}".format(
                field_id, generation or checkpoint_root, error
            )
        ) from error
