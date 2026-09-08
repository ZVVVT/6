"""Automatic orchestration boundaries; real completion builder is never mocked."""

import builtins
import inspect
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from PIL import Image

from core.analysis_v2 import task_runner as tasks
from core.analysis_v2.c18b_execution import C18BExecution
from core.analysis_v2.checkpoint_store import CheckpointStore
from core.analysis_v2.environment_snapshot import sha256_file
from core.analysis_v2.input_fingerprint import stage_input_projection
from core.analysis_v2.input_manifest_checkpoint import (
    InputManifestCheckpointError, write_and_verify_input_manifest_checkpoint,
)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    calls = []
    config = SimpleNamespace(
        app_root=tmp_path,
        get_workspace_root=lambda: tmp_path / "cases",
        get_source_project_dir=lambda: tmp_path,
        get_python_exe=lambda: sys.executable,
        get_plugins_directory=lambda: tmp_path,
        get_protein_display_name=lambda key: key,
    )
    image = tmp_path / "image.tif"
    Image.new("L", (3, 2)).save(str(image), format="TIFF")
    fields = [{"field_no": "001", "R": str(image), "G": str(image), "Merge": str(image)}]
    request = tasks.AnalysisV2TaskRequest(
        "case1", "protein1", fields, protein_part="head", case_id=7,
    )

    def segmentation(**kwargs):
        calls.append("head_segmentation")
        kwargs["paths"].create_directories()

    class Head:
        def __init__(self, task_root, interactive=True):
            assert interactive is False

        def complete(self, process_context=None):
            calls.append("head_calibration")

    class C18B:
        field_id = "001"

        def __init__(self, *args):
            assert args[3] == "graph_preserving"

        def run(self):
            calls.append("c18b")
            return {"fields": [{"field_id": "001", "output_dir": str(tmp_path / "tail"),
                                "head_labels": str(image), "fragments": str(image)}]}

    class Measurement:
        def __init__(self, **kwargs):
            self.part = "tail" if "tail" in kwargs["pipeline"].name else "head"
            self.output_dir = kwargs["task_root"] / "measurement" / self.part / "output"
            self.result_path = self.output_dir.parent / "result.json"
            self.measurement_manifest_path = self.output_dir.parent / "manifest.json"

        def run(self, process_context=None):
            calls.append(self.part + "_measurement")
            summary = {"success": True, "calculation_mode": "head_equivalent",
                       "total": {"positive_count": 68}}
            return {"parsed_result": summary,
                    "validation": {"result_parser": summary, "tail_object_count": 89}}

    monkeypatch.setattr(tasks, "run_head_segmentation", segmentation)
    monkeypatch.setattr(tasks, "HeadCalibrationService", Head)
    monkeypatch.setattr(tasks, "C18BExecution", C18B)
    monkeypatch.setattr(tasks, "HeadMeasurementService", Measurement)
    monkeypatch.setattr(tasks, "TailMeasurementService", Measurement)

    def workset(*args):
        calls.append("workset")

    def contract(*args):
        calls.append("contract")
        return {"tail_object_count": 89, "associated_object_count": 68, "unresolved_object_count": 21}

    def register(payload, contract):
        calls.append("register")
        return dict(payload, **contract)

    def complete(*args, **kwargs):
        assert kwargs == {"automatic": True}
        calls.append("tail_calibration")

    monkeypatch.setattr(tasks, "save_initial_c18b_tail_workset", workset)
    monkeypatch.setattr(tasks, "build_automatic_tail_final_contract", contract)
    monkeypatch.setattr(tasks, "register_tail_final_contract", register)
    monkeypatch.setattr(tasks, "complete_tail_calibration", complete)
    return SimpleNamespace(config=config, request=request, fields=fields, calls=calls,
                           runner=tasks.AnalysisV2TaskRunner(config), measurement=Measurement)


@pytest.mark.parametrize("key", ["protein1", "protein2", "protein4", "protein5"])
def test_head_only_synchronous(harness, key):
    request = tasks.AnalysisV2TaskRequest("case1", key, harness.fields, protein_part="head")
    thread_id = threading.get_ident()
    def check_thread(_):
        assert threading.get_ident() == thread_id
    harness.runner.log_callback = check_thread
    completion = harness.runner.run(request)
    assert harness.calls == ["head_segmentation", "head_calibration", "head_measurement"]
    assert completion["status"] == "measured"
    assert completion["part"] == "head"
    assert completion["context"]["interactive"] is False
    assert not completion["target_dir"].exists()


