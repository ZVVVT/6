"""尾部编辑器保存结果的轻量发布逻辑。

本模块只处理文件、任务状态和 manifest，不导入任何图像算法依赖。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

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


def build_initial_c18b_tail_workset(
    adapter_dir: Path,
    head_final_labels_path: Path,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """构建全新 C18B Editor 直接保存的 Workset，不读取人工编辑状态。

    输入须为绝对路径。对象全集仅取 fragments.tif；关联仅使用 Adapter
    明确提供的可信候选及 fragment ID，不进行路径邻域推断或重新匹配。
    """
    from PIL import Image

    directory = Path(adapter_dir)
    head_path = Path(head_final_labels_path)
    if not directory.is_absolute() or not head_path.is_absolute():
        raise ValueError("Adapter 和 Head 标签路径必须是绝对路径。")
    with Image.open(directory / "fragments.tif") as image:
        fragments = np.asarray(image).copy()
    with Image.open(head_path) as image:
        heads = np.asarray(image).copy()
    for name, labels in (("fragments", fragments), ("HeadFinalLabels", heads)):
        if (labels.ndim != 2 or not np.issubdtype(labels.dtype, np.integer)
                or np.any(labels < 0)):
            raise ValueError("{} 必须是二维非负整数标签图。".format(name))
    if heads.shape != fragments.shape:
        raise ValueError("HeadFinalLabels 与 fragments 尺寸不一致。")

    def results(filename: str) -> List[Dict[str, Any]]:
        with (directory / filename).open("r", encoding="utf-8") as handle:
            return list((json.load(handle) or {}).get("results", []))

    entries = results("entries.json")
    if not entries:
        raise ValueError("没有读取到任何头部记录。")
    paths = {int(row["head_id"]): row for row in results("paths.json")}
    globals_by_head = {
        int(row["head_id"]): row for row in results("global_results.json")
    }
    head_ids = set(int(value) for value in np.unique(heads) if value > 0)
    associated_by_fragment = {}  # type: Dict[int, int]
    # Editor 的 records 按 head_id 排序，同一 fragment 首个可信记录优先。
    for entry in sorted(entries, key=lambda row: int(row["head_id"])):
        head_id = int(entry["head_id"])
        global_result = globals_by_head.get(head_id, {})
        selected = global_result.get("selected_candidate") or {}
        points = np.asarray(selected.get("points_xy") or [], dtype=np.float32)
        if (head_id not in head_ids or entry.get("status") != "auto_confirmed"
                or global_result.get("status") != "auto_confirmed_unique"
                or points.ndim != 2 or points.shape[1] != 2 or len(points) < 2):
            continue
        rank = global_result.get("selected_rank")
        if rank is None:
            continue
        candidates = sorted(
            paths.get(head_id, {}).get("candidates", []),
            key=lambda row: int(row.get("rank", 999)),
        )
        for candidate in candidates:
            if int(candidate.get("rank", -1)) != int(rank):
                continue
            for value in candidate.get("selected_fragment_ids", []):
                fragment_id = int(value)
                if fragment_id > 0:
                    associated_by_fragment.setdefault(fragment_id, head_id)
            break

    fragment_ids = np.unique(fragments[fragments > 0])
    if len(fragment_ids) > np.iinfo(np.uint16).max:
        raise ValueError("Tail Workset对象数超过uint16可表示范围。")
    labels = np.zeros(fragments.shape, dtype=np.uint16)
    rows = []
    for object_id, value in enumerate(fragment_ids, start=1):
        fragment_id = int(value)
        head_id = associated_by_fragment.get(fragment_id)
        labels[fragments == fragment_id] = object_id
        rows.append({
            "tail_object_id": object_id,
            "workset_label_id": object_id,
            "accepted": True,
            "association_status": "associated" if head_id is not None else "unresolved",
            "head_label_id": head_id,
            "source": "auto",
            "fragment_label_id": fragment_id,
        })
    return labels, {
        "version": 1,
        "saved_at_unix": time.time(),
        "not_for_measurement": True,
        "not_for_publication": True,
        "accepted_count": len(rows),
        "objects": rows,
    }


def save_initial_c18b_tail_workset(
    adapter_dir: Path,
    head_final_labels_path: Path,
    output_dir: Path,
) -> Tuple[Path, Path]:
    """仅保存初始 Workset 的 TIFF/JSON，不发布、不更新任务或 manifest。"""
    from PIL import Image

    directory = Path(output_dir)
    if not directory.is_absolute():
        raise ValueError("Workset 输出路径必须是绝对路径。")
    labels, payload = build_initial_c18b_tail_workset(adapter_dir, head_final_labels_path)
    directory.mkdir(parents=True, exist_ok=True)
    labels_path = directory / "TailWorksetLabels.tif"
    json_path = directory / "tail_workset.json"
    Image.fromarray(labels).save(labels_path)
    atomic_write_json(json_path, payload)
    return labels_path, json_path


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


def build_automatic_tail_final_contract(
    field_id: str,
    output_dir: Path,
    head_final_labels_path: Path,
) -> Dict[str, Any]:
    """将已保存的自动 Workset 转为正式契约，不更新任务或 manifest。

    必须同时提供 Workset TIFF/JSON；不回退到旧 Editor 区域图。
    编号、关联验证和全部正式产物均复用正式 calibration builder。
    """
    directory = Path(output_dir).resolve()
    for name in ("TailWorksetLabels.tif", "tail_workset.json"):
        if not (directory / name).is_file():
            raise FileNotFoundError("自动 Tail contract 缺少 Workset：{}".format(directory / name))
    return build_tail_final_contract(field_id, directory, head_final_labels_path)


def build_tail_final_contract(
    field_id: str,
    output_dir: Path,
    head_final_labels_path: Path,
) -> Dict[str, Any]:
    """从 Tail Workset（优先）或旧 head_id 区域图生成正式标签契约。"""
    directory = Path(output_dir).resolve()
    workset_labels_path = directory / "TailWorksetLabels.tif"
    workset_json_path = directory / "tail_workset.json"
    source = directory / "edited_tail_regions_head_id_uint16.tif"
    has_workset = workset_labels_path.is_file() and workset_json_path.is_file()
    if not has_workset and not source.is_file():
        raise FileNotFoundError("请在尾部编辑器中点击保存结果")

    if has_workset:
        workset_labels = read_label_image(workset_labels_path)
        head_labels = read_label_image(
            Path(head_final_labels_path).resolve(),
            expected_shape=tuple(workset_labels.shape),
        )
        with workset_json_path.open("r", encoding="utf-8") as handle:
            workset_payload = json.load(handle) or {}
        rows = [
            dict(item)
            for item in (workset_payload.get("objects") or [])
            if bool(item.get("accepted"))
        ]
        rows.sort(key=lambda item: int(item.get("tail_object_id") or 0))
        if not rows:
            raise ValueError(
                "视野 {} 没有人工接受的尾部，拒绝进入测量。".format(field_id)
            )

        available_head_ids = set(
            int(value)
            for value in np.unique(head_labels[head_labels > 0]).tolist()
        )
        object_labels = np.zeros(workset_labels.shape, dtype=np.uint16)
        head_id_labels = np.zeros(workset_labels.shape, dtype=np.uint16)
        positive_head_labels = np.zeros(head_labels.shape, dtype=np.uint16)
        objects = []
        used_workset_ids = set()
        used_head_ids = set()
        for object_id, row in enumerate(rows, start=1):
            workset_id = int(row.get("workset_label_id") or 0)
            if workset_id <= 0 or workset_id in used_workset_ids:
                raise ValueError("Tail Workset 的 accepted object label 非法或重复。")
            used_workset_ids.add(workset_id)
            mask = workset_labels == workset_id
            if not np.any(mask):
                raise ValueError(
                    "Tail Workset label {} 不存在。".format(workset_id)
                )

            status = str(row.get("association_status") or "").strip()
            head_value = row.get("head_label_id")
            if status == "associated":
                if head_value is None:
                    raise ValueError("associated tail 不允许 head_label_id=null。")
                head_id = int(head_value)
                if head_id not in available_head_ids:
                    raise ValueError(
                        "视野 {} 的尾部 head_label_id 在 HeadFinalLabels 中不存在：{}".format(
                            field_id, head_id
                        )
                    )
                if head_id in used_head_ids:
                    raise ValueError("多个 accepted tail 不允许关联同一 head_label_id。")
                used_head_ids.add(head_id)
                positive_head_labels[head_labels == head_id] = object_id
                head_id_labels[mask] = head_id
            elif status == "unresolved":
                if head_value is not None:
                    raise ValueError("unresolved tail 不允许携带 head_label_id。")
                head_id = None
            else:
                raise ValueError("未知 association_status：{}".format(status))

            object_labels[mask] = object_id
            objects.append({
                "tail_object_id": object_id,
                "head_label_id": head_id,
                "association_status": status,
                "pixel_count": int(np.count_nonzero(mask)),
                "source": row.get("source"),
                "fragment_label_id": row.get("fragment_label_id"),
            })
    else:
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
                "tail_object_id": object_id,
                "head_label_id": head_id,
                "association_status": "associated",
                "pixel_count": int(np.count_nonzero(head_id_labels == head_id)),
                "source": "edited_tail_regions_head_id_uint16.tif",
                "fragment_label_id": None,
            })

    head_id_path = directory / "{}_TailFinalHeadIdLabels.tif".format(field_id)
    region_path = directory / "{}_TailFinalLabels.tif".format(field_id)
    positive_path = directory / "{}_TailPositiveHeadLabels.tif".format(field_id)
    objects_path = directory / "{}_TailFinalObjects.json".format(field_id)
    atomic_save_label_image(head_id_path, head_id_labels)
    atomic_save_label_image(region_path, object_labels)
    atomic_save_label_image(positive_path, positive_head_labels)
    associated_count = sum(
        item["association_status"] == "associated" for item in objects
    )
    unresolved_count = len(objects) - associated_count
    payload = {
        "schema_version": 2,
        "field_id": field_id,
        "tail_object_count": len(objects),
        "associated_object_count": associated_count,
        "unresolved_object_count": unresolved_count,
        # object_count 保留为旧 reader 的兼容别名。
        "object_count": len(objects),
        "objects": objects,
        "region_label_path": str(region_path),
        "head_id_label_path": str(head_id_path),
        "positive_head_label_path": str(positive_path),
    }
    atomic_write_json(objects_path, payload)

    expected = list(range(1, len(objects) + 1))
    region_stats = validate_label_image(object_labels)
    positive_stats = validate_label_image(positive_head_labels, require_objects=False)
    if region_stats["positive_labels"] != expected:
        raise ValueError("TailFinalLabels 未严格连续编号为 1...N。")
    associated_ids = [
        int(item["tail_object_id"])
        for item in objects
        if item["association_status"] == "associated"
    ]
    if positive_stats["positive_labels"] != associated_ids:
        raise ValueError("TailPositiveHeadLabels 与 associated tail IDs 不一致。")
    return {
        "tail_final_labels": str(region_path),
        "tail_final_head_id_labels": str(head_id_path),
        "tail_positive_head_labels": str(positive_path),
        "tail_final_objects": str(objects_path),
        "object_count": len(objects),
        "tail_object_count": len(objects),
        "associated_object_count": associated_count,
        "unresolved_object_count": unresolved_count,
        "head_ids": [
            int(item["head_label_id"])
            for item in objects
            if item["head_label_id"] is not None
        ],
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
    return register_tail_final_contract(payload, contract)


def register_tail_final_contract(field_payload, contract):
    """Register an already built contract using the shared calibration manifest roles."""
    payload = dict(field_payload)
    paths = task_paths_from_root(Path(payload["task_root"]))
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
    *,
    automatic: bool = False,
) -> Dict[str, object]:
    paths = task_paths_from_root(task_root)
    state = TaskStateStore.from_task_paths(paths).update(
        "tail_calibrated",
        "tail_calibration",
        "全部视野自动尾部校准已完成" if automatic else "全部视野人工尾部校准已完成",
    )
    return {
        "task_root": str(paths.task_root),
        "state": state,
        "fields": [dict(item) for item in results],
        "manifest": ManifestStore.from_task_paths(paths).load(),
    }
