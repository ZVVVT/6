"""对三个指定真实视野执行 Analysis V2 Stage 1 批量 smoke。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.analysis_v2 import (  # noqa: E402
    AnalysisTaskPaths,
    atomic_write_json,
    generate_run_id,
    run_head_segmentation,
)


DEFAULT_INPUT_DIR = (
    PROJECT_ROOT
    / "workspace"
    / "cases"
    / "CASE20260717112412"
    / "raw_images"
    / "protein1"
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "workspace" / "analysis_v2_smoke"
DEFAULT_MVIMAGEID_ROOT = Path(r"F:\MvImageID")
DEFAULT_MVIMAGEID_PYTHON = DEFAULT_MVIMAGEID_ROOT / ".venv" / "Scripts" / "python.exe"
DEFAULT_WORKER = PROJECT_ROOT / "tools" / "analysis_v2" / "direct_cellpose_worker.py"
FIELD_IDS = ["ZBFY022-A-1", "ZBFY022-A-2", "ZBFY022-A-3"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--mvimageid-root", type=Path, default=DEFAULT_MVIMAGEID_ROOT)
    parser.add_argument("--mvimageid-python", type=Path, default=DEFAULT_MVIMAGEID_PYTHON)
    parser.add_argument("--worker", type=Path, default=DEFAULT_WORKER)
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser.parse_args()


def build_fields(input_dir: Path) -> List[Dict[str, str]]:
    fields = []
    for field_id in FIELD_IDS:
        item = {"field_id": field_id}
        for channel, filename_channel in (
            ("tritc", "TRITC"),
            ("fitc", "FITC"),
            ("merge", "Merge"),
        ):
            path = (input_dir / "{}_RGB_{}.tif".format(field_id, filename_channel)).resolve()
            if not path.is_file():
                raise FileNotFoundError("指定真实输入不存在：{}".format(path))
            item["{}_path".format(channel)] = str(path)
        fields.append(item)
    return fields


def mvimageid_inventory(root: Path) -> Dict[str, Dict[str, Any]]:
    inventory = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat_result = path.stat()
            inventory[str(path.resolve())] = {
                "size_bytes": stat_result.st_size,
                "modified_ns": int(stat_result.st_mtime * 1000000000),
            }
    return inventory


def main() -> int:
    args = parse_args()
    fields = build_fields(args.input_dir.resolve())
    run_id = generate_run_id()
    task_root = args.output_root.resolve() / run_id
    paths = AnalysisTaskPaths._build(
        project_root=PROJECT_ROOT,
        task_root=task_root,
        run_id=run_id,
    )
    paths.create_directories()
    before_path = paths.logs_dir / "mvimageid_inventory_before.json"
    after_path = paths.logs_dir / "mvimageid_inventory_after.json"
    comparison_path = paths.logs_dir / "mvimageid_inventory_comparison.json"
    before = mvimageid_inventory(args.mvimageid_root.resolve())
    atomic_write_json(before_path, {"root": str(args.mvimageid_root.resolve()), "files": before})
    try:
        result = run_head_segmentation(
            paths=paths,
            paired_fields=fields,
            mvimageid_root=args.mvimageid_root,
            mvimageid_python=args.mvimageid_python,
            worker_path=args.worker,
            timeout_seconds=args.timeout,
            case_no="CASE20260717112412",
            protein_key="protein1",
        )
    finally:
        after = mvimageid_inventory(args.mvimageid_root.resolve())
        atomic_write_json(after_path, {"root": str(args.mvimageid_root.resolve()), "files": after})
        changed = sorted(
            path for path in set(before).union(after)
            if before.get(path) != after.get(path)
        )
        atomic_write_json(
            comparison_path,
            {
                "unchanged": not changed,
                "before_file_count": len(before),
                "after_file_count": len(after),
                "changed_paths": changed,
            },
        )

    worker_result = result["worker_result"]
    summary = {
        "run_id": run_id,
        "task_directory": str(paths.task_root),
        "model_init_ms": worker_result["model_init_ms"],
        "total_runtime_ms": worker_result["total_runtime_ms"],
        "runner_duration_seconds": result["run_result"]["duration_seconds"],
        "fields": [
            {
                "field_id": field["field_id"],
                "eval_ms": field["eval_ms"],
                "object_count": field["object_count"],
                "labels_path": field["labels_output_path"],
                "labels_dtype": field["labels_dtype"],
                "labels_shape": field["labels_shape"],
                "nonzero_pixels": field["nonzero_pixels"],
            }
            for field in result["fields"]
        ],
        "state": result["state"]["status"],
        "manifest_file_count": len(result["manifest"]["files"]),
        "worker_result_path": result["run_result"]["worker_result_path"],
        "logs": {
            "task": str(paths.logs_dir / "task.log"),
            "events": str(paths.logs_dir / "events.jsonl"),
            "environment": str(paths.logs_dir / "environment.json"),
            "command": result["run_result"]["command_log_path"],
            "stdout": result["run_result"]["stdout_path"],
            "stderr": result["run_result"]["stderr_path"],
        },
        "mvimageid_inventory_before": str(before_path),
        "mvimageid_inventory_after": str(after_path),
        "mvimageid_inventory_comparison": str(comparison_path),
        "mvimageid_unchanged": not changed,
        "mvimageid_changed_paths": changed,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
