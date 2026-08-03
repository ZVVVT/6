#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全图精子尾部骨架图构建：Stage 1.2

输入：
- Stage 1.1 的 prune20_cleaned_skeleton_uint8.tif
- Stage 1 的 02_probability_uint16.tif
- 1_Merge.tif

输出：
- 节点表、边表、完整图 JSON
- 图结构叠加图
- 图质量叠加图
- 汇总统计

本阶段只把清理后的骨架转换成可计算的“节点—边”结构，
还不执行头部—尾部一一分配。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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


def resolve_required(
    supplied: Optional[str],
    default_path: Path,
) -> Path:
    candidate = (
        Path(supplied).expanduser()
        if supplied
        else default_path
    ).resolve()

    if not candidate.exists():
        raise FileNotFoundError(
            f"找不到文件：{candidate}"
        )

    return candidate


def robust_normalize(
    image: np.ndarray,
    low_p: float = 0.2,
    high_p: float = 99.8,
) -> np.ndarray:
    array = image.astype(
        np.float32,
        copy=False,
    )
    low, high = np.percentile(
        array,
        [low_p, high_p],
    )
    if high <= low:
        return np.zeros_like(
            array,
            dtype=np.float32,
        )
    return np.clip(
        (array - low) / (high - low),
        0.0,
        1.0,
    )


def normalize_uint16(
    image: np.ndarray,
) -> np.ndarray:
    array = image.astype(
        np.float32,
        copy=False,
    )
    if float(array.max()) > 1.5:
        array /= 65535.0
    return np.clip(
        array,
        0.0,
        1.0,
    )


def to_uint8_rgb(
    image: np.ndarray,
) -> np.ndarray:
    if image.ndim == 2:
        gray = np.round(
            robust_normalize(image)
            * 255.0
        ).astype(np.uint8)
        return np.repeat(
            gray[..., None],
            3,
            axis=2,
        )

    if (
        image.ndim == 3
        and image.shape[2] >= 3
    ):
        rgb = np.zeros(
            image.shape[:2] + (3,),
            dtype=np.uint8,
        )
        for channel in range(3):
            rgb[..., channel] = np.round(
                robust_normalize(
                    image[..., channel]
                )
                * 255.0
            ).astype(np.uint8)
        return rgb

    raise ValueError(
        f"无法转换图像维度：{image.shape}"
    )


def effective_degree(
    mask: np.ndarray,
) -> np.ndarray:
    """
    与 Stage 1.1 一致的拓扑邻接度：
    对角像素若已可通过正交邻居连接，则不重复计数。
    """
    padded = np.pad(
        mask.astype(bool),
        1,
        mode="constant",
    )
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

    degree += (
        padded[:-2, :-2]
        & ~up
        & ~left
    ).astype(np.uint8)
    degree += (
        padded[:-2, 2:]
        & ~up
        & ~right
    ).astype(np.uint8)
    degree += (
        padded[2:, :-2]
        & ~down
        & ~left
    ).astype(np.uint8)
    degree += (
        padded[2:, 2:]
        & ~down
        & ~right
    ).astype(np.uint8)

    degree *= center.astype(
        np.uint8
    )
    return degree


def effective_neighbours(
    mask: np.ndarray,
    y: int,
    x: int,
) -> List[Tuple[int, int]]:
    height, width = mask.shape
    result: List[Tuple[int, int]] = []

    for dy, dx in (
        (-1, 0),
        (1, 0),
        (0, -1),
        (0, 1),
    ):
        yy, xx = y + dy, x + dx
        if (
            0 <= yy < height
            and 0 <= xx < width
            and mask[yy, xx]
        ):
            result.append((yy, xx))

    for dy, dx in (
        (-1, -1),
        (-1, 1),
        (1, -1),
        (1, 1),
    ):
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

        for by, bx in (
            bridge_a,
            bridge_b,
        ):
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