def test_protein3_tail_order_and_counts(harness):
    completion = harness.runner.run(tasks.AnalysisV2TaskRequest(
        "case1", "protein3", harness.fields, protein_part="tail",
    ))
    assert harness.calls == ["head_segmentation", "head_calibration", "c18b", "workset",
                             "contract", "register", "tail_calibration", "tail_measurement"]
    assert completion["status"] == "measured"
    assert tuple(completion[k] for k in ("tail_object_count", "associated_object_count",
                                       "unresolved_object_count")) == (89, 68, 21)
    assert completion["part"] == "tail"


def test_protein3_tail_emits_v3_unified_timing(harness):
    harness.runner.run(tasks.AnalysisV2TaskRequest(
        "case1", "protein3", harness.fields, protein_part="tail",
    ))
    events_path = harness.runner._paths.logs_dir / "events.jsonl"
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    finished = [
        item for item in events
        if item["event"] == "tail_v3_timing_finished"
    ]
    assert len(finished) == 1
    event = finished[0]
    assert event["duration_seconds"] >= 0
    assert event["extra"]["boundary"] == "prepared_request_to_tail_measured"
    assert event["extra"]["human_wait_seconds"] == 0.0
    assert event["extra"]["publisher_db_included"] is False
    assert set(event["extra"]["stages_seconds"]) == {
        "input_checkpoint", "head", "tail_core", "fragment_filter",
            "tail_core_checkpoint", "association_editor_adapter", "association_checkpoint", "c18b_orchestration_overhead",
        "tail_core_recovery_validation", "tail_core_reuse_materialization",
        "finalizer", "measurement", "checkpoint_overhead",
        "publisher_db",
    }
    assert event["extra"]["input_checkpoint_seconds"] >= 0
    assert len(event["extra"]["input_checkpoints"]) == 1
    assert event["extra"]["tail_core_checkpoints"] == []


def test_protein3_tail_writes_verified_input_manifest_checkpoint(harness):
    harness.runner.run(tasks.AnalysisV2TaskRequest(
        "case1", "protein3", harness.fields, protein_part="tail",
    ))
    root = harness.runner._paths.task_root / "checkpoints" / "input" / "001"
    generation = root / "generations" / "input_manifest"
    assert generation.is_dir()
    assert (generation / "completion.json").is_file()
    stored = CheckpointStore(root).load_checkpoint(generation)
    payload = json.loads((generation / "input_manifest.json").read_text(encoding="utf-8"))
    assert stored["stage"] == "input_manifest"
    assert (payload["field_id"], payload["protein_key"], payload["protein_part"]) == (
        "001", "protein3", "tail",
    )
    assert {row["role"] for row in payload["inputs"]} == {"FITC", "TRITC", "Merge"}
    assert {row["sha256"] for row in payload["inputs"]} == {
        sha256_file(Path(harness.fields[0]["R"])),
    }
    assert [item["role"] for item in stored["files"]] == ["input_manifest"]
    assert not (harness.runner._paths.task_root / "cp_output").exists()
    assert not any(path.suffix.lower() in (".tif", ".tiff") for path in generation.rglob("*"))
    assert [item["role"] for item in stage_input_projection(payload, "head")["inputs"]] == ["TRITC"]
    assert [item["role"] for item in stage_input_projection(payload, "tail_core")["inputs"]] == ["FITC"]
    assert stage_input_projection(payload, "association")["inputs"] == []


def test_input_manifest_checkpoint_rejects_second_generation(harness):
    fields = tasks.AnalysisV2TaskRunner(harness.config)._validate(
        tasks.AnalysisV2TaskRequest("case1", "protein3", harness.fields, protein_part="tail")
    )[1]
    task_root = harness.config.get_workspace_root() / "case1" / "analysis_v2" / "protein3" / "runs" / "one"
    write_and_verify_input_manifest_checkpoint(task_root, fields[0], "protein3")
    with pytest.raises(InputManifestCheckpointError, match="拒绝覆盖"):
        write_and_verify_input_manifest_checkpoint(task_root, fields[0], "protein3")


