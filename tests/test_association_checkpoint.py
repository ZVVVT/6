import copy
import json

import numpy as np
import pytest
import tifffile

from core.analysis_v2.association_checkpoint import (
    AssociationCheckpointError, write_and_verify_association_checkpoint,
)
from core.analysis_v2.checkpoint_store import CheckpointStore, CheckpointValidationError
from core.analysis_v2.input_fingerprint import canonical_json_bytes


def _fixture(tmp_path):
    project = tmp_path / "project"
    adapter_source = project / "tools" / "analysis_v2" / "c18b_tail_editor_adapter.py"
    adapter_source.parent.mkdir(parents=True)
    adapter_source.write_text("# producer fixture\n", encoding="utf-8")
    task = tmp_path / "run"
    adapter = task / "calibration" / "tail" / "field"
    adapter.mkdir(parents=True)
    (adapter / "global_results.json").write_bytes(canonical_json_bytes({"results": [{
        "head_id": 7, "status": "auto_confirmed_unique", "selected_candidate": {
            "selected_fragment_ids": [11], "source": "automatic", "matching_method": "overlap"}}]}))
    (adapter / "unassigned_tail_candidates.json").write_bytes(canonical_json_bytes({"candidates": [{
        "fragment_label_id": 22, "association_failure_reason": "no_head"}]}))
    (adapter / "manifest.json").write_text(json.dumps({"matching": {
        "skipped_matches": [], "dilation_radius_px": 20,
        "maximum_distance_px": 80.0}}), encoding="utf-8")
    head = task / "calibration" / "head" / "field_HeadFinalLabels.tif"
    head.parent.mkdir(parents=True)
    tifffile.imwrite(str(head), np.array([[0, 7], [9, 0]], dtype=np.uint16))
    generation = task / "checkpoints" / "tail_core" / "field" / "generations" / "tail_core_result"
    generation.mkdir(parents=True)
    (generation / "manifest.json").write_bytes(canonical_json_bytes({"fixture": "tail-core"}))
    core = {"generation": str(generation), "tail_core_result": {"labels": {
        "filtered_07": {"positive_ids": [11, 22]}}}}
    return task, project, adapter, head, core


def test_association_checkpoint_create_marker_reload_and_single_generation(tmp_path):
    task, project, adapter, head, core = _fixture(tmp_path)
    written = write_and_verify_association_checkpoint(task, project, "field", adapter, head, core)
    generation = written["generation"]
    assert (written["association_result"]["summary"] ==
            {"tail_count": 2, "associated_count": 1, "unresolved_count": 1})
    assert (CheckpointStore(task / "checkpoints" / "association" / "field")
            .load_checkpoint(generation)["checkpoint_id"] == "association_result")
    assert (tmp_path / "run" / "checkpoints" / "association" / "field" / "generations"
            / "association_result" / "completion.json").is_file()
    with pytest.raises(AssociationCheckpointError, match="拒绝覆盖"):
        write_and_verify_association_checkpoint(task, project, "field", adapter, head, core)


def test_association_checkpoint_tamper_is_corrupt(tmp_path):
    task, project, adapter, head, core = _fixture(tmp_path)
    written = write_and_verify_association_checkpoint(task, project, "field", adapter, head, core)
    path = (tmp_path / "run" / "checkpoints" / "association" / "field" / "generations"
            / "association_result" / "association_result.json")
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(CheckpointValidationError, match="CORRUPT"):
        CheckpointStore(task / "checkpoints" / "association" / "field").load_checkpoint(written["generation"])


@pytest.mark.parametrize("mutation, message", [
    ("unknown_head", "associated_head_id"),
    ("unknown_tail", "TailCore"),
    ("duplicate_head", "一个 head"),
])
def test_association_checkpoint_rejects_invalid_automatic_output(tmp_path, mutation, message):
    task, project, adapter, head, core = _fixture(tmp_path)
    global_path = adapter / "global_results.json"
    value = json.loads(global_path.read_text(encoding="utf-8"))
    if mutation == "unknown_head":
        value["results"][0]["head_id"] = 99
    elif mutation == "unknown_tail":
        value["results"][0]["selected_candidate"]["selected_fragment_ids"] = [99]
    elif mutation == "duplicate_head":
        value["results"].append(copy.deepcopy(value["results"][0]))
        value["results"][1]["selected_candidate"]["selected_fragment_ids"] = [22]
    global_path.write_bytes(canonical_json_bytes(value))
    with pytest.raises(AssociationCheckpointError, match=message):
        write_and_verify_association_checkpoint(task, project, "field", adapter, head, core)
