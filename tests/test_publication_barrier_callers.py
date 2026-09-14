"""Real common barrier through Batch and both GUI completion callbacks."""

from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock
import threading

import pytest

from app import batch_analysis_dialog as batch
from app.analysis_window import AnalysisWindow
from core.analysis_v2 import result_completion_service as service
from core.analysis_v2.task_runner import AnalysisV2TaskRunner
from core.analysis_v2.task_supervisor import TaskSupervisor
from test_analysis_v2_completion import page
from test_analysis_shutdown import ShutdownHarness
from test_analysis_v2_result_completion_service import completion, publication
from test_publication_commit_barrier import start_thread, joined
from test_analysis_v2_task_runner import harness


@pytest.mark.parametrize("part", ["head", "tail"])
def test_real_runner_finishes_resources_before_common_publication(harness, part, monkeypatch):
    from core.analysis_v2.task_runner import AnalysisV2TaskRequest
    request = AnalysisV2TaskRequest("case1", "protein3" if part == "tail" else "protein1",
                                    harness.fields, protein_part=part, case_id=7)
    measured = harness.runner.run(request)
    owner = harness.runner.supervisor
    assert owner.state == "RUNNING"
    assert owner.process_context._finish_requested
    assert harness.runner._done.is_set()
    staged = publication({"success": True, "calculation_mode": "head_equivalent"})
    monkeypatch.setattr(service, "stage_{}_measurement_output".format(part), Mock(return_value=staged))
    service.publish_measured_completion(measured, Mock(), supervisor=owner)
    harness.runner.shutdown()
    assert owner.state == "COMPLETED"
    assert not owner.cancel_event.is_set()
    assert harness.calls.count(part + "_measurement") == 1


@pytest.mark.parametrize("mode", ["before", "after", "failure"])
def test_batch_real_barrier_stops_following_tasks(tmp_path, monkeypatch, mode):
    runners = []
    value, summary = completion(tmp_path)
    entered, cancelled = threading.Event(), threading.Event()

    class Runner(AnalysisV2TaskRunner):
        def __init__(self, config, log_callback=None):
            super().__init__(config, log_callback)
            runners.append(self)

        def run(self, request):
            self.supervisor.expect_publication()
            self.supervisor.finalize()
            if mode == "before":
                entered.set()
                assert cancelled.wait(5)
            return value

    monkeypatch.setattr(batch, "AnalysisV2TaskRunner", Runner)
    monkeypatch.setattr(batch, "build_batch_task_request", lambda **kw: SimpleNamespace(
        case_no="case", protein_key=kw["protein_key"]))
    staged = publication(summary)

    def install(**kwargs):
        entered.set()
        assert cancelled.wait(5)
        return staged

    stage = Mock(side_effect=install)
    monkeypatch.setattr(service, "stage_head_measurement_output", stage)
    database = Mock()
    if mode == "failure":
        database.replace_protein_analysis_with_fields.side_effect = ValueError("DB root")
    worker = batch.BatchProteinWorker({}, [
        {"protein_key": "protein1", "protein_name": "first", "folder": tmp_path},
        {"protein_key": "protein2", "protein_name": "next", "folder": tmp_path},
    ], object(), database)
    statuses = []
    worker.task_status_signal.connect(lambda key, status: statuses.append((key, status)))

    def cancel():
        assert entered.wait(5)
        try:
            worker.request_cancel_after_current()
        finally:
            cancelled.set()
        return True

    thread, outcomes = start_thread(cancel)
    worker.run()
    assert joined(thread, outcomes) is True
    assert len(runners) == 1
    owner = runners[0].supervisor
    assert worker.current_runner is None
    assert ("protein2", "已取消") in statuses
    if mode == "before":
        assert owner.state == "CANCELLED"
        stage.assert_not_called()
        database.replace_protein_analysis_with_fields.assert_not_called()
    else:
        stage.assert_called_once()
        database.replace_protein_analysis_with_fields.assert_called_once()
        assert not owner.cancel_event.is_set()
        assert owner.state == ("FAILED" if mode == "failure" else "COMPLETED")
        if mode == "failure":
            assert owner.root_failure.message == "DB root"
            staged.rollback.assert_called_once()
        else:
            staged.commit.assert_called_once()


@pytest.mark.parametrize("part", ["head", "tail"])
@pytest.mark.parametrize("mode", ["cancel", "success", "failure"])
def test_gui_common_barrier_survives_worker_reference_cleanup(tmp_path, monkeypatch, part, mode):
    window, payload = page(tmp_path, part, True)
    owner = window.current_analysis_v2_supervisor
    owner.finalize()
    window.head_measurement_worker = None
    window.tail_measurement_worker = None
    # Exercise actual completion and common service, mocking only stage I/O.
    staged = publication(payload["measurement_result"]["parsed_result"])
    cancelled = []

    def install(**kwargs):
        cancelled.append(owner.request_cancel("late GUI user"))
        assert window.current_analysis_v2_supervisor is owner
        return staged

    stage = Mock(side_effect=install)
    monkeypatch.setattr(service, "stage_{}_measurement_output".format(part), stage)
    monkeypatch.setattr("app.analysis_window.QMessageBox", Mock())
    monkeypatch.setattr("app.analysis_window.show_long_message_dialog", Mock())
    monkeypatch.setattr("app.analysis_window.TaskStateStore", Mock())
    if mode == "cancel":
        owner.request_cancel("before callback")
    elif mode == "failure":
        window.database.replace_protein_analysis_with_fields.side_effect = ValueError("GUI DB root")
    getattr(AnalysisWindow, "_on_{}_measurement_finished".format(part))(
        window, True, 1.0, payload, "")
    assert window.current_analysis_v2_supervisor is owner
    if mode == "cancel":
        assert owner.finalize() == "CANCELLED"
        stage.assert_not_called()
        window.database.replace_protein_analysis_with_fields.assert_not_called()
    else:
        assert cancelled == [False]
        stage.assert_called_once()
        assert not owner.cancel_event.is_set()
        assert owner.state == ("FAILED" if mode == "failure" else "COMPLETED")
        assert owner.request_cancel("shutdown") is False


