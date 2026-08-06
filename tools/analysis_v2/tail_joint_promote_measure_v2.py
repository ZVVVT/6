#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三视野联合尾部结果：原子提升并运行正式尾部测量。

默认流程：
1. 独立校验 calibration/tail_joint_promotion_staging_mvp 下全部视野；
2. 备份旧 calibration/tail、measurement/tail、state.json、manifest.json；
3. 一次性原子替换 calibration/tail；
4. 将任务状态切换为 tail_calibrated；
5. 调用现有 TailMeasurementService 和已验证 cppipe 进行测量；
6. 测量失败时自动恢复旧正式标签、旧测量结果、state 和 manifest。

本脚本不会发布 candidate_output，也不会写数据库。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

VERSION = "tail_joint_promote_measure_v2"
STAGING_DIR_NAME = "tail_joint_promotion_staging_mvp"
SUMMARY_NAME = "tail_joint_promote_measure_v2_summary.json"
LOCK_NAME = ".tail_joint_promote_measure_v2.lock"
REQUIRED_FILE_SUFFIXES = {
    "labels": "_TailFinalLabels.tif",
    "head_ids": "_TailFinalHeadIdLabels.tif",
    "positive_heads": "_TailPositiveHeadLabels.tif",
    "objects": "_TailFinalObjects.json",
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(".{}.{}.tmp".format(path.name, uuid.uuid4().hex[:8]))
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp), str(path))
    finally:
        if temp.exists():
            temp.unlink()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_label(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise RuntimeError("无法读取标签图：{}".format(path))
    if image.ndim != 2:
        raise ValueError("标签图必须是二维：{}，shape={}".format(path, image.shape))
    if image.dtype != np.uint16:
        raise ValueError("标签图必须是 uint16：{}，dtype={}".format(path, image.dtype))
    return image


def positive_ids(image: np.ndarray) -> List[int]:
    return [int(value) for value in np.unique(image[image > 0]).tolist()]


def require_contiguous(ids: Sequence[int], name: str) -> None:
    actual = [int(value) for value in ids]
    expected = list(range(1, len(actual) + 1))
    if actual != expected:
        raise ValueError("{} 编号不连续：实际={}，预期={}".format(name, actual, expected))


def find_project_root(script_path: Path, explicit: Optional[str]) -> Path:
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if not (root / "core").is_dir() or not (root / "pipelines").is_dir():
            raise FileNotFoundError("project-root 不是有效源码目录：{}".format(root))
        return root

    candidates = [script_path.resolve().parent]
    candidates.extend(script_path.resolve().parents)
    for candidate in candidates:
        if (candidate / "core").is_dir() and (candidate / "pipelines").is_dir():
            return candidate
    raise FileNotFoundError("无法从脚本位置识别项目根目录，请传入 --project-root。")


def discover_expected_fields(task_root: Path) -> List[str]:
    head_dir = task_root / "calibration" / "head"
    suffix = "_HeadFinalLabels.tif"
    fields = sorted(
        path.name[:-len(suffix)]
        for path in head_dir.glob("*{}".format(suffix))
        if path.is_file()
    )
    if not fields:
        raise ValueError("未在 calibration/head 找到 HeadFinalLabels。")
    return fields


def staging_paths(staging_dir: Path, field_id: str) -> Dict[str, Path]:
    return {
        "labels": staging_dir / "{}{}".format(field_id, REQUIRED_FILE_SUFFIXES["labels"]),
        "head_ids": staging_dir / "{}{}".format(field_id, REQUIRED_FILE_SUFFIXES["head_ids"]),
        "positive_heads": staging_dir / "{}{}".format(field_id, REQUIRED_FILE_SUFFIXES["positive_heads"]),
        "objects": staging_dir / "{}{}".format(field_id, REQUIRED_FILE_SUFFIXES["objects"]),
        "overlay": staging_dir / "tail_final_staging_overlay.png",
        "manifest": staging_dir / "tail_promotion_staging_manifest.json",
    }


def validate_staging_field(task_root: Path, field_id: str) -> Dict[str, Any]:
    staging_dir = task_root / "calibration" / STAGING_DIR_NAME / field_id
    paths = staging_paths(staging_dir, field_id)
    head_final_path = task_root / "calibration" / "head" / "{}_HeadFinalLabels.tif".format(field_id)

    required = list(paths.values()) + [head_final_path]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("视野 {} 暂存契约缺少文件：\n{}".format(field_id, "\n".join(missing)))

    manifest = load_json(paths["manifest"])
    if str(manifest.get("field_id") or "") != field_id:
        raise ValueError("视野 {} 暂存 manifest 的 field_id 不匹配。".format(field_id))
    if not bool(manifest.get("ready_for_batch_promotion")):
        raise ValueError("视野 {} 未标记 ready_for_batch_promotion=true。".format(field_id))
    if bool(manifest.get("formal_calibration_modified")):
        raise ValueError("视野 {} 暂存 manifest 声称已修改正式目录，拒绝继续。".format(field_id))

    expected_hashes = dict(manifest.get("sha256") or {})
    for key in ("labels", "head_ids", "positive_heads", "objects", "overlay"):
        expected_hash = str(expected_hashes.get(key) or "").strip().lower()
        if not expected_hash:
            raise ValueError("视野 {} 暂存 manifest 缺少 {} 的 SHA256。".format(field_id, key))
        actual_hash = sha256_file(paths[key])
        if actual_hash.lower() != expected_hash:
            raise ValueError(
                "视野 {} 的 {} SHA256 不一致：manifest={}，实际={}".format(
                    field_id, key, expected_hash, actual_hash
                )
            )

    labels = read_label(paths["labels"])
    head_ids_image = read_label(paths["head_ids"])
    positive_heads = read_label(paths["positive_heads"])
    head_final = read_label(head_final_path)
    if len({labels.shape, head_ids_image.shape, positive_heads.shape, head_final.shape}) != 1:
        raise ValueError("视野 {} 的暂存标签与 HeadFinalLabels 尺寸不一致。".format(field_id))
    if not np.array_equal(labels > 0, head_ids_image > 0):
        raise ValueError("视野 {} 的 TailFinalLabels 与 HeadIdLabels 支持区域不一致。".format(field_id))

    object_ids = positive_ids(labels)
    require_contiguous(object_ids, "{} TailFinalLabels".format(field_id))
    object_count = len(object_ids)
    if object_count <= 0:
        raise ValueError("视野 {} 没有尾部对象。".format(field_id))

    positive_object_ids = positive_ids(positive_heads)
    require_contiguous(positive_object_ids, "{} TailPositiveHeadLabels".format(field_id))
    if len(positive_object_ids) != object_count:
        raise ValueError("视野 {} 的尾部数与阳性头部数不一致。".format(field_id))

    available_head_ids = set(positive_ids(head_final))
    object_to_head: Dict[int, int] = {}
    pixel_counts: Dict[int, int] = {}
    for object_id in object_ids:
        mask = labels == object_id
        mapped = positive_ids(head_ids_image[mask])
        if len(mapped) != 1:
            raise ValueError("视野 {} 对象 {} 没有唯一 Head ID：{}".format(field_id, object_id, mapped))
        head_id = int(mapped[0])
        if head_id not in available_head_ids:
            raise ValueError("视野 {} 对象 {} 的 Head ID={} 不存在。".format(field_id, object_id, head_id))
        object_to_head[object_id] = head_id
        pixel_counts[object_id] = int(np.count_nonzero(mask))

    if len(set(object_to_head.values())) != object_count:
        raise ValueError("视野 {} 有多个尾部对象映射到同一个 Head ID。".format(field_id))

    positive_to_head: Dict[int, int] = {}
    for object_id in positive_object_ids:
        mask = positive_heads == object_id
        mapped = positive_ids(head_final[mask])
        if len(mapped) != 1:
            raise ValueError("视野 {} 阳性头部对象 {} 映射异常：{}".format(field_id, object_id, mapped))
        positive_to_head[object_id] = int(mapped[0])
    if positive_to_head != object_to_head:
        raise ValueError("视野 {} 的阳性头部映射与尾部对象映射不一致。".format(field_id))

    objects_payload = load_json(paths["objects"])
    if str(objects_payload.get("field_id") or "") != field_id:
        raise ValueError("视野 {} TailFinalObjects 的 field_id 不匹配。".format(field_id))
    if int(objects_payload.get("object_count") or 0) != object_count:
        raise ValueError("视野 {} TailFinalObjects 的 object_count 不匹配。".format(field_id))
    rows = list(objects_payload.get("objects") or [])
    if len(rows) != object_count:
        raise ValueError("视野 {} TailFinalObjects 的 objects 数量不匹配。".format(field_id))

    normalized_rows: List[Dict[str, Any]] = []
    seen_objects = set()
    for row in rows:
        object_id = int(row.get("object_id") or 0)
        head_id = int(row.get("head_id") or 0)
        pixel_count = int(row.get("pixel_count") or 0)
        if object_id in seen_objects or object_id not in object_to_head:
            raise ValueError("视野 {} JSON 对象编号异常：{}".format(field_id, object_id))
        seen_objects.add(object_id)
        if head_id != object_to_head[object_id]:
            raise ValueError("视野 {} 对象 {} 的 JSON Head ID 不匹配。".format(field_id, object_id))
        if pixel_count != pixel_counts[object_id]:
            raise ValueError("视野 {} 对象 {} 的 pixel_count 不匹配。".format(field_id, object_id))
        normalized = dict(row)
        normalized["object_id"] = object_id
        normalized["head_id"] = head_id
        normalized["pixel_count"] = pixel_count
        normalized["accepted"] = True
        normalized_rows.append(normalized)

    if seen_objects != set(object_ids):
        raise ValueError("视野 {} JSON 对象集合不完整。".format(field_id))

    validation = dict(manifest.get("validation") or {})
    if int(manifest.get("object_count") or 0) != object_count:
        raise ValueError("视野 {} 暂存 manifest 的 object_count 不匹配。".format(field_id))
    if int(validation.get("candidate_conflict_count") or 0) != 0:
        raise ValueError("视野 {} 仍存在候选区域冲突。".format(field_id))

    return {
        "field_id": field_id,
        "staging_dir": str(staging_dir),
        "paths": {key: str(value) for key, value in paths.items()},
        "object_count": object_count,
        "head_ids": [object_to_head[index] for index in object_ids],
        "tail_pixel_count": int(np.count_nonzero(labels)),
        "positive_head_pixel_count": int(np.count_nonzero(positive_heads)),
        "full_head_count": len(available_head_ids),
        "normalized_objects": normalized_rows,
        "source_manifest": manifest,
        "source_hashes": expected_hashes,
    }


def validate_all_staging(task_root: Path) -> Tuple[List[str], List[Dict[str, Any]]]:
    fields = discover_expected_fields(task_root)
    staging_root = task_root / "calibration" / STAGING_DIR_NAME
    actual_staging_fields = sorted(
        path.name for path in staging_root.iterdir() if path.is_dir() and not path.name.startswith(".")
    ) if staging_root.is_dir() else []
    missing = sorted(set(fields) - set(actual_staging_fields))
    extra = sorted(set(actual_staging_fields) - set(fields))
    if missing:
        raise ValueError("缺少视野暂存契约：{}".format(missing))
    if extra:
        raise ValueError("存在不属于当前任务头部视野的暂存目录：{}".format(extra))
    results = [validate_staging_field(task_root, field_id) for field_id in fields]
    return fields, results


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source), str(destination))


