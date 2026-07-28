"""Analysis V2 尾部自动路径后台线程。"""

import json
import subprocess
from pathlib import Path

from PySide6.QtCore import QThread, Signal


class TailPathWorker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal(bool, object, str)

    def __init__(
        self,
        project_root: Path,
        task_root: Path,
        python_executable: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root)
        self.task_root = Path(task_root)
        self.python_executable = Path(python_executable)

    def run(self) -> None:
        try:
            worker_path = (
                self.project_root / "tools" / "analysis_v2" / "tail_path_worker.py"
            ).resolve()
            result_path = self.task_root / "tail_path_worker_result.json"
            log_path = self.task_root / "logs" / "tail_path_worker.log"
            if not self.python_executable.is_file():
                raise FileNotFoundError(
                    "MvImageID Python 不存在：{}".format(self.python_executable)
                )
            if not worker_path.is_file():
                raise FileNotFoundError("尾部 worker 不存在：{}".format(worker_path))

            self.log_signal.emit(
                "Analysis V2：通过 MvImageID Python 执行尾部 Stage 1～2.3。"
            )
            command = [
                str(self.python_executable),
                str(worker_path),
                "--project-root",
                str(self.project_root),
                "--task-root",
                str(self.task_root),
                "--result-json",
                str(result_path),
            ]
            completed = subprocess.run(
                command,
                cwd=str(self.project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(completed.stdout or "", encoding="utf-8")
            if not result_path.is_file():
                raise RuntimeError(
                    "尾部 worker 未生成结果文件，return_code={}。\n{}".format(
                        completed.returncode, completed.stdout
                    )
                )
            with result_path.open("r", encoding="utf-8") as handle:
                result = json.load(handle)
            if completed.returncode or not result.get("success"):
                raise RuntimeError(
                    str(result.get("error") or completed.stdout or "尾部 worker 失败")
                )
            fields = list(result.get("fields") or [])
            if not fields:
                raise RuntimeError("尾部 worker 没有返回任何视野。")
            self.finished_signal.emit(True, result, "")
        except BaseException as exception:
            self.finished_signal.emit(False, {}, str(exception))
