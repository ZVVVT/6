"""Tests for the Qt-free measured-result publication boundary."""

import inspect
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from core.analysis_v2.result_completion_service import (
    AnalysisV2CompletionPublishError,
    publish_measured_completion,
)


def completion(tmp_path, part="head", tail_count=77, associated_count=65):
    summary = {
        "success": True,
        "calculation_mode": "head_equivalent",
        "total": {
            "field_count": 2,
            "sperm_count": 90,
            "positive_count": associated_count,
            "mean_intensity": 12.4,
            "mean_intensity_raw": 12.45,
            "expression_rate": 72.22,
        },
        "rows": [{
            "image_number": "001",
            "sperm_count": 40,
            "positive_count": 30,
            "mean_intensity": 11.5,
            "expression_rate": 75.0,
        }],
        "image_csv": "Image.csv",
    }
    value = {
        "status": "measured",
        "part": part,
        "protein_key": "protein3" if part == "tail" else "protein1",
        "protein_name": "Q96P56" if part == "tail" else "Q9BYW3",
        "task_root": str(tmp_path / "task"),
        "source_dir": tmp_path / "candidate",
        "target_dir": tmp_path / "formal",
        "expected_field_count": 2,
        "measurement_contract": {
            "tail_object_count": tail_count,
            "result_parser": summary,
        },
        "context": {
            "case_id": 7,
            "case_no": "CASE-001",
            "protein_key": "protein3" if part == "tail" else "protein1",
            "protein_name": "Q96P56" if part == "tail" else "Q9BYW3",
            "raw_image_folder": "raw-images",
        },
        "tail_object_count": tail_count if part == "tail" else None,
        "associated_object_count": associated_count if part == "tail" else None,
        "unresolved_object_count": tail_count - associated_count if part == "tail" else None,
    }
    return value, summary


def publication(summary, cleanup_warning=""):
    value = Mock()
    value.summary = summary
    value.commit.return_value = cleanup_warning
    return value


@pytest.mark.parametrize("part", ["head", "tail"])
def test_measured_completion_publishes_and_atomically_saves(tmp_path, part):
    value, summary = completion(tmp_path, part)
    published = publication(summary)
    database = Mock()
    database.replace_protein_analysis_with_fields.return_value = 19

    with patch(
        "core.analysis_v2.result_completion_service.stage_{}_measurement_output".format(part),
        return_value=published,
    ) as publisher:
        result = publish_measured_completion(value, database)

    assert result.analysis_id == 19
    assert result.part == part
    assert result.summary == summary
    assert result.output_dir == (tmp_path / "formal").resolve()
    publisher.assert_called_once()
    database.replace_protein_analysis_with_fields.assert_called_once()
    published.commit.assert_called_once_with()
    published.rollback.assert_not_called()


@pytest.mark.parametrize(
    "key,value",
    [("status", "completed"), ("part", "body")],
)
def test_invalid_completion_is_rejected_before_publication(tmp_path, key, value):
    measured, _summary = completion(tmp_path)
    measured[key] = value
    database = Mock()
    with pytest.raises(AnalysisV2CompletionPublishError) as error:
        publish_measured_completion(measured, database)
    assert error.value.stage == "validation"
    assert error.value.cause is not None
    database.replace_protein_analysis_with_fields.assert_not_called()


@pytest.mark.parametrize("part", ["head", "tail"])
def test_publication_summary_defines_database_summary_and_fields(tmp_path, part):
    value, candidate_summary = completion(tmp_path, part)
    formal_summary = dict(candidate_summary)
    formal_summary["total"] = dict(candidate_summary["total"], sperm_count=91)
    formal_summary["rows"] = [dict(candidate_summary["rows"][0], sperm_count=41)]
    published = publication(formal_summary)
    database = Mock()

    with patch(
        "core.analysis_v2.result_completion_service.stage_{}_measurement_output".format(part),
        return_value=published,
    ):
        result = publish_measured_completion(value, database)

    saved = database.replace_protein_analysis_with_fields.call_args.kwargs
    assert saved["total_sperm_count"] == 91
    assert saved["field_results"][0]["sperm_count"] == 41
    assert saved["field_results"][0]["csv_path"] == "Image.csv"
    assert result.field_rows == saved["field_results"]