@pytest.mark.parametrize("reason", ["checkpoint create failed", "checkpoint validation failed"])
def test_input_manifest_checkpoint_failure_fails_before_head(harness, monkeypatch, reason):
    def fail(*args, **kwargs):
        raise InputManifestCheckpointError(reason)
    monkeypatch.setattr(tasks, "write_and_verify_input_manifest_checkpoint", fail)
    with pytest.raises(tasks.AnalysisV2TaskError) as error:
        harness.runner.run(tasks.AnalysisV2TaskRequest(
            "case1", "protein3", harness.fields, protein_part="tail",
        ))
    assert error.value.stage == "input_manifest"
    assert error.value.field_id == "001"
    assert reason in str(error.value)
    assert harness.calls == []


def test_protein3_head_skips_c18b_and_uses_head_measurement(harness):
    fields = [{key: value for key, value in harness.fields[0].items() if key != "Merge"}]
    completion = harness.runner.run(tasks.AnalysisV2TaskRequest(
        "case1", "protein3", fields, protein_part="head",
    ))
    assert harness.calls == ["head_segmentation", "head_calibration", "head_measurement"]
    assert completion["part"] == "head"
    assert completion["context"]["protein_part"] == "head"


def test_all_unresolved_completion(harness, monkeypatch):
    def measure(self, **kwargs):
        return {"validation": {"tail_object_count": 12, "result_parser": {
            "success": True, "calculation_mode": "head_equivalent", "total": {"positive_count": 0}}}}
    monkeypatch.setattr(harness.measurement, "run", measure)
    result = harness.runner.run(tasks.AnalysisV2TaskRequest(
        "case1", "protein3", harness.fields, protein_part="tail",
    ))
    assert tuple(result[k] for k in ("tail_object_count", "associated_object_count",
                                    "unresolved_object_count")) == (12, 0, 12)


def test_no_window_thread_publisher_database_dependencies(harness, monkeypatch):
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert not name.startswith("app.")
        assert "publisher" not in name.lower()
        assert "database" not in name.lower()
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    harness.runner.run(harness.request)
    for module in (tasks, sys.modules[C18BExecution.__module__]):
        source = inspect.getsource(module)
        for forbidden in ("QThread", "AnalysisWindow", "QWidget", "QMessageBox",
                          "HeadCalibrationWindow", "C18BTailCalibrationWindow",
                          "Publisher", "Database"):
            assert forbidden not in source


@pytest.mark.parametrize(
    "key,part",
    [
        ("bad", "head"), ("Q96P56", "head"), ("protein3", None),
        ("protein1", "tail"), ("protein2", "tail"),
        ("protein4", "tail"), ("protein5", "tail"),
    ],
)
def test_invalid_protein_or_part_rejected(harness, key, part):
    with pytest.raises(tasks.AnalysisV2TaskError) as error:
        harness.runner.run(tasks.AnalysisV2TaskRequest("case1", key, harness.fields, protein_part=part))
    assert error.value.stage == "validation"
    assert harness.calls == []


@pytest.mark.parametrize("channel", ["G", "R", "Merge"])
def test_protein3_requires_all_channels(harness, channel):
    fields = [dict(harness.fields[0])]
    del fields[0][channel]
    with pytest.raises(tasks.AnalysisV2TaskError):
        harness.runner.run(tasks.AnalysisV2TaskRequest(
            "case1", "protein3", fields, protein_part="tail",
        ))
    assert harness.calls == []


@pytest.mark.parametrize("stage", ["head_segmentation", "c18b", "tail_measurement", "head_measurement"])
def test_stage_failure_stops_chain(harness, monkeypatch, stage):
    cause = RuntimeError("injected")
    cause.return_code, cause.log_path, cause.field_id = 23, "stage.log", "001"
    def fail(*args, **kwargs):
        raise cause
    key = "protein3" if stage in ("c18b", "tail_measurement") else "protein1"
    part = "tail" if key == "protein3" else "head"
    if stage == "head_segmentation":
        monkeypatch.setattr(tasks, "run_head_segmentation", fail)
    elif stage == "c18b":
        monkeypatch.setattr(tasks.C18BExecution, "run", fail)
    else:
        monkeypatch.setattr(harness.measurement, "run", fail)
    with pytest.raises(tasks.AnalysisV2TaskError) as error:
        harness.runner.run(tasks.AnalysisV2TaskRequest(
            "case1", key, harness.fields, protein_part=part,
        ))
    assert error.value.stage == stage
    assert error.value.cause is cause
    assert error.value.case_no == "case1"
    assert error.value.protein_key == key
    assert error.value.return_code == 23
    assert error.value.log_path == "stage.log"
    assert error.value.field_id == "001"
    assert error.value.task_root
    assert "tail_measurement" not in harness.calls
    assert "head_measurement" not in harness.calls