def build_new_formal_directory(
    task_root: Path,
    validations: Sequence[Dict[str, Any]],
    new_dir: Path,
) -> Dict[str, Any]:
    if new_dir.exists():
        shutil.rmtree(str(new_dir))
    new_dir.mkdir(parents=True, exist_ok=False)

    field_records: List[Dict[str, Any]] = []
    for item in validations:
        field_id = str(item["field_id"])
        source_paths = {key: Path(value) for key, value in dict(item["paths"]).items()}
        target_field = new_dir / field_id
        target_field.mkdir(parents=True, exist_ok=False)
        targets = {
            "labels": target_field / "{}_TailFinalLabels.tif".format(field_id),
            "head_ids": target_field / "{}_TailFinalHeadIdLabels.tif".format(field_id),
            "positive_heads": target_field / "{}_TailPositiveHeadLabels.tif".format(field_id),
            "objects": target_field / "{}_TailFinalObjects.json".format(field_id),
            "overlay": target_field / "tail_final_staging_overlay.png",
            "source_manifest": target_field / "tail_joint_source_staging_manifest.json",
        }
        for key in ("labels", "head_ids", "positive_heads", "overlay"):
            copy_file(source_paths[key], targets[key])

        formal_objects = {
            "schema_version": 1,
            "field_id": field_id,
            "object_count": int(item["object_count"]),
            "objects": list(item["normalized_objects"]),
            "region_label_path": str((task_root / "calibration" / "tail" / field_id / targets["labels"].name).resolve()),
            "head_id_label_path": str((task_root / "calibration" / "tail" / field_id / targets["head_ids"].name).resolve()),
            "positive_head_label_path": str((task_root / "calibration" / "tail" / field_id / targets["positive_heads"].name).resolve()),
            "promotion_source": str(source_paths["manifest"].resolve()),
            "promotion_version": VERSION,
        }
        write_json(targets["objects"], formal_objects)
        write_json(targets["source_manifest"], item["source_manifest"])

        # 对复制后的正式候选再次验证哈希和对象数。
        copied_labels = read_label(targets["labels"])
        copied_positive = read_label(targets["positive_heads"])
        if len(positive_ids(copied_labels)) != int(item["object_count"]):
            raise RuntimeError("视野 {} 正式副本对象数校验失败。".format(field_id))
        if len(positive_ids(copied_positive)) != int(item["object_count"]):
            raise RuntimeError("视野 {} 正式阳性头部对象数校验失败。".format(field_id))

        field_records.append({
            "field_id": field_id,
            "object_count": int(item["object_count"]),
            "head_ids": list(item["head_ids"]),
            "tail_pixel_count": int(item["tail_pixel_count"]),
            "formal_files": {key: str(path) for key, path in targets.items()},
            "sha256": {
                key: sha256_file(path)
                for key, path in targets.items()
                if path.is_file()
            },
        })

    promotion_manifest = {
        "version": VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task_root": str(task_root),
        "field_count": len(field_records),
        "total_object_count": sum(int(item["object_count"]) for item in field_records),
        "fields": field_records,
        "source_staging_root": str((task_root / "calibration" / STAGING_DIR_NAME).resolve()),
        "database_modified": False,
        "published": False,
    }
    write_json(new_dir / "tail_joint_atomic_promotion_manifest.json", promotion_manifest)
    return promotion_manifest


