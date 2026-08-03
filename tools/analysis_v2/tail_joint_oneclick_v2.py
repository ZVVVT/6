#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Analysis V2 联合尾部三视野一键校准编排 v2。

目标：把多个单视野 MVP 命令收敛为一次可续跑操作。

默认流程（按 worker_input.json 的视野顺序）：
1. 自动补齐 Stage 1～1.2 图结构前置结果
2. 起始候选
3. 链候选
4. 联合精炼
5. 中心线驱动区域预览
6. TailDraft 导出
7. 依次打开人工编辑器
8. 编辑器关闭并保存后，导出 TailFinal Candidate
9. 转换为正式文件名的隔离暂存契约

安全边界：
- 不写 calibration/tail；
- 不运行测量管道；
- 不发布 cp_output；
- 不写数据库；
- 已完成暂存的视野自动跳过；
- 任一步失败立即停止，可修复后重复执行继续。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

WINDOWS_CREATION_FLAGS = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if os.name == "nt"
    else 0
)

VERSION = "tail_joint_oneclick_v2_5_graceful_manual_cancel"

GRAPH_PREREQUISITE_SCRIPTS = (
    "tail_legacy/tail_graph_stage1_extract.py",
    "tail_legacy/tail_graph_stage1_1_topology_clean.py",
    "tail_legacy/tail_graph_stage1_2_build_graph.py",
)

SCRIPT_STEPS = [
    (
        "start_candidate",
        "tail_joint_start_candidate_mvp.py",
        lambda root, field: root
        / "segmentation"
        / "tail_joint_mvp"
        / field
        / "joint_start_candidates.json",
    ),
    (
        "chain_candidate",
        "tail_joint_chain_candidate_mvp.py",
        lambda root, field: root
        / "segmentation"
        / "tail_joint_chain_mvp"
        / field
        / "joint_chain_candidates.json",
    ),
    (
        "refine_candidate",
        "tail_joint_refine_candidate_mvp.py",
        lambda root, field: root
        / "segmentation"
        / "tail_joint_refined_mvp"
        / field
        / "joint_chain_refined.json",
    ),
    (
        "region_preview",
        "tail_joint_region_preview_mvp.py",
        lambda root, field: root
        / "segmentation"
        / "tail_joint_region_preview_mvp"
        / field
        / "joint_region_preview.json",
    ),
    (
        "draft_export",
        "tail_joint_draft_export_mvp.py",
        lambda root, field: root
        / "segmentation"
        / "tail_joint_draft_mvp"
        / field
        / "tail_joint_draft_manifest.json",
    ),
]

EDITOR_REQUIRED_FILES = (
    "edited_tail_results.json",
    "edited_tail_regions_head_id_uint16.tif",
    "edited_tail_head_id_labels_uint16.tif",
    "edited_tail_centerlines_uint16.tif",
    "edited_tail_region_conflicts.json",
)

STAGING_REQUIRED_FILES = (
    "TailFinalLabels.tif",
    "TailFinalHeadIdLabels.tif",
    "TailPositiveHeadLabels.tif",
    "TailFinalObjects.json",
)


class ManualCalibrationCancelled(RuntimeError):
    """用户关闭人工尾部编辑器且未保存。

    这属于可恢复的人工取消，不应按算法或自动流程失败处理。
    """

    def __init__(self, field_id: str, output_dir: Path) -> None:
        self.field_id = str(field_id or "").strip()
        self.output_dir = Path(output_dir).resolve()
        super().__init__(
            "{} 人工尾部校准窗口已关闭且未保存；本次流程已取消。".format(
                self.field_id
            )
        )


@dataclass
class StepRecord:
    field_id: str
    step: str
    status: str
    elapsed_seconds: float
    output: str = ""
    message: str = ""


class LockedLogHandle:
    """Serialize log writes from parallel per-field preparation/finalization."""

    def __init__(self, handle) -> None:
        self._handle = handle
        self._lock = threading.RLock()

    def write(self, value: str) -> int:
        with self._lock:
            return self._handle.write(value)

    def flush(self) -> None:
        with self._lock:
            self._handle.flush()


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("JSON 顶层必须是对象：{}".format(path))
    return value


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def discover_fields(task_root: Path) -> List[str]:
    worker_input = task_root / "worker_input.json"
    if not worker_input.is_file():
        raise FileNotFoundError("未找到 worker_input.json：{}".format(worker_input))
    payload = read_json(worker_input)
    fields: List[str] = []
    for item in list(payload.get("fields") or []):
        if not isinstance(item, dict):
            continue
        field_id = str(item.get("field_id", "") or "").strip()
        if field_id and field_id not in fields:
            fields.append(field_id)
    if not fields:
        raise RuntimeError("worker_input.json 中没有视野顺序。")
    return fields



def _resolve_worker_input_path(task_root: Path, raw_value: Any) -> Path:
    value = str(raw_value or "").strip()
    if not value:
        raise ValueError("worker_input.json 缺少输入图像路径。")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = task_root / path
    return path.resolve()


