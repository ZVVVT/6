#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中心线驱动的尾部窄条区域预览 MVP（第四阶段）。

目的：
- 只读取第三阶段已精炼的高可信中心线；
- 沿中心线生成窄条尾部区域；
- 不再把整个八连通 fragment 作为尾部；
- 多条尾部区域重叠时按最近中心线分配像素。

固定输出：
- joint_region_preview.json
- joint_region_preview_overlay.png
- joint_region_preview_head_id_labels.tif

注意：本脚本只生成预览，不得替代 TailFinalLabels，不得进入测量。
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import cv2
    import numpy as np
    import tifffile
    from PIL import Image
    from skimage.morphology import skeletonize
except ImportError as exc:
    print("缺少依赖：{}".format(exc))
    raise SystemExit(1) from exc

OUTPUT_JSON_NAME = "joint_region_preview.json"
OUTPUT_OVERLAY_NAME = "joint_region_preview_overlay.png"
OUTPUT_LABELS_NAME = "joint_region_preview_head_id_labels.tif"
DEFAULT_ACCEPTED_STATUSES = ("trusted_anchor", "supported_candidate")


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


def deduplicate_consecutive(points_xy: np.ndarray) -> np.ndarray:
    points = np.asarray(points_xy, dtype=np.float32)
    if len(points) <= 1:
        return points
    keep = np.ones(len(points), dtype=bool)
    keep[1:] = np.any(np.abs(np.diff(points, axis=0)) > 1e-6, axis=1)
    return points[keep]


def rasterize_polyline(shape: Tuple[int, int], points_xy: np.ndarray) -> np.ndarray:
    points = np.rint(deduplicate_consecutive(points_xy)).astype(np.int32)
    result = np.zeros(shape, dtype=np.uint8)
    if len(points) == 1:
        x, y = int(points[0, 0]), int(points[0, 1])
        if 0 <= y < shape[0] and 0 <= x < shape[1]:
            result[y, x] = 1
    elif len(points) >= 2:
        cv2.polylines(result, [points.reshape(-1, 1, 2)], False, 1, 1, cv2.LINE_8)
    return result.astype(bool)




def densify_polyline(points_xy: np.ndarray, step_px: float = 1.0) -> np.ndarray:
    points = deduplicate_consecutive(points_xy)
    if len(points) < 2:
        return points
    lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(lengths)])
    total = float(cumulative[-1])
    if total <= 0.0:
        return points[:1]
    targets = np.arange(0.0, total, max(0.5, float(step_px)), dtype=np.float32)
    if len(targets) == 0 or targets[-1] < total:
        targets = np.concatenate([targets, [total]])
    dense = np.empty((len(targets), 2), dtype=np.float32)
    dense[:, 0] = np.interp(targets, cumulative, points[:, 0])
    dense[:, 1] = np.interp(targets, cumulative, points[:, 1])
    return dense


def oriented_ribbon_mask(shape: Tuple[int, int], points_xy: np.ndarray, radius: float) -> np.ndarray:
    """沿中心线局部法向生成窄条，避免各向同性膨胀把旁支整段带入。"""
    points = densify_polyline(points_xy, 1.0)
    result = np.zeros(shape, dtype=np.uint8)
    if len(points) == 0:
        return result.astype(bool)
    if len(points) == 1:
        x, y = np.rint(points[0]).astype(np.int32)
        if 0 <= y < shape[0] and 0 <= x < shape[1]:
            result[y, x] = 1
        return result.astype(bool)
    window = 4
    offsets = np.arange(-float(radius), float(radius) + 0.26, 0.5, dtype=np.float32)
    for index, point in enumerate(points):
        left = max(0, index - window)
        right = min(len(points) - 1, index + window)
        tangent = points[right] - points[left]
        norm = float(np.linalg.norm(tangent))
        if norm < 1e-6:
            continue
        tangent /= norm
        normal = np.asarray([-tangent[1], tangent[0]], dtype=np.float32)
        samples = point[None, :] + offsets[:, None] * normal[None, :]
        pixels = np.rint(samples).astype(np.int32)
        valid = (
            (pixels[:, 0] >= 0) & (pixels[:, 0] < shape[1])
            & (pixels[:, 1] >= 0) & (pixels[:, 1] < shape[0])
        )
        pixels = pixels[valid]
        result[pixels[:, 1], pixels[:, 0]] = 1
    # 中心线本身始终属于窄条。
    centreline = rasterize_polyline(shape, points)
    result[centreline] = 1
    return result.astype(bool)


