#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 TailFinal Candidate 安全转换为正式文件名的隔离暂存契约。

本脚本不会修改 calibration/tail，也不会运行测量、发布或写数据库。

输入：
  calibration/tail_joint_final_candidate_mvp/<field>/...Candidate.*

输出：
  calibration/tail_joint_promotion_staging_mvp/<field>/
    <field>_TailFinalLabels.tif
    <field>_TailFinalHeadIdLabels.tif
    <field>_TailPositiveHeadLabels.tif
    <field>_TailFinalObjects.json
    tail_final_staging_overlay.png
    tail_promotion_staging_manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np

VERSION = "tail_joint_promotion_staging_mvp_v1"


def read_label(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise RuntimeError(f"无法读取标签图：{path}")
    if image.ndim != 2:
        raise ValueError(f"标签图必须为二维：{path}，shape={image.shape}")
    if image.dtype != np.uint16:
        raise ValueError(f"标签图必须为 uint16：{path}，dtype={image.dtype}")
    return image


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def positive_ids(image: np.ndarray) -> np.ndarray:
    ids = np.unique(image)
    return ids[ids > 0]


def require_contiguous(ids: np.ndarray, name: str) -> None:
    expected = np.arange(1, len(ids) + 1, dtype=ids.dtype)
    if not np.array_equal(ids, expected):
        raise ValueError(
            f"{name} 编号不连续：实际={ids.tolist()}，预期={expected.tolist()}"
        )


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def validate_candidate(
    task_root: Path,
    field_id: str,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    calibration_root = task_root / "calibration"
    candidate_dir = (
        calibration_root
        / "tail_joint_final_candidate_mvp"
        / field_id
    )
    head_final_path = (
        calibration_root
        / "head"
        / f"{field_id}_HeadFinalLabels.tif"
    )

    paths = {
        "labels": candidate_dir / f"{field_id}_TailFinalLabelsCandidate.tif",
        "head_ids": candidate_dir / f"{field_id}_TailFinalHeadIdLabelsCandidate.tif",
        "positive_heads": candidate_dir / f"{field_id}_TailPositiveHeadLabelsCandidate.tif",
        "objects": candidate_dir / f"{field_id}_TailFinalObjectsCandidate.json",
        "overlay": candidate_dir / "tail_final_candidate_overlay.png",
        "manifest": candidate_dir / "tail_final_candidate_manifest.json",
        "head_final": head_final_path,
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("候选契约缺少文件：\n" + "\n".join(missing))

    candidate_manifest = load_json(paths["manifest"])
    if not bool(candidate_manifest.get("ready_for_promotion")):
        raise ValueError("候选 manifest 未标记 ready_for_promotion=true。")
    validation = dict(candidate_manifest.get("validation") or {})
    if int(validation.get("conflict_count", 0)) != 0:
        raise ValueError("候选区域仍存在冲突，不能进入暂存契约。")

    labels = read_label(paths["labels"])
    head_ids = read_label(paths["head_ids"])
    positive_heads = read_label(paths["positive_heads"])
    head_final = read_label(paths["head_final"])

    shapes = {labels.shape, head_ids.shape, positive_heads.shape, head_final.shape}
    if len(shapes) != 1:
        raise ValueError(
            "候选标签和 HeadFinalLabels 尺寸不一致："
            f"labels={labels.shape}, head_ids={head_ids.shape}, "
            f"positive_heads={positive_heads.shape}, head_final={head_final.shape}"
        )

    label_ids = positive_ids(labels)
    require_contiguous(label_ids, "TailFinalLabelsCandidate")
    object_count = int(len(label_ids))
    if object_count <= 0:
        raise ValueError("候选契约没有任何尾部对象。")

    if not np.array_equal(labels > 0, head_ids > 0):
        raise ValueError("TailFinalLabelsCandidate 与 HeadIdLabelsCandidate 支持区域不一致。")

    object_to_head: Dict[int, int] = {}
    object_pixels: Dict[int, int] = {}
    for object_id in label_ids.tolist():
        mask = labels == int(object_id)
        mapped = positive_ids(head_ids[mask])
        if len(mapped) != 1:
            raise ValueError(
                f"对象 {object_id} 没有唯一 Head ID：{mapped.tolist()}"
            )
        object_to_head[int(object_id)] = int(mapped[0])
        object_pixels[int(object_id)] = int(mask.sum())

    mapped_head_ids = list(object_to_head.values())
    if len(set(mapped_head_ids)) != object_count:
        raise ValueError("多个尾部对象映射到同一个 Head ID。")

    valid_head_ids = set(int(value) for value in positive_ids(head_final).tolist())
    invalid = sorted(set(mapped_head_ids) - valid_head_ids)
    if invalid:
        raise ValueError(f"候选包含 HeadFinalLabels 中不存在的 Head ID：{invalid}")

    positive_object_ids = positive_ids(positive_heads)
    require_contiguous(positive_object_ids, "TailPositiveHeadLabelsCandidate")
    if len(positive_object_ids) != object_count:
        raise ValueError(
            "阳性头部对象数与尾部对象数不一致："
            f"positive={len(positive_object_ids)}, tail={object_count}"
        )

    positive_object_to_head: Dict[int, int] = {}
    for object_id in positive_object_ids.tolist():
        mask = positive_heads == int(object_id)
        if not np.all(head_final[mask] > 0):
            raise ValueError(f"阳性头部对象 {object_id} 超出 HeadFinalLabels。")
        mapped = positive_ids(head_final[mask])
        if len(mapped) != 1:
            raise ValueError(
                f"阳性头部对象 {object_id} 没有唯一原始 Head ID：{mapped.tolist()}"
            )
        positive_object_to_head[int(object_id)] = int(mapped[0])

    if positive_object_to_head != object_to_head:
        raise ValueError(
            "TailPositiveHeadLabelsCandidate 与尾部对象的 Head ID 映射不一致。"
        )

    objects_payload = load_json(paths["objects"])
    if str(objects_payload.get("field_id", "")) != field_id:
        raise ValueError("TailFinalObjectsCandidate field_id 不匹配。")
    if int(objects_payload.get("object_count", -1)) != object_count:
        raise ValueError("TailFinalObjectsCandidate object_count 不匹配。")

    rows = list(objects_payload.get("objects") or [])
    if len(rows) != object_count:
        raise ValueError("TailFinalObjectsCandidate objects 数量不匹配。")

    json_mapping: Dict[int, int] = {}
    normalized_objects: List[Dict[str, Any]] = []
    for row in rows:
        object_id = int(row.get("object_id", 0))
        head_id = int(row.get("head_id", 0))
        pixel_count = int(row.get("pixel_count", -1))
        if object_id not in object_to_head:
            raise ValueError(f"JSON 含未知对象编号：{object_id}")
        if head_id != object_to_head[object_id]:
            raise ValueError(
                f"对象 {object_id} JSON Head ID={head_id}，标签 Head ID={object_to_head[object_id]}"
            )
        if pixel_count != object_pixels[object_id]:
            raise ValueError(
                f"对象 {object_id} JSON pixel_count={pixel_count}，实际={object_pixels[object_id]}"
            )
        json_mapping[object_id] = head_id
        normalized = dict(row)
        normalized["object_id"] = object_id
        normalized["head_id"] = head_id
        normalized["pixel_count"] = pixel_count
        normalized["accepted"] = True
        normalized_objects.append(normalized)

    if json_mapping != object_to_head:
        raise ValueError("JSON 对象映射与标签映射不一致。")

    expected_manifest_count = int(candidate_manifest.get("object_count", -1))
    if expected_manifest_count != object_count:
        raise ValueError("候选 manifest object_count 不匹配。")

    validation_result = {
        "shape": [int(labels.shape[0]), int(labels.shape[1])],
        "dtype": "uint16",
        "object_count": object_count,
        "head_ids": mapped_head_ids,
        "tail_pixel_count": int((labels > 0).sum()),
        "positive_head_pixel_count": int((positive_heads > 0).sum()),
        "full_head_count": int(len(valid_head_ids)),
        "supports_equal": True,
        "object_ids_contiguous": True,
        "positive_head_ids_contiguous": True,
        "object_head_mapping_unique": True,
        "positive_head_mapping_matches": True,
        "candidate_conflict_count": 0,
        "candidate_centerline_outside_region_pixel_count": int(
            validation.get("centerline_outside_region_pixel_count", 0)
        ),
    }

    context = {
        "candidate_dir": candidate_dir,
        "paths": paths,
        "candidate_manifest": candidate_manifest,
        "normalized_objects": normalized_objects,
        "object_count": object_count,
    }
    return context, validation_result, object_to_head


def atomic_replace_directory(temp_dir: Path, target_dir: Path) -> None:
    backup_dir = target_dir.with_name(
        target_dir.name + f".backup-{uuid.uuid4().hex[:8]}"
    )
    had_target = target_dir.exists()
    try:
        if had_target:
            os.replace(str(target_dir), str(backup_dir))
        os.replace(str(temp_dir), str(target_dir))
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
    except BaseException:
        if target_dir.exists() and not had_target:
            shutil.rmtree(target_dir, ignore_errors=True)
        if backup_dir.exists() and not target_dir.exists():
            os.replace(str(backup_dir), str(target_dir))
        raise


def stage_candidate(task_root: Path, field_id: str) -> Dict[str, Any]:
    started = time.perf_counter()
    context, validation, object_to_head = validate_candidate(task_root, field_id)

    calibration_root = task_root / "calibration"
    staging_root = calibration_root / "tail_joint_promotion_staging_mvp"
    target_dir = staging_root / field_id
    staging_root.mkdir(parents=True, exist_ok=True)
    temp_dir = staging_root / f".{field_id}.tmp-{uuid.uuid4().hex[:8]}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=False)

    output_paths = {
        "labels": temp_dir / f"{field_id}_TailFinalLabels.tif",
        "head_ids": temp_dir / f"{field_id}_TailFinalHeadIdLabels.tif",
        "positive_heads": temp_dir / f"{field_id}_TailPositiveHeadLabels.tif",
        "objects": temp_dir / f"{field_id}_TailFinalObjects.json",
        "overlay": temp_dir / "tail_final_staging_overlay.png",
        "manifest": temp_dir / "tail_promotion_staging_manifest.json",
    }

    source_paths = context["paths"]
    shutil.copy2(source_paths["labels"], output_paths["labels"])
    shutil.copy2(source_paths["head_ids"], output_paths["head_ids"])
    shutil.copy2(source_paths["positive_heads"], output_paths["positive_heads"])
    shutil.copy2(source_paths["overlay"], output_paths["overlay"])

    formal_objects = {
        "schema_version": 1,
        "field_id": field_id,
        "object_count": int(context["object_count"]),
        "objects": context["normalized_objects"],
        "region_label_path": str((target_dir / output_paths["labels"].name).resolve()),
        "head_id_label_path": str((target_dir / output_paths["head_ids"].name).resolve()),
        "positive_head_label_path": str((target_dir / output_paths["positive_heads"].name).resolve()),
    }
    write_json(output_paths["objects"], formal_objects)

    # 对暂存副本再做一次读取验证。
    staged_labels = read_label(output_paths["labels"])
    staged_head_ids = read_label(output_paths["head_ids"])
    staged_positive = read_label(output_paths["positive_heads"])
    if not np.array_equal(staged_labels > 0, staged_head_ids > 0):
        raise RuntimeError("暂存副本支持区域校验失败。")
    if int(len(positive_ids(staged_labels))) != int(context["object_count"]):
        raise RuntimeError("暂存副本对象数校验失败。")
    if int(len(positive_ids(staged_positive))) != int(context["object_count"]):
        raise RuntimeError("暂存阳性头部对象数校验失败。")

    manifest = {
        "version": VERSION,
        "field_id": field_id,
        "created_at_unix": time.time(),
        "object_count": int(context["object_count"]),
        "head_ids": [int(object_to_head[index]) for index in sorted(object_to_head)],
        "validation": validation,
        "source_candidate_manifest": str(source_paths["manifest"].resolve()),
        "source_candidate_version": str(
            context["candidate_manifest"].get("version", "")
        ),
        "output_directory": str(target_dir.resolve()),
        "output_files": {
            key: str((target_dir / path.name).resolve())
            for key, path in output_paths.items()
        },
        "sha256": {
            key: sha256_file(path)
            for key, path in output_paths.items()
            if key != "manifest"
        },
        "ready_for_batch_promotion": True,
        "ready_for_measurement": False,
        "formal_calibration_modified": False,
        "note": (
            "已转换为正式文件名的隔离暂存契约；尚未写入 calibration/tail，"
            "必须等待全部视野通过后再统一原子提升。"
        ),
        "elapsed_seconds": float(time.perf_counter() - started),
    }
    write_json(output_paths["manifest"], manifest)

    atomic_replace_directory(temp_dir, target_dir)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将 TailFinal Candidate 转换为正式文件名的隔离暂存契约。"
    )
    parser.add_argument("--task-root", required=True)
    parser.add_argument("--field-id", required=True)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只校验候选契约，不生成暂存目录。",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    task_root = Path(args.task_root).expanduser().resolve()
    field_id = str(args.field_id).strip()
    if not task_root.is_dir():
        raise FileNotFoundError(f"任务目录不存在：{task_root}")
    if not field_id:
        raise ValueError("field-id 不能为空。")

    context, validation, _ = validate_candidate(task_root, field_id)
    if args.validate_only:
        print("TailFinal Candidate 校验通过。")
        print(f"视野：{field_id}")
        print(f"对象数：{validation['object_count']}")
        print(f"完整头部数：{validation['full_head_count']}")
        print(f"尾部像素数：{validation['tail_pixel_count']}")
        print("未生成暂存目录。")
        return 0

    manifest = stage_candidate(task_root, field_id)
    print("TailFinal 正式命名暂存契约已生成。")
    print(f"视野：{field_id}")
    print(f"对象数：{manifest['object_count']}")
    print(f"输出目录：{manifest['output_directory']}")
    print("ready_for_batch_promotion=True")
    print("ready_for_measurement=False")
    print("calibration/tail 未修改。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
