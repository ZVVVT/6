"""Formal MvImageID Measurement launcher ownership tests."""

import io
import json
import subprocess

import pytest

from core.mvimageid_runner import MvImageIDRunner
from core.analysis_v2.task_process_spawn import AtomicProcessSpawnError


class _CompletedProcess:
    pid = 616161

    def __init__(self, stdout="MvImageID output\n"):
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


def _runner_inputs(tmp_path, pipeline_name):
    source = tmp_path / "MvImageID"
    python = source / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    pipeline = tmp_path / pipeline_name
    pipeline.write_text("pipeline", encoding="utf-8")
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output_dir = tmp_path / "output"
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    return (MvImageIDRunner(str(source), str(python), plugins_directory=str(plugins)),
            pipeline, input_dir, output_dir, plugins, source, python)


@pytest.mark.parametrize(("pipeline_name", "stage"), [
    ("measure_head_from_labels.cppipe", "head_measurement"),
    ("measure_tail_from_labels.cppipe", "tail_measurement"),
])
def test_measurement_context_uses_atomic_launcher_preserves_contract_and_reaps(
        tmp_path, monkeypatch, pipeline_name, stage):
    runner, pipeline, input_dir, output_dir, plugins, source, python = _runner_inputs(
        tmp_path, pipeline_name)
    context = _AtomicContext()
    startupinfo = object()
    monkeypatch.setattr(runner, "_get_subprocess_window_options", lambda: {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    })
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("context path must not invoke Popen")))
    monkeypatch.setattr("core.mvimageid_runner.analysis_process_registry.register",
                        lambda process: (_ for _ in ()).throw(
                            AssertionError("atomic process registered twice")))

    result = runner.run(str(pipeline), str(input_dir), str(output_dir),
                        process_context=context, field_id="field-001")

    assert result.success and result.return_code == 0
    assert result.output_text == "MvImageID output"
    assert context.process.wait_calls == 1
    assert context.unregistered == [context.process]
    command, kwargs = context.spawn_calls[0]
    assert command[-2:] == ["--plugins-directory", str(plugins.resolve())]
    assert kwargs["cwd"] == str(source.resolve())
    assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert kwargs["env"]["PATH"].split(";")[0] == str(python.parent.resolve())
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["text"] is True
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert kwargs["startupinfo"] is startupinfo
    receipt = next(json.loads(line.split("=", 1)[1]) for line in
                   result.log_file.read_text(encoding="utf-8").splitlines()
                   if line.startswith("MvImageID launch_provenance="))
    assert receipt == {
        "stage": stage, "field_id": "field-001", "pipeline": pipeline_name,
        "mode": "atomic", "worker_pid": 616161,
        "worker_python": str(python.resolve()), "job_owned": True,
        "assign_completed": True, "resume_completed": True,
    }


def test_atomic_spawn_failure_has_no_popen_fallback_or_success_receipt(tmp_path, monkeypatch):
    runner, pipeline, input_dir, output_dir, unused_plugins, unused_source, unused_python = _runner_inputs(
        tmp_path, "measure_head_from_labels.cppipe")

    class FailingContext(_AtomicContext):
        def spawn_atomic(self, command, **kwargs):
            raise AtomicProcessSpawnError("assignment failed")

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("silent legacy fallback")))
    result = runner.run(str(pipeline), str(input_dir), str(output_dir),
                        process_context=FailingContext())
    assert not result.success
    assert "assignment failed" in result.error_message
    assert "launch_provenance" not in result.log_file.read_text(encoding="utf-8")


def test_no_context_keeps_legacy_popen_and_legacy_receipt(tmp_path, monkeypatch):
    runner, pipeline, input_dir, output_dir, unused_plugins, unused_source, unused_python = _runner_inputs(
        tmp_path, "pipeline_qc.cppipe")
    process = _CompletedProcess()
    registered = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr("core.mvimageid_runner.analysis_process_registry.register",
                        lambda value: registered.append(value) or value)
    monkeypatch.setattr("core.mvimageid_runner.analysis_process_registry.unregister",
                        lambda value: registered.remove(value))
    result = runner.run(str(pipeline), str(input_dir), str(output_dir))
    assert result.success and registered == []
    receipt = next(json.loads(line.split("=", 1)[1]) for line in
                   result.log_file.read_text(encoding="utf-8").splitlines()
                   if line.startswith("MvImageID launch_provenance="))
    assert receipt["mode"] == "legacy"
    assert receipt["job_owned"] is False
    assert "assign_completed" not in receipt
    assert "resume_completed" not in receipt
