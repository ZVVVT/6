"""Analysis V2 人工校准后尾部测量服务。

本模块只服务于 Analysis V2 的 protein3 人工尾部流程。
旧批量尾部仍继续使用 pipelines/pipeline_tail.cppipe 和 legacy 公式。
"""

from __future__ import annotations

import csv
import json
import math
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import tifffile
from PIL import Image

from core.result_parser import ResultParser

from .environment_snapshot import EnvironmentSnapshotWriter
from .label_image_io import read_label_image, validate_label_image
from .manifest_store import ManifestStore
from .stage_logger import StageLogger
from .tail_calibration_service import task_paths_from_root
from .task_state import TaskStateStore, atomic_write_json


STAGE = "tail_measurement"
CALCULATION_MODE = "head_equivalent"
FORMULA_VERSION = "tail_mean_intensity_v1"


def _record_path(task_root: Path, record: Dict[str, Any]) -> Path:
    relative_path = str(record.get("relative_path") or "").strip()
    if relative_path:
        candidate = (task_root / relative_path).resolve()
        if candidate.is_file():
            return candidate

    absolute_path = str(record.get("absolute_path") or "").strip()
    if absolute_path:
        candidate = Path(absolute_path).resolve()
        if candidate.is_file():
            return candidate

    return Path()


def _load_manifest_files(task_root: Path) -> List[Dict[str, Any]]:
    manifest_path = task_root / "manifest.json"
    if not manifest_path.is_file():
        return []

    with manifest_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    files = payload.get("files") or []
    if not isinstance(files, list):
        raise ValueError("manifest.json 的 files 必须是数组。")
    return [dict(item) for item in files]


def _unique_source(
    task_root: Path,
    manifest_files: Sequence[Dict[str, Any]],
    field_id: str,
    role: str,
    fallback_directory: Path,
    fallback_matcher: Any,
) -> Path:
    candidates: List[Path] = []

    for record in manifest_files:
        metadata = record.get("metadata") or {}
        if (
            record.get("role") == role
            and str(metadata.get("field_id") or "") == field_id
        ):
            candidate = _record_path(task_root, record)
            if candidate.is_file():
                candidates.append(candidate.resolve())

    if not candidates and fallback_directory.is_dir():
        candidates.extend(
            candidate.resolve()
            for candidate in sorted(fallback_directory.rglob("*"))
            if candidate.is_file() and fallback_matcher(candidate.name)
        )

    unique: List[Path] = []
    seen = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)

    if len(unique) != 1:
        raise ValueError(
            "视野 {} 的 {} 源文件数量不是 1：{}".format(
                field_id,
                role,
                [str(item) for item in unique],
            )
        )

    return unique[0]


def _field_ids_from_manifest_or_files(
    manifest_files: Sequence[Dict[str, Any]],
    calibration_tail_dir: Path,
) -> List[str]:
    field_ids = sorted({
        str((record.get("metadata") or {}).get("field_id") or "").strip()
        for record in manifest_files
        if record.get("role") == "tail_final_labels"
        and str((record.get("metadata") or {}).get("field_id") or "").strip()
    })

    if field_ids:
        return field_ids

    suffix = "_TailFinalLabels.tif"
    return sorted({
        path.name[:-len(suffix)]
        for path in calibration_tail_dir.rglob("*{}".format(suffix))
        if path.is_file()
    })


