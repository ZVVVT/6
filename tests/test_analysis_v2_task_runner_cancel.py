"""Cancellation tests include real silent processes and a Popen/register race."""

import os
import subprocess
import sys
import threading
import time
import ctypes
from ctypes import wintypes
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


def _open_process_identity(pid):
    """Open one Windows process instance for membership and exit diagnostics."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000 | 0x00001000, False, int(pid))
    assert handle, "OpenProcess({}, SYNCHRONIZE|PROCESS_QUERY_LIMITED_INFORMATION) failed: {}".format(
        pid, ctypes.get_last_error())
    return kernel, handle


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
    part = "tail" if key == "protein3" else "head"
    def log(message):
        if message.endswith(": " + stage):
            harness.runner.cancel()
    harness.runner.log_callback = log
    with pytest.raises(tasks.AnalysisV2TaskCancelled) as error:
        harness.runner.run(tasks.AnalysisV2TaskRequest(
            "case1", key, harness.fields, protein_part=part,
        ))
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
    atomic_spawn = context.spawn_atomic

    def spawn(*args, **kwargs):
        process = popen(*args, **kwargs)
        observed.append(process)
        if race:
            # Cancellation occurs after creation but before caller can register.
            harness.runner.cancel()
        ready.set()
        return process

    def spawn_atomic(*args, **kwargs):
        process = atomic_spawn(*args, **kwargs)
        observed.append(process)
        if race:
            # The formal Direct Cellpose launcher now owns the process before
            # this seam returns; cancellation must still converge promptly.
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
    monkeypatch.setattr(context, "spawn_atomic", spawn_atomic)
    key = "protein3" if stage in ("c18b", "tail_measurement") else "protein1"
    part = "tail" if key == "protein3" else "head"
    def run():
        try:
            harness.runner.run(tasks.AnalysisV2TaskRequest(
                "case1", key, harness.fields, protein_part=part,
            ))
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=run)
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


def test_cancel_reaps_owned_direct_process_when_tree_termination_is_unavailable(monkeypatch):
    """A denied tree operation cannot leave this task's direct child running."""
    class Process:
        pid = 456789

        def __init__(self):
            self.return_code = None
            self.terminate_calls = 0

        def poll(self):
            return self.return_code

        def terminate(self):
            self.terminate_calls += 1
            self.return_code = 1

    context = TaskProcessContext()
    process = Process()
    monkeypatch.setattr(
        analysis_process_registry, "_terminate_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )
    context.register(process)
    context.cancel()
    assert process.terminate_calls == 1
    assert context.wait(time.monotonic() + 0.1)
    assert not context.has_active_processes()


def test_shutdown_reuses_one_absolute_deadline_for_every_reap_wait(harness, monkeypatch):
    deadlines = []
    monkeypatch.setattr(harness.runner._process_context, "wait",
                        lambda deadline: deadlines.append(deadline) or True)
    assert harness.runner.shutdown(1)
    assert len(deadlines) == 2
    assert deadlines[0] == deadlines[1]


def test_shutdown_timeout_does_not_replace_existing_root_failure(harness, monkeypatch):
    root = harness.runner._supervisor.record_failure(
        "c18b", "001", RuntimeError("primary failure"),
    )
    monkeypatch.setattr(harness.runner._process_context, "wait", lambda deadline: False)
    with pytest.raises(tasks.AnalysisV2TaskError, match="Task shutdown timed out"):
        harness.runner.shutdown(0)
    assert harness.runner._supervisor.root_failure is root
    assert (root.stage, root.field, root.message) == ("c18b", "001", "primary failure")


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


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree contract")
def test_windows_cancellation_terminates_three_level_tree(tmp_path):
    script = tmp_path / "tree.py"
    child_pid = tmp_path / "child.pid"
    grandchild_pid = tmp_path / "grandchild.pid"
    script.write_text(
        "import subprocess,sys,time\n"
        "from pathlib import Path\n"
        "role, child_path, grandchild_path = sys.argv[1:]\n"
        "if role == 'root':\n"
        "    child = subprocess.Popen([sys.executable, __file__, 'child', child_path, grandchild_path])\n"
        "    Path(child_path).write_text(str(child.pid))\n"
        "elif role == 'child':\n"
        "    grandchild = subprocess.Popen([sys.executable, __file__, 'grandchild', child_path, grandchild_path])\n"
        "    Path(grandchild_path).write_text(str(grandchild.pid))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    context = TaskProcessContext()
    root = context.register(subprocess.Popen(
        [sys.executable, str(script), "root", str(child_pid), str(grandchild_pid)],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ))
    try:
        _wait_for_file(grandchild_pid)
        pids = [root.pid, int(child_pid.read_text()), int(grandchild_pid.read_text())]
        assert all(_windows_pid_is_alive(pid) for pid in pids)
        context.cancel()
        root.wait(timeout=5)
        assert all(not _windows_pid_is_alive(pid) for pid in pids[1:])
        assert context.wait(time.monotonic() + 1)
    finally:
        context.cancel()


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree contract")
def test_windows_tree_cancel_does_not_terminate_unrelated_process(tmp_path):
    unrelated_context = TaskProcessContext()
    unrelated = unrelated_context.register(subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ))
    context = TaskProcessContext()
    pid_path = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(30)"
    )
    root = context.register(subprocess.Popen([sys.executable, "-c", code, str(pid_path)]))
    try:
        _wait_for_file(pid_path)
        context.cancel()
        root.wait(timeout=5)
        assert not _windows_pid_is_alive(int(pid_path.read_text()))
        assert unrelated.poll() is None
        assert context.wait(time.monotonic() + 1)
    finally:
        context.cancel()
        unrelated_context.cancel()
        unrelated_context.wait(time.monotonic() + 5)


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree contract")
def test_windows_tree_cancel_converges_spawn_race(tmp_path):
    script = tmp_path / "race.py"
    pid_path = tmp_path / "spawned.pid"
    script.write_text(
        "import subprocess,sys,time\n"
        "from pathlib import Path\n"
        "time.sleep(.05)\n"
        "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "Path(sys.argv[1]).write_text(str(child.pid))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    context = TaskProcessContext()
    root = context.register(subprocess.Popen([sys.executable, str(script), str(pid_path)]))
    try:
        # Cancellation races the delayed spawn.  Either no child is created or
        # any child published by the root is part of the subsequently verified tree.
        time.sleep(.04)
        context.cancel()
        root.wait(timeout=5)
        if pid_path.exists():
            assert not _windows_pid_is_alive(int(pid_path.read_text()))
        assert context.wait(time.monotonic() + 1)
    finally:
        context.cancel()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_windows_job_post_assignment_three_level_orphan_is_killed(tmp_path, monkeypatch):
    """A child that exits after spawning cannot detach its Job-owned grandchild."""
    script = tmp_path / "controlled_tree.py"
    allow_child = tmp_path / "allow-child"
    child_pid = tmp_path / "child.pid"
    grandchild_pid = tmp_path / "grandchild.pid"
    child_exited = tmp_path / "child-exited"
    script.write_text(
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        "role, allow, childpid, pidfile, exited = sys.argv[1:]\n"
        "if role == 'root':\n"
        " while not Path(allow).exists(): time.sleep(.005)\n"
        " child=subprocess.Popen([sys.executable, __file__, 'child', allow, childpid, pidfile, exited])\n"
        " Path(childpid).write_text(str(child.pid))\n"
        " time.sleep(30)\n"
        "elif role == 'child':\n"
        " child=subprocess.Popen([sys.executable, __file__, 'grandchild', allow, childpid, pidfile, exited])\n"
        " Path(pidfile).write_text(str(child.pid))\n"
        " Path(exited).write_text('yes')\n"
        "elif role == 'grandchild': time.sleep(30)\n",
        encoding="utf-8",
    )
    context = TaskProcessContext()
    root = context.register(subprocess.Popen([sys.executable, str(script), "root",
                                               str(allow_child), str(child_pid), str(grandchild_pid),
                                               str(child_exited)]))
    grandchild_kernel = grandchild_handle = child_handle = None
    try:
        assert context._job_diagnostics["assignments"][-1]["assigned"]
        allow_child.write_text("go", encoding="utf-8")
        _wait_for_file(child_pid)
        _wait_for_file(grandchild_pid)
        _wait_for_file(child_exited)
        child_value = int(child_pid.read_text())
        grandchild_value = int(grandchild_pid.read_text())
        child_kernel, child_handle = _open_process_identity(child_value)
        grandchild_kernel, grandchild_handle = _open_process_identity(grandchild_value)
        assert child_kernel.WaitForSingleObject(child_handle, 5000) == 0
        flags = context._job.limit_flags()
        diagnostic = {
            "pids": {"root": root.pid, "child": child_value, "grandchild": grandchild_value},
            "creationflags": {"root": 0, "child": 0, "grandchild": 0},
            "membership_before_cancel": {
                "root": context._job.is_process_in_job(root._handle),
                "child": context._job.is_process_in_job(child_handle),
                "grandchild": context._job.is_process_in_job(grandchild_handle),
            },
            "active_before_cancel": context._job.active_process_count(),
            "job_limit_flags": flags,
            "breakaway_ok": bool(flags & 0x00000800),
            "silent_breakaway_ok": bool(flags & 0x00001000),
            "child_handle_signaled_before_cancel": True,
        }
        print("post-assignment Job diagnostic before cancel: {}".format(diagnostic))
        assert diagnostic["membership_before_cancel"] == {
            "root": True, "child": True, "grandchild": True,
        }
        # Do not let the pre-Job ancestry cleanup hide the successful Job path.
        monkeypatch.setattr(analysis_process_registry, "_terminate_tree",
                            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fallback")))
        monkeypatch.setattr("core.analysis_v2.task_process_context.terminate_owned_tree",
                            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fallback")))
        context.cancel()
        termination = context._job_diagnostics["termination"]
        diagnostic["termination"] = termination
        diagnostic["active_immediately_after_cancel"] = context._job.active_process_count()
        diagnostic["grandchild_handle_wait_0"] = grandchild_kernel.WaitForSingleObject(
            grandchild_handle, 0)
        diagnostic["pid_alive_immediately_after_cancel"] = _windows_pid_is_alive(grandchild_value)
        print("post-assignment Job diagnostic after cancel: {}".format(diagnostic))
        root.wait(timeout=5)
        assert context.wait(time.monotonic() + 5)
        # Task completion, rather than a temporary empty direct-child
        # registry, owns the final Job close.
        assert context.finish()
        diagnostic["active_after_shared_deadline"] = context._job_diagnostics["active_after_reap"]
        diagnostic["grandchild_handle_after_shared_deadline"] = grandchild_kernel.WaitForSingleObject(
            grandchild_handle, 0)
        diagnostic["pid_alive_after_shared_deadline"] = _windows_pid_is_alive(grandchild_value)
        print("post-assignment Job diagnostic converged: {}".format(diagnostic))
        assert diagnostic["grandchild_handle_after_shared_deadline"] == 0
        assert not diagnostic["pid_alive_after_shared_deadline"]
        assert context._job_diagnostics["termination"]["requested"]
        assert context._job_diagnostics["active_after_reap"] == 0
    finally:
        context.cancel()
        if child_handle:
            child_kernel.CloseHandle(child_handle)
        if grandchild_handle:
            grandchild_kernel.CloseHandle(grandchild_handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_windows_job_does_not_retroactively_own_pre_assignment_descendants(tmp_path):
    """Documents the Popen-to-Assign window with a controlled, non-random probe."""
    script = tmp_path / "pre_assignment_tree.py"
    child_ready = tmp_path / "child-ready"
    allow_grandchild = tmp_path / "allow-grandchild"
    grandchild_pid = tmp_path / "grandchild.pid"
    script.write_text(
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        "role, ready, allow, pidfile = sys.argv[1:]\n"
        "if role == 'root':\n"
        " subprocess.Popen([sys.executable, __file__, 'child', ready, allow, pidfile])\n"
        " time.sleep(30)\n"
        "elif role == 'child':\n"
        " Path(ready).write_text('ready')\n"
        " while not Path(allow).exists(): time.sleep(.005)\n"
        " grandchild=subprocess.Popen([sys.executable, __file__, 'grandchild', ready, allow, pidfile])\n"
        " Path(pidfile).write_text(str(grandchild.pid))\n"
        "elif role == 'grandchild': time.sleep(30)\n",
        encoding="utf-8",
    )
    root = subprocess.Popen([sys.executable, str(script), "root", str(child_ready),
                             str(allow_grandchild), str(grandchild_pid)])
    context = TaskProcessContext()
    try:
        _wait_for_file(child_ready)
        context.register(root)
        assert context._job_diagnostics["assignments"][-1]["assigned"]
        allow_grandchild.write_text("go", encoding="utf-8")
        _wait_for_file(grandchild_pid)
        context.cancel()
        root.wait(timeout=5)
        # An already-created child was never inserted into root's Job, so its
        # later grandchild survives Job termination.  Cleanup below is explicit.
        assert _windows_pid_is_alive(int(grandchild_pid.read_text()))
    finally:
        context.cancel()
        if grandchild_pid.is_file():
            subprocess.run(["taskkill", "/PID", grandchild_pid.read_text(), "/T", "/F"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        context.wait(time.monotonic() + 5)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_windows_job_contexts_are_isolated_and_handles_close():
    context_a, context_b = TaskProcessContext(), TaskProcessContext()
    process_a = context_a.register(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]))
    process_b = context_b.register(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]))
    try:
        assert context_a._job_diagnostics["assignments"][-1]["assigned"]
        assert context_b._job_diagnostics["assignments"][-1]["assigned"]
        context_a.cancel()
        assert context_a.wait(time.monotonic() + 5)
        assert process_b.poll() is None
        assert context_a.finish()
        assert context_a._job_diagnostics["closed"]
    finally:
        context_a.cancel()
        context_b.cancel()
        context_b.wait(time.monotonic() + 5)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_windows_job_normal_completion_closes_handle():
    context = TaskProcessContext()
    process = context.register(subprocess.Popen([sys.executable, "-c", "pass"]))
    process.wait(timeout=5)
    assert context.wait(time.monotonic() + 5)
    assert context.finish()
    assert context._job_diagnostics["active_after_reap"] == 0
    assert context._job_diagnostics["closed"]


def _wait_for_file(path, timeout=5):
    deadline = time.monotonic() + timeout
    while not path.is_file() and time.monotonic() < deadline:
        time.sleep(.01)
    assert path.is_file()


def _windows_pid_is_alive(pid):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, int(pid))
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)