def build_node_regions(
    skeleton: np.ndarray,
    junction_merge_radius: int,
) -> Tuple[
    np.ndarray,
    List[dict],
    np.ndarray,
    np.ndarray,
]:
    degree = effective_degree(
        skeleton
    )
    endpoint_mask = (
        skeleton
        & (degree == 1)
    )
    junction_core = (
        skeleton
        & (degree >= 3)
    )

    junction_region = (
        skeleton
        & dilation(
            junction_core,
            footprint=disk(
                max(
                    0,
                    int(
                        junction_merge_radius
                    ),
                )
            ),
        )
        if junction_merge_radius > 0
        else junction_core
    )

    endpoint_only = (
        endpoint_mask
        & ~junction_region
    )
    raw_node_mask = (
        junction_region
        | endpoint_only
    )
    raw_labels = label(
        raw_node_mask,
        connectivity=2,
    )

    node_labels = np.zeros(
        skeleton.shape,
        dtype=np.int32,
    )
    nodes: List[dict] = []

    for region in regionprops(
        raw_labels
    ):
        coords = region.coords
        intersects_junction = bool(
            np.any(
                junction_core[
                    coords[:, 0],
                    coords[:, 1],
                ]
            )
        )
        kind = (
            "junction"
            if intersects_junction
            else "endpoint"
        )

        node_id = len(nodes) + 1
        node_labels[
            coords[:, 0],
            coords[:, 1],
        ] = node_id

        nodes.append(
            {
                "node_id": int(
                    node_id
                ),
                "kind": kind,
                "x": float(
                    coords[:, 1].mean()
                ),
                "y": float(
                    coords[:, 0].mean()
                ),
                "pixel_count": int(
                    len(coords)
                ),
                "max_effective_degree": int(
                    degree[
                        coords[:, 0],
                        coords[:, 1],
                    ].max()
                ),
                "virtual": False,
            }
        )

    return (
        node_labels,
        nodes,
        endpoint_mask,
        junction_region,
    )


def bfs_distances(
    component_mask: np.ndarray,
    start: Tuple[int, int],
) -> Tuple[
    Dict[Tuple[int, int], float],
    Dict[
        Tuple[int, int],
        Optional[Tuple[int, int]],
    ],
]:
    distances = {
        start: 0.0
    }
    parents: Dict[
        Tuple[int, int],
        Optional[Tuple[int, int]],
    ] = {
        start: None
    }
    queue: deque[
        Tuple[int, int]
    ] = deque([start])

    while queue:
        current = queue.popleft()

        for neighbour in effective_neighbours(
            component_mask,
            current[0],
            current[1],
        ):
            step = math.hypot(
                float(
                    neighbour[1]
                    - current[1]
                ),
                float(
                    neighbour[0]
                    - current[0]
                ),
            )
            candidate_distance = (
                distances[current]
                + step
            )

            if (
                neighbour
                not in distances
                or candidate_distance
                < distances[neighbour]
            ):
                distances[
                    neighbour
                ] = candidate_distance
                parents[
                    neighbour
                ] = current
                queue.append(neighbour)

    return distances, parents


def reconstruct_path(
    parents: Dict[
        Tuple[int, int],
        Optional[Tuple[int, int]],
    ],
    end: Tuple[int, int],
) -> List[Tuple[int, int]]:
    path = [end]
    current = end

    while parents.get(current) is not None:
        current = parents[current]
        path.append(current)

    path.reverse()
    return path


def farthest_point(
    component_mask: np.ndarray,
    start: Tuple[int, int],
) -> Tuple[
    Tuple[int, int],
    Dict[
        Tuple[int, int],
        Optional[Tuple[int, int]],
    ],
]:
    distances, parents = bfs_distances(
        component_mask,
        start,
    )
    farthest = max(
        distances,
        key=distances.get,
    )
    return farthest, parents


def component_diameter_path(
    component_mask: np.ndarray,
) -> List[Tuple[int, int]]:
    ys, xs = np.nonzero(
        component_mask
    )
    if len(xs) == 0:
        return []

    start = (
        int(ys[0]),
        int(xs[0]),
    )
    first, _ = farthest_point(
        component_mask,
        start,
    )
    second, parents = farthest_point(
        component_mask,
        first,
    )
    return reconstruct_path(
        parents,
        second,
    )


def nearest_component_pixel(
    coords_yx: np.ndarray,
    target_xy: np.ndarray,
) -> Tuple[int, int]:
    differences = np.column_stack(
        [
            coords_yx[:, 1],
            coords_yx[:, 0],
        ]
    ).astype(np.float32)
    differences -= target_xy[
        None,
        :
    ]

    index = int(
        np.argmin(
            np.sum(
                differences * differences,
                axis=1,
            )
        )
    )
    return (
        int(coords_yx[index, 0]),
        int(coords_yx[index, 1]),
    )


