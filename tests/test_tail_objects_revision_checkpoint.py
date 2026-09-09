import json
import hashlib
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
import tifffile

from core.analysis_v2.association_checkpoint import write_and_verify_association_checkpoint
from core.analysis_v2.checkpoint_store import CheckpointStore, CheckpointValidationError
from core.analysis_v2.input_fingerprint import canonical_json_bytes
from core.analysis_v2.input_manifest_checkpoint import write_and_verify_input_manifest_checkpoint
from core.analysis_v2.tail_core_checkpoint import PROBABILITY_NAME, write_and_verify_tail_core_checkpoint
from core.analysis_v2.tail_objects_revision_checkpoint import (
    TailObjectsRevisionCheckpointError, write_and_verify_revision0_checkpoint,
)
from core.analysis_v2.tail_objects_revision import (
    GEOMETRY_LABEL_NAME, REVISION_JSON_NAME, build_revision0, load_revision0,
)
from test_tail_core_result import fixture_dir


ROOT = Path(__file__).resolve().parents[1]


def _upstreams(tmp_path):
    task = tmp_path / "run"; field = "001"
    source = tmp_path / "input.tif"
    tifffile.imwrite(str(source), np.zeros((2, 2), dtype=np.uint8))
    write_and_verify_input_manifest_checkpoint(task, {
        "field_id": field, "fitc_path": str(source), "tritc_path": str(source),
        "merge_path": str(source)}, "protein3")
    probability = task / "segmentation" / "c18b_runner_contract" / field / PROBABILITY_NAME
    probability.parent.mkdir(parents=True)
    tifffile.imwrite(str(probability), np.zeros((2, 2), dtype=np.uint16))
    core = write_and_verify_tail_core_checkpoint(
        task, ROOT, fixture_dir(tmp_path / "c18b"), field, "graph_preserving")
    adapter = task / "calibration" / "tail" / field
    adapter.mkdir(parents=True)
    (adapter / "global_results.json").write_bytes(canonical_json_bytes({"results": [{
        "head_id": 7, "status": "auto_confirmed_unique", "selected_candidate": {
            "selected_fragment_ids": [1], "source": "automatic", "matching_method": "overlap"}}]}))
    (adapter / "unassigned_tail_candidates.json").write_bytes(canonical_json_bytes({"candidates": []}))
    (adapter / "manifest.json").write_text(json.dumps({"matching": {
        "skipped_matches": [], "dilation_radius_px": 20, "maximum_distance_px": 80.0}}), encoding="utf-8")
    head = task / "calibration" / "head" / "001_HeadFinalLabels.tif"
    head.parent.mkdir(parents=True)
    tifffile.imwrite(str(head), np.array([[0, 7], [0, 0]], dtype=np.uint16))
    association = write_and_verify_association_checkpoint(task, ROOT, field, adapter, head, core)
    return task, field, core, association


def test_revision0_checkpoint_roundtrip_geometry_and_duplicate_rejection(tmp_path):
    task, field, core, association = _upstreams(tmp_path)
    written = write_and_verify_revision0_checkpoint(task, ROOT, field, core, association)
    generation = Path(written["generation"])
    manifest = CheckpointStore(Path(written["checkpoint_root"])).load_checkpoint(generation)
    result = written["tail_objects_revision"]
    assert manifest["metadata"]["revision_number"] == 0
    assert manifest["metadata"]["revision_id"] == result["revision_id"]
    labels = tifffile.imread(str(generation / "labels" / "tail_objects_revision_labels.tif"))
    source = tifffile.imread(str(Path(core["generation"]) / "labels" / "07_extreme_fragment_filtered_labels.tif"))
    assert np.array_equal(labels, source)
    assert {row["geometry_label_id"] for row in result["objects"]} == {1}
    with pytest.raises(TailObjectsRevisionCheckpointError, match="拒绝覆盖"):
        write_and_verify_revision0_checkpoint(task, ROOT, field, core, association)


