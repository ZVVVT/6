#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全图精子尾部中心线提取 Stage 1

一次性计算绿色尾部概率图，并输出 loose / balanced / strict
三套二值掩膜、骨架、端点/交叉点叠加图和统计 CSV。

本阶段只判断“全图骨架是否提取正确”，不做头尾分配，
也不会修改现有批量识别结果。
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    import cv2
    import numpy as np
    from PIL import Image
    from scipy.ndimage import convolve
    from skimage.filters import apply_hysteresis_threshold, meijering
    from skimage.measure import label, regionprops
    from skimage.morphology import (
        binary_closing,
        disk,
        remove_small_objects,
        skeletonize,
    )
except ImportError as exc:
    print("缺少依赖：", exc)
    raise SystemExit(1) from exc


@dataclass(frozen=True)
class Preset:
    name: str
    low_threshold: float
    high_threshold: float
    min_object_size: int
    min_skeleton_component: int
    close_radius: int


PRESETS = (
    Preset("loose", 0.035, 0.100, 18, 14, 1),
    Preset("balanced", 0.055, 0.140, 24, 18, 1),
    Preset("strict", 0.080, 0.200, 32, 24, 1),
)


def read_image(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image)


def robust_normalize(
    image: np.ndarray,
    low_p: float = 1.0,
    high_p: float = 99.8,
) -> np.ndarray:
    array = image.astype(np.float32, copy=False)
    low, high = np.percentile(array, [low_p, high_p])
    if high <= low:
        return np.zeros_like(array, dtype=np.float32)
    return np.clip((array - low) / (high - low), 0.0, 1.0).astype(
        np.float32
    )


