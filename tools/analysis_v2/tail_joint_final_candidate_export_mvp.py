#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 TailDraft 人工编辑结果转换为独立的 TailFinal 候选契约。

本脚本只生成候选文件，不写入正式 calibration/tail，不触发测量、发布或数据库。

默认输入：
- calibration/tail_joint_editor_mvp/<field_id>/edited_tail_results.json
- calibration/tail_joint_editor_mvp/<field_id>/edited_tail_regions_head_id_uint16.tif
- calibration/tail_joint_editor_mvp/<field_id>/edited_tail_head_id_labels_uint16.tif
- calibration/tail_joint_editor_mvp/<field_id>/edited_tail_centerlines_uint16.tif
- calibration/tail_joint_editor_mvp/<field_id>/edited_tail_region_conflicts.json
- calibration/head/<field_id>_HeadFinalLabels.tif

默认输出：
- calibration/tail_joint_final_candidate_mvp/<field_id>/
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image

VERSION = "tail_joint_final_candidate_export_mvp_v1_1_pixel_sync"
ACCEPTED_STATUSES = {"trusted_auto", "user_accepted"}


def read_image(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image)


def read_label_image(path: Path, expected_shape: Optional[Tuple[int, int]] = None) -> np.ndarray:
    array = read_image(path)
    if array.ndim > 2:
        array = np.squeeze(array)
    if array.ndim != 2:
        raise ValueError(f"标签图必须为二维：{path}，shape={array.shape}")
    if expected_shape is not None and tuple(array.shape) != tuple(expected_shape):
        raise ValueError(
            f"标签图尺寸不一致：{path}，shape={array.shape}，expected={expected_shape}"
        )
    if not np.issubdtype(array.dtype, np.integer):
        raise ValueError(f"标签图必须为整数类型：{path}，dtype={array.dtype}")
    if np.any(array < 0):
        raise ValueError(f"标签图不能包含负数：{path}")
    return array.astype(np.uint16, copy=False)


def positive_ids(array: np.ndarray) -> List[int]:
    return [int(value) for value in np.unique(array) if int(value) > 0]


