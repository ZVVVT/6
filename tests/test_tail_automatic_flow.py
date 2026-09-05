"""Automatic routing exercises the existing calibration and measurement boundary."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest
from PIL import Image

from app.analysis_window import AnalysisWindow
from app.analysis_v2.workflow import complete_automatic_tail_calibration
from core.analysis_v2 import tail_calibration_service as calibration
from core.analysis_v2.manifest_store import ManifestStore
from core.analysis_v2.task_paths import AnalysisTaskPaths
from core.analysis_v2.task_state import TaskStateStore
from core.analysis_v2.tail_measurement_service import (
    collect_tail_measurement_fields, prepare_standardized_tail_input,
)
from test_tail_automatic_final_contract import adapter_case


def window(root, interactive):
    result = SimpleNamespace(
        _shutdown_cancel_requested=False,
        current_analysis_v2_task_root=root,
        current_analysis_v2_context={
            "workflow": "protein3_tail", "interactive": interactive,
            "field_count": 1, "project_root": str(root),
        },
        tail_calibration_controller=None, tail_measurement_worker=None,
        btn_run_analysis=Mock(), append_log=Mock(), config=object(),
        _show_analysis_v2_error=Mock(), _finish_analysis_v2_ui=Mock(),
        _on_tail_calibration_completed=Mock(), _on_tail_calibration_aborted=Mock(),
        _on_tail_measurement_finished=Mock(), _on_tail_measurement_thread_finished=Mock(),
        _worker_is_running=Mock(return_value=False),
        _analysis_context_matches_current=Mock(return_value=(True, "")),
    )
    result._complete_tail_calibration = lambda payload: AnalysisWindow._complete_tail_calibration(result, payload)
    return result


def c18b_result(root):
    return {"workflow": "c18b_tail_editor", "tail_backend": "C18B", "fields": [{
        "field_id": "field", "fragments": str(root / "fragments.tif"),
        "head_labels": str(root / "head.tif"), "output_dir": str(root),
    }]}


@pytest.mark.parametrize("interactive", [True, None])
def test_manual_keeps_editor_signals_and_start(tmp_path, interactive):
    page = window(tmp_path, interactive)
    if interactive is None:
        del page.current_analysis_v2_context["interactive"]
    with patch("app.analysis_window.mark_tail_stage") as stage, patch(
        "app.analysis_window.C18BTailCalibrationController"
    ) as editor, patch("app.analysis_window.complete_automatic_tail_calibration") as auto:
        AnalysisWindow._on_tail_path_finished(page, True, c18b_result(tmp_path), "")
    editor.assert_called_once_with(task_root=tmp_path, field_payloads=c18b_result(tmp_path)["fields"], parent=page)
    assert editor.return_value.method_calls == [
        ("log_signal.connect", (page.append_log,), {}),
        ("calibration_completed.connect", (page._on_tail_calibration_completed,), {}),
        ("calibration_aborted.connect", (page._on_tail_calibration_aborted,), {}),
        ("start", (), {}),
    ]
    auto.assert_not_called()
    assert [call.args[1] for call in stage.call_args_list] == [
        "tail_segmented", "tail_calibration_required",
    ]
    page._show_analysis_v2_error.assert_not_called()


@pytest.mark.parametrize("failure", [None, "workset", "contract", "c18b"])
def test_automatic_order_and_failure_stop(tmp_path, failure):
    page = window(tmp_path, False)
    events = ["c18b"]

    def step(name, result):
        def call(*args, **kwargs):
            events.append(name)
            if failure == name:
                raise ValueError(name)
            return result
        return call

    with patch("app.analysis_window.mark_tail_stage"), patch(
        "app.analysis_window.C18BTailCalibrationController"
    ) as editor, patch.object(calibration, "save_initial_c18b_tail_workset", side_effect=step("workset", None)), patch.object(
        calibration, "build_automatic_tail_final_contract", side_effect=step("contract", {})
    ), patch.object(calibration, "register_tail_final_contract", side_effect=lambda payload, contract: payload), patch.object(
        calibration, "complete_tail_calibration", side_effect=lambda root, fields, **kw: {"fields": fields}
    ), patch("app.analysis_window.TailMeasurementWorker") as worker:
        worker.return_value.start.side_effect = lambda: events.append("measurement")
        AnalysisWindow._on_tail_path_finished(page, failure != "c18b", c18b_result(tmp_path), "c18b failure")
    editor.assert_not_called()
    expected = ["c18b", "workset", "contract", "measurement"]
    assert events == (expected[:expected.index(failure) + 1] if failure else expected)
    if failure:
        worker.assert_not_called()
        page._show_analysis_v2_error.assert_called_once()
        assert page._analysis_v2_finish_pending is True
    else:
        worker.assert_called_once_with(project_root=tmp_path, task_root=tmp_path, config=page.config, parent=page)
        page._show_analysis_v2_error.assert_not_called()


@pytest.mark.parametrize("total,associated", [(89, 68), (12, 0)])
def test_real_automatic_contract_enters_measurement(adapter_case, total, associated):
    case = adapter_case
    case.fixture(list(range(1, total + 1)), set(range(1, associated + 1)))
    root = case.root
    task_paths = AnalysisTaskPaths._build(root, root, "auto_test")
    TaskStateStore.from_task_paths(task_paths).initialize()
    ManifestStore.from_task_paths(task_paths).initialize()
    input_dir = root / "input"
    input_dir.mkdir()
    for channel in ("FITC", "TRITC"):
        with Image.open(case.head_path) as image:
            Image.fromarray(np.zeros(np.asarray(image).shape, dtype=np.uint8)).save(input_dir / ("field_" + channel + ".tif"))
    output = root / "calibration" / "tail" / "field"
    head_dir = root / "calibration" / "head"
    head_dir.mkdir(parents=True)
    head = head_dir / "field_HeadFinalLabels.tif"
    head.write_bytes(case.head_path.read_bytes())
    fields = [{"field_id": "field", "fragments": str(case.adapter / "fragments.tif"),
               "head_labels": str(head), "output_dir": str(output)}]
    with patch.object(calibration, "build_initial_c18b_tail_workset", wraps=calibration.build_initial_c18b_tail_workset) as builder:
        result = complete_automatic_tail_calibration(root, fields)
    builder.assert_called_once_with(case.adapter, head)
    assert result["state"]["status"] == "tail_calibrated"
    assert result["ready_for_measurement"] is True
    assert result["manual_calibration_completed"] is False
    assert {row["role"] for row in result["manifest"]["files"]} == {
        "tail_final_labels", "tail_final_head_id_labels", "tail_positive_head_labels", "tail_final_objects",
    }
    measured = collect_tail_measurement_fields(root)
    assert tuple(measured[0][key] for key in (
        "tail_object_count", "associated_object_count", "unresolved_object_count"
    )) == (total, associated, total - associated)
    prepare_standardized_tail_input(measured, root / "measurement_input")
    assert (root / "measurement_input" / "field_TailFinalLabels.tif").is_file()
    # Zero positive labels are legal only for an all-unresolved contract.
    with Image.open(head) as image:
        invalid = np.zeros(np.asarray(image).shape, dtype=np.uint16)
    if not associated:
        invalid[0, 0] = 1
    Image.fromarray(invalid).save(output / "field_TailPositiveHeadLabels.tif")
    with pytest.raises(ValueError):
        collect_tail_measurement_fields(root)


def test_manual_completion_delegates_same_business_entry(tmp_path):
    page = window(tmp_path, True)
    page.tail_calibration_controller = object()
    page._complete_tail_calibration = Mock()
    payload = c18b_result(tmp_path)
    AnalysisWindow._on_tail_calibration_completed(page, payload)
    assert page.tail_calibration_controller is None
    page._complete_tail_calibration.assert_called_once_with(payload)