def extract_green_channel(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image.astype(np.float32)
    if image.ndim == 3 and image.shape[2] >= 3:
        return image[..., 1].astype(np.float32)
    raise ValueError(f"无法识别绿色图像维度：{image.shape}")


def build_probability(
    green_image: np.ndarray,
    background_sigma: float,
    ridge_sigmas: list[float],
    raw_weight: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    green = extract_green_channel(green_image)
    raw = robust_normalize(green, 0.5, 99.8)

    background = cv2.GaussianBlur(
        raw,
        (0, 0),
        sigmaX=background_sigma,
        sigmaY=background_sigma,
    )
    corrected = np.clip(raw - background, 0.0, None)
    corrected = robust_normalize(corrected, 0.5, 99.8)

    print(f"正在计算全图线状增强：sigmas={ridge_sigmas}")
    ridge = meijering(
        corrected,
        sigmas=ridge_sigmas,
        black_ridges=False,
    )
    ridge = robust_normalize(ridge, 0.5, 99.8)

    raw_weight = float(np.clip(raw_weight, 0.0, 1.0))
    probability = raw_weight * corrected + (1.0 - raw_weight) * ridge
    probability = robust_normalize(probability, 0.5, 99.8)
    return corrected, ridge, probability


def to_uint8_rgb(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        normalized = robust_normalize(image, 0.2, 99.8)
        gray = np.clip(normalized * 255.0, 0, 255).astype(np.uint8)
        return np.repeat(gray[..., None], 3, axis=2)

    if image.ndim == 3 and image.shape[2] >= 3:
        rgb = np.zeros(image.shape[:2] + (3,), dtype=np.uint8)
        for channel in range(3):
            normalized = robust_normalize(image[..., channel], 0.2, 99.8)
            rgb[..., channel] = np.clip(
                normalized * 255.0,
                0,
                255,
            ).astype(np.uint8)
        return rgb

    raise ValueError(f"无法转换Merge图维度：{image.shape}")


def save_float_tiff(path: Path, image: np.ndarray) -> None:
    array = np.round(np.clip(image, 0.0, 1.0) * 65535.0).astype(
        np.uint16
    )
    Image.fromarray(array).save(path)


def save_binary_tiff(path: Path, image: np.ndarray) -> None:
    Image.fromarray(np.asarray(image, dtype=np.uint8) * 255).save(path)


def parse_sigmas(value: str) -> list[float]:
    values = [float(item.strip()) for item in value.split(",") if item.strip()]
    if not values:
        raise argparse.ArgumentTypeError("ridge sigmas不能为空")
    return values


def resolve_required_path(supplied: str | None, default_name: str) -> Path:
    candidate = Path(supplied or default_name).expanduser()
    if not candidate.exists():
        raise FileNotFoundError(f"找不到文件：{candidate.resolve()}")
    return candidate.resolve()


def build_mask_and_skeleton(
    probability: np.ndarray,
    head_labels: np.ndarray,
    preset: Preset,
    head_exclusion_radius: int,
) -> tuple[np.ndarray, np.ndarray]:
    mask = apply_hysteresis_threshold(
        probability,
        low=preset.low_threshold,
        high=preset.high_threshold,
    )

    if preset.close_radius > 0:
        mask = binary_closing(mask, footprint=disk(preset.close_radius))

    mask = remove_small_objects(
        mask.astype(bool),
        min_size=preset.min_object_size,
        connectivity=2,
    )

    head_mask = head_labels > 0
    if head_exclusion_radius > 0:
        radius = int(head_exclusion_radius)
        kernel = np.ones((2 * radius + 1, 2 * radius + 1), dtype=np.uint8)
        head_mask = cv2.dilate(
            head_mask.astype(np.uint8),
            kernel,
            iterations=1,
        ) > 0

    mask &= ~head_mask

    skeleton = skeletonize(mask)
    skeleton = remove_small_objects(
        skeleton.astype(bool),
        min_size=preset.min_skeleton_component,
        connectivity=2,
    )
    return mask.astype(bool), skeleton.astype(bool)


def analyse_skeleton(skeleton: np.ndarray) -> tuple[dict, np.ndarray, np.ndarray]:
    kernel = np.ones((3, 3), dtype=np.uint8)
    kernel[1, 1] = 0
    degree = convolve(
        skeleton.astype(np.uint8),
        kernel,
        mode="constant",
        cval=0,
    )
    degree *= skeleton.astype(np.uint8)

    endpoint_mask = skeleton & (degree == 1)
    junction_mask = skeleton & (degree >= 3)
    component_labels = label(skeleton, connectivity=2)

    component_sizes = np.asarray(
        [region.area for region in regionprops(component_labels)],
        dtype=float,
    )

    stats = {
        "skeleton_pixel_count": int(skeleton.sum()),
        "connected_component_count": int(component_labels.max()),
        "endpoint_count": int(label(endpoint_mask, connectivity=2).max()),
        "junction_cluster_count": int(
            label(junction_mask, connectivity=2).max()
        ),
        "median_component_pixels": (
            float(np.median(component_sizes)) if len(component_sizes) else 0.0
        ),
        "p90_component_pixels": (
            float(np.percentile(component_sizes, 90.0))
            if len(component_sizes)
            else 0.0
        ),
        "max_component_pixels": (
            int(component_sizes.max()) if len(component_sizes) else 0
        ),
    }
    return stats, endpoint_mask, junction_mask


def make_overlay(
    merge_rgb: np.ndarray,
    skeleton: np.ndarray,
    endpoint_mask: np.ndarray,
    junction_mask: np.ndarray,
) -> np.ndarray:
    overlay = merge_rgb.copy()

    skeleton_pixels = skeleton.astype(bool)
    original = overlay[skeleton_pixels].astype(np.float32)
    cyan = np.asarray([0, 255, 255], dtype=np.float32)
    overlay[skeleton_pixels] = np.clip(
        0.40 * original + 0.60 * cyan,
        0,
        255,
    ).astype(np.uint8)

    endpoint_labels = label(endpoint_mask, connectivity=2)
    for region in regionprops(endpoint_labels):
        y, x = region.centroid
        cv2.circle(
            overlay,
            (int(round(x)), int(round(y))),
            5,
            (255, 0, 0),
            1,
            lineType=cv2.LINE_AA,
        )

    junction_labels = label(junction_mask, connectivity=2)
    for region in regionprops(junction_labels):
        y, x = region.centroid
        cv2.circle(
            overlay,
            (int(round(x)), int(round(y))),
            6,
            (255, 255, 0),
            1,
            lineType=cv2.LINE_AA,
        )

    return overlay


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="全图精子尾部中心线提取 Stage 1"
    )
    parser.add_argument("--green", help="默认当前目录1_G.tif")
    parser.add_argument("--merge", help="默认当前目录1_Merge.tif")
    parser.add_argument(
        "--head-labels",
        help="默认当前目录1_R_R_uint16.tiff",
    )
    parser.add_argument(
        "--output-dir",
        default="tail_graph_stage1_output",
    )
    parser.add_argument("--background-sigma", type=float, default=15.0)
    parser.add_argument(
        "--ridge-sigmas",
        type=parse_sigmas,
        default=[1.0, 2.0],
    )
    parser.add_argument("--raw-weight", type=float, default=0.35)
    parser.add_argument(
        "--head-exclusion-radius",
        type=int,
        default=2,
    )
    return parser


def main() -> int:
    started = time.perf_counter()
    args = build_parser().parse_args()

    green_path = resolve_required_path(args.green, "1_G.tif")
    merge_path = resolve_required_path(args.merge, "1_Merge.tif")
    head_labels_path = resolve_required_path(
        args.head_labels,
        "1_R_R_uint16.tiff",
    )
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print("绿色原图：", green_path)
    print("Merge图：", merge_path)
    print("头部标签：", head_labels_path)
    print("输出目录：", output_dir)

    green_image = read_image(green_path)
    merge_image = read_image(merge_path)
    head_labels = read_image(head_labels_path)

    if head_labels.ndim != 2:
        raise ValueError("头部标签必须是二维单通道图")
    head_labels = head_labels.astype(np.uint16, copy=False)

    expected_shape = head_labels.shape
    if green_image.shape[:2] != expected_shape or merge_image.shape[:2] != expected_shape:
        raise ValueError(
            "图像尺寸不一致：\n"
            f"green={green_image.shape[:2]}\n"
            f"merge={merge_image.shape[:2]}\n"
            f"head_labels={expected_shape}"
        )

    corrected, ridge, probability = build_probability(
        green_image,
        background_sigma=args.background_sigma,
        ridge_sigmas=args.ridge_sigmas,
        raw_weight=args.raw_weight,
    )

    save_float_tiff(output_dir / "00_corrected_uint16.tif", corrected)
    save_float_tiff(output_dir / "01_ridge_uint16.tif", ridge)
    save_float_tiff(output_dir / "02_probability_uint16.tif", probability)

    merge_rgb = to_uint8_rgb(merge_image)
    summary_rows = []

    for preset in PRESETS:
        preset_started = time.perf_counter()
        print(f"\n正在生成骨架预设：{preset.name}")

        mask, skeleton = build_mask_and_skeleton(
            probability,
            head_labels,
            preset,
            head_exclusion_radius=args.head_exclusion_radius,
        )
        stats, endpoint_mask, junction_mask = analyse_skeleton(skeleton)

        save_binary_tiff(
            output_dir / f"{preset.name}_mask_uint8.tif",
            mask,
        )
        save_binary_tiff(
            output_dir / f"{preset.name}_skeleton_uint8.tif",
            skeleton,
        )

        overlay = make_overlay(
            merge_rgb,
            skeleton,
            endpoint_mask,
            junction_mask,
        )
        Image.fromarray(overlay).save(
            output_dir / f"{preset.name}_graph_nodes_overlay.png"
        )

        preset_elapsed = time.perf_counter() - preset_started
        row = {
            "preset": preset.name,
            "low_threshold": preset.low_threshold,
            "high_threshold": preset.high_threshold,
            "mask_pixel_count": int(mask.sum()),
            **stats,
            "elapsed_seconds": preset_elapsed,
        }
        summary_rows.append(row)

        payload = {
            "version": "tail_graph_stage1_extract_v1",
            "preset": asdict(preset),
            "head_exclusion_radius": int(args.head_exclusion_radius),
            "image_width": int(expected_shape[1]),
            "image_height": int(expected_shape[0]),
            "stats": stats,
        }
        (output_dir / f"{preset.name}_stats.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(
            f"{preset.name} 完成："
            f"components={stats['connected_component_count']}，"
            f"endpoints={stats['endpoint_count']}，"
            f"junctions={stats['junction_cluster_count']}，"
            f"elapsed={preset_elapsed:.2f}s"
        )

    summary_path = output_dir / "tail_graph_stage1_summary.csv"
    with summary_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    elapsed = time.perf_counter() - started
    print("\nStage 1完成。")
    print(f"总耗时：{elapsed:.2f}s")
    print("请比较三张 *_graph_nodes_overlay.png，并上传summary.csv。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