def _load_tail_objects_contract(path: Path) -> Dict[str, Any]:
    """读取 schema v1/v2，并归一化对象与两类计数。"""
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle) or {}
    raw_objects = payload.get("objects") or []
    if not isinstance(raw_objects, list):
        raise ValueError("TailFinalObjects 的 objects 必须是数组。")

    objects: List[Dict[str, Any]] = []
    for raw in raw_objects:
        item = dict(raw)
        tail_value = item.get("tail_object_id", item.get("object_id"))
        head_value = item.get("head_label_id", item.get("head_id"))
        if tail_value is None:
            raise ValueError("TailFinalObjects 对象缺少 tail object ID。")
        status = str(item.get("association_status") or "").strip()
        if not status:
            status = "associated" if head_value is not None else "unresolved"
        if status not in {"associated", "unresolved"}:
            raise ValueError("未知 association_status：{}".format(status))
        if status == "associated" and head_value is None:
            raise ValueError("associated tail 不允许 head_label_id=null。")
        if status == "unresolved" and head_value is not None:
            raise ValueError("unresolved tail 不允许携带 head_label_id。")
        objects.append({
            "tail_object_id": int(tail_value),
            "head_label_id": None if head_value is None else int(head_value),
            "association_status": status,
            "pixel_count": item.get("pixel_count"),
            "source": item.get("source"),
            "fragment_label_id": item.get("fragment_label_id"),
        })

    tail_count = int(
        payload.get("tail_object_count", payload.get("object_count", len(objects)))
        or 0
    )
    associated_ids = sorted(
        item["tail_object_id"]
        for item in objects
        if item["association_status"] == "associated"
    )
    unresolved_ids = sorted(
        item["tail_object_id"]
        for item in objects
        if item["association_status"] == "unresolved"
    )
    if len(objects) != tail_count:
        raise ValueError("TailFinalObjects 对象行数与 tail_object_count 不一致。")
    if sorted(item["tail_object_id"] for item in objects) != list(
        range(1, tail_count + 1)
    ):
        raise ValueError("TailFinalObjects 的 tail_object_id 不是连续 1...N。")
    declared_associated = payload.get("associated_object_count")
    declared_unresolved = payload.get("unresolved_object_count")
    if declared_associated is not None and int(declared_associated) != len(associated_ids):
        raise ValueError("associated_object_count 与 objects 不一致。")
    if declared_unresolved is not None and int(declared_unresolved) != len(unresolved_ids):
        raise ValueError("unresolved_object_count 与 objects 不一致。")
    return {
        "tail_object_count": tail_count,
        "associated_object_count": len(associated_ids),
        "unresolved_object_count": len(unresolved_ids),
        "associated_ids": associated_ids,
        "unresolved_ids": unresolved_ids,
        "objects": objects,
    }


def collect_tail_measurement_fields(task_root: Path) -> List[Dict[str, Any]]:
    """收集每个视野的 G/R、头部标签和两类尾部标签。"""
    root = Path(task_root).resolve()
    paths = task_paths_from_root(root)
    manifest_files = _load_manifest_files(root)
    field_ids = _field_ids_from_manifest_or_files(
        manifest_files,
        paths.calibration_tail_dir,
    )

    if not field_ids:
        raise ValueError("未找到已完成人工校准的尾部视野。")

    fields: List[Dict[str, Any]] = []

    for field_id in field_ids:
        def channel_matcher(channel: str) -> Any:
            suffixes = (
                "_{}.tif".format(channel).lower(),
                "_{}.tiff".format(channel).lower(),
            )
            return lambda name: (
                name.lower().startswith(field_id.lower() + "_")
                and name.lower().endswith(suffixes)
            )

        exact_names = {
            "head_final_labels": "{}_HeadFinalLabels.tif".format(field_id),
            "tail_final_labels": "{}_TailFinalLabels.tif".format(field_id),
            "tail_positive_head_labels": "{}_TailPositiveHeadLabels.tif".format(field_id),
            "tail_final_objects": "{}_TailFinalObjects.json".format(field_id),
        }

        fitc = _unique_source(
            root,
            manifest_files,
            field_id,
            "fitc_input",
            paths.input_dir,
            channel_matcher("FITC"),
        )
        tritc = _unique_source(
            root,
            manifest_files,
            field_id,
            "tritc_input",
            paths.input_dir,
            channel_matcher("TRITC"),
        )
        head_labels = _unique_source(
            root,
            manifest_files,
            field_id,
            "head_final_labels",
            paths.calibration_head_dir,
            lambda name: name.casefold()
            == exact_names["head_final_labels"].casefold(),
        )
        tail_labels = _unique_source(
            root,
            manifest_files,
            field_id,
            "tail_final_labels",
            paths.calibration_tail_dir,
            lambda name: name.casefold()
            == exact_names["tail_final_labels"].casefold(),
        )
        positive_labels = _unique_source(
            root,
            manifest_files,
            field_id,
            "tail_positive_head_labels",
            paths.calibration_tail_dir,
            lambda name: name.casefold()
            == exact_names["tail_positive_head_labels"].casefold(),
        )
        objects_path = _unique_source(
            root,
            manifest_files,
            field_id,
            "tail_final_objects",
            paths.calibration_tail_dir,
            lambda name: name.casefold()
            == exact_names["tail_final_objects"].casefold(),
        )

        contract = _load_tail_objects_contract(objects_path)
        tail_object_count = int(contract["tail_object_count"])
        associated_object_count = int(contract["associated_object_count"])

        if tail_object_count <= 0:
            raise ValueError(
                "视野 {} 的尾部对象数量不大于 0。".format(field_id)
            )

        tail_array = read_label_image(tail_labels)
        positive_array = read_label_image(
            positive_labels,
            expected_shape=tuple(tail_array.shape),
            require_objects=associated_object_count > 0,
        )
        head_array = read_label_image(
            head_labels,
            expected_shape=tuple(tail_array.shape),
        )

        tail_stats = validate_label_image(tail_array)
        positive_stats = validate_label_image(
            positive_array, require_objects=associated_object_count > 0,
        )
        head_stats = validate_label_image(head_array)
        expected_labels = list(range(1, tail_object_count + 1))

        if tail_stats["positive_labels"] != expected_labels:
            raise ValueError(
                "视野 {} 的 TailFinalLabels 不是连续 1...N。".format(
                    field_id
                )
            )
        positive_ids = positive_stats["positive_labels"]
        if not set(positive_ids).issubset(set(expected_labels)):
            raise ValueError(
                "视野 {} 的 TailPositiveHeadLabels 包含非法 tail ID。".format(
                    field_id
                )
            )
        if positive_ids != contract["associated_ids"]:
            raise ValueError(
                "视野 {} 的 TailPositiveHeadLabels 与 associated tail IDs 不一致。".format(
                    field_id
                )
            )

        fields.append({
            "field_id": field_id,
            "fitc": fitc,
            "tritc": tritc,
            "head_labels": head_labels,
            "tail_labels": tail_labels,
            "positive_labels": positive_labels,
            "objects": objects_path,
            "tail_object_count": tail_object_count,
            "associated_object_count": associated_object_count,
            "unresolved_object_count": int(contract["unresolved_object_count"]),
            # 兼容尚未切换的内部调用方；语义仅为全部 tail 数。
            "expected_object_count": tail_object_count,
            "head_object_count": len(head_stats["positive_labels"]),
        })

    return fields