@pytest.mark.parametrize("failure", ["json_written", "tiff_written", "before_marker"])
def test_revision0_checkpoint_failures_never_become_valid(tmp_path, failure):
    task, field, core, association = _upstreams(tmp_path)
    def inject(phase):
        if phase == failure:
            raise RuntimeError(failure)
    with pytest.raises(TailObjectsRevisionCheckpointError, match="stage=tail_objects_revision"):
        write_and_verify_revision0_checkpoint(task, ROOT, field, core, association, inject)
    root = task / "checkpoints" / "tail_objects" / field / "revisions" / "revision_0000"
    generation = root / "generations" / "tail_objects_revision_0000"
    if generation.exists():
        with pytest.raises(CheckpointValidationError):
            CheckpointStore(root).load_checkpoint(generation)


def test_revision0_checkpoint_tampered_payload_is_corrupt(tmp_path):
    task, field, core, association = _upstreams(tmp_path)
    written = write_and_verify_revision0_checkpoint(task, ROOT, field, core, association)
    path = Path(written["generation"]) / "tail_objects_revision.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(CheckpointValidationError, match="CORRUPT"):
        CheckpointStore(Path(written["checkpoint_root"])).load_checkpoint(written["generation"])


def test_rebuilt_legal_checkpoint_with_one_geometry_pixel_has_new_revision_id(tmp_path):
    task, field, core_checkpoint, association_checkpoint = _upstreams(tmp_path)
    original = write_and_verify_revision0_checkpoint(
        task, ROOT, field, core_checkpoint, association_checkpoint)
    generation = Path(original["generation"])
    core = __import__("core.analysis_v2.tail_core_result", fromlist=["load_tail_core_result"]).load_tail_core_result(
        Path(core_checkpoint["generation"]), expected_field_id=field)
    association = __import__("core.analysis_v2.association_result", fromlist=["load_association_result"]).load_association_result(
        Path(association_checkpoint["generation"]), [1], [7], expected_field_id=field,
        expected_fingerprints=association_checkpoint["association_result"]["fingerprints"])
    labels = tifffile.imread(str(generation / GEOMETRY_LABEL_NAME))
    changed = labels.copy(); changed[1, 0] = 1
    rebuilt = build_revision0(core, association, changed)
    assert rebuilt["objects"] == original["tail_objects_revision"]["objects"]
    assert rebuilt["revision_id"] != original["tail_objects_revision"]["revision_id"]

    buffer = BytesIO(); tifffile.imwrite(buffer, changed)
    label_bytes = buffer.getvalue()
    stored = dict(rebuilt); stored["geometry"] = dict(rebuilt["geometry"])
    stored["geometry"]["sha256"] = hashlib.sha256(label_bytes).hexdigest()
    manifest = CheckpointStore(Path(original["checkpoint_root"])).load_checkpoint(generation)
    rebuilt_store = CheckpointStore(tmp_path / "rebuilt_checkpoint")
    attempt = rebuilt_store.begin(
        "rebuilt_revision", manifest["stage"], field,
        manifest["input_fingerprint"], manifest["parameter_fingerprint"],
        manifest["producer_fingerprint"], metadata={"revision_id": rebuilt["revision_id"]})
    attempt.add_bytes("tail_objects_revision", REVISION_JSON_NAME, canonical_json_bytes(stored))
    attempt.add_bytes("revision_geometry_labels", GEOMETRY_LABEL_NAME, label_bytes)
    rebuilt_generation = attempt.commit()
    rebuilt_store.load_checkpoint(rebuilt_generation)
    assert load_revision0(rebuilt_generation, core, association)["revision_id"] == rebuilt["revision_id"]


@pytest.mark.parametrize("key", ["input_fingerprint", "parameter_fingerprint",
                                   "producer_fingerprint"])
def test_revision0_checkpoint_wrong_compatibility_identity_is_incompatible(tmp_path, key):
    task, field, core, association = _upstreams(tmp_path)
    written = write_and_verify_revision0_checkpoint(task, ROOT, field, core, association)
    with pytest.raises(CheckpointValidationError, match="INCOMPATIBLE"):
        CheckpointStore(Path(written["checkpoint_root"])).load_checkpoint(
            written["generation"], expected={key: "0" * 64})
