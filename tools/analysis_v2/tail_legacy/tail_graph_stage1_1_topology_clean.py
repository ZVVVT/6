#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全图精子尾部骨架拓扑清理：Stage 1.1

读取 Stage 1 的 strict 骨架，解决两个问题：
1. 8邻域楼梯状像素造成大量伪交叉点；
2. 二值骨架上的短毛刺造成大量伪端点。

程序同时输出 prune10 / prune20 / prune30 三个版本，便于比较。
本阶段仍不做头尾一一匹配，也不修改原有批量识别结果。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from pathlib import Path
from typing import List, Optional, Set, Tuple

try:
    import cv2
    import numpy as np
    from PIL import Image
    from skimage.measure import label, regionprops
    from skimage.morphology import disk, dilation
except ImportError as exc:
    print("缺少依赖：", exc)
    raise SystemExit(1) from exc


def read_image(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image)


def resolve_required(path: Optional[str], default_path: Path) -> Path:
    candidate = Path(path).expanduser() if path else default_path
    candidate = candidate.resolve()
    if not candidate.exists():
        raise FileNotFoundError(f"找不到文件：{candidate}")
    return candidate


def parse_int_list(value: str) -> List[int]:
    result = []
    for item in value.split(","):
        item = item.strip()
        if item:
            result.append(int(item))
    if not result:
        raise argparse.ArgumentTypeError("列表不能为空")
    return result


def normalize_uint16(image: np.ndarray) -> np.ndarray:
    array = image.astype(np.float32)
    maximum = float(array.max())
    if maximum <= 0:
        return np.zeros_like(array, dtype=np.float32)
    if maximum > 1.5:
        array /= 65535.0
    return np.clip(array, 0.0, 1.0)


def robust_normalize(
    image: np.ndarray,
    low_p: float = 0.2,
    high_p: float = 99.8,
) -> np.ndarray:
    array = image.astype(np.float32, copy=False)
    low, high = np.percentile(array, [low_p, high_p])
    if high <= low:
        return np.zeros_like(array, dtype=np.float32)
    return np.clip((array - low) / (high - low), 0.0, 1.0)


