#!/usr/bin/env python
"""Analysis V2 直接 Cellpose 头部识别 worker。

本文件从 experiments/r_preview/benchmark_cellpose_r_preview.py 的已验证逻辑
整理而来，但运行时不依赖 experiments 目录。worker 在一个进程中只初始化一次
模型，并按 worker_input.json 顺序处理全部 TRITC 图像。
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
import platform
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
import tifffile
import torch
from cellpose import models
from PIL import Image
from scipy import ndimage


SCHEMA_VERSION = "analysis_v2_direct_cellpose_worker_v1"
UINT16_MAX = int(np.iinfo(np.uint16).max)
BASELINE_PARAMETERS = {
    "model": "cpsam",
    "channels": [0, 0],
    "diameter": None,
    "flow_threshold": None,
    "cellprob_threshold": None,
    "normalize": False,
    "do_3D": False,
    "min_area": 20.0,
    "max_area": 5000.0,
    "min_circularity": 0.2,
    "remove_edge_masks": False,
    "max_equivalent_diameter": None,
    "overlay_mode": "gray",
    "outline_color": "00ffff",
    "input_normalization": "percentile_1_99_to_float32_0_1",
}


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=".{}.".format(path.name),
            suffix=".tmp",
            dir=str(path.parent),
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary_path), str(path))
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def read_image(path: Path) -> np.ndarray:
    if path.suffix.lower() in (".tif", ".tiff"):
        array = np.asarray(tifffile.imread(str(path)))
    else:
        with Image.open(str(path)) as image:
            prepared = image if image.mode in ("RGB", "RGBA", "L", "I", "F", "1") else image.convert("RGB")
            array = np.asarray(prepared).copy()
    if array.ndim == 2:
        return array
    if array.ndim == 3 and array.shape[2] in (1, 3, 4):
        return array
    raise ValueError("仅支持二维灰度或 HxWx1/3/4 图像，实际 shape={}".format(array.shape))


def normalize_channel(channel: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    values = np.asarray(channel)
    if values.ndim != 2:
        raise ValueError("待归一化通道必须是二维数组")
    finite = np.isfinite(values)
    if not np.any(finite):
        raise ValueError("图像不包含有效像素")
    finite_values = values[finite].astype(np.float32, copy=False)
    low, high = np.percentile(finite_values, (1.0, 99.0))
    low, high = float(low), float(high)
    if high <= low:
        low, high = float(np.min(finite_values)), float(np.max(finite_values))
    if high <= low:
        normalized = np.zeros(values.shape, dtype=np.float32)
    else:
        normalized = (values.astype(np.float32) - low) / (high - low)
        normalized[~finite] = 0.0
        normalized = np.clip(normalized, 0.0, 1.0).astype(np.float32, copy=False)
    return normalized, np.rint(normalized * 255.0).astype(np.uint8)


def extract_red_channel(image: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    if image.ndim == 2:
        return normalize_channel(image)
    return normalize_channel(image[:, :, 0])


def contour_for_mask(component_mask: np.ndarray) -> Optional[np.ndarray]:
    contours, _hierarchy = cv2.findContours(
        component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    return max(contours, key=cv2.contourArea) if contours else None


def normalize_label_connectivity(
    raw_labels: np.ndarray,
) -> Tuple[np.ndarray, List[Dict[str, int]]]:
    """每个正标签仅保留一个 8 邻域连通的最大区域。"""
    labels = np.asarray(raw_labels)
    normalized = labels.copy()
    changes = []  # type: List[Dict[str, int]]
    object_slices = ndimage.find_objects(labels)

    for raw_id in np.unique(labels[labels > 0]):
        object_id = int(raw_id)
        object_slice = object_slices[object_id - 1]
        if object_slice is None:
            continue
        local_mask = (labels[object_slice] == raw_id).astype(np.uint8)
        component_count, components = cv2.connectedComponents(
            local_mask,
            connectivity=8,
        )
        if component_count <= 2:
            continue

        component_areas = np.bincount(components.ravel())[1:]
        maximum_area = int(component_areas.max())
        largest_components = np.flatnonzero(component_areas == maximum_area) + 1
        # 面积并列时保留左上像素按行优先最靠前的区域，不依赖组件编号。
        kept_component = min(
            (int(component_id) for component_id in largest_components),
            key=lambda component_id: int(
                np.flatnonzero(components == component_id)[0]
            ),
        )
        kept_pixels = int(component_areas[kept_component - 1])
        removed_pixels = int(component_areas.sum()) - kept_pixels
        local_labels = normalized[object_slice]
        local_labels[(local_mask > 0) & (components != kept_component)] = 0
        changes.append({
            "object_id": object_id,
            "component_count": int(component_count - 1),
            "kept_pixels": kept_pixels,
            "removed_pixels": removed_pixels,
        })

    return normalized, changes


def measure_and_filter(
    raw_labels: np.ndarray,
    parameters: Dict[str, Any],
    field_id: Optional[str] = None,
) -> Tuple[np.ndarray, List[Dict[str, Any]], List[np.ndarray]]:
    labels = np.squeeze(np.asarray(raw_labels))
    if labels.ndim != 2:
        raise ValueError("Cellpose 标签必须为二维，实际 shape={}".format(labels.shape))
    if np.any(labels < 0):
        raise ValueError("Cellpose 标签包含负值")

    labels, connectivity_changes = normalize_label_connectivity(labels)
    for change in connectivity_changes:
        print(
            "head_label_connectivity_normalized: "
            "field={} object_id={} components={} kept_pixels={} removed_pixels={}".format(
                field_id or "",
                change["object_id"],
                change["component_count"],
                change["kept_pixels"],
                change["removed_pixels"],
            ),
            flush=True,
        )

    height, width = labels.shape
    source_ids, source_areas = np.unique(labels, return_counts=True)
    object_slices = ndimage.find_objects(labels)
    filtered = np.zeros((height, width), dtype=np.uint16)
    objects = []  # type: List[Dict[str, Any]]
    contours = []  # type: List[np.ndarray]

    for raw_id, raw_area in zip(source_ids, source_areas):
        source_id, area = int(raw_id), int(raw_area)
        if source_id == 0 or area < parameters["min_area"] or area > parameters["max_area"]:
            continue
        object_slice = object_slices[source_id - 1]
        if object_slice is None:
            continue
        y_slice, x_slice = object_slice
        component = labels[object_slice] == source_id
        touches_border = bool(
            y_slice.start == 0 or y_slice.stop == height
            or x_slice.start == 0 or x_slice.stop == width
        )
        if parameters["remove_edge_masks"] and touches_border:
            continue
        equivalent_diameter = float(math.sqrt(4.0 * area / math.pi))
        maximum_diameter = parameters["max_equivalent_diameter"]
        if maximum_diameter is not None and equivalent_diameter > maximum_diameter:
            continue
        component_u8 = component.astype(np.uint8) * 255
        local_contour = contour_for_mask(component_u8)
        if local_contour is None:
            continue
        contour = local_contour.copy()
        contour[:, :, 0] += int(x_slice.start)
        contour[:, :, 1] += int(y_slice.start)
        perimeter = float(cv2.arcLength(contour, True))
        circularity = min(
            float(4.0 * math.pi * area / (perimeter * perimeter)) if perimeter > 0 else 0.0,
            1.0,
        )
        if circularity < parameters["min_circularity"]:
            continue
        local_x, local_y, box_width, box_height = cv2.boundingRect(local_contour)
        x, y = int(x_slice.start) + local_x, int(y_slice.start) + local_y
        moments = cv2.moments(component_u8, binaryImage=True)
        center_x = float(moments["m10"] / moments["m00"] + int(x_slice.start))
        center_y = float(moments["m01"] / moments["m00"] + int(y_slice.start))
        object_id = len(objects) + 1
        if object_id > UINT16_MAX:
            raise ValueError("对象数超过 uint16 标签上限")
        filtered[object_slice][component] = object_id
        contours.append(contour)
        objects.append({
            "object_id": object_id,
            "center_x": center_x,
            "center_y": center_y,
            "area": area,
            "bbox": [x, y, int(box_width), int(box_height)],
            "equivalent_diameter": equivalent_diameter,
            "circularity": circularity,
            "touches_border": touches_border,
            "status": "candidate",
        })
    return filtered, objects, contours


def build_overlay(gray_preview: np.ndarray, objects: Sequence[Dict[str, Any]], contours: Sequence[np.ndarray]) -> np.ndarray:
    overlay = cv2.cvtColor(gray_preview, cv2.COLOR_GRAY2BGR)
    for item, contour in zip(objects, contours):
        cv2.drawContours(overlay, [contour], -1, (255, 255, 0), 1)
        center = (int(round(item["center_x"])), int(round(item["center_y"])))
        cv2.putText(overlay, str(item["object_id"]), center, cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1, cv2.LINE_AA)
    return overlay


def checked_imwrite(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise OSError("OpenCV 写入失败：{}".format(path))


def eval_model(model: Any, image: np.ndarray, parameters: Dict[str, Any]) -> np.ndarray:
    kwargs = {
        "channels": parameters["channels"],
        "diameter": parameters["diameter"],
        "normalize": parameters["normalize"],
        "do_3D": parameters["do_3D"],
    }
    if parameters["flow_threshold"] is not None:
        kwargs["flow_threshold"] = parameters["flow_threshold"]
    if parameters["cellprob_threshold"] is not None:
        kwargs["cellprob_threshold"] = parameters["cellprob_threshold"]
    result = model.eval(image, **kwargs)
    return np.asarray(result[0] if isinstance(result, tuple) else result)


def validate_saved_labels(path: Path, source_shape: Sequence[int], object_count: int) -> Dict[str, Any]:
    labels = np.asarray(tifffile.imread(str(path)))
    unique = np.unique(labels)
    positive = unique[unique > 0]
    stats = {
        "labels_shape": [int(value) for value in labels.shape],
        "labels_dtype": str(labels.dtype),
        "minimum_label": int(labels.min()),
        "maximum_label": int(labels.max()),
        "positive_unique_count": int(positive.size),
        "nonzero_pixels": int(np.count_nonzero(labels)),
        "is_binary": bool(unique.size <= 2),
    }
    if labels.dtype != np.uint16:
        raise ValueError("标签 dtype 不是 uint16")
    if tuple(labels.shape) != tuple(source_shape[:2]):
        raise ValueError("标签尺寸与 TRITC 不一致")
    if stats["minimum_label"] != 0:
        raise ValueError("标签背景不是 0")
    if object_count <= 0 or stats["positive_unique_count"] <= 0:
        raise ValueError("没有正标签对象")
    if stats["is_binary"]:
        raise ValueError("标签是普通二值图")
    if stats["maximum_label"] != object_count or stats["positive_unique_count"] != object_count:
        raise ValueError("标签数量与对象数量不一致")
    return stats


def process_field(item: Dict[str, Any], model: Any, runtime: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    started = time.perf_counter()
    tritc_path = Path(item["tritc_path"]).resolve()
    labels_path = Path(item["labels_output_path"]).resolve()
    overlay_path = Path(item["overlay_output_path"]).resolve()
    objects_path = Path(item["objects_output_path"]).resolve()
    merge_value = str(item.get("merge_path") or "").strip()
    merge_path = (
        str(Path(merge_value).resolve())
        if merge_value
        else ""
    )
    image = read_image(tritc_path)
    cellpose_input, gray_preview = extract_red_channel(image)
    eval_started = time.perf_counter()
    raw_labels = eval_model(model, cellpose_input, parameters)
    eval_ms = (time.perf_counter() - eval_started) * 1000.0
    post_started = time.perf_counter()
    labels, objects, contours = measure_and_filter(
        raw_labels,
        parameters,
        field_id=str(item["field_id"]),
    )
    overlay = build_overlay(gray_preview, objects, contours)
    postprocess_ms = (time.perf_counter() - post_started) * 1000.0
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(labels_path), labels, photometric="minisblack")
    checked_imwrite(overlay_path, overlay)
    object_payload = {
        "schema_version": "analysis_v2_head_initial_objects_v1",
        "field_id": item["field_id"],
        "tritc_path": str(tritc_path),
        "fitc_path": str(Path(item["fitc_path"]).resolve()),
        "merge_path": merge_path,
        "labels_path": str(labels_path),
        "overlay_path": str(overlay_path),
        "runtime": runtime,
        "parameters": parameters,
        "object_count": len(objects),
        "objects": objects,
    }
    atomic_write_json(objects_path, object_payload)
    validation = validate_saved_labels(labels_path, image.shape, len(objects))
    with Image.open(str(overlay_path)) as opened_overlay:
        overlay_size = [int(opened_overlay.size[0]), int(opened_overlay.size[1])]
    with objects_path.open("r", encoding="utf-8") as handle:
        saved_objects = json.load(handle)
    if len(saved_objects.get("objects", [])) != len(objects):
        raise ValueError("对象 JSON 数量不一致")
    total_ms = (time.perf_counter() - started) * 1000.0
    result = {
        "field_id": item["field_id"],
        "tritc_path": str(tritc_path),
        "fitc_path": str(Path(item["fitc_path"]).resolve()),
        "merge_path": merge_path,
        "labels_output_path": str(labels_path),
        "overlay_output_path": str(overlay_path),
        "objects_output_path": str(objects_path),
        "source_shape": [int(value) for value in image.shape],
        "source_dtype": str(image.dtype),
        "object_count": len(objects),
        "overlay_size": overlay_size,
        "eval_ms": eval_ms,
        "postprocess_ms": postprocess_ms,
        "total_ms": total_ms,
        "error": None,
    }
    result.update(validation)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json", required=True, type=Path)
    args = parser.parse_args()
    input_path = args.input_json.resolve()
    with input_path.open("r", encoding="utf-8") as handle:
        worker_input = json.load(handle)
    result_path = Path(worker_input["worker_result_path"]).resolve()
    parameters = dict(BASELINE_PARAMETERS)
    requested_parameters = worker_input.get("parameters", {})
    if requested_parameters != parameters:
        raise ValueError("本阶段只允许已验证的固定基线参数")
    gpu_requested = bool(worker_input.get("gpu", True))
    cuda_available = bool(torch.cuda.is_available())
    gpu_used = bool(gpu_requested and cuda_available)
    runtime = {
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "cellpose_version": importlib.metadata.version("cellpose"),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "cuda_available": cuda_available,
        "gpu_requested": gpu_requested,
        "gpu_used": gpu_used,
        "gpu_name": torch.cuda.get_device_name(0) if gpu_used else None,
        "model": parameters["model"],
    }
    overall_started = time.perf_counter()
    print("[DIRECT_CELLPOSE] 初始化模型 model={} gpu={}".format(parameters["model"], gpu_used), flush=True)
    model_started = time.perf_counter()
    try:
        model = models.CellposeModel(gpu=gpu_used, pretrained_model=parameters["model"])
    except BaseException as exception:
        model_init_ms = (time.perf_counter() - model_started) * 1000.0
        result = {
            "schema_version": SCHEMA_VERSION,
            "runtime": runtime,
            "parameters": parameters,
            "model_init_ms": model_init_ms,
            "total_runtime_ms": (time.perf_counter() - overall_started) * 1000.0,
            "success": False,
            "field_count": len(worker_input["fields"]),
            "successful_field_count": 0,
            "fields": [],
            "model_init_error": {
                "exception_type": type(exception).__name__,
                "exception_message": str(exception),
                "traceback": "".join(
                    traceback.format_exception(
                        type(exception),
                        exception,
                        exception.__traceback__,
                    )
                ),
            },
        }
        atomic_write_json(result_path, result)
        print("[DIRECT_CELLPOSE] 模型初始化失败：{}".format(exception), file=sys.stderr, flush=True)
        return 1
    model_init_ms = (time.perf_counter() - model_started) * 1000.0
    print("[DIRECT_CELLPOSE] 模型初始化完成 {:.3f}s".format(model_init_ms / 1000.0), flush=True)
    fields = []
    any_error = False
    for item in worker_input["fields"]:
        try:
            field_result = process_field(item, model, runtime, parameters)
            print(
                "[DIRECT_CELLPOSE] field={} objects={} eval={:.3f}s total={:.3f}s".format(
                    item["field_id"], field_result["object_count"],
                    field_result["eval_ms"] / 1000.0, field_result["total_ms"] / 1000.0,
                ),
                flush=True,
            )
        except BaseException as exception:
            any_error = True
            field_result = {
                "field_id": item.get("field_id"),
                "tritc_path": item.get("tritc_path"),
                "fitc_path": item.get("fitc_path"),
                "merge_path": item.get("merge_path"),
                "labels_output_path": item.get("labels_output_path"),
                "overlay_output_path": item.get("overlay_output_path"),
                "objects_output_path": item.get("objects_output_path"),
                "error": {
                    "exception_type": type(exception).__name__,
                    "exception_message": str(exception),
                    "traceback": "".join(traceback.format_exception(type(exception), exception, exception.__traceback__)),
                },
            }
            print("[DIRECT_CELLPOSE] field={} 失败：{}".format(item.get("field_id"), exception), file=sys.stderr, flush=True)
        fields.append(field_result)
    result = {
        "schema_version": SCHEMA_VERSION,
        "runtime": runtime,
        "parameters": parameters,
        "model_init_ms": model_init_ms,
        "total_runtime_ms": (time.perf_counter() - overall_started) * 1000.0,
        "success": not any_error,
        "field_count": len(fields),
        "successful_field_count": sum(1 for field in fields if field.get("error") is None),
        "fields": fields,
    }
    atomic_write_json(result_path, result)
    print("[DIRECT_CELLPOSE] worker_result={}".format(result_path), flush=True)
    return 1 if any_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