def resolve_field_inputs(task_root: Path, field_id: str) -> Dict[str, Path]:
    worker_input_path = task_root / "worker_input.json"
    payload = read_json(worker_input_path)
    field_payload = None
    for item in list(payload.get("fields") or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("field_id", "") or "").strip() == field_id:
            field_payload = item
            break
    if field_payload is None:
        raise KeyError("worker_input.json 中找不到视野：{}".format(field_id))

    fitc_path = _resolve_worker_input_path(
        task_root,
        field_payload.get("fitc_path"),
    )
    merge_path = _resolve_worker_input_path(
        task_root,
        field_payload.get("merge_path"),
    )
    head_labels_path = (
        task_root
        / "calibration"
        / "head"
        / "{}_HeadFinalLabels.tif".format(field_id)
    ).resolve()

    missing = [
        str(path)
        for path in (fitc_path, merge_path, head_labels_path)
        if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "视野 {} 缺少联合尾部前置输入：\n{}".format(
                field_id,
                "\n".join(missing),
            )
        )
    return {
        "fitc": fitc_path,
        "merge": merge_path,
        "head_labels": head_labels_path,
    }


def graph_prerequisites_paths(task_root: Path, field_id: str) -> Dict[str, Path]:
    field_root = task_root / "segmentation" / "tail" / field_id
    stage1 = field_root / "stage1"
    stage1_1 = field_root / "stage1_1"
    stage1_2 = field_root / "stage1_2"
    return {
        "field_root": field_root,
        "stage1": stage1,
        "stage1_1": stage1_1,
        "stage1_2": stage1_2,
        "probability": stage1 / "02_probability_uint16.tif",
        "balanced_mask": stage1 / "balanced_mask_uint8.tif",
        "clean_skeleton": stage1_1 / "prune20_cleaned_skeleton_uint8.tif",
        "graph": stage1_2 / "tail_graph_stage1_2.json",
    }


def graph_prerequisites_ready(task_root: Path, field_id: str) -> bool:
    paths = graph_prerequisites_paths(task_root, field_id)
    required = (
        paths["probability"],
        paths["balanced_mask"],
        paths["clean_skeleton"],
        paths["graph"],
    )
    if not all(path.is_file() and path.stat().st_size > 0 for path in required):
        return False
    try:
        graph = read_json(paths["graph"])
    except Exception:
        return False
    return isinstance(graph.get("nodes"), list) and isinstance(
        graph.get("edges"), list
    )


def prepare_graph_prerequisites(
    *,
    task_root: Path,
    field_id: str,
    python_executable: Path,
    project_root: Path,
    scripts: Dict[str, Path],
    force: bool,
    records: List["StepRecord"],
    log_handle,
) -> None:
    paths = graph_prerequisites_paths(task_root, field_id)
    if not force and graph_prerequisites_ready(task_root, field_id):
        records.append(
            StepRecord(
                field_id=field_id,
                step="graph_prerequisites",
                status="skipped_existing",
                elapsed_seconds=0.0,
                output=str(paths["graph"]),
            )
        )
        print(
            "[BATCH] SKIP {} graph_prerequisites（Stage 1～1.2 已存在）".format(
                field_id
            )
        )
        return

    inputs = resolve_field_inputs(task_root, field_id)
    paths["field_root"].mkdir(parents=True, exist_ok=True)

    commands = (
        (
            "graph_stage1",
            [
                str(python_executable),
                "-u",
                str(scripts["tail_legacy/tail_graph_stage1_extract.py"]),
                "--green",
                str(inputs["fitc"]),
                "--merge",
                str(inputs["merge"]),
                "--head-labels",
                str(inputs["head_labels"]),
                "--output-dir",
                str(paths["stage1"]),
            ],
            (paths["probability"], paths["balanced_mask"]),
        ),
        (
            "graph_stage1_1",
            [
                str(python_executable),
                "-u",
                str(
                    scripts[
                        "tail_legacy/tail_graph_stage1_1_topology_clean.py"
                    ]
                ),
                "--stage1-dir",
                str(paths["stage1"]),
                "--merge",
                str(inputs["merge"]),
                "--head-labels",
                str(inputs["head_labels"]),
                "--output-dir",
                str(paths["stage1_1"]),
            ],
            (paths["clean_skeleton"],),
        ),
        (
            "graph_stage1_2",
            [
                str(python_executable),
                "-u",
                str(
                    scripts[
                        "tail_legacy/tail_graph_stage1_2_build_graph.py"
                    ]
                ),
                "--stage1-1-dir",
                str(paths["stage1_1"]),
                "--stage1-dir",
                str(paths["stage1"]),
                "--merge",
                str(inputs["merge"]),
                "--output-dir",
                str(paths["stage1_2"]),
            ],
            (paths["graph"],),
        ),
    )

    total_elapsed = 0.0
    for step_name, command, expected_files in commands:
        elapsed = run_command(
            command,
            cwd=paths["field_root"],
            log_handle=log_handle,
            label="{} {}".format(field_id, step_name),
        )
        total_elapsed += elapsed
        missing = [
            str(path)
            for path in expected_files
            if not path.is_file() or path.stat().st_size <= 0
        ]
        if missing:
            raise RuntimeError(
                "{} 完成但缺少输出：\n{}".format(
                    step_name,
                    "\n".join(missing),
                )
            )

    if not graph_prerequisites_ready(task_root, field_id):
        raise RuntimeError(
            "{} Stage 1～1.2 输出校验失败：{}".format(
                field_id,
                paths["graph"],
            )
        )

    records.append(
        StepRecord(
            field_id=field_id,
            step="graph_prerequisites",
            status="completed",
            elapsed_seconds=total_elapsed,
            output=str(paths["graph"]),
        )
    )


