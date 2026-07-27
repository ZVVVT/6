"""手动运行 Analysis V2 三视野头部测量 smoke。

本工具不发布、不入库、不修改 cp_output，也不修改任务 state 或 manifest。
必须由用户在普通 PowerShell 中手动运行。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.analysis_v2.head_measurement_result import (
    validate_head_measurement_output,
)
from core.analysis_v2.head_measurement_service import (
    collect_head_measurement_fields,
    prepare_standardized_head_input,
)


DEFAULT_TASK_ROOT = (
    PROJECT_ROOT
    / "workspace"
    / "analysis_v2_stage2_gui_smoke"
    / "20260727_164913"
)
DEFAULT_PIPELINE = (
    PROJECT_ROOT
    / "pipelines"
    / "analysis_v2"
    / "measure_head_from_labels.cppipe"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-root", type=Path, default=DEFAULT_TASK_ROOT)
    parser.add_argument("--pipeline", type=Path, default=DEFAULT_PIPELINE)
    parser.add_argument(
        "--mvimageid-root",
        type=Path,
        default=Path(r"F:\MvImageID"),
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="只准备并预检12个输入文件，不启动 MvImageID",
    )
    return parser.parse_args()


def _recreate_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(str(path))
    path.mkdir(parents=True, exist_ok=True)


def _log(message: str) -> None:
    print(str(message), flush=True)


def main() -> int:
    try:
        args = parse_args()
        task_root = args.task_root.resolve()
        pipeline = args.pipeline.resolve()
        mvimageid_root = args.mvimageid_root.resolve()
        python_exe = mvimageid_root / ".venv" / "Scripts" / "python.exe"
        plugins_dir = mvimageid_root / "C-plugins" / "active_plugins"
        measurement_head = task_root / "measurement" / "head"
        input_dir = measurement_head / "manual_smoke_input"
        output_dir = measurement_head / "manual_smoke_output"

        _recreate_directory(input_dir)
        _recreate_directory(output_dir)
        fields = collect_head_measurement_fields(task_root)
        if len(fields) != 3:
            raise ValueError(
                "校准视野数量应为 3，实际为 {}".format(len(fields))
            )
        prepared = prepare_standardized_head_input(fields, input_dir)
        print(
            json.dumps(
                {
                    "input_dir": str(input_dir),
                    "file_names": prepared["file_names"],
                    "counts": prepared["counts"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if args.prepare_only:
            return 0

        from core.mvimageid_runner import MvImageIDRunner

        runner = MvImageIDRunner(
            source_project_dir=str(mvimageid_root),
            python_exe=str(python_exe),
            module_name="MvImageID",
            plugins_directory=str(plugins_dir),
            log_file="",
        )
        result = runner.run(
            pipeline_file=str(pipeline),
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            log_callback=_log,
            cancel_callback=None,
            log_file="",
        )
        if not result.success:
            raise RuntimeError(
                result.error_message
                or "MvImageID 退出码为 {}".format(result.return_code)
            )

        validation = validate_head_measurement_output(
            output_dir=output_dir,
            field_ids=[field["field_id"] for field in fields],
            expected_object_counts={
                field["field_id"]: field["expected_object_count"]
                for field in fields
            },
        )
        summary = {
            "success": True,
            "return_code": result.return_code,
            "duration_seconds": result.elapsed_seconds,
            "input_dir": str(input_dir),
            "input_file_count": 12,
            "output_dir": str(output_dir),
            "image_csv_row_count": validation["image_csv_row_count"],
            "fields": [
                {
                    "field_id": field["field_id"],
                    "count_r_objects": field["count_r_objects"],
                    "count_g_colocalized": field[
                        "count_g_colocalized"
                    ],
                    "colocalization_rate": field[
                        "calculated_colocalization_rate"
                    ],
                }
                for field in validation["fields"]
            ],
            "head_fluorescence_intensity": validation["totals"][
                "head_fluorescence_intensity"
            ],
            "head_colocalization_rate": validation["totals"][
                "head_colocalization_rate"
            ],
            "overlay_count": validation["overlay_count"],
            "result_parser": validation["result_parser"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as exception:
        print(
            "三视野头部测量 smoke 失败：{}: {}".format(
                type(exception).__name__,
                exception,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
