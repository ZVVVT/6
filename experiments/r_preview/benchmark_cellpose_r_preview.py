#!/usr/bin/env python
"""Benchmark direct Cellpose segmentation of the R channel.

This experiment is independent from the production application and pipeline.
It initializes Cellpose once, processes one image or a directory, and writes
preview artifacts plus a CSV timing summary.
"""

import argparse
import csv
import importlib.metadata
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
import tifffile
import torch
from cellpose import models
from PIL import Image
from scipy import ndimage


SUPPORTED_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}
SCHEMA_VERSION = "r_preview_cellpose_v1"
ALGORITHM_NAME = "cellpose_r_channel_preview"
UINT16_MAX = int(np.iinfo(np.uint16).max)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="直接调用 Cellpose 识别图像 R 通道并生成预览与性能统计。"
    )
    parser.add_argument("--input", required=True, help="输入单张图片或图片目录")
    parser.add_argument(
        "--output",
        help=(
            "输出目录；默认 workspace/experiments/r_preview_cellpose/"
            "<YYYYMMDD_HHMMSS>"
        ),
    )
    parser.add_argument(
        "--recursive", action="store_true", help="输入目录时递归扫描"
    )
    gpu_group = parser.add_mutually_exclusive_group()
    gpu_group.add_argument(
        "--gpu", dest="gpu", action="store_true", help="使用 GPU（默认）"
    )
    gpu_group.add_argument(
        "--no-gpu", dest="gpu", action="store_false", help="强制使用 CPU"
    )
    parser.set_defaults(gpu=True)
    parser.add_argument(
        "--model",
        default="cpsam",
        help="Cellpose 模型名称或本地模型文件完整路径（默认：cpsam）",
    )
    parser.add_argument(
        "--diameter",
        type=float,
        default=None,
        help="Cellpose 对象直径；省略或设为 0 时使用模型默认值",
    )
    parser.add_argument(
        "--flow-threshold", type=float, default=None, help="Cellpose flow threshold"
    )
    parser.add_argument(
        "--cellprob-threshold",
        type=float,
        default=None,
        help="Cellpose cell probability threshold",
    )
    parser.add_argument("--min-area", type=float, default=20.0, help="最小对象面积")
    parser.add_argument("--max-area", type=float, default=5000.0, help="最大对象面积")
    parser.add_argument(
        "--max-equivalent-diameter",
        type=float,
        default=None,
        help="最大对象等效直径",
    )
    parser.add_argument(
        "--remove-edge-masks",
        action="store_true",
        help="移除所有接触图像边缘的对象",
    )
    parser.add_argument(
        "--min-circularity", type=float, default=0.2, help="最小对象圆度"
    )
    parser.add_argument(
        "--max-images", type=int, default=None, help="最多处理的图片数"
    )
    parser.add_argument(
        "--debug", action="store_true", help="保存实际送入 Cellpose 的灰度图"
    )
    parser.add_argument(
        "--overlay-mode",
        choices=("gray", "red", "rgb"),
        default="gray",
        help="Overlay 底图显示模式（默认：gray）",
    )
    parser.add_argument(
        "--outline-color",
        default="00ffff",
        help="轮廓线 RGB 十六进制颜色，可带 #（默认：00ffff）",
    )
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    outline_color = args.outline_color.strip()
    if outline_color.startswith("#"):
        outline_color = outline_color[1:]
    if len(outline_color) != 6 or any(
        character not in "0123456789abcdefABCDEF" for character in outline_color
    ):
        parser.error(
            "--outline-color 必须是 6 位 RGB 十六进制颜色，例如 00ffff 或 #00ffff"
        )
    args.outline_color = outline_color.lower()
    if args.diameter is not None and args.diameter < 0:
        parser.error("--diameter 不能小于 0")
    if args.min_area < 0:
        parser.error("--min-area 不能小于 0")
    if args.max_area < args.min_area:
        parser.error("--max-area 不能小于 --min-area")
    if (
        args.max_equivalent_diameter is not None
        and args.max_equivalent_diameter < 0
    ):
        parser.error("--max-equivalent-diameter 不能小于 0")
    if not 0.0 <= args.min_circularity <= 1.0:
        parser.error("--min-circularity 必须在 0 到 1 之间")
    if args.max_images is not None and args.max_images < 1:
        parser.error("--max-images 必须大于等于 1")


def default_output_dir() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        project_root
        / "workspace"
        / "experiments"
        / "r_preview_cellpose"
        / timestamp
    )


