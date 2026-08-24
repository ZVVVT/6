"""Analysis V2 头部测量输出的严格验证与汇总。"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.result_parser import ResultParser

from .task_state import atomic_write_json


VALIDATION_VERSION = "analysis_v2_head_measurement_validation_v2"


def _find_exact_file(output_dir: Path, filename: str) -> Path:
    matches = sorted(
        path
        for path in output_dir.rglob("*.csv")
        if path.name.lower() == filename.lower()
    )
    if len(matches) != 1:
        raise ValueError(
            "{} 数量应为 1，实际为 {}".format(filename, len(matches))
        )
    return matches[0]


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def _number(row: Dict[str, str], name: str) -> float:
    try:
        return float(row[name])
    except (KeyError, TypeError, ValueError):
        raise ValueError("CSV 字段 {} 不是有效数字".format(name))


def _field_id_for_row(
    row: Dict[str, str],
    field_ids: List[str],
) -> str:
    sample_values = [
        str(value or "")
        for name, value in row.items()
        if "samplename" in str(name).lower()
    ]
    values = sample_values or [str(value or "") for value in row.values()]

    def has_boundary_match(field_id: str) -> bool:
        pattern = (
            r"(?<![^\W_])"
            + re.escape(field_id)
            + r"(?![^\W_])"
        )
        return any(
            re.search(pattern, value, flags=re.IGNORECASE) is not None
            for value in values
        )

    boundary_matches = [
        field_id
        for field_id in field_ids
        if has_boundary_match(field_id)
    ]
    if len(boundary_matches) == 1:
        return boundary_matches[0]

    matches = [
        field_id
        for field_id in field_ids
        if any(field_id.lower() in value.lower() for value in values)
    ]
    if len(matches) != 1:
        raise ValueError(
            "Image.csv 行无法唯一关联 field_id；候选={}；行={}".format(
                matches,
                row,
            )
        )
    return matches[0]


def _find_overlays(output_dir: Path, field_ids: List[str]) -> List[Path]:
    files = [path for path in output_dir.rglob("*") if path.is_file()]
    overlays = []
    for field_id in field_ids:
        for object_name in ("R_objects", "G_objects", "G_colocalized"):
            matches = [
                path
                for path in files
                if field_id.lower() in path.name.lower()
                and object_name.lower() in path.name.lower()
                and "overlay" in path.name.lower()
            ]
            if len(matches) != 1:
                raise ValueError(
                    "{} 的 {} Overlay 数量应为 1，实际为 {}".format(
                        field_id,
                        object_name,
                        len(matches),
                    )
                )
            overlays.append(matches[0])
    return overlays


def validate_head_measurement_output(
    output_dir: Path,
    field_ids: List[str],
    expected_object_counts: Dict[str, int],
    validation_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """严格验证测量 CSV、Overlay、对象计数并生成跨视野汇总。"""
    resolved_output = Path(output_dir).resolve()
    image_csv = _find_exact_file(resolved_output, "Image.csv")
    object_csv = _find_exact_file(resolved_output, "G_colocalized.csv")
    image_rows = _read_csv(image_csv)
    object_rows = _read_csv(object_csv)
    ordered_field_ids = list(field_ids)

    if len(image_rows) != len(ordered_field_ids):
        raise ValueError(
            "Image.csv 行数应为 {}，实际为 {}".format(
                len(ordered_field_ids),
                len(image_rows),
            )
        )
    missing_expected = [
        field_id
        for field_id in ordered_field_ids
        if field_id not in expected_object_counts
    ]
    if missing_expected:
        raise ValueError(
            "缺少视野期望对象数：{}".format(", ".join(missing_expected))
        )

    rows_by_field = {}
    for row in image_rows:
        field_id = _field_id_for_row(row, ordered_field_ids)
        if field_id in rows_by_field:
            raise ValueError(
                "Image.csv 中 field_id 重复：{}".format(field_id)
            )
        rows_by_field[field_id] = row
    missing_rows = [
        field_id
        for field_id in ordered_field_ids
        if field_id not in rows_by_field
    ]
    if missing_rows:
        raise ValueError(
            "Image.csv 缺少视野：{}".format(", ".join(missing_rows))
        )

    fields = []
    for field_id in ordered_field_ids:
        row = rows_by_field[field_id]
        csv_image_number = int(round(_number(row, "ImageNumber")))
        r_count = int(round(_number(row, "Count_R_objects")))
        g_count = int(round(_number(row, "Count_G_objects")))
        positive_count = int(round(_number(row, "Count_G_colocalized")))
        pipeline_rate = _number(row, "Math_ColocalizationRate")
        expected_count = int(expected_object_counts[field_id])
        calculated_rate = positive_count / r_count if r_count else 0.0

        if r_count != expected_count:
            raise ValueError(
                "{} 的 Count_R_objects={}，期望对象数={}".format(
                    field_id,
                    r_count,
                    expected_count,
                )
            )
        if g_count != r_count:
            raise ValueError(
                "{} 的 Count_G_objects 与 Count_R_objects 不一致".format(field_id)
            )
        if positive_count > r_count:
            raise ValueError(
                "{} 的 Count_G_colocalized 大于 Count_R_objects".format(field_id)
            )
        if abs(pipeline_rate - calculated_rate) > 1e-4:
            raise ValueError(
                "{} 的 Math_ColocalizationRate 不符合计数公式".format(field_id)
            )

        fields.append({
            "field_id": field_id,
            "image_number": csv_image_number,
            "expected_object_count": expected_count,
            "count_r_objects": r_count,
            "count_g_objects": g_count,
            "count_g_colocalized": positive_count,
            "pipeline_colocalization_rate": pipeline_rate,
            "calculated_colocalization_rate": calculated_rate,
        })

    positive_total = sum(item["count_g_colocalized"] for item in fields)
    if len(object_rows) != positive_total:
        raise ValueError(
            "G_colocalized.csv 对象行数 {} 与阳性总数 {} 不一致".format(
                len(object_rows),
                positive_total,
            )
        )

    mean_intensity_sum = sum(
        _number(row, "Math_MeanIntensity255")
        for row in object_rows
    )
    r_total = sum(item["count_r_objects"] for item in fields)
    total_intensity = mean_intensity_sum / r_total if r_total else 0.0
    total_rate = positive_total / r_total if r_total else 0.0
    overlays = _find_overlays(resolved_output, ordered_field_ids)

    parser = ResultParser(str(resolved_output), protein_part="head")
    parsed_result = parser.parse_image_summary(protein_part="head")
    object_result = parser.parse_object_summary()
    if not parsed_result.get("success"):
        raise ValueError(
            parsed_result.get("message", "ResultParser 图像汇总解析失败")
        )
    if not object_result.get("success"):
        raise ValueError(
            object_result.get("message", "ResultParser 对象汇总解析失败")
        )

    validation = {
        "schema_version": 2,
        "validation_version": VALIDATION_VERSION,
        "success": True,
        "field_count": len(fields),
        "image_csv_row_count": len(image_rows),
        "g_colocalized_row_count": len(object_rows),
        "overlay_count": len(overlays),
        "fields": fields,
        "totals": {
            "count_r_objects": r_total,
            "count_g_colocalized": positive_total,
            "mean_intensity255_sum": mean_intensity_sum,
            "head_fluorescence_intensity": total_intensity,
            "head_colocalization_rate": total_rate,
        },
        "image_csv_path": str(image_csv),
        "g_colocalized_csv_path": str(object_csv),
        "overlay_paths": [str(path) for path in overlays],
        "result_parser": {
            "image_summary": parsed_result,
            "object_summary": object_result,
        },
    }
    if validation_path is not None:
        atomic_write_json(Path(validation_path), validation)
    return validation


build_head_measurement_result = validate_head_measurement_output