def _reset_directory(path: Path) -> None:
    target = Path(path)
    if target.exists():
        shutil.rmtree(str(target))
    target.mkdir(parents=True, exist_ok=True)


def _prepare_measurement_channel_image(
    source: Path,
    destination: Path,
) -> None:
    """为 MvImageID 准备真实 TIFF 通道图像。

    实际格式为 TIFF 且 tifffile 可读取时保持原文件字节不变；其他
    格式从解码像素重新编码为无压缩 TIFF，禁止仅修改扩展名。
    """
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()

    try:
        with Image.open(source_path) as image:
            image.load()
            source_format = str(image.format or "").upper()

            if source_format == "TIFF":
                tifffile.imread(str(source_path))
                shutil.copy2(str(source_path), str(destination_path))
            else:
                image.save(
                    str(destination_path),
                    format="TIFF",
                    compression="raw",
                )

        with Image.open(destination_path) as verification:
            if str(verification.format or "").upper() != "TIFF":
                raise ValueError("生成文件的实际格式不是 TIFF")
            verification.verify()
        tifffile.imread(str(destination_path))
    except Exception as exception:
        raise ValueError(
            "无法准备标准 TIFF 测量通道图像：{} -> {}；原因：{}".format(
                source_path,
                destination_path,
                exception,
            )
        ) from exception


