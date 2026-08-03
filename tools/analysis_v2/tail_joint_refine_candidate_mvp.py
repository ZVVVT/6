#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双向头尾联合候选的证据精炼 MVP（第三阶段）。

本脚本只评估现有最佳尾部中心线候选，不生成最终尾部区域，不进入测量。
固定输出：
- joint_chain_refined.json
- joint_chain_refined_overlay.png
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import cv2
    import numpy as np
    from PIL import Image
except ImportError as exc:
    print("缺少依赖：{}".format(exc))
    raise SystemExit(1) from exc

OUTPUT_JSON_NAME = "joint_chain_refined.json"
OUTPUT_OVERLAY_NAME = "joint_chain_refined_overlay.png"


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
            rgb[..., channel] = np.round(robust_normalize(image[..., channel]) * 255.0).astype(np.uint8)
        return rgb
    raise ValueError("无法转换图像维度：{}".format(image.shape))


def path_length(points_xy: np.ndarray) -> float:
    points = np.asarray(points_xy, dtype=np.float32)
    if len(points) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())


def deduplicate_consecutive(points_xy: np.ndarray) -> np.ndarray:
    points = np.asarray(points_xy, dtype=np.float32)
    if len(points) <= 1:
        return points
    keep = np.ones(len(points), dtype=bool)
    keep[1:] = np.any(np.abs(np.diff(points, axis=0)) > 1e-6, axis=1)
    return points[keep]


def resample_polyline(points_xy: np.ndarray, step_px: float = 4.0) -> np.ndarray:
    points = deduplicate_consecutive(points_xy)
    if len(points) < 2:
        return points
    steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(steps)])
    total = float(cumulative[-1])
    if total <= step_px:
        return points[[0, -1]]
    targets = np.arange(0.0, total, max(1.0, float(step_px)), dtype=np.float32)
    if targets[-1] < total:
        targets = np.concatenate([targets, [total]])
    result = np.empty((len(targets), 2), dtype=np.float32)
    result[:, 0] = np.interp(targets, cumulative, points[:, 0])
    result[:, 1] = np.interp(targets, cumulative, points[:, 1])
    return result