def path_between_pixels(
    component_mask: np.ndarray,
    start: Tuple[int, int],
    end: Tuple[int, int],
) -> List[Tuple[int, int]]:
    _, parents = bfs_distances(
        component_mask,
        start,
    )
    if end not in parents:
        return []
    return reconstruct_path(
        parents,
        end,
    )


def path_length_xy(
    points_xy: np.ndarray,
) -> float:
    if len(points_xy) < 2:
        return 0.0
    return float(
        np.linalg.norm(
            np.diff(
                points_xy.astype(
                    np.float32
                ),
                axis=0,
            ),
            axis=1,
        ).sum()
    )


def tangent_at_start(
    points_xy: np.ndarray,
    distance_px: float = 12.0,
) -> Tuple[float, float]:
    if len(points_xy) < 2:
        return 0.0, 0.0

    start = points_xy[0].astype(
        np.float32
    )

    selected = points_xy[-1].astype(
        np.float32
    )
    for point in points_xy[1:]:
        if float(
            np.linalg.norm(
                point.astype(np.float32)
                - start
            )
        ) >= distance_px:
            selected = point.astype(
                np.float32
            )
            break

    vector = selected - start
    norm = float(
        np.linalg.norm(vector)
    )
    if norm < 1e-6:
        return 0.0, 0.0

    vector /= norm
    return (
        float(vector[0]),
        float(vector[1]),
    )


def add_virtual_node(
    nodes: List[dict],
    y: int,
    x: int,
    kind: str,
) -> int:
    node_id = len(nodes) + 1
    nodes.append(
        {
            "node_id": int(node_id),
            "kind": kind,
            "x": float(x),
            "y": float(y),
            "pixel_count": 1,
            "max_effective_degree": 0,
            "virtual": True,
        }
    )
    return node_id


