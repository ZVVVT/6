import hashlib
import json
import shutil
from pathlib import Path

import pytest
import numpy as np
import tifffile

from core.analysis_v2.checkpoint_store import CheckpointConflictError, CheckpointStore
from core.analysis_v2.c18b_execution import C18BExecution
from core.analysis_v2.input_manifest_checkpoint import write_and_verify_input_manifest_checkpoint
from core.analysis_v2.tail_core_checkpoint import (
    PROBABILITY_NAME, PROBABILITY_ROLE,
    TailCoreCheckpointError, recover_and_verify_tail_core_checkpoint,
    write_and_verify_tail_core_checkpoint,
)
from core.analysis_v2.tail_core_result import load_tail_core_result
from test_tail_core_result import fixture_dir


def _project_root():
    return Path(__file__).resolve().parents[1]


def _prepare_input_checkpoint(tmp_path, pixel=0, field_id="001"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    task_root = tmp_path / "run"
    source = tmp_path / "input.tif"
    tifffile.imwrite(str(source), np.full((2, 2), pixel, dtype=np.uint8))
    field = {"field_id": field_id, "fitc_path": str(source),
             "tritc_path": str(source), "merge_path": str(source)}
    write_and_verify_input_manifest_checkpoint(task_root, field, "protein3")
    probability = (task_root / "segmentation" / "c18b_runner_contract" / field_id
                   / PROBABILITY_NAME)
    probability.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(probability), np.full((2, 2), pixel, dtype=np.uint16))
    return task_root


def test_tail_core_checkpoint_is_immutable_byte_exact_and_round_trips(tmp_path):
    task_root = _prepare_input_checkpoint(tmp_path)
    c18b = fixture_dir(tmp_path / "c18b")
    written = write_and_verify_tail_core_checkpoint(
        task_root, _project_root(), c18b, "001", "graph_preserving")
    generation = Path(written["generation"])
    store = CheckpointStore(Path(written["checkpoint_root"]))
    manifest = store.load_checkpoint(generation, expected={
        "stage": "tail_core", "field_id": "001",
        "input_fingerprint": written["checkpoint_manifest"]["input_fingerprint"],
        "parameter_fingerprint": written["checkpoint_manifest"]["parameter_fingerprint"],
        "producer_fingerprint": written["checkpoint_manifest"]["producer_fingerprint"],
    })
    assert (generation / "completion.json").is_file()
    files = dict((item["role"], item) for item in manifest["files"])
    for role, name in (("baseline_06", "06_final_tail_instances.tif"),
                       ("filtered_07", "07_extreme_fragment_filtered_labels.tif")):
        source = c18b / name
        copied = generation / "labels" / name
        assert hashlib.sha256(source.read_bytes()).hexdigest() == hashlib.sha256(copied.read_bytes()).hexdigest()
        assert files[role]["byte_size"] == copied.stat().st_size
    probability = task_root / "segmentation" / "c18b_runner_contract" / "001" / PROBABILITY_NAME
    copied_probability = generation / "recovery" / PROBABILITY_NAME
    assert hashlib.sha256(probability.read_bytes()).hexdigest() == hashlib.sha256(copied_probability.read_bytes()).hexdigest()
    assert files[PROBABILITY_ROLE]["byte_size"] == copied_probability.stat().st_size
    result = load_tail_core_result(generation, "001", {
        "input": manifest["input_fingerprint"], "parameter": manifest["parameter_fingerprint"],
        "producer": manifest["producer_fingerprint"],
    })
    assert result == written["tail_core_result"]
    assert not any(item["role"] in ("FITC", "TRITC", "Merge") for item in manifest["files"])
    with pytest.raises(TailCoreCheckpointError, match="拒绝覆盖"):
        write_and_verify_tail_core_checkpoint(
            task_root, _project_root(), c18b, "001", "graph_preserving")


def test_tail_core_checkpoint_tamper_is_corrupt(tmp_path):
    task_root = _prepare_input_checkpoint(tmp_path)
    c18b = fixture_dir(tmp_path / "c18b")
    written = write_and_verify_tail_core_checkpoint(
        task_root, _project_root(), c18b, "001", "graph_preserving")
    generation = Path(written["generation"])
    (generation / "labels" / "07_extreme_fragment_filtered_labels.tif").write_bytes(b"tampered")
    with pytest.raises(Exception, match="CORRUPT"):
        CheckpointStore(Path(written["checkpoint_root"])).load_checkpoint(generation)


def test_explicit_recovery_clones_valid_source_into_current_run(tmp_path):
    source_task = _prepare_input_checkpoint(tmp_path / "source")
    c18b = fixture_dir(tmp_path / "c18b")
    source = write_and_verify_tail_core_checkpoint(
        source_task, _project_root(), c18b, "001", "graph_preserving")
    current_task = _prepare_input_checkpoint(tmp_path / "current")
    recovered = recover_and_verify_tail_core_checkpoint(
        current_task, _project_root(), "001", "graph_preserving", source["generation"])
    generation = Path(recovered["generation"])
    manifest = CheckpointStore(Path(recovered["checkpoint_root"])).load_checkpoint(generation)
    assert manifest["metadata"]["origin"]["mode"] == "reused"
    assert set(item["role"] for item in manifest["files"]) == {
        "baseline_06", "filtered_07", "tail_core_result", PROBABILITY_ROLE}
    for name in ("06_final_tail_instances.tif", "07_extreme_fragment_filtered_labels.tif"):
        assert hashlib.sha256((Path(source["generation"]) / "labels" / name).read_bytes()).hexdigest() == hashlib.sha256((generation / "labels" / name).read_bytes()).hexdigest()
    assert hashlib.sha256((Path(source["generation"]) / "recovery" / PROBABILITY_NAME).read_bytes()).hexdigest() == hashlib.sha256((generation / "recovery" / PROBABILITY_NAME).read_bytes()).hexdigest()


