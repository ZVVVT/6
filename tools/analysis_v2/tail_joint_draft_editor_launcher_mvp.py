#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Analysis V2 TailDraft -> existing tail editor adapter MVP.

Purpose
-------
1. Preserve TailDraft objects as exact initial fragments.
2. Convert the remaining balanced green mask into graph-edge atomic fragments.
3. Generate the legacy editor's entries / paths / global-results contracts.
4. Launch the existing full-image editor with no formal publication.

This MVP deliberately does NOT publish TailFinalLabels and does NOT run
measurement.  The editor output remains in a separate calibration directory.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import cv2
    import numpy as np
    import tifffile
    from scipy.ndimage import distance_transform_edt
    from skimage.measure import regionprops
except ImportError as exc:
    print("缺少依赖：", exc)
    raise SystemExit(1) from exc


WINDOWS_CREATION_FLAGS = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if os.name == "nt"
    else 0
)


SCHEMA_VERSION = "tail_joint_draft_editor_adapter_mvp_v2_reuse"


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def resolve_one(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"未找到：{directory / pattern}")
    if len(matches) > 1:
        exact_tif = [item for item in matches if item.suffix.lower() in {".tif", ".tiff"}]
        if len(exact_tif) == 1:
            return exact_tif[0].resolve()
        raise RuntimeError(
            f"匹配到多个文件，无法确定：{pattern}\n"
            + "\n".join(str(item) for item in matches)
        )
    return matches[0].resolve()


def normalize_2d(image: np.ndarray, name: str) -> np.ndarray:
    array = np.asarray(image)
    if array.ndim > 2:
        array = np.squeeze(array)
    if array.ndim != 2:
        raise ValueError(f"{name}不是二维图像：{array.shape}")
    return array


def dense_edge_seed(
    shape: Tuple[int, int],
    graph_payload: Dict[str, Any],
) -> np.ndarray:
    seed = np.zeros(shape, dtype=np.uint16)
    edges = list(graph_payload.get("edges") or [])
    if len(edges) > np.iinfo(np.uint16).max - 1000:
        raise ValueError("图边数量超过uint16契约。")

    for edge in edges:
        edge_id = int(edge["edge_id"])
        points = np.asarray(edge.get("points_xy") or [], dtype=np.int32)
        if len(points) >= 2:
            cv2.polylines(
                seed,
                [points.reshape(-1, 1, 2)],
                False,
                edge_id,
                1,
                lineType=cv2.LINE_8,
            )
        elif len(points) == 1:
            x, y = [int(value) for value in points[0]]
            if 0 <= y < shape[0] and 0 <= x < shape[1]:
                seed[y, x] = edge_id
    return seed


