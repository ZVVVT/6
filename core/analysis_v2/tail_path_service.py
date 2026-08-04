"""Analysis V2 尾部历史完整路径流程适配层。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import cv2
import numpy as np
import tifffile


class TailPathService:
    """按历史 Stage 1 -> Stage 2.3 顺序处理一个视野。"""

    def __init__(
        self,
        project_root: Path,
        task_root: Path,
        python_executable: Path = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.task_root = Path(task_root).resolve()
        self.source_dir = (
            self.project_root / "tools" / "analysis_v2" / "tail_legacy"
        )
        self.python_executable = Path(
            python_executable or sys.executable
        ).resolve()

    def _run(
        self,
        script_name: str,
        arguments: List[str],
        cwd: Path,
        field_id: str,
        stage_name: str,
    ) -> float:
        script_path = self.source_dir / script_name
        if not script_path.is_file():
            raise FileNotFoundError("尾部历史算法不存在：{}".format(script_path))

        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["MPLBACKEND"] = "Agg"
        cache_dir = self.task_root / "calibration" / "tail" / ".cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        environment["XDG_CACHE_HOME"] = str(cache_dir)
        environment["POOCH_HOME"] = str(cache_dir)
        environment["LOCALAPPDATA"] = str(cache_dir)

        print(
            "[TAIL_PATH_TIMING] START field={} stage={} script={}".format(
                field_id,
                stage_name,
                script_name,
            ),
            flush=True,
        )
        started = time.perf_counter()
        completed = subprocess.run(
            [str(self.python_executable), str(script_path)]
            + [str(value) for value in arguments],
            cwd=str(cwd),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        elapsed = time.perf_counter() - started

        print(
            (
                "[TAIL_PATH_TIMING] DONE field={} stage={} "
                "elapsed_seconds={:.6f} return_code={}"
            ).format(
                field_id,
                stage_name,
                elapsed,
                completed.returncode,
            ),
            flush=True,
        )

        if completed.returncode:
            raise RuntimeError(
                "{} 执行失败（{}，耗时 {:.2f} 秒）：\n{}".format(
                    script_name,
                    completed.returncode,
                    elapsed,
                    completed.stdout,
                )
            )
        return elapsed

    @staticmethod
    def _connected_fragment_labels(mask_path: Path, output_path: Path) -> None:
        """把历史 Stage 1 高召回掩模转换为编辑器所需碎片标签契约。"""
        mask = tifffile.imread(str(mask_path))
        if mask.ndim > 2:
            mask = np.squeeze(mask)
        _, labels = cv2.connectedComponents((mask > 0).astype(np.uint8), 8)
        tifffile.imwrite(str(output_path), labels.astype(np.uint16))

    def run_field(
        self,
        field_id: str,
        green_path: Path,
        merge_path: Path,
        head_labels_path: Path,
    ) -> Dict[str, Any]:
        field_started = time.perf_counter()
        field_root = self.task_root / "segmentation" / "tail" / field_id
        field_root.mkdir(parents=True, exist_ok=True)

        stage1 = field_root / "stage1"
        stage1_1 = field_root / "stage1_1"
        stage1_2 = field_root / "stage1_2"
        stage2_1 = field_root / "stage2_1"
        stage2_2 = field_root / "stage2_2"
        stage2_3 = field_root / "stage2_3"
        editor_dir = self.task_root / "calibration" / "tail" / field_id

        stage_seconds: Dict[str, float] = {}

        stage_seconds["stage1"] = self._run(
            "tail_graph_stage1_extract.py",
            [
                "--green", green_path,
                "--merge", merge_path,
                "--head-labels", head_labels_path,
                "--output-dir", stage1,
            ],
            field_root,
            field_id,
            "Stage 1",
        )
        stage_seconds["stage1_1"] = self._run(
            "tail_graph_stage1_1_topology_clean.py",
            [
                "--stage1-dir", stage1,
                "--merge", merge_path,
                "--head-labels", head_labels_path,
                "--output-dir", stage1_1,
            ],
            field_root,
            field_id,
            "Stage 1.1",
        )
        stage_seconds["stage1_2"] = self._run(
            "tail_graph_stage1_2_build_graph.py",
            [
                "--stage1-1-dir", stage1_1,
                "--stage1-dir", stage1,
                "--merge", merge_path,
                "--output-dir", stage1_2,
            ],
            field_root,
            field_id,
            "Stage 1.2",
        )
        stage_seconds["stage2_1"] = self._run(
            "tail_graph_stage2_1_head_entry_match_v1_1_baseline.py",
            [
                "--graph", stage1_2 / "tail_graph_stage1_2.json",
                "--probability", stage1 / "02_probability_uint16.tif",
                "--head-labels", head_labels_path,
                "--merge", merge_path,
                "--output-dir", stage2_1,
            ],
            field_root,
            field_id,
            "Stage 2.1",
        )
        stage_seconds["stage2_2"] = self._run(
            "tail_graph_stage2_2_beam_path_v1_2_fullfix.py",
            [
                "--graph", stage1_2 / "tail_graph_stage1_2.json",
                "--entries", stage2_1 / "head_graph_entry_results.json",
                "--probability", stage1 / "02_probability_uint16.tif",
                "--merge", merge_path,
                "--output-dir", stage2_2,
            ],
            field_root,
            field_id,
            "Stage 2.2",
        )
        stage_seconds["stage2_3"] = self._run(
            "tail_graph_stage2_3_global_unique_v1_1.py",
            [
                "--paths", stage2_2 / "path_results.json",
                "--graph", stage1_2 / "tail_graph_stage1_2.json",
                "--merge", merge_path,
                "--output-dir", stage2_3,
            ],
            field_root,
            field_id,
            "Stage 2.3",
        )

        fragments_path = field_root / "{}_TailFragmentsLabels.tif".format(field_id)
        fragments_started = time.perf_counter()
        self._connected_fragment_labels(
            stage1 / "balanced_mask_uint8.tif",
            fragments_path,
        )
        fragment_labels_seconds = time.perf_counter() - fragments_started

        editor_dir.mkdir(parents=True, exist_ok=True)
        field_total_seconds = time.perf_counter() - field_started

        print(
            (
                "[TAIL_PATH_TIMING] FIELD_DONE field={} total_seconds={:.6f} "
                "fragment_labels_seconds={:.6f}"
            ).format(
                field_id,
                field_total_seconds,
                fragment_labels_seconds,
            ),
            flush=True,
        )

        return {
            "field_id": field_id,
            "green": str(Path(green_path).resolve()),
            "merge": str(Path(merge_path).resolve()),
            "head_labels": str(Path(head_labels_path).resolve()),
            "probability": str((stage1 / "02_probability_uint16.tif").resolve()),
            "fragments": str(fragments_path.resolve()),
            "entries": str((stage2_1 / "head_graph_entry_results.json").resolve()),
            "paths": str((stage2_2 / "path_results.json").resolve()),
            "global_results": str(
                (stage2_3 / "global_selection_results.json").resolve()
            ),
            "output_dir": str(editor_dir.resolve()),
            "python_executable": str(self.python_executable),
            "editor_script": str(
                (self.source_dir / "tail_result_editor_v2_2.py").resolve()
            ),
            "timing": {
                "stage_seconds": stage_seconds,
                "fragment_labels_seconds": fragment_labels_seconds,
                "total_seconds": field_total_seconds,
            },
        }

    def run_all_fields(self) -> List[Dict[str, Any]]:
        batch_started = time.perf_counter()
        worker_input_path = self.task_root / "worker_input.json"
        if not worker_input_path.is_file():
            raise FileNotFoundError("未找到头部 worker_input.json。")
        with worker_input_path.open("r", encoding="utf-8") as handle:
            worker_input = json.load(handle)
        ordered_field_ids = [
            str(item.get("field_id", "") or "").strip()
            for item in list(worker_input.get("fields") or [])
        ]
        ordered_field_ids = [value for value in ordered_field_ids if value]
        if not ordered_field_ids:
            raise RuntimeError("头部 worker_input.json 没有视野顺序。")

        final_labels = [
            self.task_root
            / "calibration"
            / "head"
            / "{}_HeadFinalLabels.tif".format(field_id)
            for field_id in ordered_field_ids
        ]
        missing_labels = [path for path in final_labels if not path.is_file()]
        if missing_labels:
            raise FileNotFoundError("未找到 HeadFinalLabels。")

        print(
            "[TAIL_PATH_TIMING] BATCH_START field_count={}".format(
                len(final_labels)
            ),
            flush=True,
        )

        results: List[Dict[str, Any]] = []
        for head_path in final_labels:
            field_id = head_path.name[: -len("_HeadFinalLabels.tif")]
            green_matches = sorted(
                (self.task_root / "input").glob("{}_FITC.*".format(field_id))
            )
            merge_matches = sorted(
                (self.task_root / "input").glob("{}_Merge.*".format(field_id))
            )
            if not green_matches or not merge_matches:
                raise FileNotFoundError(
                    "视野 {} 缺少 FITC 或 Merge 输入。".format(field_id)
                )
            results.append(
                self.run_field(
                    field_id,
                    green_matches[0],
                    merge_matches[0],
                    head_path,
                )
            )

        batch_total_seconds = time.perf_counter() - batch_started
        print(
            (
                "[TAIL_PATH_TIMING] BATCH_DONE field_count={} "
                "total_seconds={:.6f}"
            ).format(
                len(results),
                batch_total_seconds,
            ),
            flush=True,
        )
        return results
