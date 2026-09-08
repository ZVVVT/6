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
PROBABILITY_NAME = "02_probability_uint16.tif"
PROBABILITY_ROLE = "adapter_probability"
RECOVERY_REQUIRED_ROLES = (
    "baseline_06", "filtered_07", "tail_core_result", PROBABILITY_ROLE,
)


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


def _probability_source_path(task_root, field_id):
    return (Path(task_root) / "segmentation" / "c18b_runner_contract"
            / str(field_id) / PROBABILITY_NAME)


def expected_tail_core_fingerprints(task_root, project_root, field_id,
                                    candidate_path_mode):
    """Compute the current attempt's Tail Core compatibility contract.

    This deliberately reads the current run's input checkpoint and current
    producer resources.  Recovery callers must never derive ``expected`` from
    a source checkpoint's self-reported manifest.
    """
    input_manifest = _input_manifest(task_root, field_id)
    return {
        "input": stage_input_fingerprint(input_manifest, STAGE_TAIL_CORE),
        "parameter": parameter_fingerprint(
            STAGE_TAIL_CORE,
            _effective_parameters(project_root, candidate_path_mode),
        ),
        "producer": producer_fingerprint(
            STAGE_TAIL_CORE, ALGORITHM_VERSION,
            resources=_producer_resources(project_root),
        ),
    }


def write_and_verify_tail_core_checkpoint(task_root, project_root, c18b_dir,
                                           field_id, candidate_path_mode):
    """Build, commit, immediately reload, and strictly validate one generation."""
    started = time.perf_counter()
    field_id = str(field_id)
    checkpoint_root = Path(task_root) / "checkpoints" / "tail_core" / field_id
    generation = None
    try:
        fingerprints = expected_tail_core_fingerprints(
            task_root, project_root, field_id, candidate_path_mode)
        input_value = fingerprints["input"]
        parameter_value = fingerprints["parameter"]
        producer_value = fingerprints["producer"]
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
        probability = _probability_source_path(task_root, field_id)
        if not probability.is_file() or probability.stat().st_size <= 0:
            raise TailCoreCheckpointError(
                "TailCore recovery payload probability 不存在：{}".format(probability)
            )
        store = CheckpointStore(checkpoint_root)
        attempt = store.begin(CHECKPOINT_ID, STAGE_TAIL_CORE, field_id,
                              input_value, parameter_value, producer_value,
                              metadata={"origin": {"mode": "computed"}})
        attempt.add_file_payload(baseline, "labels/" + BASELINE_LABEL_NAME,
                                 "baseline_06")
        attempt.add_file_payload(filtered, "labels/" + FILTERED_LABEL_NAME,
                                 "filtered_07")
        attempt.add_file_payload(probability, "recovery/" + PROBABILITY_NAME,
                                 PROBABILITY_ROLE)
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
                                  "filtered_07": payloads["filtered_07"]["byte_size"],
                                  PROBABILITY_ROLE: payloads[PROBABILITY_ROLE]["byte_size"]}}
    except Exception as error:
        raise TailCoreCheckpointError(
            "field={} stage=tail_core checkpoint_path={} cause={}".format(
                field_id, generation or checkpoint_root, error
            )
        ) from error


def recover_and_verify_tail_core_checkpoint(task_root, project_root, field_id,
                                            candidate_path_mode,
                                            source_generation):
    """Strictly validate and clone one explicit TailCoreResult generation.

    The resulting generation is owned by ``task_root``.  Its business payload
    is copied byte-for-byte; only non-business checkpoint metadata records the
    recovery origin.
    """
    started = time.perf_counter()
    field_id = str(field_id)
    source = Path(source_generation).resolve()
    checkpoint_root = Path(task_root) / "checkpoints" / "tail_core" / field_id
    generation = None
    try:
        if source.parent.name != "generations":
            raise TailCoreCheckpointError(
                "recovery source 必须是明确的 generations/<generation> 路径：{}".format(source)
            )
        fingerprints = expected_tail_core_fingerprints(
            task_root, project_root, field_id, candidate_path_mode)
        expected = {
            "stage": STAGE_TAIL_CORE,
            "field_id": field_id,
            "input_fingerprint": fingerprints["input"],
            "parameter_fingerprint": fingerprints["parameter"],
            "producer_fingerprint": fingerprints["producer"],
        }
        source_store = CheckpointStore(source.parent.parent)
        source_manifest = source_store.load_checkpoint(source, expected=expected)
        result = load_tail_core_result(
            source, expected_field_id=field_id,
            expected_fingerprints=fingerprints,
        )
        source_files = dict((item["role"], item) for item in source_manifest["files"])
        required_roles = RECOVERY_REQUIRED_ROLES
        if set(required_roles) - set(source_files):
            raise TailCoreCheckpointError("recovery source TailCoreResult payload 不完整")
        store = CheckpointStore(checkpoint_root)
        source_manifest_sha256 = sha256_file(source / "manifest.json")
        attempt = store.begin(
            CHECKPOINT_ID, STAGE_TAIL_CORE, field_id,
            fingerprints["input"], fingerprints["parameter"], fingerprints["producer"],
            metadata={"origin": {"mode": "reused",
                                 "source_manifest_sha256": source_manifest_sha256}},
        )
        for role in required_roles:
            item = source_files[role]
            attempt.add_file_payload(source / item["relative_path"],
                                     item["relative_path"], role)
        generation = attempt.commit()
        manifest = store.load_checkpoint(generation, expected=expected)
        reloaded = load_tail_core_result(
            generation, expected_field_id=field_id,
            expected_fingerprints=fingerprints,
        )
        if canonical_json_bytes(reloaded) != canonical_json_bytes(result):
            raise TailCoreCheckpointError("reused TailCoreResult round-trip 不一致")
        payloads = dict((item["role"], item) for item in manifest["files"])
        return {
            "field_id": field_id,
            "checkpoint_root": str(checkpoint_root),
            "generation": str(generation),
            "checkpoint_manifest": manifest,
            "tail_core_result": reloaded,
            "tail_core_checkpoint_seconds": time.perf_counter() - started,
            "tail_core_mode": "reused",
            "tail_core_recovery_validation_seconds": time.perf_counter() - started,
                              "payload_bytes": {"baseline_06": payloads["baseline_06"]["byte_size"],
                              "filtered_07": payloads["filtered_07"]["byte_size"],
                              PROBABILITY_ROLE: payloads[PROBABILITY_ROLE]["byte_size"]},
        }
    except Exception as error:
        raise TailCoreCheckpointError(
            "field={} stage=tail_core recovery_source={} cause={}".format(
                field_id, source, error
            )
        ) from error