def normalize_requested_fields(
    ordered_fields: Sequence[str],
    requested: Optional[Sequence[str]],
) -> List[str]:
    if not requested:
        return list(ordered_fields)
    flattened: List[str] = []
    for raw in requested:
        for value in str(raw).replace(",", " ").split():
            value = value.strip()
            if value and value not in flattened:
                flattened.append(value)
    unknown = [value for value in flattened if value not in ordered_fields]
    if unknown:
        raise ValueError("指定了不存在的视野：{}".format(", ".join(unknown)))
    requested_set = set(flattened)
    return [field for field in ordered_fields if field in requested_set]


def editor_output_dir(task_root: Path, field_id: str) -> Path:
    return task_root / "calibration" / "tail_joint_editor_mvp" / field_id


def final_candidate_dir(task_root: Path, field_id: str) -> Path:
    return task_root / "calibration" / "tail_joint_final_candidate_mvp" / field_id


def staging_dir(task_root: Path, field_id: str) -> Path:
    return task_root / "calibration" / "tail_joint_promotion_staging_mvp" / field_id


def manifest_flag(payload: dict, name: str) -> bool:
    """兼容状态字段位于 manifest 顶层或 validation 子对象。"""
    if name in payload:
        return bool(payload.get(name))
    validation = payload.get("validation")
    if isinstance(validation, dict):
        return bool(validation.get(name))
    return False


def draft_ready(task_root: Path, field_id: str) -> bool:
    path = (
        task_root
        / "segmentation"
        / "tail_joint_draft_mvp"
        / field_id
        / "tail_joint_draft_manifest.json"
    )
    if not path.is_file():
        return False
    try:
        payload = read_json(path)
    except Exception:
        return False
    validation = payload.get("validation")
    if isinstance(validation, dict) and not bool(validation.get("valid")):
        return False
    return manifest_flag(payload, "ready_for_manual_calibration") and not manifest_flag(
        payload, "ready_for_measurement"
    )


def editor_results_ready(task_root: Path, field_id: str) -> bool:
    directory = editor_output_dir(task_root, field_id)
    return all((directory / name).is_file() for name in EDITOR_REQUIRED_FILES)


def final_candidate_ready(task_root: Path, field_id: str) -> bool:
    directory = final_candidate_dir(task_root, field_id)
    manifest_path = directory / "tail_final_candidate_manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = read_json(manifest_path)
    except Exception:
        return False
    if not bool(manifest.get("ready_for_promotion")):
        return False
    if bool(manifest.get("ready_for_measurement")):
        return False
    field_prefix = field_id + "_"
    expected = (
        field_prefix + "TailFinalLabelsCandidate.tif",
        field_prefix + "TailFinalHeadIdLabelsCandidate.tif",
        field_prefix + "TailPositiveHeadLabelsCandidate.tif",
        field_prefix + "TailFinalObjectsCandidate.json",
    )
    return all((directory / name).is_file() for name in expected)


def staging_ready(task_root: Path, field_id: str) -> bool:
    directory = staging_dir(task_root, field_id)
    manifest_path = directory / "tail_promotion_staging_manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = read_json(manifest_path)
    except Exception:
        return False
    if not bool(manifest.get("ready_for_batch_promotion")):
        return False
    if bool(manifest.get("ready_for_measurement")):
        return False
    if bool(manifest.get("formal_calibration_modified")):
        return False
    expected = (
        field_id + "_TailFinalLabels.tif",
        field_id + "_TailFinalHeadIdLabels.tif",
        field_id + "_TailPositiveHeadLabels.tif",
        field_id + "_TailFinalObjects.json",
    )
    return all((directory / name).is_file() for name in expected)


def validate_json_output(path: Path, step_name: str) -> None:
    if not path.is_file():
        raise RuntimeError("{} 未生成输出：{}".format(step_name, path))
    payload = read_json(path)
    if step_name == "draft_export":
        validation = payload.get("validation")
        if isinstance(validation, dict) and not bool(validation.get("valid")):
            raise RuntimeError("TailDraft validation.valid=false：{}".format(path))
        if not manifest_flag(payload, "ready_for_manual_calibration"):
            raise RuntimeError("TailDraft 未标记为可人工校准：{}".format(path))
        if manifest_flag(payload, "ready_for_measurement"):
            raise RuntimeError("TailDraft 不应标记为可测量：{}".format(path))


