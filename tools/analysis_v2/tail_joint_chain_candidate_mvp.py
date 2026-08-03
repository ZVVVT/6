#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双向头尾联合候选的尾部链延伸 MVP（第二阶段）。

目标：
1. 从第一阶段 joint_start_candidates.json 的双向起始候选出发；
2. 在无分叉原子边图上按局部方向、信号和断裂间隔延伸单条中心线；
3. 每个联合假设始终绑定一个头部，但允许头部无可信链；
4. 不做最终全局强制分配，不生成 TailFinalLabels，不进入测量。

固定输出：
- joint_chain_candidates.json
- joint_chain_candidates_overlay.png
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import cv2
    import numpy as np
    from PIL import Image
except ImportError as exc:
    print("缺少依赖：{}".format(exc))
    raise SystemExit(1) from exc


OUTPUT_JSON_NAME = "joint_chain_candidates.json"
OUTPUT_OVERLAY_NAME = "joint_chain_candidates_overlay.png"


@dataclass(frozen=True)
class DirectedEdge:
    edge_id: int
    side: str
    start_node_id: int
    end_node_id: int
    points_xy: np.ndarray
    length_px: float
    mean_probability: float
    min_probability: float
    quality: str


@dataclass
class PathState:
    head_id: int
    start_status: str
    start_score: float
    head_major_axis_length: float
    edge_ids: Tuple[int, ...]
    edge_sides: Tuple[str, ...]
    points_xy: np.ndarray
    transitions: Tuple[Dict[str, Any], ...]
    total_length_px: float
    signal_weighted_sum: float
    signal_length_sum: float
    continuity_sum: float
    transition_count: int
    bridge_count: int
    maximum_turn_angle_deg: float
    cumulative_objective: float
    ended_reason: str = ""

    @property
    def mean_probability(self) -> float:
        if self.signal_length_sum <= 1e-6:
            return 0.0
        return float(self.signal_weighted_sum / self.signal_length_sum)

    @property
    def mean_continuity(self) -> float:
        if self.transition_count <= 0:
            return 1.0
        return float(self.continuity_sum / self.transition_count)


@dataclass(frozen=True)
class Transition:
    next_edge: DirectedEdge
    mode: str
    gap_px: float
    turn_angle_deg: float
    exit_alignment_deg: float
    entry_alignment_deg: float
    continuity_score: float
    transition_score: float
    bridge_points_xy: np.ndarray


def read_image(path: Path) -> np.ndarray:
    with Image.open(str(path)) as image:
        return np.asarray(image)


def robust_normalize(image: np.ndarray, low_p: float = 0.2, high_p: float = 99.8) -> np.ndarray:
    array = image.astype(np.float32, copy=False)
    low, high = np.percentile(array, [low_p, high_p])
    if high <= low:
        return np.zeros(array.shape, dtype=np.float32)
    return np.clip((array - low) / (high - low), 0.0, 1.0)


def normalize_probability(image: np.ndarray) -> np.ndarray:
    array = image.astype(np.float32, copy=False)
    maximum = float(array.max()) if array.size else 0.0
    if maximum > 1.5:
        array /= 255.0 if maximum <= 255.0 else 65535.0
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
    raise ValueError("无法转换图像维度：{}".format(image.shape))


def unit_vector(vector: np.ndarray) -> Optional[np.ndarray]:
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if norm < 1e-6:
        return None
    return vector / norm


def angle_degrees(first: np.ndarray, second: np.ndarray) -> float:
    first_unit = unit_vector(first)
    second_unit = unit_vector(second)
    if first_unit is None or second_unit is None:
        return 180.0
    cosine = float(np.clip(np.dot(first_unit, second_unit), -1.0, 1.0))
    return float(math.degrees(math.acos(cosine)))


def cumulative_lengths(points_xy: np.ndarray) -> np.ndarray:
    points = np.asarray(points_xy, dtype=np.float32)
    if len(points) == 0:
        return np.zeros(0, dtype=np.float32)
    if len(points) == 1:
        return np.zeros(1, dtype=np.float32)
    steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    return np.concatenate(
        [np.zeros(1, dtype=np.float32), np.cumsum(steps).astype(np.float32)]
    )


def path_length(points_xy: np.ndarray) -> float:
    cumulative = cumulative_lengths(points_xy)
    return float(cumulative[-1]) if len(cumulative) else 0.0


def tangent_from_start(points_xy: np.ndarray, distance_px: float = 14.0) -> np.ndarray:
    points = np.asarray(points_xy, dtype=np.float32)
    if len(points) < 2:
        return np.asarray([0.0, 0.0], dtype=np.float32)
    cumulative = cumulative_lengths(points)
    index = int(np.searchsorted(cumulative, min(distance_px, float(cumulative[-1])), side="left"))
    index = max(1, min(index, len(points) - 1))
    vector = unit_vector(points[index] - points[0])
    return vector if vector is not None else np.asarray([0.0, 0.0], dtype=np.float32)