@pytest.mark.parametrize("change", ["fitc", "parameter", "wrong_field"])
def test_explicit_recovery_rejects_incompatible_source(tmp_path, change):
    source_task = _prepare_input_checkpoint(tmp_path / "source")
    c18b = fixture_dir(tmp_path / "c18b")
    source = write_and_verify_tail_core_checkpoint(
        source_task, _project_root(), c18b, "001", "graph_preserving")
    current_task = _prepare_input_checkpoint(
        tmp_path / "current", pixel=1 if change == "fitc" else 0,
        field_id="other" if change == "wrong_field" else "001")
    field = "001"
    mode = "graph_preserving"
    if change == "fitc":
        pass
    elif change == "parameter":
        mode = "ordered"
    else:
        field = "other"
    with pytest.raises(TailCoreCheckpointError, match="(INCOMPATIBLE|field_id)"):
        recover_and_verify_tail_core_checkpoint(
            current_task, _project_root(), field, mode, source["generation"])


@pytest.mark.parametrize("relative_path", [
    "labels/07_extreme_fragment_filtered_labels.tif",
    "recovery/" + PROBABILITY_NAME,
])
def test_explicit_recovery_rejects_tampered_or_incomplete_source(tmp_path, relative_path):
    source_task = _prepare_input_checkpoint(tmp_path / "source")
    c18b = fixture_dir(tmp_path / "c18b")
    source = write_and_verify_tail_core_checkpoint(
        source_task, _project_root(), c18b, "001", "graph_preserving")
    generation = Path(source["generation"])
    (generation / relative_path).write_bytes(b"tampered")
    current_task = _prepare_input_checkpoint(tmp_path / "current")
    with pytest.raises(TailCoreCheckpointError, match="CORRUPT"):
        recover_and_verify_tail_core_checkpoint(
            current_task, _project_root(), "001", "graph_preserving", generation)


def test_tail_core_result_loads_without_recovery_payload_but_recovery_rejects(tmp_path):
    source_task = _prepare_input_checkpoint(tmp_path / "source")
    c18b = fixture_dir(tmp_path / "c18b")
    source = write_and_verify_tail_core_checkpoint(
        source_task, _project_root(), c18b, "001", "graph_preserving")
    generation = Path(source["generation"])
    (generation / "recovery" / PROBABILITY_NAME).unlink()
    assert load_tail_core_result(generation, "001", {
        "input": source["checkpoint_manifest"]["input_fingerprint"],
        "parameter": source["checkpoint_manifest"]["parameter_fingerprint"],
        "producer": source["checkpoint_manifest"]["producer_fingerprint"],
    }) == source["tail_core_result"]
    current_task = _prepare_input_checkpoint(tmp_path / "current")
    with pytest.raises(TailCoreCheckpointError, match="CORRUPT"):
        recover_and_verify_tail_core_checkpoint(
            current_task, _project_root(), "001", "graph_preserving", generation)


def test_recovery_materializes_probability_from_current_checkpoint_only(tmp_path):
    source_task = _prepare_input_checkpoint(tmp_path / "source")
    c18b = fixture_dir(tmp_path / "c18b")
    source = write_and_verify_tail_core_checkpoint(
        source_task, _project_root(), c18b, "001", "graph_preserving")
    current_task = _prepare_input_checkpoint(tmp_path / "current")
    recovered = recover_and_verify_tail_core_checkpoint(
        current_task, _project_root(), "001", "graph_preserving", source["generation"])
    fitc = current_task / "input" / "001_FITC.tif"
    fitc.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(fitc), np.zeros((2, 2), dtype=np.uint8))
    execution = C18BExecution(
        _project_root(), current_task, Path(__file__).resolve(),
        tail_core_recovery_sources={"001": {"checkpoint_generation_path": source["generation"]}},
    )
    assert execution._recovery_source_for_field("001") == {
        "generation": Path(source["generation"])}
    source_generation = Path(source["generation"])
    # Current generation was committed before materialization; old source is not
    # consulted by the adapter working-copy path.
    shutil.rmtree(str(source_generation))
    instances, _ = execution._materialize_recovered_editor_inputs(
        "001", recovered, {"generation": source_generation})
    assert instances.is_file()
    working_probability = (current_task / "segmentation" / "c18b_runner_contract"
                           / "001" / PROBABILITY_NAME)
    current_payload = Path(recovered["generation"]) / "recovery" / PROBABILITY_NAME
    assert hashlib.sha256(working_probability.read_bytes()).hexdigest() == hashlib.sha256(current_payload.read_bytes()).hexdigest()