def build_edges(
    skeleton: np.ndarray,
    node_labels: np.ndarray,
    nodes: List[dict],
    probability: np.ndarray,
    minimum_edge_pixels: int,
) -> Tuple[
    List[dict],
    dict,
]:
    node_mask = node_labels > 0
    edge_interior = (
        skeleton
        & ~node_mask
    )

    edge_component_labels = label(
        edge_interior,
        connectivity=2,
    )
    edges: List[dict] = []

    simple_count = 0
    virtual_count = 0
    multi_contact_count = 0
    branched_interior_count = 0
    discarded_short_count = 0

    node_centres = {
        int(node["node_id"]): np.asarray(
            [node["x"], node["y"]],
            dtype=np.float32,
        )
        for node in nodes
    }

    image_height, image_width = (
        edge_component_labels.shape
    )

    for region in regionprops(
        edge_component_labels
    ):
        coords = region.coords

        if len(coords) < max(
            1,
            int(minimum_edge_pixels),
        ):
            discarded_short_count += 1
            continue

        # 旧实现会为每个小组件重复创建一张全图大小的布尔掩模，
        # 并在全图上执行膨胀、拓扑度计算和 BFS。这里保留完全相同
        # 的算法，只把运算限制在该组件的局部边界框内。
        min_y, min_x, max_y, max_x = (
            int(value)
            for value in region.bbox
        )
        padding = 1
        y0 = max(0, min_y - padding)
        x0 = max(0, min_x - padding)
        y1 = min(
            image_height,
            max_y + padding,
        )
        x1 = min(
            image_width,
            max_x + padding,
        )

        local_component_labels = (
            edge_component_labels[
                y0:y1,
                x0:x1,
            ]
        )
        component_mask = (
            local_component_labels
            == int(region.label)
        )
        local_node_labels = node_labels[
            y0:y1,
            x0:x1,
        ]

        dilated = cv2.dilate(
            component_mask.astype(
                np.uint8
            ),
            np.ones(
                (3, 3),
                dtype=np.uint8,
            ),
            iterations=1,
        ) > 0

        contact_node_ids = sorted(
            int(value)
            for value in np.unique(
                local_node_labels[
                    dilated
                    & (local_node_labels > 0)
                ]
            )
            if int(value) > 0
        )

        component_degree = effective_degree(
            component_mask
        )
        branch_pixel_count = int(
            np.sum(
                component_degree >= 3
            )
        )
        if branch_pixel_count > 0:
            branched_interior_count += 1

        quality_flags: List[str] = []

        if len(contact_node_ids) == 2:
            start_node_id = (
                contact_node_ids[0]
            )
            end_node_id = (
                contact_node_ids[1]
            )
            start_pixel_global = nearest_component_pixel(
                coords,
                node_centres[
                    start_node_id
                ],
            )
            end_pixel_global = nearest_component_pixel(
                coords,
                node_centres[
                    end_node_id
                ],
            )
            start_pixel = (
                start_pixel_global[0] - y0,
                start_pixel_global[1] - x0,
            )
            end_pixel = (
                end_pixel_global[0] - y0,
                end_pixel_global[1] - x0,
            )
            ordered_yx = path_between_pixels(
                component_mask,
                start_pixel,
                end_pixel,
            )
            simple_count += 1

        elif len(contact_node_ids) == 1:
            start_node_id = (
                contact_node_ids[0]
            )
            start_pixel_global = nearest_component_pixel(
                coords,
                node_centres[
                    start_node_id
                ],
            )
            start_pixel = (
                start_pixel_global[0] - y0,
                start_pixel_global[1] - x0,
            )
            distances, parents = bfs_distances(
                component_mask,
                start_pixel,
            )
            end_pixel = max(
                distances,
                key=distances.get,
            )
            ordered_yx = reconstruct_path(
                parents,
                end_pixel,
            )
            end_pixel_global = (
                end_pixel[0] + y0,
                end_pixel[1] + x0,
            )
            end_node_id = add_virtual_node(
                nodes,
                end_pixel_global[0],
                end_pixel_global[1],
                "virtual_terminal",
            )
            node_centres[
                end_node_id
            ] = np.asarray(
                [
                    float(end_pixel_global[1]),
                    float(end_pixel_global[0]),
                ],
                dtype=np.float32,
            )
            quality_flags.append(
                "one_contact_virtual_terminal"
            )
            virtual_count += 1

        elif len(contact_node_ids) == 0:
            ordered_yx = component_diameter_path(
                component_mask
            )
            if len(ordered_yx) < 2:
                discarded_short_count += 1
                continue

            start_pixel = ordered_yx[0]
            end_pixel = ordered_yx[-1]
            start_pixel_global = (
                start_pixel[0] + y0,
                start_pixel[1] + x0,
            )
            end_pixel_global = (
                end_pixel[0] + y0,
                end_pixel[1] + x0,
            )

            start_node_id = add_virtual_node(
                nodes,
                start_pixel_global[0],
                start_pixel_global[1],
                "virtual_terminal",
            )
            end_node_id = add_virtual_node(
                nodes,
                end_pixel_global[0],
                end_pixel_global[1],
                "virtual_terminal",
            )
            node_centres[
                start_node_id
            ] = np.asarray(
                [
                    float(start_pixel_global[1]),
                    float(start_pixel_global[0]),
                ],
                dtype=np.float32,
            )
            node_centres[
                end_node_id
            ] = np.asarray(
                [
                    float(end_pixel_global[1]),
                    float(end_pixel_global[0]),
                ],
                dtype=np.float32,
            )
            quality_flags.append(
                "zero_contact_virtual_terminals"
            )
            virtual_count += 1

        else:
            multi_contact_count += 1
            quality_flags.append(
                "multi_contact_component"
            )

            contact_pairs = []
            for index_a in range(
                len(contact_node_ids)
            ):
                for index_b in range(
                    index_a + 1,
                    len(contact_node_ids),
                ):
                    node_a = (
                        contact_node_ids[
                            index_a
                        ]
                    )
                    node_b = (
                        contact_node_ids[
                            index_b
                        ]
                    )
                    distance = float(
                        np.linalg.norm(
                            node_centres[node_a]
                            - node_centres[node_b]
                        )
                    )
                    contact_pairs.append(
                        (
                            distance,
                            node_a,
                            node_b,
                        )
                    )

            _, start_node_id, end_node_id = max(
                contact_pairs,
                key=lambda item: item[0],
            )
            start_pixel_global = nearest_component_pixel(
                coords,
                node_centres[
                    start_node_id
                ],
            )
            end_pixel_global = nearest_component_pixel(
                coords,
                node_centres[
                    end_node_id
                ],
            )
            start_pixel = (
                start_pixel_global[0] - y0,
                start_pixel_global[1] - x0,
            )
            end_pixel = (
                end_pixel_global[0] - y0,
                end_pixel_global[1] - x0,
            )
            ordered_yx = path_between_pixels(
                component_mask,
                start_pixel,
                end_pixel,
            )

        if len(ordered_yx) < 2:
            discarded_short_count += 1
            continue

        if branch_pixel_count > 0:
            quality_flags.append(
                "branched_edge_interior"
            )

        points_yx = np.asarray(
            ordered_yx,
            dtype=np.int32,
        )
        points_yx[:, 0] += y0
        points_yx[:, 1] += x0
        points_xy = np.column_stack(
            [
                points_yx[:, 1],
                points_yx[:, 0],
            ]
        ).astype(np.int32)

        probability_values = probability[
            points_yx[:, 0],
            points_yx[:, 1],
        ]

        start_tangent = tangent_at_start(
            points_xy
        )
        reversed_tangent = tangent_at_start(
            points_xy[::-1]
        )

        sample_step = max(
            1,
            len(points_xy) // 300,
        )
        sampled_points = points_xy[
            ::sample_step
        ]
        if not np.array_equal(
            sampled_points[-1],
            points_xy[-1],
        ):
            sampled_points = np.vstack(
                [
                    sampled_points,
                    points_xy[-1],
                ]
            )

        edge_length = path_length_xy(
            points_xy
        )

        edge = {
            "edge_id": int(
                len(edges) + 1
            ),
            "start_node_id": int(
                start_node_id
            ),
            "end_node_id": int(
                end_node_id
            ),
            "component_label": int(
                region.label
            ),
            "contact_node_count": int(
                len(contact_node_ids)
            ),
            "quality": (
                "simple"
                if not quality_flags
                else "|".join(
                    quality_flags
                )
            ),
            "branch_pixel_count": int(
                branch_pixel_count
            ),
            "pixel_count": int(
                len(points_xy)
            ),
            "length_px": float(
                edge_length
            ),
            "mean_probability": float(
                probability_values.mean()
            ),
            "min_probability": float(
                probability_values.min()
            ),
            "start_tangent_x": float(
                start_tangent[0]
            ),
            "start_tangent_y": float(
                start_tangent[1]
            ),
            "end_tangent_x": float(
                reversed_tangent[0]
            ),
            "end_tangent_y": float(
                reversed_tangent[1]
            ),
            "bbox_min_x": int(
                points_xy[:, 0].min()
            ),
            "bbox_min_y": int(
                points_xy[:, 1].min()
            ),
            "bbox_max_x": int(
                points_xy[:, 0].max()
            ),
            "bbox_max_y": int(
                points_xy[:, 1].max()
            ),
            "points_xy": sampled_points.tolist(),
        }
        edges.append(edge)

    edge_lengths = np.asarray(
        [
            edge["length_px"]
            for edge in edges
        ],
        dtype=np.float32,
    )

    stats = {
        "edge_count": int(
            len(edges)
        ),
        "simple_two_contact_edge_count": int(
            simple_count
        ),
        "virtual_terminal_edge_count": int(
            virtual_count
        ),
        "multi_contact_edge_count": int(
            multi_contact_count
        ),
        "branched_interior_edge_count": int(
            branched_interior_count
        ),
        "discarded_short_component_count": int(
            discarded_short_count
        ),
        "total_edge_length_px": float(
            edge_lengths.sum()
            if len(edge_lengths)
            else 0.0
        ),
        "median_edge_length_px": float(
            np.median(edge_lengths)
            if len(edge_lengths)
            else 0.0
        ),
        "p90_edge_length_px": float(
            np.percentile(
                edge_lengths,
                90.0,
            )
            if len(edge_lengths)
            else 0.0
        ),
    }

    return edges, stats

