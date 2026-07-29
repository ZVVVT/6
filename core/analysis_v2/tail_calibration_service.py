"""尾部编辑器保存结果的轻量发布逻辑。

本模块只处理文件、任务状态和 manifest，不导入任何图像算法依赖。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

import numpy as np

from .label_image_io import (
    atomic_save_label_image,
    read_label_image,
    relabel_consecutive,
    validate_label_image,
)
from .manifest_store import ManifestStore
from .task_paths import AnalysisTaskPaths
from .task_state import TaskStateStore, atomic_write_json


def task_paths_from_root(task_root: Path) -> AnalysisTaskPaths:
    root = Path(task_root).resolve()
    state_path = root / "state.json"
    with state_path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    task_id = str(state.get("task_id", "") or "").strip()
    if not task_id:
        raise RuntimeError("state.json 缺少 task_id。")
    project_root = root
    for parent in root.parents:
        if (parent / "app").is_dir() and (parent / "core").is_dir():
            project_root = parent
            break
    return AnalysisTaskPaths._build(project_root, root, task_id)


def mark_tail_stage(task_root: Path, status: str, message: str) -> None:
    paths = task_paths_from_root(task_root)
    TaskStateStore.from_task_paths(paths).update(
        status,
        "tail_path" if status in {"tail_segmenting", "tail_segmented"} else "tail_calibration",
        message,
    )


def _read_conflicts(output_dir: Path) -> List[Dict[str, Any]]:
    path = output_dir / "edited_tail_region_conflicts.json"
    if not path.is_file():
        raise FileNotFoundError("尾部结果缺少冲突记录：{}".format(path))
    with path.open("r", encoding="utf-8") as handle:
        conflicts = list((json.load(handle) or {}).get("conflicts") or [])
    effective = [
        dict(item)
        for item in conflicts
        if int(item.get("conflict_pixels") or 0) > 0
    ]
    if effective:
        raise ValueError(
            "尾部人工结果存在有效区域冲突，请重新处理后保存：{}".format(
                effective
            )
        )
    return effective


def build_tail_final_contract(
    field_id: str,
    output_dir: Path,
    head_final_labels_path: Path,
) -> Dict[str, Any]:
    """从编辑器 head_id 区域图生成可测量的连续对象标签契约。"""
    directory = Path(output_dir).resolve()
    source = directory / "edited_tail_regions_head_id_uint16.tif"
    if not source.is_file():
        raise FileNotFoundError("请在尾部编辑器中点击保存结果")
    _read_conflicts(directory)

    head_id_labels = read_label_image(source)
    head_labels = read_label_image(
        Path(head_final_labels_path).resolve(),
        expected_shape=tuple(head_id_labels.shape),
    )
    head_ids = [
        int(value)
        for value in np.unique(head_id_labels[head_id_labels > 0]).tolist()
    ]
    if not head_ids:
        raise ValueError("视野 {} 没有人工接受的尾部，拒绝进入测量。".format(field_id))

    available_head_ids = set(
        int(value)
        for value in np.unique(head_labels[head_labels > 0]).tolist()
    )
    missing = [head_id for head_id in head_ids if head_id not in available_head_ids]
    if missing:
        raise ValueError(
            "视野 {} 的尾部 head_id 在 HeadFinalLabels 中不存在：{}".format(
                field_id, missing
            )
        )

    object_labels, head_to_object = relabel_consecutive(head_id_labels)
    positive_head_labels = np.zeros(head_labels.shape, dtype=np.uint16)
    objects = []
    for head_id in head_ids:
        object_id = int(head_to_object[head_id])
        positive_head_labels[head_labels == head_id] = object_id
        objects.append({
            "object_id": object_id,
            "head_id": head_id,
            "pixel_count": int(np.count_nonzero(head_id_labels == head_id)),
            "source": "edited_tail_regions_head_id_uint16.tif",
            "accepted": True,
        })

    head_id_path = directory / "{}_TailFinalHeadIdLabels.tif".format(field_id)
    region_path = directory / "{}_TailFinalLabels.tif".format(field_id)
    positive_path = directory / "{}_TailPositiveHeadLabels.tif".format(field_id)
    objects_path = directory / "{}_TailFinalObjects.json".format(field_id)
    atomic_save_label_image(head_id_path, head_id_labels)
    atomic_save_label_image(region_path, object_labels)
    atomic_save_label_image(positive_path, positive_head_labels)
    payload = {
        "schema_version": 1,
        "field_id": field_id,
        "object_count": len(objects),
        "objects": objects,
        "region_label_path": str(region_path),
        "head_id_label_path": str(head_id_path),
        "positive_head_label_path": str(positive_path),
    }
    atomic_write_json(objects_path, payload)

    expected = list(range(1, len(objects) + 1))
    region_stats = validate_label_image(object_labels)
    positive_stats = validate_label_image(positive_head_labels)
    if region_stats["positive_labels"] != expected:
        raise ValueError("TailFinalLabels 未严格连续编号为 1...N。")
    if positive_stats["positive_labels"] != expected:
        raise ValueError("TailPositiveHeadLabels 未严格连续编号为 1...N。")
    return {
        "tail_final_labels": str(region_path),
        "tail_final_head_id_labels": str(head_id_path),
        "tail_positive_head_labels": str(positive_path),
        "tail_final_objects": str(objects_path),
        "object_count": len(objects),
        "head_ids": head_ids,
    }


def publish_tail_final_labels(field_payload: Dict[str, str]) -> Dict[str, str]:
    payload = dict(field_payload)
    output_dir = Path(payload["output_dir"]).resolve()
    paths = task_paths_from_root(Path(payload["task_root"]))
    head_final_labels = (
        paths.calibration_head_dir
        / "{}_HeadFinalLabels.tif".format(payload["field_id"])
    )
    contract = build_tail_final_contract(
        payload["field_id"], output_dir, head_final_labels
    )
    manifest = ManifestStore.from_task_paths(paths)
    roles = (
        ("tail_final_labels", "tail_final_labels", "image/tiff"),
        ("tail_final_head_id_labels", "tail_final_head_id_labels", "image/tiff"),
        ("tail_positive_head_labels", "tail_positive_head_labels", "image/tiff"),
        ("tail_final_objects", "tail_final_objects", "application/json"),
    )
    for key, role, media_type in roles:
        manifest.add_file(
            Path(contract[key]),
            role=role,
            stage="tail_calibration",
            media_type=media_type,
            metadata={"field_id": payload["field_id"]},
        )
    payload.update({key: str(value) for key, value in contract.items()})
    return payload


def complete_tail_calibration(
    task_root: Path,
    results: Sequence[Dict[str, str]],
) -> Dict[str, object]:
    paths = task_paths_from_root(task_root)
    state = TaskStateStore.from_task_paths(paths).update(
        "tail_calibrated",
        "tail_calibration",
        "全部视野人工尾部校准已完成",
    )
    return {
        "task_root": str(paths.task_root),
        "state": state,
        "fields": [dict(item) for item in results],
        "manifest": ManifestStore.from_task_paths(paths).load(),
    }
