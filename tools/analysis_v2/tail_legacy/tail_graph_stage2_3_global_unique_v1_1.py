#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stage 2.3：完整尾部候选的全局唯一分配

输入：
- Stage 2.2 的 path_results.json
- Stage 1.2 的 tail_graph_stage1_2.json
- Merge 图

作用：
1. 不再对共享路径的所有头部一律降级；
2. 在每个头部最多若干条完整候选中进行全局优化；
3. 每个头部最多选择一条路径；
4. 每条骨架边最多分配给一个头部；
5. 自动选择冲突组件中的最优组合；
6. 选择第二/第三候选解决冲突时，强制保留为待复核；
7. 无可用唯一候选时标记为未分配，不伪造结果。

本阶段不会修改 Stage 2.2 原始结果。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    import cv2
    import numpy as np
    from PIL import Image
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_matrix
except ImportError as exc:
    print("缺少依赖：", exc)
    raise SystemExit(1) from exc


def read_image(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image)


def resolve_required(
    supplied: str,
) -> Path:
    path = Path(supplied).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"找不到文件：{path}")
    return path


def robust_normalize(
    image: np.ndarray,
    low_p: float = 0.2,
    high_p: float = 99.8,
) -> np.ndarray:
    array = image.astype(np.float32, copy=False)
    low, high = np.percentile(array, [low_p, high_p])
    if high <= low:
        return np.zeros_like(array, dtype=np.float32)
    return np.clip(
        (array - low) / (high - low),
        0.0,
        1.0,
    )


def to_uint8_rgb(
    image: np.ndarray,
) -> np.ndarray:
    if image.ndim == 2:
        gray = np.round(
            robust_normalize(image) * 255.0
        ).astype(np.uint8)
        return np.repeat(gray[..., None], 3, axis=2)

    if image.ndim == 3 and image.shape[2] >= 3:
        rgb = np.zeros(
            image.shape[:2] + (3,),
            dtype=np.uint8,
        )
        for channel in range(3):
            rgb[..., channel] = np.round(
                robust_normalize(image[..., channel]) * 255.0
            ).astype(np.uint8)
        return rgb

    raise ValueError(
        f"无法转换图像维度：{image.shape}"
    )


def parse_edge_lengths(
    graph_payload: dict[str, Any],
) -> dict[int, float]:
    return {
        int(edge["edge_id"]): float(edge.get("length_px", 0.0))
        for edge in graph_payload.get("edges", [])
    }


def candidate_utility(
    candidate: dict[str, Any],
    *,
    entry_score: float,
    path_margin: float | None,
    intrinsic_review_reason_count: int,
    rank_penalty: float,
    virtual_bridge_penalty: float,
    non_simple_penalty: float,
    not_endpoint_penalty: float,
    margin_bonus_weight: float,
    small_margin_penalty: float,
    clean_path_bonus: float,
    intrinsic_reason_penalty: float,
) -> float:
    rank = int(candidate.get("rank", 999))
    final_score = float(candidate.get("final_score", 0.0))
    signal_score = float(candidate.get("signal_score", 0.0))
    continuity_score = float(candidate.get("continuity_score", 0.0))
    length_score = float(candidate.get("length_score", 0.0))
    endpoint_score = float(candidate.get("endpoint_score", 0.0))

    utility = (
        0.68 * final_score
        + 0.10 * signal_score
        + 0.08 * continuity_score
        + 0.07 * length_score
        + 0.04 * endpoint_score
        + 0.03 * float(np.clip(entry_score, 0.0, 1.0))
    )

    utility -= max(0, rank - 1) * float(rank_penalty)

    # 第一候选的“领先幅度”比单纯绝对分数更能反映是否稳定。
    # 已知H22/H37冲突中，H22的路径分差很大，而H37几乎并列。
    if rank == 1 and path_margin is not None:
        normalized_margin = float(
            np.clip(path_margin / 0.20, 0.0, 1.0)
        )
        utility += float(margin_bonus_weight) * normalized_margin
        if path_margin < 0.03:
            utility -= float(small_margin_penalty)

    # shared_path_edge属于全局分配要解决的冲突，不应惩罚路径本身；
    # 其他复核原因才表示该路径自身存在风险。
    if intrinsic_review_reason_count == 0:
        utility += float(clean_path_bonus)
    else:
        utility -= (
            float(intrinsic_reason_penalty)
            * float(intrinsic_review_reason_count)
        )

    if not bool(candidate.get("reached_endpoint", False)):
        utility -= float(not_endpoint_penalty)

    if bool(candidate.get("contains_virtual_junction_bridge", False)):
        utility -= float(virtual_bridge_penalty)

    if bool(candidate.get("contains_non_simple_edge", False)):
        utility -= float(non_simple_penalty)

    length_px = float(candidate.get("length_px", 0.0))
    if length_px < 100.0:
        utility -= 0.25
    elif length_px < 160.0:
        utility -= 0.10

    if length_px > 520.0:
        utility -= 0.25
    elif length_px > 450.0:
        utility -= 0.10

    return float(utility)