def effective_degree(mask: np.ndarray) -> np.ndarray:
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
    degree += (padded[:-2, :-2] & ~up & ~left).astype(np.uint8)
    degree += (padded[:-2, 2:] & ~up & ~right).astype(np.uint8)
    degree += (padded[2:, :-2] & ~down & ~left).astype(np.uint8)
    degree += (padded[2:, 2:] & ~down & ~right).astype(np.uint8)
    degree *= center.astype(np.uint8)
    return degree


def component_stats(mask: np.ndarray) -> Dict[str, Any]:
    binary = mask.astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    component_sizes = [int(stats[index, cv2.CC_STAT_AREA]) for index in range(1, count)]
    if not component_sizes:
        return {
            "pixel_count": 0,
            "connected_component_count": 0,
            "largest_component_ratio": 0.0,
            "skeleton_pixel_count": 0,
            "endpoint_count": 0,
            "branch_pixel_count": 0,
        }
    skeleton = skeletonize(mask)
    degree = effective_degree(skeleton)
    return {
        "pixel_count": int(mask.sum()),
        "connected_component_count": int(count - 1),
        "largest_component_ratio": float(max(component_sizes) / max(sum(component_sizes), 1)),
        "skeleton_pixel_count": int(skeleton.sum()),
        "endpoint_count": int(np.sum(skeleton & (degree == 1))),
        "branch_pixel_count": int(np.sum(skeleton & (degree >= 3))),
    }


def choose_radius(record: Dict[str, Any], minimum: float, maximum: float) -> float:
    median_width = float(
        (record.get("width_signal") or {}).get("median_width_px", 0.0) or 0.0
    )
    if median_width <= 0.0:
        median_width = 6.0
    # 距离变换给出的宽度约为直径；增加少量余量，但限制最大范围，避免吃入旁支。
    return float(np.clip(0.5 * median_width + 0.75, minimum, maximum))


def local_candidate(
    shape: Tuple[int, int],
    points_xy: np.ndarray,
    support_mask: np.ndarray,
    radius: float,
    margin: int = 3,
) -> Dict[str, Any]:
    points = np.rint(deduplicate_consecutive(points_xy)).astype(np.int32)
    if len(points) < 2:
        raise ValueError("中心线点数不足。")
    height, width = shape
    pad = int(math.ceil(radius)) + int(margin)
    x0 = max(0, int(points[:, 0].min()) - pad)
    y0 = max(0, int(points[:, 1].min()) - pad)
    x1 = min(width, int(points[:, 0].max()) + pad + 1)
    y1 = min(height, int(points[:, 1].max()) + pad + 1)
    local_points = points.copy()
    local_points[:, 0] -= x0
    local_points[:, 1] -= y0
    centreline = rasterize_polyline((y1 - y0, x1 - x0), local_points)
    distance = cv2.distanceTransform((~centreline).astype(np.uint8), cv2.DIST_L2, 5)
    ribbon = oriented_ribbon_mask((y1 - y0, x1 - x0), local_points, float(radius))
    support = support_mask[y0:y1, x0:x1]
    candidate = ribbon & support
    return {
        "bbox": (x0, y0, x1, y1),
        "centreline": centreline,
        "distance": distance.astype(np.float32),
        "candidate": candidate,
        "tube": ribbon,
    }


