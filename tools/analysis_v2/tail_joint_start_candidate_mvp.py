#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双向头部-尾部起始片段候选 MVP。

目标：
1. 头部位置、轮廓和轴向帮助发现可能的尾部起始片段；
2. 尾部端点、局部切线和绿色信号反向验证头部归属；
3. 允许头部无尾部、片段无头部，不执行强制匹配；
4. 本阶段只输出起始关系候选，不拼接完整尾部，不生成最终标签。

正式接入前的输出契约固定为两个文件：
- joint_start_candidates.json
- joint_start_candidates_overlay.png
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import cv2
    import numpy as np
    from PIL import Image
    from scipy.spatial import cKDTree
    from skimage.measure import regionprops
except ImportError as exc:
    print("缺少依赖：{}".format(exc))
    raise SystemExit(1) from exc


OUTPUT_JSON_NAME = "joint_start_candidates.json"
OUTPUT_OVERLAY_NAME = "joint_start_candidates_overlay.png"


@dataclass
class HeadFeature:
    head_id: int
    center_xy: np.ndarray
    area: float
    major_axis_length: float
    minor_axis_length: float
    major_axis_unit_xy: Optional[np.ndarray]
    contour_xy: Optional[np.ndarray]
    bbox_xyxy: Tuple[int, int, int, int]
    source: str


@dataclass
class EndpointFeature:
    endpoint_key: str
    edge_id: int
    side: str
    point_xy: np.ndarray
    forward_unit_xy: np.ndarray
    forward_sample_xy: np.ndarray
    available_forward_px: float
    local_probability: float
    edge_mean_probability: float
    edge_min_probability: float
    edge_length_px: float
    edge_quality: str
    points_xy: np.ndarray


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
        if maximum <= 255.0:
            array /= 255.0
        else:
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
    raise ValueError("无法转换图像维度：{}".format(image.shape))


def unit_vector(vector: np.ndarray) -> Optional[np.ndarray]:
    norm = float(np.linalg.norm(vector))
    if norm < 1e-6:
        return None
    return vector.astype(np.float32) / norm


def angle_degrees(first: np.ndarray, second: np.ndarray, axis_symmetric: bool = False) -> float:
    first_unit = unit_vector(first)
    second_unit = unit_vector(second)
    if first_unit is None or second_unit is None:
        return 180.0
    cosine = float(np.clip(np.dot(first_unit, second_unit), -1.0, 1.0))
    if axis_symmetric:
        cosine = abs(cosine)
    return float(math.degrees(math.acos(cosine)))


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
        [np.zeros(1, dtype=np.float32), np.cumsum(steps).astype(np.float32)]
    )


def sample_probability(probability: np.ndarray, points_xy: np.ndarray) -> float:
    if len(points_xy) == 0:
        return 0.0
    x = np.clip(np.round(points_xy[:, 0]).astype(np.int32), 0, probability.shape[1] - 1)
    y = np.clip(np.round(points_xy[:, 1]).astype(np.int32), 0, probability.shape[0] - 1)
    return float(np.mean(probability[y, x]))


def pca_major_axis(coords_yx: np.ndarray) -> Optional[np.ndarray]:
    if len(coords_yx) < 2:
        return None
    points_xy = np.column_stack([coords_yx[:, 1], coords_yx[:, 0]]).astype(np.float32)
    centered = points_xy - points_xy.mean(axis=0, keepdims=True)
    covariance = np.cov(centered, rowvar=False)
    if covariance.shape != (2, 2) or not np.all(np.isfinite(covariance)):
        return None
    values, vectors = np.linalg.eigh(covariance)
    vector = vectors[:, int(np.argmax(values))].astype(np.float32)
    return unit_vector(vector)


def region_contour(head_labels: np.ndarray, head_id: int, bbox: Tuple[int, int, int, int]) -> Optional[np.ndarray]:
    min_x, min_y, max_x, max_y = bbox
    local = (head_labels[min_y:max_y, min_x:max_x] == int(head_id)).astype(np.uint8)
    contours, _ = cv2.findContours(local, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea).reshape(-1, 2).astype(np.float32)
    contour[:, 0] += float(min_x)
    contour[:, 1] += float(min_y)
    return contour


