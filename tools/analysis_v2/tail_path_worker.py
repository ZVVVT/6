"""在 MvImageID Python 中运行 Analysis V2 尾部 Stage 1～2.3。"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--task-root", required=True)
    parser.add_argument("--result-json", required=True)
    arguments = parser.parse_args()

    project_root = Path(arguments.project_root).resolve()
    task_root = Path(arguments.task_root).resolve()
    result_path = Path(arguments.result_json).resolve()
    sys.path.insert(0, str(project_root))

    payload = {"success": False, "fields": [], "error": ""}
    try:
        from core.analysis_v2.tail_path_service import TailPathService

        fields = TailPathService(
            project_root=project_root,
            task_root=task_root,
            python_executable=Path(sys.executable),
        ).run_all_fields()
        payload.update({"success": True, "fields": fields})
    except BaseException:
        payload["error"] = traceback.format_exc()

    result_path.parent.mkdir(parents=True, exist_ok=True)
    with result_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
