"""Publish a measured Analysis V2 completion and atomically save its database rows."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .head_result_publisher import stage_head_measurement_output
from .tail_result_publisher import stage_tail_measurement_output


class AnalysisV2CompletionPublishError(RuntimeError):
    """A measured completion could not be published as a formal result."""

    def __init__(self, message, *, stage, completion=None, cause=None):
        super().__init__(message)
        value = dict(completion or {})
        context = dict(value.get("context") or {})
        self.message = str(message)
        self.stage = stage
        self.case_id = context.get("case_id")
        self.case_no = context.get("case_no")
        self.protein_key = value.get("protein_key") or context.get("protein_key")
        self.part = value.get("part")
        self.task_root = str(value.get("task_root") or "")
        self.source_dir = str(value.get("source_dir") or "")
        self.target_dir = str(value.get("target_dir") or "")
        self.cause = cause
        self.rollback_error = None


@dataclass(frozen=True)
class PublishedAnalysisV2Completion:
    analysis_id: Any
    protein_key: str
    part: str
    summary: Dict[str, Any]
    field_rows: List[Dict[str, Any]]
    output_dir: Path
    database_message: str
    cleanup_warning: str
    tail_object_count: Any = None
    associated_object_count: Any = None
    unresolved_object_count: Any = None


def _format_int_for_display(value):
    try:
        return str(int(round(float(value))))
    except Exception:
        return str(value)


def _format_rate_for_display(value):
    try:
        return "{:.2f}%".format(float(value))
    except Exception:
        return "{}%".format(value) if value not in (None, "") else "0.00%"


def _build_field_rows(summary):
    image_csv = str(summary.get("image_csv", "") or "")
    field_rows = []
    for item in list(summary.get("rows") or []):
        field_rows.append({
            "field_no": str(item.get("image_number", "") or ""),
            "sperm_count": item.get("sperm_count", 0),
            "positive_count": item.get("positive_count", 0),
            "mean_intensity": item.get("mean_intensity", 0),
            "expression_rate": item.get("expression_rate", 0),
            "overlay_image_path": "",
            "csv_path": image_csv,
        })
    return field_rows


def _database_message(part, protein_name, total):
    common = (
        "{} 结果已保存到数据库：视野数 {}，精子总数 {}，"
        .format(
            protein_name,
            total.get("field_count", 0),
            total.get("sperm_count", 0),
        )
    )
    if part == "tail":
        return (
            common
            + "关联尾部数 {}，标定率 {}，C 荧光强度 {}。".format(
                total.get("positive_count", 0),
                _format_rate_for_display(total.get("expression_rate", 0)),
                total.get("mean_intensity_raw", total.get("mean_intensity", 0)),
            )
        )
    return (
        common
        + "共定位数 {}，标定率 {}，荧光强度 {}。".format(
            total.get("positive_count", 0),
            _format_rate_for_display(total.get("expression_rate", 0)),
            _format_int_for_display(total.get("mean_intensity", 0)),
        )
    )


def publish_measured_completion(completion_result, database):
    """Publish one measured result, atomically replace its DB rows, then commit files."""
    completion = dict(completion_result or {})
    publication = None
    stage = "validation"

    try:
        if completion.get("status") != "measured":
            raise ValueError("Analysis V2 CompletionResult 状态必须为 measured。")

        part = str(completion.get("part") or "").strip()
        if part not in ("head", "tail"):
            raise ValueError("不支持的 Analysis V2 结果部位：{}".format(part))

        context = dict(completion.get("context") or {})
        case_id = context.get("case_id")
        protein_key = str(
            completion.get("protein_key") or context.get("protein_key") or ""
        ).strip()
        protein_name = str(
            completion.get("protein_name") or context.get("protein_name") or ""
        ).strip()
        source_value = completion.get("source_dir")
        target_value = completion.get("target_dir")
        if not str(source_value or "").strip():
            raise ValueError("Analysis V2 CompletionResult 缺少测量输出目录。")
        if not str(target_value or "").strip():
            raise ValueError("Analysis V2 CompletionResult 缺少正式输出目录。")
        source_dir = Path(source_value).resolve()
        target_dir = Path(target_value).resolve()
        expected_field_count = int(completion.get("expected_field_count", 0) or 0)

        if not case_id:
            raise ValueError("当前 Analysis V2 上下文缺少数据库病例 ID。")
        if not protein_key:
            raise ValueError("当前 Analysis V2 上下文缺少蛋白内部编号。")
        if not protein_name:
            raise ValueError("当前 Analysis V2 上下文缺少蛋白名称。")
        replace = getattr(database, "replace_protein_analysis_with_fields", None)
        if replace is None:
            raise ValueError("数据库组件缺少原子结果保存接口。")

        stage = "publication"
        if part == "head":
            publication = stage_head_measurement_output(
                source_dir=source_dir,
                target_dir=target_dir,
                expected_field_count=expected_field_count,
            )
        else:
            publication = stage_tail_measurement_output(
                source_dir=source_dir,
                target_dir=target_dir,
                expected_field_count=expected_field_count,
                measurement_contract=dict(completion.get("measurement_contract") or {}),
            )

        summary = dict(publication.summary or {})
        if not summary.get("success"):
            raise ValueError(summary.get("message") or "正式结果解析失败。")
        if part == "tail" and summary.get("calculation_mode") != "head_equivalent":
            raise ValueError("尾部数据库保存拒绝非 head_equivalent 结果。")

        total = dict(summary.get("total") or {})
        field_rows = _build_field_rows(summary)

        stage = "database"
        analysis_id = replace(
            case_id=case_id,
            protein_name=protein_name,
            protein_part=part,
            image_folder=str(context.get("raw_image_folder", "") or ""),
            output_folder=str(target_dir),
            total_fields=total.get("field_count", 0),
            total_sperm_count=total.get("sperm_count", 0),
            positive_count=total.get("positive_count", 0),
            mean_intensity=total.get("mean_intensity", 0),
            expression_rate=total.get("expression_rate", 0),
            field_results=field_rows,
            status="完成",
        )

        stage = "commit"
        cleanup_warning = publication.commit()
        publication = None

        return PublishedAnalysisV2Completion(
            analysis_id=analysis_id,
            protein_key=protein_key,
            part=part,
            summary=summary,
            field_rows=field_rows,
            output_dir=target_dir,
            database_message=_database_message(part, protein_name, total),
            cleanup_warning=str(cleanup_warning or ""),
            tail_object_count=completion.get("tail_object_count"),
            associated_object_count=completion.get("associated_object_count"),
            unresolved_object_count=completion.get("unresolved_object_count"),
        )
    except BaseException as cause:
        error = AnalysisV2CompletionPublishError(
            str(cause), stage=stage, completion=completion, cause=cause,
        )
        if publication is not None:
            try:
                publication.rollback()
            except BaseException as rollback_error:
                error.rollback_error = rollback_error
        raise error from cause