def build_atomic_fragment_atlas(
    balanced_mask: np.ndarray,
    graph_payload: Dict[str, Any],
    draft_labels: np.ndarray,
    maximum_edge_distance_px: float,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Preserve TailDraft object IDs and fill the remaining high-recall mask with
    graph-edge-level atomic fragments.

    Draft labels occupy IDs 1..N.  Residual graph-edge fragments use
    N + edge_id, so they can never overwrite or merge with initial drafts.
    """
    mask = normalize_2d(balanced_mask, "balanced_mask") > 0
    draft = normalize_2d(draft_labels, "draft_labels").astype(np.uint16, copy=False)
    if mask.shape != draft.shape:
        raise ValueError(
            f"balanced mask与TailDraft尺寸不一致：{mask.shape} != {draft.shape}"
        )

    maximum_draft_id = int(draft.max())
    seed = dense_edge_seed(mask.shape, graph_payload)
    if not np.any(seed):
        raise ValueError("Stage 1.2图结构没有可用边中心线。")

    distance, nearest_indices = distance_transform_edt(
        seed == 0,
        return_indices=True,
    )
    nearest_edge_id = seed[nearest_indices[0], nearest_indices[1]]

    atlas = draft.copy()
    residual_support = (
        mask
        & (atlas == 0)
        & (nearest_edge_id > 0)
        & (distance <= float(maximum_edge_distance_px))
    )
    residual_ids = nearest_edge_id[residual_support].astype(np.uint32)
    shifted_ids = residual_ids + np.uint32(maximum_draft_id)

    maximum_output_id = int(shifted_ids.max()) if len(shifted_ids) else maximum_draft_id
    if maximum_output_id > np.iinfo(np.uint16).max:
        raise ValueError("原子碎片编号超过uint16范围。")
    atlas[residual_support] = shifted_ids.astype(np.uint16)

    mask_pixel_count = int(mask.sum())
    assigned_mask_pixel_count = int(np.sum((atlas > 0) & mask))
    residual_pixel_count = int(residual_support.sum())
    stats = {
        "maximum_draft_id": maximum_draft_id,
        "graph_edge_count": int(len(graph_payload.get("edges") or [])),
        "balanced_mask_pixel_count": mask_pixel_count,
        "assigned_balanced_mask_pixel_count": assigned_mask_pixel_count,
        "balanced_mask_coverage_ratio": (
            float(assigned_mask_pixel_count / mask_pixel_count)
            if mask_pixel_count
            else 0.0
        ),
        "draft_pixel_count": int(np.sum(draft > 0)),
        "residual_atomic_pixel_count": residual_pixel_count,
        "atlas_fragment_count": int(len(np.unique(atlas[atlas > 0]))),
        "maximum_edge_distance_px": float(maximum_edge_distance_px),
    }
    return atlas, stats


def head_records(head_labels: np.ndarray) -> List[Dict[str, Any]]:
    labels = normalize_2d(head_labels, "HeadFinalLabels")
    records: List[Dict[str, Any]] = []
    for region in regionprops(labels.astype(np.int32, copy=False)):
        records.append(
            {
                "head_id": int(region.label),
                "center_x": float(region.centroid[1]),
                "center_y": float(region.centroid[0]),
            }
        )
    records.sort(key=lambda item: item["head_id"])
    if not records:
        raise ValueError("HeadFinalLabels没有头部对象。")
    return records


def prepare_editor_contracts(
    head_labels: np.ndarray,
    refined_payload: Dict[str, Any],
    draft_objects_payload: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    heads = head_records(head_labels)
    draft_objects = list(draft_objects_payload.get("objects") or [])
    object_id_by_head = {
        int(item["head_id"]): int(item["object_id"])
        for item in draft_objects
    }
    accepted_head_ids = set(object_id_by_head)

    refined_by_head = {
        int(item["head_id"]): item
        for item in list(refined_payload.get("refined_chains") or [])
    }

    entries_results: List[Dict[str, Any]] = []
    path_results: List[Dict[str, Any]] = []
    global_results: List[Dict[str, Any]] = []

    for head in heads:
        head_id = int(head["head_id"])
        accepted = head_id in accepted_head_ids
        entries_results.append(
            {
                **head,
                "status": "auto_confirmed" if accepted else "manual_required",
            }
        )

        refined = refined_by_head.get(head_id)
        if refined is None:
            continue
        points_xy = list(refined.get("points_xy") or [])
        if len(points_xy) < 2:
            continue

        candidate = {
            "rank": 1,
            "points_xy": points_xy,
            "score": float(refined.get("refined_score", 0.0)),
            "length_px": float(refined.get("total_length_px", 0.0)),
            "selected_fragment_ids": (
                [int(object_id_by_head[head_id])]
                if accepted
                else []
            ),
            "source": "tail_joint_refined_mvp",
            "refined_status": str(refined.get("refined_status", "")),
            "review_reasons": list(refined.get("refined_reasons") or []),
        }
        path_results.append(
            {
                "head_id": head_id,
                "candidates": [candidate],
            }
        )

        if accepted:
            global_results.append(
                {
                    "head_id": head_id,
                    "status": "auto_confirmed_unique",
                    "selected_rank": 1,
                    "selected_candidate": candidate,
                    "review_reasons": list(refined.get("refined_reasons") or []),
                }
            )

    entries_payload = {
        "version": SCHEMA_VERSION,
        "results": entries_results,
    }
    paths_payload = {
        "version": SCHEMA_VERSION,
        "results": path_results,
    }
    global_payload = {
        "version": SCHEMA_VERSION,
        "results": global_results,
    }
    stats = {
        "head_count": len(heads),
        "draft_object_count": len(draft_objects),
        "accepted_head_count": len(accepted_head_ids),
        "heads_with_suggested_chain_count": len(path_results),
        "global_initial_result_count": len(global_results),
        "one_draft_per_head": len(accepted_head_ids) == len(draft_objects),
    }
    return entries_payload, paths_payload, global_payload, stats



def adapter_manifest_reusable(
    manifest_path: Path,
    *,
    field_id: str,
    required_sources: List[Path],
    required_outputs: List[Path],
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Validate that prepared editor inputs are complete and newer than sources."""
    if not manifest_path.is_file():
        return False, None, "manifest不存在"
    try:
        payload = load_json(manifest_path)
    except Exception as exc:
        return False, None, f"manifest读取失败：{exc}"
    if str(payload.get("field_id", "")) != str(field_id):
        return False, payload, "field_id不一致"
    validation = payload.get("validation")
    if not isinstance(validation, dict) or not bool(
        validation.get("ready_to_open_editor")
    ):
        return False, payload, "未标记ready_to_open_editor"
    if not all(path.is_file() and path.stat().st_size > 0 for path in required_outputs):
        return False, payload, "适配输出不完整"
    if not all(path.is_file() and path.stat().st_size > 0 for path in required_sources):
        return False, payload, "适配源文件不完整"
    prepared_mtime = manifest_path.stat().st_mtime
    newest_source_mtime = max(path.stat().st_mtime for path in required_sources)
    if prepared_mtime + 1e-6 < newest_source_mtime:
        return False, payload, "源文件晚于适配manifest"
    editor_arguments = payload.get("editor_arguments")
    if not isinstance(editor_arguments, list) or not editor_arguments:
        return False, payload, "缺少editor_arguments"
    return True, payload, ""


def update_argument(arguments: List[str], name: str, value: str) -> List[str]:
    result = [str(item) for item in arguments]
    try:
        index = result.index(name)
    except ValueError:
        result.extend([name, value])
    else:
        if index + 1 >= len(result):
            result.append(value)
        else:
            result[index + 1] = value
    return result

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="TailDraft接入现有尾部编辑器的离线适配器MVP"
    )
    parser.add_argument("--task-root", required=True)
    parser.add_argument("--field-id", required=True)
    parser.add_argument(
        "--maximum-edge-distance-px",
        type=float,
        default=8.0,
        help="balanced mask像素归入最近图边原子碎片的最大距离",
    )
    parser.add_argument(
        "--display-max-dim",
        type=int,
        default=1400,
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="只生成适配文件，不打开编辑器",
    )
    parser.add_argument(
        "--reuse-prepared",
        action="store_true",
        help="已有且未过期的适配文件直接复用，跳过全图距离变换和契约重建",
    )
    parser.add_argument(
        "--validate-editor-input",
        action="store_true",
        help="生成后调用编辑器--validate-only，不打开界面",
    )
    return parser


