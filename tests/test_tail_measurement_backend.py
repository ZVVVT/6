from pathlib import Path
from types import SimpleNamespace

import pytest

from core.analysis_v2 import tail_measurement_service as module
from core.analysis_v2.lightweight_tail_measurement import LightweightGenerationError
from core.config_manager import ConfigManager


def _service(tmp_path, monkeypatch, backend):
    pipeline = tmp_path / "measure.cppipe"
    pipeline.write_text("test", encoding="utf-8")
    task = tmp_path / "task"
    task.mkdir()
    (task / "state.json").write_text('{"task_id":"test"}', encoding="utf-8")
    service = module.TailMeasurementService(
        task, pipeline, tmp_path, tmp_path / "python.exe",
        backend=backend,
    )
    service.state = SimpleNamespace(
        load=lambda: {"status": "tail_calibrated", "history": []},
        update=lambda *args: {"status": args[0]},
        mark_failed=lambda *args: None,
    )
    service.logger = SimpleNamespace(info=lambda *args: None, event=lambda *args, **kwargs: None,
                                     record_exception=lambda *args: None)
    service.manifest = SimpleNamespace(add_file=lambda *args: None)
    monkeypatch.setattr(module, "collect_tail_measurement_fields",
                        lambda root: [{"field_id": "a"}])
    monkeypatch.setattr(module, "prepare_standardized_tail_input",
                        lambda fields, directory: {"records": [{"field_id": "a"}]})
    monkeypatch.setattr(module, "validate_standardized_tail_fields",
                        lambda *args: None)

    class Environment:
        def __init__(self, paths, **kwargs):
            self.environment_path = paths.task_root / "environment.json"

        def write(self):
            self.environment_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(module, "EnvironmentSnapshotWriter", Environment)

    def validate(output_dir, fields, result_path, timing_callback=None):
        output = Path(output_dir)
        result_path.write_text("{}", encoding="utf-8")
        return {"image_csv_path": str(output / "Image.csv"),
                "g_objects_csv_path": str(output / "G_objects.csv"),
                "overlay_paths": [], "result_parser": {"success": True}}

    monkeypatch.setattr(module, "validate_tail_measurement_output", validate)
    return service


def _run_result():
    return SimpleNamespace(command="real", return_code=0, elapsed_seconds=0.1,
                           command_file=None, log_file=None, success=True,
                           error_message="")


def test_auto_lightweight_success_skips_mvimageid(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, "auto")
    calls = []

    def generate(records, output, *args):
        calls.append("lightweight")
        output.mkdir()
        (output / "Image.csv").write_text("csv")
        (output / "G_objects.csv").write_text("csv")
        return {"runtime": "test", "generation_seconds": 0.1, "field_count": 1}

    monkeypatch.setattr(module, "generate_tail_measurement", generate)
    service.runner.run = lambda **kwargs: pytest.fail("MvImageID must not run")
    result = service.run()
    assert calls == ["lightweight"]
    assert result["run"]["backend"] == "lightweight"
    assert service.output_dir.name == "lightweight"


def test_mvimageid_direct_skips_lightweight(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, "mvimageid")
    monkeypatch.setattr(module, "generate_tail_measurement",
                        lambda *args: pytest.fail("Lightweight must not run"))
    calls = []

    def runner(**kwargs):
        calls.append(Path(kwargs["output_dir"]))
        return _run_result()

    service.runner.run = runner
    result = service.run()
    assert len(calls) == 1
    assert calls[0].name == "mvimageid"
    assert result["run"]["backend"] == "mvimageid"


def test_eligible_failure_falls_back_once_to_isolated_output(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, "auto")
    failed = []

    def generate(records, output, *args):
        output.mkdir()
        (output / "partial.txt").write_text("partial")
        failed.append(output)
        raise LightweightGenerationError("injected array failure")

    monkeypatch.setattr(module, "generate_tail_measurement", generate)
    calls = []

    def runner(**kwargs):
        calls.append(Path(kwargs["output_dir"]))
        return _run_result()

    service.runner.run = runner
    result = service.run()
    assert len(calls) == 1
    assert calls[0] != failed[0] and calls[0].parent == failed[0].parent
    assert "partial.txt" not in {path.name for path in calls[0].iterdir()}
    assert result["run"]["fallback_reason"]["type"] == "LightweightGenerationError"


@pytest.mark.parametrize("error", [OSError("disk"), MemoryError(), ValueError("input")])
def test_noneligible_failure_does_not_fallback(tmp_path, monkeypatch, error):
    service = _service(tmp_path, monkeypatch, "auto")
    monkeypatch.setattr(module, "generate_tail_measurement",
                        lambda *args: (_ for _ in ()).throw(error))
    service.runner.run = lambda **kwargs: pytest.fail("unexpected fallback")
    with pytest.raises(type(error)):
        service.run()


def test_unknown_backend_rejected_before_generation(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, "typo")
    with pytest.raises(ValueError, match="tail_measurement_backend"):
        service.run()


def test_hidden_config_defaults_and_rejects_unknown(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text("[AnalysisV2]\nworkspace_subdir = analysis_v2\n", encoding="utf-8")
    config = ConfigManager(str(config_path))
    assert config.get_tail_measurement_backend() == "auto"
    config.set("AnalysisV2", "tail_measurement_backend", "mvimageid")
    assert config.get_tail_measurement_backend() == "mvimageid"
    config.set("AnalysisV2", "tail_measurement_backend", "unknown")
    with pytest.raises(ValueError, match="tail_measurement_backend"):
        config.get_tail_measurement_backend()


def test_validator_failure_is_terminal(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, "auto")

    def generate(records, output, *args):
        output.mkdir()
        return {"runtime": "test", "generation_seconds": 0.1, "field_count": 1}

    monkeypatch.setattr(module, "generate_tail_measurement", generate)
    monkeypatch.setattr(module, "validate_tail_measurement_output",
                        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("ResultParser failed")))
    service.runner.run = lambda **kwargs: pytest.fail("validator cannot trigger fallback")
    with pytest.raises(ValueError, match="ResultParser failed"):
        service.run()


def test_fallback_engine_failure_is_terminal_once(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, "auto")
    monkeypatch.setattr(module, "generate_tail_measurement",
                        lambda *args: (_ for _ in ()).throw(LightweightGenerationError("engine")))
    calls = []

    def runner(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(command="real", return_code=4, elapsed_seconds=0.1,
                               command_file=None, log_file=None, success=False,
                               error_message="MvImageID failed")

    service.runner.run = runner
    with pytest.raises(RuntimeError, match="MvImageID failed"):
        service.run()
    assert len(calls) == 1


def test_cancel_does_not_fallback(tmp_path, monkeypatch):
    from core.analysis_v2.task_process_context import TaskProcessCancelled

    service = _service(tmp_path, monkeypatch, "auto")
    monkeypatch.setattr(module, "generate_tail_measurement",
                        lambda *args: (_ for _ in ()).throw(TaskProcessCancelled("cancel")))
    service.runner.run = lambda **kwargs: pytest.fail("cancel cannot trigger fallback")
    context = SimpleNamespace(check_cancelled=lambda: None)
    with pytest.raises(TaskProcessCancelled):
        service.run(process_context=context)