def tangent_at_end(points_xy: np.ndarray, distance_px: float = 14.0) -> np.ndarray:
    return -tangent_from_start(np.asarray(points_xy, dtype=np.float32)[::-1], distance_px)


def sample_path(points_xy: np.ndarray, maximum_points: int = 500) -> np.ndarray:
    points = np.asarray(points_xy, dtype=np.float32)
    if len(points) <= maximum_points:
        return points
    step = max(1, int(math.ceil(len(points) / float(maximum_points))))
    sampled = points[::step]
    if not np.array_equal(sampled[-1], points[-1]):
        sampled = np.vstack([sampled, points[-1]])
    return sampled


def load_graph(graph_path: Path) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, List[int]], List[DirectedEdge]]:
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    edge_by_id: Dict[int, Dict[str, Any]] = {}
    adjacency: Dict[int, List[int]] = {}
    directed: List[DirectedEdge] = []

    for item in list(payload.get("edges") or []):
        edge_id = int(item["edge_id"])
        points = np.asarray(item.get("points_xy") or [], dtype=np.float32)
        if len(points) < 2:
            continue
        start_node = int(item["start_node_id"])
        end_node = int(item["end_node_id"])
        length_px = float(item.get("length_px", path_length(points)))
        record = dict(item)
        record["points_array"] = points
        edge_by_id[edge_id] = record
        adjacency.setdefault(start_node, []).append(edge_id)
        adjacency.setdefault(end_node, []).append(edge_id)
        directed.append(
            DirectedEdge(
                edge_id=edge_id,
                side="start",
                start_node_id=start_node,
                end_node_id=end_node,
                points_xy=points,
                length_px=length_px,
                mean_probability=float(item.get("mean_probability", 0.0)),
                min_probability=float(item.get("min_probability", 0.0)),
                quality=str(item.get("quality", "unknown")),
            )
        )
        directed.append(
            DirectedEdge(
                edge_id=edge_id,
                side="end",
                start_node_id=end_node,
                end_node_id=start_node,
                points_xy=points[::-1].copy(),
                length_px=length_px,
                mean_probability=float(item.get("mean_probability", 0.0)),
                min_probability=float(item.get("min_probability", 0.0)),
                quality=str(item.get("quality", "unknown")),
            )
        )

    if not edge_by_id:
        raise ValueError("图 JSON 中没有有效边。")
    return edge_by_id, adjacency, directed


def directed_edge(edge_by_id: Dict[int, Dict[str, Any]], edge_id: int, side: str) -> DirectedEdge:
    item = edge_by_id[int(edge_id)]
    points = np.asarray(item["points_array"], dtype=np.float32)
    start_node = int(item["start_node_id"])
    end_node = int(item["end_node_id"])
    if side == "start":
        oriented = points
        oriented_start, oriented_end = start_node, end_node
    elif side == "end":
        oriented = points[::-1].copy()
        oriented_start, oriented_end = end_node, start_node
    else:
        raise ValueError("未知边方向：{}".format(side))
    return DirectedEdge(
        edge_id=int(edge_id),
        side=str(side),
        start_node_id=oriented_start,
        end_node_id=oriented_end,
        points_xy=oriented,
        length_px=float(item.get("length_px", path_length(oriented))),
        mean_probability=float(item.get("mean_probability", 0.0)),
        min_probability=float(item.get("min_probability", 0.0)),
        quality=str(item.get("quality", "unknown")),
    )


def load_start_selections(start_json_path: Path, include_review: bool) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    payload = json.loads(start_json_path.read_text(encoding="utf-8"))
    allowed = {"strong_bidirectional"}
    if include_review:
        allowed.add("review_bidirectional")
    selections = [
        dict(item)
        for item in list(payload.get("selections") or [])
        if str(item.get("status")) in allowed
        and item.get("edge_id") is not None
        and item.get("side") in {"start", "end"}
    ]
    return payload, selections


def directed_endpoint_index(directed_edges: Sequence[DirectedEdge]) -> Tuple[np.ndarray, List[DirectedEdge]]:
    usable = [item for item in directed_edges if len(item.points_xy) >= 2]
    points = np.asarray([item.points_xy[0] for item in usable], dtype=np.float32)
    return points, usable