def test_stage_failure_records_root_failure_and_requests_failure_cancel(harness, monkeypatch):
    cause = RuntimeError("primary stage failure")
    cause.field_id = "001"
    monkeypatch.setattr(tasks, "run_head_segmentation", lambda **kwargs: (_ for _ in ()).throw(cause))
    with pytest.raises(tasks.AnalysisV2TaskError):
        harness.runner.run(tasks.AnalysisV2TaskRequest(
            "case1", "protein1", harness.fields, protein_part="head",
        ))
    root = harness.runner._supervisor.root_failure
    assert (root.stage, root.field, root.exception_type, root.message) == (
        "head_segmentation", "001", "RuntimeError", "primary stage failure",
    )
    assert harness.runner._supervisor.cancellation_is_failure_triggered


def test_successful_runner_finalizes_supervisor_completed(harness):
    harness.runner.run(tasks.AnalysisV2TaskRequest(
        "case1", "protein1", harness.fields, protein_part="head",
    ))
    assert harness.runner._supervisor.state == "COMPLETED"


def test_user_cancel_is_not_recorded_as_computation_failure(harness):
    harness.runner.cancel()
    with pytest.raises(tasks.AnalysisV2TaskCancelled):
        harness.runner.run(harness.request)
    assert harness.runner._supervisor.root_failure is None


def test_service_shaped_fields_and_workspace(harness, tmp_path):
    row = harness.fields[0]
    fields = [{"field_id": "001", "tritc_path": row["R"], "fitc_path": row["G"]}]
    request = tasks.AnalysisV2TaskRequest(
        "case1", "protein1", fields, protein_part="head",
        workspace_root=tmp_path / "custom",
    )
    result = harness.runner.run(request)
    assert Path(result["task_root"]).relative_to(tmp_path / "custom" / "case1")


def test_interactive_tail_worker_delegates_shared_execution(tmp_path):
    from app.analysis_v2.tail_analysis_workers import TailPathWorker, TailFieldPrepareWorker
    assert TailPathWorker._ensure_c18b_result is C18BExecution._ensure_c18b_result
    assert TailPathWorker._prepare_c18b_editor_payload is C18BExecution._prepare_c18b_editor_payload
    (tmp_path / "manifest.json").write_text('{"protein_key":"protein3"}')
    (tmp_path / "worker_input.json").write_text('{"fields":[{"field_id":"001"}]}')
    worker = TailPathWorker(tmp_path, tmp_path, Path(sys.executable))
    results = []
    worker.finished_signal.connect(lambda *args: results.append(args))
    with mock.patch.object(C18BExecution, "_ensure_c18b_result", return_value=tmp_path / "labels") as ensure:
        with mock.patch.object(C18BExecution, "_prepare_c18b_editor_payload", return_value={"field_id": "001"}):
            worker.run()
    ensure.assert_called_once()
    assert results[0][0] is True

    assert results[0][1]["ready_for_measurement"] is False
    assert not worker.isRunning()
    head = tmp_path / "calibration" / "head" / "001_HeadFinalLabels.tif"
    head.parent.mkdir(parents=True)
    head.write_bytes(b"labels")
    field_worker = TailFieldPrepareWorker(tmp_path, tmp_path, Path(sys.executable), "001")
    results.clear()
    field_worker.finished_signal.connect(lambda *args: results.append(args))
    with mock.patch.object(C18BExecution, "_ensure_c18b_result", return_value=tmp_path / "labels") as ensure:
        field_worker.run()
    ensure.assert_called_once()
    assert results[0][0] is True