def to_uint8_rgb(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        gray = np.round(
            robust_normalize(image) * 255.0
        ).astype(np.uint8)
        return np.repeat(gray[..., None], 3, axis=2)

    if image.ndim == 3 and image.shape[2] >= 3:
        rgb = np.zeros(image.shape[:2] + (3,), dtype=np.uint8)
        for channel in range(3):
            rgb[..., channel] = np.round(
                robust_normalize(image[..., channel]) * 255.0
            ).astype(np.uint8)
        return rgb

    raise ValueError(f"无法转换图像维度：{image.shape}")


def save_binary(path: Path, mask: np.ndarray) -> None:
    Image.fromarray(mask.astype(np.uint8) * 255).save(path)


def effective_degree(mask: np.ndarray) -> np.ndarray:
    """
    计算拓扑邻接度。

    正交邻居正常计数；若一个对角邻居已经可通过正交像素连接，
    则不再把该对角关系重复计数。这样可消除骨架楼梯边缘产生的
    大量 degree=3 伪交叉点，同时保留真正的斜线连通性。
    """
    padded = np.pad(mask.astype(bool), 1, mode="constant")
    center = padded[1:-1, 1:-1]

    up = padded[:-2, 1:-1]
    down = padded[2:, 1:-1]
    left = padded[1:-1, :-2]
    right = padded[1:-1, 2:]

    degree = (
        up.astype(np.uint8)
        + down.astype(np.uint8)
        + left.astype(np.uint8)
        + right.astype(np.uint8)
    )

    up_left = (
        padded[:-2, :-2]
        & ~up
        & ~left
    )
    up_right = (
        padded[:-2, 2:]
        & ~up
        & ~right
    )
    down_left = (
        padded[2:, :-2]
        & ~down
        & ~left
    )
    down_right = (
        padded[2:, 2:]
        & ~down
        & ~right
    )

    degree += up_left.astype(np.uint8)
    degree += up_right.astype(np.uint8)
    degree += down_left.astype(np.uint8)
    degree += down_right.astype(np.uint8)
    degree *= center.astype(np.uint8)
    return degree


def effective_neighbours(
    mask: np.ndarray,
    y: int,
    x: int,
) -> List[Tuple[int, int]]:
    height, width = mask.shape
    result: List[Tuple[int, int]] = []

    orthogonal = (
        (-1, 0),
        (1, 0),
        (0, -1),
        (0, 1),
    )
    for dy, dx in orthogonal:
        yy, xx = y + dy, x + dx
        if (
            0 <= yy < height
            and 0 <= xx < width
            and mask[yy, xx]
        ):
            result.append((yy, xx))

    diagonals = (
        (-1, -1),
        (-1, 1),
        (1, -1),
        (1, 1),
    )
    for dy, dx in diagonals:
        yy, xx = y + dy, x + dx
        if not (
            0 <= yy < height
            and 0 <= xx < width
            and mask[yy, xx]
        ):
            continue

        bridge_a = (y + dy, x)
        bridge_b = (y, x + dx)
        bridge_exists = False

        for by, bx in (bridge_a, bridge_b):
            if (
                0 <= by < height
                and 0 <= bx < width
                and mask[by, bx]
            ):
                bridge_exists = True
                break

        if not bridge_exists:
            result.append((yy, xx))

    return result


def head_centres_from_labels(
    head_labels: np.ndarray,
) -> np.ndarray:
    centres = []
    for region in regionprops(head_labels.astype(np.int32)):
        y, x = region.centroid
        centres.append((float(x), float(y)))
    return np.asarray(centres, dtype=np.float32)


def likely_head_start(
    path_yx: List[Tuple[int, int]],
    head_centres_xy: np.ndarray,
    probability: np.ndarray,
    minimum_distance: float,
    maximum_distance: float,
    maximum_angle_degree: float,
) -> bool:
    if (
        len(path_yx) < 3
        or len(head_centres_xy) == 0
    ):
        return False

    endpoint_y, endpoint_x = path_yx[0]
    endpoint_xy = np.asarray(
        [float(endpoint_x), float(endpoint_y)],
        dtype=np.float32,
    )

    differences = head_centres_xy - endpoint_xy
    distances = np.linalg.norm(differences, axis=1)
    nearest_index = int(np.argmin(distances))
    nearest_distance = float(distances[nearest_index])

    if not (
        minimum_distance
        <= nearest_distance
        <= maximum_distance
    ):
        return False

    head_xy = head_centres_xy[nearest_index]
    radial = endpoint_xy - head_xy
    radial_norm = float(np.linalg.norm(radial))
    if radial_norm < 1e-6:
        return False
    radial /= radial_norm

    sample_index = min(5, len(path_yx) - 1)
    sample_y, sample_x = path_yx[sample_index]
    outgoing = np.asarray(
        [
            float(sample_x - endpoint_x),
            float(sample_y - endpoint_y),
        ],
        dtype=np.float32,
    )
    outgoing_norm = float(np.linalg.norm(outgoing))
    if outgoing_norm < 1e-6:
        return False
    outgoing /= outgoing_norm

    cosine = float(np.clip(np.dot(radial, outgoing), -1.0, 1.0))
    angle = math.degrees(math.acos(cosine))

    local_probability = float(
        probability[endpoint_y, endpoint_x]
    )

    return (
        angle <= maximum_angle_degree
        and local_probability >= 0.020
    )


def trace_terminal_branch(
    skeleton: np.ndarray,
    degree: np.ndarray,
    endpoint: Tuple[int, int],
    length_limit: float,
) -> Tuple[List[Tuple[int, int]], float, int]:
    path = [endpoint]
    previous: Optional[Tuple[int, int]] = None
    current = endpoint
    length = 0.0

    while True:
        neighbours = [
            point
            for point in effective_neighbours(
                skeleton,
                current[0],
                current[1],
            )
            if point != previous
        ]

        if len(neighbours) != 1:
            break

        next_point = neighbours[0]
        length += math.hypot(
            float(next_point[1] - current[1]),
            float(next_point[0] - current[0]),
        )
        path.append(next_point)
        previous, current = current, next_point

        current_degree = int(degree[current])
        if current_degree != 2:
            break

        if length > length_limit:
            break

    return path, length, int(degree[current])


def prune_terminal_spurs(
    skeleton: np.ndarray,
    probability: np.ndarray,
    head_centres_xy: np.ndarray,
    spur_length: float,
    preserve_head_starts: bool,
    head_start_min_distance: float,
    head_start_max_distance: float,
    head_start_max_angle: float,
) -> Tuple[np.ndarray, dict]:
    cleaned = skeleton.astype(bool).copy()

    removed_pixels = 0
    removed_branches = 0
    preserved_head_branches = 0

    for _ in range(30):
        degree = effective_degree(cleaned)
        endpoints = list(
            zip(*np.nonzero(cleaned & (degree == 1)))
        )
        pixels_to_remove: Set[Tuple[int, int]] = set()

        for endpoint in endpoints:
            path, branch_length, end_degree = trace_terminal_branch(
                cleaned,
                degree,
                endpoint,
                spur_length,
            )

            if (
                branch_length > spur_length
                or end_degree < 3
                or len(path) < 2
            ):
                continue

            if (
                preserve_head_starts
                and likely_head_start(
                    path,
                    head_centres_xy,
                    probability,
                    minimum_distance=head_start_min_distance,
                    maximum_distance=head_start_max_distance,
                    maximum_angle_degree=head_start_max_angle,
                )
            ):
                preserved_head_branches += 1
                continue

            for point in path[:-1]:
                pixels_to_remove.add(point)
            removed_branches += 1

        if not pixels_to_remove:
            break

        for point in pixels_to_remove:
            cleaned[point] = False
        removed_pixels += len(pixels_to_remove)

    return cleaned, {
        "removed_spur_pixels": int(removed_pixels),
        "removed_spur_branches": int(removed_branches),
        "preserved_head_start_branches": int(
            preserved_head_branches
        ),
    }


def analyse_topology(
    skeleton: np.ndarray,
    junction_merge_radius: int,
) -> Tuple[dict, np.ndarray, np.ndarray, np.ndarray]:
    degree = effective_degree(skeleton)
    endpoint_mask = skeleton & (degree == 1)
    junction_core = skeleton & (degree >= 3)

    if junction_merge_radius > 0:
        merged_junction_regions = dilation(
            junction_core,
            footprint=disk(junction_merge_radius),
        )
    else:
        merged_junction_regions = junction_core

    endpoint_labels = label(endpoint_mask, connectivity=2)
    junction_labels = label(
        merged_junction_regions,
        connectivity=2,
    )
    component_labels = label(skeleton, connectivity=2)

    component_sizes = np.asarray(
        [region.area for region in regionprops(component_labels)],
        dtype=float,
    )

    stats = {
        "skeleton_pixel_count": int(skeleton.sum()),
        "connected_component_count": int(component_labels.max()),
        "endpoint_count": int(endpoint_labels.max()),
        "junction_cluster_count": int(junction_labels.max()),
        "junction_core_pixel_count": int(junction_core.sum()),
        "median_component_pixels": (
            float(np.median(component_sizes))
            if len(component_sizes)
            else 0.0
        ),
        "p90_component_pixels": (
            float(np.percentile(component_sizes, 90.0))
            if len(component_sizes)
            else 0.0
        ),
        "max_component_pixels": (
            int(component_sizes.max())
            if len(component_sizes)
            else 0
        ),
    }

    return (
        stats,
        endpoint_mask,
        junction_core,
        merged_junction_regions,
    )


def make_overlay(
    merge_rgb: np.ndarray,
    skeleton: np.ndarray,
    endpoint_mask: np.ndarray,
    junction_regions: np.ndarray,
) -> np.ndarray:
    overlay = merge_rgb.copy()

    skeleton_pixels = skeleton.astype(bool)
    original = overlay[skeleton_pixels].astype(np.float32)
    cyan = np.asarray([0, 255, 255], dtype=np.float32)
    overlay[skeleton_pixels] = np.clip(
        0.35 * original + 0.65 * cyan,
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

    junction_labels = label(junction_regions, connectivity=2)
    for region in regionprops(junction_labels):
        y, x = region.centroid
        cv2.circle(
            overlay,
            (int(round(x)), int(round(y))),
            7,
            (255, 255, 0),
            1,
            lineType=cv2.LINE_AA,
        )

    return overlay


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="全图尾部骨架拓扑清理 Stage 1.1"
    )
    parser.add_argument(
        "--stage1-dir",
        default="tail_graph_stage1_output",
        help="Stage 1输出目录",
    )
    parser.add_argument(
        "--strict-skeleton",
        help=(
            "可单独指定strict骨架；"
            "默认 <stage1-dir>/strict_skeleton_uint8.tif"
        ),
    )
    parser.add_argument(
        "--probability",
        help=(
            "可单独指定概率图；"
            "默认 <stage1-dir>/02_probability_uint16.tif"
        ),
    )
    parser.add_argument(
        "--merge",
        default="1_Merge.tif",
    )
    parser.add_argument(
        "--head-labels",
        default="1_R_R_uint16.tiff",
    )
    parser.add_argument(
        "--output-dir",
        default="tail_graph_stage1_1_output",
    )
    parser.add_argument(
        "--spur-lengths",
        type=parse_int_list,
        default=[10, 20, 30],
        help="输出的毛刺剪除长度，例如10,20,30",
    )
    parser.add_argument(
        "--junction-merge-radius",
        type=int,
        default=2,
    )
    parser.add_argument(
        "--disable-head-start-protection",
        action="store_true",
    )
    parser.add_argument(
        "--head-start-min-distance",
        type=float,
        default=20.0,
    )
    parser.add_argument(
        "--head-start-max-distance",
        type=float,
        default=110.0,
    )
    parser.add_argument(
        "--head-start-max-angle",
        type=float,
        default=38.0,
    )
    return parser


def main() -> int:
    started = time.perf_counter()
    args = build_parser().parse_args()

    stage1_dir = Path(args.stage1_dir).expanduser().resolve()

    skeleton_path = resolve_required(
        args.strict_skeleton,
        stage1_dir / "strict_skeleton_uint8.tif",
    )
    probability_path = resolve_required(
        args.probability,
        stage1_dir / "02_probability_uint16.tif",
    )
    merge_path = resolve_required(
        args.merge,
        Path(args.merge),
    )
    head_labels_path = resolve_required(
        args.head_labels,
        Path(args.head_labels),
    )

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print("strict骨架：", skeleton_path)
    print("概率图：", probability_path)
    print("Merge图：", merge_path)
    print("头部标签：", head_labels_path)
    print("输出目录：", output_dir)

    strict_skeleton = read_image(skeleton_path) > 0
    probability = normalize_uint16(read_image(probability_path))
    merge_rgb = to_uint8_rgb(read_image(merge_path))
    head_labels = read_image(head_labels_path).astype(np.int32)

    expected_shape = strict_skeleton.shape
    for name, array in (
        ("probability", probability),
        ("merge", merge_rgb),
        ("head_labels", head_labels),
    ):
        if array.shape[:2] != expected_shape:
            raise ValueError(
                f"{name}尺寸不一致："
                f"{array.shape[:2]} != {expected_shape}"
            )

    head_centres_xy = head_centres_from_labels(head_labels)
    print("头部数量：", len(head_centres_xy))

    original_stats, _, _, _ = analyse_topology(
        strict_skeleton,
        junction_merge_radius=args.junction_merge_radius,
    )
    print(
        "原strict拓扑："
        f"endpoints={original_stats['endpoint_count']}，"
        f"junctions={original_stats['junction_cluster_count']}，"
        f"components={original_stats['connected_component_count']}"
    )

    summary_rows = []

    for spur_length in args.spur_lengths:
        preset_started = time.perf_counter()
        name = f"prune{int(spur_length)}"
        print(f"\n正在清理：{name}")

        cleaned, prune_stats = prune_terminal_spurs(
            strict_skeleton,
            probability,
            head_centres_xy,
            spur_length=float(spur_length),
            preserve_head_starts=(
                not args.disable_head_start_protection
            ),
            head_start_min_distance=(
                args.head_start_min_distance
            ),
            head_start_max_distance=(
                args.head_start_max_distance
            ),
            head_start_max_angle=(
                args.head_start_max_angle
            ),
        )

        (
            topology_stats,
            endpoint_mask,
            _junction_core,
            junction_regions,
        ) = analyse_topology(
            cleaned,
            junction_merge_radius=args.junction_merge_radius,
        )

        save_binary(
            output_dir / f"{name}_cleaned_skeleton_uint8.tif",
            cleaned,
        )

        overlay = make_overlay(
            merge_rgb,
            cleaned,
            endpoint_mask,
            junction_regions,
        )
        Image.fromarray(overlay).save(
            output_dir / f"{name}_topology_overlay.png"
        )

        elapsed = time.perf_counter() - preset_started
        row = {
            "preset": name,
            "spur_length_px": int(spur_length),
            "head_count": int(len(head_centres_xy)),
            **prune_stats,
            **topology_stats,
            "elapsed_seconds": float(elapsed),
        }
        summary_rows.append(row)

        payload = {
            "version": "tail_graph_stage1_1_topology_clean_v1",
            "preset": name,
            "parameters": {
                "spur_length_px": int(spur_length),
                "junction_merge_radius": int(
                    args.junction_merge_radius
                ),
                "head_start_protection": bool(
                    not args.disable_head_start_protection
                ),
                "head_start_min_distance": float(
                    args.head_start_min_distance
                ),
                "head_start_max_distance": float(
                    args.head_start_max_distance
                ),
                "head_start_max_angle": float(
                    args.head_start_max_angle
                ),
            },
            "stats": row,
        }
        (
            output_dir / f"{name}_stats.json"
        ).write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print(
            f"{name}完成："
            f"removed_pixels={prune_stats['removed_spur_pixels']}，"
            f"removed_branches={prune_stats['removed_spur_branches']}，"
            f"protected={prune_stats['preserved_head_start_branches']}，"
            f"endpoints={topology_stats['endpoint_count']}，"
            f"junctions={topology_stats['junction_cluster_count']}，"
            f"components={topology_stats['connected_component_count']}，"
            f"elapsed={elapsed:.2f}s"
        )

    summary_path = output_dir / "tail_graph_stage1_1_summary.csv"
    with summary_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(summary_rows[0].keys()),
        )
        writer.writeheader()
        writer.writerows(summary_rows)

    total_elapsed = time.perf_counter() - started
    print("\nStage 1.1完成。")
    print(f"总耗时：{total_elapsed:.2f}s")
    print(
        "请比较：\n"
        "prune10_topology_overlay.png\n"
        "prune20_topology_overlay.png\n"
        "prune30_topology_overlay.png\n"
        "并上传 tail_graph_stage1_1_summary.csv。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
