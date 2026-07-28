#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stage 2.1：P-S1-S2 头部入口与全图骨架边匹配

目标：
1. 不点击，自动为每个红色头部寻找附近的骨架入口；
2. 使用 P-S1-S2 几何关系评估候选；
3. 通过全局一对一分配，避免多个头部占用同一条入口边；
4. 只把高置信度入口标为 auto_confirmed，其余进入 review_required。

本阶段只确认“头部连接到哪条骨架入口边”，还不在交叉图中追踪整条尾部。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import cv2
    import numpy as np
    from PIL import Image
    from scipy.optimize import linear_sum_assignment
    from scipy.spatial import cKDTree
    from skimage.measure import regionprops
except ImportError as exc:
    print("缺少依赖：", exc)
    raise SystemExit(1) from exc


@dataclass
class EdgeData:
    edge_id: int
    points_xy: np.ndarray
    cumulative_px: np.ndarray
    length_px: float
    mean_probability: float
    min_probability: float
    quality: str


def read_image(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image)


def resolve_required(
    supplied: str | None,
    default_path: Path,
) -> Path:
    candidate = (
        Path(supplied).expanduser()
        if supplied
        else default_path
    ).resolve()
    if not candidate.exists():
        raise FileNotFoundError(f"找不到文件：{candidate}")
    return candidate


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


def normalize_probability(image: np.ndarray) -> np.ndarray:
    array = image.astype(np.float32, copy=False)
    if float(array.max()) > 1.5:
        array /= 65535.0
    return np.clip(array, 0.0, 1.0)