def build_regions(
    records: Sequence[Dict[str, Any]],
    mask: np.ndarray,
    probability: np.ndarray,
    head_labels: Optional[np.ndarray],
    minimum_probability: float,
    radius_min: float,
    radius_max: float,
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    shape = mask.shape
    support_mask = mask | (probability >= float(minimum_probability))
    if head_labels is not None:
        support_mask &= head_labels == 0

    owner = np.zeros(shape, dtype=np.uint16)
    best_distance = np.full(shape, np.inf, dtype=np.float32)
    owner_score = np.full(shape, -np.inf, dtype=np.float32)
    prepared: List[Dict[str, Any]] = []

    # 先将所有中心线管道放入同一竞争空间；重叠像素优先归最近中心线，距离相同时归更高分候选。
    for record in records:
        head_id = int(record["head_id"])
        points = np.asarray(record.get("points_xy") or [], dtype=np.float32)
        radius = choose_radius(record, radius_min, radius_max)
        item = local_candidate(shape, points, support_mask, radius)
        x0, y0, x1, y1 = item["bbox"]
        candidate = item["candidate"]
        distance = item["distance"]
        score = float(record.get("refined_score", 0.0))
        local_best = best_distance[y0:y1, x0:x1]
        local_owner_score = owner_score[y0:y1, x0:x1]
        nearer = distance < (local_best - 1e-5)
        tie = np.abs(distance - local_best) <= 1e-5
        better = candidate & (nearer | (tie & (score > local_owner_score)))
        local_best[better] = distance[better]
        local_owner_score[better] = score
        owner[y0:y1, x0:x1][better] = np.uint16(head_id)
        prepared.append(
            {
                "record": record,
                "radius_px": radius,
                "bbox": item["bbox"],
                "candidate_pixel_count_before_conflict": int(candidate.sum()),
                "tube_pixel_count": int(item["tube"].sum()),
                "centreline_pixel_count": int(item["centreline"].sum()),
            }
        )

    results: List[Dict[str, Any]] = []
    for item in prepared:
        record = item["record"]
        head_id = int(record["head_id"])
        region = owner == head_id
        stats = component_stats(region)
        candidate_count = int(item["candidate_pixel_count_before_conflict"])
        final_count = int(stats["pixel_count"])
        points = np.asarray(record.get("points_xy") or [], dtype=np.float32)
        centreline_global = rasterize_polyline(shape, points)
        supported_centreline = centreline_global & support_mask
        centreline_coverage = float(
            np.sum(region & centreline_global) / max(int(supported_centreline.sum()), 1)
        )
        ys, xs = np.nonzero(region)
        bbox = None
        if len(xs):
            bbox = {
                "min_x": int(xs.min()),
                "min_y": int(ys.min()),
                "max_x": int(xs.max()),
                "max_y": int(ys.max()),
            }
        result = {
            "head_id": head_id,
            "source_status": str(record.get("refined_status", "")),
            "source_score": float(record.get("refined_score", 0.0)),
            "source_length_px": float(record.get("total_length_px", 0.0)),
            "radius_px": float(item["radius_px"]),
            "candidate_pixel_count_before_conflict": candidate_count,
            "final_pixel_count": final_count,
            "lost_to_nearer_centreline_pixel_count": int(max(0, candidate_count - final_count)),
            "centreline_support_coverage_ratio": centreline_coverage,
            "bbox": bbox,
            "region_topology": stats,
            "preview_warnings": [],
        }
        warnings = result["preview_warnings"]
        if final_count == 0:
            warnings.append("empty_after_support_and_conflict_resolution")
        if stats["connected_component_count"] > 3:
            warnings.append("many_disconnected_signal_components")
        if centreline_coverage < 0.70:
            warnings.append("low_supported_centreline_coverage")
        if candidate_count and final_count / candidate_count < 0.75:
            warnings.append("substantial_overlap_assigned_to_other_tail")
        results.append(result)

    return owner, results


def make_overlay(
    base_rgb: np.ndarray,
    labels: np.ndarray,
    records: Sequence[Dict[str, Any]],
) -> np.ndarray:
    overlay = base_rgb.copy()
    record_map = {int(item["head_id"]): item for item in records}
    fill_colors = {
        "trusted_anchor": np.asarray([65, 200, 255], dtype=np.float32),
        "supported_candidate": np.asarray([90, 130, 255], dtype=np.float32),
    }
    line_colors = {
        "trusted_anchor": (245, 245, 245),
        "supported_candidate": (255, 210, 80),
    }
    for head_id, record in record_map.items():
        region = labels == int(head_id)
        if not np.any(region):
            continue
        status = str(record.get("refined_status", "trusted_anchor"))
        color = fill_colors.get(status, fill_colors["trusted_anchor"])
        original = overlay[region].astype(np.float32)
        overlay[region] = np.clip(0.45 * original + 0.55 * color, 0, 255).astype(np.uint8)
        contours, _ = cv2.findContours(region.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(overlay, contours, -1, (255, 255, 255), 1, cv2.LINE_AA)

    for record in records:
        points = np.rint(np.asarray(record.get("points_xy") or [], dtype=np.float32)).astype(np.int32)
        if len(points) < 2:
            continue
        status = str(record.get("refined_status", "trusted_anchor"))
        color = line_colors.get(status, (255, 255, 255))
        path = points.reshape(-1, 1, 2)
        cv2.polylines(overlay, [path], False, (5, 5, 5), 3, cv2.LINE_AA)
        cv2.polylines(overlay, [path], False, color, 1, cv2.LINE_AA)
        start = (int(points[0, 0]), int(points[0, 1]))
        cv2.circle(overlay, start, 4, (5, 5, 5), -1, cv2.LINE_AA)
        cv2.circle(overlay, start, 2, color, -1, cv2.LINE_AA)
        cv2.putText(
            overlay,
            "H{}".format(int(record["head_id"])),
            (start[0] + 5, start[1] - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.34,
            color,
            1,
            cv2.LINE_AA,
        )

    cv2.rectangle(overlay, (8, 8), (610, 78), (0, 0, 0), -1)
    cv2.putText(overlay, "Cyan region + white line: strict anchor", (18, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (80, 225, 255), 1, cv2.LINE_AA)
    cv2.putText(overlay, "Blue region + yellow line: adaptive support", (18, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (90, 150, 255), 1, cv2.LINE_AA)
    cv2.putText(overlay, "Preview only - not TailFinalLabels", (330, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 255, 255), 1, cv2.LINE_AA)
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
        "refined_json": (root / "segmentation" / "tail_joint_refined_mvp" / field_id / "joint_chain_refined.json").resolve(),
        "mask": (field_root / "stage1" / "balanced_mask_uint8.tif").resolve(),
        "probability": (field_root / "stage1" / "02_probability_uint16.tif").resolve(),
        "head_labels": head_path.resolve() if head_path.is_file() else None,
        "merge": find_single("{}_Merge.*".format(field_id), root / "input"),
        "output_dir": (output_dir or (root / "segmentation" / "tail_joint_region_preview_mvp" / field_id)).resolve(),
    }


def run_mvp(
    *,
    refined_json_path: Path,
    mask_path: Path,
    probability_path: Path,
    output_dir: Path,
    merge_path: Optional[Path] = None,
    head_labels_path: Optional[Path] = None,
    accepted_statuses: Sequence[str] = DEFAULT_ACCEPTED_STATUSES,
    minimum_probability: float = 0.08,
    radius_min: float = 2.0,
    radius_max: float = 5.0,
) -> Dict[str, Any]:
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    refined_payload = json.loads(refined_json_path.read_text(encoding="utf-8"))
    records = [
        dict(item)
        for item in list(refined_payload.get("refined_chains") or [])
        if str(item.get("refined_status")) in set(str(value) for value in accepted_statuses)
    ]
    if not records:
        raise RuntimeError("精炼结果中没有可用于区域预览的高可信中心线。")

    mask = read_image(mask_path)
    if mask.ndim > 2:
        mask = np.squeeze(mask)
    mask = mask > 0
    probability = normalize_probability(read_image(probability_path))
    if probability.ndim > 2:
        probability = np.squeeze(probability)
    if mask.shape != probability.shape:
        raise ValueError("掩模与概率图尺寸不一致。")

    head_labels: Optional[np.ndarray] = None
    if head_labels_path is not None:
        head_labels = read_image(head_labels_path)
        if head_labels.ndim > 2:
            head_labels = np.squeeze(head_labels)
        if head_labels.shape != mask.shape:
            raise ValueError("头部标签与尾部图尺寸不一致。")

    labels, region_results = build_regions(
        records,
        mask,
        probability,
        head_labels,
        minimum_probability,
        radius_min,
        radius_max,
    )
    labels_path = output_dir / OUTPUT_LABELS_NAME
    tifffile.imwrite(str(labels_path), labels.astype(np.uint16))

    overlay_path: Optional[Path] = None
    if merge_path is not None:
        base_rgb = to_uint8_rgb(read_image(merge_path))
        if base_rgb.shape[:2] != mask.shape:
            raise ValueError("Merge 图与尾部图尺寸不一致。")
        overlay = make_overlay(base_rgb, labels, records)
        overlay_path = output_dir / OUTPUT_OVERLAY_NAME
        Image.fromarray(overlay).save(overlay_path)

    warning_counts: Dict[str, int] = {}
    for item in region_results:
        for warning in item["preview_warnings"]:
            warning_counts[warning] = int(warning_counts.get(warning, 0) + 1)

    payload = {
        "schema_version": "tail_joint_region_preview_mvp_v1",
        "purpose": "从精炼中心线生成窄条尾部区域预览；不是 TailFinalLabels，不得进入测量。",
        "sources": {
            "refined_json": str(refined_json_path.resolve()),
            "balanced_mask": str(mask_path.resolve()),
            "probability": str(probability_path.resolve()),
            "head_labels": str(head_labels_path.resolve()) if head_labels_path is not None else None,
            "merge": str(merge_path.resolve()) if merge_path is not None else None,
        },
        "parameters": {
            "accepted_statuses": list(accepted_statuses),
            "minimum_probability": float(minimum_probability),
            "radius_min_px": float(radius_min),
            "radius_max_px": float(radius_max),
            "overlap_resolution": "nearest_centreline_then_higher_refined_score",
            "support_rule": "balanced_mask OR probability_threshold; head labels excluded",
        },
        "summary": {
            "selected_chain_count": int(len(records)),
            "nonzero_head_id_count": int(len(np.unique(labels[labels > 0]))),
            "total_region_pixel_count": int(np.sum(labels > 0)),
            "overlap_lost_pixel_count": int(sum(item["lost_to_nearer_centreline_pixel_count"] for item in region_results)),
            "warning_counts": warning_counts,
            "elapsed_seconds": float(time.perf_counter() - started),
        },
        "regions": region_results,
        "outputs": {
            "labels": str(labels_path.resolve()),
            "overlay": str(overlay_path.resolve()) if overlay_path is not None else None,
        },
    }
    json_path = output_dir / OUTPUT_JSON_NAME
    payload["outputs"]["json"] = str(json_path.resolve())
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="中心线驱动的尾部窄条区域预览 MVP")
    parser.add_argument("--task-root")
    parser.add_argument("--field-id")
    parser.add_argument("--refined-json")
    parser.add_argument("--mask")
    parser.add_argument("--probability")
    parser.add_argument("--head-labels")
    parser.add_argument("--merge")
    parser.add_argument("--output-dir")
    parser.add_argument("--minimum-probability", type=float, default=0.08)
    parser.add_argument("--radius-min", type=float, default=2.0)
    parser.add_argument("--radius-max", type=float, default=5.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.task_root:
        if not args.field_id:
            raise SystemExit("使用 --task-root 时必须提供 --field-id。")
        resolved = resolve_task_inputs(
            Path(args.task_root),
            str(args.field_id),
            Path(args.output_dir) if args.output_dir else None,
        )
    else:
        required = [args.refined_json, args.mask, args.probability, args.output_dir]
        if not all(required):
            raise SystemExit("未使用 --task-root 时必须提供 --refined-json、--mask、--probability 和 --output-dir。")
        resolved = {
            "refined_json": Path(args.refined_json).resolve(),
            "mask": Path(args.mask).resolve(),
            "probability": Path(args.probability).resolve(),
            "head_labels": Path(args.head_labels).resolve() if args.head_labels else None,
            "merge": Path(args.merge).resolve() if args.merge else None,
            "output_dir": Path(args.output_dir).resolve(),
        }

    for key in ("refined_json", "mask", "probability"):
        path = resolved[key]
        if path is None or not Path(path).is_file():
            raise FileNotFoundError("{} 不存在：{}".format(key, path))
    for key in ("head_labels", "merge"):
        path = resolved.get(key)
        if path is not None and not Path(path).is_file():
            raise FileNotFoundError("{} 不存在：{}".format(key, path))

    result = run_mvp(
        refined_json_path=Path(resolved["refined_json"]),
        mask_path=Path(resolved["mask"]),
        probability_path=Path(resolved["probability"]),
        head_labels_path=Path(resolved["head_labels"]) if resolved.get("head_labels") else None,
        merge_path=Path(resolved["merge"]) if resolved.get("merge") else None,
        output_dir=Path(resolved["output_dir"]),
        minimum_probability=float(args.minimum_probability),
        radius_min=float(args.radius_min),
        radius_max=float(args.radius_max),
    )
    print("区域预览完成：selected={}，pixels={}，elapsed={:.3f}s".format(
        result["summary"]["selected_chain_count"],
        result["summary"]["total_region_pixel_count"],
        result["summary"]["elapsed_seconds"],
    ))
    print("输出：{}".format(result["outputs"]["json"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
