#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Analysis V2 联合尾部草稿导出 MVP（第五阶段）。

把第四阶段的 Head-ID 区域预览转换为人工校准可消费的草稿契约：
- 连续的尾部对象标签（TailDraftLabels）；
- 保留头部编号的尾部标签（TailDraftHeadIdLabels）；
- 对应阳性头部标签（TailDraftPositiveHeadLabels）；
- 对象清单、校验信息和叠加图。

注意：所有输出均为 Draft，严禁直接进入测量或发布。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import cv2
    import numpy as np
    import tifffile
    from PIL import Image
except ImportError as exc:
    print("缺少依赖：{}".format(exc))
    raise SystemExit(1) from exc

SCHEMA_VERSION = "tail_joint_draft_export_mvp_v1"


def read_image(path: Path) -> np.ndarray:
    with Image.open(str(path)) as image:
        return np.asarray(image)


def robust_normalize(image: np.ndarray, low_p: float = 0.2, high_p: float = 99.8) -> np.ndarray:
    array = image.astype(np.float32, copy=False)
    low, high = np.percentile(array, [low_p, high_p])
    if high <= low:
        return np.zeros(array.shape, dtype=np.float32)
    return np.clip((array - low) / (high - low), 0.0, 1.0)


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


def find_single(pattern: str, directory: Path) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError("目录 {} 中找不到 {}。".format(directory, pattern))
    return matches[0].resolve()


def load_uint16(path: Path) -> np.ndarray:
    array = tifffile.imread(str(path))
    if array.ndim > 2:
        array = np.squeeze(array)
    if array.ndim != 2:
        raise ValueError("标签必须为二维图像：{}".format(path))
    if not np.issubdtype(array.dtype, np.integer):
        raise ValueError("标签必须为整数类型：{}".format(path))
    if np.any(array < 0) or int(array.max(initial=0)) > 65535:
        raise ValueError("标签值超出 uint16 范围：{}".format(path))
    return array.astype(np.uint16, copy=False)


def bbox_from_mask(mask: np.ndarray) -> Optional[Dict[str, int]]:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return {
        "min_x": int(xs.min()),
        "min_y": int(ys.min()),
        "max_x": int(xs.max()),
        "max_y": int(ys.max()),
    }