def move_if_exists(source: Path, target: Path) -> bool:
    if not source.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.is_dir():
            shutil.rmtree(str(target))
        else:
            target.unlink()
    os.replace(str(source), str(target))
    return True


def restore_file_from_backup(backup: Path, target: Path) -> None:
    if backup.is_file():
        copy_file(backup, target)


def add_project_to_path(project_root: Path) -> None:
    text = str(project_root)
    if text not in sys.path:
        sys.path.insert(0, text)


def register_promoted_files(project_root: Path, task_root: Path, fields: Sequence[str]) -> None:
    add_project_to_path(project_root)
    from core.analysis_v2.manifest_store import ManifestStore
    from core.analysis_v2.tail_calibration_service import task_paths_from_root

    paths = task_paths_from_root(task_root)
    manifest = ManifestStore.from_task_paths(paths)
    role_specs = (
        ("_TailFinalLabels.tif", "tail_final_labels", "image/tiff"),
        ("_TailFinalHeadIdLabels.tif", "tail_final_head_id_labels", "image/tiff"),
        ("_TailPositiveHeadLabels.tif", "tail_positive_head_labels", "image/tiff"),
        ("_TailFinalObjects.json", "tail_final_objects", "application/json"),
    )
    for field_id in fields:
        field_dir = task_root / "calibration" / "tail" / field_id
        for suffix, role, media_type in role_specs:
            manifest.add_file(
                field_dir / "{}{}".format(field_id, suffix),
                role=role,
                stage="tail_calibration",
                media_type=media_type,
                metadata={"field_id": field_id, "promotion_version": VERSION},
            )
    manifest.add_file(
        task_root / "calibration" / "tail" / "tail_joint_atomic_promotion_manifest.json",
        role="tail_joint_atomic_promotion_manifest",
        stage="tail_calibration",
        media_type="application/json",
        metadata={"promotion_version": VERSION},
    )


