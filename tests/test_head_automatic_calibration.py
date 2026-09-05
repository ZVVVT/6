"""Head automatic confirmation must use the existing final-label contract."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest
import tifffile

from app.analysis_window import AnalysisWindow
from core.analysis_v2.head_calibration_service import HeadCalibrationService
from core.analysis_v2.head_measurement_service import (
    collect_head_measurement_fields,
    prepare_standardized_head_input,
)
from core.analysis_v2.task_state import TaskStateStore
from core.analysis_v2.manifest_store import ManifestStore


def _task(root):
    root.mkdir()
    (root / "input").mkdir()
    initial_dir = root / "segmentation" / "head"
    initial_dir.mkdir(parents=True)
    TaskStateStore(root / "state.json", "test-head").initialize()
    ManifestStore(root / "manifest.json", root, "test-head").initialize()
    for field_id, count in (("001", 2), ("002", 1), ("003", 1)):
        labels = np.zeros((12, 14), dtype=np.uint16)
        if count:
            labels[1:4, 1:5] = 7
        if count == 2:
            labels[6:10, 8:12] = 513
        tifffile.imwrite(str(initial_dir / (field_id + "_HeadInitialLabels.tif")), labels)
        (initial_dir / (field_id + "_HeadInitialObjects.json")).write_text(
            json.dumps({"object_count": count}), encoding="utf-8",
        )
        for channel in ("TRITC", "FITC"):
            tifffile.imwrite(
                str(root / "input" / (field_id + "_" + channel + ".tif")),
                np.zeros((12, 14, 3), dtype=np.uint8),
            )
    return root


def _normalize(value, root):
    if isinstance(value, dict):
        return {key: _normalize(item, root) for key, item in value.items()
                if key not in ("updated_at", "created_at", "timestamp", "modified_at", "sha256", "size_bytes")}
    if isinstance(value, list):
        return [_normalize(item, root) for item in value]
    if isinstance(value, str):
        return value.replace(str(root), "TASK")
    return value


def test_automatic_outputs_match_unedited_manual_confirmation(tmp_path):
    manual_root = _task(tmp_path / "manual")
    auto_root = _task(tmp_path / "auto")
    manual = HeadCalibrationService(manual_root).complete()
    auto = HeadCalibrationService(auto_root, interactive=False).complete()
    assert auto.keys() == manual.keys() == {"state", "fields", "manifest"}
    assert auto["state"]["status"] == manual["state"]["status"] == "head_calibrated"
    assert _normalize(auto["fields"], auto_root) == _normalize(manual["fields"], manual_root)
    assert [(r["role"], r.get("metadata")) for r in auto["manifest"]["files"]] == [
        (r["role"], r.get("metadata")) for r in manual["manifest"]["files"]
    ]
    for field_id, ids in (("001", [0, 1, 2]), ("002", [0, 1]), ("003", [0, 1])):
        auto_dir = auto_root / "calibration" / "head"
        manual_dir = manual_root / "calibration" / "head"
        filename = field_id + "_HeadFinalLabels.tif"
        labels = tifffile.imread(str(auto_dir / filename))
        assert labels.dtype == np.uint16
        assert labels.shape == (12, 14)
        assert np.unique(labels).tolist() == ids
        np.testing.assert_array_equal(labels, tifffile.imread(str(manual_dir / filename)))
        for suffix in ("_HeadFinalObjects.json", "_HeadCalibrationState.json"):
            automatic_json = json.loads((auto_dir / (field_id + suffix)).read_text(encoding="utf-8"))
            manual_json = json.loads((manual_dir / (field_id + suffix)).read_text(encoding="utf-8"))
            assert _normalize(automatic_json, auto_root) == _normalize(manual_json, manual_root)
        objects = json.loads((auto_dir / (field_id + "_HeadFinalObjects.json")).read_text(encoding="utf-8"))
        assert [item["object_id"] for item in objects["objects"]] == ids[1:]
        assert all(item["source"] == "initial" for item in objects["objects"])

    manual_fields = collect_head_measurement_fields(manual_root)
    auto_fields = collect_head_measurement_fields(auto_root)
    for manual_field, auto_field in zip(manual_fields, auto_fields):
        assert manual_field.keys() == auto_field.keys()
        assert manual_field["expected_object_count"] == auto_field["expected_object_count"]
    prepare_standardized_head_input(auto_fields, auto_root / "measurement_input")
    for field in auto_fields:
        source = field["labels"]
        assert (auto_root / "measurement_input" / source.name).read_bytes() == source.read_bytes()


def test_manual_resumes_edits_and_automatic_uses_initial_labels(tmp_path):
    root = _task(tmp_path / "task")
    service = HeadCalibrationService(root)
    service.select_object("001", 2, 2)
    service.delete_selected("001")
    resumed = HeadCalibrationService(root)
    assert resumed.load_field("001").model.object_count == 1
    automatic = HeadCalibrationService(root, interactive=False)
    assert automatic.load_field("001").model.object_count == 2
    assert automatic.load_field("001").model.revision == 0


@pytest.mark.parametrize("interactive", [True, False])
@pytest.mark.parametrize("invalid", ["empty", "disconnected"])
def test_both_modes_keep_existing_label_validation(tmp_path, interactive, invalid):
    root = _task(tmp_path / "task")
    labels = np.zeros((12, 14), dtype=np.uint16)
    if invalid == "disconnected":
        labels[1:3, 1:3] = 7
        labels[7:9, 7:9] = 7
    tifffile.imwrite(str(root / "segmentation" / "head" / "001_HeadInitialLabels.tif"), labels)
    with pytest.raises(ValueError):
        HeadCalibrationService(root, interactive=interactive).complete()
    assert not (root / "calibration" / "head" / "001_HeadFinalLabels.tif").exists()


def _window(root, interactive=True, workflow="head"):
    window = SimpleNamespace(
        _shutdown_cancel_requested=False,
        current_analysis_v2_context={
            "case_no": "case", "protein_key": "protein1",
            "workflow": workflow, "interactive": interactive,
            "project_root": str(root),
        },
        head_calibration_window=None,
        head_measurement_worker=None,
        btn_run_analysis=Mock(), append_log=Mock(),
        _show_analysis_v2_error=Mock(),
        _on_head_field_calibration_completed=Mock(),
        _on_head_calibration_completed=Mock(),
        _on_head_calibration_closed=Mock(),
    )
    return window


def _segmented(window, root):
    AnalysisWindow._on_head_segmentation_finished(window, True, 1.0, {
        "task_root": str(root), "case_no": "case", "protein_key": "protein1",
    }, "")


@pytest.mark.parametrize("interactive", [True, None])
def test_interactive_and_default_still_open_original_window(tmp_path, interactive):
    window = _window(tmp_path)
    if interactive is None:
        del window.current_analysis_v2_context["interactive"]
    with patch("app.analysis_window.HeadCalibrationWindow") as factory, patch(
        "app.analysis_window.HeadCalibrationService"
    ) as service:
        _segmented(window, tmp_path)
    factory.assert_called_once_with(task_root=tmp_path.resolve(), progressive_tail=False, parent=window)
    factory.return_value.calibration_completed.connect.assert_called_once_with(window._on_head_calibration_completed)
    factory.return_value.show.assert_called_once()
    service.assert_not_called()
    window._on_head_calibration_completed.assert_not_called()
    window._show_analysis_v2_error.assert_not_called()


def test_automatic_does_not_create_window_and_passes_real_result(tmp_path):
    root = _task(tmp_path / "task")
    window = _window(root, interactive=False)
    with patch("app.analysis_window.HeadCalibrationWindow") as factory:
        _segmented(window, root)
    factory.assert_not_called()
    window._show_analysis_v2_error.assert_not_called()
    window._on_head_calibration_completed.assert_called_once()
    args, kwargs = window._on_head_calibration_completed.call_args
    assert kwargs == {"automatic": True}
    assert args[0]["state"]["status"] == "head_calibrated"
    assert len(args[0]["fields"]) == 3


def test_automatic_failure_does_not_start_measurement(tmp_path):
    window = _window(tmp_path, interactive=False)
    with patch("app.analysis_window.HeadCalibrationWindow") as factory, patch(
        "app.analysis_window.HeadCalibrationService", side_effect=ValueError("invalid labels")
    ):
        _segmented(window, tmp_path)
    factory.assert_not_called()
    window._on_head_calibration_completed.assert_not_called()
    window._show_analysis_v2_error.assert_called_once()
    assert window._analysis_v2_finish_pending is True


@pytest.mark.parametrize("interactive", [True, False])
@pytest.mark.parametrize("protein_key", ["protein1", "protein2", "protein4", "protein5"])
def test_completion_starts_same_measurement_worker(tmp_path, interactive, protein_key):
    window = _window(tmp_path, interactive=interactive)
    window.current_analysis_v2_context["protein_key"] = protein_key
    calibration_window = Mock() if interactive else None
    window.head_calibration_window = calibration_window
    window.sender = Mock(return_value=calibration_window if interactive else object())
    window.current_analysis_v2_task_root = tmp_path
    window._worker_is_running = Mock(return_value=False)
    window._analysis_context_matches_current = Mock(return_value=(True, ""))
    window.config = object()
    window._on_head_measurement_finished = Mock()
    window._on_head_measurement_thread_finished = Mock()
    result = {"state": {"status": "head_calibrated"}, "fields": [], "manifest": {}}
    with patch("app.analysis_window.HeadMeasurementWorker") as worker:
        AnalysisWindow._on_head_calibration_completed(window, result, automatic=not interactive)
    worker.assert_called_once_with(
        project_root=tmp_path.resolve(), task_root=tmp_path.resolve(), config=window.config, parent=window,
    )
    worker.return_value.start.assert_called_once()
    assert window.current_analysis_v2_context["calibration_result"] is result
    if interactive:
        calibration_window.close.assert_called_once()
    else:
        window.sender.assert_not_called()


def test_tail_automatic_confirms_head_without_window(tmp_path):
    window = _window(tmp_path, interactive=False, workflow="protein3_tail")
    with patch("app.analysis_window.HeadCalibrationWindow") as factory, patch(
        "app.analysis_window.HeadCalibrationService"
    ) as service:
        _segmented(window, tmp_path)
    factory.assert_not_called()
    service.assert_called_once_with(tmp_path.resolve(), interactive=False)
    window._on_head_calibration_completed.assert_called_once_with(
        service.return_value.complete.return_value, automatic=True,
    )
    assert window.tail_field_prepare_queue == []


def test_tail_manual_head_keeps_progressive_signals(tmp_path):
    window = _window(tmp_path, interactive=True, workflow="protein3_tail")
    with patch("app.analysis_window.HeadCalibrationWindow") as factory, patch(
        "app.analysis_window.HeadCalibrationService"
    ) as service:
        _segmented(window, tmp_path)
    factory.assert_called_once_with(task_root=tmp_path.resolve(), progressive_tail=True, parent=window)
    factory.return_value.field_calibration_completed.connect.assert_called_once_with(window._on_head_field_calibration_completed)
    factory.return_value.calibration_completed.connect.assert_called_once_with(window._on_head_calibration_completed)
    factory.return_value.calibration_closed.connect.assert_called_once_with(window._on_head_calibration_closed)
    factory.return_value.show.assert_called_once()
    service.assert_not_called()


def test_tail_automatic_head_completion_queues_existing_c18b(tmp_path):
    root = _task(tmp_path / "task")
    window = _window(root, interactive=False, workflow="protein3_tail")
    window.current_analysis_v2_task_root = root
    window._worker_is_running = Mock(return_value=False)
    window._analysis_context_matches_current = Mock(return_value=(True, ""))
    window._finish_analysis_v2_ui = Mock()
    window.sender = Mock(side_effect=AssertionError("no UI sender"))
    window._start_next_tail_field_prepare = Mock()
    window._maybe_start_tail_path_after_field_prepare = Mock()
    window._on_head_calibration_completed = lambda result, **kw: AnalysisWindow._on_head_calibration_completed(window, result, **kw)
    with patch("app.analysis_window.HeadCalibrationWindow") as factory, patch(
        "app.analysis_window.HeadMeasurementWorker"
    ) as measurement:
        _segmented(window, root)
    factory.assert_not_called()
    measurement.assert_not_called()
    window._show_analysis_v2_error.assert_not_called()
    assert window.tail_field_prepare_queue == ["001", "002", "003"]
    assert window.tail_field_order == ["001", "002", "003"]
    assert window._tail_head_calibration_finished is True
    assert window._tail_path_start_pending is True
    window._start_next_tail_field_prepare.assert_called_once()
    window._maybe_start_tail_path_after_field_prepare.assert_called_once()