def load_heads_from_labels(head_labels_path: Path) -> Tuple[List[HeadFeature], np.ndarray]:
    head_labels = read_image(head_labels_path)
    if head_labels.ndim > 2:
        head_labels = np.squeeze(head_labels)
    if head_labels.ndim != 2:
        raise ValueError("HeadFinalLabels 必须是二维标签图。")
    head_labels = head_labels.astype(np.int32, copy=False)

    heads: List[HeadFeature] = []
    for region in regionprops(head_labels):
        head_id = int(region.label)
        y, x = region.centroid
        min_y, min_x, max_y, max_x = [int(value) for value in region.bbox]
        bbox = (min_x, min_y, max_x, max_y)
        major_length = float(
            region.axis_major_length
            if hasattr(region, "axis_major_length")
            else region.major_axis_length
        )
        minor_length = float(
            region.axis_minor_length
            if hasattr(region, "axis_minor_length")
            else region.minor_axis_length
        )
        heads.append(
            HeadFeature(
                head_id=head_id,
                center_xy=np.asarray([float(x), float(y)], dtype=np.float32),
                area=float(region.area),
                major_axis_length=float(max(major_length, 18.0)),
                minor_axis_length=float(max(minor_length, 8.0)),
                major_axis_unit_xy=pca_major_axis(region.coords),
                contour_xy=region_contour(head_labels, head_id, bbox),
                bbox_xyxy=bbox,
                source="HeadFinalLabels",
            )
        )
    heads.sort(key=lambda item: item.head_id)
    if not heads:
        raise ValueError("HeadFinalLabels 中没有头部对象。")
    return heads, head_labels


def load_heads_from_stage2_1(head_results_path: Path, expected_shape: Tuple[int, int]) -> Tuple[List[HeadFeature], None]:
    """仅用于旧调试包回放；正式流程必须传 HeadFinalLabels。"""
    payload = json.loads(head_results_path.read_text(encoding="utf-8"))
    heads: List[HeadFeature] = []
    for item in list(payload.get("results") or []):
        head_id = int(item["head_id"])
        center_x = float(item["center_x"])
        center_y = float(item["center_y"])
        major = float(max(item.get("major_axis_length", 18.0), 18.0))
        minor = float(max(item.get("minor_axis_length", 8.0), 8.0))
        half_x = int(math.ceil(major / 2.0))
        half_y = int(math.ceil(minor / 2.0))
        min_x = max(0, int(round(center_x)) - half_x)
        max_x = min(expected_shape[1], int(round(center_x)) + half_x + 1)
        min_y = max(0, int(round(center_y)) - half_y)
        max_y = min(expected_shape[0], int(round(center_y)) + half_y + 1)
        heads.append(
            HeadFeature(
                head_id=head_id,
                center_xy=np.asarray([center_x, center_y], dtype=np.float32),
                area=float(item.get("area", math.pi * major * minor / 4.0)),
                major_axis_length=major,
                minor_axis_length=minor,
                major_axis_unit_xy=None,
                contour_xy=None,
                bbox_xyxy=(min_x, min_y, max_x, max_y),
                source="stage2_1_fallback_without_orientation",
            )
        )
    heads.sort(key=lambda item: item.head_id)
    if not heads:
        raise ValueError("旧 Stage 2.1 结果中没有头部对象。")
    return heads, None


