"""独立启动 Analysis V2 人工头部校准窗口。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.analysis_v2 import HeadCalibrationWindow  # noqa: E402


DEFAULT_TASK_ROOT = (
    PROJECT_ROOT
    / "workspace"
    / "analysis_v2_smoke"
    / "20260727_162808_89cf95"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动独立人工头部校准 MVP")
    parser.add_argument("--task-root", type=Path, default=DEFAULT_TASK_ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    application = QApplication.instance() or QApplication(sys.argv)
    try:
        window = HeadCalibrationWindow(args.task_root.resolve())
    except BaseException as exception:
        QMessageBox.critical(
            None,
            "人工头部校准启动失败",
            "{}：{}".format(type(exception).__name__, exception),
        )
        return 1
    window.show()
    return int(application.exec())


if __name__ == "__main__":
    raise SystemExit(main())
