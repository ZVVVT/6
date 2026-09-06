"""Completion boundary tests using real window methods without constructing Qt UI."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.analysis_window import AnalysisWindow
from core.analysis_v2.completion import build_completion_result


def inputs(tmp_path, part):
    summary = {"success": True, "calculation_mode": "head_equivalent",
               "total": {"positive_count": 3, "sperm_count": 8,
                         "mean_intensity": 12, "mean_intensity_raw": 12.3,
                         "expression_rate": 37.5}, "rows": [{"image_number": 1}]}
    contract = {"tail_object_count": 5, "result_parser": summary if part == "tail"
                else {"image_summary": summary}}
    payload = {"measurement_output_dir": str(tmp_path / "candidate"),
               "candidate_output_dir": str(tmp_path / "candidate"),
               "measurement_result": {"validation": contract, "parsed_result": summary}}
    context = {"protein_key": "protein3", "protein_name": "P", "case_id": 2,
               "raw_image_folder": "raw", "field_count": 1,
               "target_output_dir": str(tmp_path / "published")}
    return payload, context


def page(tmp_path, part, interactive, running=False):
    payload, context = inputs(tmp_path, part)
    context["interactive"] = interactive
    window = SimpleNamespace(
        current_analysis_v2_context=context, current_analysis_v2_task_root=tmp_path,
        _shutdown_cancel_requested=False, head_measurement_worker=object(),
        _analysis_running=True,
        tail_measurement_worker=object(), _worker_is_running=Mock(return_value=running),
        _analysis_context_matches_current=Mock(return_value=(True, "")),
        database=Mock(),
        result_viewer=Mock(), append_log=Mock(), refresh_protein_status=Mock(),
        refresh_current_protein_workspace=Mock(), set_running_state=Mock(),
        select_next_unanalyzed_protein=Mock(),
    )
    window._clear_analysis_v2_state = lambda: AnalysisWindow._clear_analysis_v2_state(window)
    window._finish_analysis_v2_ui = lambda **kw: AnalysisWindow._finish_analysis_v2_ui(window, **kw)
    window._show_analysis_v2_error = lambda *args: AnalysisWindow._show_analysis_v2_error(window, *args)
    return window, payload


@pytest.mark.parametrize("part", ["head", "tail"])
@pytest.mark.parametrize("interactive", [True, False])
def test_completion_dispatch_and_manual_order(tmp_path, part, interactive):
    window, payload = page(tmp_path, part, interactive)
    events = []
    published = SimpleNamespace(
        output_dir=(tmp_path / "published").resolve(),
        summary=payload["measurement_result"]["parsed_result"],
        database_message="saved",
        cleanup_warning="",
    )
    window.result_viewer.refresh_results.side_effect = lambda: events.append("refresh")
    window.set_running_state.side_effect = lambda value: events.append("buttons")
    window.select_next_unanalyzed_protein.side_effect = lambda: events.append("next")
    with patch("app.analysis_window.publish_measured_completion", side_effect=lambda **kw: events.extend(["publish", "db", "commit"]) or published) as publisher, patch("app.analysis_window.QMessageBox") as boxes, patch("app.analysis_window.show_long_message_dialog") as errors, patch("app.analysis_window.TaskStateStore"):
        boxes.information.side_effect = lambda *args: events.append("message")
        getattr(AnalysisWindow, "_on_" + part + "_measurement_finished")(window, True, 1.2, payload, "")
    assert window.analysis_v2_completion_result["part"] == part
    errors.assert_not_called()
    if interactive:
        assert events == ["publish", "db", "commit", "refresh", "buttons", "next", "message"]
        boxes.information.assert_called_once()
    else:
        assert events == []
        assert boxes.mock_calls == []
        publisher.assert_not_called()
        window._analysis_context_matches_current.assert_not_called()
        assert window.result_viewer.mock_calls == []
        window.refresh_protein_status.assert_not_called()
        window.refresh_current_protein_workspace.assert_not_called()


@pytest.mark.parametrize("part", ["head", "tail"])
@pytest.mark.parametrize("success", [True, False])
def test_automatic_thread_cleanup_and_error(tmp_path, part, success):
    window, payload = page(tmp_path, part, False, running=True)
    worker = getattr(window, part + "_measurement_worker")
    window.sender = lambda: worker
    with patch("app.analysis_window.QMessageBox") as boxes, patch("app.analysis_window.show_long_message_dialog") as dialog, patch("app.analysis_window.publish_measured_completion") as publisher:
        getattr(AnalysisWindow, "_on_" + part + "_measurement_finished")(window, success, 1, payload, "failed")
        getattr(AnalysisWindow, "_on_" + part + "_measurement_thread_finished")(window)
    assert getattr(window, part + "_measurement_worker") is None
    assert window.current_analysis_v2_context is None
    assert window._analysis_running is False
    assert bool(window.analysis_v2_completion_result) is success
    assert bool(window.analysis_v2_completion_error) is not success
    window.set_running_state.assert_not_called()
    window.select_next_unanalyzed_protein.assert_not_called()
    assert boxes.mock_calls == []
    dialog.assert_not_called()
    publisher.assert_not_called()


@pytest.mark.parametrize("part", ["head", "tail"])
def test_result_preserves_publisher_and_database_data(tmp_path, part):
    payload, context = inputs(tmp_path, part)
    result = build_completion_result(part, payload, context, tmp_path, 2)
    assert result["measurement_contract"] == payload["measurement_result"]["validation"]
    assert result["context"] == context
    assert result["source_dir"] == (tmp_path / "candidate").resolve()
    assert result["target_dir"] == (tmp_path / "published").resolve()
    assert result["summary_result"]["rows"] == [{"image_number": 1}]
    assert result["mean_intensity_raw"] == 12.3
    assert result["expression_rate"] == 37.5
    if part == "tail":
        assert [result[key] for key in ("tail_object_count", "associated_object_count", "unresolved_object_count")] == [5, 3, 2]
    payload["measurement_result"].clear()
    context.clear()
    assert result["context"]["case_id"] == 2
    assert result["measurement_result"]["validation"]


@pytest.mark.parametrize("part", ["head", "tail"])
def test_manual_measurement_error_never_publishes(tmp_path, part):
    window, payload = page(tmp_path, part, True)
    with patch("app.analysis_window.show_long_message_dialog") as dialog, patch("app.analysis_window.QMessageBox") as boxes, patch("app.analysis_window.publish_measured_completion") as publisher:
        getattr(AnalysisWindow, "_on_" + part + "_measurement_finished")(window, False, 1, payload, "failed")
    dialog.assert_called_once()
    publisher.assert_not_called()
    assert boxes.mock_calls == []
    assert window.analysis_v2_completion_result is None


@pytest.mark.parametrize("part", ["head", "tail"])
@pytest.mark.parametrize("invalid", ["summary", "source"])
def test_invalid_automatic_payload_is_error_without_dialog(tmp_path, part, invalid):
    window, payload = page(tmp_path, part, False)
    if invalid == "summary":
        payload["measurement_result"]["parsed_result"]["success"] = False
    else:
        payload["measurement_output_dir"] = ""
        payload["candidate_output_dir"] = ""
    with patch("app.analysis_window.QMessageBox") as boxes, patch(
        "app.analysis_window.show_long_message_dialog"
    ) as dialog:
        getattr(AnalysisWindow, "_on_" + part + "_measurement_finished")(
            window, True, 1, payload, "",
        )
    assert window.analysis_v2_completion_result is None
    assert window.analysis_v2_completion_error["detail"]
    assert window._analysis_running is False
    assert boxes.mock_calls == []
    dialog.assert_not_called()


@pytest.mark.parametrize("part", ["head", "tail"])
def test_manual_delayed_thread_cleanup_preserves_next_selection(tmp_path, part):
    window, payload = page(tmp_path, part, True, running=True)
    worker = getattr(window, part + "_measurement_worker")
    window.sender = lambda: worker
    published = SimpleNamespace(
        output_dir=(tmp_path / "published").resolve(),
        summary=payload["measurement_result"]["parsed_result"],
        database_message="saved",
        cleanup_warning="",
    )
    with patch("app.analysis_window.QMessageBox") as boxes, patch(
        "app.analysis_window.publish_measured_completion", return_value=published,
    ), patch("app.analysis_window.TaskStateStore"):
        getattr(AnalysisWindow, "_on_" + part + "_measurement_finished")(
            window, True, 1, payload, "",
        )
        boxes.information.assert_called_once()
        window.set_running_state.assert_not_called()
        getattr(AnalysisWindow, "_on_" + part + "_measurement_thread_finished")(window)
    assert getattr(window, part + "_measurement_worker") is None
    window.set_running_state.assert_called_once_with(False)
    # Preserve the original head/tail thread-finished behavior exactly.
    assert window.select_next_unanalyzed_protein.call_count == (1 if part == "head" else 0)