def build_variables(
    results: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[
    list[dict[str, Any]],
    dict[int, list[int]],
    dict[int, int],
]:
    variables: list[dict[str, Any]] = []
    variables_by_head: dict[int, list[int]] = defaultdict(list)
    unmatched_variable_by_head: dict[int, int] = {}

    for result in results:
        head_id = int(result["head_id"])
        entry_score = float(result.get("entry_score") or 0.0)
        candidates = list(result.get("candidates", []))

        intrinsic_review_reasons = [
            str(reason)
            for reason in result.get("review_reasons", [])
            if str(reason) != "shared_path_edge"
        ]

        rank1_margin: float | None = None
        if len(candidates) >= 2:
            ordered_scores = sorted(
                (
                    float(candidate.get("final_score", 0.0))
                    for candidate in candidates
                ),
                reverse=True,
            )
            rank1_margin = float(
                ordered_scores[0] - ordered_scores[1]
            )

        for candidate in candidates:
            edge_ids = tuple(
                sorted(
                    {
                        int(edge_id)
                        for edge_id in candidate.get("edge_ids", [])
                    }
                )
            )
            if not edge_ids:
                continue

            utility = candidate_utility(
                candidate,
                entry_score=entry_score,
                path_margin=(
                    rank1_margin
                    if int(candidate.get("rank", 999)) == 1
                    else None
                ),
                intrinsic_review_reason_count=len(
                    intrinsic_review_reasons
                ),
                rank_penalty=float(args.rank_penalty),
                virtual_bridge_penalty=float(
                    args.virtual_bridge_penalty
                ),
                non_simple_penalty=float(
                    args.non_simple_penalty
                ),
                not_endpoint_penalty=float(
                    args.not_endpoint_penalty
                ),
                margin_bonus_weight=float(
                    args.margin_bonus_weight
                ),
                small_margin_penalty=float(
                    args.small_margin_penalty
                ),
                clean_path_bonus=float(
                    args.clean_path_bonus
                ),
                intrinsic_reason_penalty=float(
                    args.intrinsic_reason_penalty
                ),
            )

            variable_index = len(variables)
            variables.append(
                {
                    "kind": "candidate",
                    "head_id": head_id,
                    "candidate": candidate,
                    "edge_ids": edge_ids,
                    "utility": utility,
                }
            )
            variables_by_head[head_id].append(variable_index)

        unmatched_index = len(variables)
        variables.append(
            {
                "kind": "unmatched",
                "head_id": head_id,
                "candidate": None,
                "edge_ids": tuple(),
                "utility": float(args.unmatched_utility),
            }
        )
        variables_by_head[head_id].append(unmatched_index)
        unmatched_variable_by_head[head_id] = unmatched_index

    return (
        variables,
        variables_by_head,
        unmatched_variable_by_head,
    )


def solve_global_assignment(
    variables: list[dict[str, Any]],
    variables_by_head: dict[int, list[int]],
    edge_lengths: dict[int, float],
    *,
    time_limit: float,
    shareable_short_edge_max_length: float,
    shareable_short_edge_cap: int,
) -> tuple[set[int], str]:
    variable_count = len(variables)
    edge_to_variables: dict[int, list[int]] = defaultdict(list)

    for variable_index, variable in enumerate(variables):
        for edge_id in variable["edge_ids"]:
            edge_to_variables[int(edge_id)].append(variable_index)

    row_indices: list[int] = []
    column_indices: list[int] = []
    data: list[float] = []
    lower_bounds: list[float] = []
    upper_bounds: list[float] = []

    row = 0

    # 每个头部必须且只能选择：一条候选路径或“未分配”。
    for head_id in sorted(variables_by_head):
        for variable_index in variables_by_head[head_id]:
            row_indices.append(row)
            column_indices.append(variable_index)
            data.append(1.0)
        lower_bounds.append(1.0)
        upper_bounds.append(1.0)
        row += 1

    # 长边必须唯一。极短边通常是构图时压缩出来的交叉区域，
    # 最多允许两个头部共享，但后续会强制标记为待复核。
    for edge_id in sorted(edge_to_variables):
        indices = edge_to_variables[edge_id]
        if len(indices) <= 1:
            continue

        edge_length = float(edge_lengths.get(int(edge_id), 0.0))
        if edge_length <= float(shareable_short_edge_max_length):
            occupancy_cap = max(
                1,
                int(shareable_short_edge_cap),
            )
        else:
            occupancy_cap = 1

        for variable_index in indices:
            row_indices.append(row)
            column_indices.append(variable_index)
            data.append(1.0)

        lower_bounds.append(-np.inf)
        upper_bounds.append(float(occupancy_cap))
        row += 1

    matrix = coo_matrix(
        (
            np.asarray(data, dtype=np.float64),
            (
                np.asarray(row_indices, dtype=np.int32),
                np.asarray(column_indices, dtype=np.int32),
            ),
        ),
        shape=(row, variable_count),
    ).tocsr()

    objective = -np.asarray(
        [variable["utility"] for variable in variables],
        dtype=np.float64,
    )

    result = milp(
        c=objective,
        integrality=np.ones(variable_count, dtype=np.int8),
        bounds=Bounds(
            np.zeros(variable_count, dtype=np.float64),
            np.ones(variable_count, dtype=np.float64),
        ),
        constraints=LinearConstraint(
            matrix,
            np.asarray(lower_bounds, dtype=np.float64),
            np.asarray(upper_bounds, dtype=np.float64),
        ),
        options={
            "time_limit": float(time_limit),
            "mip_rel_gap": 0.0,
            "presolve": True,
        },
    )

    if result.x is None:
        raise RuntimeError(
            f"全局优化失败：status={result.status}, "
            f"message={result.message}"
        )

    selected = {
        int(index)
        for index, value in enumerate(result.x)
        if float(value) >= 0.5
    }

    solver_status = (
        f"status={result.status};"
        f"success={result.success};"
        f"message={result.message}"
    )
    return selected, solver_status


def quality_reasons(
    candidate: dict[str, Any],
    *,
    selected_rank: int,
    local_margin: float | None,
    args: argparse.Namespace,
) -> list[str]:
    reasons: list[str] = []

    if not bool(candidate.get("reached_endpoint", False)):
        reasons.append("not_reached_endpoint")

    length_px = float(candidate.get("length_px", 0.0))
    if length_px < float(args.auto_min_length):
        reasons.append("path_too_short")
    if length_px > float(args.auto_max_length):
        reasons.append("path_too_long")

    if (
        float(candidate.get("mean_probability", 0.0))
        < float(args.auto_min_mean_probability)
    ):
        reasons.append("mean_probability_low")

    if (
        float(candidate.get("low_probability_fraction", 1.0))
        > float(args.auto_max_low_probability_fraction)
    ):
        reasons.append("low_probability_fraction_high")

    if (
        float(candidate.get("mean_transition_angle_deg", 180.0))
        > float(args.auto_max_mean_transition_angle)
    ):
        reasons.append("mean_transition_angle_large")

    if (
        float(candidate.get("max_transition_angle_deg", 180.0))
        > float(args.auto_max_transition_angle)
    ):
        reasons.append("max_transition_angle_large")

    if (
        float(candidate.get("final_score", 0.0))
        < float(args.auto_min_final_score)
    ):
        reasons.append("final_score_low")

    if (
        local_margin is not None
        and local_margin < float(args.auto_min_path_margin)
    ):
        reasons.append("path_margin_small")

    if bool(candidate.get("contains_non_simple_edge", False)):
        reasons.append("contains_non_simple_edge")

    if bool(candidate.get("contains_virtual_junction_bridge", False)):
        reasons.append("virtual_junction_bridge_used")

    if int(selected_rank) != 1:
        reasons.append("global_alternative_candidate_selected")

    return reasons


def local_margin_for_candidate(
    selected_candidate: dict[str, Any],
    all_candidates: list[dict[str, Any]],
) -> float | None:
    selected_score = float(selected_candidate.get("final_score", 0.0))
    alternative_scores = [
        float(candidate.get("final_score", 0.0))
        for candidate in all_candidates
        if candidate is not selected_candidate
    ]
    if not alternative_scores:
        return None
    return float(
        selected_score - max(alternative_scores)
    )


def edge_conflict_candidates(
    results: list[dict[str, Any]],
) -> dict[int, set[int]]:
    edge_heads: dict[int, set[int]] = defaultdict(set)

    for result in results:
        head_id = int(result["head_id"])
        for candidate in result.get("candidates", []):
            for edge_id in candidate.get("edge_ids", []):
                edge_heads[int(edge_id)].add(head_id)

    return edge_heads


def selected_edge_occupancy(
    variables: list[dict[str, Any]],
    selected_variable_indices: set[int],
) -> dict[int, set[int]]:
    occupancy: dict[int, set[int]] = defaultdict(set)

    for variable_index in selected_variable_indices:
        variable = variables[variable_index]
        if variable["kind"] != "candidate":
            continue

        head_id = int(variable["head_id"])
        for edge_id in variable["edge_ids"]:
            occupancy[int(edge_id)].add(head_id)

    return occupancy


def build_selection_results(
    *,
    original_results: list[dict[str, Any]],
    variables: list[dict[str, Any]],
    selected_variable_indices: set[int],
    edge_lengths: dict[int, float],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    original_by_head = {
        int(result["head_id"]): result
        for result in original_results
    }

    selected_by_head: dict[int, dict[str, Any]] = {}
    for variable_index in selected_variable_indices:
        variable = variables[variable_index]
        selected_by_head[int(variable["head_id"])] = variable

    edge_heads = edge_conflict_candidates(original_results)
    selected_occupancy = selected_edge_occupancy(
        variables,
        selected_variable_indices,
    )
    output: list[dict[str, Any]] = []

    for head_id in sorted(original_by_head):
        original = original_by_head[head_id]
        variable = selected_by_head[head_id]

        base = {
            "head_id": head_id,
            "center_x": float(original.get("center_x", 0.0)),
            "center_y": float(original.get("center_y", 0.0)),
            "s1_x": float(original.get("s1_x", 0.0)),
            "s1_y": float(original.get("s1_y", 0.0)),
            "entry_edge_id": original.get("entry_edge_id"),
            "entry_status": original.get("entry_status", ""),
            "entry_score": original.get("entry_score"),
            "original_status": original.get("status", ""),
            "original_review_reasons": list(
                original.get("review_reasons", [])
            ),
            "original_best_edge_ids": (
                list(original.get("candidates", [{}])[0].get("edge_ids", []))
                if original.get("candidates")
                else []
            ),
            "original_shared_edge_conflict_heads": list(
                original.get("shared_edge_conflict_heads", [])
            ),
        }

        if variable["kind"] == "unmatched":
            output.append(
                {
                    **base,
                    "status": "global_unassigned",
                    "review_reasons": [
                        "no_conflict_free_candidate_selected"
                    ],
                    "selected_rank": None,
                    "selected_utility": float(variable["utility"]),
                    "selected_candidate": None,
                    "local_margin": None,
                    "candidate_pool_size": len(
                        original.get("candidates", [])
                    ),
                    "competing_heads_on_selected_edges": [],
                    "shared_micro_edge_ids": [],
                }
            )
            continue

        candidate = variable["candidate"]
        selected_rank = int(candidate.get("rank", 999))
        local_margin = local_margin_for_candidate(
            candidate,
            original.get("candidates", []),
        )

        reasons = quality_reasons(
            candidate,
            selected_rank=selected_rank,
            local_margin=local_margin,
            args=args,
        )

        shared_micro_edge_ids = [
            int(edge_id)
            for edge_id in candidate.get("edge_ids", [])
            if (
                len(
                    selected_occupancy.get(
                        int(edge_id),
                        set(),
                    )
                ) > 1
                and float(
                    edge_lengths.get(
                        int(edge_id),
                        0.0,
                    )
                )
                <= float(
                    args.shareable_short_edge_max_length
                )
            )
        ]
        if shared_micro_edge_ids:
            reasons.append("shared_micro_edge")

        status = (
            "auto_confirmed_unique"
            if not reasons
            else "review_required_unique"
        )

        competing_heads: set[int] = set()
        for edge_id in candidate.get("edge_ids", []):
            competing_heads.update(
                edge_heads.get(int(edge_id), set())
            )
        competing_heads.discard(head_id)

        output.append(
            {
                **base,
                "status": status,
                "review_reasons": reasons,
                "selected_rank": selected_rank,
                "selected_utility": float(variable["utility"]),
                "selected_candidate": candidate,
                "local_margin": local_margin,
                "candidate_pool_size": len(
                    original.get("candidates", [])
                ),
                "competing_heads_on_selected_edges": sorted(
                    competing_heads
                ),
                "shared_micro_edge_ids": sorted(
                    shared_micro_edge_ids
                ),
            }
        )

    return output


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


def color_for_status(
    status: str,
) -> tuple[int, int, int]:
    if status == "auto_confirmed_unique":
        return (0, 255, 0)
    if status == "review_required_unique":
        return (255, 255, 0)
    return (255, 0, 0)


def draw_selection_overlay(
    merge_rgb: np.ndarray,
    results: list[dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray]:
    selected_overlay = merge_rgb.copy()
    comparison_overlay = merge_rgb.copy()

    for result in results:
        head_id = int(result["head_id"])
        status = str(result["status"])
        color = color_for_status(status)
        center = (
            float(result["center_x"]),
            float(result["center_y"]),
        )

        selected = result.get("selected_candidate")
        if selected is None:
            cv2.drawMarker(
                selected_overlay,
                tuple(np.rint(center).astype(int)),
                color,
                markerType=cv2.MARKER_TILTED_CROSS,
                markerSize=16,
                thickness=2,
                line_type=cv2.LINE_AA,
            )
            cv2.putText(
                selected_overlay,
                f"H{head_id}",
                (
                    int(round(center[0])) + 6,
                    int(round(center[1])) - 6,
                ),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                color,
                1,
                lineType=cv2.LINE_AA,
            )
            continue

        points = np.asarray(
            selected.get("points_xy", []),
            dtype=np.float32,
        )
        if len(points) < 2:
            continue

        dotted_line(
            selected_overlay,
            center,
            tuple(points[0]),
            color,
            thickness=1,
        )
        cv2.polylines(
            selected_overlay,
            [np.rint(points).astype(np.int32).reshape(-1, 1, 2)],
            False,
            color,
            2,
            lineType=cv2.LINE_AA,
        )
        cv2.putText(
            selected_overlay,
            f"H{head_id}/R{result['selected_rank']}",
            (
                int(round(points[len(points) // 2, 0])),
                int(round(points[len(points) // 2, 1])),
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.40,
            color,
            1,
            lineType=cv2.LINE_AA,
        )

        # 对比图：全局选择结果为实线，原始第一候选为青色虚线。
        dotted_line(
            comparison_overlay,
            center,
            tuple(points[0]),
            color,
            thickness=1,
        )
        cv2.polylines(
            comparison_overlay,
            [np.rint(points).astype(np.int32).reshape(-1, 1, 2)],
            False,
            color,
            2,
            lineType=cv2.LINE_AA,
        )

        original_candidates = result.get("original_best_edge_ids", [])
        selected_edges = selected.get("edge_ids", [])
        if (
            result.get("selected_rank") != 1
            and original_candidates
            and original_candidates != selected_edges
        ):
            # path_results 中未单独重复保存原始点；这里只用标签标明改选。
            cv2.putText(
                comparison_overlay,
                f"H{head_id}: R1->R{result['selected_rank']}",
                (
                    int(round(points[0, 0])) + 6,
                    int(round(points[0, 1])) - 6,
                ),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (0, 255, 255),
                1,
                lineType=cv2.LINE_AA,
            )

    return selected_overlay, comparison_overlay


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
) -> None:
    fieldnames = [
        "head_id",
        "status",
        "review_reasons",
        "original_status",
        "original_review_reasons",
        "entry_status",
        "entry_edge_id",
        "entry_score",
        "selected_rank",
        "selected_utility",
        "selected_edge_ids",
        "selected_length_px",
        "selected_mean_probability",
        "selected_low_probability_fraction",
        "selected_mean_transition_angle_deg",
        "selected_max_transition_angle_deg",
        "selected_final_score",
        "selected_reached_endpoint",
        "selected_virtual_bridge",
        "local_margin",
        "candidate_pool_size",
        "competing_heads_on_selected_edges",
        "shared_micro_edge_ids",
        "original_shared_edge_conflict_heads",
    ]

    prepared: list[dict[str, Any]] = []
    for result in rows:
        candidate = result.get("selected_candidate")
        prepared.append(
            {
                "head_id": result["head_id"],
                "status": result["status"],
                "review_reasons": "|".join(
                    result.get("review_reasons", [])
                ),
                "original_status": result.get("original_status", ""),
                "original_review_reasons": "|".join(
                    result.get("original_review_reasons", [])
                ),
                "entry_status": result.get("entry_status", ""),
                "entry_edge_id": result.get("entry_edge_id"),
                "entry_score": result.get("entry_score"),
                "selected_rank": result.get("selected_rank"),
                "selected_utility": result.get("selected_utility"),
                "selected_edge_ids": (
                    ",".join(
                        str(value)
                        for value in candidate.get("edge_ids", [])
                    )
                    if candidate
                    else ""
                ),
                "selected_length_px": (
                    candidate.get("length_px")
                    if candidate
                    else None
                ),
                "selected_mean_probability": (
                    candidate.get("mean_probability")
                    if candidate
                    else None
                ),
                "selected_low_probability_fraction": (
                    candidate.get("low_probability_fraction")
                    if candidate
                    else None
                ),
                "selected_mean_transition_angle_deg": (
                    candidate.get("mean_transition_angle_deg")
                    if candidate
                    else None
                ),
                "selected_max_transition_angle_deg": (
                    candidate.get("max_transition_angle_deg")
                    if candidate
                    else None
                ),
                "selected_final_score": (
                    candidate.get("final_score")
                    if candidate
                    else None
                ),
                "selected_reached_endpoint": (
                    candidate.get("reached_endpoint")
                    if candidate
                    else False
                ),
                "selected_virtual_bridge": (
                    candidate.get(
                        "contains_virtual_junction_bridge",
                        False,
                    )
                    if candidate
                    else False
                ),
                "local_margin": result.get("local_margin"),
                "candidate_pool_size": result.get(
                    "candidate_pool_size"
                ),
                "competing_heads_on_selected_edges": ",".join(
                    str(value)
                    for value in result.get(
                        "competing_heads_on_selected_edges",
                        [],
                    )
                ),
                "shared_micro_edge_ids": ",".join(
                    str(value)
                    for value in result.get(
                        "shared_micro_edge_ids",
                        [],
                    )
                ),
                "original_shared_edge_conflict_heads": ",".join(
                    str(value)
                    for value in result.get(
                        "original_shared_edge_conflict_heads",
                        [],
                    )
                ),
            }
        )

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        writer.writerows(prepared)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 2.3：完整尾部候选全局唯一分配"
        )
    )
    parser.add_argument(
        "--paths",
        required=True,
        help="Stage 2.2 path_results.json",
    )
    parser.add_argument(
        "--graph",
        required=True,
        help="Stage 1.2 tail_graph_stage1_2.json",
    )
    parser.add_argument(
        "--merge",
        required=True,
        help="Merge图",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
    )

    parser.add_argument(
        "--time-limit",
        type=float,
        default=60.0,
    )
    parser.add_argument(
        "--unmatched-utility",
        type=float,
        default=0.18,
    )
    parser.add_argument(
        "--rank-penalty",
        type=float,
        default=0.035,
    )
    parser.add_argument(
        "--virtual-bridge-penalty",
        type=float,
        default=0.08,
    )
    parser.add_argument(
        "--non-simple-penalty",
        type=float,
        default=0.10,
    )
    parser.add_argument(
        "--not-endpoint-penalty",
        type=float,
        default=0.25,
    )
    parser.add_argument(
        "--margin-bonus-weight",
        type=float,
        default=0.16,
        help="第一候选领先第二候选时的稳定性奖励",
    )
    parser.add_argument(
        "--small-margin-penalty",
        type=float,
        default=0.08,
        help="第一、第二候选几乎并列时的额外惩罚",
    )
    parser.add_argument(
        "--clean-path-bonus",
        type=float,
        default=0.08,
        help="除共享边外没有其他复核原因时的奖励",
    )
    parser.add_argument(
        "--intrinsic-reason-penalty",
        type=float,
        default=0.04,
        help="每个路径自身复核原因的惩罚",
    )
    parser.add_argument(
        "--shareable-short-edge-max-length",
        type=float,
        default=8.0,
        help=(
            "长度不超过该值的交叉微边可由最多两个头部共享"
        ),
    )
    parser.add_argument(
        "--shareable-short-edge-cap",
        type=int,
        default=2,
        help="交叉微边允许的最大占用数",
    )

    # 与Stage 2.2保持一致的自动确认安全门槛。
    parser.add_argument(
        "--auto-min-length",
        type=float,
        default=220.0,
    )
    parser.add_argument(
        "--auto-max-length",
        type=float,
        default=420.0,
    )
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


def main() -> int:
    started = time.perf_counter()
    args = build_parser().parse_args()

    paths_path = resolve_required(args.paths)
    graph_path = resolve_required(args.graph)
    merge_path = resolve_required(args.merge)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    path_payload = json.loads(
        paths_path.read_text(encoding="utf-8")
    )
    graph_payload = json.loads(
        graph_path.read_text(encoding="utf-8")
    )
    merge_rgb = to_uint8_rgb(
        read_image(merge_path)
    )

    original_results = path_payload.get("results", [])
    if not original_results:
        raise ValueError(
            "path_results.json中没有results。"
        )

    edge_lengths = parse_edge_lengths(graph_payload)
    print(
        f"头部结果={len(original_results)}，"
        f"图边={len(edge_lengths)}"
    )

    (
        variables,
        variables_by_head,
        _,
    ) = build_variables(
        original_results,
        args,
    )

    candidate_variable_count = sum(
        variable["kind"] == "candidate"
        for variable in variables
    )
    print(
        f"候选变量={candidate_variable_count}，"
        f"未分配变量={len(original_results)}"
    )

    selected_indices, solver_status = solve_global_assignment(
        variables,
        variables_by_head,
        edge_lengths,
        time_limit=float(args.time_limit),
        shareable_short_edge_max_length=float(
            args.shareable_short_edge_max_length
        ),
        shareable_short_edge_cap=int(
            args.shareable_short_edge_cap
        ),
    )
    print("求解器：", solver_status)

    selection_results = build_selection_results(
        original_results=original_results,
        variables=variables,
        selected_variable_indices=selected_indices,
        edge_lengths=edge_lengths,
        args=args,
    )

    selected_overlay, comparison_overlay = draw_selection_overlay(
        merge_rgb,
        selection_results,
    )

    Image.fromarray(selected_overlay).save(
        output_dir
        / "01_global_unique_paths_overlay.png"
    )
    Image.fromarray(comparison_overlay).save(
        output_dir
        / "02_global_selection_changes_overlay.png"
    )

    centerlines = np.zeros(
        merge_rgb.shape[:2],
        dtype=np.uint16,
    )
    for result in selection_results:
        candidate = result.get("selected_candidate")
        if candidate is None:
            continue
        points = np.asarray(
            candidate.get("points_xy", []),
            dtype=np.float32,
        )
        if len(points) < 2:
            continue
        points_int = np.rint(points).astype(np.int32)
        cv2.polylines(
            centerlines,
            [points_int.reshape(-1, 1, 2)],
            False,
            65535,
            1,
            lineType=cv2.LINE_8,
        )

    Image.fromarray(centerlines).save(
        output_dir
        / "03_global_unique_centerlines_uint16.tif"
    )

    write_csv(
        output_dir
        / "global_selection_summary.csv",
        selection_results,
    )

    status_counts = {
        status: int(
            sum(
                result["status"] == status
                for result in selection_results
            )
        )
        for status in (
            "auto_confirmed_unique",
            "review_required_unique",
            "global_unassigned",
        )
    }

    selected_rank_counts: dict[str, int] = defaultdict(int)
    for result in selection_results:
        rank = result.get("selected_rank")
        selected_rank_counts[
            "unassigned" if rank is None else f"rank_{rank}"
        ] += 1

    output_payload = {
        "version": (
            "tail_graph_stage2_3_global_unique_assignment_v1_1"
        ),
        "source_paths": str(paths_path),
        "source_graph": str(graph_path),
        "solver_status": solver_status,
        "parameters": vars(args),
        "status_counts": status_counts,
        "selected_rank_counts": dict(
            selected_rank_counts
        ),
        "results": selection_results,
    }
    (
        output_dir
        / "global_selection_results.json"
    ).write_text(
        json.dumps(
            output_payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    elapsed = time.perf_counter() - started
    print("\nStage 2.3完成。")
    print(
        f"auto_confirmed_unique="
        f"{status_counts['auto_confirmed_unique']}，"
        f"review_required_unique="
        f"{status_counts['review_required_unique']}，"
        f"global_unassigned="
        f"{status_counts['global_unassigned']}"
    )
    print(
        "选择排名：",
        dict(selected_rank_counts),
    )
    print(f"总耗时：{elapsed:.2f}s")
    print(
        "请上传：\n"
        "01_global_unique_paths_overlay.png\n"
        "global_selection_summary.csv\n"
        "global_selection_results.json"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