def to_uint8_rgb(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        gray = np.round(robust_normalize(image) * 255.0).astype(np.uint8)
        return np.repeat(gray[..., None], 3, axis=2)

    if image.ndim == 3 and image.shape[2] >= 3:
        rgb = np.zeros(image.shape[:2] + (3,), dtype=np.uint8)
        for channel in range(3):
            rgb[..., channel] = np.round(
                robust_normalize(image[..., channel]) * 255.0
            ).astype(np.uint8)
        return rgb

    raise ValueError(f"无法转换图像维度：{image.shape}")


def parse_head_ids(value: str | None) -> set[int] | None:
    if not value:
        return None
    result = {
        int(item.strip())
        for item in value.split(",")
        if item.strip()
    }
    return result or None


def angle_degrees(first: np.ndarray, second: np.ndarray) -> float:
    first_norm = float(np.linalg.norm(first))
    second_norm = float(np.linalg.norm(second))
    if first_norm < 1e-6 or second_norm < 1e-6:
        return 180.0
    cosine = float(
        np.clip(
            np.dot(first, second) / (first_norm * second_norm),
            -1.0,
            1.0,
        )
    )
    return math.degrees(math.acos(cosine))


def cumulative_lengths(points_xy: np.ndarray) -> np.ndarray:
    if len(points_xy) == 0:
        return np.zeros(0, dtype=np.float32)
    if len(points_xy) == 1:
        return np.zeros(1, dtype=np.float32)

    steps = np.linalg.norm(
        np.diff(points_xy.astype(np.float32), axis=0),
        axis=1,
    )
    return np.concatenate(
        [
            np.zeros(1, dtype=np.float32),
            np.cumsum(steps).astype(np.float32),
        ]
    )


def load_edges(graph_path: Path) -> list[EdgeData]:
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    edges: list[EdgeData] = []

    for item in payload.get("edges", []):
        points = np.asarray(item.get("points_xy", []), dtype=np.float32)
        if len(points) < 2:
            continue

        cumulative = cumulative_lengths(points)
        edges.append(
            EdgeData(
                edge_id=int(item["edge_id"]),
                points_xy=points,
                cumulative_px=cumulative,
                length_px=float(item.get("length_px", cumulative[-1])),
                mean_probability=float(item.get("mean_probability", 0.0)),
                min_probability=float(item.get("min_probability", 0.0)),
                quality=str(item.get("quality", "unknown")),
            )
        )

    if not edges:
        raise ValueError("图JSON中没有可用边。")

    return edges


def build_point_index(
    edges: list[EdgeData],
    sampling_step_px: float,
) -> tuple[cKDTree, np.ndarray, np.ndarray, np.ndarray]:
    all_points: list[np.ndarray] = []
    all_edge_indices: list[int] = []
    all_point_indices: list[int] = []

    for edge_index, edge in enumerate(edges):
        last_distance = -1e9
        for point_index, distance in enumerate(edge.cumulative_px):
            if (
                point_index not in (0, len(edge.points_xy) - 1)
                and float(distance - last_distance) < sampling_step_px
            ):
                continue

            all_points.append(edge.points_xy[point_index])
            all_edge_indices.append(edge_index)
            all_point_indices.append(point_index)
            last_distance = float(distance)

    points_array = np.asarray(all_points, dtype=np.float32)
    edge_indices = np.asarray(all_edge_indices, dtype=np.int32)
    point_indices = np.asarray(all_point_indices, dtype=np.int32)

    return (
        cKDTree(points_array),
        points_array,
        edge_indices,
        point_indices,
    )


def point_at_forward_distance(
    edge: EdgeData,
    start_index: int,
    direction: int,
    target_distance: float,
) -> tuple[int, float]:
    start_distance = float(edge.cumulative_px[start_index])
    desired = start_distance + direction * float(target_distance)

    if direction > 0:
        index = int(np.searchsorted(edge.cumulative_px, desired, side="left"))
        index = min(index, len(edge.points_xy) - 1)
    else:
        index = int(np.searchsorted(edge.cumulative_px, desired, side="right") - 1)
        index = max(index, 0)

    actual = abs(float(edge.cumulative_px[index]) - start_distance)
    return index, actual


def available_distance(
    edge: EdgeData,
    point_index: int,
    direction: int,
) -> float:
    current = float(edge.cumulative_px[point_index])
    if direction > 0:
        return max(0.0, float(edge.cumulative_px[-1]) - current)
    return max(0.0, current - float(edge.cumulative_px[0]))


def sample_segment_probability(
    probability: np.ndarray,
    points_xy: np.ndarray,
) -> float:
    if len(points_xy) == 0:
        return 0.0

    x = np.clip(
        np.round(points_xy[:, 0]).astype(int),
        0,
        probability.shape[1] - 1,
    )
    y = np.clip(
        np.round(points_xy[:, 1]).astype(int),
        0,
        probability.shape[0] - 1,
    )
    return float(np.mean(probability[y, x]))


def gap_score(gap_ratio: float) -> float:
    return float(
        math.exp(
            -((gap_ratio - 1.35) / 0.65) ** 2
        )
    )


def alignment_score(angle_degree: float) -> float:
    return float(
        math.exp(
            -(angle_degree / 15.0) ** 2
        )
    )


def candidate_score(
    *,
    angle_degree: float,
    gap_ratio: float,
    local_probability: float,
    edge_probability: float,
    available_px: float,
    edge_quality: str,
) -> float:
    align = alignment_score(angle_degree)
    gap = gap_score(gap_ratio)
    local = float(np.clip((local_probability - 0.04) / 0.55, 0.0, 1.0))
    edge = float(np.clip((edge_probability - 0.05) / 0.65, 0.0, 1.0))
    continuation = float(np.clip(available_px / 45.0, 0.0, 1.0))

    score = (
        0.43 * align
        + 0.18 * gap
        + 0.20 * local
        + 0.11 * edge
        + 0.08 * continuation
    )

    # 非simple边通常来自闭环、孤立段或需要虚拟端点的异常拓扑。
    # 它可以保留为待复核候选，但不应仅凭局部角度优势压过
    # 一条信号更完整、拓扑正常的simple入口边。
    if edge_quality != "simple":
        score -= 0.10

    return float(max(0.0, score))


def find_head_candidates(
    *,
    head: dict[str, Any],
    edges: list[EdgeData],
    point_tree: cKDTree,
    indexed_points: np.ndarray,
    indexed_edge_indices: np.ndarray,
    indexed_point_indices: np.ndarray,
    probability: np.ndarray,
    max_candidates: int,
) -> list[dict[str, Any]]:
    center = np.asarray(
        [head["center_x"], head["center_y"]],
        dtype=np.float32,
    )
    head_length = float(head["major_axis_length"])

    minimum_gap = max(25.0, 0.80 * head_length)
    maximum_gap = min(105.0, max(58.0, 2.80 * head_length))
    s2_target = float(np.clip(0.90 * head_length, 22.0, 42.0))

    nearby_indices = point_tree.query_ball_point(center, r=maximum_gap)
    candidates_by_key: dict[tuple[int, int], dict[str, Any]] = {}

    for indexed_index in nearby_indices:
        s1 = indexed_points[indexed_index]
        radial = s1 - center
        gap_px = float(np.linalg.norm(radial))
        if gap_px < minimum_gap:
            continue

        edge_index = int(indexed_edge_indices[indexed_index])
        point_index = int(indexed_point_indices[indexed_index])
        edge = edges[edge_index]

        for direction in (-1, 1):
            continuation_px = available_distance(edge, point_index, direction)
            if continuation_px < 12.0:
                continue

            target = min(s2_target, continuation_px)
            s2_index, actual_s2_distance = point_at_forward_distance(
                edge,
                point_index,
                direction,
                target,
            )
            if actual_s2_distance < 11.0 or s2_index == point_index:
                continue

            s2 = edge.points_xy[s2_index]
            tangent = s2 - s1
            angle = angle_degrees(radial, tangent)
            if angle > 38.0:
                continue

            start = min(point_index, s2_index)
            stop = max(point_index, s2_index) + 1
            local_points = edge.points_xy[start:stop]
            local_probability = sample_segment_probability(
                probability,
                local_points,
            )

            ratio = gap_px / max(head_length, 1.0)
            score = candidate_score(
                angle_degree=angle,
                gap_ratio=ratio,
                local_probability=local_probability,
                edge_probability=edge.mean_probability,
                available_px=continuation_px,
                edge_quality=edge.quality,
            )

            candidate = {
                "head_id": int(head["head_id"]),
                "edge_id": int(edge.edge_id),
                "direction": int(direction),
                "score": float(score),
                "gap_px": float(gap_px),
                "gap_head_ratio": float(ratio),
                "alignment_angle_deg": float(angle),
                "local_probability": float(local_probability),
                "edge_mean_probability": float(edge.mean_probability),
                "edge_min_probability": float(edge.min_probability),
                "edge_length_px": float(edge.length_px),
                "available_forward_px": float(continuation_px),
                "s1_x": float(s1[0]),
                "s1_y": float(s1[1]),
                "s2_x": float(s2[0]),
                "s2_y": float(s2[1]),
                "edge_quality": edge.quality,
            }

            key = (int(edge.edge_id), int(direction))
            previous = candidates_by_key.get(key)
            if previous is None or candidate["score"] > previous["score"]:
                candidates_by_key[key] = candidate

    ordered = sorted(
        candidates_by_key.values(),
        key=lambda item: item["score"],
        reverse=True,
    )

    # 同一条边无论方向如何，只保留更优方向。
    unique_by_edge: list[dict[str, Any]] = []
    used_edges: set[int] = set()
    for candidate in ordered:
        edge_id = int(candidate["edge_id"])
        if edge_id in used_edges:
            continue
        used_edges.add(edge_id)
        unique_by_edge.append(candidate)
        if len(unique_by_edge) >= max_candidates:
            break

    for rank, candidate in enumerate(unique_by_edge, start=1):
        candidate["local_rank"] = int(rank)

    return unique_by_edge


def build_heads(
    head_labels: np.ndarray,
    selected_ids: set[int] | None,
) -> list[dict[str, Any]]:
    heads: list[dict[str, Any]] = []

    for region in regionprops(head_labels.astype(np.int32)):
        head_id = int(region.label)
        if selected_ids is not None and head_id not in selected_ids:
            continue

        y, x = region.centroid
        heads.append(
            {
                "head_id": head_id,
                "center_x": float(x),
                "center_y": float(y),
                "area": float(region.area),
                "major_axis_length": float(max(region.major_axis_length, 18.0)),
                "minor_axis_length": float(max(region.minor_axis_length, 8.0)),
            }
        )

    heads.sort(key=lambda item: item["head_id"])
    return heads


def global_assign(
    heads: list[dict[str, Any]],
    candidates_by_head: dict[int, list[dict[str, Any]]],
    unmatched_score: float,
) -> dict[int, dict[str, Any] | None]:
    resource_edge_ids = sorted(
        {
            int(candidate["edge_id"])
            for candidates in candidates_by_head.values()
            for candidate in candidates
        }
    )
    edge_column = {
        edge_id: index
        for index, edge_id in enumerate(resource_edge_ids)
    }

    head_count = len(heads)
    edge_count = len(resource_edge_ids)
    score_matrix = np.full(
        (head_count, edge_count + head_count),
        -1.0,
        dtype=np.float64,
    )

    candidate_lookup: dict[tuple[int, int], dict[str, Any]] = {}

    for row, head in enumerate(heads):
        head_id = int(head["head_id"])
        for candidate in candidates_by_head.get(head_id, []):
            edge_id = int(candidate["edge_id"])
            column = edge_column[edge_id]
            score_matrix[row, column] = max(
                score_matrix[row, column],
                float(candidate["score"]),
            )
            candidate_lookup[(head_id, edge_id)] = candidate

        score_matrix[row, edge_count + row] = float(unmatched_score)

    row_indices, column_indices = linear_sum_assignment(
        -score_matrix
    )

    assignments: dict[int, dict[str, Any] | None] = {
        int(head["head_id"]): None
        for head in heads
    }

    for row, column in zip(row_indices, column_indices):
        head_id = int(heads[int(row)]["head_id"])
        if column >= edge_count:
            assignments[head_id] = None
            continue

        edge_id = resource_edge_ids[int(column)]
        assignments[head_id] = candidate_lookup.get((head_id, edge_id))

    return assignments


def dotted_line(
    image: np.ndarray,
    start_xy: tuple[float, float],
    end_xy: tuple[float, float],
    color: tuple[int, int, int],
    thickness: int = 1,
    dash_length: float = 7.0,
) -> None:
    start = np.asarray(start_xy, dtype=np.float32)
    end = np.asarray(end_xy, dtype=np.float32)
    vector = end - start
    length = float(np.linalg.norm(vector))
    if length < 1.0:
        return

    direction = vector / length
    position = 0.0
    draw = True
    while position < length:
        next_position = min(length, position + dash_length)
        if draw:
            first = start + direction * position
            second = start + direction * next_position
            cv2.line(
                image,
                tuple(np.round(first).astype(int)),
                tuple(np.round(second).astype(int)),
                color,
                thickness,
                lineType=cv2.LINE_AA,
            )
        draw = not draw
        position = next_position


def make_overlays(
    merge_rgb: np.ndarray,
    results: list[dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    combined = merge_rgb.copy()
    auto = merge_rgb.copy()
    review = merge_rgb.copy()

    colors = {
        "auto_confirmed": (0, 255, 0),
        "review_required": (255, 255, 0),
        "unmatched": (255, 0, 0),
    }

    for result in results:
        status = str(result["status"])
        color = colors[status]
        center = (
            int(round(result["center_x"])),
            int(round(result["center_y"])),
        )

        targets = [combined]
        if status == "auto_confirmed":
            targets.append(auto)
        else:
            targets.append(review)

        if result.get("edge_id") is None:
            for image in targets:
                cv2.drawMarker(
                    image,
                    center,
                    color,
                    markerType=cv2.MARKER_TILTED_CROSS,
                    markerSize=15,
                    thickness=2,
                    line_type=cv2.LINE_AA,
                )
                cv2.putText(
                    image,
                    f"H{result['head_id']}",
                    (center[0] + 5, center[1] - 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.42,
                    color,
                    1,
                    lineType=cv2.LINE_AA,
                )
            continue

        s1 = (float(result["s1_x"]), float(result["s1_y"]))
        s2 = (float(result["s2_x"]), float(result["s2_y"]))

        for image in targets:
            dotted_line(image, center, s1, color, thickness=1)
            cv2.line(
                image,
                tuple(np.round(s1).astype(int)),
                tuple(np.round(s2).astype(int)),
                color,
                2,
                lineType=cv2.LINE_AA,
            )
            cv2.circle(
                image,
                tuple(np.round(s1).astype(int)),
                4,
                color,
                1,
                lineType=cv2.LINE_AA,
            )
            cv2.drawMarker(
                image,
                tuple(np.round(s2).astype(int)),
                color,
                markerType=cv2.MARKER_TILTED_CROSS,
                markerSize=8,
                thickness=1,
                line_type=cv2.LINE_AA,
            )
            cv2.putText(
                image,
                f"H{result['head_id']}/E{result['edge_id']}",
                (int(round(s1[0])) + 5, int(round(s1[1])) - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.38,
                color,
                1,
                lineType=cv2.LINE_AA,
            )

    return combined, auto, review


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fieldnames: list[str],
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage 2.1：P-S1-S2头部入口与骨架边匹配"
    )
    parser.add_argument(
        "--graph",
        default=(
            "tail_graph_stage1_2_output/"
            "tail_graph_stage1_2.json"
        ),
    )
    parser.add_argument(
        "--probability",
        default=(
            "tail_graph_stage1_output/"
            "02_probability_uint16.tif"
        ),
    )
    parser.add_argument(
        "--head-labels",
        default="1_R_R_uint16.tiff",
    )
    parser.add_argument(
        "--merge",
        default="1_Merge.tif",
    )
    parser.add_argument(
        "--head-ids",
        help="例如13,88,21,31,22；不填则处理全图",
    )
    parser.add_argument(
        "--output-dir",
        default="tail_graph_stage2_1_output",
    )
    parser.add_argument(
        "--point-sampling-step",
        type=float,
        default=3.0,
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=6,
    )
    parser.add_argument(
        "--unmatched-score",
        type=float,
        default=0.30,
    )
    parser.add_argument(
        "--auto-score-threshold",
        type=float,
        default=0.70,
    )
    parser.add_argument(
        "--auto-margin-threshold",
        type=float,
        default=0.07,
    )
    parser.add_argument(
        "--auto-edge-competition-margin",
        type=float,
        default=0.05,
    )
    parser.add_argument(
        "--auto-angle-threshold",
        type=float,
        default=18.0,
    )
    return parser


def main() -> int:
    started = time.perf_counter()
    args = build_parser().parse_args()

    graph_path = resolve_required(args.graph, Path(args.graph))
    probability_path = resolve_required(
        args.probability,
        Path(args.probability),
    )
    head_labels_path = resolve_required(
        args.head_labels,
        Path(args.head_labels),
    )
    merge_path = resolve_required(args.merge, Path(args.merge))

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print("图结构：", graph_path)
    print("概率图：", probability_path)
    print("头部标签：", head_labels_path)
    print("Merge图：", merge_path)
    print("输出目录：", output_dir)

    edges = load_edges(graph_path)
    probability = normalize_probability(read_image(probability_path))
    head_labels = read_image(head_labels_path).astype(np.int32)
    merge_rgb = to_uint8_rgb(read_image(merge_path))

    expected_shape = head_labels.shape
    if probability.shape != expected_shape or merge_rgb.shape[:2] != expected_shape:
        raise ValueError("输入图像尺寸不一致。")

    selected_ids = parse_head_ids(args.head_ids)
    heads = build_heads(head_labels, selected_ids)

    if not heads:
        raise ValueError("没有找到需要处理的头部。")

    (
        point_tree,
        indexed_points,
        indexed_edge_indices,
        indexed_point_indices,
    ) = build_point_index(
        edges,
        sampling_step_px=float(args.point_sampling_step),
    )

    print(
        f"头部数量={len(heads)}，"
        f"图边数量={len(edges)}，"
        f"索引点数量={len(indexed_points)}"
    )

    candidates_by_head: dict[int, list[dict[str, Any]]] = {}
    all_candidates: list[dict[str, Any]] = []

    for index, head in enumerate(heads, start=1):
        candidates = find_head_candidates(
            head=head,
            edges=edges,
            point_tree=point_tree,
            indexed_points=indexed_points,
            indexed_edge_indices=indexed_edge_indices,
            indexed_point_indices=indexed_point_indices,
            probability=probability,
            max_candidates=int(args.max_candidates),
        )
        candidates_by_head[int(head["head_id"])] = candidates
        all_candidates.extend(candidates)

        if index % 20 == 0 or index == len(heads):
            print(f"候选生成：{index}/{len(heads)}")

    assignments = global_assign(
        heads,
        candidates_by_head,
        unmatched_score=float(args.unmatched_score),
    )

    edge_competitors: dict[int, list[float]] = {}
    for candidates in candidates_by_head.values():
        for candidate in candidates:
            edge_competitors.setdefault(
                int(candidate["edge_id"]),
                [],
            ).append(float(candidate["score"]))

    results: list[dict[str, Any]] = []

    for head in heads:
        head_id = int(head["head_id"])
        candidates = candidates_by_head.get(head_id, [])
        assigned = assignments.get(head_id)

        top_score = float(candidates[0]["score"]) if candidates else 0.0
        second_score = (
            float(candidates[1]["score"])
            if len(candidates) >= 2
            else 0.0
        )
        local_margin = top_score - second_score

        result: dict[str, Any] = {
            **head,
            "candidate_count": int(len(candidates)),
            "top_score": float(top_score),
            "second_score": float(second_score),
            "local_margin": float(local_margin),
        }

        if assigned is None:
            result.update(
                {
                    "status": "unmatched",
                    "review_reasons": "no_global_assignment",
                    "edge_id": None,
                    "assigned_rank": None,
                    "score": None,
                    "edge_competition_margin": None,
                }
            )
            results.append(result)
            continue

        edge_scores = sorted(
            edge_competitors.get(int(assigned["edge_id"]), []),
            reverse=True,
        )
        assigned_score = float(assigned["score"])
        competing_score = 0.0
        consumed_self = False
        for score in edge_scores:
            if not consumed_self and abs(score - assigned_score) < 1e-9:
                consumed_self = True
                continue
            competing_score = float(score)
            break

        edge_competition_margin = assigned_score - competing_score
        assigned_rank = int(assigned["local_rank"])

        reasons: list[str] = []
        if assigned_rank != 1:
            reasons.append("assigned_candidate_not_local_best")
        if assigned_score < float(args.auto_score_threshold):
            reasons.append("score_below_auto_threshold")
        if local_margin < float(args.auto_margin_threshold):
            reasons.append("local_candidate_margin_small")
        if edge_competition_margin < float(args.auto_edge_competition_margin):
            reasons.append("edge_competition_margin_small")
        if float(assigned["alignment_angle_deg"]) > float(args.auto_angle_threshold):
            reasons.append("alignment_angle_large")
        if not (0.80 <= float(assigned["gap_head_ratio"]) <= 2.50):
            reasons.append("gap_head_ratio_outside_safe_range")
        if float(assigned["local_probability"]) < 0.10:
            reasons.append("local_probability_low")
        if str(assigned["edge_quality"]) != "simple":
            reasons.append("edge_quality_not_simple")

        status = "auto_confirmed" if not reasons else "review_required"

        result.update(
            {
                **assigned,
                "status": status,
                "review_reasons": "|".join(reasons),
                "assigned_rank": assigned_rank,
                "edge_competition_margin": float(edge_competition_margin),
                "competing_head_score": float(competing_score),
            }
        )
        results.append(result)

    combined_overlay, auto_overlay, review_overlay = make_overlays(
        merge_rgb,
        results,
    )

    Image.fromarray(combined_overlay).save(
        output_dir / "head_graph_entry_all_overlay.png"
    )
    Image.fromarray(auto_overlay).save(
        output_dir / "head_graph_entry_auto_overlay.png"
    )
    Image.fromarray(review_overlay).save(
        output_dir / "head_graph_entry_review_overlay.png"
    )

    result_fields = [
        "head_id",
        "status",
        "review_reasons",
        "center_x",
        "center_y",
        "area",
        "major_axis_length",
        "minor_axis_length",
        "candidate_count",
        "edge_id",
        "assigned_rank",
        "score",
        "top_score",
        "second_score",
        "local_margin",
        "edge_competition_margin",
        "competing_head_score",
        "gap_px",
        "gap_head_ratio",
        "alignment_angle_deg",
        "local_probability",
        "edge_mean_probability",
        "edge_min_probability",
        "edge_length_px",
        "available_forward_px",
        "s1_x",
        "s1_y",
        "s2_x",
        "s2_y",
        "direction",
        "edge_quality",
    ]
    candidate_fields = [
        "head_id",
        "local_rank",
        "edge_id",
        "direction",
        "score",
        "gap_px",
        "gap_head_ratio",
        "alignment_angle_deg",
        "local_probability",
        "edge_mean_probability",
        "edge_min_probability",
        "edge_length_px",
        "available_forward_px",
        "s1_x",
        "s1_y",
        "s2_x",
        "s2_y",
        "edge_quality",
    ]

    write_csv(
        output_dir / "head_graph_entry_summary.csv",
        results,
        result_fields,
    )
    write_csv(
        output_dir / "head_graph_entry_candidates.csv",
        all_candidates,
        candidate_fields,
    )

    status_counts = {
        status: int(sum(result["status"] == status for result in results))
        for status in (
            "auto_confirmed",
            "review_required",
            "unmatched",
        )
    }

    payload = {
        "version": "tail_graph_stage2_1_head_entry_match_v1_1",
        "parameters": {
            "point_sampling_step": float(args.point_sampling_step),
            "max_candidates": int(args.max_candidates),
            "unmatched_score": float(args.unmatched_score),
            "auto_score_threshold": float(args.auto_score_threshold),
            "auto_margin_threshold": float(args.auto_margin_threshold),
            "auto_edge_competition_margin": float(
                args.auto_edge_competition_margin
            ),
            "auto_angle_threshold": float(args.auto_angle_threshold),
        },
        "status_counts": status_counts,
        "results": results,
        "candidates": all_candidates,
    }
    (
        output_dir / "head_graph_entry_results.json"
    ).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    elapsed = time.perf_counter() - started
    print("\nStage 2.1完成。")
    print(
        f"auto_confirmed={status_counts['auto_confirmed']}，"
        f"review_required={status_counts['review_required']}，"
        f"unmatched={status_counts['unmatched']}"
    )
    print(f"总耗时：{elapsed:.2f}s")
    print(
        "请上传：\n"
        "head_graph_entry_all_overlay.png\n"
        "head_graph_entry_summary.csv\n"
        "head_graph_entry_results.json"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