def score_transition(
    current_points: np.ndarray,
    next_edge: DirectedEdge,
    mode: str,
    gap_px: float,
    maximum_turn_angle_deg: float,
    maximum_bridge_angle_deg: float,
) -> Optional[Transition]:
    incoming = tangent_at_end(current_points)
    outgoing = tangent_from_start(next_edge.points_xy)
    current_end = np.asarray(current_points[-1], dtype=np.float32)
    next_start = np.asarray(next_edge.points_xy[0], dtype=np.float32)

    if mode == "adjacent":
        turn_angle = angle_degrees(incoming, outgoing)
        if turn_angle > maximum_turn_angle_deg:
            return None
        exit_alignment = turn_angle
        entry_alignment = turn_angle
        bridge_points = np.empty((0, 2), dtype=np.float32)
        continuity = math.exp(-((turn_angle / 34.0) ** 2))
        gap_score = 1.0
    else:
        bridge = next_start - current_end
        bridge_unit = unit_vector(bridge)
        if bridge_unit is None:
            return None
        exit_alignment = angle_degrees(incoming, bridge_unit)
        entry_alignment = angle_degrees(bridge_unit, outgoing)
        turn_angle = max(exit_alignment, entry_alignment)
        if exit_alignment > maximum_bridge_angle_deg or entry_alignment > maximum_bridge_angle_deg:
            return None
        continuity = math.exp(-(((exit_alignment + entry_alignment) / 2.0 / 30.0) ** 2))
        gap_score = math.exp(-((gap_px / 10.0) ** 2))
        bridge_points = np.asarray([current_end, next_start], dtype=np.float32)

    topology_score = 1.0 if next_edge.quality == "simple" else 0.15
    signal_score = float(np.clip((next_edge.mean_probability - 0.03) / 0.55, 0.0, 1.0))
    transition_score = (
        0.56 * continuity
        + 0.20 * gap_score
        + 0.18 * signal_score
        + 0.06 * topology_score
    )
    if mode == "bridge":
        transition_score -= 0.08
    return Transition(
        next_edge=next_edge,
        mode=mode,
        gap_px=float(gap_px),
        turn_angle_deg=float(turn_angle),
        exit_alignment_deg=float(exit_alignment),
        entry_alignment_deg=float(entry_alignment),
        continuity_score=float(continuity),
        transition_score=float(transition_score),
        bridge_points_xy=bridge_points,
    )


def find_transitions(
    state: PathState,
    edge_by_id: Dict[int, Dict[str, Any]],
    adjacency: Dict[int, List[int]],
    directed_points: np.ndarray,
    directed_edges: Sequence[DirectedEdge],
    maximum_turn_angle_deg: float,
    maximum_bridge_gap_px: float,
    maximum_bridge_angle_deg: float,
    maximum_options: int,
) -> List[Transition]:
    used = set(state.edge_ids)
    current_edge_id = int(state.edge_ids[-1])
    current_side = str(state.edge_sides[-1])
    current = directed_edge(edge_by_id, current_edge_id, current_side)
    options: Dict[Tuple[int, str], Transition] = {}

    # 图节点内连续延伸。
    for edge_id in adjacency.get(current.end_node_id, []):
        if int(edge_id) in used:
            continue
        item = edge_by_id[int(edge_id)]
        if int(item["start_node_id"]) == current.end_node_id:
            side = "start"
        elif int(item["end_node_id"]) == current.end_node_id:
            side = "end"
        else:
            continue
        next_edge = directed_edge(edge_by_id, int(edge_id), side)
        transition = score_transition(
            state.points_xy,
            next_edge,
            "adjacent",
            0.0,
            maximum_turn_angle_deg,
            maximum_bridge_angle_deg,
        )
        if transition is not None:
            options[(next_edge.edge_id, next_edge.side)] = transition

    # 对骨架断裂做短距离、双向切线一致的桥接。
    current_end = np.asarray(state.points_xy[-1], dtype=np.float32)
    distances = np.linalg.norm(directed_points - current_end[None, :], axis=1)
    nearby = np.flatnonzero((distances > 1.25) & (distances <= maximum_bridge_gap_px))
    for index in nearby:
        next_edge = directed_edges[int(index)]
        if next_edge.edge_id in used:
            continue
        key = (next_edge.edge_id, next_edge.side)
        transition = score_transition(
            state.points_xy,
            next_edge,
            "bridge",
            float(distances[int(index)]),
            maximum_turn_angle_deg,
            maximum_bridge_angle_deg,
        )
        if transition is None:
            continue
        existing = options.get(key)
        if existing is None or transition.transition_score > existing.transition_score:
            options[key] = transition

    ranked = sorted(options.values(), key=lambda item: item.transition_score, reverse=True)
    return ranked[: max(1, int(maximum_options))]


