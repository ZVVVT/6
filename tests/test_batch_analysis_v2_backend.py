"""Phase 4-B Step 3: Batch 单蛋白执行边界切换到 Analysis V2。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import batch_analysis_dialog as batch
from core.analysis_v2.batch_input_adapter import AnalysisV2BatchInputError
from core.analysis_v2.task_runner import AnalysisV2TaskCancelled, AnalysisV2TaskError


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def task(protein_key, folder):
    names = {
        "protein1": "Q9BYW3",
        "protein2": "P10323",
        "protein3": "Q96P56",
        "protein4": "Q8IYV9",
        "protein5": "W5XKT8",
    }
    return {
        "protein_key": protein_key,
        "protein_name": names[protein_key],
        "folder": folder,
    }


def measured(request):
    return {
        "status": "measured",
        "protein_key": request.protein_key,
        "part": request.protein_part,
    }


def published(request):
    return SimpleNamespace(
        protein_key=request.protein_key,
        database_message="{} 已发布".format(request.protein_key),
        cleanup_warning="",
    )


def install_success_backend(monkeypatch, events, parts=None):
    parts = parts or {}
    runners = []

    def adapter(case_data, protein_key, protein_folder, config):
        events.append(("adapter", protein_key, protein_folder, case_data["id"]))
        return SimpleNamespace(
            case_no=case_data["case_no"],
            case_id=case_data["id"],
            protein_key=protein_key,
            protein_part=parts.get(protein_key, "tail" if protein_key == "protein3" else "head"),
            raw_image_folder=str(protein_folder),
        )

    class Runner:
        def __init__(self, config, log_callback=None):
            self.cancelled = False
            self.shutdown_called = False
            runners.append(self)

        def run(self, request):
            events.append(("run", request.protein_key, request.protein_part))
            return measured(request)

        def cancel(self):
            self.cancelled = True
            events.append(("cancel",))

        def shutdown(self):
            self.shutdown_called = True
            events.append(("shutdown",))
            return True

    def publisher(completion, database):
        events.append(("publish", completion["protein_key"], database))
        request = SimpleNamespace(protein_key=completion["protein_key"])
        return published(request)

    monkeypatch.setattr(batch, "build_batch_task_request", adapter)
    monkeypatch.setattr(batch, "AnalysisV2TaskRunner", Runner)
    monkeypatch.setattr(batch, "publish_measured_completion", publisher)
    return runners


@pytest.mark.parametrize(
    "protein_key,expected_part",
    [
        ("protein1", "head"),
        ("protein3", "tail"),
        ("protein2", "head"),
        ("protein4", "head"),
        ("protein5", "head"),
    ],
)
def test_each_formal_protein_uses_adapter_runner_then_publish(
    tmp_path, monkeypatch, protein_key, expected_part,
):
    events = []
    runners = install_success_backend(monkeypatch, events)
    folder = tmp_path / protein_key
    worker = batch.BatchProteinWorker(
        {"id": 17, "case_no": "CASE001"}, [task(protein_key, folder)], object(), "database",
    )
    statuses = []
    worker.task_status_signal.connect(lambda key, status: statuses.append((key, status)))

    worker.run()

    assert events[:3] == [
        ("adapter", protein_key, folder, 17),
        ("run", protein_key, expected_part),
        ("publish", protein_key, "database"),
    ]
    assert statuses == [(protein_key, "分析中"), (protein_key, "已完成")]
    assert runners[0].shutdown_called
    assert worker.current_runner is None


def test_multiple_proteins_remain_strictly_sequential(tmp_path, monkeypatch):
    events = []
    install_success_backend(monkeypatch, events)
    worker = batch.BatchProteinWorker(
        {"id": 17, "case_no": "CASE001"},
        [task("protein1", tmp_path / "one"), task("protein3", tmp_path / "three")],
        object(),
        "database",
    )

    worker.run()

    significant = [item[:2] for item in events if item[0] in ("run", "publish")]
    assert significant == [
        ("run", "protein1"),
        ("publish", "protein1"),
        ("run", "protein3"),
        ("publish", "protein3"),
    ]


def test_measured_is_required_before_publish(tmp_path, monkeypatch):
    events = []
    install_success_backend(monkeypatch, events)

    class WrongStatusRunner(batch.AnalysisV2TaskRunner):
        def run(self, request):
            events.append(("run", request.protein_key, request.protein_part))
            return {"status": "completed", "protein_key": request.protein_key}

    monkeypatch.setattr(batch, "AnalysisV2TaskRunner", WrongStatusRunner)
    worker = batch.BatchProteinWorker(
        {"id": 17, "case_no": "CASE001"}, [task("protein1", tmp_path)], object(), object(),
    )
    finished = []
    worker.finished_signal.connect(lambda results, errors: finished.append((results, errors)))

    worker.run()

    assert not any(item[0] == "publish" for item in events)
    assert finished[0][0] == []
    assert finished[0][1][0]["kind"] == "failed"


def test_adapter_error_does_not_start_runner(tmp_path, monkeypatch):
    runner = Mock()

    def fail_adapter(**kwargs):
        raise AnalysisV2BatchInputError("bad input", "protein1", tmp_path)

    monkeypatch.setattr(batch, "build_batch_task_request", fail_adapter)
    monkeypatch.setattr(batch, "AnalysisV2TaskRunner", runner)
    worker = batch.BatchProteinWorker(
        {"id": 17, "case_no": "CASE001"}, [task("protein1", tmp_path)], object(), object(),
    )
    finished = []
    worker.finished_signal.connect(lambda results, errors: finished.append((results, errors)))

    worker.run()

    runner.assert_not_called()
    assert finished[0][1][0]["message"].startswith("输入错误：")


def test_runner_failure_does_not_publish(tmp_path, monkeypatch):
    events = []
    runners = install_success_backend(monkeypatch, events)

    class FailedRunner(batch.AnalysisV2TaskRunner):
        def run(self, request):
            raise AnalysisV2TaskError("boom", stage="head_segmentation")

    publisher = Mock()
    monkeypatch.setattr(batch, "AnalysisV2TaskRunner", FailedRunner)
    monkeypatch.setattr(batch, "publish_measured_completion", publisher)
    worker = batch.BatchProteinWorker(
        {"id": 17, "case_no": "CASE001"}, [task("protein1", tmp_path)], object(), object(),
    )

    worker.run()

    publisher.assert_not_called()
    assert runners[0].shutdown_called
    assert worker.current_runner is None


@pytest.mark.parametrize("stage,label", [("publication", "发布失败"), ("database", "数据库失败")])
def test_publisher_or_database_failure_marks_protein_failed(
    tmp_path, monkeypatch, stage, label,
):
    events = []
    runners = install_success_backend(monkeypatch, events)

    def fail_publish(completion, database):
        raise batch.AnalysisV2CompletionPublishError(
            "write failed", stage=stage, completion=completion,
        )

    monkeypatch.setattr(batch, "publish_measured_completion", fail_publish)
    worker = batch.BatchProteinWorker(
        {"id": 17, "case_no": "CASE001"}, [task("protein1", tmp_path)], object(), object(),
    )
    statuses = []
    finished = []
    worker.task_status_signal.connect(lambda key, status: statuses.append(status))
    worker.finished_signal.connect(lambda results, errors: finished.append((results, errors)))

    worker.run()

    assert statuses[-1] == "失败"
    assert finished[0][0] == []
    assert finished[0][1][0]["message"].startswith(label + "：")
    assert runners[0].shutdown_called
    assert worker.current_runner is None


def test_cancel_calls_current_runner_and_stops_following_protein(tmp_path, monkeypatch):
    events = []
    runners = install_success_backend(monkeypatch, events)
    original_publish = batch.publish_measured_completion
    worker = batch.BatchProteinWorker(
        {"id": 17, "case_no": "CASE001"},
        [task("protein1", tmp_path / "one"), task("protein3", tmp_path / "three")],
        object(),
        object(),
    )

    def publish_then_cancel(completion, database):
        value = original_publish(completion, database)
        worker.request_cancel_after_current()
        return value

    monkeypatch.setattr(batch, "publish_measured_completion", publish_then_cancel)
    statuses = []
    finished = []
    worker.task_status_signal.connect(lambda key, status: statuses.append((key, status)))
    worker.finished_signal.connect(lambda results, errors: finished.append((results, errors)))

    worker.run()

    assert runners[0].cancelled
    assert not any(item[:2] == ("run", "protein3") for item in events)
    assert ("protein3", "已取消") in statuses
    assert finished[0][1][0]["kind"] == "cancelled"


def test_runner_cancelled_is_not_reported_as_failed(tmp_path, monkeypatch):
    events = []
    runners = install_success_backend(monkeypatch, events)

    class CancelledRunner(batch.AnalysisV2TaskRunner):
        def run(self, request):
            raise AnalysisV2TaskCancelled("cancelled", stage="head_segmentation")

    monkeypatch.setattr(batch, "AnalysisV2TaskRunner", CancelledRunner)
    worker = batch.BatchProteinWorker(
        {"id": 17, "case_no": "CASE001"}, [task("protein1", tmp_path)], object(), object(),
    )
    statuses = []
    finished = []
    worker.task_status_signal.connect(lambda key, status: statuses.append(status))
    worker.finished_signal.connect(lambda results, errors: finished.append((results, errors)))

    worker.run()

    assert statuses == ["分析中", "已取消"]
    assert finished[0][1][0]["kind"] == "cancelled"
    assert runners[0].shutdown_called
    assert worker.current_runner is None


def test_progress_and_error_aggregation_are_preserved(tmp_path, monkeypatch):
    events = []
    install_success_backend(monkeypatch, events)
    calls = {"count": 0}

    class OneFailureRunner(batch.AnalysisV2TaskRunner):
        def run(self, request):
            calls["count"] += 1
            if calls["count"] == 1:
                raise AnalysisV2TaskError("first failed", stage="head_segmentation")
            return measured(request)

    monkeypatch.setattr(batch, "AnalysisV2TaskRunner", OneFailureRunner)
    worker = batch.BatchProteinWorker(
        {"id": 17, "case_no": "CASE001"},
        [task("protein1", tmp_path / "one"), task("protein3", tmp_path / "three")],
        object(),
        object(),
    )
    progress = []
    finished = []
    worker.progress_signal.connect(lambda index, total, name: progress.append((index, total, name)))
    worker.finished_signal.connect(lambda results, errors: finished.append((results, errors)))

    worker.run()

    assert progress == [(1, 2, "Q9BYW3"), (2, 2, "Q96P56")]
    assert len(finished[0][0]) == 1
    assert len(finished[0][1]) == 1


def test_dialog_completion_does_not_repeat_legacy_database_save(monkeypatch):
    monkeypatch.setattr(batch, "show_batch_information", Mock())
    harness = SimpleNamespace(
        save_result_to_database=Mock(side_effect=AssertionError("legacy database save")),
        set_running_state=Mock(),
        progress_bar=Mock(),
        progress_label=Mock(),
        batch_finished=Mock(),
    )
    batch.BatchAnalysisDialog.on_finished(harness, [object()], [])
    harness.save_result_to_database.assert_not_called()


def test_transition_code_and_pipelines_remain_but_new_boundary_does_not_call_them():
    source = (PROJECT_ROOT / "app" / "batch_analysis_dialog.py").read_text(encoding="utf-8")
    service_source = (PROJECT_ROOT / "core" / "protein_analysis_service.py").read_text(encoding="utf-8")
    parser_source = (PROJECT_ROOT / "core" / "result_parser.py").read_text(encoding="utf-8")
    worker_source = source[
        source.index("class BatchProteinWorker"):
        source.index("class BatchAnalysisDialog")
    ]

    assert (PROJECT_ROOT / "core" / "protein_analysis_service.py").is_file()
    assert (PROJECT_ROOT / "core" / "result_parser.py").is_file()
    assert (PROJECT_ROOT / "pipelines" / "pipeline_head.cppipe").is_file()
    assert (PROJECT_ROOT / "pipelines" / "pipeline_tail.cppipe").is_file()
    assert "class ProteinAnalysisService" in service_source
    assert "def run_one_protein" in service_source
    assert "class ResultParser" in parser_source
    assert "ProteinAnalysisService" not in worker_source
    assert "ResultParser" not in worker_source
    assert "pipeline_head.cppipe" not in worker_source
    assert "pipeline_tail.cppipe" not in worker_source
    assert "HeadCalibrationWindow" not in worker_source
    assert "Tail" + "CalibrationWindow" not in worker_source
    assert worker_source.count("class BatchProteinWorker(QThread)") == 1
    assert "= QThread(" not in worker_source


def test_batch_folder_alias_and_manual_matching_implementation_remain():
    source = (PROJECT_ROOT / "app" / "batch_analysis_dialog.py").read_text(encoding="utf-8")
    assert 'SECTION_NAME = "BatchFolderAliases"' in source
    assert "def match_folder_to_keys" in source
    assert "def on_folder_combo_changed" in source
    assert "def save_current_mapping" in source