def make_structure_overlay(
    merge_rgb: np.ndarray,
    skeleton: np.ndarray,
    nodes: List[dict],
    edges: List[dict],
    show_edge_ids: bool,
) -> np.ndarray:
    overlay = merge_rgb.copy()

    original = overlay[
        skeleton
    ].astype(np.float32)
    cyan = np.asarray(
        [0, 255, 255],
        dtype=np.float32,
    )
    overlay[skeleton] = np.clip(
        0.35 * original
        + 0.65 * cyan,
        0,
        255,
    ).astype(np.uint8)

    for node in nodes:
        center = (
            int(round(node["x"])),
            int(round(node["y"])),
        )
        kind = node["kind"]

        if kind == "junction":
            color = (255, 255, 0)
            radius = 7
        elif kind == "endpoint":
            color = (255, 0, 0)
            radius = 5
        else:
            color = (255, 0, 255)
            radius = 5

        cv2.circle(
            overlay,
            center,
            radius,
            color,
            1,
            lineType=cv2.LINE_AA,
        )

    if show_edge_ids:
        for edge in edges:
            if edge["length_px"] < 45.0:
                continue

            points = np.asarray(
                edge["points_xy"],
                dtype=np.int32,
            )
            if len(points) == 0:
                continue

            midpoint = points[
                len(points) // 2
            ]
            cv2.putText(
                overlay,
                f"E{edge['edge_id']}",
                (
                    int(midpoint[0]),
                    int(midpoint[1]),
                ),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.34,
                (255, 255, 255),
                1,
                lineType=cv2.LINE_AA,
            )

    return overlay


