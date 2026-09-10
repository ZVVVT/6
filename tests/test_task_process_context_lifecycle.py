"""Deterministic Windows Job lifecycle tests for future task branches."""

import os
import sys
import threading
import time

import pytest

from core.analysis_v2.task_process_context import (
    TaskProcessCancelled, TaskProcessContext,
)


pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Windows Job lifecycle contract",
)


def _short_lived_command():
    return [sys.executable, "-c", "pass"]


def _long_lived_command():
    return [sys.executable, "-c", "import time; time.sleep(30)"]


def _reap(context, process):
    process.wait(timeout=5)
    context.unregister(process)


def test_zero_direct_children_does_not_close_job_before_finish():
    context = TaskProcessContext()
    first = context.spawn_atomic(_short_lived_command())
    _reap(context, first)
    assert not context.has_active_processes()
    assert context._job.available, "temporary zero children must retain the task Job"

    second = context.spawn_atomic(_short_lived_command())
    _reap(context, second)
    assert context.finish()
    assert context._job_diagnostics["closed"]
    assert context._job_diagnostics["close_calls"] == 1


def test_concurrent_spawns_share_one_open_task_job():
    context = TaskProcessContext()
    start = threading.Barrier(3)
    processes = []
    failures = []

    def spawn():
        try:
            start.wait()
            processes.append(context.spawn_atomic(_long_lived_command()))
        except Exception as error:
            failures.append(error)

    threads = [threading.Thread(target=spawn) for _unused in range(2)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=5)
    try:
        assert not failures
        assert len(processes) == 2
        assert all(context._is_job_owned(process) for process in processes)
        assert context.has_active_processes()
    finally:
        context.cancel()
        assert context.wait(time.monotonic() + 5)
        assert context.finish()


def test_unregister_and_later_spawn_are_safe_across_threads():
    context = TaskProcessContext()
    first = context.spawn_atomic(_short_lived_command())
    first.wait(timeout=5)
    start = threading.Barrier(2)
    failures = []

    def unregister_first():
        try:
            start.wait()
            context.unregister(first)
        except Exception as error:
            failures.append(error)

    thread = threading.Thread(target=unregister_first)
    thread.start()
    start.wait()
    second = context.spawn_atomic(_short_lived_command())
    thread.join(timeout=5)
    _reap(context, second)
    assert not failures
    assert context.finish()


@pytest.mark.parametrize("seam_name", [
    "_before_atomic_create_locked",
    "_after_atomic_create_locked",
    "_after_atomic_assign_locked",
    "_before_atomic_resume_locked",
])
def test_cancel_during_atomic_spawn_never_resumes_an_unowned_child(tmp_path, monkeypatch,
                                                                   seam_name):
    context = TaskProcessContext()
    entered = threading.Event()
    release = threading.Event()
    marker = tmp_path / (seam_name + ".marker")
    outcome = []

    def pause(*_args):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(context, seam_name, pause)

    def spawn():
        try:
            context.spawn_atomic([
                sys.executable, "-c",
                "from pathlib import Path; Path(r'{}').write_text('ran')".format(marker),
            ])
        except Exception as error:
            outcome.append(error)

    spawn_thread = threading.Thread(target=spawn)
    spawn_thread.start()
    assert entered.wait(5)
    cancel_thread = threading.Thread(target=context.cancel)
    cancel_thread.start()
    assert context.cancel_event.wait(5)
    release.set()
    spawn_thread.join(timeout=5)
    cancel_thread.join(timeout=5)
    assert not spawn_thread.is_alive()
    assert not cancel_thread.is_alive()
    assert len(outcome) == 1 and isinstance(outcome[0], TaskProcessCancelled)
    assert not marker.exists()
    assert not context.has_active_processes()
    assert context.finish()


def test_finish_is_idempotent_and_forbids_future_spawns():
    context = TaskProcessContext()
    assert context.finish()
    assert context.finish()
    assert context._job_diagnostics["close_calls"] == 1
    with pytest.raises(RuntimeError, match="finished"):
        context.spawn_atomic(_short_lived_command())


def test_repeated_cancel_is_idempotent_and_forbids_future_spawns():
    context = TaskProcessContext()
    context.cancel()
    context.cancel()
    with pytest.raises(TaskProcessCancelled):
        context.spawn_atomic(_short_lived_command())
    assert context.finish()
