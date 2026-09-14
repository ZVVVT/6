"""GUI Measurement workers each own and pass their own task process context."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from core.analysis_v2.task_process_context import TaskProcessContext


class _Config:
    def get_source_project_dir(self): return "C:/MvImageID"
    def get_python_exe(self): return "C:/MvImageID/.venv/Scripts/python.exe"
    def get_plugins_directory(self): return "C:/MvImageID/C-plugins/active_plugins"


@pytest.mark.parametrize(("module_name", "worker_name", "service_name", "pipeline_name"), [
    ("app.analysis_v2.head_analysis_workers", "HeadMeasurementWorker",
     "HeadMeasurementService", "measure_head_from_labels.cppipe"),
    ("app.analysis_v2.tail_analysis_workers", "TailMeasurementWorker",
     "TailMeasurementService", "measure_tail_from_labels.cppipe"),
])
def test_measurement_worker_owns_context_passes_it_to_service_and_cancels_it(
        tmp_path, monkeypatch, module_name, worker_name, service_name, pipeline_name):
    module = __import__(module_name, fromlist=[worker_name])
    pipeline = tmp_path / "pipelines" / "analysis_v2" / pipeline_name
    pipeline.parent.mkdir(parents=True)
    pipeline.write_text("pipeline", encoding="utf-8")
    contexts = []

    class Context(TaskProcessContext):
        def __init__(self):
            super().__init__()
            self.cancel = Mock(wraps=self.cancel)
            contexts.append(self)

    received = []
    class Service:
        def __init__(self, **kwargs):
            self.output_dir = tmp_path / "out"
            self.result_path = tmp_path / "result.json"
            self.measurement_manifest_path = tmp_path / "manifest.json"
        def run(self, process_context=None):
            received.append(process_context)
            return {"success": True}

    monkeypatch.setattr(module, "TaskProcessContext", Context)
    monkeypatch.setattr(module, service_name, Service)
    worker = getattr(module, worker_name)(tmp_path, tmp_path / "task", _Config())
    assert worker.process_context is contexts[0]
    worker.run()
    assert received == [worker.process_context]
    assert worker.supervisor.state == "RUNNING"
    assert worker.process_context._finish_requested
    worker.request_cancel()
    worker.process_context.cancel.assert_called_once_with(deadline=None)


@pytest.mark.parametrize("module_name,worker_name", [
    ("app.analysis_v2.head_analysis_workers", "HeadMeasurementWorker"),
    ("app.analysis_v2.tail_analysis_workers", "TailMeasurementWorker"),
])
def test_late_worker_cancel_does_not_interrupt_or_cancel_context(
        tmp_path, monkeypatch, module_name, worker_name):
    from core.analysis_v2.task_supervisor import TaskSupervisor
    module = __import__(module_name, fromlist=[worker_name])
    owner = TaskSupervisor()
    worker = getattr(module, worker_name)(tmp_path, tmp_path, _Config(), supervisor=owner)
    interrupted = Mock()
    monkeypatch.setattr(worker, "requestInterruption", interrupted)
    cancelled = Mock(wraps=owner.process_context.cancel)
    monkeypatch.setattr(owner.process_context, "cancel", cancelled)
    owner.expect_publication()
    owner.begin_publication()
    assert worker.request_cancel() is False
    interrupted.assert_not_called()
    cancelled.assert_not_called()
    owner.mark_publication_completed()
    assert worker.request_cancel() is False
    assert owner.state == "COMPLETED"
    assert not owner.cancel_event.is_set()
