"""尾部编辑器保存结果的轻量发布逻辑。

本模块只处理文件、任务状态和 manifest，不导入任何图像算法依赖。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, Sequence

from .manifest_store import ManifestStore
from .task_paths import AnalysisTaskPaths
from .task_state import TaskStateStore


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


def publish_tail_final_labels(field_payload: Dict[str, str]) -> Dict[str, str]:
    payload = dict(field_payload)
    output_dir = Path(payload["output_dir"]).resolve()
    source = output_dir / "edited_tail_regions_head_id_uint16.tif"
    if not source.is_file():
        raise FileNotFoundError("请在尾部编辑器中点击保存结果")

    target = output_dir / "{}_TailFinalLabels.tif".format(payload["field_id"])
    shutil.copy2(str(source), str(target))
    paths = task_paths_from_root(Path(payload["task_root"]))
    ManifestStore.from_task_paths(paths).add_file(
        target,
        role="tail_final_labels",
        stage="tail_calibration",
        media_type="image/tiff",
        metadata={"field_id": payload["field_id"]},
    )
    payload["tail_final_labels"] = str(target)
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