def make_overlay(
    merge_rgb: np.ndarray,
    draft_labels: np.ndarray,
    objects: List[Dict[str, Any]],
) -> np.ndarray:
    overlay = merge_rgb.copy()
    by_object = {int(item["object_id"]): item for item in objects}
    status_colors = {
        "trusted_anchor": np.asarray([60, 205, 255], dtype=np.float32),
        "supported_candidate": np.asarray([90, 135, 255], dtype=np.float32),
    }
    line_colors = {
        "trusted_anchor": (245, 245, 245),
        "supported_candidate": (255, 215, 75),
    }

    for object_id, item in by_object.items():
        region = draft_labels == object_id
        if not np.any(region):
            continue
        status = str(item.get("source_status", "trusted_anchor"))
        fill = status_colors.get(status, status_colors["trusted_anchor"])
        original = overlay[region].astype(np.float32)
        overlay[region] = np.clip(0.45 * original + 0.55 * fill, 0, 255).astype(np.uint8)
        contours, _ = cv2.findContours(
            region.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        cv2.drawContours(overlay, contours, -1, (255, 255, 255), 1, cv2.LINE_AA)
        bbox = item.get("bbox")
        if bbox:
            anchor = (int(bbox["min_x"]), max(12, int(bbox["min_y"]) - 4))
            text = "O{} H{}".format(object_id, int(item["head_id"]))
            cv2.putText(
                overlay,
                text,
                anchor,
                cv2.FONT_HERSHEY_SIMPLEX,
                0.34,
                line_colors.get(status, (255, 255, 255)),
                1,
                cv2.LINE_AA,
            )

    cv2.rectangle(overlay, (8, 8), (650, 78), (0, 0, 0), -1)
    cv2.putText(
        overlay,
        "TailDraft only - object ID and matched Head ID",
        (18, 31),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        overlay,
        "Not TailFinalLabels; manual calibration required",
        (18, 57),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (255, 215, 75),
        1,
        cv2.LINE_AA,
    )
    return overlay


def build_draft(
    preview_head_id_labels: np.ndarray,
    preview_payload: Dict[str, Any],
    refined_payload: Dict[str, Any],
    head_labels: Optional[np.ndarray],
) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray], List[Dict[str, Any]], Dict[str, Any]]:
    if preview_head_id_labels.ndim != 2:
        raise ValueError("区域预览标签必须为二维。")
    if head_labels is not None and head_labels.shape != preview_head_id_labels.shape:
        raise ValueError("头部标签与尾部区域预览尺寸不一致。")

    preview_by_head = {
        int(item["head_id"]): dict(item)
        for item in list(preview_payload.get("regions") or [])
    }
    refined_by_head = {
        int(item["head_id"]): dict(item)
        for item in list(refined_payload.get("refined_chains") or [])
        if str(item.get("refined_status")) in {"trusted_anchor", "supported_candidate"}
    }

    label_head_ids = sorted(int(value) for value in np.unique(preview_head_id_labels) if int(value) > 0)
    missing_preview_records = [head_id for head_id in label_head_ids if head_id not in preview_by_head]
    missing_refined_records = [head_id for head_id in label_head_ids if head_id not in refined_by_head]
    if missing_preview_records:
        raise ValueError("区域预览 JSON 缺少 Head：{}".format(missing_preview_records))
    if missing_refined_records:
        raise ValueError("精炼 JSON 缺少 Head：{}".format(missing_refined_records))

    if head_labels is not None:
        available_heads = {int(value) for value in np.unique(head_labels) if int(value) > 0}
        missing_heads = [head_id for head_id in label_head_ids if head_id not in available_heads]
        if missing_heads:
            raise ValueError("尾部草稿引用了不存在的头部编号：{}".format(missing_heads))

    draft_labels = np.zeros_like(preview_head_id_labels, dtype=np.uint16)
    draft_head_id_labels = np.zeros_like(preview_head_id_labels, dtype=np.uint16)
    draft_positive_head_labels = (
        np.zeros_like(head_labels, dtype=np.uint16) if head_labels is not None else None
    )

    objects: List[Dict[str, Any]] = []
    for object_id, head_id in enumerate(label_head_ids, start=1):
        region = preview_head_id_labels == head_id
        if not np.any(region):
            continue
        preview = preview_by_head[head_id]
        refined = refined_by_head[head_id]
        draft_labels[region] = np.uint16(object_id)
        draft_head_id_labels[region] = np.uint16(head_id)
        if draft_positive_head_labels is not None:
            draft_positive_head_labels[head_labels == head_id] = np.uint16(head_id)

        topology = dict(preview.get("region_topology") or {})
        review_reasons = list(preview.get("preview_warnings") or [])
        branch_pixel_count = int(topology.get("branch_pixel_count", 0) or 0)
        endpoint_count = int(topology.get("endpoint_count", 0) or 0)
        component_count = int(topology.get("connected_component_count", 0) or 0)
        if branch_pixel_count > 0:
            review_reasons.append("region_skeleton_has_branch_pixels")
        if component_count == 1 and endpoint_count not in (0, 2):
            review_reasons.append("single_component_endpoint_count_unusual")
        if component_count > 3:
            review_reasons.append("many_disconnected_signal_components")

        objects.append(
            {
                "object_id": int(object_id),
                "head_id": int(head_id),
                "source_status": str(refined.get("refined_status", "")),
                "source_score": float(refined.get("refined_score", 0.0)),
                "source_length_px": float(refined.get("total_length_px", 0.0)),
                "pixel_count": int(region.sum()),
                "bbox": bbox_from_mask(region),
                "connected_component_count": component_count,
                "largest_component_ratio": float(topology.get("largest_component_ratio", 0.0) or 0.0),
                "skeleton_pixel_count": int(topology.get("skeleton_pixel_count", 0) or 0),
                "endpoint_count": endpoint_count,
                "branch_pixel_count": branch_pixel_count,
                "centreline_support_coverage_ratio": float(
                    preview.get("centreline_support_coverage_ratio", 0.0)
                ),
                "preview_warnings": list(preview.get("preview_warnings") or []),
                "draft_review_reasons": sorted(set(review_reasons)),
                "requires_manual_calibration": True,
            }
        )

    object_ids = sorted(int(value) for value in np.unique(draft_labels) if int(value) > 0)
    expected_ids = list(range(1, len(objects) + 1))
    validation = {
        "object_ids_contiguous": object_ids == expected_ids,
        "object_count": int(len(objects)),
        "head_id_count": int(len(np.unique(draft_head_id_labels[draft_head_id_labels > 0]))),
        "one_object_per_head": int(len(objects)) == int(len(set(item["head_id"] for item in objects))),
        "draft_and_head_id_support_equal": bool(
            np.array_equal(draft_labels > 0, draft_head_id_labels > 0)
        ),
        "positive_head_labels_available": draft_positive_head_labels is not None,
        "ready_for_manual_calibration": True,
        "ready_for_measurement": False,
    }
    required_checks = [
        validation["object_ids_contiguous"],
        validation["one_object_per_head"],
        validation["draft_and_head_id_support_equal"],
    ]
    validation["valid"] = bool(all(required_checks))
    if not validation["valid"]:
        raise RuntimeError("尾部草稿契约校验失败：{}".format(validation))

    return (
        draft_labels,
        draft_head_id_labels,
        draft_positive_head_labels,
        objects,
        validation,
    )


