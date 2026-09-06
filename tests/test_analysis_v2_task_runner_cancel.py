"""Cancellation tests include real silent processes and a Popen/register race."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

from core.analysis_process_registry import analysis_process_registry
from core.analysis_v2 import task_runner as tasks
from core.analysis_v2.c18b_execution import C18BExecution
from core.analysis_v2.direct_cellpose_runner import DirectCellposeRunner
from core.analysis_v2.task_process_context import TaskProcessContext, TaskProcessCancelled
from core.mvimageid_runner import MvImageIDRunner
from test_analysis_v2_task_runner import harness


def test_cancel_before_run_is_sticky_and_idempotent(harness):
    harness.runner.cancel()
    harness.runner.cancel()
    with pytest.raises(tasks.AnalysisV2TaskCancelled):
        harness.runner.run(harness.request)
    assert harness.calls == []


@pytest.mark.parametrize("stage", ["head_segmentation", "head_calibration", "c18b",
                                  "tail_calibration", "head_measurement", "tail_measurement", "completion"])
def test_cancel_at_boundary_never_enters_next_stage(harness, stage):
    key = "protein3" if stage in ("c18b", "tail_calibration", "tail_measurement") else "protein1"
    def log(message):
        if message.endswith(": " + stage):
            harness.runner.cancel()
    harness.runner.log_callback = log
    with pytest.raises(tasks.AnalysisV2TaskCancelled) as error:
        harness.runner.run(tasks.AnalysisV2TaskRequest("case1", key, harness.fields))
    assert error.value.stage == stage
    assert stage not in harness.calls


def test_cancel_masks_stage_failure(harness, monkeypatch):
    def fail(**kwargs):
        harness.runner.cancel()
        raise RuntimeError("terminated process")
    monkeypatch.setattr(tasks, "run_head_segmentation", fail)
    with pytest.raises(tasks.AnalysisV2TaskCancelled):
        harness.runner.run(harness.request)
    assert harness.calls == []


def test_shutdown_normal_and_no_reuse(harness):
    assert harness.runner.shutdown()
    assert harness.runner.shutdown()
    with pytest.raises(tasks.AnalysisV2TaskCancelled):
        harness.runner.run(harness.request)


def test_shutdown_timeout_and_concurrent_run(harness, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    errors = []
    def segmentation(**kwargs):
        entered.set()
        release.wait(5)
    monkeypatch.setattr(tasks, "run_head_segmentation", segmentation)
    def run():
        try:
            harness.runner.run(harness.request)
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(tasks.AnalysisV2TaskError, match="already running"):
            harness.runner.run(harness.request)
        with pytest.raises(tasks.AnalysisV2TaskError) as error:
            harness.runner.shutdown(0.01)
        assert error.value.stage == "shutdown"
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert isinstance(errors[0], tasks.AnalysisV2TaskCancelled)
    assert harness.runner.shutdown(1)


@pytest.mark.parametrize("stage", ["head_segmentation", "c18b", "head_measurement", "tail_measurement"])
@pytest.mark.parametrize("race", [False, True])
def test_real_process_cancellation_is_task_scoped(harness, tmp_path, monkeypatch, stage, race):
    """Actual low-level runners execute a silent helper, without loading models."""
    ready = threading.Event()
    observed, errors = [], []
    popen = subprocess.Popen
    # Keep a separate, unrelated task alive throughout this task's cancellation.
    unrelated_context = TaskProcessContext()
    unrelated = unrelated_context.register(popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ))
    helper = tmp_path / "silent.py"
    helper.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    context = harness.runner._process_context

    def spawn(*args, **kwargs):
        process = popen(*args, **kwargs)
        observed.append(process)
        if race:
            # Cancellation occurs after creation but before caller can register.
            harness.runner.cancel()
        ready.set()
        return process

    def execute(**kwargs):
        if stage == "head_segmentation":
            runner = DirectCellposeRunner(Path(sys.executable), helper, tmp_path)
            runner.run(tmp_path / "input.json", tmp_path / "logs", tmp_path / "result.json",
                       process_context=context)
        elif stage == "c18b":
            runner = C18BExecution(tmp_path, tmp_path, sys.executable, process_context=context)
            with (tmp_path / "c18b.log").open("w") as handle:
                runner._run_streaming_command([sys.executable, str(helper)], "test", handle)
        else:
            runner = MvImageIDRunner(str(tmp_path), python_exe=sys.executable)
            monkeypatch.setattr(runner, "validate_paths", lambda *args: None)
            monkeypatch.setattr(runner, "build_command", lambda *args: [sys.executable, str(helper)])
            runner.run("pipeline", str(tmp_path), str(tmp_path / "measurement"), process_context=context)

    if stage == "head_segmentation":
        monkeypatch.setattr(tasks, "run_head_segmentation", execute)
    elif stage == "c18b":
        monkeypatch.setattr(tasks.C18BExecution, "run", lambda self: execute())
    else:
        monkeypatch.setattr(harness.measurement, "run", lambda self, **kwargs: execute())
    monkeypatch.setattr(subprocess, "Popen", spawn)
    key = "protein3" if stage in ("c18b", "tail_measurement") else "protein1"
    def run():
        try:
            harness.runner.run(tasks.AnalysisV2TaskRequest("case1", key, harness.fields))
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=run)
    # taskkill itself uses Popen; exclude it from the Popen race injection.
    def scoped_spawn(*args, **kwargs):
        if args and str(args[0][0]).lower() == "taskkill":
            return popen(*args, **kwargs)
        return spawn(*args, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", scoped_spawn)
    with mock.patch.object(analysis_process_registry, "terminate_all", side_effect=AssertionError("global cancel")):
        thread.start()
        try:
            assert ready.wait(5)
            if not race:
                harness.runner.cancel()
            assert harness.runner.shutdown(5)
            thread.join(5)
            assert not thread.is_alive()
            assert len(errors) == 1
            assert isinstance(errors[0], tasks.AnalysisV2TaskCancelled)
            assert all(process.poll() is not None for process in observed)
            assert unrelated.poll() is None
        finally:
            harness.runner.cancel()
            unrelated_context.cancel()
            unrelated.wait(timeout=5)
            unrelated_context.unregister(unrelated)
            thread.join(5)


def test_c18b_cancel_stops_before_next_field(tmp_path):
    context = TaskProcessContext()
    execution = C18BExecution(tmp_path, tmp_path, sys.executable, process_context=context)
    seen = []
    def prepare(field_id, *args):
        seen.append(field_id)
        context.cancel()
        return {}
    with mock.patch.object(execution, "_ensure_c18b_result", return_value=tmp_path / "labels"):
        with mock.patch.object(execution, "_prepare_c18b_editor_payload", side_effect=prepare):
            with (tmp_path / "log").open("w") as handle:
                with pytest.raises(TaskProcessCancelled):
                    execution._run_c18b_workflow(["001", "002"], handle, time.perf_counter())
    assert seen == ["001"]


def test_shutdown_reports_owned_process_that_will_not_exit(harness):
    process = mock.Mock(pid=123456, poll=mock.Mock(return_value=None))
    context = harness.runner._process_context
    with mock.patch.object(context, "_terminate"):
        context.register(process)
        try:
            with pytest.raises(tasks.AnalysisV2TaskError) as error:
                harness.runner.shutdown(0.01)
            assert error.value.stage == "shutdown"
        finally:
            process.poll.return_value = 1
            context.unregister(process)


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree contract")
def test_windows_cancellation_terminates_grandchild(tmp_path):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    pid_path = tmp_path / "grandchild.pid"
    code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)']); "
        "Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(20)"
    )
    context = TaskProcessContext()
    process = context.register(subprocess.Popen(
        [sys.executable, "-c", code, str(pid_path)],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ))
    handle = None
    try:
        deadline = time.monotonic() + 5
        while not pid_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_path.is_file()
        handle = kernel.OpenProcess(0x00100000, False, int(pid_path.read_text()))
        assert handle
        assert kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT: alive
        context.cancel()
        process.wait(timeout=5)
        assert kernel.WaitForSingleObject(handle, 5000) == 0
        assert context.wait(time.monotonic() + 1)
    finally:
        context.cancel()
        if handle:
            kernel.CloseHandle(handle)