def append_transition(state: PathState, transition: Transition) -> PathState:
    next_points = np.asarray(transition.next_edge.points_xy, dtype=np.float32)
    parts = [state.points_xy]
    added_length = float(transition.next_edge.length_px)
    if transition.mode == "bridge":
        parts.append(transition.bridge_points_xy[1:])
        added_length += float(transition.gap_px)
    parts.append(next_points[1:])
    combined = np.vstack(parts)

    record = {
        "mode": transition.mode,
        "from_edge_id": int(state.edge_ids[-1]),
        "to_edge_id": int(transition.next_edge.edge_id),
        "to_side": str(transition.next_edge.side),
        "gap_px": float(transition.gap_px),
        "turn_angle_deg": float(transition.turn_angle_deg),
        "exit_alignment_deg": float(transition.exit_alignment_deg),
        "entry_alignment_deg": float(transition.entry_alignment_deg),
        "continuity_score": float(transition.continuity_score),
        "transition_score": float(transition.transition_score),
    }
    return PathState(
        head_id=state.head_id,
        start_status=state.start_status,
        start_score=state.start_score,
        head_major_axis_length=state.head_major_axis_length,
        edge_ids=state.edge_ids + (int(transition.next_edge.edge_id),),
        edge_sides=state.edge_sides + (str(transition.next_edge.side),),
        points_xy=combined,
        transitions=state.transitions + (record,),
        total_length_px=float(state.total_length_px + added_length),
        signal_weighted_sum=float(
            state.signal_weighted_sum
            + transition.next_edge.mean_probability * transition.next_edge.length_px
        ),
        signal_length_sum=float(state.signal_length_sum + transition.next_edge.length_px),
        continuity_sum=float(state.continuity_sum + transition.continuity_score),
        transition_count=int(state.transition_count + 1),
        bridge_count=int(state.bridge_count + (1 if transition.mode == "bridge" else 0)),
        maximum_turn_angle_deg=float(max(state.maximum_turn_angle_deg, transition.turn_angle_deg)),
        cumulative_objective=float(
            state.cumulative_objective
            + transition.transition_score
            + 0.05 * min(1.0, transition.next_edge.length_px / 80.0)
        ),
    )


def expected_tail_length_px(head_major_axis_length: float) -> float:
    # 同一倍率下，以头部长轴作为内部尺度。用户实测完整尾部约为头长的10倍；
    # 这里只作为软目标，不作为短荧光尾部的硬性淘汰条件。
    major = max(float(head_major_axis_length), 18.0)
    return float(np.clip(9.5 * major, 220.0, 430.0))


def minimum_preferred_length_px(head_major_axis_length: float) -> float:
    major = max(float(head_major_axis_length), 18.0)
    return float(np.clip(5.0 * major, 110.0, 250.0))


def length_score(state: PathState) -> float:
    length = float(state.total_length_px)
    target = expected_tail_length_px(state.head_major_axis_length)
    preferred = minimum_preferred_length_px(state.head_major_axis_length)

    if length < preferred:
        score = 0.18 + 0.57 * np.clip(length / max(preferred, 1.0), 0.0, 1.0)
    elif length < target:
        score = 0.75 + 0.25 * (length - preferred) / max(target - preferred, 1.0)
    elif length <= min(520.0, 1.22 * target):
        score = 1.0
    elif length <= 540.0:
        score = max(0.0, 1.0 - (length - 1.22 * target) / max(540.0 - 1.22 * target, 1.0))
    else:
        score = 0.0

    # 若图上确实没有可继续的平滑信号，允许较短的“局部荧光表达”尾部保留。
    if (
        state.ended_reason == "no_valid_continuation"
        and length >= max(70.0, 2.8 * state.head_major_axis_length)
    ):
        score = max(float(score), 0.78)
    return float(np.clip(score, 0.0, 1.0))


def final_chain_score(state: PathState) -> float:
    signal = float(np.clip((state.mean_probability - 0.03) / 0.52, 0.0, 1.0))
    continuity = state.mean_continuity
    bridge_penalty = 0.10 * max(0, state.bridge_count - 1) + 0.04 * min(state.bridge_count, 1)
    sharp_penalty = 0.0
    if state.maximum_turn_angle_deg > 65.0:
        sharp_penalty = min(0.20, (state.maximum_turn_angle_deg - 65.0) / 100.0)
    score = (
        0.34 * state.start_score
        + 0.25 * continuity
        + 0.18 * signal
        + 0.18 * length_score(state)
        + 0.05 * min(1.0, len(state.edge_ids) / 4.0)
        - bridge_penalty
        - sharp_penalty
    )
    return float(np.clip(score, 0.0, 1.0))