def unit_vectors(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    valid = norms[:, 0] > 1e-6
    result = np.zeros_like(vectors, dtype=np.float32)
    result[valid] = vectors[valid] / norms[valid]
    return result


def orientation(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    return float((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))


def segments_intersect(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> bool:
    # 仅统计真正交叉；相邻端点接触由调用方排除。
    o1 = orientation(a, b, c)
    o2 = orientation(a, b, d)
    o3 = orientation(c, d, a)
    o4 = orientation(c, d, b)
    return (o1 * o2 < -1e-6) and (o3 * o4 < -1e-6)


def geometry_features(points_xy: np.ndarray, sample_step_px: float = 4.0) -> Dict[str, Any]:
    sampled = resample_polyline(points_xy, sample_step_px)
    length = path_length(sampled)
    endpoint_distance = float(np.linalg.norm(sampled[-1] - sampled[0])) if len(sampled) >= 2 else 0.0
    tortuosity = float(length / max(endpoint_distance, 1.0))
    turns: List[float] = []
    if len(sampled) >= 3:
        directions = unit_vectors(np.diff(sampled, axis=0))
        dots = np.sum(directions[:-1] * directions[1:], axis=1)
        turns = np.degrees(np.arccos(np.clip(dots, -1.0, 1.0))).astype(np.float32).tolist()
    self_intersections = 0
    for i in range(max(0, len(sampled) - 1)):
        for j in range(i + 3, len(sampled) - 1):
            if segments_intersect(sampled[i], sampled[i + 1], sampled[j], sampled[j + 1]):
                self_intersections += 1
    turns_array = np.asarray(turns, dtype=np.float32)
    cumulative_turn = float(turns_array.sum()) if len(turns_array) else 0.0
    return {
        "sample_count": int(len(sampled)),
        "resampled_length_px": float(length),
        "endpoint_distance_px": float(endpoint_distance),
        "tortuosity": float(tortuosity),
        "mean_turn_angle_deg": float(turns_array.mean()) if len(turns_array) else 0.0,
        "p90_turn_angle_deg": float(np.percentile(turns_array, 90.0)) if len(turns_array) else 0.0,
        "maximum_local_turn_angle_deg": float(turns_array.max()) if len(turns_array) else 0.0,
        "sharp_turn_count_45deg": int(np.sum(turns_array >= 45.0)) if len(turns_array) else 0,
        "reverse_turn_count_90deg": int(np.sum(turns_array >= 90.0)) if len(turns_array) else 0,
        "cumulative_turn_per_100px": float(cumulative_turn * 100.0 / max(length, 1.0)),
        "self_intersection_count": int(self_intersections),
        "sampled_points_xy": sampled.tolist(),
    }


def sample_image(image: np.ndarray, points_xy: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    points = np.rint(np.asarray(points_xy, dtype=np.float32)).astype(np.int32)
    xs = np.clip(points[:, 0], 0, width - 1)
    ys = np.clip(points[:, 1], 0, height - 1)
    return image[ys, xs]


def width_signal_features(
    points_xy: np.ndarray,
    mask: np.ndarray,
    distance_map: np.ndarray,
    probability: np.ndarray,
) -> Dict[str, Any]:
    sampled = resample_polyline(points_xy, 3.0)
    mask_values = sample_image(mask.astype(np.uint8), sampled) > 0
    half_width = sample_image(distance_map, sampled).astype(np.float32)
    widths = 2.0 * half_width[mask_values]
    probability_values = sample_image(probability, sampled).astype(np.float32)
    positive_probability = probability_values[mask_values]
    if len(widths):
        median_width = float(np.median(widths))
        p10 = float(np.percentile(widths, 10.0))
        p90 = float(np.percentile(widths, 90.0))
        mean_width = float(widths.mean())
        width_cv = float(widths.std() / max(mean_width, 1e-6))
        width_spread = float((p90 - p10) / max(median_width, 1e-6))
        thick_ratio = float(np.mean(widths > max(8.0, 2.2 * median_width)))
    else:
        median_width = p10 = p90 = mean_width = width_cv = width_spread = thick_ratio = 0.0
    return {
        "mask_coverage_ratio": float(mask_values.mean()) if len(mask_values) else 0.0,
        "median_width_px": median_width,
        "mean_width_px": mean_width,
        "p10_width_px": p10,
        "p90_width_px": p90,
        "width_cv": width_cv,
        "normalized_width_spread": width_spread,
        "abnormally_thick_sample_ratio": thick_ratio,
        "mean_path_probability": float(probability_values.mean()) if len(probability_values) else 0.0,
        "low_probability_sample_ratio": float(np.mean(probability_values < 0.10)) if len(probability_values) else 1.0,
        "mean_in_mask_probability": float(positive_probability.mean()) if len(positive_probability) else 0.0,
    }


def load_head_centres(start_payload: Dict[str, Any]) -> Dict[int, Dict[str, float]]:
    centres: Dict[int, Dict[str, float]] = {}
    for item in list(start_payload.get("candidates") or []):
        head_id = int(item["head_id"])
        if head_id in centres:
            continue
        centres[head_id] = {
            "x": float(item.get("head_center_x", 0.0)),
            "y": float(item.get("head_center_y", 0.0)),
            "major": float(item.get("head_major_axis_length", 20.0) or 20.0),
            "minor": float(item.get("head_minor_axis_length", 12.0) or 12.0),
        }
    return centres


def head_exclusion_features(
    head_id: int,
    points_xy: np.ndarray,
    head_labels: Optional[np.ndarray],
    head_centres: Dict[int, Dict[str, float]],
) -> Dict[str, Any]:
    sampled = resample_polyline(points_xy, 3.0)
    # 靠头端允许处于头部邻域；从约 18 px 后开始评估其他头部侵入。
    evaluate = sampled[6:] if len(sampled) > 6 else sampled[-1:]
    other_label_ratio = 0.0
    other_label_ids: List[int] = []
    own_label_ratio_after_start = 0.0
    if head_labels is not None and len(evaluate):
        values = sample_image(head_labels, evaluate).astype(np.int64)
        other = values[(values > 0) & (values != int(head_id))]
        other_label_ratio = float(len(other) / len(values))
        other_label_ids = sorted(int(value) for value in np.unique(other))
        own_label_ratio_after_start = float(np.mean(values == int(head_id)))

    minimum_other_head_clearance = float("inf")
    nearest_other_head_id: Optional[int] = None
    if len(evaluate):
        for other_id, item in head_centres.items():
            if int(other_id) == int(head_id):
                continue
            centre = np.asarray([item["x"], item["y"]], dtype=np.float32)
            distances = np.linalg.norm(evaluate - centre[None, :], axis=1)
            clearance = float(distances.min() - 0.5 * max(item["major"], item["minor"]))
            if clearance < minimum_other_head_clearance:
                minimum_other_head_clearance = clearance
                nearest_other_head_id = int(other_id)
    if not math.isfinite(minimum_other_head_clearance):
        minimum_other_head_clearance = 9999.0
    return {
        "other_head_label_overlap_ratio": float(other_label_ratio),
        "other_head_label_ids": other_label_ids,
        "own_head_reentry_ratio": float(own_label_ratio_after_start),
        "minimum_other_head_clearance_px": float(minimum_other_head_clearance),
        "nearest_other_head_id": nearest_other_head_id,
        "approaches_other_head": bool(minimum_other_head_clearance < 3.0),
    }


def start_lookup(start_payload: Dict[str, Any]) -> Dict[Tuple[int, int, str], Dict[str, Any]]:
    result: Dict[Tuple[int, int, str], Dict[str, Any]] = {}
    for item in list(start_payload.get("candidates") or []):
        key = (int(item["head_id"]), int(item["edge_id"]), str(item["side"]))
        result[key] = dict(item)
    return result


def graph_edge_lookup(graph_payload: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    return {int(item["edge_id"]): dict(item) for item in list(graph_payload.get("edges") or [])}


def robust_range(values: Sequence[float], minimum_count: int = 5) -> Optional[Dict[str, float]]:
    array = np.asarray([float(value) for value in values if math.isfinite(float(value))], dtype=np.float32)
    if len(array) < minimum_count:
        return None
    q10, q25, median, q75, q90 = np.percentile(array, [10, 25, 50, 75, 90]).astype(float)
    iqr = max(q75 - q25, 1e-6)
    return {
        "count": int(len(array)),
        "q10": q10,
        "q25": q25,
        "median": median,
        "q75": q75,
        "q90": q90,
        "lower_fence": float(max(0.0, q10 - 0.50 * iqr)),
        "upper_fence": float(q90 + 0.50 * iqr),
    }


def preliminary_anchor(record: Dict[str, Any]) -> Tuple[bool, List[str]]:
    start = record["start_evidence"]
    geometry = record["geometry"]
    width = record["width_signal"]
    heads = record["head_exclusion"]
    reasons: List[str] = []
    if record["start_status"] != "strong_bidirectional": reasons.append("start_not_strong")
    if not bool(start.get("mutual_top1")): reasons.append("start_not_mutual_top1")
    if start.get("ray_available") is True and not bool(start.get("ray_hits_expected_head")): reasons.append("ray_misses_head")
    if float(start.get("direction_angle_deg", 180.0)) > 22.0: reasons.append("start_direction_large")
    if float(record["chain_score"]) < 0.78: reasons.append("chain_score_low")
    if bool(record.get("has_cross_head_conflict")): reasons.append("cross_head_edge_conflict")
    if int(record.get("bridge_count", 0)) > 0: reasons.append("uses_bridge")
    if int(record.get("edge_count", 0)) < 2 and float(record.get("total_length_px", 0.0)) < 180.0: reasons.append("single_short_edge")
    if float(record.get("total_length_px", 0.0)) < 90.0: reasons.append("too_short")
    if int(record["non_simple_edge_count"]) > 0: reasons.append("non_simple_graph_edge")
    if geometry["self_intersection_count"] > 0: reasons.append("self_intersection")
    if geometry["reverse_turn_count_90deg"] > 0: reasons.append("reverse_turn")
    if geometry["maximum_local_turn_angle_deg"] > 55.0: reasons.append("sharp_local_turn")
    if geometry["tortuosity"] > 4.5: reasons.append("extreme_tortuosity")
    if width["mask_coverage_ratio"] < 0.76: reasons.append("low_mask_coverage")
    if width["normalized_width_spread"] > 2.2: reasons.append("width_unstable")
    if width["abnormally_thick_sample_ratio"] > 0.12: reasons.append("crosses_thick_region")
    if width["mean_path_probability"] < 0.14: reasons.append("signal_low")
    if heads["other_head_label_overlap_ratio"] > 0.0: reasons.append("crosses_other_head")
    if heads["approaches_other_head"]: reasons.append("approaches_other_head")
    return len(reasons) == 0, reasons


def adaptive_support(record: Dict[str, Any], model: Dict[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    length_model = model.get("length_px")
    width_model = model.get("median_width_px")
    curvature_model = model.get("cumulative_turn_per_100px")
    if length_model:
        value = float(record["total_length_px"])
        if not (length_model["lower_fence"] <= value <= length_model["upper_fence"]): reasons.append("length_outside_anchor_distribution")
    if width_model:
        value = float(record["width_signal"]["median_width_px"])
        if not (width_model["lower_fence"] <= value <= width_model["upper_fence"]): reasons.append("width_outside_anchor_distribution")
    if curvature_model:
        value = float(record["geometry"]["cumulative_turn_per_100px"])
        if value > curvature_model["upper_fence"]: reasons.append("curvature_above_anchor_distribution")
    return len(reasons) == 0, reasons


def refined_score(record: Dict[str, Any], adaptive_ok: bool) -> float:
    start = record["start_evidence"]
    geometry = record["geometry"]
    width = record["width_signal"]
    heads = record["head_exclusion"]
    start_strength = float(record.get("start_score", 0.0))
    chain_strength = float(record.get("chain_score", 0.0))
    direction = math.exp(-((float(start.get("direction_angle_deg", 90.0)) / 25.0) ** 2))
    geometry_score = math.exp(-((geometry["p90_turn_angle_deg"] / 42.0) ** 2))
    width_score = float(np.clip(width["mask_coverage_ratio"], 0.0, 1.0)) * math.exp(-min(width["normalized_width_spread"], 5.0) / 2.5)
    signal_score = float(np.clip((width["mean_path_probability"] - 0.05) / 0.45, 0.0, 1.0))
    score = 0.20 * start_strength + 0.20 * chain_strength + 0.14 * direction + 0.16 * geometry_score + 0.14 * width_score + 0.11 * signal_score + 0.05 * float(adaptive_ok)
    penalties = 0.0
    penalties += 0.25 * int(bool(record.get("has_cross_head_conflict")))
    penalties += 0.12 * min(int(record.get("bridge_count", 0)), 2)
    penalties += 0.25 * min(int(geometry["self_intersection_count"]), 1)
    penalties += 0.18 * min(int(geometry["reverse_turn_count_90deg"]), 1)
    penalties += 0.20 * int(bool(heads["approaches_other_head"]))
    penalties += 0.25 * int(heads["other_head_label_overlap_ratio"] > 0.0)
    penalties += 0.08 * min(int(record["non_simple_edge_count"]), 2)
    return float(np.clip(score - penalties, 0.0, 1.0))


def classify_record(record: Dict[str, Any], anchor: bool, adaptive_ok: bool, score: float) -> Tuple[str, List[str]]:
    reasons: List[str] = []
    if anchor:
        return "trusted_anchor", ["strict_joint_evidence"]
    geometry = record["geometry"]
    width = record["width_signal"]
    heads = record["head_exclusion"]
    hard_failure = (
        geometry["self_intersection_count"] > 0
        or geometry["reverse_turn_count_90deg"] > 0
        or heads["other_head_label_overlap_ratio"] > 0.0
        or bool(heads["approaches_other_head"])
        or width["mask_coverage_ratio"] < 0.45
        or record["total_length_px"] < 40.0
    )
    if hard_failure:
        reasons.append("hard_geometric_or_head_exclusion_failure")
        return "rejected_candidate", reasons
    supported = (
        adaptive_ok
        and score >= 0.70
        and record["start_status"] == "strong_bidirectional"
        and not bool(record.get("has_cross_head_conflict"))
        and int(record.get("bridge_count", 0)) <= 1
        and geometry["maximum_local_turn_angle_deg"] <= 68.0
        and width["mask_coverage_ratio"] >= 0.65
        and record["total_length_px"] >= 75.0
    )
    if supported:
        return "supported_candidate", ["passes_adaptive_joint_evidence"]
    if score >= 0.48 and record["total_length_px"] >= 55.0:
        return "review_candidate", ["insufficient_for_auto_accept"]
    return "rejected_candidate", ["joint_evidence_too_weak"]


def make_overlay(base_rgb: np.ndarray, records: Sequence[Dict[str, Any]], head_centres: Dict[int, Dict[str, float]]) -> np.ndarray:
    overlay = base_rgb.copy()
    colors = {
        "trusted_anchor": (80, 225, 255),
        "supported_candidate": (90, 150, 255),
        "review_candidate": (255, 220, 60),
        "rejected_candidate": (255, 70, 210),
    }
    thickness = {"trusted_anchor": 3, "supported_candidate": 2, "review_candidate": 2, "rejected_candidate": 1}
    for record in records:
        status = str(record["refined_status"])
        points = np.asarray(record.get("points_xy") or [], dtype=np.int32)
        if len(points) < 2:
            continue
        path = points.reshape(-1, 1, 2)
        cv2.polylines(overlay, [path], False, (8, 8, 8), thickness[status] + 2, cv2.LINE_AA)
        cv2.polylines(overlay, [path], False, colors[status], thickness[status], cv2.LINE_AA)
        start = tuple(int(v) for v in points[0])
        cv2.circle(overlay, start, 5, (8, 8, 8), -1, cv2.LINE_AA)
        cv2.circle(overlay, start, 3, colors[status], -1, cv2.LINE_AA)
        label = "H{} {:.2f}".format(int(record["head_id"]), float(record["refined_score"]))
        cv2.putText(overlay, label, (start[0] + 5, start[1] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.34, colors[status], 1, cv2.LINE_AA)
    for head_id, item in head_centres.items():
        centre = (int(round(item["x"])), int(round(item["y"])))
        cv2.circle(overlay, centre, 3, (255, 70, 70), 1, cv2.LINE_AA)
    cv2.rectangle(overlay, (8, 8), (545, 100), (0, 0, 0), -1)
    lines = [
        ("Cyan: strict anchor", colors["trusted_anchor"]),
        ("Blue: adaptively supported", colors["supported_candidate"]),
        ("Yellow: review", colors["review_candidate"]),
        ("Magenta: rejected evidence", colors["rejected_candidate"]),
    ]
    for index, (text, color) in enumerate(lines):
        cv2.putText(overlay, text, (18, 28 + 22 * index), cv2.FONT_HERSHEY_SIMPLEX, 0.46, color, 1, cv2.LINE_AA)
    return overlay


def find_single(pattern: str, directory: Path) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError("目录 {} 中找不到 {}。".format(directory, pattern))
    return matches[0].resolve()


def resolve_task_inputs(task_root: Path, field_id: str, output_dir: Optional[Path]) -> Dict[str, Optional[Path]]:
    root = task_root.resolve()
    field_root = root / "segmentation" / "tail" / field_id
    head_path = root / "calibration" / "head" / "{}_HeadFinalLabels.tif".format(field_id)
    return {
        "chain_json": (root / "segmentation" / "tail_joint_chain_mvp" / field_id / "joint_chain_candidates.json").resolve(),
        "start_json": (root / "segmentation" / "tail_joint_mvp" / field_id / "joint_start_candidates.json").resolve(),
        "graph": (field_root / "stage1_2" / "tail_graph_stage1_2.json").resolve(),
        "mask": (field_root / "stage1" / "balanced_mask_uint8.tif").resolve(),
        "probability": (field_root / "stage1" / "02_probability_uint16.tif").resolve(),
        "head_labels": head_path.resolve() if head_path.is_file() else None,
        "merge": find_single("{}_Merge.*".format(field_id), root / "input"),
        "output_dir": (output_dir or (root / "segmentation" / "tail_joint_refined_mvp" / field_id)).resolve(),
    }


def validate_file(name: str, path: Optional[Path], required: bool = True) -> None:
    if path is None:
        if required:
            raise FileNotFoundError("{} 未提供。".format(name))
        return
    if not path.is_file():
        raise FileNotFoundError("{} 不存在：{}".format(name, path))


def run_mvp(
    *,
    chain_json_path: Path,
    start_json_path: Path,
    graph_path: Path,
    mask_path: Path,
    probability_path: Path,
    output_dir: Path,
    merge_path: Optional[Path] = None,
    head_labels_path: Optional[Path] = None,
) -> Dict[str, Any]:
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    chain_payload = json.loads(chain_json_path.read_text(encoding="utf-8"))
    start_payload = json.loads(start_json_path.read_text(encoding="utf-8"))
    graph_payload = json.loads(graph_path.read_text(encoding="utf-8"))
    mask = read_image(mask_path)
    if mask.ndim > 2: mask = np.squeeze(mask)
    mask = mask > 0
    probability = normalize_probability(read_image(probability_path))
    if probability.ndim > 2: probability = np.squeeze(probability)
    if mask.shape != probability.shape:
        raise ValueError("掩模与概率图尺寸不一致。")
    distance_map = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    head_labels: Optional[np.ndarray] = None
    if head_labels_path is not None:
        head_labels = read_image(head_labels_path)
        if head_labels.ndim > 2: head_labels = np.squeeze(head_labels)
        if head_labels.shape != mask.shape:
            raise ValueError("头部标签与尾部图尺寸不一致。")

    start_map = start_lookup(start_payload)
    edge_map = graph_edge_lookup(graph_payload)
    head_centres = load_head_centres(start_payload)
    records: List[Dict[str, Any]] = []
    for source in list(chain_payload.get("best_chains") or []):
        record = dict(source)
        points = np.asarray(source.get("points_xy") or [], dtype=np.float32)
        key = (int(source["head_id"]), int(source["edge_ids"][0]), str(source["edge_sides"][0]))
        start = dict(start_map.get(key) or {})
        qualities = [str(edge_map.get(int(edge_id), {}).get("quality", "missing")) for edge_id in source.get("edge_ids") or []]
        record["start_evidence"] = start
        record["edge_qualities"] = qualities
        record["non_simple_edge_count"] = int(sum(value != "simple" for value in qualities))
        record["geometry"] = geometry_features(points)
        record["width_signal"] = width_signal_features(points, mask, distance_map, probability)
        record["head_exclusion"] = head_exclusion_features(int(source["head_id"]), points, head_labels, head_centres)
        anchor, anchor_failures = preliminary_anchor(record)
        record["preliminary_anchor"] = bool(anchor)
        record["preliminary_anchor_failures"] = anchor_failures
        records.append(record)

    anchors = [item for item in records if item["preliminary_anchor"]]
    adaptive_model = {
        "source": "strict_preliminary_anchors",
        "anchor_count": int(len(anchors)),
        "length_px": robust_range([item["total_length_px"] for item in anchors]),
        "median_width_px": robust_range([item["width_signal"]["median_width_px"] for item in anchors]),
        "cumulative_turn_per_100px": robust_range([item["geometry"]["cumulative_turn_per_100px"] for item in anchors]),
        "boundary_gap_px": robust_range([item["start_evidence"].get("boundary_gap_px", float("nan")) for item in anchors]),
    }

    status_counts: Dict[str, int] = {}
    for record in records:
        adaptive_ok, adaptive_failures = adaptive_support(record, adaptive_model)
        score = refined_score(record, adaptive_ok)
        status, reasons = classify_record(record, bool(record["preliminary_anchor"]), adaptive_ok, score)
        record["adaptive_distribution_pass"] = bool(adaptive_ok)
        record["adaptive_distribution_failures"] = adaptive_failures
        record["refined_score"] = float(score)
        record["refined_status"] = status
        record["refined_reasons"] = reasons
        status_counts[status] = status_counts.get(status, 0) + 1

    # 只在叠加图画最佳候选；拒绝项也保留细线，便于查错。
    if merge_path is not None:
        base_rgb = to_uint8_rgb(read_image(merge_path))
    else:
        base_rgb = to_uint8_rgb(probability)
    if base_rgb.shape[:2] != mask.shape:
        raise ValueError("背景图与分析图尺寸不一致。")
    overlay = make_overlay(base_rgb, records, head_centres)
    overlay_path = output_dir / OUTPUT_OVERLAY_NAME
    Image.fromarray(overlay).save(overlay_path)

    compact_records: List[Dict[str, Any]] = []
    for item in records:
        compact_records.append(item)

    payload: Dict[str, Any] = {
        "schema_version": "tail_joint_chain_refined_mvp_v1",
        "purpose": "对双向头尾联合链候选进行宽度、曲率、头部排他、冲突和本视野自适应精炼；不是最终尾部标签，不得进入测量。",
        "sources": {
            "chain_candidates": str(chain_json_path.resolve()),
            "start_candidates": str(start_json_path.resolve()),
            "graph": str(graph_path.resolve()),
            "balanced_mask": str(mask_path.resolve()),
            "probability": str(probability_path.resolve()),
            "head_labels": None if head_labels_path is None else str(head_labels_path.resolve()),
            "merge": None if merge_path is None else str(merge_path.resolve()),
        },
        "summary": {
            "evaluated_best_chain_count": int(len(records)),
            "preliminary_anchor_count": int(len(anchors)),
            "refined_status_counts": status_counts,
            "head_labels_available": bool(head_labels is not None),
            "elapsed_seconds": float(time.perf_counter() - started),
        },
        "adaptive_model": adaptive_model,
        "refined_chains": compact_records,
        "outputs": {
            "json": str((output_dir / OUTPUT_JSON_NAME).resolve()),
            "overlay": str(overlay_path.resolve()),
        },
    }
    json_path = output_dir / OUTPUT_JSON_NAME
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="双向头尾联合链候选证据精炼 MVP")
    parser.add_argument("--task-root")
    parser.add_argument("--field-id")
    parser.add_argument("--chain-json")
    parser.add_argument("--start-json")
    parser.add_argument("--graph")
    parser.add_argument("--mask")
    parser.add_argument("--probability")
    parser.add_argument("--head-labels")
    parser.add_argument("--merge")
    parser.add_argument("--output-dir")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.task_root:
        if not args.field_id:
            raise SystemExit("使用 --task-root 时必须同时提供 --field-id。")
        resolved = resolve_task_inputs(Path(args.task_root), str(args.field_id), None if not args.output_dir else Path(args.output_dir))
    else:
        required = [args.chain_json, args.start_json, args.graph, args.mask, args.probability, args.output_dir]
        if not all(required):
            raise SystemExit("显式模式需要 --chain-json、--start-json、--graph、--mask、--probability、--output-dir。")
        resolved = {
            "chain_json": Path(args.chain_json).resolve(),
            "start_json": Path(args.start_json).resolve(),
            "graph": Path(args.graph).resolve(),
            "mask": Path(args.mask).resolve(),
            "probability": Path(args.probability).resolve(),
            "head_labels": None if not args.head_labels else Path(args.head_labels).resolve(),
            "merge": None if not args.merge else Path(args.merge).resolve(),
            "output_dir": Path(args.output_dir).resolve(),
        }
    for name in ["chain_json", "start_json", "graph", "mask", "probability"]:
        validate_file(name, resolved[name])
    validate_file("head_labels", resolved.get("head_labels"), required=False)
    validate_file("merge", resolved.get("merge"), required=False)
    payload = run_mvp(
        chain_json_path=resolved["chain_json"],
        start_json_path=resolved["start_json"],
        graph_path=resolved["graph"],
        mask_path=resolved["mask"],
        probability_path=resolved["probability"],
        output_dir=resolved["output_dir"],
        merge_path=resolved.get("merge"),
        head_labels_path=resolved.get("head_labels"),
    )
    print("双向链候选证据精炼 MVP 完成。")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print("输出：{}".format(resolved["output_dir"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