def run_command(
    command: Sequence[str],
    cwd: Path,
    log_handle,
    label: str,
) -> float:
    started = time.perf_counter()
    command_text = subprocess.list2cmdline([str(value) for value in command])
    banner = "\n[BATCH] START {}\n[BATCH] COMMAND {}\n".format(
        label,
        command_text,
    )
    print(banner, end="", flush=True)
    log_handle.write(banner)
    log_handle.flush()

    process = subprocess.Popen(
        [str(value) for value in command],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1,
        creationflags=WINDOWS_CREATION_FLAGS,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        log_handle.write(line)
    return_code = process.wait()
    elapsed = time.perf_counter() - started
    footer = "[BATCH] DONE {} elapsed={:.3f}s return_code={}\n".format(
        label,
        elapsed,
        return_code,
    )
    print(footer, end="", flush=True)
    log_handle.write(footer)
    log_handle.flush()
    if return_code != 0:
        raise RuntimeError("{} 执行失败，return_code={}".format(label, return_code))
    return elapsed


def ensure_scripts(script_dir: Path) -> Dict[str, Path]:
    names = list(GRAPH_PREREQUISITE_SCRIPTS) + [
        item[1] for item in SCRIPT_STEPS
    ] + [
        "tail_joint_draft_editor_launcher_mvp.py",
        "tail_joint_final_candidate_export_mvp.py",
        "tail_joint_promotion_staging_mvp.py",
        "tail_legacy/tail_result_editor_v2_3_draft_mvp.py",
    ]
    result: Dict[str, Path] = {}
    missing: List[str] = []
    for name in names:
        path = (script_dir / name).resolve()
        if not path.is_file():
            missing.append(str(path))
        result[name] = path
    if missing:
        raise FileNotFoundError("缺少批量流程依赖脚本：\n" + "\n".join(missing))
    return result


def automatic_prepare_field(
    *,
    task_root: Path,
    field_id: str,
    python_executable: Path,
    project_root: Path,
    scripts: Dict[str, Path],
    force: bool,
    records: List[StepRecord],
    log_handle,
) -> None:
    prepare_graph_prerequisites(
        task_root=task_root,
        field_id=field_id,
        python_executable=python_executable,
        project_root=project_root,
        scripts=scripts,
        force=force,
        records=records,
        log_handle=log_handle,
    )

    for step_name, script_name, output_builder in SCRIPT_STEPS:
        output_path = output_builder(task_root, field_id)
        if not force and output_path.is_file():
            try:
                validate_json_output(output_path, step_name)
                records.append(
                    StepRecord(
                        field_id=field_id,
                        step=step_name,
                        status="skipped_existing",
                        elapsed_seconds=0.0,
                        output=str(output_path),
                    )
                )
                print("[BATCH] SKIP {} {}（已有有效输出）".format(field_id, step_name))
                continue
            except Exception:
                pass

        command = [
            str(python_executable),
            "-u",
            str(scripts[script_name]),
            "--task-root",
            str(task_root),
            "--field-id",
            field_id,
        ]
        elapsed = run_command(
            command,
            cwd=project_root,
            log_handle=log_handle,
            label="{} {}".format(field_id, step_name),
        )
        validate_json_output(output_path, step_name)
        records.append(
            StepRecord(
                field_id=field_id,
                step=step_name,
                status="completed",
                elapsed_seconds=elapsed,
                output=str(output_path),
            )
        )


def run_editor_and_stage(
    *,
    task_root: Path,
    field_id: str,
    python_executable: Path,
    project_root: Path,
    scripts: Dict[str, Path],
    display_max_dim: int,
    prepare_only: bool,
    force_editor: bool,
    records: List[StepRecord],
    log_handle,
) -> None:
    if staging_ready(task_root, field_id):
        records.append(
            StepRecord(
                field_id=field_id,
                step="promotion_staging",
                status="skipped_staged",
                elapsed_seconds=0.0,
                output=str(staging_dir(task_root, field_id)),
            )
        )
        print("[BATCH] SKIP {}（暂存契约已通过）".format(field_id))
        return

    if not draft_ready(task_root, field_id):
        raise RuntimeError("{} 的 TailDraft 未准备完成。".format(field_id))

    edited_ready = editor_results_ready(task_root, field_id)
    if prepare_only:
        command = [
            str(python_executable),
            "-u",
            str(scripts["tail_joint_draft_editor_launcher_mvp.py"]),
            "--task-root",
            str(task_root),
            "--field-id",
            field_id,
            "--display-max-dim",
            str(display_max_dim),
            "--prepare-only",
            "--validate-editor-input",
        ]
        elapsed = run_command(
            command,
            cwd=project_root,
            log_handle=log_handle,
            label="{} editor_prepare".format(field_id),
        )
        records.append(
            StepRecord(
                field_id=field_id,
                step="editor_prepare",
                status="completed",
                elapsed_seconds=elapsed,
            )
        )
        return

    if force_editor or not edited_ready:
        print("\n[BATCH] 即将打开 {} 人工尾部校准窗口。".format(field_id))
        print("[BATCH] 完成编辑后点击保存并关闭窗口，下一视野会自动继续。\n")
        command = [
            str(python_executable),
            "-u",
            str(scripts["tail_joint_draft_editor_launcher_mvp.py"]),
            "--task-root",
            str(task_root),
            "--field-id",
            field_id,
            "--display-max-dim",
            str(display_max_dim),
        ]
        elapsed = run_command(
            command,
            cwd=project_root,
            log_handle=log_handle,
            label="{} manual_editor".format(field_id),
        )
        records.append(
            StepRecord(
                field_id=field_id,
                step="manual_editor",
                status="completed",
                elapsed_seconds=elapsed,
                output=str(editor_output_dir(task_root, field_id)),
            )
        )
    else:
        records.append(
            StepRecord(
                field_id=field_id,
                step="manual_editor",
                status="skipped_existing",
                elapsed_seconds=0.0,
                output=str(editor_output_dir(task_root, field_id)),
            )
        )
        print("[BATCH] SKIP {} 编辑器（已有完整人工编辑结果）".format(field_id))

    if not editor_results_ready(task_root, field_id):
        raise ManualCalibrationCancelled(
            field_id,
            editor_output_dir(task_root, field_id),
        )

    if final_candidate_ready(task_root, field_id):
        records.append(
            StepRecord(
                field_id=field_id,
                step="final_candidate_export",
                status="skipped_existing",
                elapsed_seconds=0.0,
                output=str(final_candidate_dir(task_root, field_id)),
            )
        )
    else:
        command = [
            str(python_executable),
            "-u",
            str(scripts["tail_joint_final_candidate_export_mvp.py"]),
            "--task-root",
            str(task_root),
            "--field-id",
            field_id,
        ]
        elapsed = run_command(
            command,
            cwd=project_root,
            log_handle=log_handle,
            label="{} final_candidate_export".format(field_id),
        )
        if not final_candidate_ready(task_root, field_id):
            raise RuntimeError("{} TailFinal Candidate 导出后校验失败。".format(field_id))
        records.append(
            StepRecord(
                field_id=field_id,
                step="final_candidate_export",
                status="completed",
                elapsed_seconds=elapsed,
                output=str(final_candidate_dir(task_root, field_id)),
            )
        )

    if staging_ready(task_root, field_id):
        records.append(
            StepRecord(
                field_id=field_id,
                step="promotion_staging",
                status="skipped_existing",
                elapsed_seconds=0.0,
                output=str(staging_dir(task_root, field_id)),
            )
        )
    else:
        command = [
            str(python_executable),
            "-u",
            str(scripts["tail_joint_promotion_staging_mvp.py"]),
            "--task-root",
            str(task_root),
            "--field-id",
            field_id,
        ]
        elapsed = run_command(
            command,
            cwd=project_root,
            log_handle=log_handle,
            label="{} promotion_staging".format(field_id),
        )
        if not staging_ready(task_root, field_id):
            raise RuntimeError("{} 隔离暂存生成后校验失败。".format(field_id))
        records.append(
            StepRecord(
                field_id=field_id,
                step="promotion_staging",
                status="completed",
                elapsed_seconds=elapsed,
                output=str(staging_dir(task_root, field_id)),
            )
        )



def prepare_editor_adapter(
    *,
    task_root: Path,
    field_id: str,
    python_executable: Path,
    project_root: Path,
    scripts: Dict[str, Path],
    display_max_dim: int,
    log_handle,
) -> List[StepRecord]:
    if staging_ready(task_root, field_id):
        return [
            StepRecord(
                field_id=field_id,
                step="editor_prepare",
                status="skipped_staged",
                elapsed_seconds=0.0,
                output=str(staging_dir(task_root, field_id)),
            )
        ]
    command = [
        str(python_executable),
        "-u",
        str(scripts["tail_joint_draft_editor_launcher_mvp.py"]),
        "--task-root",
        str(task_root),
        "--field-id",
        field_id,
        "--display-max-dim",
        str(display_max_dim),
        "--prepare-only",
        "--reuse-prepared",
    ]
    elapsed = run_command(
        command,
        cwd=project_root,
        log_handle=log_handle,
        label="{} editor_prepare".format(field_id),
    )
    return [
        StepRecord(
            field_id=field_id,
            step="editor_prepare",
            status="completed",
            elapsed_seconds=elapsed,
            output=str(
                task_root
                / "segmentation"
                / "tail_joint_editor_adapter_mvp"
                / field_id
            ),
        )
    ]



def editor_adapter_manifest_path(task_root: Path, field_id: str) -> Path:
    return (
        task_root
        / "segmentation"
        / "tail_joint_editor_adapter_mvp"
        / field_id
        / "tail_joint_editor_adapter_manifest.json"
    )


def editor_adapter_ready(task_root: Path, field_id: str) -> bool:
    if staging_ready(task_root, field_id) or editor_results_ready(task_root, field_id):
        return True
    manifest_path = editor_adapter_manifest_path(task_root, field_id)
    if not draft_ready(task_root, field_id):
        return False
    if not manifest_path.is_file() or manifest_path.stat().st_size <= 0:
        return False
    try:
        payload = read_json(manifest_path)
    except Exception:
        return False
    return bool(payload)


def wait_for_editor_adapter(
    *,
    task_root: Path,
    field_id: str,
    timeout_seconds: float,
    poll_seconds: float = 0.25,
) -> bool:
    if editor_adapter_ready(task_root, field_id):
        return True

    timeout_seconds = max(1.0, float(timeout_seconds))
    started = time.perf_counter()
    next_report = started
    print(
        "[BATCH] {} 尚未准备好，等待对应尾部后台任务；"
        "不会等待其他视野。".format(field_id)
    )

    while True:
        if editor_adapter_ready(task_root, field_id):
            elapsed = time.perf_counter() - started
            print(
                "[BATCH] {} 已准备完成，等待 {:.1f}s，立即打开人工窗口。".format(
                    field_id,
                    elapsed,
                )
            )
            return True

        now = time.perf_counter()
        if now - started >= timeout_seconds:
            return False
        if now >= next_report:
            print(
                "[BATCH] 正在等待 {} 尾部准备，已等待 {:.1f}s。".format(
                    field_id,
                    now - started,
                )
            )
            next_report = now + 5.0
        time.sleep(max(0.05, float(poll_seconds)))


def resolve_task_output_path(
    task_root: Path,
    raw_value: Optional[str],
    default_path: Path,
) -> Path:
    value = str(raw_value or "").strip()
    if not value:
        return default_path.resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = task_root / path
    return path.resolve()


def run_manual_editor_only(
    *,
    task_root: Path,
    field_id: str,
    python_executable: Path,
    project_root: Path,
    scripts: Dict[str, Path],
    display_max_dim: int,
    force_editor: bool,
    log_handle,
) -> List[StepRecord]:
    if staging_ready(task_root, field_id):
        print("[BATCH] SKIP {}（暂存契约已通过）".format(field_id))
        return [
            StepRecord(
                field_id=field_id,
                step="manual_editor",
                status="skipped_staged",
                elapsed_seconds=0.0,
                output=str(staging_dir(task_root, field_id)),
            )
        ]
    if not draft_ready(task_root, field_id):
        raise RuntimeError("{} 的 TailDraft 未准备完成。".format(field_id))
    if not force_editor and editor_results_ready(task_root, field_id):
        print("[BATCH] SKIP {} 编辑器（已有完整人工编辑结果）".format(field_id))
        return [
            StepRecord(
                field_id=field_id,
                step="manual_editor",
                status="skipped_existing",
                elapsed_seconds=0.0,
                output=str(editor_output_dir(task_root, field_id)),
            )
        ]

    print("\n[BATCH] 打开 {} 人工尾部校准窗口。".format(field_id))
    print("[BATCH] 保存并关闭后将立即打开下一视野；本视野导出在后台完成。\n")
    command = [
        str(python_executable),
        "-u",
        str(scripts["tail_joint_draft_editor_launcher_mvp.py"]),
        "--task-root",
        str(task_root),
        "--field-id",
        field_id,
        "--display-max-dim",
        str(display_max_dim),
        "--reuse-prepared",
    ]
    elapsed = run_command(
        command,
        cwd=project_root,
        log_handle=log_handle,
        label="{} manual_editor".format(field_id),
    )
    if not editor_results_ready(task_root, field_id):
        raise ManualCalibrationCancelled(
            field_id,
            editor_output_dir(task_root, field_id),
        )
    return [
        StepRecord(
            field_id=field_id,
            step="manual_editor",
            status="completed",
            elapsed_seconds=elapsed,
            output=str(editor_output_dir(task_root, field_id)),
        )
    ]


def finalize_edited_field(
    *,
    task_root: Path,
    field_id: str,
    python_executable: Path,
    project_root: Path,
    scripts: Dict[str, Path],
    display_max_dim: int,
    log_handle,
) -> List[StepRecord]:
    local_records: List[StepRecord] = []
    run_editor_and_stage(
        task_root=task_root,
        field_id=field_id,
        python_executable=python_executable,
        project_root=project_root,
        scripts=scripts,
        display_max_dim=display_max_dim,
        prepare_only=False,
        force_editor=False,
        records=local_records,
        log_handle=log_handle,
    )
    return local_records


def automatic_prepare_field_collect(**kwargs) -> List[StepRecord]:
    local_records: List[StepRecord] = []
    automatic_prepare_field(records=local_records, **kwargs)
    return local_records


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="一次完成多个视野的联合尾部草稿、人工校准和隔离暂存。"
    )
    parser.add_argument("--task-root", required=True)
    parser.add_argument(
        "--fields",
        nargs="*",
        help="可选；支持空格或逗号分隔。未指定时按 worker_input.json 处理全部视野。",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="只批量生成/校验草稿与编辑器输入，不打开界面，也不生成 Candidate/暂存。",
    )
    parser.add_argument(
        "--force-auto",
        action="store_true",
        help="强制重跑起始候选到 TailDraft 自动阶段。",
    )
    parser.add_argument(
        "--force-editor",
        action="store_true",
        help="即使已有人工编辑结果，也重新打开编辑器。",
    )
    parser.add_argument(
        "--auto-workers",
        type=int,
        default=2,
        help="自动尾部准备的并行视野数；默认2，设为1可恢复串行。",
    )
    parser.add_argument(
        "--finalize-workers",
        type=int,
        default=1,
        help="人工窗口关闭后的后台导出并行数；默认1，避免影响下一窗口交互。",
    )
    parser.add_argument(
        "--manual-stream",
        action="store_true",
        help=(
            "不再等待全部视野自动准备完成；按视野顺序等待当前视野就绪后"
            "立即打开人工窗口，后续视野可由外部后台任务继续准备。"
        ),
    )
    parser.add_argument(
        "--wait-ready-timeout",
        type=float,
        default=300.0,
        help="manual-stream 等待单个视野后台准备的超时秒数；超时后本进程安全补算。",
    )
    parser.add_argument(
        "--log-path",
        default="",
        help="可选日志路径；相对路径按 task-root 解析。",
    )
    parser.add_argument(
        "--summary-path",
        default="",
        help="可选汇总路径；相对路径按 task-root 解析。",
    )
    parser.add_argument("--display-max-dim", type=int, default=1400)
    return parser