def make_quality_overlay(
    merge_rgb: np.ndarray,
    edges: List[dict],
) -> np.ndarray:
    overlay = merge_rgb.copy()

    for edge in edges:
        points = np.asarray(
            edge["points_xy"],
            dtype=np.int32,
        )
        if len(points) < 2:
            continue

        if edge["quality"] == "simple":
            color = (0, 255, 0)
        elif (
            "multi_contact"
            in edge["quality"]
            or "branched_edge"
            in edge["quality"]
        ):
            color = (255, 0, 0)
        else:
            color = (255, 255, 0)

        cv2.polylines(
            overlay,
            [points.reshape(-1, 1, 2)],
            False,
            color,
            1,
            lineType=cv2.LINE_AA,
        )

    return overlay


def write_csv(
    path: Path,
    rows: List[dict],
    fieldnames: List[str],
) -> None:
    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "全图精子尾部骨架图构建 Stage 1.2"
        )
    )
    parser.add_argument(
        "--stage1-1-dir",
        default=(
            "tail_graph_stage1_1_output"
        ),
    )
    parser.add_argument(
        "--stage1-dir",
        default=(
            "tail_graph_stage1_output"
        ),
    )
    parser.add_argument(
        "--skeleton",
        help=(
            "默认读取Stage 1.1的"
            "prune20_cleaned_skeleton_uint8.tif"
        ),
    )
    parser.add_argument(
        "--probability",
        help=(
            "默认读取Stage 1的"
            "02_probability_uint16.tif"
        ),
    )
    parser.add_argument(
        "--merge",
        default="1_Merge.tif",
    )
    parser.add_argument(
        "--output-dir",
        default=(
            "tail_graph_stage1_2_output"
        ),
    )
    parser.add_argument(
        "--junction-merge-radius",
        type=int,
        default=2,
    )
    parser.add_argument(
        "--minimum-edge-pixels",
        type=int,
        default=3,
    )
    return parser