@pytest.mark.parametrize("total,associated", [(89, 68), (12, 0)])
def test_runner_real_calibration_and_tail_measurement_contract(harness, tmp_path, monkeypatch, total, associated):
    """Only model execution and the external CSV producer are replaced."""
    import shutil
    import numpy as np
    import tifffile
    from core.analysis_v2 import tail_calibration_service as calibration
    from core.analysis_v2.head_calibration_service import HeadCalibrationService
    from core.analysis_v2.tail_measurement_service import TailMeasurementService
    from core.analysis_v2.manifest_store import ManifestStore
    from core.analysis_v2.task_state import TaskStateStore
    from core.mvimageid_runner import MvImageIDRunner, MvImageIDRunResult
    from test_tail_automatic_workset import TailAutomaticWorksetTests
    from test_tail_contract_counts import _write_csv

    fixture = TailAutomaticWorksetTests()
    fixture.setUp()
    try:
        fixture.fixture(list(range(1, total + 1)), set(range(1, associated + 1)))
        # Match the formal consecutive head IDs produced by automatic calibration.
        for name in ("entries.json", "paths.json", "global_results.json"):
            data = json.loads((fixture.adapter / name).read_text())
            for row in data["results"]:
                row["head_id"] -= 100
            (fixture.adapter / name).write_text(json.dumps(data))
        harness.config.app_root = Path(__file__).resolve().parents[1]
        source = tmp_path / "image.tif"
        tifffile.imwrite(str(source), np.zeros((4, total + 1, 3), dtype=np.uint8))

        def segmentation(**kwargs):
            paths = kwargs["paths"]
            paths.create_directories()
            TaskStateStore.from_task_paths(paths).initialize(case_no="case1", protein_key="protein3")
            ManifestStore.from_task_paths(paths).initialize(case_no="case1", protein_key="protein3")
            for channel in ("TRITC", "FITC", "Merge"):
                shutil.copy2(str(source), str(paths.input_dir / ("001_" + channel + ".tif")))
            shutil.copy2(str(fixture.head_path), str(paths.segmentation_head_dir / "001_HeadInitialLabels.tif"))
            (paths.segmentation_head_dir / "001_HeadInitialObjects.json").write_text(json.dumps({"object_count": total}))

        class Prepared:
            field_id = "001"
            def __init__(self, project_root, task_root, *args):
                self.root = task_root
            def run(self):
                output = self.root / "calibration" / "tail" / "001"
                shutil.copytree(str(fixture.adapter), str(output))
                return {"fields": [{"field_id": "001", "output_dir": str(output),
                                    "head_labels": str(self.root / "calibration" / "head" / "001_HeadFinalLabels.tif"),
                                    "fragments": str(output / "fragments.tif")}]}

        def csv_producer(self, **kwargs):
            assert kwargs["process_context"] is harness.runner._process_context
            output = Path(kwargs["output_dir"])
            output.mkdir(parents=True, exist_ok=True)
            _write_csv(output / "Image.csv", ["ImageNumber", "Count_G_objects", "Count_R_objects",
                       "Count_R_colocalized", "Math_ColocalizationRate"], [{"ImageNumber": 1,
                       "Count_G_objects": total, "Count_R_objects": total,
                       "Count_R_colocalized": associated, "Math_ColocalizationRate": associated / total}])
            _write_csv(output / "G_objects.csv", ["ImageNumber", "ObjectNumber", "AreaShape_Area",
                       "Math_MeanIntensity255"], [{"ImageNumber": 1, "ObjectNumber": index,
                       "AreaShape_Area": 2, "Math_MeanIntensity255": 10} for index in range(1, total + 1)])
            for name in ("G_G_objects", "R_R_objects", "G_G_colocalized"):
                (output / ("001_" + name + "_OrigOverlay.png")).write_bytes(b"overlay")
            return MvImageIDRunResult(True, 0.01, 0)

        monkeypatch.setattr(tasks, "run_head_segmentation", segmentation)
        monkeypatch.setattr(tasks, "HeadCalibrationService", HeadCalibrationService)
        monkeypatch.setattr(tasks, "C18BExecution", Prepared)
        monkeypatch.setattr(tasks, "TailMeasurementService", TailMeasurementService)
        monkeypatch.setattr(MvImageIDRunner, "run", csv_producer)
        for name in ("save_initial_c18b_tail_workset", "build_automatic_tail_final_contract",
                     "register_tail_final_contract", "complete_tail_calibration"):
            monkeypatch.setattr(tasks, name, getattr(calibration, name))
        result = harness.runner.run(tasks.AnalysisV2TaskRequest(
            "case1", "protein3", harness.fields, protein_part="tail",
        ))
        assert result["status"] == "measured"
        assert tuple(result[k] for k in ("tail_object_count", "associated_object_count",
                                        "unresolved_object_count")) == (total, associated, total - associated)
        manifest = json.loads((Path(result["task_root"]) / "manifest.json").read_text(encoding="utf-8"))
        assert {"head_final_labels", "tail_final_labels", "tail_positive_head_labels",
                "tail_final_objects", "tail_measurement_image_csv"}.issubset({row["role"] for row in manifest["files"]})
        assert not result["target_dir"].exists()
    finally:
        fixture.doCleanups()
