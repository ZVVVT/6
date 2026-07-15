#!/usr/bin/env python
"""Benchmark a fast, traditional R-channel object preview pipeline.

This script is intentionally independent from the application's production
analysis flow.  It reads one image or a directory of images and writes preview
artifacts plus a CSV benchmark summary.
"""

import argparse
import csv
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
from PIL import Image


SUPPORTED_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}
SCHEMA_VERSION = "r_preview_v1"
ALGORITHM_NAME = "traditional_r_channel_preview"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="快速识别 R 通道红色对象并生成预览和基准结果。"
    )
    parser.add_argument("--input", required=True, help="输入图片或图片目录")
    parser.add_argument(
        "--output",
        help=(
            "输出目录；默认 workspace/experiments/r_preview/"
            "<YYYYMMDD_HHMMSS>"
        ),
    )
    parser.add_argument("--min-area", type=float, default=20.0, help="最小面积")
    parser.add_argument("--max-area", type=float, default=5000.0, help="最大面积")
    parser.add_argument(
        "--min-circularity", type=float, default=0.2, help="最小圆度"
    )
    parser.add_argument(
        "--threshold-method",
        choices=("otsu", "percentile"),
        default="otsu",
        help="阈值方法",
    )
    parser.add_argument(
        "--percentile",
        type=float,
        default=99.0,
        help="percentile 阈值方法使用的百分位数",
    )
    parser.add_argument(
        "--background-radius",
        type=int,
        default=31,
        help="背景估计的形态学核大小（偶数会调整为下一个奇数）",
    )
    parser.add_argument(
        "--watershed", action="store_true", help="启用分水岭以尝试分离粘连对象"
    )
    parser.add_argument(
        "--recursive", action="store_true", help="输入目录时递归扫描"
    )
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.min_area < 0:
        parser.error("--min-area 不能小于 0")
    if args.max_area < args.min_area:
        parser.error("--max-area 不能小于 --min-area")
    if not 0.0 <= args.min_circularity <= 1.0:
        parser.error("--min-circularity 必须在 0 到 1 之间")
    if not 0.0 <= args.percentile <= 100.0:
        parser.error("--percentile 必须在 0 到 100 之间")
    if args.background_radius < 1:
        parser.error("--background-radius 必须大于等于 1")


def default_output_dir() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return project_root / "workspace" / "experiments" / "r_preview" / timestamp


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
    """Read an image while preserving TIFF bit depth when possible."""
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


def normalize_to_uint8(channel: np.ndarray) -> np.ndarray:
    values = np.asarray(channel)
    if values.ndim != 2:
        raise ValueError("待归一化通道必须是二维数组")

    finite = np.isfinite(values)
    if not np.any(finite):
        raise ValueError("图片不包含有效像素")

    finite_values = values[finite].astype(np.float64, copy=False)
    low = float(np.min(finite_values))
    high = float(np.max(finite_values))
    if high <= low:
        return np.zeros(values.shape, dtype=np.uint8)

    scaled = (values.astype(np.float64) - low) * (255.0 / (high - low))
    scaled[~finite] = 0.0
    return np.clip(scaled, 0.0, 255.0).astype(np.uint8)


def extract_red_and_overlay_base(image: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    if image.ndim == 2:
        gray = normalize_to_uint8(image)
        return gray, np.repeat(gray[:, :, np.newaxis], 3, axis=2)

    if image.shape[2] == 1:
        gray = normalize_to_uint8(image[:, :, 0])
        return gray, np.repeat(gray[:, :, np.newaxis], 3, axis=2)

    red = normalize_to_uint8(image[:, :, 0])
    rgb_channels = [normalize_to_uint8(image[:, :, index]) for index in range(3)]
    return red, np.stack(rgb_channels, axis=2)


def effective_kernel_size(requested_size: int) -> int:
    return requested_size if requested_size % 2 == 1 else requested_size + 1


def segment_objects(
    red_channel: np.ndarray,
    threshold_method: str,
    percentile: float,
    background_kernel_size: int,
    use_watershed: bool,
) -> Tuple[np.ndarray, float]:
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (background_kernel_size, background_kernel_size)
    )
    background = cv2.morphologyEx(red_channel, cv2.MORPH_OPEN, kernel)
    corrected = cv2.subtract(red_channel, background)

    if threshold_method == "otsu":
        threshold_value, binary = cv2.threshold(
            corrected, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
    else:
        threshold_value = float(np.percentile(corrected, percentile))
        binary = np.where(corrected > threshold_value, 255, 0).astype(np.uint8)

    cleanup_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cleanup_kernel)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, cleanup_kernel)

    if use_watershed and np.any(binary):
        binary = apply_watershed(binary, corrected, cleanup_kernel)
    return binary, float(threshold_value)