def main() -> int:
    started = time.perf_counter()
    args = build_parser().parse_args()

    stage1_1_dir = Path(
        args.stage1_1_dir
    ).expanduser().resolve()
    stage1_dir = Path(
        args.stage1_dir
    ).expanduser().resolve()

    skeleton_path = resolve_required(
        args.skeleton,
        stage1_1_dir
        / (
            "prune20_"
            "cleaned_skeleton_uint8.tif"
        ),
    )
    probability_path = resolve_required(
        args.probability,
        stage1_dir
        / "02_probability_uint16.tif",
    )
    merge_path = resolve_required(
        args.merge,
        Path(args.merge),
    )

    output_dir = Path(
        args.output_dir
    ).expanduser().resolve()
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("骨架：", skeleton_path)
    print("概率图：", probability_path)
    print("Merge图：", merge_path)
    print("输出目录：", output_dir)

    skeleton = (
        read_image(
            skeleton_path
        ) > 0
    )
    probability = normalize_uint16(
        read_image(
            probability_path
        )
    )
    merge_rgb = to_uint8_rgb(
        read_image(
            merge_path
        )
    )

    expected_shape = skeleton.shape
    if (
        probability.shape
        != expected_shape
        or merge_rgb.shape[:2]
        != expected_shape
    ):
        raise ValueError(
            "输入图像尺寸不一致。"
        )

    (
        node_labels,
        nodes,
        endpoint_mask,
        junction_region,
    ) = build_node_regions(
        skeleton,
        junction_merge_radius=(
            args.junction_merge_radius
        ),
    )

    edges, edge_stats = build_edges(
        skeleton,
        node_labels,
        nodes,
        probability,
        minimum_edge_pixels=(
            args.minimum_edge_pixels
        ),
    )

    endpoint_count = sum(
        node["kind"] == "endpoint"
        for node in nodes
    )
    junction_count = sum(
        node["kind"] == "junction"
        for node in nodes
    )
    virtual_count = sum(
        bool(node["virtual"])
        for node in nodes
    )

    summary = {
        "version": (
            "tail_graph_stage1_2_build_graph_v1"
        ),
        "image_width": int(
            expected_shape[1]
        ),
        "image_height": int(
            expected_shape[0]
        ),
        "skeleton_pixel_count": int(
            skeleton.sum()
        ),
        "node_count": int(
            len(nodes)
        ),
        "endpoint_node_count": int(
            endpoint_count
        ),
        "junction_node_count": int(
            junction_count
        ),
        "virtual_node_count": int(
            virtual_count
        ),
        **edge_stats,
    }

    graph_payload = {
        "version": summary["version"],
        "source_skeleton": str(
            skeleton_path
        ),
        "source_probability": str(
            probability_path
        ),
        "parameters": {
            "junction_merge_radius": int(
                args.junction_merge_radius
            ),
            "minimum_edge_pixels": int(
                args.minimum_edge_pixels
            ),
        },
        "summary": summary,
        "nodes": nodes,
        "edges": edges,
    }

    (
        output_dir
        / "tail_graph_stage1_2.json"
    ).write_text(
        json.dumps(
            graph_payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    node_csv_rows = [
        {
            key: value
            for key, value in node.items()
        }
        for node in nodes
    ]
    edge_csv_rows = [
        {
            key: value
            for key, value in edge.items()
            if key != "points_xy"
        }
        for edge in edges
    ]

    write_csv(
        output_dir
        / "tail_graph_nodes.csv",
        node_csv_rows,
        [
            "node_id",
            "kind",
            "x",
            "y",
            "pixel_count",
            "max_effective_degree",
            "virtual",
        ],
    )

    write_csv(
        output_dir
        / "tail_graph_edges.csv",
        edge_csv_rows,
        [
            "edge_id",
            "start_node_id",
            "end_node_id",
            "component_label",
            "contact_node_count",
            "quality",
            "branch_pixel_count",
            "pixel_count",
            "length_px",
            "mean_probability",
            "min_probability",
            "start_tangent_x",
            "start_tangent_y",
            "end_tangent_x",
            "end_tangent_y",
            "bbox_min_x",
            "bbox_min_y",
            "bbox_max_x",
            "bbox_max_y",
        ],
    )

    (
        output_dir
        / "tail_graph_stage1_2_summary.json"
    ).write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    structure_overlay = (
        make_structure_overlay(
            merge_rgb,
            skeleton,
            nodes,
            edges,
            show_edge_ids=True,
        )
    )
    quality_overlay = (
        make_quality_overlay(
            merge_rgb,
            edges,
        )
    )

    Image.fromarray(
        structure_overlay
    ).save(
        output_dir
        / "tail_graph_structure_overlay.png"
    )
    Image.fromarray(
        quality_overlay
    ).save(
        output_dir
        / "tail_graph_quality_overlay.png"
    )

    Image.fromarray(
        (
            endpoint_mask.astype(
                np.uint8
            )
            * 255
        )
    ).save(
        output_dir
        / "endpoint_mask_uint8.tif"
    )
    Image.fromarray(
        (
            junction_region.astype(
                np.uint8
            )
            * 255
        )
    ).save(
        output_dir
        / "junction_region_uint8.tif"
    )

    elapsed = (
        time.perf_counter()
        - started
    )

    print("\nStage 1.2完成。")
    print(
        f"nodes={summary['node_count']}，"
        f"endpoints="
        f"{summary['endpoint_node_count']}，"
        f"junctions="
        f"{summary['junction_node_count']}，"
        f"virtual="
        f"{summary['virtual_node_count']}"
    )
    print(
        f"edges={summary['edge_count']}，"
        f"simple="
        f"{summary['simple_two_contact_edge_count']}，"
        f"virtual_edges="
        f"{summary['virtual_terminal_edge_count']}，"
        f"multi_contact="
        f"{summary['multi_contact_edge_count']}，"
        f"branched_interior="
        f"{summary['branched_interior_edge_count']}"
    )
    print(
        f"总耗时：{elapsed:.2f}s"
    )
    print(
        "请上传：\n"
        "tail_graph_structure_overlay.png\n"
        "tail_graph_quality_overlay.png\n"
        "tail_graph_stage1_2_summary.json"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