def chain_status(state: PathState, score: float) -> str:
    high = (
        state.start_status == "strong_bidirectional"
        and score >= 0.76
        and 90.0 <= state.total_length_px <= 430.0
        and state.maximum_turn_angle_deg <= 62.0
        and state.bridge_count <= 1
        and state.mean_probability >= 0.14
    )
    review = (
        not high
        and score >= 0.58
        and 60.0 <= state.total_length_px <= 500.0
        and state.maximum_turn_angle_deg <= 82.0
        and state.bridge_count <= 2
    )
    return "high_confidence_chain" if high else (
        "review_chain" if review else "unresolved_chain"
    )


def search_chains_for_start(
    selection: Dict[str, Any],
    edge_by_id: Dict[int, Dict[str, Any]],
    adjacency: Dict[int, List[int]],
    directed_points: np.ndarray,
    directed_edges: Sequence[DirectedEdge],
    beam_width: int,
    top_k: int,
    maximum_edges: int,
    maximum_length_px: float,
    maximum_turn_angle_deg: float,
    maximum_bridge_gap_px: float,
    maximum_bridge_angle_deg: float,
) -> List[PathState]:
    start = directed_edge(
        edge_by_id,
        int(selection["edge_id"]),
        str(selection["side"]),
    )
    initial = PathState(
        head_id=int(selection["head_id"]),
        start_status=str(selection["status"]),
        start_score=float(selection.get("score") or 0.0),
        head_major_axis_length=float(selection.get("head_major_axis_length") or 24.0),
        edge_ids=(int(start.edge_id),),
        edge_sides=(str(start.side),),
        points_xy=np.asarray(start.points_xy, dtype=np.float32),
        transitions=tuple(),
        total_length_px=float(start.length_px),
        signal_weighted_sum=float(start.mean_probability * start.length_px),
        signal_length_sum=float(start.length_px),
        continuity_sum=0.0,
        transition_count=0,
        bridge_count=0,
        maximum_turn_angle_deg=0.0,
        cumulative_objective=float(selection.get("score") or 0.0),
    )

    active = [initial]
    completed: List[PathState] = []
    for _ in range(maximum_edges - 1):
        expanded: List[PathState] = []
        for state in active:
            if state.total_length_px >= maximum_length_px:
                state.ended_reason = "maximum_length"
                completed.append(state)
                continue
            transitions = find_transitions(
                state,
                edge_by_id,
                adjacency,
                directed_points,
                directed_edges,
                maximum_turn_angle_deg,
                maximum_bridge_gap_px,
                maximum_bridge_angle_deg,
                maximum_options=max(beam_width, 4),
            )
            if not transitions:
                state.ended_reason = "no_valid_continuation"
                completed.append(state)
                continue
            for transition in transitions:
                next_state = append_transition(state, transition)
                if next_state.total_length_px > maximum_length_px + 35.0:
                    next_state.ended_reason = "maximum_length"
                    completed.append(next_state)
                else:
                    expanded.append(next_state)
            # 只有在达到软目标，或后续连接明显变差时才允许主动终止。
            # 这避免原算法在85px后过早选择一条“干净但只有半截”的路径；
            # 若图上完全没有后续信号，上面的no_valid_continuation仍可自然停止。
            best_transition_score = max(
                (float(item.transition_score) for item in transitions),
                default=0.0,
            )
            preferred_length = minimum_preferred_length_px(
                state.head_major_axis_length
            )
            target_length = expected_tail_length_px(
                state.head_major_axis_length
            )
            allow_optional_stop = (
                state.total_length_px >= target_length
                or best_transition_score < 0.34
                or (
                    state.total_length_px >= preferred_length
                    and best_transition_score < 0.48
                )
            )
            if allow_optional_stop and state.total_length_px >= 70.0:
                stopped = PathState(**{**state.__dict__, "ended_reason": "optional_stop"})
                completed.append(stopped)

        if not expanded:
            break
        expanded.sort(
            key=lambda item: (
                final_chain_score(item),
                item.cumulative_objective / max(1, len(item.edge_ids)),
            ),
            reverse=True,
        )
        active = expanded[: max(1, int(beam_width))]

    completed.extend(active)
    unique: Dict[Tuple[int, ...], PathState] = {}
    for state in completed:
        key = tuple(state.edge_ids)
        existing = unique.get(key)
        if existing is None or final_chain_score(state) > final_chain_score(existing):
            unique[key] = state
    ranked = sorted(unique.values(), key=final_chain_score, reverse=True)
    return ranked[: max(1, int(top_k))]