def resolve_task_inputs(task_root: Path, field_id: str, output_dir: Optional[Path]) -> Dict[str, Optional[Path]]:
    root = task_root.resolve()
    preview_dir = root / "segmentation" / "tail_joint_region_preview_mvp" / field_id
    refined_dir = root / "segmentation" / "tail_joint_refined_mvp" / field_id
    head_path = root / "calibration" / "head" / "{}_HeadFinalLabels.tif".format(field_id)
    return {
        "preview_json": (preview_dir / "joint_region_preview.json").resolve(),
        "preview_labels": (preview_dir / "joint_region_preview_head_id_labels.tif").resolve(),
        "refined_json": (refined_dir / "joint_chain_refined.json").resolve(),
        "head_labels": head_path.resolve() if head_path.is_file() else None,
        "merge": find_single("{}_Merge.*".format(field_id), root / "input"),
        "output_dir": (output_dir or (root / "segmentation" / "tail_joint_draft_mvp" / field_id)).resolve(),
    }


def run_mvp(
    *,
    preview_json_path: Path,
    preview_labels_path: Path,
    refined_json_path: Path,
    output_dir: Path,
    field_id: str,
    head_labels_path: Optional[Path] = None,
    merge_path: Optional[Path] = None,
) -> Dict[str, Any]:
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)

    preview_payload = json.loads(preview_json_path.read_text(encoding="utf-8"))
    refined_payload = json.loads(refined_json_path.read_text(encoding="utf-8"))
    preview_labels = load_uint16(preview_labels_path)
    head_labels = load_uint16(head_labels_path) if head_labels_path is not None else None

    (
        draft_labels,
        draft_head_id_labels,
        draft_positive_head_labels,
        objects,
        validation,
    ) = build_draft(preview_labels, preview_payload, refined_payload, head_labels)

    labels_path = output_dir / "{}_TailDraftLabels.tif".format(field_id)
    head_id_path = output_dir / "{}_TailDraftHeadIdLabels.tif".format(field_id)
    positive_head_path = output_dir / "{}_TailDraftPositiveHeadLabels.tif".format(field_id)
    objects_path = output_dir / "{}_TailDraftObjects.json".format(field_id)
    manifest_path = output_dir / "tail_joint_draft_manifest.json"
    overlay_path: Optional[Path] = None

    tifffile.imwrite(str(labels_path), draft_labels.astype(np.uint16))
    tifffile.imwrite(str(head_id_path), draft_head_id_labels.astype(np.uint16))
    if draft_positive_head_labels is not None:
        tifffile.imwrite(str(positive_head_path), draft_positive_head_labels.astype(np.uint16))

    objects_payload = {
        "schema_version": SCHEMA_VERSION,
        "field_id": str(field_id),
        "draft": True,
        "requires_manual_calibration": True,
        "objects": objects,
    }
    objects_path.write_text(
        json.dumps(objects_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if merge_path is not None:
        merge_rgb = to_uint8_rgb(read_image(merge_path))
        if merge_rgb.shape[:2] != draft_labels.shape:
            raise ValueError("Merge 图与草稿标签尺寸不一致。")
        overlay = make_overlay(merge_rgb, draft_labels, objects)
        overlay_path = output_dir / "tail_joint_draft_overlay.png"
        Image.fromarray(overlay).save(overlay_path)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "purpose": "人工尾部校准前的草稿标签契约；不得直接测量或发布。",
        "field_id": str(field_id),
        "sources": {
            "preview_json": str(preview_json_path.resolve()),
            "preview_labels": str(preview_labels_path.resolve()),
            "refined_json": str(refined_json_path.resolve()),
            "head_labels": str(head_labels_path.resolve()) if head_labels_path is not None else None,
            "merge": str(merge_path.resolve()) if merge_path is not None else None,
        },
        "summary": {
            "draft_object_count": int(len(objects)),
            "trusted_anchor_count": int(sum(item["source_status"] == "trusted_anchor" for item in objects)),
            "supported_candidate_count": int(sum(item["source_status"] == "supported_candidate" for item in objects)),
            "total_draft_pixel_count": int(np.sum(draft_labels > 0)),
            "objects_with_topology_review_reasons": int(
                sum(bool(item.get("draft_review_reasons")) for item in objects)
            ),
            "elapsed_seconds": float(time.perf_counter() - started),
        },
        "validation": validation,
        "outputs": {
            "draft_labels": str(labels_path.resolve()),
            "draft_head_id_labels": str(head_id_path.resolve()),
            "draft_positive_head_labels": str(positive_head_path.resolve()) if draft_positive_head_labels is not None else None,
            "draft_objects": str(objects_path.resolve()),
            "overlay": str(overlay_path.resolve()) if overlay_path is not None else None,
            "manifest": str(manifest_path.resolve()),
        },
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analysis V2 联合尾部草稿导出 MVP")
    parser.add_argument("--task-root")
    parser.add_argument("--field-id", required=True)
    parser.add_argument("--preview-json")
    parser.add_argument("--preview-labels")
    parser.add_argument("--refined-json")
    parser.add_argument("--head-labels")
    parser.add_argument("--merge")
    parser.add_argument("--output-dir")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.task_root:
        resolved = resolve_task_inputs(
            Path(args.task_root),
            str(args.field_id),
            Path(args.output_dir) if args.output_dir else None,
        )
    else:
        required = [args.preview_json, args.preview_labels, args.refined_json, args.output_dir]
        if not all(required):
            raise SystemExit(
                "未使用 --task-root 时必须提供 --preview-json、--preview-labels、--refined-json 和 --output-dir。"
            )
        resolved = {
            "preview_json": Path(args.preview_json).resolve(),
            "preview_labels": Path(args.preview_labels).resolve(),
            "refined_json": Path(args.refined_json).resolve(),
            "head_labels": Path(args.head_labels).resolve() if args.head_labels else None,
            "merge": Path(args.merge).resolve() if args.merge else None,
            "output_dir": Path(args.output_dir).resolve(),
        }

    for key in ("preview_json", "preview_labels", "refined_json"):
        path = resolved[key]
        if path is None or not Path(path).is_file():
            raise FileNotFoundError("缺少输入 {}：{}".format(key, path))

    payload = run_mvp(
        preview_json_path=Path(resolved["preview_json"]),
        preview_labels_path=Path(resolved["preview_labels"]),
        refined_json_path=Path(resolved["refined_json"]),
        output_dir=Path(resolved["output_dir"]),
        field_id=str(args.field_id),
        head_labels_path=Path(resolved["head_labels"]) if resolved.get("head_labels") else None,
        merge_path=Path(resolved["merge"]) if resolved.get("merge") else None,
    )
    print("联合尾部草稿导出完成。")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print("输出目录：{}".format(resolved["output_dir"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