def prepare_standardized_tail_input(
    fields: Sequence[Dict[str, Any]],
    input_dir: Path,
) -> Dict[str, Any]:
    """生成新版尾部测量管道要求的五类输入。"""
    target_dir = Path(input_dir).resolve()
    _reset_directory(target_dir)
    records: List[Dict[str, Any]] = []

    for field in fields:
        field_id = str(field["field_id"])
        destinations = {
            "g": target_dir / "{}_G.tif".format(field_id),
            "r": target_dir / "{}_R.tif".format(field_id),
            "head_labels": target_dir
            / "{}_HeadFinalLabels.tif".format(field_id),
            "tail_labels": target_dir
            / "{}_TailFinalLabels.tif".format(field_id),
            "positive_labels": target_dir
            / "{}_TailPositiveHeadLabels.tif".format(field_id),
        }
        sources = {
            "g": Path(field["fitc"]),
            "r": Path(field["tritc"]),
            "head_labels": Path(field["head_labels"]),
            "tail_labels": Path(field["tail_labels"]),
            "positive_labels": Path(field["positive_labels"]),
        }

        for key, destination in destinations.items():
            source = sources[key]
            if not source.is_file():
                raise FileNotFoundError("测量源文件不存在：{}".format(source))
            if key in {"g", "r"}:
                _prepare_measurement_channel_image(source, destination)
            else:
                shutil.copy2(str(source), str(destination))

        records.append({
            "field_id": field_id,
            "g_source_path": str(sources["g"].resolve()),
            "r_source_path": str(sources["r"].resolve()),
            "head_final_labels_source_path": str(
                sources["head_labels"].resolve()
            ),
            "tail_final_labels_source_path": str(
                sources["tail_labels"].resolve()
            ),
            "tail_positive_head_labels_source_path": str(
                sources["positive_labels"].resolve()
            ),
            "g_path": str(destinations["g"]),
            "r_path": str(destinations["r"]),
            "head_final_labels_path": str(destinations["head_labels"]),
            "tail_final_labels_path": str(destinations["tail_labels"]),
            "tail_positive_head_labels_path": str(
                destinations["positive_labels"]
            ),
            "expected_object_count": int(field["expected_object_count"]),
            "tail_object_count": int(
                field.get("tail_object_count", field["expected_object_count"])
            ),
            "associated_object_count": int(
                field.get("associated_object_count", field["expected_object_count"])
            ),
            "head_object_count": int(field["head_object_count"]),
        })

    expected_names = sorted(
        name
        for field in fields
        for name in (
            "{}_G.tif".format(field["field_id"]),
            "{}_R.tif".format(field["field_id"]),
            "{}_HeadFinalLabels.tif".format(field["field_id"]),
            "{}_TailFinalLabels.tif".format(field["field_id"]),
            "{}_TailPositiveHeadLabels.tif".format(field["field_id"]),
        )
    )
    actual_names = sorted(
        path.name for path in target_dir.iterdir() if path.is_file()
    )

    if actual_names != expected_names:
        raise ValueError(
            "尾部测量输入预检失败；实际文件={}。".format(actual_names)
        )

    return {
        "records": records,
        "file_names": actual_names,
        "field_count": len(records),
    }