def state_to_record(state: PathState, rank: int) -> Dict[str, Any]:
    score = final_chain_score(state)
    status = chain_status(state, score)
    sampled = sample_path(state.points_xy, maximum_points=600)
    return {
        "head_id": int(state.head_id),
        "rank": int(rank),
        "status": status,
        "chain_score": float(score),
        "start_status": str(state.start_status),
        "start_score": float(state.start_score),
        "head_major_axis_length": float(state.head_major_axis_length),
        "expected_tail_length_px": float(expected_tail_length_px(state.head_major_axis_length)),
        "minimum_preferred_length_px": float(minimum_preferred_length_px(state.head_major_axis_length)),
        "length_to_expected_ratio": float(state.total_length_px / max(expected_tail_length_px(state.head_major_axis_length), 1.0)),
        "edge_ids": [int(value) for value in state.edge_ids],
        "edge_sides": list(state.edge_sides),
        "edge_count": int(len(state.edge_ids)),
        "total_length_px": float(state.total_length_px),
        "mean_probability": float(state.mean_probability),
        "mean_continuity": float(state.mean_continuity),
        "maximum_turn_angle_deg": float(state.maximum_turn_angle_deg),
        "bridge_count": int(state.bridge_count),
        "ended_reason": str(state.ended_reason or "beam_end"),
        "transitions": list(state.transitions),
        "points_xy": np.round(sampled, 2).tolist(),
    }


def annotate_cross_head_conflicts(best_records: List[Dict[str, Any]]) -> None:
    edge_to_heads: Dict[int, List[int]] = {}
    for record in best_records:
        for edge_id in record["edge_ids"]:
            edge_to_heads.setdefault(int(edge_id), []).append(int(record["head_id"]))
    for record in best_records:
        conflicting_heads = set()
        shared_edges = []
        for edge_id in record["edge_ids"]:
            owners = set(edge_to_heads.get(int(edge_id), []))
            owners.discard(int(record["head_id"]))
            if owners:
                shared_edges.append(int(edge_id))
                conflicting_heads.update(owners)
        record["shared_edge_ids"] = shared_edges
        record["conflicting_head_ids"] = sorted(conflicting_heads)
        record["has_cross_head_conflict"] = bool(conflicting_heads)
        if conflicting_heads and record["status"] == "high_confidence_chain":
            record["status"] = "review_chain"
            record["status_adjustment_reason"] = "与其他头部的最佳候选共享图边，降级为复核。"


