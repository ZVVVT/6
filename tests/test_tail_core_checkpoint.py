import hashlib
import json
from pathlib import Path

import pytest
import numpy as np
import tifffile

from core.analysis_v2.checkpoint_store import CheckpointConflictError, CheckpointStore
from core.analysis_v2.input_manifest_checkpoint import write_and_verify_input_manifest_checkpoint
from core.analysis_v2.tail_core_checkpoint import (
    TailCoreCheckpointError, write_and_verify_tail_core_checkpoint,
)
from core.analysis_v2.tail_core_result import load_tail_core_result
from test_tail_core_result import fixture_dir


def _project_root():
    return Path(__file__).resolve().parents[1]


def _prepare_input_checkpoint(tmp_path):
    task_root = tmp_path / "run"
    source = tmp_path / "input.tif"
    tifffile.imwrite(str(source), np.zeros((2, 2), dtype=np.uint8))
    field = {"field_id": "001", "fitc_path": str(source),
             "tritc_path": str(source), "merge_path": str(source)}
    write_and_verify_input_manifest_checkpoint(task_root, field, "protein3")
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