def mark_tail_calibrated(project_root: Path, task_root: Path) -> Dict[str, Any]:
    add_project_to_path(project_root)
    from core.analysis_v2.tail_calibration_service import task_paths_from_root
    from core.analysis_v2.task_state import TaskStateStore

    paths = task_paths_from_root(task_root)
    return TaskStateStore.from_task_paths(paths).update(
        "tail_calibrated",
        "tail_calibration",
        "联合尾部三视野原子提升完成，准备重新测量",
    )


def install_headless_pyside6_stub_if_needed() -> None:
    """MvImageID Python 通常不安装 PySide6；测量服务只需要 Runner，不需要 GUI。"""
    try:
        __import__("PySide6.QtCore")
        return
    except ModuleNotFoundError:
        pass

    import types

    class DummySignal:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def emit(self, *args: Any, **kwargs: Any) -> None:
            pass

    class DummyQThread:
        def __init__(self, parent: Any = None) -> None:
            self.parent = parent

    pyside_module = types.ModuleType("PySide6")
    qtcore_module = types.ModuleType("PySide6.QtCore")
    qtcore_module.QThread = DummyQThread
    qtcore_module.Signal = DummySignal
    pyside_module.QtCore = qtcore_module
    sys.modules.setdefault("PySide6", pyside_module)
    sys.modules.setdefault("PySide6.QtCore", qtcore_module)