def make_overlay(
    base_rgb: np.ndarray,
    start_payload: Dict[str, Any],
    best_records: Sequence[Dict[str, Any]],
) -> np.ndarray:
    overlay = base_rgb.copy()

    # 使用第一阶段候选中的真实头部中心作辅助标记。
    centers: Dict[int, Tuple[int, int]] = {}
    for item in list(start_payload.get("candidates") or []):
        head_id = int(item["head_id"])
        centers.setdefault(
            head_id,
            (int(round(float(item["head_center_x"]))), int(round(float(item["head_center_y"])))),
        )
    for head_id, center in centers.items():
        cv2.circle(overlay, center, 4, (255, 70, 70), 1, lineType=cv2.LINE_AA)

    for record in best_records:
        status = str(record["status"])
        if status == "high_confidence_chain":
            color = (80, 220, 255)  # cyan
            thickness = 2
        elif status == "review_chain":
            color = (255, 220, 60)  # yellow
            thickness = 2
        else:
            color = (255, 90, 220)  # magenta
            thickness = 1
        points = np.round(np.asarray(record["points_xy"], dtype=np.float32)).astype(np.int32)
        if len(points) < 2:
            continue
        polyline = points.reshape(-1, 1, 2)
        cv2.polylines(overlay, [polyline], False, (8, 8, 8), thickness + 2, lineType=cv2.LINE_AA)
        cv2.polylines(overlay, [polyline], False, color, thickness, lineType=cv2.LINE_AA)
        cv2.circle(overlay, tuple(points[0]), 4, (8, 8, 8), -1, lineType=cv2.LINE_AA)
        cv2.circle(overlay, tuple(points[0]), 2, color, -1, lineType=cv2.LINE_AA)
        label_point = tuple((points[0] + np.asarray([5, -5])).tolist())
        cv2.putText(
            overlay,
            "H{} {:.2f}".format(int(record["head_id"]), float(record["chain_score"])),
            label_point,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.33,
            color,
            1,
            lineType=cv2.LINE_AA,
        )

    cv2.rectangle(overlay, (8, 8), (545, 94), (0, 0, 0), -1)
    cv2.putText(overlay, "Cyan: high-confidence chain candidate", (18, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (80, 220, 255), 1, cv2.LINE_AA)
    cv2.putText(overlay, "Yellow: review chain candidate", (18, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (255, 220, 60), 1, cv2.LINE_AA)
    cv2.putText(overlay, "Magenta: unresolved chain / Red: head center", (18, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (255, 90, 220), 1, cv2.LINE_AA)
    cv2.putText(overlay, "MVP only: not final tail labels", (18, 92), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (230, 230, 230), 1, cv2.LINE_AA)
    return overlay


def find_single(pattern: str, directory: Path) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError("目录 {} 中找不到 {}。".format(directory, pattern))
    return matches[0].resolve()


def resolve_task_inputs(task_root: Path, field_id: str, output_dir: Optional[Path]) -> Dict[str, Path]:
    root = task_root.resolve()
    field_root = root / "segmentation" / "tail" / field_id
    start_dir = root / "segmentation" / "tail_joint_mvp" / field_id
    return {
        "start_json": (start_dir / "joint_start_candidates.json").resolve(),
        "graph": (field_root / "stage1_2" / "tail_graph_stage1_2.json").resolve(),
        "probability": (field_root / "stage1" / "02_probability_uint16.tif").resolve(),
        "merge": find_single("{}_Merge.*".format(field_id), root / "input"),
        "output_dir": (output_dir or (root / "segmentation" / "tail_joint_chain_mvp" / field_id)).resolve(),
    }


def validate_file(name: str, path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError("{} 不存在：{}".format(name, path))


def run_mvp(
    *,
    start_json_path: Path,
    graph_path: Path,
    probability_path: Path,
    output_dir: Path,
    merge_path: Optional[Path] = None,
    include_review_starts: bool = True,
    beam_width: int = 8,
    top_k: int = 3,
    maximum_edges: int = 22,
    maximum_length_px: float = 500.0,
    maximum_turn_angle_deg: float = 78.0,
    maximum_bridge_gap_px: float = 16.0,
    maximum_bridge_angle_deg: float = 42.0,
) -> Dict[str, Any]:
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)

    start_payload, selections = load_start_selections(start_json_path, include_review_starts)
    edge_by_id, adjacency, directed_edges = load_graph(graph_path)
    directed_points, directed_edges = directed_endpoint_index(directed_edges)
    probability = normalize_probability(read_image(probability_path))
    if probability.ndim > 2:
        probability = np.squeeze(probability)
    if probability.ndim != 2:
        raise ValueError("概率图必须为二维图像。")

    all_records: List[Dict[str, Any]] = []
    best_records: List[Dict[str, Any]] = []
    per_head_summary: List[Dict[str, Any]] = []

    for selection in selections:
        states = search_chains_for_start(
            selection,
            edge_by_id,
            adjacency,
            directed_points,
            directed_edges,
            beam_width,
            top_k,
            maximum_edges,
            maximum_length_px,
            maximum_turn_angle_deg,
            maximum_bridge_gap_px,
            maximum_bridge_angle_deg,
        )
        records = [state_to_record(state, rank + 1) for rank, state in enumerate(states)]
        all_records.extend(records)
        if records:
            best_records.append(records[0])
            per_head_summary.append(
                {
                    "head_id": int(selection["head_id"]),
                    "start_status": str(selection["status"]),
                    "start_edge_id": int(selection["edge_id"]),
                    "start_side": str(selection["side"]),
                    "candidate_count": int(len(records)),
                    "best_status": str(records[0]["status"]),
                    "best_score": float(records[0]["chain_score"]),
                    "best_length_px": float(records[0]["total_length_px"]),
                    "best_edge_count": int(records[0]["edge_count"]),
                }
            )

    annotate_cross_head_conflicts(best_records)
    best_by_head = {int(item["head_id"]): item for item in best_records}
    for item in per_head_summary:
        best = best_by_head.get(int(item["head_id"]))
        if best is not None:
            item["best_status"] = str(best["status"])
            item["has_cross_head_conflict"] = bool(best.get("has_cross_head_conflict"))
            item["conflicting_head_ids"] = list(best.get("conflicting_head_ids") or [])

    # 同步全候选里的 rank-1 记录冲突状态。
    for item in all_records:
        if int(item["rank"]) == 1 and int(item["head_id"]) in best_by_head:
            item.update({
                key: value
                for key, value in best_by_head[int(item["head_id"])].items()
                if key in {
                    "status", "shared_edge_ids", "conflicting_head_ids",
                    "has_cross_head_conflict", "status_adjustment_reason",
                }
            })

    if merge_path is not None:
        base_rgb = to_uint8_rgb(read_image(merge_path))
    else:
        base_rgb = to_uint8_rgb(probability)
    if base_rgb.shape[:2] != probability.shape:
        raise ValueError("背景图与概率图尺寸不一致。")
    overlay = make_overlay(base_rgb, start_payload, best_records)
    overlay_path = output_dir / OUTPUT_OVERLAY_NAME
    Image.fromarray(overlay).save(overlay_path)

    status_counts: Dict[str, int] = {}
    conflict_count = 0
    for record in best_records:
        status = str(record["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
        conflict_count += int(bool(record.get("has_cross_head_conflict")))

    payload: Dict[str, Any] = {
        "schema_version": "tail_joint_chain_candidate_mvp_v1",
        "purpose": "双向起始候选条件下的无分叉尾部链候选；不是最终尾部标签，不得进入测量。",
        "sources": {
            "start_candidates": str(start_json_path.resolve()),
            "graph": str(graph_path.resolve()),
            "probability": str(probability_path.resolve()),
            "merge": None if merge_path is None else str(merge_path.resolve()),
        },
        "parameters": {
            "include_review_starts": bool(include_review_starts),
            "beam_width": int(beam_width),
            "length_prior": "head_major_axis_soft_target_v1",
            "top_k": int(top_k),
            "maximum_edges": int(maximum_edges),
            "maximum_length_px": float(maximum_length_px),
            "maximum_turn_angle_deg": float(maximum_turn_angle_deg),
            "maximum_bridge_gap_px": float(maximum_bridge_gap_px),
            "maximum_bridge_angle_deg": float(maximum_bridge_angle_deg),
            "high_confidence_score_threshold": 0.76,
            "review_score_threshold": 0.58,
        },
        "summary": {
            "eligible_start_count": int(len(selections)),
            "head_with_chain_count": int(len(best_records)),
            "all_chain_candidate_count": int(len(all_records)),
            "best_status_counts": status_counts,
            "cross_head_conflict_count": int(conflict_count),
            "elapsed_seconds": float(time.perf_counter() - started),
        },
        "per_head": per_head_summary,
        "best_chains": best_records,
        "chain_candidates": all_records,
        "outputs": {
            "json": str((output_dir / OUTPUT_JSON_NAME).resolve()),
            "overlay": str(overlay_path.resolve()),
        },
    }
    json_path = output_dir / OUTPUT_JSON_NAME
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="双向头尾联合尾部链候选 MVP")
    parser.add_argument("--task-root", help="Analysis V2 单次运行目录")
    parser.add_argument("--field-id", help="例如 ZBFY022-C-1_RGB")
    parser.add_argument("--start-json")
    parser.add_argument("--graph")
    parser.add_argument("--probability")
    parser.add_argument("--merge")
    parser.add_argument("--output-dir")
    parser.add_argument("--strong-starts-only", action="store_true")
    parser.add_argument("--beam-width", type=int, default=8)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--maximum-edges", type=int, default=22)
    parser.add_argument("--maximum-length-px", type=float, default=500.0)
    parser.add_argument("--maximum-turn-angle-deg", type=float, default=78.0)
    parser.add_argument("--maximum-bridge-gap-px", type=float, default=16.0)
    parser.add_argument("--maximum-bridge-angle-deg", type=float, default=42.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.task_root:
        if not args.field_id:
            raise SystemExit("使用 --task-root 时必须同时提供 --field-id。")
        resolved = resolve_task_inputs(
            Path(args.task_root),
            str(args.field_id),
            None if not args.output_dir else Path(args.output_dir),
        )
        start_json = resolved["start_json"]
        graph = resolved["graph"]
        probability = resolved["probability"]
        merge = resolved["merge"]
        output_dir = resolved["output_dir"]
    else:
        if not args.start_json or not args.graph or not args.probability or not args.output_dir:
            raise SystemExit("显式模式需要 --start-json、--graph、--probability、--output-dir。")
        start_json = Path(args.start_json).resolve()
        graph = Path(args.graph).resolve()
        probability = Path(args.probability).resolve()
        merge = None if not args.merge else Path(args.merge).resolve()
        output_dir = Path(args.output_dir).resolve()

    validate_file("start_json", start_json)
    validate_file("graph", graph)
    validate_file("probability", probability)
    if merge is not None:
        validate_file("merge", merge)

    payload = run_mvp(
        start_json_path=start_json,
        graph_path=graph,
        probability_path=probability,
        output_dir=output_dir,
        merge_path=merge,
        include_review_starts=not bool(args.strong_starts_only),
        beam_width=args.beam_width,
        top_k=args.top_k,
        maximum_edges=args.maximum_edges,
        maximum_length_px=args.maximum_length_px,
        maximum_turn_angle_deg=args.maximum_turn_angle_deg,
        maximum_bridge_gap_px=args.maximum_bridge_gap_px,
        maximum_bridge_angle_deg=args.maximum_bridge_angle_deg,
    )
    print("双向尾部链候选 MVP 完成。")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print("输出：{}".format(output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