def main() -> int:
    started = time.perf_counter()
    args = build_parser().parse_args()
    task_root = Path(args.task_root).expanduser().resolve()
    if not task_root.is_dir():
        raise FileNotFoundError("任务目录不存在：{}".format(task_root))

    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parents[1]
    python_executable = Path(sys.executable).resolve()
    scripts = ensure_scripts(script_dir)

    ordered_fields = discover_fields(task_root)
    selected_fields = normalize_requested_fields(ordered_fields, args.fields)
    if not selected_fields:
        raise ValueError("没有需要处理的视野。")

    logs_dir = task_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = resolve_task_output_path(
        task_root,
        args.log_path,
        logs_dir / "tail_joint_oneclick_v2.log",
    )
    summary_path = resolve_task_output_path(
        task_root,
        args.summary_path,
        logs_dir / "tail_joint_oneclick_v2_summary.json",
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    records: List[StepRecord] = []
    error: Optional[str] = None
    success = False
    cancelled = False
    cancelled_field = ""
    cancel_message = ""

    current_script = Path(__file__).resolve()
    print("联合尾部一键校准 v2")
    print("版本：{}".format(VERSION))
    print("脚本文件：{}".format(current_script))
    print("脚本SHA256：{}".format(file_sha256(current_script)))
    print("Python：{}".format(python_executable))
    print("任务目录：{}".format(task_root))
    print("视野顺序：{}".format(", ".join(selected_fields)))
    print("安全状态：不会修改 calibration/tail，不测量、不发布、不写数据库。")

    try:
        with log_path.open("a", encoding="utf-8") as log_handle:
            log_handle.write(
                "\n===== {} task={} fields={} =====\n".format(
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                    task_root,
                    ",".join(selected_fields),
                )
            )

            locked_log = LockedLogHandle(log_handle)
            display_max_dim = max(600, int(args.display_max_dim))

            if bool(args.manual_stream):
                if bool(args.prepare_only):
                    raise ValueError("--manual-stream 不能与 --prepare-only 同时使用。")

                print(
                    "[BATCH] 启用实时人工流水线：当前视野准备好即打开；"
                    "后续视野继续由头部流程后台准备。"
                )
                finalize_workers = max(1, int(args.finalize_workers))
                finalize_futures = []
                with ThreadPoolExecutor(
                    max_workers=finalize_workers,
                    thread_name_prefix="tail-finalize",
                ) as finalize_executor:
                    for field_id in selected_fields:
                        if (
                            not staging_ready(task_root, field_id)
                            and not editor_results_ready(task_root, field_id)
                            and not editor_adapter_ready(task_root, field_id)
                        ):
                            ready = wait_for_editor_adapter(
                                task_root=task_root,
                                field_id=field_id,
                                timeout_seconds=float(args.wait_ready_timeout),
                            )
                            if not ready:
                                print(
                                    "[BATCH] {} 等待后台准备超时，"
                                    "本进程开始安全补算当前视野。".format(field_id)
                                )
                                records.extend(
                                    automatic_prepare_field_collect(
                                        task_root=task_root,
                                        field_id=field_id,
                                        python_executable=python_executable,
                                        project_root=project_root,
                                        scripts=scripts,
                                        force=bool(args.force_auto),
                                        log_handle=locked_log,
                                    )
                                )
                                records.extend(
                                    prepare_editor_adapter(
                                        task_root=task_root,
                                        field_id=field_id,
                                        python_executable=python_executable,
                                        project_root=project_root,
                                        scripts=scripts,
                                        display_max_dim=display_max_dim,
                                        log_handle=locked_log,
                                    )
                                )

                        editor_records = run_manual_editor_only(
                            task_root=task_root,
                            field_id=field_id,
                            python_executable=python_executable,
                            project_root=project_root,
                            scripts=scripts,
                            display_max_dim=display_max_dim,
                            force_editor=bool(args.force_editor),
                            log_handle=locked_log,
                        )
                        records.extend(editor_records)
                        if staging_ready(task_root, field_id):
                            continue
                        finalize_futures.append(
                            (
                                field_id,
                                finalize_executor.submit(
                                    finalize_edited_field,
                                    task_root=task_root,
                                    field_id=field_id,
                                    python_executable=python_executable,
                                    project_root=project_root,
                                    scripts=scripts,
                                    display_max_dim=display_max_dim,
                                    log_handle=locked_log,
                                ),
                            )
                        )

                    print("[BATCH] 人工窗口全部完成，等待后台导出收尾。")
                    for field_id, future in finalize_futures:
                        records.extend(future.result())
                        print("[BATCH] {} 后台导出和暂存完成。".format(field_id))
            else:
                # 第一阶段：不同视野自动准备互不写同一输出目录，最多并行2个。
                pending_auto_fields = []
                for field_id in selected_fields:
                    if staging_ready(task_root, field_id):
                        records.append(
                            StepRecord(
                                field_id=field_id,
                                step="automatic_prepare",
                                status="skipped_staged",
                                elapsed_seconds=0.0,
                                output=str(staging_dir(task_root, field_id)),
                            )
                        )
                        print("[BATCH] {} 已暂存，跳过自动阶段。".format(field_id))
                    else:
                        pending_auto_fields.append(field_id)

                auto_workers = max(
                    1,
                    min(int(args.auto_workers), len(pending_auto_fields) or 1),
                )
                print(
                    "[BATCH] 自动准备：待处理{}个视野，并行数{}。".format(
                        len(pending_auto_fields),
                        auto_workers,
                    )
                )
                if auto_workers == 1:
                    for field_id in pending_auto_fields:
                        records.extend(
                            automatic_prepare_field_collect(
                                task_root=task_root,
                                field_id=field_id,
                                python_executable=python_executable,
                                project_root=project_root,
                                scripts=scripts,
                                force=bool(args.force_auto),
                                log_handle=locked_log,
                            )
                        )
                else:
                    with ThreadPoolExecutor(max_workers=auto_workers) as executor:
                        futures = {
                            executor.submit(
                                automatic_prepare_field_collect,
                                task_root=task_root,
                                field_id=field_id,
                                python_executable=python_executable,
                                project_root=project_root,
                                scripts=scripts,
                                force=bool(args.force_auto),
                                log_handle=locked_log,
                            ): field_id
                            for field_id in pending_auto_fields
                        }
                        for future in as_completed(futures):
                            field_id = futures[future]
                            records.extend(future.result())
                            print("[BATCH] {} 自动准备完成。".format(field_id))

                # 第二阶段：先预生成全部编辑器适配输入，避免视野切换时等待。
                adapter_fields = [
                    field_id
                    for field_id in selected_fields
                    if not staging_ready(task_root, field_id)
                ]
                adapter_workers = max(1, min(2, len(adapter_fields) or 1))
                if adapter_fields:
                    print("[BATCH] 预加载{}个尾部校准视野。".format(len(adapter_fields)))
                    with ThreadPoolExecutor(max_workers=adapter_workers) as executor:
                        futures = {
                            executor.submit(
                                prepare_editor_adapter,
                                task_root=task_root,
                                field_id=field_id,
                                python_executable=python_executable,
                                project_root=project_root,
                                scripts=scripts,
                                display_max_dim=display_max_dim,
                                log_handle=locked_log,
                            ): field_id
                            for field_id in adapter_fields
                        }
                        for future in as_completed(futures):
                            records.extend(future.result())

                if not bool(args.prepare_only):
                    # 第三阶段：人工窗口按顺序打开；上一视野导出在后台进行，
                    # 不再阻塞下一视野窗口。
                    finalize_workers = max(1, int(args.finalize_workers))
                    finalize_futures = []
                    with ThreadPoolExecutor(
                        max_workers=finalize_workers,
                        thread_name_prefix="tail-finalize",
                    ) as finalize_executor:
                        for field_id in selected_fields:
                            editor_records = run_manual_editor_only(
                                task_root=task_root,
                                field_id=field_id,
                                python_executable=python_executable,
                                project_root=project_root,
                                scripts=scripts,
                                display_max_dim=display_max_dim,
                                force_editor=bool(args.force_editor),
                                log_handle=locked_log,
                            )
                            records.extend(editor_records)
                            if staging_ready(task_root, field_id):
                                continue
                            finalize_futures.append(
                                (
                                    field_id,
                                    finalize_executor.submit(
                                        finalize_edited_field,
                                        task_root=task_root,
                                        field_id=field_id,
                                        python_executable=python_executable,
                                        project_root=project_root,
                                        scripts=scripts,
                                        display_max_dim=display_max_dim,
                                        log_handle=locked_log,
                                    ),
                                )
                            )

                        print("[BATCH] 人工窗口全部完成，等待后台导出收尾。")
                        for field_id, future in finalize_futures:
                            records.extend(future.result())
                            print("[BATCH] {} 后台导出和暂存完成。".format(field_id))

        success = True
    except ManualCalibrationCancelled as exception:
        cancelled = True
        cancelled_field = exception.field_id
        cancel_message = (
            "用户关闭 {} 人工尾部校准窗口且未保存；"
            "本次尾部流程已正常取消。任务目录、自动候选和此前已保存结果均保留，"
            "重新运行可继续。输出目录：{}"
        ).format(exception.field_id, exception.output_dir)
        error = None
        print("\n[BATCH] 用户取消：{}".format(cancel_message))
    except BaseException as exception:
        error = "".join(
            traceback.format_exception(
                type(exception),
                exception,
                exception.__traceback__,
            )
        )
        print("\n[BATCH] 失败：{}".format(exception), file=sys.stderr)
    finally:
        staged_fields = [
            field_id for field_id in selected_fields if staging_ready(task_root, field_id)
        ]
        payload = {
            "version": VERSION,
            "success": bool(success),
            "cancelled": bool(cancelled),
            "cancelled_field": cancelled_field,
            "cancel_message": cancel_message,
            "task_root": str(task_root),
            "python_executable": str(python_executable),
            "selected_fields": selected_fields,
            "staged_fields": staged_fields,
            "all_selected_fields_staged": (
                not bool(args.prepare_only)
                and len(staged_fields) == len(selected_fields)
            ),
            "prepare_only": bool(args.prepare_only),
            "manual_stream": bool(args.manual_stream),
            "wait_ready_timeout": float(args.wait_ready_timeout),
            "log_path": str(log_path),
            "summary_path": str(summary_path),
            "auto_workers": max(1, int(args.auto_workers)),
            "finalize_workers": max(1, int(args.finalize_workers)),
            "formal_calibration_modified": False,
            "ready_for_measurement": False,
            "records": [asdict(record) for record in records],
            "error": error,
            "elapsed_seconds": float(time.perf_counter() - started),
        }
        write_json(summary_path, payload)

    print("\n批量流程汇总：{}".format(summary_path))
    print("已完成隔离暂存：{}".format(", ".join(
        field_id for field_id in selected_fields if staging_ready(task_root, field_id)
    ) or "无"))
    print("calibration/tail 未修改。")
    if success and not bool(args.prepare_only):
        staged_now = [
            field_id for field_id in selected_fields
            if staging_ready(task_root, field_id)
        ]
        if len(staged_now) == len(selected_fields):
            print("全部视野已完成隔离暂存，可进入三视野原子提升。")
        else:
            print("尚未全部暂存：{}".format(
                ", ".join(
                    field for field in selected_fields
                    if field not in staged_now
                )
            ))

    return 0 if success else (2 if cancelled else 1)


if __name__ == "__main__":
    raise SystemExit(main())
