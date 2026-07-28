"""独立运行历史尾部 Stage 1～2.3 和 V2.2 人工编辑器。"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict

import tifffile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.analysis_v2.tail_path_service import TailPathService


def required_file(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError("{}不存在：{}".format(label, path))
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "独立尾部流程：自动 Stage 1～2.3、V2.2 人工编辑、"
            "TailFinalLabels 发布"
        )
    )
    parser.add_argument("--green", required=True, help="G/FITC 图")
    parser.add_argument("--merge", required=True, help="Merge 图")
    parser.add_argument(
        "--head-labels",
        required=True,
        help="头部 uint16 标签图；支持昨天格式或 HeadFinalLabels",
    )
    parser.add_argument("--field-id", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument(
        "--python-executable",
        required=True,
        help="能够运行历史算法和 Matplotlib 编辑器的 Python 3.8",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--auto-only",
        action="store_true",
        help="只运行自动 Stage 1～2.3",
    )
    mode.add_argument(
        "--editor-only",
        action="store_true",
        help="读取 output-root/workflow_result.json，只启动编辑器",
    )
    return parser


def write_result(output_root: Path, payload: Dict[str, str]) -> Path:
    result_path = output_root / "workflow_result.json"
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result_path


def read_result(output_root: Path) -> Dict[str, str]:
    result_path = output_root / "workflow_result.json"
    if not result_path.is_file():
        raise FileNotFoundError(
            "editor-only 缺少自动结果：{}".format(result_path)
        )
    return dict(json.loads(result_path.read_text(encoding="utf-8")))


def editor_command(payload: Dict[str, str]):
    return [
        payload["python_executable"],
        payload["editor_script"],
        "--merge",
        payload["merge"],
        "--green",
        payload["green"],
        "--probability",
        payload["probability"],
        "--fragments",
        payload["fragments"],
        "--entries",
        payload["entries"],
        "--paths",
        payload["paths"],
        "--global-results",
        payload["global_results"],
        "--output-dir",
        payload["output_dir"],
        "--manual-margin",
        "60",
        "--manual-radius",
        "5",
        "--display-max-dim",
        "1400",
    ]


def publish_tail_final_labels(payload: Dict[str, str]) -> Path:
    output_dir = Path(payload["output_dir"]).resolve()
    source = output_dir / "edited_tail_regions_head_id_uint16.tif"
    if not source.is_file():
        raise FileNotFoundError(
            "编辑器关闭但未找到保存结果：{}".format(source)
        )

    image = tifffile.imread(str(source))
    if image.ndim != 2 or str(image.dtype) != "uint16":
        raise ValueError("人工尾部标签必须是二维 uint16。")

    target = output_dir / "{}_TailFinalLabels.tif".format(
        payload["field_id"]
    )
    shutil.copy2(str(source), str(target))
    return target


def main() -> int:
    args = build_parser().parse_args()
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    python_executable = required_file(
        args.python_executable,
        "Python 解释器",
    )

    if args.editor_only:
        payload = read_result(output_root)
    else:
        payload = TailPathService(
            PROJECT_ROOT,
            output_root,
            python_executable,
        ).run_field(
            str(args.field_id),
            required_file(args.green, "G/FITC 图"),
            required_file(args.merge, "Merge 图"),
            required_file(args.head_labels, "头部标签图"),
        )
        write_result(output_root, payload)

    if args.auto_only:
        print("TAIL_AUTO_OK")
        print(output_root / "workflow_result.json")
        return 0

    completed = subprocess.run(
        editor_command(payload),
        cwd=payload["output_dir"],
    )
    if completed.returncode:
        raise RuntimeError(
            "尾部编辑器异常退出：{}".format(completed.returncode)
        )

    final_path = publish_tail_final_labels(payload)
    print("TAIL_WORKFLOW_OK")
    print(final_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