def forward_sample(points_xy: np.ndarray, side: str, target_distance: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    oriented = points_xy if side == "start" else points_xy[::-1]
    cumulative = cumulative_lengths(oriented)
    if len(oriented) < 2 or float(cumulative[-1]) < 1.0:
        raise ValueError("边端点没有足够路径用于计算切线。")
    index = int(np.searchsorted(cumulative, float(target_distance), side="left"))
    index = max(1, min(index, len(oriented) - 1))
    endpoint = oriented[0].astype(np.float32)
    sample = oriented[index].astype(np.float32)
    forward = unit_vector(sample - endpoint)
    if forward is None:
        raise ValueError("边端点切线无法计算。")
    return endpoint, sample, forward, float(cumulative[-1])


def load_endpoints(graph_path: Path, probability: np.ndarray, tangent_distance_px: float) -> List[EndpointFeature]:
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    endpoints: List[EndpointFeature] = []
    for item in list(payload.get("edges") or []):
        points = np.asarray(item.get("points_xy") or [], dtype=np.float32)
        if len(points) < 2:
            continue
        for side in ("start", "end"):
            try:
                endpoint, sample, forward, available = forward_sample(
                    points, side, tangent_distance_px
                )
            except ValueError:
                continue
            oriented = points if side == "start" else points[::-1]
            cumulative = cumulative_lengths(oriented)
            local_limit = min(35.0, float(cumulative[-1]))
            local_count = int(np.searchsorted(cumulative, local_limit, side="right"))
            local_count = max(2, min(local_count, len(oriented)))
            local_probability = sample_probability(probability, oriented[:local_count])
            edge_id = int(item["edge_id"])
            endpoints.append(
                EndpointFeature(
                    endpoint_key="edge_{}:{}".format(edge_id, side),
                    edge_id=edge_id,
                    side=side,
                    point_xy=endpoint,
                    forward_unit_xy=forward,
                    forward_sample_xy=sample,
                    available_forward_px=available,
                    local_probability=local_probability,
                    edge_mean_probability=float(item.get("mean_probability", 0.0)),
                    edge_min_probability=float(item.get("min_probability", 0.0)),
                    edge_length_px=float(item.get("length_px", available)),
                    edge_quality=str(item.get("quality", "unknown")),
                    points_xy=oriented,
                )
            )
    if not endpoints:
        raise ValueError("图 JSON 中没有可用边端点。")
    return endpoints


def contour_gap(head: HeadFeature, point_xy: np.ndarray) -> float:
    if head.contour_xy is None or len(head.contour_xy) < 3:
        center_distance = float(np.linalg.norm(point_xy - head.center_xy))
        return max(0.0, center_distance - 0.5 * head.major_axis_length)
    contour = head.contour_xy.reshape(-1, 1, 2).astype(np.float32)
    signed = float(cv2.pointPolygonTest(contour, (float(point_xy[0]), float(point_xy[1])), True))
    return max(0.0, -signed)


def ray_probe(
    head_labels: Optional[np.ndarray],
    expected_head_id: int,
    endpoint_xy: np.ndarray,
    forward_unit_xy: np.ndarray,
    maximum_distance: float,
    center_xy: np.ndarray,
) -> Dict[str, Any]:
    backward = -forward_unit_xy
    sample_distances = np.arange(0.0, max(1.0, maximum_distance) + 1.0, 1.0, dtype=np.float32)
    points = endpoint_xy[None, :] + sample_distances[:, None] * backward[None, :]
    min_center_distance = float(np.min(np.linalg.norm(points - center_xy[None, :], axis=1)))

    result: Dict[str, Any] = {
        "available": head_labels is not None,
        "hits_expected_head": None,
        "first_hit_head_id": None,
        "first_hit_distance_px": None,
        "minimum_center_distance_px": min_center_distance,
    }
    if head_labels is None:
        return result

    x = np.round(points[:, 0]).astype(np.int32)
    y = np.round(points[:, 1]).astype(np.int32)
    valid = (
        (x >= 0)
        & (x < head_labels.shape[1])
        & (y >= 0)
        & (y < head_labels.shape[0])
    )
    labels = np.zeros(len(points), dtype=np.int32)
    labels[valid] = head_labels[y[valid], x[valid]]
    hit_indices = np.flatnonzero(labels > 0)
    if len(hit_indices):
        index = int(hit_indices[0])
        hit_id = int(labels[index])
        result["first_hit_head_id"] = hit_id
        result["first_hit_distance_px"] = float(sample_distances[index])
        result["hits_expected_head"] = bool(hit_id == int(expected_head_id))
    else:
        result["hits_expected_head"] = False
    return result


def gaussian_score(value: float, center: float, width: float) -> float:
    if width <= 1e-6:
        return 0.0
    return float(math.exp(-((float(value) - float(center)) / float(width)) ** 2))


def clipped_signal_score(value: float) -> float:
    return float(np.clip((float(value) - 0.03) / 0.52, 0.0, 1.0))


def score_candidate(
    *,
    direction_angle_deg: float,
    axis_angle_deg: Optional[float],
    boundary_gap_ratio: float,
    ray: Dict[str, Any],
    local_probability: float,
    edge_mean_probability: float,
    available_forward_px: float,
    edge_quality: str,
    head_minor_axis_length: float,
) -> Tuple[float, Dict[str, float]]:
    components: Dict[str, float] = {}
    weights: Dict[str, float] = {}

    components["direction"] = gaussian_score(direction_angle_deg, 0.0, 24.0)
    weights["direction"] = 0.30

    components["gap"] = gaussian_score(boundary_gap_ratio, 0.65, 0.75)
    weights["gap"] = 0.15

    if axis_angle_deg is not None:
        components["head_axis"] = gaussian_score(axis_angle_deg, 0.0, 32.0)
        weights["head_axis"] = 0.12

    if bool(ray.get("available")):
        if ray.get("hits_expected_head") is True:
            ray_score = 1.0
        elif ray.get("first_hit_head_id") is not None:
            ray_score = 0.0
        else:
            normalized_miss = float(ray["minimum_center_distance_px"]) / max(
                1.0, 0.75 * float(head_minor_axis_length)
            )
            ray_score = 0.35 * math.exp(-(normalized_miss ** 2))
        components["ray_hit"] = float(ray_score)
        weights["ray_hit"] = 0.20

    combined_signal = 0.62 * float(local_probability) + 0.38 * float(edge_mean_probability)
    components["signal"] = clipped_signal_score(combined_signal)
    weights["signal"] = 0.13

    components["continuation"] = float(np.clip(available_forward_px / 90.0, 0.0, 1.0))
    weights["continuation"] = 0.05

    components["topology"] = 1.0 if edge_quality == "simple" else 0.20
    weights["topology"] = 0.05

    total_weight = float(sum(weights.values()))
    score = sum(weights[key] * components[key] for key in weights) / max(total_weight, 1e-6)
    return float(np.clip(score, 0.0, 1.0)), components


def build_candidates(
    heads: Sequence[HeadFeature],
    endpoints: Sequence[EndpointFeature],
    head_labels: Optional[np.ndarray],
    max_candidates_per_head: int,
    minimum_forward_px: float,
    maximum_direction_angle_deg: float,
) -> List[Dict[str, Any]]:
    endpoint_points = np.asarray([endpoint.point_xy for endpoint in endpoints], dtype=np.float32)
    endpoint_tree = cKDTree(endpoint_points)
    candidates: List[Dict[str, Any]] = []

    for head in heads:
        maximum_center_distance = min(
            150.0,
            max(82.0, 3.8 * float(head.major_axis_length)),
        )
        nearby_indices = endpoint_tree.query_ball_point(head.center_xy, r=maximum_center_distance)
        local_candidates: List[Dict[str, Any]] = []

        for endpoint_index in nearby_indices:
            endpoint = endpoints[int(endpoint_index)]
            if endpoint.available_forward_px < float(minimum_forward_px):
                continue

            radial = endpoint.point_xy - head.center_xy
            center_distance = float(np.linalg.norm(radial))
            if center_distance < 1.0:
                continue
            direction_angle = angle_degrees(radial, endpoint.forward_unit_xy)
            if direction_angle > float(maximum_direction_angle_deg):
                continue

            boundary_gap = contour_gap(head, endpoint.point_xy)
            boundary_gap_ratio = boundary_gap / max(head.major_axis_length, 1.0)
            if boundary_gap_ratio > 2.4:
                continue

            axis_angle: Optional[float] = None
            if head.major_axis_unit_xy is not None:
                axis_angle = angle_degrees(
                    head.major_axis_unit_xy,
                    radial,
                    axis_symmetric=True,
                )

            ray = ray_probe(
                head_labels=head_labels,
                expected_head_id=head.head_id,
                endpoint_xy=endpoint.point_xy,
                forward_unit_xy=endpoint.forward_unit_xy,
                maximum_distance=center_distance + head.major_axis_length,
                center_xy=head.center_xy,
            )

            score, components = score_candidate(
                direction_angle_deg=direction_angle,
                axis_angle_deg=axis_angle,
                boundary_gap_ratio=boundary_gap_ratio,
                ray=ray,
                local_probability=endpoint.local_probability,
                edge_mean_probability=endpoint.edge_mean_probability,
                available_forward_px=endpoint.available_forward_px,
                edge_quality=endpoint.edge_quality,
                head_minor_axis_length=head.minor_axis_length,
            )

            local_candidates.append(
                {
                    "head_id": int(head.head_id),
                    "endpoint_key": endpoint.endpoint_key,
                    "edge_id": int(endpoint.edge_id),
                    "side": endpoint.side,
                    "score": float(score),
                    "score_components": {key: float(value) for key, value in components.items()},
                    "head_source": head.source,
                    "head_center_x": float(head.center_xy[0]),
                    "head_center_y": float(head.center_xy[1]),
                    "head_major_axis_length": float(head.major_axis_length),
                    "head_minor_axis_length": float(head.minor_axis_length),
                    "endpoint_x": float(endpoint.point_xy[0]),
                    "endpoint_y": float(endpoint.point_xy[1]),
                    "forward_sample_x": float(endpoint.forward_sample_xy[0]),
                    "forward_sample_y": float(endpoint.forward_sample_xy[1]),
                    "forward_unit_x": float(endpoint.forward_unit_xy[0]),
                    "forward_unit_y": float(endpoint.forward_unit_xy[1]),
                    "center_distance_px": center_distance,
                    "boundary_gap_px": float(boundary_gap),
                    "boundary_gap_head_ratio": float(boundary_gap_ratio),
                    "direction_angle_deg": float(direction_angle),
                    "head_axis_angle_deg": None if axis_angle is None else float(axis_angle),
                    "ray_available": bool(ray.get("available")),
                    "ray_hits_expected_head": ray.get("hits_expected_head"),
                    "ray_first_hit_head_id": ray.get("first_hit_head_id"),
                    "ray_first_hit_distance_px": ray.get("first_hit_distance_px"),
                    "ray_minimum_center_distance_px": float(ray["minimum_center_distance_px"]),
                    "local_probability": float(endpoint.local_probability),
                    "edge_mean_probability": float(endpoint.edge_mean_probability),
                    "edge_min_probability": float(endpoint.edge_min_probability),
                    "edge_length_px": float(endpoint.edge_length_px),
                    "available_forward_px": float(endpoint.available_forward_px),
                    "edge_quality": endpoint.edge_quality,
                }
            )

        local_candidates.sort(key=lambda item: item["score"], reverse=True)
        for candidate in local_candidates[: max(1, int(max_candidates_per_head))]:
            candidates.append(candidate)

    candidates.sort(key=lambda item: (int(item["head_id"]), -float(item["score"])))
    return candidates


def assign_bidirectional_ranks(candidates: List[Dict[str, Any]]) -> None:
    by_head: Dict[int, List[Dict[str, Any]]] = {}
    by_endpoint: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in candidates:
        by_head.setdefault(int(candidate["head_id"]), []).append(candidate)
        by_endpoint.setdefault(str(candidate["endpoint_key"]), []).append(candidate)

    for group in by_head.values():
        group.sort(key=lambda item: item["score"], reverse=True)
        second = float(group[1]["score"]) if len(group) > 1 else 0.0
        for rank, candidate in enumerate(group, start=1):
            candidate["head_rank"] = int(rank)
            candidate["head_margin"] = float(candidate["score"] - second) if rank == 1 else 0.0

    for group in by_endpoint.values():
        group.sort(key=lambda item: item["score"], reverse=True)
        second = float(group[1]["score"]) if len(group) > 1 else 0.0
        for rank, candidate in enumerate(group, start=1):
            candidate["endpoint_rank"] = int(rank)
            candidate["endpoint_margin"] = float(candidate["score"] - second) if rank == 1 else 0.0

    for candidate in candidates:
        score = float(candidate["score"])
        direction = float(candidate["direction_angle_deg"])
        gap_ratio = float(candidate["boundary_gap_head_ratio"])
        axis_angle = candidate.get("head_axis_angle_deg")
        ray_available = bool(candidate.get("ray_available"))
        ray_hit = candidate.get("ray_hits_expected_head")
        topology_ok = str(candidate.get("edge_quality")) == "simple"

        geometric_anchor = (
            ray_hit is True
            if ray_available
            else (
                direction <= 18.0
                and (axis_angle is None or float(axis_angle) <= 28.0)
            )
        )
        strong = (
            int(candidate.get("head_rank", 999)) == 1
            and int(candidate.get("endpoint_rank", 999)) == 1
            and score >= 0.76
            and direction <= 28.0
            and gap_ratio <= 1.80
            and topology_ok
            and geometric_anchor
            and float(candidate.get("head_margin", 0.0)) >= 0.04
            and float(candidate.get("endpoint_margin", 0.0)) >= 0.025
        )
        review = (
            not strong
            and score >= 0.56
            and int(candidate.get("head_rank", 999)) <= 3
            and int(candidate.get("endpoint_rank", 999)) <= 3
            and direction <= 55.0
            and gap_ratio <= 2.20
        )
        candidate["status"] = "strong_bidirectional" if strong else (
            "review_bidirectional" if review else "weak"
        )
        candidate["mutual_top1"] = bool(
            int(candidate.get("head_rank", 999)) == 1
            and int(candidate.get("endpoint_rank", 999)) == 1
        )


def select_per_head(heads: Sequence[HeadFeature], candidates: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_head: Dict[int, List[Dict[str, Any]]] = {}
    for candidate in candidates:
        by_head.setdefault(int(candidate["head_id"]), []).append(candidate)

    selections: List[Dict[str, Any]] = []
    for head in heads:
        group = sorted(by_head.get(head.head_id, []), key=lambda item: item["score"], reverse=True)
        accepted = [item for item in group if item["status"] in {"strong_bidirectional", "review_bidirectional"}]
        if accepted:
            chosen = accepted[0]
            selections.append(
                {
                    "head_id": int(head.head_id),
                    "status": str(chosen["status"]),
                    "endpoint_key": str(chosen["endpoint_key"]),
                    "edge_id": int(chosen["edge_id"]),
                    "side": str(chosen["side"]),
                    "score": float(chosen["score"]),
                    "head_rank": int(chosen["head_rank"]),
                    "endpoint_rank": int(chosen["endpoint_rank"]),
                    "reason": "双向候选达到阈值；仍不是完整尾部结论。",
                }
            )
        else:
            selections.append(
                {
                    "head_id": int(head.head_id),
                    "status": "unmatched",
                    "endpoint_key": None,
                    "edge_id": None,
                    "side": None,
                    "score": None,
                    "head_rank": None,
                    "endpoint_rank": None,
                    "reason": "没有达到候选阈值；允许该头部无尾部荧光。",
                }
            )
    return selections


def dotted_line(image: np.ndarray, start_xy: np.ndarray, end_xy: np.ndarray, color: Tuple[int, int, int]) -> None:
    start = start_xy.astype(np.float32)
    end = end_xy.astype(np.float32)
    vector = end - start
    length = float(np.linalg.norm(vector))
    if length < 1.0:
        return
    direction = vector / length
    position = 0.0
    draw = True
    while position < length:
        next_position = min(length, position + 7.0)
        if draw:
            first = start + direction * position
            second = start + direction * next_position
            cv2.line(
                image,
                tuple(np.round(first).astype(int)),
                tuple(np.round(second).astype(int)),
                color,
                1,
                lineType=cv2.LINE_AA,
            )
        draw = not draw
        position = next_position


def make_overlay(
    base_rgb: np.ndarray,
    heads: Sequence[HeadFeature],
    endpoints: Sequence[EndpointFeature],
    candidates: Sequence[Dict[str, Any]],
    selections: Sequence[Dict[str, Any]],
) -> np.ndarray:
    overlay = base_rgb.copy()
    endpoint_by_key = {item.endpoint_key: item for item in endpoints}
    candidate_by_pair = {
        (int(item["head_id"]), str(item["endpoint_key"])): item
        for item in candidates
    }

    for head in heads:
        if head.contour_xy is not None and len(head.contour_xy) >= 3:
            contour = np.round(head.contour_xy).astype(np.int32).reshape(-1, 1, 2)
            cv2.polylines(overlay, [contour], True, (255, 70, 70), 1, lineType=cv2.LINE_AA)
        else:
            center = tuple(np.round(head.center_xy).astype(int))
            axes = (
                max(4, int(round(head.major_axis_length / 2.0))),
                max(3, int(round(head.minor_axis_length / 2.0))),
            )
            cv2.ellipse(overlay, center, axes, 0.0, 0.0, 360.0, (255, 70, 70), 1, cv2.LINE_AA)

    for selection in selections:
        if selection["status"] == "unmatched":
            continue
        head_id = int(selection["head_id"])
        endpoint_key = str(selection["endpoint_key"])
        candidate = candidate_by_pair[(head_id, endpoint_key)]
        endpoint = endpoint_by_key[endpoint_key]
        head = next(item for item in heads if item.head_id == head_id)

        if selection["status"] == "strong_bidirectional":
            color = (40, 255, 90)
            thickness = 2
        else:
            color = (255, 190, 40)
            thickness = 1

        dotted_line(overlay, head.center_xy, endpoint.point_xy, color)
        cumulative = cumulative_lengths(endpoint.points_xy)
        limit_index = int(np.searchsorted(cumulative, min(50.0, float(cumulative[-1])), side="right"))
        limit_index = max(2, min(limit_index, len(endpoint.points_xy)))
        path = np.round(endpoint.points_xy[:limit_index]).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(overlay, [path], False, color, thickness, lineType=cv2.LINE_AA)
        cv2.circle(
            overlay,
            tuple(np.round(endpoint.point_xy).astype(int)),
            5,
            color,
            thickness,
            lineType=cv2.LINE_AA,
        )
        label_xy = tuple(np.round(endpoint.point_xy + np.asarray([6.0, -6.0])).astype(int))
        cv2.putText(
            overlay,
            "H{} {:.2f}".format(head_id, float(candidate["score"])),
            label_xy,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            color,
            1,
            lineType=cv2.LINE_AA,
        )

    cv2.rectangle(overlay, (8, 8), (430, 76), (0, 0, 0), -1)
    cv2.putText(overlay, "Green: strong bidirectional start", (18, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (40, 255, 90), 1, cv2.LINE_AA)
    cv2.putText(overlay, "Orange: review start candidate", (18, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 190, 40), 1, cv2.LINE_AA)
    cv2.putText(overlay, "Red: calibrated head", (18, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 70, 70), 1, cv2.LINE_AA)
    return overlay


def find_single(pattern: str, directory: Path) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError("目录 {} 中找不到 {}。".format(directory, pattern))
    return matches[0].resolve()


def resolve_task_inputs(task_root: Path, field_id: str, output_dir: Optional[Path]) -> Dict[str, Path]:
    root = task_root.resolve()
    field_root = root / "segmentation" / "tail" / field_id
    return {
        "head_labels": (root / "calibration" / "head" / "{}_HeadFinalLabels.tif".format(field_id)).resolve(),
        "graph": (field_root / "stage1_2" / "tail_graph_stage1_2.json").resolve(),
        "probability": (field_root / "stage1" / "02_probability_uint16.tif").resolve(),
        "merge": find_single("{}_Merge.*".format(field_id), root / "input"),
        "output_dir": (output_dir or (root / "segmentation" / "tail_joint_mvp" / field_id)).resolve(),
    }


def validate_paths(paths: Dict[str, Optional[Path]]) -> None:
    for key in ("graph", "probability"):
        path = paths.get(key)
        if path is None or not path.is_file():
            raise FileNotFoundError("{} 不存在：{}".format(key, path))
    if paths.get("head_labels") is None and paths.get("head_results") is None:
        raise ValueError("必须提供 --head-labels，旧调试包回放时才允许 --head-results。")
    for key in ("head_labels", "head_results", "merge"):
        path = paths.get(key)
        if path is not None and not path.is_file():
            raise FileNotFoundError("{} 不存在：{}".format(key, path))


def run_mvp(
    *,
    graph_path: Path,
    probability_path: Path,
    output_dir: Path,
    head_labels_path: Optional[Path] = None,
    head_results_path: Optional[Path] = None,
    merge_path: Optional[Path] = None,
    tangent_distance_px: float = 18.0,
    minimum_forward_px: float = 18.0,
    maximum_direction_angle_deg: float = 75.0,
    max_candidates_per_head: int = 5,
) -> Dict[str, Any]:
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)

    probability = normalize_probability(read_image(probability_path))
    if probability.ndim > 2:
        probability = np.squeeze(probability)
    if probability.ndim != 2:
        raise ValueError("概率图必须是二维图像。")

    if head_labels_path is not None:
        heads, head_labels = load_heads_from_labels(head_labels_path)
    else:
        if head_results_path is None:
            raise ValueError("缺少头部输入。")
        heads, head_labels = load_heads_from_stage2_1(head_results_path, probability.shape)

    endpoints = load_endpoints(graph_path, probability, tangent_distance_px)
    candidates = build_candidates(
        heads=heads,
        endpoints=endpoints,
        head_labels=head_labels,
        max_candidates_per_head=max_candidates_per_head,
        minimum_forward_px=minimum_forward_px,
        maximum_direction_angle_deg=maximum_direction_angle_deg,
    )
    assign_bidirectional_ranks(candidates)
    selections = select_per_head(heads, candidates)

    if merge_path is not None:
        base_rgb = to_uint8_rgb(read_image(merge_path))
    else:
        base_rgb = to_uint8_rgb(probability)
    if base_rgb.shape[:2] != probability.shape:
        raise ValueError("Merge/背景图与概率图尺寸不一致。")

    overlay = make_overlay(base_rgb, heads, endpoints, candidates, selections)
    overlay_path = output_dir / OUTPUT_OVERLAY_NAME
    Image.fromarray(overlay).save(overlay_path)

    status_counts: Dict[str, int] = {}
    for item in selections:
        status = str(item["status"])
        status_counts[status] = status_counts.get(status, 0) + 1

    candidate_status_counts: Dict[str, int] = {}
    for item in candidates:
        status = str(item["status"])
        candidate_status_counts[status] = candidate_status_counts.get(status, 0) + 1

    payload: Dict[str, Any] = {
        "schema_version": "tail_joint_start_candidate_mvp_v1",
        "purpose": "双向头部-尾部起始片段候选；不是完整尾部结果，不得进入测量。",
        "sources": {
            "graph": str(graph_path.resolve()),
            "probability": str(probability_path.resolve()),
            "head_labels": None if head_labels_path is None else str(head_labels_path.resolve()),
            "head_results_fallback": None if head_results_path is None else str(head_results_path.resolve()),
            "merge": None if merge_path is None else str(merge_path.resolve()),
        },
        "parameters": {
            "tangent_distance_px": float(tangent_distance_px),
            "minimum_forward_px": float(minimum_forward_px),
            "maximum_direction_angle_deg": float(maximum_direction_angle_deg),
            "max_candidates_per_head": int(max_candidates_per_head),
            "strong_score_threshold": 0.76,
            "review_score_threshold": 0.56,
        },
        "summary": {
            "head_count": len(heads),
            "edge_endpoint_count": len(endpoints),
            "candidate_count": len(candidates),
            "candidate_status_counts": candidate_status_counts,
            "selection_status_counts": status_counts,
            "elapsed_seconds": float(time.perf_counter() - started),
            "head_orientation_available": bool(head_labels_path is not None),
            "exact_ray_hit_available": bool(head_labels_path is not None),
        },
        "selections": selections,
        "candidates": candidates,
        "outputs": {
            "overlay": str(overlay_path.resolve()),
            "json": str((output_dir / OUTPUT_JSON_NAME).resolve()),
        },
    }
    json_path = output_dir / OUTPUT_JSON_NAME
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="双向头部-尾部起始片段候选 MVP")
    parser.add_argument("--task-root", help="Analysis V2 单次运行目录")
    parser.add_argument("--field-id", help="例如 ZBFY022-C-1_RGB")
    parser.add_argument("--head-labels")
    parser.add_argument("--head-results", help="仅用于缺少 HeadFinalLabels 的旧调试包回放")
    parser.add_argument("--graph")
    parser.add_argument("--probability")
    parser.add_argument("--merge")
    parser.add_argument("--output-dir")
    parser.add_argument("--tangent-distance-px", type=float, default=18.0)
    parser.add_argument("--minimum-forward-px", type=float, default=18.0)
    parser.add_argument("--maximum-direction-angle-deg", type=float, default=75.0)
    parser.add_argument("--max-candidates-per-head", type=int, default=5)
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
        paths: Dict[str, Optional[Path]] = {
            "head_labels": resolved["head_labels"],
            "head_results": None,
            "graph": resolved["graph"],
            "probability": resolved["probability"],
            "merge": resolved["merge"],
            "output_dir": resolved["output_dir"],
        }
    else:
        if not args.graph or not args.probability or not args.output_dir:
            raise SystemExit("显式模式至少需要 --graph、--probability、--output-dir。")
        paths = {
            "head_labels": None if not args.head_labels else Path(args.head_labels).resolve(),
            "head_results": None if not args.head_results else Path(args.head_results).resolve(),
            "graph": Path(args.graph).resolve(),
            "probability": Path(args.probability).resolve(),
            "merge": None if not args.merge else Path(args.merge).resolve(),
            "output_dir": Path(args.output_dir).resolve(),
        }

    validate_paths(paths)
    payload = run_mvp(
        graph_path=paths["graph"],  # type: ignore[arg-type]
        probability_path=paths["probability"],  # type: ignore[arg-type]
        output_dir=paths["output_dir"],  # type: ignore[arg-type]
        head_labels_path=paths["head_labels"],
        head_results_path=paths["head_results"],
        merge_path=paths["merge"],
        tangent_distance_px=float(args.tangent_distance_px),
        minimum_forward_px=float(args.minimum_forward_px),
        maximum_direction_angle_deg=float(args.maximum_direction_angle_deg),
        max_candidates_per_head=int(args.max_candidates_per_head),
    )
    summary = payload["summary"]
    print("双向起始候选 MVP 完成。")
    print("heads={} endpoints={} candidates={}".format(
        summary["head_count"],
        summary["edge_endpoint_count"],
        summary["candidate_count"],
    ))
    print("selection_status_counts={}".format(summary["selection_status_counts"]))
    print("elapsed_seconds={:.3f}".format(float(summary["elapsed_seconds"])))
    print("输出目录：{}".format(paths["output_dir"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