def main() -> int:
    started = time.perf_counter()
    args = build_parser().parse_args()

    task_root = Path(args.task_root).expanduser().resolve()
    field_id = str(args.field_id).strip()
    if not task_root.is_dir():
        raise FileNotFoundError(f"任务目录不存在：{task_root}")
    if not field_id:
        raise ValueError("field-id不能为空。")

    project_root = Path(__file__).resolve().parents[2]
    field_tail_root = task_root / "segmentation" / "tail" / field_id
    stage1 = field_tail_root / "stage1"
    stage1_2 = field_tail_root / "stage1_2"
    refined_dir = (
        task_root
        / "segmentation"
        / "tail_joint_refined_mvp"
        / field_id
    )
    draft_dir = (
        task_root
        / "segmentation"
        / "tail_joint_draft_mvp"
        / field_id
    )
    adapter_dir = (
        task_root
        / "segmentation"
        / "tail_joint_editor_adapter_mvp"
        / field_id
    )
    editor_output_dir = (
        task_root
        / "calibration"
        / "tail_joint_editor_mvp"
        / field_id
    )
    adapter_dir.mkdir(parents=True, exist_ok=True)
    editor_output_dir.mkdir(parents=True, exist_ok=True)

    merge_path = resolve_one(task_root / "input", f"{field_id}_Merge.*")
    green_path = resolve_one(task_root / "input", f"{field_id}_FITC.*")
    probability_path = (stage1 / "02_probability_uint16.tif").resolve()
    balanced_mask_path = (stage1 / "balanced_mask_uint8.tif").resolve()
    graph_path = (stage1_2 / "tail_graph_stage1_2.json").resolve()
    head_labels_path = (
        task_root / "calibration" / "head" / f"{field_id}_HeadFinalLabels.tif"
    ).resolve()
    refined_path = (refined_dir / "joint_chain_refined.json").resolve()
    draft_labels_path = (
        draft_dir / f"{field_id}_TailDraftLabels.tif"
    ).resolve()
    draft_objects_path = (
        draft_dir / f"{field_id}_TailDraftObjects.json"
    ).resolve()
    editor_script = (
        project_root
        / "tools"
        / "analysis_v2"
        / "tail_legacy"
        / "tail_result_editor_v2_3_draft_mvp.py"
    ).resolve()

    required_paths = [
        merge_path,
        green_path,
        probability_path,
        balanced_mask_path,
        graph_path,
        head_labels_path,
        refined_path,
        draft_labels_path,
        draft_objects_path,
        editor_script,
    ]
    missing = [path for path in required_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "缺少TailDraft编辑器适配输入：\n"
            + "\n".join(str(path) for path in missing)
        )

    atomic_fragments_path = (
        adapter_dir / f"{field_id}_TailDraftAtomicFragments.tif"
    )
    entries_path = adapter_dir / "draft_editor_entries.json"
    paths_path = adapter_dir / "draft_editor_paths.json"
    global_path = adapter_dir / "draft_editor_global_results.json"
    payload_path = adapter_dir / "tail_joint_editor_adapter_manifest.json"
    required_sources = [
        merge_path,
        green_path,
        probability_path,
        balanced_mask_path,
        graph_path,
        head_labels_path,
        refined_path,
        draft_labels_path,
        draft_objects_path,
    ]
    required_outputs = [
        atomic_fragments_path,
        entries_path,
        paths_path,
        global_path,
    ]

    if args.reuse_prepared:
        reusable, prepared_payload, reason = adapter_manifest_reusable(
            payload_path,
            field_id=field_id,
            required_sources=required_sources,
            required_outputs=required_outputs,
        )
        if reusable and prepared_payload is not None:
            print(f"TailDraft编辑器适配文件已复用：{payload_path}")
            print("跳过balanced mask全图距离变换和编辑器契约重建。")
            if args.prepare_only:
                return 0
            editor_arguments = update_argument(
                list(prepared_payload["editor_arguments"]),
                "--display-max-dim",
                str(max(900, int(args.display_max_dim))),
            )
            command = [str(Path(sys.executable).resolve())] + editor_arguments
            if args.validate_editor_input:
                command.append("--validate-only")
            completed = subprocess.run(
                command,
                cwd=str(editor_output_dir),
                creationflags=WINDOWS_CREATION_FLAGS,
            )
            if completed.returncode:
                raise RuntimeError(
                    f"TailDraft编辑器退出码：{completed.returncode}"
                )
            return 0
        print(f"已有适配文件不可复用，将重新生成：{reason}")

    balanced_mask = tifffile.imread(str(balanced_mask_path))
    draft_labels = tifffile.imread(str(draft_labels_path))
    head_labels = tifffile.imread(str(head_labels_path))
    graph_payload = load_json(graph_path)
    refined_payload = load_json(refined_path)
    draft_objects_payload = load_json(draft_objects_path)

    atlas, atlas_stats = build_atomic_fragment_atlas(
        balanced_mask,
        graph_payload,
        draft_labels,
        maximum_edge_distance_px=args.maximum_edge_distance_px,
    )
    tifffile.imwrite(str(atomic_fragments_path), atlas.astype(np.uint16))

    (
        entries_payload,
        paths_payload,
        global_payload,
        contract_stats,
    ) = prepare_editor_contracts(
        head_labels,
        refined_payload,
        draft_objects_payload,
    )

    write_json(entries_path, entries_payload)
    write_json(paths_path, paths_payload)
    write_json(global_path, global_payload)

    editor_arguments = [
        str(editor_script),
        "--merge", str(merge_path),
        "--green", str(green_path),
        "--probability", str(probability_path),
        "--fragments", str(atomic_fragments_path),
        "--head-labels", str(head_labels_path),
        "--entries", str(entries_path),
        "--paths", str(paths_path),
        "--global-results", str(global_path),
        "--output-dir", str(editor_output_dir),
        "--manual-margin", "60",
        "--manual-radius", "5",
        "--display-max-dim", str(max(900, int(args.display_max_dim))),
    ]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "purpose": (
            "TailDraft人工校准适配层；编辑器输出仍不得直接测量或发布。"
        ),
        "task_root": str(task_root),
        "field_id": field_id,
        "python_executable": str(Path(sys.executable).resolve()),
        "editor_script": str(editor_script),
        "editor_arguments": editor_arguments,
        "sources": {
            "merge": str(merge_path),
            "green": str(green_path),
            "probability": str(probability_path),
            "balanced_mask": str(balanced_mask_path),
            "graph": str(graph_path),
            "head_labels": str(head_labels_path),
            "refined": str(refined_path),
            "draft_labels": str(draft_labels_path),
            "draft_objects": str(draft_objects_path),
        },
        "outputs": {
            "atomic_fragments": str(atomic_fragments_path),
            "entries": str(entries_path),
            "paths": str(paths_path),
            "global_results": str(global_path),
            "editor_output_dir": str(editor_output_dir),
        },
        "validation": {
            **atlas_stats,
            **contract_stats,
            "ready_to_open_editor": True,
            "ready_for_measurement": False,
            "ready_for_publication": False,
        },
        "elapsed_seconds_before_editor": float(time.perf_counter() - started),
    }
    write_json(payload_path, payload)

    print("TailDraft编辑器适配文件已生成。")
    print(f"头部数：{contract_stats['head_count']}")
    print(f"初始草稿：{contract_stats['draft_object_count']}")
    print(
        "balanced mask覆盖率："
        f"{atlas_stats['balanced_mask_coverage_ratio']:.3%}"
    )
    print(f"适配清单：{payload_path}")
    print(f"编辑输出目录：{editor_output_dir}")

    if args.prepare_only:
        return 0

    command = [str(Path(sys.executable).resolve())] + editor_arguments
    if args.validate_editor_input:
        command.append("--validate-only")

    completed = subprocess.run(
        command,
        cwd=str(editor_output_dir),
        creationflags=WINDOWS_CREATION_FLAGS,
    )
    if completed.returncode:
        raise RuntimeError(
            f"TailDraft编辑器退出码：{completed.returncode}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
