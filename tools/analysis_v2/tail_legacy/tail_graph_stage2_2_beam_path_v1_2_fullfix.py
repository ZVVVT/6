#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stage 2.2：基于图结构的完整精子尾部路径搜索（Beam Search）

输入：
- Stage 1.2 图结构 JSON；
- Stage 2.1 头部入口匹配 JSON；
- 尾部概率图；
- Merge 图。

本阶段从已确定的入口边及方向出发，在交叉节点处同时保留多个候选路径，
根据多尺度方向连续性、曲率、沿途信号、路径长度和端点合理性选择完整尾部。

第一轮建议仅测试已知头部：13,21,22,31,88。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    import cv2
    import numpy as np
    from PIL import Image
except ImportError as exc:
    print("缺少依赖：", exc)
    raise SystemExit(1) from exc


@dataclass(frozen=True)
class NodeData:
    node_id: int
    kind: str
    x: float
    y: float
    virtual: bool


@dataclass
class EdgeData:
    edge_id: int
    start_node_id: int
    end_node_id: int
    points_xy: np.ndarray
    cumulative_px: np.ndarray
    length_px: float
    mean_probability: float
    min_probability: float
    quality: str


@dataclass
class PathState:
    current_node_id: int
    edge_ids: tuple[int, ...]
    oriented_segments: list[np.ndarray]
    length_px: float
    transition_records: list[dict[str, Any]]
    partial_score_sum: float
    visited_node_ids: frozenset[int]
    termination_reason: str = ""


@dataclass
class PathCandidate:
    rank: int
    head_id: int
    edge_ids: tuple[int, ...]
    points_xy: np.ndarray
    final_node_id: int
    final_node_kind: str
    termination_reason: str
    reached_endpoint: bool
    length_px: float
    mean_probability: float
    min_probability: float
    low_probability_fraction: float
    mean_transition_angle_deg: float
    max_transition_angle_deg: float
    continuity_score: float
    signal_score: float
    length_score: float
    endpoint_score: float
    final_score: float
    transition_records: list[dict[str, Any]]
    contains_non_simple_edge: bool


def read_image(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image)


def resolve_required(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"找不到{label}：{path}")
    return path


def normalize_probability(image: np.ndarray) -> np.ndarray:
    array = image.astype(np.float32, copy=False)
    maximum = float(array.max()) if array.size else 0.0
    if maximum > 1.5:
        if np.issubdtype(image.dtype, np.integer):
            array = array / float(np.iinfo(image.dtype).max)
        else:
            array = array / maximum
    return np.clip(array, 0.0, 1.0)


def robust_normalize(image: np.ndarray) -> np.ndarray:
    array = image.astype(np.float32, copy=False)
    low, high = np.percentile(array, [0.2, 99.8])
    if high <= low:
        return np.zeros_like(array, dtype=np.float32)
    return np.clip((array - low) / (high - low), 0.0, 1.0)