@pytest.mark.parametrize("state", ["started", "completed", "failed"])
def test_gui_shutdown_never_kills_or_writes_cancelled_after_barrier(monkeypatch, state):
    window = ShutdownHarness()
    owner = TaskSupervisor()
    owner.expect_publication()
    owner.begin_publication()
    if state == "completed":
        owner.mark_publication_completed()
    elif state == "failed":
        owner.record_failure("publication", None, ValueError("root"))
    window.current_analysis_v2_supervisor = owner
    window.current_analysis_v2_task_root = "existing-task"
    window._shutdown_cancel_requested = False
    registry, store = Mock(), Mock()
    monkeypatch.setattr("app.analysis_window.analysis_process_registry", registry)
    monkeypatch.setattr("app.analysis_window.TaskStateStore", store)
    window._cancel_analysis_for_shutdown()
    registry.terminate_all.assert_not_called()
    store.from_task_paths.assert_not_called()
    assert not window._shutdown_cancel_requested
    assert not owner.cancel_event.is_set()
    if state == "started":
        owner.mark_publication_completed()
    assert owner.finalize() == ("FAILED" if state == "failed" else "COMPLETED")


def test_real_head_measurement_worker_service_publisher_and_db(tmp_path, monkeypatch):
    from app.analysis_v2.head_analysis_workers import HeadMeasurementWorker
    from core.analysis_v2.head_calibration_service import HeadCalibrationService
    from core.analysis_v2.completion import build_completion_result
    from core.database import Database
    from core.mvimageid_runner import MvImageIDRunner, MvImageIDRunResult
    from test_head_automatic_calibration import _task
    from test_tail_result_publisher import _write_csv

    root = _task(tmp_path / "task")
    HeadCalibrationService(root, interactive=False).complete()
    project = Path(__file__).resolve().parents[1]
    config = SimpleNamespace(get_source_project_dir=lambda: project,
                             get_python_exe=lambda: project / ".venv" / "Scripts" / "python.exe",
                             get_plugins_directory=lambda: project)
    owner = TaskSupervisor()
    calls = []

    def produce_csv(runner, **kwargs):
        calls.append(kwargs["pipeline_file"])
        assert kwargs["process_context"] is owner.process_context
        output = Path(kwargs["output_dir"])
        rows, objects = [], []
        for index, count in ((1, 2), (2, 1), (3, 1)):
            field_id = "{:03d}".format(index)
            rows.append({"ImageNumber": index, "Metadata_SampleName": field_id,
                         "Count_R_objects": count, "Count_G_objects": count,
                         "Count_G_colocalized": 1, "Math_ColocalizationRate": 1 / count})
            objects.append({"ImageNumber": index, "ObjectNumber": 1,
                            "AreaShape_Area": 10, "Math_MeanIntensity255": 80})
            for name in ("R_objects", "G_objects", "G_colocalized"):
                (output / (field_id + "_" + name + "_Overlay.png")).write_bytes(b"overlay")
        _write_csv(output / "Image.csv", list(rows[0]), rows)
        _write_csv(output / "G_colocalized.csv", list(objects[0]), objects)
        return MvImageIDRunResult(True, 0.01, 0)

    monkeypatch.setattr(MvImageIDRunner, "run", produce_csv)
    worker = HeadMeasurementWorker(project, root, config, supervisor=owner)
    results = []
    worker.finished_signal.connect(lambda *args: results.append(args))
    worker.run()
    assert results[0][0], results[0][3]
    assert owner.state == "RUNNING"
    assert owner.process_context._finish_requested
    database = Database(str(tmp_path / "head.db"))
    with database.connect() as conn:
        case_id = conn.execute("INSERT INTO cases (case_no) VALUES ('head-integration')").lastrowid
        conn.commit()
    context = {"case_id": case_id, "protein_key": "protein1", "protein_name": "head",
               "field_count": 3, "target_output_dir": str(tmp_path / "formal")}
    measured = build_completion_result("head", results[0][2], context, root, results[0][1])
    del worker
    result = service.publish_measured_completion(measured, database, supervisor=owner)
    assert result.summary["total"]["positive_count"] == 3
    assert len(database.get_protein_analysis_by_case(case_id)) == 1
    assert owner.state == "COMPLETED"
    assert owner.request_cancel("shutdown") is False
    assert len(calls) == 1
    assert Path(calls[0]).name == "measure_head_from_labels.cppipe"