def load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在：{path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON根节点必须是对象：{path}")
    return payload


def to_uint8_rgb(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        channels = [image, image, image]
    elif image.ndim == 3 and image.shape[2] >= 3:
        channels = [image[..., 0], image[..., 1], image[..., 2]]
    else:
        raise ValueError(f"无法转换为RGB：shape={image.shape}")

    output = np.zeros(channels[0].shape + (3,), dtype=np.uint8)
    for index, channel in enumerate(channels):
        array = channel.astype(np.float32, copy=False)
        low, high = np.percentile(array, [0.2, 99.8])
        if high <= low:
            output[..., index] = 0
        else:
            output[..., index] = np.round(
                np.clip((array - low) / (high - low), 0.0, 1.0) * 255.0
            ).astype(np.uint8)
    return output


def find_merge(task_root: Path, field_id: str) -> Optional[Path]:
    input_dir = task_root / "input"
    matches: List[Path] = []
    for suffix in ("tif", "tiff", "png", "jpg", "jpeg"):
        matches.extend(input_dir.glob(f"{field_id}_Merge.{suffix}"))
        matches.extend(input_dir.glob(f"{field_id}_MERGE.{suffix}"))
    unique = sorted({path.resolve() for path in matches if path.is_file()})
    return unique[0] if unique else None


def mask_boundary(mask: np.ndarray) -> np.ndarray:
    kernel = np.ones((3, 3), dtype=np.uint8)
    eroded = cv2.erode(mask.astype(np.uint8), kernel, iterations=1) > 0
    return mask & ~eroded


def selected_records(results_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    records = list(results_payload.get("results") or [])
    selected: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("edited_tail_results.json 的 results 存在非对象记录。")
        status = str(record.get("current_status") or "").strip()
        deleted = bool(record.get("deleted", False))
        region_pixel_count = int(record.get("region_pixel_count") or 0)
        if deleted or region_pixel_count <= 0:
            continue
        if status not in ACCEPTED_STATUSES:
            raise ValueError(
                "存在已选区域但状态未确认的记录：head_id={}，status={}".format(
                    record.get("head_id"), status
                )
            )
        selected.append(record)
    selected.sort(key=lambda item: int(item.get("head_id") or 0))
    return selected


def validate_inputs(
    head_final_labels: np.ndarray,
    edited_regions_head_id: np.ndarray,
    edited_centerline_head_id: np.ndarray,
    edited_centerlines_binary: np.ndarray,
    results_payload: Dict[str, Any],
    conflicts_payload: Dict[str, Any],
) -> Dict[str, Any]:
    selected = selected_records(results_payload)
    if not selected:
        raise ValueError("人工编辑结果中没有可接受的尾部。")

    head_ids = [int(record.get("head_id") or 0) for record in selected]
    if any(head_id <= 0 for head_id in head_ids):
        raise ValueError("存在无效 Head ID。")
    if len(set(head_ids)) != len(head_ids):
        raise ValueError("人工编辑结果存在重复 Head ID。")

    conflict_rows = list(conflicts_payload.get("conflicts") or [])
    if conflict_rows:
        raise ValueError(f"仍存在区域冲突，数量={len(conflict_rows)}。")

    region_ids = positive_ids(edited_regions_head_id)
    centerline_head_ids = positive_ids(edited_centerline_head_id)
    expected_ids = sorted(head_ids)
    if region_ids != expected_ids:
        raise ValueError(
            f"区域标签 Head ID 与已接受记录不一致：regions={region_ids}，expected={expected_ids}"
        )
    if centerline_head_ids != expected_ids:
        raise ValueError(
            "中心线 Head ID 与已接受记录不一致：centerlines={}，expected={}".format(
                centerline_head_ids, expected_ids
            )
        )

    if not np.array_equal(edited_centerlines_binary > 0, edited_centerline_head_id > 0):
        raise ValueError("二值中心线和 Head-ID 中心线的支持区域不一致。")

    full_head_ids = positive_ids(head_final_labels)
    missing_heads = sorted(set(expected_ids) - set(full_head_ids))
    if missing_heads:
        raise ValueError(f"HeadFinalLabels 缺少已接受 Head ID：{missing_heads}")

    pixel_mismatches: List[Dict[str, int]] = []
    centerline_outside_region: List[Dict[str, int]] = []
    selected_by_head = {int(record["head_id"]): record for record in selected}

    for head_id in expected_ids:
        actual_pixels = int(np.sum(edited_regions_head_id == head_id))
        recorded_pixels = int(selected_by_head[head_id].get("region_pixel_count") or 0)
        if actual_pixels != recorded_pixels:
            pixel_mismatches.append(
                {
                    "head_id": head_id,
                    "recorded_pixels": recorded_pixels,
                    "actual_pixels": actual_pixels,
                }
            )
            # 最终区域TIFF是测量与发布的唯一事实来源。
            # 编辑器V2.7+会补充路径窄带内无碎片编号的真实信号像素，
            # 旧JSON可能仍保留补充前的像素数；在此同步而不是误判失败。
            selected_by_head[head_id]["region_pixel_count"] = actual_pixels

        outside = int(
            np.sum(
                (edited_centerline_head_id == head_id)
                & (edited_regions_head_id != head_id)
            )
        )
        if outside > 0:
            centerline_outside_region.append(
                {"head_id": head_id, "pixel_count": outside}
            )

    return {
        "selected_records": selected,
        "head_ids": expected_ids,
        "full_head_count": len(full_head_ids),
        "conflict_count": 0,
        "region_pixel_count_sync_count": len(pixel_mismatches),
        "region_pixel_count_sync_rows": pixel_mismatches,
        "region_pixel_count_source": "edited_tail_regions_head_id_uint16.tif",
        "centerline_outside_region": centerline_outside_region,
        "centerline_outside_region_pixel_count": int(
            sum(row["pixel_count"] for row in centerline_outside_region)
        ),
    }


def build_candidate_arrays(
    head_final_labels: np.ndarray,
    edited_regions_head_id: np.ndarray,
    head_ids: Sequence[int],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[Dict[str, int]]]:
    tail_final = np.zeros(edited_regions_head_id.shape, dtype=np.uint16)
    tail_head_ids = np.zeros(edited_regions_head_id.shape, dtype=np.uint16)
    positive_heads = np.zeros(edited_regions_head_id.shape, dtype=np.uint16)
    objects: List[Dict[str, int]] = []

    for object_id, head_id in enumerate(head_ids, start=1):
        region_mask = edited_regions_head_id == int(head_id)
        head_mask = head_final_labels == int(head_id)
        if not np.any(region_mask):
            raise ValueError(f"Head ID {head_id} 没有尾部区域像素。")
        if not np.any(head_mask):
            raise ValueError(f"Head ID {head_id} 没有头部标签像素。")

        tail_final[region_mask] = int(object_id)
        tail_head_ids[region_mask] = int(head_id)
        positive_heads[head_mask] = int(object_id)
        objects.append(
            {
                "object_id": int(object_id),
                "head_id": int(head_id),
                "pixel_count": int(np.sum(region_mask)),
                "head_pixel_count": int(np.sum(head_mask)),
            }
        )

    expected = list(range(1, len(head_ids) + 1))
    if positive_ids(tail_final) != expected:
        raise RuntimeError("候选 TailFinalLabels 不是连续 1...N。")
    if positive_ids(positive_heads) != expected:
        raise RuntimeError("候选 TailPositiveHeadLabels 不是连续 1...N。")
    if positive_ids(tail_head_ids) != list(head_ids):
        raise RuntimeError("候选 TailFinalHeadIdLabels 与 Head ID 列表不一致。")

    return tail_final, tail_head_ids, positive_heads, objects


def make_overlay(
    base_rgb: np.ndarray,
    tail_final: np.ndarray,
    positive_heads: np.ndarray,
) -> np.ndarray:
    overlay = base_rgb.copy()
    tail_boundary = mask_boundary(tail_final > 0)
    head_boundary = mask_boundary(positive_heads > 0)
    overlay[tail_boundary] = np.asarray([80, 220, 255], dtype=np.uint8)
    overlay[head_boundary] = np.asarray([255, 70, 70], dtype=np.uint8)
    return overlay


def write_candidate(
    task_root: Path,
    field_id: str,
    output_dir: Path,
) -> Dict[str, Any]:
    started = time.perf_counter()
    editor_dir = task_root / "calibration" / "tail_joint_editor_mvp" / field_id
    head_final_path = (
        task_root / "calibration" / "head" / f"{field_id}_HeadFinalLabels.tif"
    )

    paths = {
        "edited_results": editor_dir / "edited_tail_results.json",
        "edited_regions_head_id": editor_dir / "edited_tail_regions_head_id_uint16.tif",
        "edited_centerline_head_id": editor_dir / "edited_tail_head_id_labels_uint16.tif",
        "edited_centerlines_binary": editor_dir / "edited_tail_centerlines_uint16.tif",
        "edited_conflicts": editor_dir / "edited_tail_region_conflicts.json",
        "head_final_labels": head_final_path,
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("缺少候选转换输入：\n" + "\n".join(missing))

    results_payload = load_json(paths["edited_results"])
    conflicts_payload = load_json(paths["edited_conflicts"])
    edited_regions = read_label_image(paths["edited_regions_head_id"])
    shape = tuple(edited_regions.shape)
    edited_centerline_head = read_label_image(
        paths["edited_centerline_head_id"], expected_shape=shape
    )
    edited_centerlines = read_label_image(
        paths["edited_centerlines_binary"], expected_shape=shape
    )
    head_final = read_label_image(paths["head_final_labels"], expected_shape=shape)

    validation = validate_inputs(
        head_final,
        edited_regions,
        edited_centerline_head,
        edited_centerlines,
        results_payload,
        conflicts_payload,
    )
    sync_count = int(validation.get("region_pixel_count_sync_count", 0))
    if sync_count:
        print(
            f"提示：已按最终区域TIFF自动同步{sync_count}条JSON像素数，"
            "无需重新人工编辑。"
        )
    selected = list(validation.pop("selected_records"))
    head_ids = list(validation["head_ids"])

    tail_final, tail_head_ids, positive_heads, object_rows = build_candidate_arrays(
        head_final,
        edited_regions,
        head_ids,
    )

    selected_by_head = {int(record["head_id"]): record for record in selected}
    objects: List[Dict[str, Any]] = []
    for row in object_rows:
        record = selected_by_head[int(row["head_id"])]
        objects.append(
            {
                **row,
                "source": "edited_tail_regions_head_id_uint16.tif",
                "accepted": True,
                "current_status": str(record.get("current_status") or ""),
                "selected_source": str(record.get("selected_source") or ""),
                "accepted_by_user": bool(record.get("accepted_by_user", False)),
                "length_px": float(record.get("length_px") or 0.0),
                "mean_probability": float(record.get("mean_probability") or 0.0),
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = {
        "tail_final_labels_candidate": output_dir
        / f"{field_id}_TailFinalLabelsCandidate.tif",
        "tail_final_head_id_labels_candidate": output_dir
        / f"{field_id}_TailFinalHeadIdLabelsCandidate.tif",
        "tail_positive_head_labels_candidate": output_dir
        / f"{field_id}_TailPositiveHeadLabelsCandidate.tif",
        "tail_final_objects_candidate": output_dir
        / f"{field_id}_TailFinalObjectsCandidate.json",
        "overlay": output_dir / "tail_final_candidate_overlay.png",
        "manifest": output_dir / "tail_final_candidate_manifest.json",
    }

    Image.fromarray(tail_final).save(output_paths["tail_final_labels_candidate"])
    Image.fromarray(tail_head_ids).save(
        output_paths["tail_final_head_id_labels_candidate"]
    )
    Image.fromarray(positive_heads).save(
        output_paths["tail_positive_head_labels_candidate"]
    )

    objects_payload = {
        "schema_version": 1,
        "candidate_version": VERSION,
        "field_id": field_id,
        "object_count": len(objects),
        "objects": objects,
        "region_label_path": str(
            output_paths["tail_final_labels_candidate"].resolve()
        ),
        "head_id_label_path": str(
            output_paths["tail_final_head_id_labels_candidate"].resolve()
        ),
        "positive_head_label_path": str(
            output_paths["tail_positive_head_labels_candidate"].resolve()
        ),
    }
    output_paths["tail_final_objects_candidate"].write_text(
        json.dumps(objects_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    merge_path = find_merge(task_root, field_id)
    if merge_path is not None:
        base_rgb = to_uint8_rgb(read_image(merge_path))
    else:
        base_rgb = np.zeros(shape + (3,), dtype=np.uint8)
    overlay = make_overlay(base_rgb, tail_final, positive_heads)
    Image.fromarray(overlay).save(output_paths["overlay"])

    elapsed = time.perf_counter() - started
    manifest = {
        "version": VERSION,
        "field_id": field_id,
        "created_at_unix": time.time(),
        "object_count": len(objects),
        "head_ids": head_ids,
        "full_head_count": int(validation["full_head_count"]),
        "validation": validation,
        "source_files": {key: str(path.resolve()) for key, path in paths.items()},
        "output_files": {
            key: str(path.resolve()) for key, path in output_paths.items()
        },
        "ready_for_promotion": True,
        "ready_for_measurement": False,
        "promotion_note": (
            "候选契约已通过结构校验，但尚未复制到正式 calibration/tail，"
            "不会被现有测量流程自动读取。"
        ),
        "elapsed_seconds": float(elapsed),
    }
    output_paths["manifest"].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将人工编辑尾部结果转换为独立 TailFinal 候选契约"
    )
    parser.add_argument("--task-root", required=True)
    parser.add_argument("--field-id", required=True)
    parser.add_argument("--output-dir")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    task_root = Path(args.task_root).expanduser().resolve()
    field_id = str(args.field_id).strip()
    if not task_root.is_dir():
        raise FileNotFoundError(f"任务目录不存在：{task_root}")
    if not field_id:
        raise ValueError("field-id不能为空。")

    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else task_root
        / "calibration"
        / "tail_joint_final_candidate_mvp"
        / field_id
    )
    manifest = write_candidate(task_root, field_id, output_dir)
    print("TailFinal候选契约已生成。")
    print(f"对象数：{manifest['object_count']}")
    print(f"完整头部数：{manifest['full_head_count']}")
    print(
        "中心线位于荧光区域外的像素：{}".format(
            manifest["validation"]["centerline_outside_region_pixel_count"]
        )
    )
    print(f"候选目录：{output_dir}")
    print("ready_for_promotion=True")
    print("ready_for_measurement=False")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