def apply_watershed(
    binary: np.ndarray, corrected: np.ndarray, cleanup_kernel: np.ndarray
) -> np.ndarray:
    distance = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    max_distance = float(distance.max())
    if max_distance <= 0.0:
        return binary

    sure_foreground = np.where(distance >= 0.4 * max_distance, 255, 0).astype(
        np.uint8
    )
    sure_background = cv2.dilate(binary, cleanup_kernel, iterations=1)
    unknown = cv2.subtract(sure_background, sure_foreground)
    _count, markers = cv2.connectedComponents(sure_foreground)
    markers = markers.astype(np.int32) + 1
    markers[unknown == 255] = 0
    watershed_image = cv2.cvtColor(corrected, cv2.COLOR_GRAY2BGR)
    cv2.watershed(watershed_image, markers)
    return np.where(markers > 1, 255, 0).astype(np.uint8)


def contour_for_component(component_mask: np.ndarray) -> Optional[np.ndarray]:
    contours, _hierarchy = cv2.findContours(
        component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None
    return max(contours, key=cv2.contourArea)


def measure_and_filter(
    binary: np.ndarray,
    min_area: float,
    max_area: float,
    min_circularity: float,
) -> Tuple[np.ndarray, List[Dict[str, Any]], List[np.ndarray]]:
    component_count, components, stats, centroids = cv2.connectedComponentsWithStats(
        binary, connectivity=8
    )
    height, width = binary.shape
    accepted = []

    for component_id in range(1, component_count):
        area = int(stats[component_id, cv2.CC_STAT_AREA])
        if area < min_area or area > max_area:
            continue

        component_mask = np.where(components == component_id, 255, 0).astype(
            np.uint8
        )
        contour = contour_for_component(component_mask)
        if contour is None:
            continue
        perimeter = float(cv2.arcLength(contour, True))
        circularity = (
            float(4.0 * math.pi * area / (perimeter * perimeter))
            if perimeter > 0.0
            else 0.0
        )
        circularity = min(circularity, 1.0)
        if circularity < min_circularity:
            continue

        x = int(stats[component_id, cv2.CC_STAT_LEFT])
        y = int(stats[component_id, cv2.CC_STAT_TOP])
        box_width = int(stats[component_id, cv2.CC_STAT_WIDTH])
        box_height = int(stats[component_id, cv2.CC_STAT_HEIGHT])
        touches_border = (
            x == 0
            or y == 0
            or x + box_width >= width
            or y + box_height >= height
        )
        accepted.append(
            {
                "component_id": component_id,
                "center_x": float(centroids[component_id, 0]),
                "center_y": float(centroids[component_id, 1]),
                "area": area,
                "bbox": [x, y, box_width, box_height],
                "equivalent_diameter": float(math.sqrt(4.0 * area / math.pi)),
                "circularity": circularity,
                "touches_border": bool(touches_border),
                "status": "candidate",
                "contour": contour,
            }
        )

    if len(accepted) > np.iinfo(np.uint16).max:
        raise ValueError("过滤后对象数超过 uint16 label mask 可表示的上限")

    label_mask = np.zeros(binary.shape, dtype=np.uint16)
    objects = []
    contours = []
    for object_id, item in enumerate(accepted, start=1):
        label_mask[components == item["component_id"]] = object_id
        contours.append(item["contour"])
        objects.append(
            {
                "object_id": object_id,
                "center_x": item["center_x"],
                "center_y": item["center_y"],
                "area": item["area"],
                "bbox": item["bbox"],
                "equivalent_diameter": item["equivalent_diameter"],
                "circularity": item["circularity"],
                "touches_border": item["touches_border"],
                "status": item["status"],
            }
        )
    return label_mask, objects, contours


def build_overlay(
    overlay_base: np.ndarray,
    objects: Sequence[Dict[str, Any]],
    contours: Sequence[np.ndarray],
) -> np.ndarray:
    overlay = overlay_base.copy()
    for item, contour in zip(objects, contours):
        cv2.drawContours(overlay, [contour], -1, (0, 255, 0), 1)
        center = (int(round(item["center_x"])), int(round(item["center_y"])))
        cv2.putText(
            overlay,
            str(item["object_id"]),
            center,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (255, 255, 0),
            1,
            cv2.LINE_AA,
        )
    return overlay


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


def process_image(
    image_path: Path,
    output_dir: Path,
    view_name: str,
    args: argparse.Namespace,
) -> Dict[str, Any]:
    started = time.perf_counter()
    overlay_path = output_dir / "{0}_overlay.png".format(view_name)
    mask_path = output_dir / "{0}_labels.tif".format(view_name)
    json_path = output_dir / "{0}_objects.json".format(view_name)
    background_kernel_size = effective_kernel_size(args.background_radius)

    image = read_image(image_path)
    red_channel, overlay_base = extract_red_and_overlay_base(image)
    binary, threshold_value = segment_objects(
        red_channel,
        args.threshold_method,
        args.percentile,
        background_kernel_size,
        args.watershed,
    )
    label_mask, objects, contours = measure_and_filter(
        binary, args.min_area, args.max_area, args.min_circularity
    )
    overlay = build_overlay(overlay_base, objects, contours)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    parameters = {
        "min_area": args.min_area,
        "max_area": args.max_area,
        "min_circularity": args.min_circularity,
        "threshold_method": args.threshold_method,
        "percentile": args.percentile,
        "background_radius": args.background_radius,
        "effective_background_kernel_size": background_kernel_size,
        "watershed": args.watershed,
        "threshold_value": threshold_value,
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "view_name": view_name,
        "source_image": str(image_path.resolve()),
        "width": int(red_channel.shape[1]),
        "height": int(red_channel.shape[0]),
        "coordinate_system": "origin_top_left_x_right_y_down",
        "mask_path": relative_output_path(mask_path, output_dir),
        "overlay_path": relative_output_path(overlay_path, output_dir),
        "algorithm": ALGORITHM_NAME,
        "parameters": parameters,
        "elapsed_ms": elapsed_ms,
        "object_count": len(objects),
        "objects": objects,
    }

    tifffile.imwrite(str(mask_path), label_mask, photometric="minisblack")
    Image.fromarray(overlay, mode="RGB").save(str(overlay_path), format="PNG")
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    return {
        "image_path": str(image_path.resolve()),
        "object_count": len(objects),
        "elapsed_ms": "{0:.3f}".format(elapsed_ms),
        "overlay_path": str(overlay_path.resolve()),
        "mask_path": str(mask_path.resolve()),
        "json_path": str(json_path.resolve()),
        "error": "",
    }


def write_summary(output_dir: Path, rows: Sequence[Dict[str, Any]]) -> Path:
    summary_path = output_dir / "benchmark_summary.csv"
    fieldnames = [
        "image_path",
        "object_count",
        "elapsed_ms",
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

    output_dir.mkdir(parents=True, exist_ok=True)
    view_names = unique_view_names(images)
    rows = []  # type: List[Dict[str, Any]]

    for image_path in images:
        started = time.perf_counter()
        try:
            row = process_image(
                image_path, output_dir, view_names[image_path], args
            )
            print(
                "[PREVIEW] image={0} objects={1} elapsed={2:.3f}s".format(
                    image_path,
                    row["object_count"],
                    float(row["elapsed_ms"]) / 1000.0,
                )
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            row = {
                "image_path": str(image_path.resolve()),
                "object_count": 0,
                "elapsed_ms": "{0:.3f}".format(elapsed_ms),
                "overlay_path": "",
                "mask_path": "",
                "json_path": "",
                "error": "{0}: {1}".format(type(exc).__name__, exc),
            }
            print(
                "[PREVIEW] image={0} error={1} elapsed={2:.3f}s".format(
                    image_path, row["error"], elapsed_ms / 1000.0
                ),
                file=sys.stderr,
            )
        rows.append(row)

    summary_path = write_summary(output_dir, rows)
    print("[PREVIEW] summary saved: {0}".format(summary_path.resolve()))
    if not images:
        print("[PREVIEW] warning: no supported images found", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