def run_measurement(project_root: Path, task_root: Path, timeout_seconds: float) -> Dict[str, Any]:
    add_project_to_path(project_root)
    install_headless_pyside6_stub_if_needed()
    from core.analysis_v2.tail_measurement_service import TailMeasurementService
    from core.config_manager import ConfigManager

    config = ConfigManager(str(project_root / "config.ini"))
    pipeline = project_root / "pipelines" / "analysis_v2" / "measure_tail_from_labels.cppipe"
    if not pipeline.is_file():
        raise FileNotFoundError("尾部测量管道不存在：{}".format(pipeline))
    service = TailMeasurementService(
        task_root=task_root,
        pipeline=pipeline,
        mvimageid_root=config.get_source_project_dir(),
        python_exe=config.get_python_exe(),
        plugins_directory=config.get_plugins_directory(),
        timeout_seconds=float(timeout_seconds),
    )
    return service.run()


def acquire_lock(task_root: Path) -> Path:
    lock_path = task_root / LOCK_NAME
    try:
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("检测到未释放的一键提升锁：{}。确认没有另一进程运行后再删除该文件。".format(lock_path))
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write("pid={}\nversion={}\nstarted={}\n".format(
            os.getpid(), VERSION, datetime.now().astimezone().isoformat(timespec="seconds")
        ))
    return lock_path


