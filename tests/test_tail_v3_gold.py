from pathlib import Path

import pytest

from core.analysis_v2 import c18b_execution
from tools.analysis_v2.tail_v3_gold import (
    GoldMismatch,
    TIER1_RUNS,
    _canonical_json,
    compare_runs,
    verify_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "workspace" / "benchmarks" / "tail_v3_gold" / "gold_manifest.json"


@pytest.mark.parametrize("field_id,case_no,run_id", TIER1_RUNS)
def test_tier1_gold_comparator_accepts_frozen_run(field_id, case_no, run_id):
    run_root = (
        ROOT / "workspace" / "cases" / case_no / "analysis_v2"
        / "protein3" / "runs" / run_id
    )
    if not run_root.is_dir():
        pytest.skip("local Tier1 formal run unavailable")
    assert compare_runs(run_root, run_root, field_id) == {
        "field_id": field_id,
        "equal": True,
    }


def test_tier1_manifest_verifies_318_objects():
    if not MANIFEST.is_file():
        pytest.skip("local Tier1 Gold manifest unavailable")
    assert verify_manifest(ROOT, MANIFEST) == {
        "field_count": 4,
        "object_count": 318,
    }


def test_json_semantics_ignore_only_declared_metadata():
    baseline = {
        "tail_object_count": 1,
        "region_label_path": "C:/old/run/labels.tif",
        "objects": [{"tail_object_id": 1, "pixel_count": 10, "source": "auto"}],
    }
    candidate = {
        "tail_object_count": 1,
        "region_label_path": "D:/new/run/labels.tif",
        "objects": [{"tail_object_id": 1, "pixel_count": 10, "source": "auto"}],
    }
    assert _canonical_json(candidate) == _canonical_json(baseline)
    candidate["objects"][0]["pixel_count"] = 11
    with pytest.raises(GoldMismatch):
        if _canonical_json(candidate) != _canonical_json(baseline):
            raise GoldMismatch("business field changed")


def test_c18b_phase_timings_are_wall_clock_and_reconcile(monkeypatch):
    execution = c18b_execution.C18BExecution.__new__(
        c18b_execution.C18BExecution
    )
    execution._log = lambda message: None
    execution._check_cancelled = lambda: None
    execution.task_root = Path("task")
    execution._ensure_c18b_result = lambda field, handle: Path("labels.tif")

    def prepare(field, labels, handle):
        execution._add_phase_timing("fragment_filter", 0.5)
        execution._add_phase_timing("association_editor_adapter", 0.75)
        return {"field_id": field}

    execution._prepare_c18b_editor_payload = prepare
    clock = iter((1.0, 3.0, 5.0))
    monkeypatch.setattr(c18b_execution.time, "perf_counter", lambda: next(clock))
    result = execution._run_c18b_workflow(["field"], None, 0.0)
    assert result["elapsed_seconds"] == 5.0
    assert result["phase_timings_seconds"] == {
        "tail_core": 2.0,
        "fragment_filter": 0.5,
        "association_editor_adapter": 0.75,
        "c18b_orchestration_overhead": 1.75,
    }
