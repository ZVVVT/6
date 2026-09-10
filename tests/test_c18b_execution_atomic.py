"""C18B formal launcher ownership and Popen-contract tests."""

import io
import json
import os
import subprocess
import sys
import time

import pytest

from core.analysis_v2.c18b_execution import C18BExecution
from core.analysis_v2.task_process_context import TaskProcessContext
from core.analysis_v2.task_process_spawn import AtomicProcessSpawnError


class _CompletedProcess:
    pid = 525252

    def __init__(self, stdout="adapter output\n"):
        self.stdout = io.StringIO(stdout)
        self.wait_calls = 0

    def wait(self, timeout=None):
        self.wait_calls += 1
        return 0

    def poll(self):
        return 0


class _AtomicContext:
    def __init__(self):
        self.process = _CompletedProcess()
        self.spawn_calls = []
        self.unregistered = []
        self.checks = 0

    def check_cancelled(self):
        self.checks += 1

    def spawn_atomic(self, command, **kwargs):
        self.spawn_calls.append((command, kwargs))
        return self.process

    def unregister(self, process):
        self.unregistered.append(process)


def _execution(tmp_path, context=None):
    execution = C18BExecution(
        tmp_path, tmp_path, tmp_path / "configured runtime" / "python.exe",
        process_context=context,
    )
    execution.field_id = "field-001"
    return execution


def _provenance(log_path):
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("C18B launch_provenance="):
            return json.loads(line.split("=", 1)[1])
    raise AssertionError("missing C18B launch provenance")


def test_context_uses_atomic_launcher_preserves_popen_contract_and_reaps(
        tmp_path, monkeypatch):
    context = _AtomicContext()
    execution = _execution(tmp_path, context)
    log_path = tmp_path / "c18b.log"
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("context C18B launch used legacy Popen")
        ),
    )

    with log_path.open("w", encoding="utf-8") as handle:
        output, return_code = execution._run_streaming_command(
            ["configured-python", "-u", "adapter.py"], "C18B core", handle,
            launch_stage="c18b_tail_core",
        )

    assert output == "adapter output\n"
    assert return_code == 0
    assert context.unregistered == [context.process]
    assert context.process.wait_calls == 1
    command, kwargs = context.spawn_calls[0]
    assert command == ["configured-python", "-u", "adapter.py"]
    assert kwargs["cwd"] == str(tmp_path.resolve())
    assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert kwargs["env"]["PYTHONUNBUFFERED"] == "1"
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["text"] is True
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["bufsize"] == 1
    assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert _provenance(log_path) == {
        "stage": "c18b_tail_core",
        "field_id": "field-001",
        "mode": "atomic",
        "worker_pid": 525252,
        "worker_python": str((tmp_path / "configured runtime" / "python.exe").resolve()),
        "job_owned": True,
        "assign_completed": True,
        "resume_completed": True,
    }


def test_atomic_failure_fails_fast_without_popen_or_success_provenance(
        tmp_path, monkeypatch):
    class _FailingContext(_AtomicContext):
        def spawn_atomic(self, command, **kwargs):
            raise AtomicProcessSpawnError("AssignProcessToJobObject failed")

    execution = _execution(tmp_path, _FailingContext())
    log_path = tmp_path / "c18b.log"
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("atomic failure silently fell back to legacy Popen")
        ),
    )
    with log_path.open("w", encoding="utf-8") as handle:
        with pytest.raises(AtomicProcessSpawnError, match="AssignProcessToJobObject"):
            execution._run_streaming_command(["python", "adapter.py"], "C18B", handle)

    assert "launch_provenance" not in log_path.read_text(encoding="utf-8")


def test_no_context_keeps_explicit_legacy_popen_and_never_claims_atomic(
        tmp_path, monkeypatch):
    execution = _execution(tmp_path)
    log_path = tmp_path / "c18b.log"
    process = _CompletedProcess()
    registered = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        "core.analysis_v2.c18b_execution.analysis_process_registry.register",
        lambda value: registered.append(value) or value,
    )
    monkeypatch.setattr(
        "core.analysis_v2.c18b_execution.analysis_process_registry.unregister",
        lambda value: registered.remove(value),
    )

    with log_path.open("w", encoding="utf-8") as handle:
        execution._run_streaming_command(
            ["python", "adapter.py"], "C18B", handle,
            launch_stage="tail_editor_adapter",
        )

    assert registered == []
    provenance = _provenance(log_path)
    assert provenance["stage"] == "tail_editor_adapter"
    assert provenance["mode"] == "legacy"
    assert provenance["job_owned"] is False
    assert "assign_completed" not in provenance
    assert "resume_completed" not in provenance


def test_all_formal_c18b_process_call_sites_name_their_atomic_stage():
    source = C18BExecution._ensure_c18b_result.__code__.co_consts
    assert "c18b_tail_core" in source
    source = C18BExecution._prepare_c18b_editor_payload.__code__.co_consts
    assert "c18b_head_dependent_finalize" in source
    assert "tail_editor_adapter" in source


@pytest.mark.skipif(os.name != "nt", reason="Windows Job inheritance contract")
def test_c18b_style_reexec_child_inherits_atomic_task_job(tmp_path, monkeypatch):
    """The outer configured runtime may re-exec into .venv-c18b unchanged."""
    outer = tmp_path / "outer_adapter.py"
    child_pid_path = tmp_path / "c18b_reexec.pid"
    outer.write_text(
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "\"from pathlib import Path; import os,sys,time; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)\", "
        "sys.argv[1]])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    context = TaskProcessContext()
    monkeypatch.setattr(
        "core.analysis_process_registry.analysis_process_registry._terminate_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Job ownership must handle adapter re-exec")
        ),
    )
    monkeypatch.setattr(
        "core.analysis_v2.task_process_context.terminate_owned_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Job ownership must handle adapter re-exec")
        ),
    )
    outer_process = context.spawn_atomic(
        [sys.executable, str(outer), str(child_pid_path)]
    )
    deadline = time.monotonic() + 5
    while not child_pid_path.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert child_pid_path.is_file()
    # The Job's active count includes both its direct adapter and the child
    # started by adapter-internal subprocess.run/Popen without BREAKAWAY.
    assert context._job.active_process_count() >= 2
    try:
        context.cancel()
        outer_process.wait(timeout=5)
        assert context.wait(time.monotonic() + 5)
    finally:
        context.cancel()