def rollback_interrupted_promotion(task_root: Path) -> List[str]:
    """Restore the latest atomic-promotion backup after its recorded process stops."""
    root = Path(task_root).resolve()
    calibration_root = root / "calibration"
    backup_parent = calibration_root / "tail_joint_atomic_backups"
    backup_roots = sorted(
        [path for path in backup_parent.iterdir() if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ) if backup_parent.is_dir() else []
    old_formal = sorted(
        calibration_root.glob(".tail_joint_old_*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    old_measurement = sorted(
        (root / "measurement").glob(".tail_old_*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    backup_root = backup_roots[0] if backup_roots else None
    errors: List[str] = []

    try:
        source = old_formal[0] if old_formal else (
            backup_root / "formal_tail_before" if backup_root is not None else None
        )
        if source is not None and source.is_dir():
            target = calibration_root / "tail"
            if target.exists():
                shutil.rmtree(str(target))
            os.replace(str(source), str(target))
    except BaseException as exception:
        errors.append("恢复 calibration/tail 失败：{}".format(exception))

    try:
        source = old_measurement[0] if old_measurement else (
            backup_root / "measurement_tail_before" if backup_root is not None else None
        )
        if source is not None and source.is_dir():
            target = root / "measurement" / "tail"
            if target.exists():
                shutil.rmtree(str(target))
            os.replace(str(source), str(target))
    except BaseException as exception:
        errors.append("恢复 measurement/tail 失败：{}".format(exception))

    try:
        if backup_root is not None:
            restore_file_from_backup(backup_root / "state_before.json", root / "state.json")
            restore_file_from_backup(backup_root / "manifest_before.json", root / "manifest.json")
    except BaseException as exception:
        errors.append("恢复 state/manifest 失败：{}".format(exception))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="联合尾部三视野原子提升并重新测量。")
    parser.add_argument("--task-root", required=True)
    parser.add_argument("--project-root", default="")
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--promote-only", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    task_root = Path(args.task_root).expanduser().resolve()
    if not task_root.is_dir():
        raise FileNotFoundError("任务目录不存在：{}".format(task_root))
    project_root = find_project_root(Path(__file__), str(args.project_root or "").strip() or None)

    print("联合尾部原子提升与测量 v2", flush=True)
    print("版本：{}".format(VERSION), flush=True)
    print("项目目录：{}".format(project_root), flush=True)
    print("任务目录：{}".format(task_root), flush=True)
    print("安全边界：不发布 candidate_output，不写数据库；测量失败自动恢复旧正式结果。", flush=True)

    lock_path = acquire_lock(task_root)
    old_formal_temp: Optional[Path] = None
    old_measurement_temp: Optional[Path] = None
    new_formal_temp: Optional[Path] = None
    backup_root: Optional[Path] = None
    state_backup: Optional[Path] = None
    manifest_backup: Optional[Path] = None
    summary_path = task_root / "logs" / SUMMARY_NAME

    try:
        fields, validations = validate_all_staging(task_root)
        print("暂存契约校验通过：{}".format(", ".join(fields)), flush=True)
        for item in validations:
            print(
                "  {}：对象数={}，尾部像素={}".format(
                    item["field_id"], item["object_count"], item["tail_pixel_count"]
                ),
                flush=True,
            )
        print("三视野尾部总数：{}".format(sum(int(item["object_count"]) for item in validations)), flush=True)

        if args.validate_only:
            write_json(summary_path, {
                "version": VERSION,
                "success": True,
                "mode": "validate_only",
                "task_root": str(task_root),
                "project_root": str(project_root),
                "fields": validations,
                "elapsed_seconds": time.perf_counter() - started,
            })
            print("仅校验完成，正式目录未修改。", flush=True)
            return 0

        token = "{}-{}".format(datetime.now().strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:8])
        calibration_root = task_root / "calibration"
        formal_tail = calibration_root / "tail"
        measurement_tail = task_root / "measurement" / "tail"
        backup_root = calibration_root / "tail_joint_atomic_backups" / token
        backup_root.mkdir(parents=True, exist_ok=False)
        state_backup = backup_root / "state_before.json"
        manifest_backup = backup_root / "manifest_before.json"
        copy_file(task_root / "state.json", state_backup)
        copy_file(task_root / "manifest.json", manifest_backup)

        new_formal_temp = calibration_root / ".tail_joint_new_{}".format(token)
        promotion_manifest = build_new_formal_directory(task_root, validations, new_formal_temp)

        old_formal_temp = calibration_root / ".tail_joint_old_{}".format(token)
        old_measurement_temp = task_root / "measurement" / ".tail_old_{}".format(token)
        had_old_formal = move_if_exists(formal_tail, old_formal_temp)
        try:
            os.replace(str(new_formal_temp), str(formal_tail))
            new_formal_temp = None
        except BaseException:
            if had_old_formal and old_formal_temp.exists() and not formal_tail.exists():
                os.replace(str(old_formal_temp), str(formal_tail))
            raise

        had_old_measurement = move_if_exists(measurement_tail, old_measurement_temp)
        register_promoted_files(project_root, task_root, fields)
        state_after_promotion = mark_tail_calibrated(project_root, task_root)
        print("三视野已一次性提升到 calibration/tail。", flush=True)

        if args.promote_only:
            if old_formal_temp.exists():
                move_if_exists(old_formal_temp, backup_root / "formal_tail_before")
            if old_measurement_temp.exists():
                move_if_exists(old_measurement_temp, backup_root / "measurement_tail_before")
            summary = {
                "version": VERSION,
                "success": True,
                "mode": "promote_only",
                "task_root": str(task_root),
                "project_root": str(project_root),
                "backup_root": str(backup_root),
                "promotion": promotion_manifest,
                "state": state_after_promotion,
                "database_modified": False,
                "published": False,
                "elapsed_seconds": time.perf_counter() - started,
            }
            write_json(summary_path, summary)
            print("仅提升完成，未运行测量。", flush=True)
            return 0

        print("开始调用现有 measure_tail_from_labels.cppipe 重新测量……", flush=True)
        measurement_started = time.perf_counter()
        measurement_result = run_measurement(project_root, task_root, args.timeout_seconds)
        measurement_elapsed = time.perf_counter() - measurement_started

        if old_formal_temp.exists():
            move_if_exists(old_formal_temp, backup_root / "formal_tail_before")
        if old_measurement_temp.exists():
            move_if_exists(old_measurement_temp, backup_root / "measurement_tail_before")

        validation = dict(measurement_result.get("validation") or {})
        strict_totals = dict(validation.get("strict_totals") or {})
        summary = {
            "version": VERSION,
            "success": True,
            "mode": "promote_and_measure",
            "task_root": str(task_root),
            "project_root": str(project_root),
            "backup_root": str(backup_root),
            "promotion": promotion_manifest,
            "measurement": measurement_result,
            "strict_totals": strict_totals,
            "candidate_output_dir": str(measurement_result.get("candidate_output_dir") or ""),
            "measurement_elapsed_seconds": measurement_elapsed,
            "database_modified": False,
            "published": False,
            "ready_for_result_review": True,
            "elapsed_seconds": time.perf_counter() - started,
        }
        write_json(summary_path, summary)

        print("尾部测量和严格校验通过。", flush=True)
        print("视野数：{}".format(validation.get("field_count")), flush=True)
        print("尾部对象总数：{}".format(validation.get("expected_object_count")), flush=True)
        print("完整精子总数：{}".format(strict_totals.get("sperm_count")), flush=True)
        print("阳性尾部总数：{}".format(strict_totals.get("positive_count")), flush=True)
        print("尾部荧光强度：{}".format(strict_totals.get("mean_intensity_raw")), flush=True)
        print("尾部标定率：{}%".format(strict_totals.get("expression_rate")), flush=True)
        print("候选测量输出：{}".format(measurement_result.get("candidate_output_dir")), flush=True)
        print("旧正式结果备份：{}".format(backup_root), flush=True)
        print("汇总：{}".format(summary_path), flush=True)
        print("数据库未修改，candidate_output 尚未发布。", flush=True)
        return 0

    except BaseException as exception:
        rollback_errors: List[str] = []
        try:
            formal_tail = task_root / "calibration" / "tail"
            if old_formal_temp is not None and old_formal_temp.exists():
                if formal_tail.exists():
                    shutil.rmtree(str(formal_tail))
                os.replace(str(old_formal_temp), str(formal_tail))
        except BaseException as rollback_exception:
            rollback_errors.append("恢复 calibration/tail 失败：{}".format(rollback_exception))
        try:
            measurement_tail = task_root / "measurement" / "tail"
            if old_measurement_temp is not None and old_measurement_temp.exists():
                if measurement_tail.exists():
                    shutil.rmtree(str(measurement_tail))
                os.replace(str(old_measurement_temp), str(measurement_tail))
        except BaseException as rollback_exception:
            rollback_errors.append("恢复 measurement/tail 失败：{}".format(rollback_exception))
        try:
            if state_backup is not None:
                restore_file_from_backup(state_backup, task_root / "state.json")
            if manifest_backup is not None:
                restore_file_from_backup(manifest_backup, task_root / "manifest.json")
        except BaseException as rollback_exception:
            rollback_errors.append("恢复 state/manifest 失败：{}".format(rollback_exception))
        try:
            if new_formal_temp is not None and new_formal_temp.exists():
                shutil.rmtree(str(new_formal_temp))
        except BaseException as rollback_exception:
            rollback_errors.append("清理临时目录失败：{}".format(rollback_exception))

        failure = {
            "version": VERSION,
            "success": False,
            "task_root": str(task_root),
            "project_root": str(project_root),
            "error": str(exception),
            "traceback": traceback.format_exc(),
            "rollback_errors": rollback_errors,
            "formal_calibration_restored": not rollback_errors,
            "database_modified": False,
            "published": False,
            "backup_root": str(backup_root) if backup_root is not None else "",
            "elapsed_seconds": time.perf_counter() - started,
        }
        try:
            write_json(summary_path, failure)
        except BaseException:
            pass
        print("失败：{}".format(exception), flush=True)
        if rollback_errors:
            for text in rollback_errors:
                print("回滚异常：{}".format(text), flush=True)
        else:
            print("已自动恢复原 calibration/tail、measurement/tail、state.json 和 manifest.json。", flush=True)
        print("故障汇总：{}".format(summary_path), flush=True)
        return 1

    finally:
        try:
            if lock_path.exists():
                lock_path.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