@pytest.mark.parametrize(
    "tail_count,associated_count,unresolved_count",
    [(77, 65, 12), (12, 0, 12)],
)
def test_tail_counts_remain_distinct_and_legal(
    tmp_path, tail_count, associated_count, unresolved_count,
):
    value, summary = completion(tmp_path, "tail", tail_count, associated_count)
    published = publication(summary)
    database = Mock()
    with patch(
        "core.analysis_v2.result_completion_service.stage_tail_measurement_output",
        return_value=published,
    ) as publisher:
        result = publish_measured_completion(value, database)

    contract = publisher.call_args.kwargs["measurement_contract"]
    saved = database.replace_protein_analysis_with_fields.call_args.kwargs
    assert contract["tail_object_count"] == tail_count
    assert saved["positive_count"] == associated_count
    assert result.tail_object_count == tail_count
    assert result.associated_object_count == associated_count
    assert result.unresolved_object_count == unresolved_count


def test_database_failure_rolls_back_publication_and_preserves_cause(tmp_path):
    value, summary = completion(tmp_path)
    published = publication(summary)
    cause = RuntimeError("database failed")
    database = Mock()
    database.replace_protein_analysis_with_fields.side_effect = cause
    with patch(
        "core.analysis_v2.result_completion_service.stage_head_measurement_output",
        return_value=published,
    ):
        with pytest.raises(AnalysisV2CompletionPublishError) as error:
            publish_measured_completion(value, database)
    assert error.value.stage == "database"
    assert error.value.cause is cause
    assert error.value.__cause__ is cause
    assert error.value.case_id == 7
    assert error.value.protein_key == "protein1"
    published.rollback.assert_called_once_with()
    published.commit.assert_not_called()


def test_publisher_failure_never_writes_database(tmp_path):
    value, _summary = completion(tmp_path)
    cause = RuntimeError("publisher failed")
    database = Mock()
    with patch(
        "core.analysis_v2.result_completion_service.stage_head_measurement_output",
        side_effect=cause,
    ):
        with pytest.raises(AnalysisV2CompletionPublishError) as error:
            publish_measured_completion(value, database)
    assert error.value.stage == "publication"
    assert error.value.cause is cause
    database.replace_protein_analysis_with_fields.assert_not_called()


def test_precommit_validation_failure_rolls_back(tmp_path):
    value, summary = completion(tmp_path, "tail")
    invalid_summary = dict(summary, calculation_mode="legacy")
    published = publication(invalid_summary)
    database = Mock()
    with patch(
        "core.analysis_v2.result_completion_service.stage_tail_measurement_output",
        return_value=published,
    ):
        with pytest.raises(AnalysisV2CompletionPublishError):
            publish_measured_completion(value, database)
    published.rollback.assert_called_once_with()
    database.replace_protein_analysis_with_fields.assert_not_called()


def test_cleanup_warning_is_success_after_database_commit(tmp_path):
    value, summary = completion(tmp_path)
    published = publication(summary, "backup cleanup warning")
    database = Mock()
    with patch(
        "core.analysis_v2.result_completion_service.stage_head_measurement_output",
        return_value=published,
    ):
        result = publish_measured_completion(value, database)
    assert result.cleanup_warning == "backup cleanup warning"
    published.rollback.assert_not_called()


def test_only_atomic_database_api_is_used(tmp_path):
    value, summary = completion(tmp_path)
    published = publication(summary)
    database = Mock()
    with patch(
        "core.analysis_v2.result_completion_service.stage_head_measurement_output",
        return_value=published,
    ):
        publish_measured_completion(value, database)
    database.replace_protein_analysis_with_fields.assert_called_once()
    database.save_protein_analysis.assert_not_called()
    database.save_field_result.assert_not_called()


def test_publication_database_commit_order_is_preserved(tmp_path):
    value, summary = completion(tmp_path)
    events = []
    published = publication(summary)
    published.commit.side_effect = lambda: events.append("commit") or ""
    database = Mock()
    database.replace_protein_analysis_with_fields.side_effect = (
        lambda **_kwargs: events.append("database") or 1
    )
    with patch(
        "core.analysis_v2.result_completion_service.stage_head_measurement_output",
        side_effect=lambda **_kwargs: events.append("publication") or published,
    ):
        publish_measured_completion(value, database)
    assert events == ["publication", "database", "commit"]


def test_service_has_no_qt_or_window_dependency():
    from core.analysis_v2 import result_completion_service as service

    source = inspect.getsource(service)
    for forbidden in ("PySide6", "QWidget", "QMessageBox", "AnalysisWindow", "ResultViewer", "BatchDialog"):
        assert forbidden not in source