def discover_images(input_path: Path, recursive: bool) -> List[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError("不支持的图片格式: {0}".format(input_path.suffix))
        return [input_path]
    if not input_path.is_dir():
        raise ValueError("输入路径不存在或不是文件/目录: {0}".format(input_path))

    iterator = input_path.rglob("*") if recursive else input_path.glob("*")
    images = [
        path
        for path in iterator
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return sorted(images, key=lambda path: str(path).lower())


def read_image(path: Path) -> np.ndarray:
    """Read an image, preferring tifffile for TIFF bit-depth preservation."""
    if path.suffix.lower() in (".tif", ".tiff"):
        array = np.asarray(tifffile.imread(str(path)))
    else:
        with Image.open(str(path)) as image:
            if image.mode in ("RGB", "RGBA", "L", "I", "F", "1") or image.mode.startswith(
                "I;16"
            ):
                prepared = image
            elif image.mode == "P":
                prepared = image.convert("RGBA" if "transparency" in image.info else "RGB")
            else:
                prepared = image.convert("RGB")
            array = np.asarray(prepared).copy()

    if array.ndim == 2:
        return array
    if array.ndim == 3 and array.shape[2] in (1, 3, 4):
        return array
    raise ValueError(
        "仅支持二维灰度或 HxWx1/3/4 图片，实际 shape={0}".format(array.shape)
    )


def normalize_channel(channel: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Return Cellpose float32 input and an equivalent uint8 preview."""
    values = np.asarray(channel)
    if values.ndim != 2:
        raise ValueError("待归一化通道必须是二维数组")

    finite = np.isfinite(values)
    if not np.any(finite):
        raise ValueError("图片不包含有效像素")
    finite_values = values[finite].astype(np.float32, copy=False)
    low, high = np.percentile(finite_values, (1.0, 99.0))
    low = float(low)
    high = float(high)
    if high <= low:
        low = float(np.min(finite_values))
        high = float(np.max(finite_values))
    if high <= low:
        normalized = np.zeros(values.shape, dtype=np.float32)
    else:
        normalized = (values.astype(np.float32) - low) / (high - low)
        normalized[~finite] = 0.0
        normalized = np.clip(normalized, 0.0, 1.0).astype(np.float32, copy=False)
    preview = np.rint(normalized * 255.0).astype(np.uint8)
    return normalized, preview


def extract_red_channel(image: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    if image.ndim == 2:
        return normalize_channel(image)
    if image.shape[2] == 1:
        return normalize_channel(image[:, :, 0])

    return normalize_channel(image[:, :, 0])


def build_overlay_base(
    image: np.ndarray, gray_preview: np.ndarray, overlay_mode: str
) -> np.ndarray:
    if overlay_mode == "gray":
        return cv2.cvtColor(gray_preview, cv2.COLOR_GRAY2BGR)
    if overlay_mode == "red":
        overlay_base = np.zeros(
            (gray_preview.shape[0], gray_preview.shape[1], 3), dtype=np.uint8
        )
        overlay_base[:, :, 2] = gray_preview
        return overlay_base
    if overlay_mode == "rgb":
        if image.ndim == 2 or image.shape[2] == 1:
            return cv2.cvtColor(gray_preview, cv2.COLOR_GRAY2BGR)
        rgb_channels = [normalize_channel(image[:, :, index])[1] for index in range(3)]
        rgb = np.stack(rgb_channels, axis=2)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    raise ValueError("Unsupported overlay mode: {0}".format(overlay_mode))


def contour_for_mask(component_mask: np.ndarray) -> Optional[np.ndarray]:
    contours, _hierarchy = cv2.findContours(
        component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None
    return max(contours, key=cv2.contourArea)


def measure_and_filter(
    raw_labels: np.ndarray,
    min_area: float,
    max_area: float,
    min_circularity: float,
    remove_edge_masks: bool = False,
    max_equivalent_diameter: Optional[float] = None,
) -> Tuple[np.ndarray, List[Dict[str, Any]], List[np.ndarray]]:
    labels = np.asarray(raw_labels)
    labels = np.squeeze(labels)
    if labels.ndim != 2:
        raise ValueError("Cellpose label mask 不是二维数组: shape={0}".format(labels.shape))
    if np.any(labels < 0):
        raise ValueError("Cellpose label mask 包含负标签")

    height, width = labels.shape
    source_ids, source_areas = np.unique(labels, return_counts=True)
    object_slices = ndimage.find_objects(labels)

    filtered_labels = np.zeros((height, width), dtype=np.uint16)
    objects = []  # type: List[Dict[str, Any]]
    contours = []  # type: List[np.ndarray]
    for raw_source_id, raw_area in zip(source_ids, source_areas):
        source_id = int(raw_source_id)
        if source_id == 0:
            continue
        area = int(raw_area)
        if area < min_area or area > max_area:
            continue

        object_slice = object_slices[source_id - 1]
        if object_slice is None:
            continue
        y_slice, x_slice = object_slice
        component = labels[object_slice] == source_id
        touches_border = bool(
            y_slice.start == 0
            or y_slice.stop == height
            or x_slice.start == 0
            or x_slice.stop == width
        )
        if remove_edge_masks and touches_border:
            continue

        equivalent_diameter = float(math.sqrt(4.0 * area / math.pi))
        if (
            max_equivalent_diameter is not None
            and equivalent_diameter > max_equivalent_diameter
        ):
            continue

        component_u8 = component.astype(np.uint8) * 255
        local_contour = contour_for_mask(component_u8)
        if local_contour is None:
            continue

        contour = local_contour.copy()
        contour[:, :, 0] += int(x_slice.start)
        contour[:, :, 1] += int(y_slice.start)
        perimeter = float(cv2.arcLength(contour, True))
        circularity = (
            float(4.0 * math.pi * area / (perimeter * perimeter))
            if perimeter > 0.0
            else 0.0
        )
        circularity = min(circularity, 1.0)
        if circularity < min_circularity:
            continue

        local_x, local_y, box_width, box_height = cv2.boundingRect(local_contour)
        x = int(x_slice.start) + local_x
        y = int(y_slice.start) + local_y
        moments = cv2.moments(component_u8, binaryImage=True)
        if moments["m00"] > 0.0:
            center_x = float(
                moments["m10"] / moments["m00"] + int(x_slice.start)
            )
            center_y = float(
                moments["m01"] / moments["m00"] + int(y_slice.start)
            )
        else:
            center_x = float(x + box_width / 2.0)
            center_y = float(y + box_height / 2.0)
        object_id = len(objects) + 1
        if object_id > UINT16_MAX:
            raise ValueError("过滤后对象数超过 uint16 label mask 可表示的上限")

        filtered_region = filtered_labels[object_slice]
        filtered_region[component] = object_id
        contours.append(contour)
        objects.append(
            {
                "object_id": object_id,
                "center_x": center_x,
                "center_y": center_y,
                "area": area,
                "bbox": [int(x), int(y), int(box_width), int(box_height)],
                "equivalent_diameter": equivalent_diameter,
                "circularity": circularity,
                "touches_border": touches_border,
                "status": "candidate",
            }
        )
    return filtered_labels, objects, contours


def build_overlay(
    overlay_base: np.ndarray,
    objects: Sequence[Dict[str, Any]],
    contours: Sequence[np.ndarray],
    outline_color: Tuple[int, int, int],
) -> np.ndarray:
    overlay = overlay_base.copy()
    for item, contour in zip(objects, contours):
        cv2.drawContours(overlay, [contour], -1, outline_color, 1)
        center = (int(round(item["center_x"])), int(round(item["center_y"])))
        cv2.putText(
            overlay,
            str(item["object_id"]),
            center,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return overlay


def rgb_hex_to_bgr(rgb_hex: str) -> Tuple[int, int, int]:
    red = int(rgb_hex[0:2], 16)
    green = int(rgb_hex[2:4], 16)
    blue = int(rgb_hex[4:6], 16)
    return blue, green, red


def unique_view_names(images: Sequence[Path]) -> Dict[Path, str]:
    counts = {}  # type: Dict[str, int]
    names = {}  # type: Dict[Path, str]
    for image_path in images:
        stem = image_path.stem
        key = stem.lower()
        counts[key] = counts.get(key, 0) + 1
        suffix = "" if counts[key] == 1 else "_{0}".format(counts[key])
        names[image_path] = "{0}{1}".format(stem, suffix)
    return names


def relative_output_path(path: Path, output_dir: Path) -> str:
    return path.relative_to(output_dir).as_posix()


def checked_imwrite(path: Path, image: np.ndarray) -> None:
    if not cv2.imwrite(str(path), image):
        raise OSError("OpenCV 写入失败: {0}".format(path))


def cellpose_eval(model: Any, image: np.ndarray, args: argparse.Namespace) -> np.ndarray:
    diameter = args.diameter if args.diameter and args.diameter > 0 else None
    eval_kwargs = {
        "channels": [0, 0],
        "diameter": diameter,
        "normalize": False,
        "do_3D": False,
    }  # type: Dict[str, Any]
    if args.flow_threshold is not None:
        eval_kwargs["flow_threshold"] = args.flow_threshold
    if args.cellprob_threshold is not None:
        eval_kwargs["cellprob_threshold"] = args.cellprob_threshold
    result = model.eval(image, **eval_kwargs)
    if isinstance(result, tuple):
        return np.asarray(result[0])
    return np.asarray(result)


def process_image(
    image_path: Path,
    output_dir: Path,
    view_name: str,
    args: argparse.Namespace,
    model: Any,
    runtime: Dict[str, Any],
    model_init_ms: float,
) -> Dict[str, Any]:
    total_started = time.perf_counter()
    overlay_path = output_dir / "{0}_cellpose_overlay.png".format(view_name)
    mask_path = output_dir / "{0}_cellpose_labels.tif".format(view_name)
    json_path = output_dir / "{0}_cellpose_objects.json".format(view_name)
    debug_path = output_dir / "{0}_cellpose_input_gray.png".format(view_name)

    image = read_image(image_path)
    cellpose_input, gray_preview = extract_red_channel(image)
    overlay_base = build_overlay_base(image, gray_preview, args.overlay_mode)

    eval_started = time.perf_counter()
    raw_labels = cellpose_eval(model, cellpose_input, args)
    eval_ms = (time.perf_counter() - eval_started) * 1000.0

    post_started = time.perf_counter()
    label_mask, objects, contours = measure_and_filter(
        raw_labels,
        args.min_area,
        args.max_area,
        args.min_circularity,
        args.remove_edge_masks,
        args.max_equivalent_diameter,
    )
    overlay = build_overlay(
        overlay_base, objects, contours, rgb_hex_to_bgr(args.outline_color)
    )
    postprocess_ms = (time.perf_counter() - post_started) * 1000.0

    parameters = {
        "diameter": args.diameter if args.diameter and args.diameter > 0 else None,
        "flow_threshold": args.flow_threshold,
        "cellprob_threshold": args.cellprob_threshold,
        "min_area": args.min_area,
        "max_area": args.max_area,
        "remove_edge_masks": args.remove_edge_masks,
        "max_equivalent_diameter": args.max_equivalent_diameter,
        "min_circularity": args.min_circularity,
        "overlay_mode": args.overlay_mode,
        "normalization": "percentile_1_99_to_float32_0_1",
        "cellpose_normalize": False,
    }
    performance = {
        "model_init_ms": model_init_ms,
        "per_image_eval_ms": eval_ms,
        "postprocess_ms": postprocess_ms,
        "save_ms": 0.0,
        "total_ms": 0.0,
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "view_name": view_name,
        "source_image": str(image_path.resolve()),
        "width": int(label_mask.shape[1]),
        "height": int(label_mask.shape[0]),
        "coordinate_system": "origin_top_left_x_right_y_down",
        "mask_path": relative_output_path(mask_path, output_dir),
        "overlay_path": relative_output_path(overlay_path, output_dir),
        "algorithm": ALGORITHM_NAME,
        "cellpose": runtime,
        "parameters": parameters,
        "performance": performance,
        "object_count": len(objects),
        "objects": objects,
    }

    save_started = time.perf_counter()
    tifffile.imwrite(str(mask_path), label_mask, photometric="minisblack")
    checked_imwrite(overlay_path, overlay)
    if args.debug:
        checked_imwrite(debug_path, gray_preview)
    # JSON records artifact-save time measured immediately before its own write.
    performance["save_ms"] = (time.perf_counter() - save_started) * 1000.0
    performance["total_ms"] = (time.perf_counter() - total_started) * 1000.0
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    save_ms = (time.perf_counter() - save_started) * 1000.0
    total_ms = (time.perf_counter() - total_started) * 1000.0

    return {
        "image_path": str(image_path.resolve()),
        "object_count": len(objects),
        "model_init_ms": "{0:.3f}".format(model_init_ms),
        "eval_ms": "{0:.3f}".format(eval_ms),
        "postprocess_ms": "{0:.3f}".format(postprocess_ms),
        "save_ms": "{0:.3f}".format(save_ms),
        "total_ms": "{0:.3f}".format(total_ms),
        "overlay_path": str(overlay_path.resolve()),
        "mask_path": str(mask_path.resolve()),
        "json_path": str(json_path.resolve()),
        "error": "",
    }


def error_row(image_path: Path, model_init_ms: float, error: Exception) -> Dict[str, Any]:
    return {
        "image_path": str(image_path.resolve()),
        "object_count": 0,
        "model_init_ms": "{0:.3f}".format(model_init_ms),
        "eval_ms": "",
        "postprocess_ms": "",
        "save_ms": "",
        "total_ms": "",
        "overlay_path": "",
        "mask_path": "",
        "json_path": "",
        "error": "{0}: {1}".format(type(error).__name__, error),
    }


def write_summary(output_dir: Path, rows: Sequence[Dict[str, Any]]) -> Path:
    summary_path = output_dir / "benchmark_summary.csv"
    fieldnames = [
        "image_path",
        "object_count",
        "model_init_ms",
        "eval_ms",
        "postprocess_ms",
        "save_ms",
        "total_ms",
        "overlay_path",
        "mask_path",
        "json_path",
        "error",
    ]
    with summary_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return summary_path


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    validate_args(parser, args)

    input_path = Path(args.input).expanduser().resolve()
    output_dir = (
        Path(args.output).expanduser().resolve() if args.output else default_output_dir()
    )
    try:
        images = discover_images(input_path, args.recursive)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.max_images is not None:
        images = images[: args.max_images]

    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []  # type: List[Dict[str, Any]]
    if not images:
        summary_path = write_summary(output_dir, rows)
        print("[CELLPOSE_PREVIEW] warning: no supported images found", file=sys.stderr)
        print("[CELLPOSE_PREVIEW] summary saved: {0}".format(summary_path.resolve()))
        return 0

    cellpose_version = importlib.metadata.version("cellpose")
    torch_version = torch.__version__
    cuda_available = bool(torch.cuda.is_available())
    effective_gpu = bool(args.gpu and cuda_available)
    runtime = {
        "version": cellpose_version,
        "torch_version": torch_version,
        "cuda_available": cuda_available,
        "gpu": effective_gpu,
        "gpu_requested": bool(args.gpu),
        "model": args.model,
    }

    model_started = time.perf_counter()
    try:
        model = models.CellposeModel(
            gpu=effective_gpu,
            pretrained_model=args.model,
        )
    except Exception as exc:
        model_init_ms = (time.perf_counter() - model_started) * 1000.0
        for image_path in images:
            rows.append(error_row(image_path, model_init_ms, exc))
        summary_path = write_summary(output_dir, rows)
        print(
            "[CELLPOSE_PREVIEW] model_init_error={0}: {1}".format(
                type(exc).__name__, exc
            ),
            file=sys.stderr,
        )
        print("[CELLPOSE_PREVIEW] summary saved: {0}".format(summary_path.resolve()))
        return 1
    model_init_ms = (time.perf_counter() - model_started) * 1000.0
    print(
        "[CELLPOSE_PREVIEW] model={0} model_init={1:.3f}s gpu={2} "
        "cuda_available={3}".format(
            args.model, model_init_ms / 1000.0, effective_gpu, cuda_available
        )
    )

    view_names = unique_view_names(images)
    for image_path in images:
        image_started = time.perf_counter()
        try:
            row = process_image(
                image_path,
                output_dir,
                view_names[image_path],
                args,
                model,
                runtime,
                model_init_ms,
            )
            print(
                "[CELLPOSE_PREVIEW] image={0} objects={1} total={2:.3f}s "
                "eval={3:.3f}s post={4:.3f}s save={5:.3f}s".format(
                    image_path,
                    row["object_count"],
                    float(row["total_ms"]) / 1000.0,
                    float(row["eval_ms"]) / 1000.0,
                    float(row["postprocess_ms"]) / 1000.0,
                    float(row["save_ms"]) / 1000.0,
                )
            )
        except Exception as exc:
            row = error_row(image_path, model_init_ms, exc)
            row["total_ms"] = "{0:.3f}".format(
                (time.perf_counter() - image_started) * 1000.0
            )
            print(
                "[CELLPOSE_PREVIEW] image={0} error={1} total={2:.3f}s".format(
                    image_path, row["error"], float(row["total_ms"]) / 1000.0
                ),
                file=sys.stderr,
            )
        rows.append(row)

    summary_path = write_summary(output_dir, rows)
    print("[CELLPOSE_PREVIEW] summary saved: {0}".format(summary_path.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