def _read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _finite_number(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("{} 不是有效数字：{}".format(field_name, value))

    if not math.isfinite(number):
        raise ValueError("{} 不是有限数字：{}".format(field_name, value))
    return number


def _integer_number(value: Any, field_name: str) -> int:
    number = _finite_number(value, field_name)
    rounded = int(round(number))
    if abs(number - rounded) > 1e-6:
        raise ValueError("{} 不是整数：{}".format(field_name, value))
    return rounded


def _image_number_key(value: Any) -> int:
    return _integer_number(value, "ImageNumber")


def validate_tail_measurement_output(
    output_dir: Path,
    fields: Sequence[Dict[str, Any]],
    result_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """严格验证新版尾部 CSV、对象数量、公式和三类叠加图。"""
    output = Path(output_dir).resolve()
    image_csv = output / "Image.csv"
    object_csv = output / "G_objects.csv"

    for path in (image_csv, object_csv):
        if not path.is_file() or path.stat().st_size <= 0:
            raise FileNotFoundError("尾部测量缺少有效文件：{}".format(path))

    ordered_fields = sorted(fields, key=lambda item: str(item["field_id"]))
    image_rows = _read_csv_rows(image_csv)
    object_rows = _read_csv_rows(object_csv)

    if len(image_rows) != len(ordered_fields):
        raise ValueError(
            "Image.csv 视野数不一致：期望 {}，实际 {}。".format(
                len(ordered_fields), len(image_rows)
            )
        )

    required_image_columns = {
        "ImageNumber",
        "Count_G_objects",
        "Count_R_objects",
        "Count_R_colocalized",
        "Math_ColocalizationRate",
    }
    required_object_columns = {
        "ImageNumber",
        "ObjectNumber",
        "AreaShape_Area",
        "Math_MeanIntensity255",
    }

    image_columns = set(image_rows[0].keys()) if image_rows else set()
    object_columns = set(object_rows[0].keys()) if object_rows else set()
    missing_image = sorted(required_image_columns - image_columns)
    missing_object = sorted(required_object_columns - object_columns)

    if missing_image:
        raise ValueError("Image.csv 缺少列：{}".format(missing_image))
    if missing_object:
        raise ValueError("G_objects.csv 缺少列：{}".format(missing_object))

    image_rows = sorted(image_rows, key=lambda row: _image_number_key(row["ImageNumber"]))
    grouped_objects: Dict[int, List[Dict[str, str]]] = {}
    for row in object_rows:
        image_number = _image_number_key(row.get("ImageNumber"))
        grouped_objects.setdefault(image_number, []).append(row)

    field_results: List[Dict[str, Any]] = []
    total_mean255 = 0.0
    total_sperm = 0
    total_positive = 0
    expected_total_objects = 0
    expected_total_associated = 0
    overlay_paths: List[str] = []

    for index, (field, row) in enumerate(
        zip(ordered_fields, image_rows),
        start=1,
    ):
        image_number = _image_number_key(row.get("ImageNumber"))
        if image_number != index:
            raise ValueError(
                "Image.csv 的 ImageNumber 应连续为 1...N，实际第 {} 行为 {}。".format(
                    index, image_number
                )
            )

        tail_object_count = int(
            field.get("tail_object_count", field["expected_object_count"])
        )
        associated_object_count = int(
            field.get("associated_object_count", field["expected_object_count"])
        )
        expected_total_objects += tail_object_count
        expected_total_associated += associated_object_count
        g_count = _integer_number(row.get("Count_G_objects"), "Count_G_objects")
        positive_count = _integer_number(
            row.get("Count_R_colocalized"),
            "Count_R_colocalized",
        )
        sperm_count = _integer_number(
            row.get("Count_R_objects"),
            "Count_R_objects",
        )
        rate = _finite_number(
            row.get("Math_ColocalizationRate"),
            "Math_ColocalizationRate",
        )

        if sperm_count <= 0:
            raise ValueError(
                "视野 {} 的 Count_R_objects 不大于 0。".format(
                    field["field_id"]
                )
            )
        if g_count != tail_object_count or positive_count != associated_object_count:
            raise ValueError(
                "视野 {} 的尾部计数不一致：tail_object_count={}，"
                "associated_object_count={}，Count_G_objects={}，"
                "Count_R_colocalized={}。".format(
                    field["field_id"],
                    tail_object_count,
                    associated_object_count,
                    g_count,
                    positive_count,
                )
            )
        if positive_count > sperm_count:
            raise ValueError(
                "视野 {} 的阳性数大于全部精子数。".format(field["field_id"])
            )

        expected_rate = positive_count / sperm_count
        if abs(rate - round(expected_rate, 4)) > 0.00011:
            raise ValueError(
                "视野 {} 的标定率异常：CSV={}，按计数应为 {}。".format(
                    field["field_id"],
                    rate,
                    round(expected_rate, 4),
                )
            )

        current_objects = grouped_objects.get(image_number, [])
        if len(current_objects) != tail_object_count:
            raise ValueError(
                "视野 {} 的 G_objects.csv 行数不一致：期望 {}，实际 {}。".format(
                    field["field_id"],
                    tail_object_count,
                    len(current_objects),
                )
            )

        object_numbers: List[int] = []
        field_mean255 = 0.0
        for object_row in current_objects:
            object_number = _integer_number(
                object_row.get("ObjectNumber"),
                "ObjectNumber",
            )
            object_numbers.append(object_number)
            area = _finite_number(
                object_row.get("AreaShape_Area"),
                "AreaShape_Area",
            )
            mean255 = _finite_number(
                object_row.get("Math_MeanIntensity255"),
                "Math_MeanIntensity255",
            )
            if area <= 0:
                raise ValueError(
                    "视野 {} 对象 {} 的面积不大于 0。".format(
                        field["field_id"], object_number
                    )
                )
            if mean255 < 0 or mean255 > 255.01:
                raise ValueError(
                    "视野 {} 对象 {} 的 Math_MeanIntensity255 超出 0~255：{}。".format(
                        field["field_id"], object_number, mean255
                    )
                )
            field_mean255 += mean255

        if sorted(object_numbers) != list(range(1, tail_object_count + 1)):
            raise ValueError(
                "视野 {} 的 ObjectNumber 不是连续 1...N。".format(
                    field["field_id"]
                )
            )

        field_id = str(field["field_id"])
        expected_overlays = (
            output / "{}_G_G_objects_OrigOverlay.png".format(field_id),
            output / "{}_R_R_objects_OrigOverlay.png".format(field_id),
            output / "{}_G_G_colocalized_OrigOverlay.png".format(field_id),
        )
        for overlay in expected_overlays:
            if not overlay.is_file() or overlay.stat().st_size <= 0:
                raise FileNotFoundError(
                    "视野 {} 缺少叠加图：{}".format(field_id, overlay.name)
                )
            overlay_paths.append(str(overlay))

        total_mean255 += field_mean255
        total_sperm += sperm_count
        total_positive += positive_count
        field_results.append({
            "field_id": field_id,
            "image_number": image_number,
            "sperm_count": sperm_count,
            "positive_count": positive_count,
            "tail_object_count": tail_object_count,
            "associated_object_count": associated_object_count,
            "mean255_sum": round(field_mean255, 4),
            "mean_intensity_raw": round(field_mean255 / sperm_count, 4),
            "expression_rate": round(expected_rate * 100, 2),
        })

    if len(object_rows) != expected_total_objects:
        raise ValueError(
            "G_objects.csv 总行数不一致：期望 {}，实际 {}。".format(
                expected_total_objects, len(object_rows)
            )
        )

    parser_result = ResultParser(
        str(output),
        protein_part="tail",
        calculation_mode=CALCULATION_MODE,
    ).parse_image_summary("tail")

    if not parser_result.get("success"):
        raise ValueError(
            "ResultParser 新尾部解析失败：{}".format(
                parser_result.get("message") or "未知错误"
            )
        )
    if parser_result.get("calculation_mode") != CALCULATION_MODE:
        raise ValueError("ResultParser 未使用 head_equivalent 模式。")
    parser_warnings = list(parser_result.get("warnings") or [])
    remaining_warnings = [
        warning
        for warning in parser_warnings
        if not (
            "Count_G_objects=" in str(warning)
            and "Count_R_colocalized=" in str(warning)
            and "不一致" in str(warning)
        )
    ]
    if remaining_warnings:
        raise ValueError(
            "ResultParser 一致性检查未通过：{}".format(
                remaining_warnings
            )
        )

    total = dict(parser_result.get("total") or {})
    expected_intensity = total_mean255 / total_sperm if total_sperm else 0.0
    expected_rate = total_positive / total_sperm if total_sperm else 0.0

    if int(total.get("sperm_count") or 0) != total_sperm:
        raise ValueError("ResultParser 精子总数与严格校验结果不一致。")
    if int(total.get("positive_count") or 0) != total_positive:
        raise ValueError("ResultParser 阳性总数与严格校验结果不一致。")
    if abs(float(total.get("mean_intensity_raw") or 0) - expected_intensity) > 0.00011:
        raise ValueError("ResultParser 尾部荧光强度公式结果不一致。")
    if abs(float(total.get("rate_fraction") or 0) - expected_rate) > 0.0000011:
        raise ValueError("ResultParser 尾部标定率结果不一致。")

    validation = {
        "schema_version": 1,
        "analysis_version": "analysis_v2",
        "protein_part": "tail",
        "calculation_mode": CALCULATION_MODE,
        "formula_version": FORMULA_VERSION,
        "formula": "sum(Math_MeanIntensity255) / sum(Count_R_objects)",
        "rate_formula": "sum(Count_R_colocalized) / sum(Count_R_objects)",
        "field_count": len(ordered_fields),
        "expected_object_count": expected_total_objects,
        "tail_object_count": expected_total_objects,
        "associated_object_count": expected_total_associated,
        "image_csv_path": str(image_csv),
        "g_objects_csv_path": str(object_csv),
        "overlay_paths": overlay_paths,
        "fields": field_results,
        "strict_totals": {
            "sperm_count": total_sperm,
            "positive_count": total_positive,
            "mean255_sum": round(total_mean255, 4),
            "mean_intensity_raw": round(expected_intensity, 4),
            "expression_rate": round(expected_rate * 100, 2),
        },
        "result_parser": parser_result,
    }

    if result_path is not None:
        atomic_write_json(Path(result_path), validation)

    return validation


class TailMeasurementService:
    """运行新版尾部管道并将结果保留在任务 candidate_output 中。"""

    def __init__(
        self,
        task_root: Path,
        pipeline: Path,
        mvimageid_root: Path,
        python_exe: Path,
        timeout_seconds: float = 900.0,
        plugins_directory: Optional[Path] = None,
    ) -> None:
        self.paths = task_paths_from_root(Path(task_root))
        self.pipeline = Path(pipeline).resolve()
        self.mvimageid_root = Path(mvimageid_root).resolve()
        self.python_exe = Path(python_exe).resolve()
        self.tail_measurement_dir = self.paths.measurement_dir / "tail"
        # 本管道只使用 CellProfiler 内置模块。使用任务内专用空目录，
        # 避免加载全局 active_plugins 中与尾部标签测量无关的重型插件。
        self.plugins_dir = (
            self.tail_measurement_dir / "_builtin_only_plugins"
        ).resolve()
        self.input_dir = self.tail_measurement_dir / "input"
        self.output_dir = self.tail_measurement_dir / "candidate_output"
        self.result_path = self.tail_measurement_dir / "tail_measurement_result.json"
        self.measurement_input_path = self.tail_measurement_dir / "measurement_input.json"
        self.measurement_manifest_path = self.tail_measurement_dir / "measurement_manifest.json"
        self.state = TaskStateStore.from_task_paths(self.paths)
        self.manifest = ManifestStore.from_task_paths(self.paths)
        self.logger = StageLogger.from_task_paths(self.paths)
        self.timeout_seconds = float(timeout_seconds)

        from core.mvimageid_runner import MvImageIDRunner

        self.runner = MvImageIDRunner(
            source_project_dir=str(self.mvimageid_root),
            python_exe=str(self.python_exe),
            module_name="MvImageID",
            plugins_directory=str(self.plugins_dir),
            log_file="",
        )

    def _prepare_builtin_only_plugins_dir(self) -> None:
        if self.plugins_dir.exists() and not self.plugins_dir.is_dir():
            raise ValueError(
                "尾部标签测量专用插件路径不是目录：{}".format(
                    self.plugins_dir
                )
            )
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        unexpected = sorted(path.name for path in self.plugins_dir.iterdir())
        if unexpected:
            raise ValueError(
                "尾部标签测量专用插件目录必须为空：{}；发现：{}".format(
                    self.plugins_dir,
                    unexpected,
                )
            )

    def run(self, process_context=None) -> Dict[str, Any]:
        if process_context is not None:
            process_context.check_cancelled()
        try:
            current = self.state.load()
            calibrated_before = any(
                item.get("status") == "tail_calibrated"
                for item in current.get("history", [])
            )
            allowed = {"tail_calibrated", "tail_measuring", "tail_measured"}
            if (
                current.get("status") not in allowed
                and not (
                    current.get("status") == "failed"
                    and calibrated_before
                )
            ):
                raise ValueError(
                    "当前状态不是 tail_calibrated：{}".format(
                        current.get("status")
                    )
                )

            if not self.pipeline.is_file():
                raise FileNotFoundError(
                    "尾部测量管道不存在：{}".format(self.pipeline)
                )

            self._prepare_builtin_only_plugins_dir()
            fields = collect_tail_measurement_fields(self.paths.task_root)
            prepared = prepare_standardized_tail_input(fields, self.input_dir)
            _reset_directory(self.output_dir)

            atomic_write_json(
                self.measurement_input_path,
                {
                    "schema_version": 1,
                    "calculation_mode": CALCULATION_MODE,
                    "formula_version": FORMULA_VERSION,
                    "fields": prepared["records"],
                },
            )
            atomic_write_json(
                self.measurement_manifest_path,
                {
                    "schema_version": 1,
                    "analysis_version": "analysis_v2",
                    "protein_part": "tail",
                    "calculation_mode": CALCULATION_MODE,
                    "formula_version": FORMULA_VERSION,
                    "pipeline_path": str(self.pipeline),
                    "plugins_directory": str(self.plugins_dir),
                    "input_dir": str(self.input_dir),
                    "candidate_output_dir": str(self.output_dir),
                    "field_count": len(fields),
                },
            )

            self.state.update(
                "tail_measuring",
                STAGE,
                "正在使用人工校准尾部标签执行新版尾部测量",
            )
            self.logger.info(
                STAGE,
                "开始一次性测量 {} 个尾部视野".format(len(fields)),
            )
            self.logger.info(
                STAGE,
                "尾部标签测量仅使用 CellProfiler 内置模块。",
            )
            self.logger.info(STAGE, "插件目录：{}".format(self.plugins_dir))
            self.logger.info(STAGE, "Pipeline：{}".format(self.pipeline))
            self.logger.info(STAGE, "输入目录：{}".format(self.input_dir))
            self.logger.info(STAGE, "输出目录：{}".format(self.output_dir))

            environment = EnvironmentSnapshotWriter(
                self.paths,
                mvimageid_root=self.mvimageid_root,
                mvimageid_python=self.python_exe,
                plugins_dir=self.plugins_dir,
                pipeline_path=self.pipeline,
                input_dir=self.input_dir,
                output_dir=self.output_dir,
            )
            environment.write()

            run_result = self.runner.run(
                pipeline_file=str(self.pipeline),
                input_dir=str(self.input_dir),
                output_dir=str(self.output_dir),
                log_callback=lambda message: self.logger.info(
                    STAGE,
                    str(message),
                ),
                **({"process_context": process_context} if process_context is not None else {}),
                cancel_callback=None,
                log_file="",
            )

            if process_context is not None:
                process_context.check_cancelled()
            run_payload = {
                "command": run_result.command,
                "return_code": run_result.return_code,
                "duration_seconds": run_result.elapsed_seconds,
                "command_log_path": str(run_result.command_file or ""),
                "stdout_path": str(run_result.log_file or ""),
                "success": run_result.success,
                "runner_class": "core.mvimageid_runner.MvImageIDRunner",
            }
            self.logger.event(
                "mvimageid_tail_measurement",
                STAGE,
                "succeeded" if run_result.success else "failed",
                duration_seconds=run_result.elapsed_seconds,
                return_code=run_result.return_code,
                extra=run_payload,
            )

            if not run_result.success:
                error = RuntimeError(
                    run_result.error_message
                    or "MvImageID 尾部测量失败，退出码 {}。".format(
                        run_result.return_code
                    )
                )
                error.return_code = run_result.return_code
                error.log_path = run_result.log_file
                raise error

            validation = validate_tail_measurement_output(
                output_dir=self.output_dir,
                fields=fields,
                result_path=self.result_path,
            )

            registrations = [
                (self.measurement_input_path, "tail_measurement_input", "application/json"),
                (self.measurement_manifest_path, "tail_measurement_manifest", "application/json"),
                (Path(validation["image_csv_path"]), "tail_measurement_image_csv", "text/csv"),
                (Path(validation["g_objects_csv_path"]), "tail_measurement_object_csv", "text/csv"),
                (self.result_path, "tail_measurement_result", "application/json"),
                (environment.environment_path, "environment", "application/json"),
            ]
            for overlay_path in validation["overlay_paths"]:
                registrations.append((
                    Path(overlay_path),
                    "tail_measurement_overlay",
                    "image/png",
                ))
            if run_result.command_file is not None:
                registrations.append((
                    run_result.command_file,
                    "tail_measurement_command",
                    "text/plain",
                ))
            if run_result.log_file is not None:
                registrations.append((
                    run_result.log_file,
                    "tail_measurement_stdout",
                    "text/plain",
                ))

            for path, role, media_type in registrations:
                self.manifest.add_file(path, role, STAGE, media_type)

            final_state = self.state.update(
                "tail_measured",
                STAGE,
                "人工校准后尾部测量完成，等待安全发布",
            )
            self.logger.info(STAGE, "尾部测量和严格校验完成")

            return {
                "run": run_payload,
                "validation": validation,
                "parsed_result": validation["result_parser"],
                "state": final_state,
                "candidate_output_dir": str(self.output_dir),
                "measurement_input_path": str(self.measurement_input_path),
                "measurement_manifest_path": str(self.measurement_manifest_path),
                "measurement_result_path": str(self.result_path),
            }

        except BaseException as exception:
            if process_context is not None:
                process_context.check_cancelled()
            self.logger.record_exception(STAGE, exception)
            self.state.mark_failed(
                STAGE,
                exception,
                "人工校准后尾部测量失败",
            )
            raise
