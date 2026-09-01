import os
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.analysis_window import AnalysisWindow
from core.analysis_process_registry import AnalysisProcessRegistry


class FakeButton:
    def __init__(self):
        self.text = ""
        self.enabled = True

    def setText(self, text):
        self.text = text

    def setEnabled(self, enabled):
        self.enabled = enabled


class FakeWorker:
    def __init__(self):
        self.running = True
        self.cancelled = False

    def isRunning(self):
        return self.running

    def request_cancel(self):
        self.cancelled = True
        self.running = False

    def wait(self, _milliseconds):
        self.running = False
        return True


class FakeHeadWindow:
    def __init__(self):
        self.closed = False

    def close_for_shutdown(self):
        self.closed = True


class FakeTailController:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class ShutdownHarness:
    _worker_is_running = AnalysisWindow._worker_is_running
    _cancel_analysis_for_shutdown = AnalysisWindow._cancel_analysis_for_shutdown

    def __init__(self):
        self.btn_run_analysis = FakeButton()
        self.head_calibration_window = None
        self.tail_calibration_controller = None
        self.analysis_worker = None
        self.head_segmentation_worker = None
        self.head_measurement_worker = None
        self.tail_field_prepare_worker = None
        self.tail_path_worker = None
        self.tail_measurement_worker = None
        self.current_analysis_v2_task_root = None
        self._analysis_running = True
        self.messages = []

    def append_log(self, message):
        self.messages.append(str(message))


def _run_shutdown(worker_name, head_window=False, tail_window=False):
    QApplication.instance() or QApplication([])
    harness = ShutdownHarness()
    worker = FakeWorker()
    setattr(harness, worker_name, worker)
    if head_window:
        harness.head_calibration_window = FakeHeadWindow()
    if tail_window:
        harness.tail_calibration_controller = FakeTailController()
    old_head = harness.head_calibration_window
    old_tail = harness.tail_calibration_controller
    with mock.patch("app.analysis_window.analysis_process_registry") as registry:
        harness._cancel_analysis_for_shutdown()
        assert registry.terminate_all.call_count >= 2
    assert worker.cancelled
    assert not worker.running
    assert harness._shutdown_cancel_requested
    assert not harness._analysis_running
    assert harness.btn_run_analysis.text == "正在终止分析并关闭后台任务，请稍候……"
    if old_head is not None:
        assert old_head.closed
    if old_tail is not None:
        assert old_tail.stopped


def test_exit_during_head_auto_segmentation():
    _run_shutdown("head_segmentation_worker")


def test_exit_with_head_calibration_window():
    _run_shutdown("tail_field_prepare_worker", head_window=True)


def test_exit_with_tail_calibration_window():
    _run_shutdown("tail_path_worker", tail_window=True)


def test_exit_during_tail_measurement():
    _run_shutdown("tail_measurement_worker")


def test_registry_terminates_only_registered_process_tree():
    registry = AnalysisProcessRegistry()
    root = mock.Mock()
    root.pid = 43210
    registry.register(root)
    assert registry.root_pids() == [43210]
    with mock.patch("core.analysis_process_registry.os.name", "nt"):
        with mock.patch("core.analysis_process_registry.subprocess.run") as run:
            registry.terminate_all()
    assert run.call_args.args[0] == ["taskkill", "/PID", "43210", "/T", "/F"]
