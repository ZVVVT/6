#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safely rebuild one field from refined candidates through the manual editor."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List


VERSION = "tail_corner_guard_replay_v1"


def run_command(command: List[str], cwd: Path) -> None:
    print("[CORNER-GUARD] COMMAND", subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(command, cwd=str(cwd))
    if completed.returncode:
        raise RuntimeError(
            f"命令执行失败，return_code={completed.returncode}："
            f"{subprocess.list2cmdline(command)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="重新生成单视野折角防护结果并打开尾部校准窗口"
    )
    parser.add_argument("--task-root", required=True)
    parser.add_argument("--field-id", required=True)
    parser.add_argument("--display-max-dim", type=int, default=1400)
    args = parser.parse_args()

    task_root = Path(args.task_root).expanduser().resolve()
    field_id = str(args.field_id).strip()
    if not task_root.is_dir():
        raise FileNotFoundError(f"任务目录不存在：{task_root}")
    if not field_id:
        raise ValueError("field-id不能为空。")

    project_root = Path(__file__).resolve().parents[2]
    python_executable = str(Path(sys.executable).resolve())
    scripts = {
        "refine": project_root / "tools" / "analysis_v2" / "tail_joint_refine_candidate_mvp.py",
        "region": project_root / "tools" / "analysis_v2" / "tail_joint_region_preview_mvp.py",
        "draft": project_root / "tools" / "analysis_v2" / "tail_joint_draft_export_mvp.py",
        "editor": project_root / "tools" / "analysis_v2" / "tail_joint_draft_editor_launcher_mvp.py",
    }
    missing = [path for path in scripts.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "缺少重放脚本：\n" + "\n".join(str(path) for path in missing)
        )

    relative_outputs = [
        Path("segmentation") / "tail_joint_refined_mvp" / field_id,
        Path("segmentation") / "tail_joint_region_preview_mvp" / field_id,
        Path("segmentation") / "tail_joint_draft_mvp" / field_id,
        Path("segmentation") / "tail_joint_editor_adapter_mvp" / field_id,
        Path("calibration") / "tail_joint_editor_mvp" / field_id,
        Path("calibration") / "tail_joint_final_candidate_mvp" / field_id,
        Path("calibration") / "tail_joint_promotion_staging_mvp" / field_id,
    ]
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_root = (
        task_root
        / "corner_guard_replay_backups"
        / f"{timestamp}_{field_id}"
    )
    backup_root.mkdir(parents=True, exist_ok=False)

    backed_up: List[str] = []
    for relative in relative_outputs:
        source = task_root / relative
        if not source.exists():
            continue
        target = backup_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
        shutil.rmtree(source)
        backed_up.append(str(relative))

    summary_path = backup_root / "replay_manifest.json"
    summary_path.write_text(
        json.dumps(
            {
                "version": VERSION,
                "task_root": str(task_root),
                "field_id": field_id,
                "backed_up_outputs": backed_up,
                "formal_calibration_tail_modified": False,
                "measurement_modified": False,
                "database_modified": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    common = ["--task-root", str(task_root), "--field-id", field_id]
    print(f"折角防护重放：{field_id}")
    print(f"旧测试输出备份：{backup_root}")
    print("不会修改 calibration/tail、measurement、cp_output 或数据库。")

    run_command(
        [python_executable, "-u", str(scripts["refine"]), *common],
        project_root,
    )
    run_command(
        [python_executable, "-u", str(scripts["region"]), *common],
        project_root,
    )
    run_command(
        [python_executable, "-u", str(scripts["draft"]), *common],
        project_root,
    )
    run_command(
        [
            python_executable,
            "-u",
            str(scripts["editor"]),
            *common,
            "--display-max-dim",
            str(max(900, int(args.display_max_dim))),
        ],
        project_root,
    )
    print("折角防护重放完成；当前仅用于编辑器检查，未测量、未发布。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
