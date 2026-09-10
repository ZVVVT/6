"""Synthetic contract tests for the Phase2D2A atomic Windows spawn primitive."""

import os
import subprocess
import sys
import threading
import time

import pytest

from core.analysis_process_registry import analysis_process_registry
from core.analysis_v2.task_process_context import TaskProcessContext, TaskProcessCancelled
from core.analysis_v2.task_process_spawn import AtomicProcessSpawnError


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows atomic Job spawn contract")


def _wait_for(path, timeout=5):
    deadline = time.monotonic() + timeout
    while not path.is_file() and time.monotonic() < deadline:
        time.sleep(.005)
    assert path.is_file()


def _alive(pid):
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


def _wait_dead(pid, timeout_ms=5000):
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
        return True
    try:
        return kernel.WaitForSingleObject(handle, timeout_ms) == 0
    finally:
        kernel.CloseHandle(handle)


def _tree_script(path):
    path.write_text(
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        "role, marker, child_pid, grandchild_pid = sys.argv[1:]\n"
        "if role == 'root':\n"
        " Path(marker).write_text('started', encoding='utf-8')\n"
        " child=subprocess.Popen([sys.executable, __file__, 'child', marker, child_pid, grandchild_pid])\n"
        " Path(child_pid).write_text(str(child.pid))\n"
        " time.sleep(30)\n"
        "elif role == 'child':\n"
        " grandchild=subprocess.Popen([sys.executable, __file__, 'grandchild', marker, child_pid, grandchild_pid])\n"
        " Path(grandchild_pid).write_text(str(grandchild.pid))\n"
        "elif role == 'grandchild': time.sleep(30)\n",
        encoding="utf-8",
    )


def test_atomic_spawn_assigns_before_any_user_code(tmp_path, monkeypatch):
    marker = tmp_path / "started.marker"
    context = TaskProcessContext()
    original_assign = context._job.assign_process

    def assign(process_handle):
        assert not marker.exists(), "suspended root executed before Job assignment"
        return original_assign(process_handle)

    monkeypatch.setattr(context._job, "assign_process", assign)
    process = context.spawn_atomic([sys.executable, "-c",
                                    "from pathlib import Path; Path(r'{}').write_text('x')".format(marker)])
    process.wait(timeout=5)
    _wait_for(marker)
    assert context.wait(time.monotonic() + 5)


def test_atomic_immediate_tree_job_only_termination_30_times(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis_process_registry, "_terminate_tree",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("taskkill disabled")))
    monkeypatch.setattr("core.analysis_v2.task_process_context.terminate_owned_tree",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Toolhelp disabled")))
    for index in range(30):
        script = tmp_path / ("tree {}.py".format(index))
        marker, child_pid, grandchild_pid = (tmp_path / ("{} {}".format(index, name))
                                              for name in ("started", "child.pid", "grandchild.pid"))
        _tree_script(script)
        context = TaskProcessContext()
        root = context.spawn_atomic([sys.executable, str(script), "root", str(marker),
                                     str(child_pid), str(grandchild_pid)])
        try:
            _wait_for(grandchild_pid)
            assert context._is_job_owned(root)
            context.cancel()
            root.wait(timeout=5)
            assert _wait_dead(int(child_pid.read_text()))
            assert _wait_dead(int(grandchild_pid.read_text()))
            assert context.wait(time.monotonic() + 5)
        finally:
            context.cancel()


def test_atomic_cancel_before_resume_never_runs_user_code(tmp_path, monkeypatch):
    marker = tmp_path / "must-not-exist"
    context = TaskProcessContext()

    def cancel_before_resume(process):
        # This is the same sticky transition used by cancel(), injected while
        # the atomic critical section still owns the lock.
        context._set_cancelled_locked()

    monkeypatch.setattr(context, "_before_atomic_resume_locked", cancel_before_resume)
    with pytest.raises(TaskProcessCancelled):
        context.spawn_atomic([sys.executable, "-c",
                              "from pathlib import Path; Path(r'{}').write_text('bad')".format(marker)])
    assert not marker.exists()
    assert not context.has_active_processes()
    assert context.finish()
    assert context._job_diagnostics["active_after_reap"] == 0


def test_atomic_assign_failure_reaps_without_resume(tmp_path, monkeypatch):
    marker = tmp_path / "must-not-exist"
    context = TaskProcessContext()
    monkeypatch.setattr(context._job, "assign_process",
                        lambda handle: {"assigned": False, "winerror": 5})
    with pytest.raises(AtomicProcessSpawnError, match="AssignProcessToJobObject"):
        context.spawn_atomic([sys.executable, "-c",
                              "from pathlib import Path; Path(r'{}').write_text('bad')".format(marker)])
    assert not marker.exists()
    assert not context.has_active_processes()
    assert context.finish()
    assert context._job_diagnostics["active_after_reap"] == 0


def test_atomic_stdio_cwd_env_unicode_and_normal_completion(tmp_path):
    cwd = tmp_path / "中文 空格"
    cwd.mkdir()
    context = TaskProcessContext()
    env = os.environ.copy()
    env["ATOMIC_SPAWN_TEST_VALUE"] = "中文 value"
    process = context.spawn_atomic(
        [sys.executable, "-c", "import os; print(os.getcwd()); print(os.environ['ATOMIC_SPAWN_TEST_VALUE'])"],
        cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate(timeout=5)
    assert stderr == b""
    assert str(cwd).encode("utf-8") in stdout
    assert "中文 value".encode("utf-8") in stdout
    assert process.returncode == 0
    assert context.wait(time.monotonic() + 5)
    assert context.finish()
    assert context._job_diagnostics["active_after_reap"] == 0
    assert context._job_diagnostics["closed"]


def test_atomic_multiple_task_and_unrelated_process(tmp_path):
    context_a, context_b = TaskProcessContext(), TaskProcessContext()
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    process_a = context_a.spawn_atomic([sys.executable, "-c", "import time; time.sleep(30)"])
    process_b = context_b.spawn_atomic([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        context_a.cancel()
        process_a.wait(timeout=5)
        assert context_a.wait(time.monotonic() + 5)
        assert process_b.poll() is None
        assert unrelated.poll() is None
    finally:
        context_a.cancel()
        context_b.cancel()
        unrelated.terminate()
        context_b.wait(time.monotonic() + 5)
        unrelated.wait(timeout=5)


def test_atomic_fast_spawn_cancel_50_times(tmp_path):
    pids = []
    for index in range(50):
        context = TaskProcessContext()
        process = context.spawn_atomic([sys.executable, "-c", "import time; time.sleep(30)"])
        pids.append(process.pid)
        if index % 2:
            process.terminate()
            process.wait(timeout=5)
        else:
            context.cancel()
            process.wait(timeout=5)
        assert context.wait(time.monotonic() + 5)
    assert all(not _alive(pid) for pid in pids)