def to_uint8_rgb(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        gray = np.round(robust_normalize(image) * 255.0).astype(np.uint8)
        return np.repeat(gray[..., None], 3, axis=2)

    if image.ndim == 3 and image.shape[2] >= 3:
        if image.dtype == np.uint8:
            return image[..., :3].copy()
        rgb = np.zeros(image.shape[:2] + (3,), dtype=np.uint8)
        for channel in range(3):
            rgb[..., channel] = np.round(
                robust_normalize(image[..., channel]) * 255.0
            ).astype(np.uint8)
        return rgb

    raise ValueError(f"无法转换Merge图维度：{image.shape}")


def parse_head_ids(value: str | None) -> set[int] | None:
    if not value:
        return None
    result = {
        int(item.strip())
        for item in value.split(",")
        if item.strip()
    }
    return result or None


def cumulative_lengths(points_xy: np.ndarray) -> np.ndarray:
    points = np.asarray(points_xy, dtype=np.float32)
    if len(points) == 0:
        return np.zeros(0, dtype=np.float32)
    if len(points) == 1:
        return np.zeros(1, dtype=np.float32)
    steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    return np.concatenate(
        [
            np.zeros(1, dtype=np.float32),
            np.cumsum(steps).astype(np.float32),
        ]
    )


def path_length(points_xy: np.ndarray) -> float:
    cumulative = cumulative_lengths(points_xy)
    return float(cumulative[-1]) if len(cumulative) else 0.0


def load_graph(
    graph_path: Path,
) -> tuple[
    dict[int, NodeData],
    dict[int, EdgeData],
    dict[int, list[int]],
]:
    payload = json.loads(graph_path.read_text(encoding="utf-8"))

    nodes: dict[int, NodeData] = {}
    for item in payload.get("nodes", []):
        node = NodeData(
            node_id=int(item["node_id"]),
            kind=str(item.get("kind", "unknown")),
            x=float(item.get("x", 0.0)),
            y=float(item.get("y", 0.0)),
            virtual=bool(item.get("virtual", False)),
        )
        nodes[node.node_id] = node

    edges: dict[int, EdgeData] = {}
    adjacency: dict[int, list[int]] = defaultdict(list)

    for item in payload.get("edges", []):
        points = np.asarray(item.get("points_xy", []), dtype=np.float32)
        if len(points) < 2:
            continue

        edge = EdgeData(
            edge_id=int(item["edge_id"]),
            start_node_id=int(item["start_node_id"]),
            end_node_id=int(item["end_node_id"]),
            points_xy=points,
            cumulative_px=cumulative_lengths(points),
            length_px=float(item.get("length_px", path_length(points))),
            mean_probability=float(item.get("mean_probability", 0.0)),
            min_probability=float(item.get("min_probability", 0.0)),
            quality=str(item.get("quality", "unknown")),
        )
        edges[edge.edge_id] = edge
        adjacency[edge.start_node_id].append(edge.edge_id)
        adjacency[edge.end_node_id].append(edge.edge_id)

    if not nodes or not edges:
        raise ValueError("图JSON中没有可用节点或边。")

    return nodes, edges, dict(adjacency)


def load_entry_results(entry_path: Path) -> dict[int, dict[str, Any]]:
    payload = json.loads(entry_path.read_text(encoding="utf-8"))
    results: dict[int, dict[str, Any]] = {}
    for item in payload.get("results", []):
        results[int(item["head_id"])] = item
    if not results:
        raise ValueError("Stage 2.1结果中没有头部入口记录。")
    return results


def orient_edge_from_node(
    edge: EdgeData,
    current_node_id: int,
) -> tuple[np.ndarray, int]:
    if edge.start_node_id == current_node_id:
        return edge.points_xy.copy(), edge.end_node_id
    if edge.end_node_id == current_node_id:
        return edge.points_xy[::-1].copy(), edge.start_node_id
    raise ValueError(
        f"边E{edge.edge_id}不连接当前节点N{current_node_id}。"
    )


def initial_segment_from_entry(
    entry: dict[str, Any],
    edge: EdgeData,
) -> tuple[np.ndarray, int]:
    s1 = np.asarray(
        [float(entry["s1_x"]), float(entry["s1_y"])],
        dtype=np.float32,
    )
    distances = np.linalg.norm(edge.points_xy - s1, axis=1)
    point_index = int(np.argmin(distances))
    direction = int(entry["direction"])

    if direction > 0:
        segment = edge.points_xy[point_index:].copy()
        terminal_node = edge.end_node_id
    else:
        segment = edge.points_xy[: point_index + 1][::-1].copy()
        terminal_node = edge.start_node_id

    if len(segment) < 2:
        raise ValueError(
            f"Head {entry['head_id']} 的入口边E{edge.edge_id}"
            "在指定方向上没有足够路径点。"
        )

    return segment, terminal_node


def vector_from_end(points_xy: np.ndarray, distance_px: float) -> np.ndarray:
    points = np.asarray(points_xy, dtype=np.float32)
    cumulative = cumulative_lengths(points)
    if len(points) < 2 or float(cumulative[-1]) < 1e-6:
        return np.zeros(2, dtype=np.float32)

    target = max(0.0, float(cumulative[-1]) - float(distance_px))
    index = int(np.searchsorted(cumulative, target, side="left"))
    index = min(index, len(points) - 1)
    return points[-1] - points[index]


def vector_from_start(points_xy: np.ndarray, distance_px: float) -> np.ndarray:
    points = np.asarray(points_xy, dtype=np.float32)
    cumulative = cumulative_lengths(points)
    if len(points) < 2 or float(cumulative[-1]) < 1e-6:
        return np.zeros(2, dtype=np.float32)

    index = int(
        np.searchsorted(cumulative, float(distance_px), side="left")
    )
    index = min(index, len(points) - 1)
    return points[index] - points[0]


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
    return float(math.degrees(math.acos(cosine)))


def transition_metrics(
    incoming_segment: np.ndarray,
    outgoing_segment: np.ndarray,
    distances_px: tuple[float, float, float],
) -> dict[str, Any]:
    angles: list[float] = []
    for distance in distances_px:
        incoming = vector_from_end(incoming_segment, distance)
        outgoing = vector_from_start(outgoing_segment, distance)
        angles.append(angle_degrees(incoming, outgoing))

    weighted_angle = (
        0.50 * angles[0]
        + 0.30 * angles[1]
        + 0.20 * angles[2]
    )

    # 近、中、远三个尺度方向变化相互接近时，说明转弯是连续的；
    # 三个尺度差异过大时，更像局部噪声、突然折返或错误串线。
    angle_spread = float(max(angles) - min(angles))
    curvature_penalty_angle = weighted_angle + 0.20 * angle_spread

    return {
        "near_angle_deg": float(angles[0]),
        "middle_angle_deg": float(angles[1]),
        "far_angle_deg": float(angles[2]),
        "weighted_angle_deg": float(weighted_angle),
        "angle_spread_deg": float(angle_spread),
        "curvature_penalty_angle_deg": float(
            curvature_penalty_angle
        ),
    }


def bridge_probability_metrics(
    probability: np.ndarray,
    start_xy: np.ndarray,
    end_xy: np.ndarray,
) -> tuple[float, float, np.ndarray]:
    start = np.asarray(start_xy, dtype=np.float32)
    end = np.asarray(end_xy, dtype=np.float32)
    distance = float(np.linalg.norm(end - start))
    sample_count = max(3, int(math.ceil(distance * 2.0)) + 1)
    weights = np.linspace(0.0, 1.0, sample_count, dtype=np.float32)
    points = start[None, :] * (1.0 - weights[:, None]) + end[None, :] * weights[:, None]

    x = np.clip(
        np.rint(points[:, 0]).astype(np.int32),
        0,
        probability.shape[1] - 1,
    )
    y = np.clip(
        np.rint(points[:, 1]).astype(np.int32),
        0,
        probability.shape[0] - 1,
    )
    values = probability[y, x]
    return float(values.mean()), float(values.min()), points


def probability_metrics(
    probability: np.ndarray,
    points_xy: np.ndarray,
    low_threshold: float,
) -> tuple[float, float, float]:
    points = np.asarray(points_xy, dtype=np.float32)
    x = np.clip(
        np.rint(points[:, 0]).astype(np.int32),
        0,
        probability.shape[1] - 1,
    )
    y = np.clip(
        np.rint(points[:, 1]).astype(np.int32),
        0,
        probability.shape[0] - 1,
    )
    values = probability[y, x]
    return (
        float(values.mean()),
        float(values.min()),
        float(np.mean(values < float(low_threshold))),
    )


def concatenate_segments(segments: list[np.ndarray]) -> np.ndarray:
    parts: list[np.ndarray] = []
    for index, segment in enumerate(segments):
        if index == 0:
            parts.append(segment)
        else:
            # 不同图边在交叉节点区域的接触像素可能相隔几像素。
            # 保留第二条边全部点，由绘图和最终平滑阶段连接该小间隙。
            parts.append(segment)
    return np.vstack(parts).astype(np.float32)


def partial_transition_score(
    transition: dict[str, Any],
    edge: EdgeData,
) -> float:
    angle_value = float(transition["curvature_penalty_angle_deg"])
    continuity = math.exp(-((angle_value / 28.0) ** 2))
    signal = float(
        np.clip((edge.mean_probability - 0.08) / 0.82, 0.0, 1.0)
    )
    length_support = float(np.clip(edge.length_px / 60.0, 0.0, 1.0))
    quality_penalty = 0.0 if edge.quality == "simple" else 0.15

    return float(
        0.62 * continuity
        + 0.30 * signal
        + 0.08 * length_support
        - quality_penalty
    )


def state_sort_value(state: PathState) -> float:
    transition_count = max(1, len(state.transition_records))
    mean_partial = state.partial_score_sum / transition_count
    useful_length = float(np.clip(state.length_px / 260.0, 0.0, 1.0))
    return float(mean_partial + 0.08 * useful_length)


def finalize_candidate(
    *,
    rank: int,
    head_id: int,
    state: PathState,
    nodes: dict[int, NodeData],
    edges: dict[int, EdgeData],
    probability: np.ndarray,
    low_probability_threshold: float,
    target_length_px: float,
    length_tolerance_px: float,
) -> PathCandidate:
    points = concatenate_segments(state.oriented_segments)
    mean_probability, min_probability, low_fraction = probability_metrics(
        probability,
        points,
        low_threshold=low_probability_threshold,
    )

    transition_angles = [
        float(item["curvature_penalty_angle_deg"])
        for item in state.transition_records
    ]
    mean_transition = (
        float(np.mean(transition_angles))
        if transition_angles
        else 0.0
    )
    max_transition = (
        float(max(transition_angles))
        if transition_angles
        else 0.0
    )

    continuity_score = float(
        math.exp(-((mean_transition / 22.0) ** 2))
        * math.exp(-((max(0.0, max_transition - 35.0) / 35.0) ** 2))
    )
    signal_score = float(
        np.clip((mean_probability - 0.08) / 0.82, 0.0, 1.0)
    )
    length_score = float(
        math.exp(
            -(
                (state.length_px - float(target_length_px))
                / float(length_tolerance_px)
            )
            ** 2
        )
    )

    final_node = nodes[state.current_node_id]
    reached_endpoint = final_node.kind == "endpoint"
    endpoint_score = 1.0 if reached_endpoint else 0.20
    contains_non_simple = any(
        edges[edge_id].quality != "simple"
        for edge_id in state.edge_ids
    )

    final_score = float(
        0.44 * continuity_score
        + 0.27 * signal_score
        + 0.21 * length_score
        + 0.08 * endpoint_score
        - 0.08 * low_fraction
        - (0.05 if contains_non_simple else 0.0)
    )

    return PathCandidate(
        rank=rank,
        head_id=head_id,
        edge_ids=state.edge_ids,
        points_xy=points,
        final_node_id=state.current_node_id,
        final_node_kind=final_node.kind,
        termination_reason=state.termination_reason,
        reached_endpoint=reached_endpoint,
        length_px=float(state.length_px),
        mean_probability=mean_probability,
        min_probability=min_probability,
        low_probability_fraction=low_fraction,
        mean_transition_angle_deg=mean_transition,
        max_transition_angle_deg=max_transition,
        continuity_score=continuity_score,
        signal_score=signal_score,
        length_score=length_score,
        endpoint_score=endpoint_score,
        final_score=final_score,
        transition_records=state.transition_records,
        contains_non_simple_edge=contains_non_simple,
    )


def beam_search_for_head(
    *,
    head_id: int,
    entry: dict[str, Any],
    nodes: dict[int, NodeData],
    edges: dict[int, EdgeData],
    adjacency: dict[int, list[int]],
    probability: np.ndarray,
    beam_width: int,
    max_edges: int,
    max_path_length_px: float,
    hard_transition_angle_deg: float,
    hard_single_scale_angle_deg: float,
    transition_distances_px: tuple[float, float, float],
    low_probability_threshold: float,
    target_length_px: float,
    length_tolerance_px: float,
    candidate_limit: int,
    junction_bridge_max_distance_px: float,
    junction_bridge_min_probability: float,
    junction_bridge_penalty: float,
) -> list[PathCandidate]:
    entry_edge_id = int(entry["edge_id"])
    entry_edge = edges.get(entry_edge_id)
    if entry_edge is None:
        raise ValueError(f"找不到入口边E{entry_edge_id}。")

    initial_segment, initial_node_id = initial_segment_from_entry(
        entry,
        entry_edge,
    )
    initial_length = path_length(initial_segment)

    active: list[PathState] = [
        PathState(
            current_node_id=initial_node_id,
            edge_ids=(entry_edge_id,),
            oriented_segments=[initial_segment],
            length_px=initial_length,
            transition_records=[],
            partial_score_sum=0.0,
            visited_node_ids=frozenset({initial_node_id}),
        )
    ]
    terminated: list[PathState] = []

    for depth in range(max_edges):
        next_states: list[PathState] = []

        for state in active:
            current_node = nodes[state.current_node_id]
            if current_node.kind == "endpoint":
                state.termination_reason = "endpoint"
                terminated.append(state)
                continue

            incident_edges = [
                edge_id
                for edge_id in adjacency.get(state.current_node_id, [])
                if edge_id not in state.edge_ids
            ]

            if not incident_edges:
                state.termination_reason = "no_unused_edge"
                terminated.append(state)
                continue

            accepted_expansion_count = 0
            for next_edge_id in incident_edges:
                next_edge = edges[next_edge_id]
                outgoing_segment, next_node_id = orient_edge_from_node(
                    next_edge,
                    state.current_node_id,
                )

                if next_node_id in state.visited_node_ids:
                    continue

                transition = transition_metrics(
                    state.oriented_segments[-1],
                    outgoing_segment,
                    distances_px=transition_distances_px,
                )

                if (
                    float(transition["curvature_penalty_angle_deg"])
                    > float(hard_transition_angle_deg)
                    or max(
                        float(transition["near_angle_deg"]),
                        float(transition["middle_angle_deg"]),
                        float(transition["far_angle_deg"]),
                    )
                    > float(hard_single_scale_angle_deg)
                ):
                    continue

                new_length = state.length_px + next_edge.length_px
                if new_length > float(max_path_length_px):
                    continue

                accepted_expansion_count += 1
                transition_record = {
                    "at_node_id": int(state.current_node_id),
                    "from_edge_id": int(state.edge_ids[-1]),
                    "to_edge_id": int(next_edge_id),
                    **transition,
                }

                next_states.append(
                    PathState(
                        current_node_id=next_node_id,
                        edge_ids=state.edge_ids + (next_edge_id,),
                        oriented_segments=(
                            state.oriented_segments + [outgoing_segment]
                        ),
                        length_px=float(new_length),
                        transition_records=(
                            state.transition_records + [transition_record]
                        ),
                        partial_score_sum=(
                            state.partial_score_sum
                            + partial_transition_score(
                                transition,
                                next_edge,
                            )
                        ),
                        visited_node_ids=(
                            state.visited_node_ids | {next_node_id}
                        ),
                    )
                )

            if (
                accepted_expansion_count == 0
                and current_node.kind == "junction"
                and float(junction_bridge_max_distance_px) > 0.0
            ):
                current_xy = np.asarray(
                    [current_node.x, current_node.y],
                    dtype=np.float32,
                )

                nearby_junctions: list[tuple[float, int]] = []
                for target_node_id, target_node in nodes.items():
                    if (
                        target_node_id == state.current_node_id
                        or target_node_id in state.visited_node_ids
                        or target_node.kind != "junction"
                    ):
                        continue

                    target_xy = np.asarray(
                        [target_node.x, target_node.y],
                        dtype=np.float32,
                    )
                    bridge_distance = float(
                        np.linalg.norm(target_xy - current_xy)
                    )
                    if (
                        0.5 < bridge_distance
                        <= float(junction_bridge_max_distance_px)
                    ):
                        nearby_junctions.append(
                            (bridge_distance, int(target_node_id))
                        )

                nearby_junctions.sort(key=lambda item: item[0])

                for bridge_distance, target_node_id in nearby_junctions:
                    target_node = nodes[target_node_id]
                    target_xy = np.asarray(
                        [target_node.x, target_node.y],
                        dtype=np.float32,
                    )
                    (
                        bridge_mean_probability,
                        bridge_min_probability,
                        bridge_segment,
                    ) = bridge_probability_metrics(
                        probability,
                        current_xy,
                        target_xy,
                    )

                    if (
                        bridge_mean_probability
                        < float(junction_bridge_min_probability)
                    ):
                        continue

                    for next_edge_id in adjacency.get(target_node_id, []):
                        if next_edge_id in state.edge_ids:
                            continue

                        next_edge = edges[next_edge_id]
                        outgoing_segment, next_node_id = orient_edge_from_node(
                            next_edge,
                            target_node_id,
                        )
                        if next_node_id in state.visited_node_ids:
                            continue

                        combined_outgoing = np.vstack(
                            [
                                bridge_segment,
                                outgoing_segment,
                            ]
                        ).astype(np.float32)

                        transition = transition_metrics(
                            state.oriented_segments[-1],
                            combined_outgoing,
                            distances_px=transition_distances_px,
                        )

                        if (
                            float(
                                transition[
                                    "curvature_penalty_angle_deg"
                                ]
                            )
                            > float(hard_transition_angle_deg)
                            or max(
                                float(transition["near_angle_deg"]),
                                float(transition["middle_angle_deg"]),
                                float(transition["far_angle_deg"]),
                            )
                            > float(hard_single_scale_angle_deg)
                        ):
                            continue

                        new_length = (
                            state.length_px
                            + float(bridge_distance)
                            + next_edge.length_px
                        )
                        if new_length > float(max_path_length_px):
                            continue

                        accepted_expansion_count += 1
                        transition_record = {
                            "at_node_id": int(state.current_node_id),
                            "from_edge_id": int(state.edge_ids[-1]),
                            "to_edge_id": int(next_edge_id),
                            "virtual_junction_bridge": True,
                            "bridge_from_node_id": int(
                                state.current_node_id
                            ),
                            "bridge_to_node_id": int(target_node_id),
                            "bridge_distance_px": float(bridge_distance),
                            "bridge_mean_probability": float(
                                bridge_mean_probability
                            ),
                            "bridge_min_probability": float(
                                bridge_min_probability
                            ),
                            **transition,
                        }

                        next_states.append(
                            PathState(
                                current_node_id=next_node_id,
                                edge_ids=(
                                    state.edge_ids + (next_edge_id,)
                                ),
                                oriented_segments=(
                                    state.oriented_segments
                                    + [combined_outgoing]
                                ),
                                length_px=float(new_length),
                                transition_records=(
                                    state.transition_records
                                    + [transition_record]
                                ),
                                partial_score_sum=(
                                    state.partial_score_sum
                                    + partial_transition_score(
                                        transition,
                                        next_edge,
                                    )
                                    - float(junction_bridge_penalty)
                                ),
                                visited_node_ids=(
                                    state.visited_node_ids
                                    | {target_node_id, next_node_id}
                                ),
                            )
                        )

            if accepted_expansion_count == 0:
                state.termination_reason = "no_forward_continuation"
                terminated.append(state)

        if not next_states:
            active = []
            break

        # 在同一节点、同一末边附近只保留更优状态，减少重复搜索。
        best_by_signature: dict[tuple[int, int, int], PathState] = {}
        for state in next_states:
            signature = (
                int(state.current_node_id),
                int(state.edge_ids[-1]),
                int(round(state.length_px / 25.0)),
            )
            previous = best_by_signature.get(signature)
            if (
                previous is None
                or state_sort_value(state) > state_sort_value(previous)
            ):
                best_by_signature[signature] = state

        active = sorted(
            best_by_signature.values(),
            key=state_sort_value,
            reverse=True,
        )[: int(beam_width)]

    for state in active:
        if not state.termination_reason:
            state.termination_reason = "max_edges_reached"
        terminated.append(state)

    unique_states: dict[tuple[int, ...], PathState] = {}
    for state in terminated:
        previous = unique_states.get(state.edge_ids)
        if previous is None or state_sort_value(state) > state_sort_value(previous):
            unique_states[state.edge_ids] = state

    all_candidates: list[PathCandidate] = []
    for state in unique_states.values():
        all_candidates.append(
            finalize_candidate(
                rank=0,
                head_id=head_id,
                state=state,
                nodes=nodes,
                edges=edges,
                probability=probability,
                low_probability_threshold=low_probability_threshold,
                target_length_px=target_length_px,
                length_tolerance_px=length_tolerance_px,
            )
        )

    # 只要搜索到了长度合理的真实端点，优先在这些端点路径中排名。
    endpoint_candidates = [
        candidate
        for candidate in all_candidates
        if candidate.reached_endpoint
        and candidate.length_px >= 120.0
    ]
    ranking_pool = endpoint_candidates or all_candidates
    ranking_pool.sort(key=lambda item: item.final_score, reverse=True)

    selected = ranking_pool[: int(candidate_limit)]
    for rank, candidate in enumerate(selected, start=1):
        candidate.rank = rank

    return selected


def determine_status(
    best: PathCandidate | None,
    second: PathCandidate | None,
    args: argparse.Namespace,
) -> tuple[str, list[str], float | None]:
    if best is None:
        return "failed_path", ["no_path_candidate"], None

    margin = (
        float(best.final_score - second.final_score)
        if second is not None
        else None
    )
    reasons: list[str] = []

    if not best.reached_endpoint:
        reasons.append("not_reached_endpoint")
    if best.length_px < float(args.auto_min_length):
        reasons.append("path_too_short")
    if best.length_px > float(args.auto_max_length):
        reasons.append("path_too_long")
    if best.mean_probability < float(args.auto_min_mean_probability):
        reasons.append("mean_probability_low")
    if (
        best.low_probability_fraction
        > float(args.auto_max_low_probability_fraction)
    ):
        reasons.append("low_probability_fraction_high")
    if (
        best.mean_transition_angle_deg
        > float(args.auto_max_mean_transition_angle)
    ):
        reasons.append("mean_transition_angle_large")
    if (
        best.max_transition_angle_deg
        > float(args.auto_max_transition_angle)
    ):
        reasons.append("max_transition_angle_large")
    if best.final_score < float(args.auto_min_final_score):
        reasons.append("final_score_low")
    if (
        second is not None
        and margin is not None
        and margin < float(args.auto_min_path_margin)
    ):
        reasons.append("path_margin_small")
    if best.contains_non_simple_edge:
        reasons.append("contains_non_simple_edge")
    if any(
        bool(record.get("virtual_junction_bridge"))
        for record in best.transition_records
    ):
        reasons.append("virtual_junction_bridge_used")

    status = "auto_confirmed_path" if not reasons else "review_required_path"
    return status, reasons, margin


def candidate_to_dict(candidate: PathCandidate) -> dict[str, Any]:
    return {
        "rank": int(candidate.rank),
        "edge_ids": [int(value) for value in candidate.edge_ids],
        "final_node_id": int(candidate.final_node_id),
        "final_node_kind": candidate.final_node_kind,
        "termination_reason": candidate.termination_reason,
        "reached_endpoint": bool(candidate.reached_endpoint),
        "length_px": float(candidate.length_px),
        "mean_probability": float(candidate.mean_probability),
        "min_probability": float(candidate.min_probability),
        "low_probability_fraction": float(
            candidate.low_probability_fraction
        ),
        "mean_transition_angle_deg": float(
            candidate.mean_transition_angle_deg
        ),
        "max_transition_angle_deg": float(
            candidate.max_transition_angle_deg
        ),
        "continuity_score": float(candidate.continuity_score),
        "signal_score": float(candidate.signal_score),
        "length_score": float(candidate.length_score),
        "endpoint_score": float(candidate.endpoint_score),
        "final_score": float(candidate.final_score),
        "contains_non_simple_edge": bool(
            candidate.contains_non_simple_edge
        ),
        "contains_virtual_junction_bridge": bool(
            any(
                bool(record.get("virtual_junction_bridge"))
                for record in candidate.transition_records
            )
        ),
        "transition_records": candidate.transition_records,
        "points_xy": np.round(candidate.points_xy, 2).tolist(),
    }


def color_for_status(status: str) -> tuple[int, int, int]:
    if status == "auto_confirmed_path":
        return (0, 255, 0)
    if status == "review_required_path":
        return (255, 255, 0)
    return (255, 0, 0)


def draw_dotted_line(
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
    cursor = 0.0
    draw = True
    while cursor < length:
        stop = min(length, cursor + dash_length)
        if draw:
            first = start + direction * cursor
            second = start + direction * stop
            cv2.line(
                image,
                tuple(np.rint(first).astype(int)),
                tuple(np.rint(second).astype(int)),
                color,
                thickness,
                lineType=cv2.LINE_AA,
            )
        draw = not draw
        cursor = stop


def draw_dashed_polyline(
    image: np.ndarray,
    points_xy: np.ndarray,
    color: tuple[int, int, int],
    thickness: int = 1,
    dash_length_px: float = 10.0,
) -> None:
    points = np.asarray(points_xy, dtype=np.float32)
    if len(points) < 2:
        return
    cumulative = cumulative_lengths(points)
    total = float(cumulative[-1])
    start_distance = 0.0
    draw = True
    while start_distance < total:
        end_distance = min(total, start_distance + dash_length_px)
        if draw:
            start_index = int(
                np.searchsorted(cumulative, start_distance, side="left")
            )
            end_index = int(
                np.searchsorted(cumulative, end_distance, side="left")
            )
            end_index = min(end_index, len(points) - 1)
            segment = points[start_index : end_index + 1]
            if len(segment) >= 2:
                cv2.polylines(
                    image,
                    [np.rint(segment).astype(np.int32).reshape(-1, 1, 2)],
                    False,
                    color,
                    thickness,
                    lineType=cv2.LINE_AA,
                )
        draw = not draw
        start_distance = end_distance


def make_overlays(
    *,
    merge_rgb: np.ndarray,
    outputs: list[dict[str, Any]],
    output_dir: Path,
) -> tuple[np.ndarray, np.ndarray]:
    best_overlay = merge_rgb.copy()
    comparison_overlay = merge_rgb.copy()

    for output in outputs:
        head_id = int(output["head_id"])
        status = str(output["status"])
        color = color_for_status(status)
        center = (
            float(output["center_x"]),
            float(output["center_y"]),
        )
        s1 = (float(output["s1_x"]), float(output["s1_y"]))
        candidates: list[PathCandidate] = output["candidate_objects"]

        if not candidates:
            cv2.drawMarker(
                best_overlay,
                tuple(np.rint(center).astype(int)),
                color,
                markerType=cv2.MARKER_TILTED_CROSS,
                markerSize=18,
                thickness=2,
                line_type=cv2.LINE_AA,
            )
            continue

        best = candidates[0]
        best_points = np.rint(best.points_xy).astype(np.int32)

        for image in (best_overlay, comparison_overlay):
            draw_dotted_line(image, center, s1, color, thickness=1)
            cv2.polylines(
                image,
                [best_points.reshape(-1, 1, 2)],
                False,
                color,
                3,
                lineType=cv2.LINE_AA,
            )
            midpoint = best_points[len(best_points) // 2]
            cv2.putText(
                image,
                f"H{head_id}",
                (int(midpoint[0]) + 4, int(midpoint[1]) - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                color,
                1,
                lineType=cv2.LINE_AA,
            )

        if len(candidates) >= 2:
            draw_dashed_polyline(
                comparison_overlay,
                candidates[1].points_xy,
                color=(0, 255, 255),
                thickness=2,
                dash_length_px=11.0,
            )

        # 每个头部输出局部候选对比图：最优实线，第二候选青色虚线。
        all_points = [best.points_xy, np.asarray([center, s1])]
        if len(candidates) >= 2:
            all_points.append(candidates[1].points_xy)
        combined = np.vstack(all_points)
        x0 = max(0, int(math.floor(float(combined[:, 0].min()) - 80)))
        y0 = max(0, int(math.floor(float(combined[:, 1].min()) - 80)))
        x1 = min(
            merge_rgb.shape[1],
            int(math.ceil(float(combined[:, 0].max()) + 80)),
        )
        y1 = min(
            merge_rgb.shape[0],
            int(math.ceil(float(combined[:, 1].max()) + 80)),
        )
        crop = comparison_overlay[y0:y1, x0:x1]
        Image.fromarray(crop).save(
            output_dir / f"head_{head_id:03d}_path_candidates.png"
        )

    return best_overlay, comparison_overlay


def detect_shared_edges(outputs: list[dict[str, Any]]) -> None:
    edge_owners: dict[int, list[int]] = defaultdict(list)
    for output in outputs:
        candidates: list[PathCandidate] = output["candidate_objects"]
        if not candidates:
            continue
        for edge_id in candidates[0].edge_ids:
            edge_owners[int(edge_id)].append(int(output["head_id"]))

    conflicts_by_head: dict[int, set[int]] = defaultdict(set)
    for owners in edge_owners.values():
        unique = sorted(set(owners))
        if len(unique) < 2:
            continue
        for head_id in unique:
            conflicts_by_head[head_id].update(
                other for other in unique if other != head_id
            )

    for output in outputs:
        head_id = int(output["head_id"])
        conflicts = sorted(conflicts_by_head.get(head_id, set()))
        output["shared_edge_conflict_heads"] = conflicts
        if conflicts:
            reasons = output["review_reasons"]
            if "shared_path_edge" not in reasons:
                reasons.append("shared_path_edge")
            if output["status"] == "auto_confirmed_path":
                output["status"] = "review_required_path"


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
        description="Stage 2.2：图结构Beam Search完整尾部路径"
    )
    parser.add_argument(
        "--graph",
        default=(
            "tail_graph_stage1_2_output/"
            "tail_graph_stage1_2.json"
        ),
    )
    parser.add_argument(
        "--entries",
        default=(
            "tail_graph_stage2_1_full_v1_1/"
            "head_graph_entry_results.json"
        ),
    )
    parser.add_argument(
        "--probability",
        default=(
            "tail_graph_stage1_output/"
            "02_probability_uint16.tif"
        ),
    )
    parser.add_argument("--merge", default="1_Merge.tif")
    parser.add_argument(
        "--head-ids",
        default="",
        help="例如13,21,22,31,88；留空则按entry-status筛选",
    )
    parser.add_argument(
        "--entry-status",
        default="auto_confirmed,review_required",
        help="head-ids留空时允许的Stage2.1状态，逗号分隔",
    )
    parser.add_argument(
        "--output-dir",
        default="tail_graph_stage2_2_known5",
    )

    parser.add_argument("--beam-width", type=int, default=32)
    parser.add_argument("--max-edges", type=int, default=18)
    parser.add_argument("--max-path-length", type=float, default=520.0)
    parser.add_argument(
        "--hard-transition-angle",
        type=float,
        default=82.0,
    )
    parser.add_argument(
        "--hard-single-scale-angle",
        type=float,
        default=125.0,
    )
    parser.add_argument(
        "--transition-distances",
        default="8,18,30",
        help="交叉节点多尺度切线距离，例如8,18,30",
    )
    parser.add_argument("--candidate-limit", type=int, default=5)
    parser.add_argument(
        "--junction-bridge-max-distance",
        type=float,
        default=8.0,
        help=(
            "仅当交叉节点没有可接受的直接后继时，"
            "允许跨越的相邻交叉节点最大距离"
        ),
    )
    parser.add_argument(
        "--junction-bridge-min-probability",
        type=float,
        default=0.25,
        help="短交叉节点桥接线的最低平均尾部概率",
    )
    parser.add_argument(
        "--junction-bridge-penalty",
        type=float,
        default=0.12,
        help="使用一次短交叉节点桥接时的搜索分数惩罚",
    )
    parser.add_argument(
        "--low-probability-threshold",
        type=float,
        default=0.12,
    )
    parser.add_argument("--target-length", type=float, default=300.0)
    parser.add_argument(
        "--length-tolerance",
        type=float,
        default=150.0,
    )

    parser.add_argument("--auto-min-length", type=float, default=220.0)
    parser.add_argument("--auto-max-length", type=float, default=420.0)
    parser.add_argument(
        "--auto-min-mean-probability",
        type=float,
        default=0.38,
    )
    parser.add_argument(
        "--auto-max-low-probability-fraction",
        type=float,
        default=0.18,
    )
    parser.add_argument(
        "--auto-max-mean-transition-angle",
        type=float,
        default=32.0,
    )
    parser.add_argument(
        "--auto-max-transition-angle",
        type=float,
        default=60.0,
    )
    parser.add_argument(
        "--auto-min-final-score",
        type=float,
        default=0.46,
    )
    parser.add_argument(
        "--auto-min-path-margin",
        type=float,
        default=0.08,
    )
    return parser


def parse_three_floats(value: str) -> tuple[float, float, float]:
    parts = [float(item.strip()) for item in value.split(",") if item.strip()]
    if len(parts) != 3 or any(item <= 0 for item in parts):
        raise ValueError("transition-distances必须是3个正数，例如8,18,30。")
    return float(parts[0]), float(parts[1]), float(parts[2])


def main() -> int:
    started = time.perf_counter()
    args = build_parser().parse_args()

    graph_path = resolve_required(args.graph, "图结构JSON")
    entries_path = resolve_required(args.entries, "Stage 2.1入口结果")
    probability_path = resolve_required(args.probability, "概率图")
    merge_path = resolve_required(args.merge, "Merge图")
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    transition_distances = parse_three_floats(args.transition_distances)
    nodes, edges, adjacency = load_graph(graph_path)
    entry_results = load_entry_results(entries_path)
    probability = normalize_probability(read_image(probability_path))
    merge_rgb = to_uint8_rgb(read_image(merge_path))

    if probability.shape != merge_rgb.shape[:2]:
        raise ValueError(
            "概率图与Merge图尺寸不一致："
            f"probability={probability.shape}，"
            f"merge={merge_rgb.shape[:2]}"
        )

    selected_head_ids = parse_head_ids(args.head_ids)
    if selected_head_ids is None:
        allowed_status = {
            item.strip()
            for item in str(args.entry_status).split(",")
            if item.strip()
        }
        selected_head_ids = {
            head_id
            for head_id, item in entry_results.items()
            if str(item.get("status", "")) in allowed_status
            and item.get("edge_id") is not None
        }

    ordered_head_ids = sorted(selected_head_ids)
    print("图结构：", graph_path)
    print("入口结果：", entries_path)
    print("概率图：", probability_path)
    print("Merge图：", merge_path)
    print("输出目录：", output_dir)
    print("处理头部：", ordered_head_ids)

    outputs: list[dict[str, Any]] = []

    for index, head_id in enumerate(ordered_head_ids, start=1):
        entry = entry_results.get(head_id)
        if entry is None:
            outputs.append(
                {
                    "head_id": head_id,
                    "center_x": 0.0,
                    "center_y": 0.0,
                    "s1_x": 0.0,
                    "s1_y": 0.0,
                    "entry_edge_id": None,
                    "entry_status": "missing",
                    "status": "failed_path",
                    "review_reasons": ["missing_entry_result"],
                    "path_margin": None,
                    "candidate_objects": [],
                }
            )
            continue

        if entry.get("edge_id") is None:
            outputs.append(
                {
                    "head_id": head_id,
                    "center_x": float(entry.get("center_x", 0.0)),
                    "center_y": float(entry.get("center_y", 0.0)),
                    "s1_x": float(entry.get("center_x", 0.0)),
                    "s1_y": float(entry.get("center_y", 0.0)),
                    "entry_edge_id": None,
                    "entry_status": str(entry.get("status", "unmatched")),
                    "status": "failed_path",
                    "review_reasons": ["entry_unmatched"],
                    "path_margin": None,
                    "candidate_objects": [],
                }
            )
            continue

        try:
            # 第一遍严格使用原始图结构。只有完全没有到达真实端点时，
            # 才启动短距离交叉节点桥接。这样不会改变原本已经完整的路径。
            candidates = beam_search_for_head(
                head_id=head_id,
                entry=entry,
                nodes=nodes,
                edges=edges,
                adjacency=adjacency,
                probability=probability,
                beam_width=int(args.beam_width),
                max_edges=int(args.max_edges),
                max_path_length_px=float(args.max_path_length),
                hard_transition_angle_deg=float(
                    args.hard_transition_angle
                ),
                hard_single_scale_angle_deg=float(
                    args.hard_single_scale_angle
                ),
                transition_distances_px=transition_distances,
                low_probability_threshold=float(
                    args.low_probability_threshold
                ),
                target_length_px=float(args.target_length),
                length_tolerance_px=float(args.length_tolerance),
                candidate_limit=int(args.candidate_limit),
                junction_bridge_max_distance_px=0.0,
                junction_bridge_min_probability=float(
                    args.junction_bridge_min_probability
                ),
                junction_bridge_penalty=float(
                    args.junction_bridge_penalty
                ),
            )

            if (
                not any(candidate.reached_endpoint for candidate in candidates)
                and float(args.junction_bridge_max_distance) > 0.0
            ):
                candidates = beam_search_for_head(
                    head_id=head_id,
                    entry=entry,
                    nodes=nodes,
                    edges=edges,
                    adjacency=adjacency,
                    probability=probability,
                    beam_width=int(args.beam_width),
                    max_edges=int(args.max_edges),
                    max_path_length_px=float(args.max_path_length),
                    hard_transition_angle_deg=float(
                        args.hard_transition_angle
                    ),
                    hard_single_scale_angle_deg=float(
                        args.hard_single_scale_angle
                    ),
                    transition_distances_px=transition_distances,
                    low_probability_threshold=float(
                        args.low_probability_threshold
                    ),
                    target_length_px=float(args.target_length),
                    length_tolerance_px=float(args.length_tolerance),
                    candidate_limit=int(args.candidate_limit),
                    junction_bridge_max_distance_px=float(
                        args.junction_bridge_max_distance
                    ),
                    junction_bridge_min_probability=float(
                        args.junction_bridge_min_probability
                    ),
                    junction_bridge_penalty=float(
                        args.junction_bridge_penalty
                    ),
                )

            best = candidates[0] if candidates else None
            second = candidates[1] if len(candidates) >= 2 else None
            status, reasons, margin = determine_status(
                best,
                second,
                args,
            )

            outputs.append(
                {
                    "head_id": int(head_id),
                    "center_x": float(entry["center_x"]),
                    "center_y": float(entry["center_y"]),
                    "s1_x": float(entry["s1_x"]),
                    "s1_y": float(entry["s1_y"]),
                    "entry_edge_id": int(entry["edge_id"]),
                    "entry_status": str(entry.get("status", "")),
                    "entry_score": float(entry.get("score", 0.0)),
                    "status": status,
                    "review_reasons": reasons,
                    "path_margin": margin,
                    "candidate_objects": candidates,
                }
            )
        except Exception as exc:  # noqa: BLE001
            outputs.append(
                {
                    "head_id": int(head_id),
                    "center_x": float(entry.get("center_x", 0.0)),
                    "center_y": float(entry.get("center_y", 0.0)),
                    "s1_x": float(entry.get("s1_x", 0.0)),
                    "s1_y": float(entry.get("s1_y", 0.0)),
                    "entry_edge_id": int(entry["edge_id"]),
                    "entry_status": str(entry.get("status", "")),
                    "status": "failed_path",
                    "review_reasons": [f"search_error:{exc}"],
                    "path_margin": None,
                    "candidate_objects": [],
                }
            )

        print(f"路径搜索：{index}/{len(ordered_head_ids)}，Head={head_id}")

    detect_shared_edges(outputs)

    best_overlay, comparison_overlay = make_overlays(
        merge_rgb=merge_rgb,
        outputs=outputs,
        output_dir=output_dir,
    )
    Image.fromarray(best_overlay).save(
        output_dir / "01_best_paths_overlay.png"
    )
    Image.fromarray(comparison_overlay).save(
        output_dir / "02_best_and_second_paths_overlay.png"
    )

    centerlines = np.zeros(probability.shape, dtype=np.uint16)
    for output in outputs:
        candidates: list[PathCandidate] = output["candidate_objects"]
        if not candidates:
            continue
        points = np.rint(candidates[0].points_xy).astype(np.int32)
        x = np.clip(points[:, 0], 0, centerlines.shape[1] - 1)
        y = np.clip(points[:, 1], 0, centerlines.shape[0] - 1)
        centerlines[y, x] = np.uint16(output["head_id"])
    Image.fromarray(centerlines).save(
        output_dir / "03_best_path_centerlines_uint16.tif"
    )

    summary_rows: list[dict[str, Any]] = []
    json_results: list[dict[str, Any]] = []

    for output in outputs:
        candidates: list[PathCandidate] = output.pop("candidate_objects")
        best = candidates[0] if candidates else None
        second = candidates[1] if len(candidates) >= 2 else None

        summary_row = {
            "head_id": int(output["head_id"]),
            "status": output["status"],
            "review_reasons": "|".join(output["review_reasons"]),
            "entry_status": output.get("entry_status", ""),
            "entry_edge_id": output.get("entry_edge_id"),
            "entry_score": output.get("entry_score"),
            "candidate_count": len(candidates),
            "best_edge_ids": (
                ",".join(str(value) for value in best.edge_ids)
                if best is not None
                else ""
            ),
            "best_final_node_id": (
                best.final_node_id if best is not None else None
            ),
            "best_termination_reason": (
                best.termination_reason if best is not None else ""
            ),
            "best_reached_endpoint": (
                best.reached_endpoint if best is not None else False
            ),
            "best_length_px": (
                best.length_px if best is not None else None
            ),
            "best_mean_probability": (
                best.mean_probability if best is not None else None
            ),
            "best_min_probability": (
                best.min_probability if best is not None else None
            ),
            "best_low_probability_fraction": (
                best.low_probability_fraction if best is not None else None
            ),
            "best_mean_transition_angle_deg": (
                best.mean_transition_angle_deg if best is not None else None
            ),
            "best_max_transition_angle_deg": (
                best.max_transition_angle_deg if best is not None else None
            ),
            "best_final_score": (
                best.final_score if best is not None else None
            ),
            "second_edge_ids": (
                ",".join(str(value) for value in second.edge_ids)
                if second is not None
                else ""
            ),
            "second_final_score": (
                second.final_score if second is not None else None
            ),
            "path_margin": output.get("path_margin"),
            "shared_edge_conflict_heads": ",".join(
                str(value)
                for value in output.get("shared_edge_conflict_heads", [])
            ),
        }
        summary_rows.append(summary_row)

        json_results.append(
            {
                **output,
                "review_reasons": list(output["review_reasons"]),
                "candidates": [
                    candidate_to_dict(candidate)
                    for candidate in candidates
                ],
            }
        )

    write_csv(
        output_dir / "path_summary.csv",
        summary_rows,
        [
            "head_id",
            "status",
            "review_reasons",
            "entry_status",
            "entry_edge_id",
            "entry_score",
            "candidate_count",
            "best_edge_ids",
            "best_final_node_id",
            "best_termination_reason",
            "best_reached_endpoint",
            "best_length_px",
            "best_mean_probability",
            "best_min_probability",
            "best_low_probability_fraction",
            "best_mean_transition_angle_deg",
            "best_max_transition_angle_deg",
            "best_final_score",
            "second_edge_ids",
            "second_final_score",
            "path_margin",
            "shared_edge_conflict_heads",
        ],
    )

    status_counts = {
        status: int(
            sum(item["status"] == status for item in json_results)
        )
        for status in (
            "auto_confirmed_path",
            "review_required_path",
            "failed_path",
        )
    }

    payload = {
        "version": "tail_graph_stage2_2_beam_path_v1_2_fullfix",
        "parameters": {
            "beam_width": int(args.beam_width),
            "max_edges": int(args.max_edges),
            "max_path_length": float(args.max_path_length),
            "hard_transition_angle": float(
                args.hard_transition_angle
            ),
            "hard_single_scale_angle": float(
                args.hard_single_scale_angle
            ),
            "transition_distances": list(transition_distances),
            "candidate_limit": int(args.candidate_limit),
            "junction_bridge_max_distance": float(
                args.junction_bridge_max_distance
            ),
            "junction_bridge_min_probability": float(
                args.junction_bridge_min_probability
            ),
            "junction_bridge_penalty": float(
                args.junction_bridge_penalty
            ),
            "low_probability_threshold": float(
                args.low_probability_threshold
            ),
            "target_length": float(args.target_length),
            "length_tolerance": float(args.length_tolerance),
            "auto_min_length": float(args.auto_min_length),
            "auto_max_length": float(args.auto_max_length),
            "auto_min_mean_probability": float(
                args.auto_min_mean_probability
            ),
            "auto_max_low_probability_fraction": float(
                args.auto_max_low_probability_fraction
            ),
            "auto_max_mean_transition_angle": float(
                args.auto_max_mean_transition_angle
            ),
            "auto_max_transition_angle": float(
                args.auto_max_transition_angle
            ),
            "auto_min_final_score": float(args.auto_min_final_score),
            "auto_min_path_margin": float(args.auto_min_path_margin),
        },
        "status_counts": status_counts,
        "results": json_results,
    }
    (output_dir / "path_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    elapsed = time.perf_counter() - started
    print("\nStage 2.2完成。")
    print(
        f"auto_confirmed_path={status_counts['auto_confirmed_path']}，"
        f"review_required_path={status_counts['review_required_path']}，"
        f"failed_path={status_counts['failed_path']}"
    )
    print(f"总耗时：{elapsed:.2f}s")
    print("请重点检查：")
    print(output_dir / "01_best_paths_overlay.png")
    print(output_dir / "02_best_and_second_paths_overlay.png")
    print(output_dir / "path_summary.csv")
    print(output_dir / "path_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
