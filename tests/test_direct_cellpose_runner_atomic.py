"""Direct Cellpose launcher ownership and Popen-contract tests."""

import os
import json
import subprocess
from pathlib import Path

import pytest

from core.analysis_v2.direct_cellpose_runner import DirectCellposeRunner
from core.analysis_v2.task_process_spawn import AtomicProcessSpawnError


class _CompletedProcess:
    pid = 424242

    def __init__(self):
        self.wait_timeouts = []

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        return 0

    def poll(self):
        return 0


class _AtomicContext:
    def __init__(self):
        self.spawn_calls = []
        self.unregistered = []
        self.checks = 0
        self.process = _CompletedProcess()

    def check_cancelled(self):
        self.checks += 1

    def spawn_atomic(self, args, **kwargs):
        self.spawn_calls.append((args, kwargs))
        return self.process

    def unregister(self, process):
        self.unregistered.append(process)


def _write_worker_input(path, field_ids=("field-001",)):
    path.write_text(
        json.dumps({"fields": [{"field_id": field_id} for field_id in field_ids]}),
        encoding="utf-8",
    )


def test_task_context_uses_atomic_launcher_and_preserves_popen_contract(
        tmp_path, monkeypatch):
    """The formal path must never fall back to Popen then register."""
    python_path = tmp_path / "MvImageID Python" / "python.exe"
    worker_path = tmp_path / "direct_cellpose_worker.py"
    input_path = tmp_path / "worker_input.json"
    _write_worker_input(input_path, ("field-001", "field-002"))
    context = _AtomicContext()
    runner = DirectCellposeRunner(python_path, worker_path, tmp_path)

    monkeypatch.setattr(
        subprocess, "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("formal Direct Cellpose path used legacy Popen")
        ),
    )

    result = runner.run(
        input_path, tmp_path / "logs", tmp_path / "worker_result.json",
        process_context=context,
    )

    assert result.return_code == 0
    assert context.unregistered == [context.process]
    assert len(context.spawn_calls) == 1
    args, kwargs = context.spawn_calls[0]
    assert args == [
        str(python_path.resolve()), "-u", str(worker_path.resolve()),
        "--input-json", str(input_path.resolve()),
    ]
    assert kwargs["cwd"] == str(tmp_path.resolve())
    assert kwargs["env"]["PYTHONUNBUFFERED"] == "1"
    assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert kwargs["stdout"].name == str((tmp_path / "logs" / "head_segmentation_stdout.log").resolve())
    assert kwargs["stderr"].name == str((tmp_path / "logs" / "head_segmentation_stderr.log").resolve())
    command_record = json.loads(
        (tmp_path / "logs" / "head_segmentation_command.txt").read_text(
            encoding="utf-8"
        )
    )
    provenance = command_record["launch_provenance"]
    assert provenance == {
        "stage": "direct_cellpose_spawn",
        "mode": "atomic",
        "field_ids": ["field-001", "field-002"],
        "worker_pid": 424242,
        "worker_python": str(python_path.resolve()),
        # These success facts rely on spawn_atomic's return contract, rather
        # than a second attempt to inspect Windows process state.
        "job_owned": True,
        "assign_completed": True,
        "resume_completed": True,
    }


def test_task_context_atomic_failure_is_not_downgraded_to_legacy_popen(
        tmp_path, monkeypatch):
    class FailingContext(_AtomicContext):
        def spawn_atomic(self, args, **kwargs):
            raise AtomicProcessSpawnError("AssignProcessToJobObject failed")

    runner = DirectCellposeRunner(
        tmp_path / "python.exe", tmp_path / "worker.py", tmp_path,
    )
    input_path = tmp_path / "input.json"
    _write_worker_input(input_path)
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("atomic failure was silently downgraded")
        ),
    )

    with pytest.raises(AtomicProcessSpawnError, match="AssignProcessToJobObject"):
        runner.run(
            input_path, tmp_path / "logs", tmp_path / "result.json",
            process_context=FailingContext(),
        )
    command_record = json.loads(
        (tmp_path / "logs" / "head_segmentation_command.txt").read_text(
            encoding="utf-8"
        )
    )
    assert "launch_provenance" not in command_record


def test_legacy_launcher_records_legacy_provenance_without_job_ownership(
        tmp_path, monkeypatch):
    input_path = tmp_path / "worker_input.json"
    _write_worker_input(input_path)
    runner = DirectCellposeRunner(
        tmp_path / "python.exe", tmp_path / "worker.py", tmp_path,
    )
    process = _CompletedProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)

    result = runner.run(
        input_path, tmp_path / "logs", tmp_path / "worker_result.json",
    )

    assert result.return_code == 0
    command_record = json.loads(
        (tmp_path / "logs" / "head_segmentation_command.txt").read_text(
            encoding="utf-8"
        )
    )
    provenance = command_record["launch_provenance"]
    assert provenance["mode"] == "legacy"
    assert provenance["job_owned"] is False
    assert provenance["worker_pid"] == 424242
    assert "assign_completed" not in provenance
    assert "resume_completed" not in provenance
