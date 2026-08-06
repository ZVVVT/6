"""使用固定算法环境启动 Analysis V2 直接 Cellpose worker。"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .task_state import atomic_write_json
from core.analysis_process_registry import analysis_process_registry


@dataclass
class DirectCellposeRunResult:
    command: List[str]
    cwd: str
    python_path: str
    worker_path: str
    input_json_path: str
    started_at: str
    ended_at: str
    return_code: int
    duration_seconds: float
    command_log_path: str
    stdout_path: str
    stderr_path: str
    worker_result_path: str

    @property
    def success(self) -> bool:
        return self.return_code == 0 and Path(self.worker_result_path).is_file()

    def as_dict(self) -> Dict[str, Any]:
        result = dict(self.__dict__)
        result["success"] = self.success
        return result


class DirectCellposeRunner:
    """在 F:\\MvImageID 的 Python 环境中运行正式 worker。"""

    def __init__(
        self,
        python_path: Path,
        worker_path: Path,
        project_root: Path,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.python_path = Path(python_path).resolve()
        self.worker_path = Path(worker_path).resolve()
        self.project_root = Path(project_root).resolve()
        self.timeout_seconds = float(timeout_seconds)
        if self.timeout_seconds < 120.0:
            raise ValueError("直接 Cellpose timeout 不得小于 120 秒")

    def run(
        self,
        input_json_path: Path,
        logs_dir: Path,
        worker_result_path: Path,
        timeout_seconds: Optional[float] = None,
    ) -> DirectCellposeRunResult:
        timeout = self.timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        if timeout < 120.0:
            raise ValueError("直接 Cellpose timeout 不得小于 120 秒")
        input_json = Path(input_json_path).resolve()
        logs = Path(logs_dir).resolve()
        logs.mkdir(parents=True, exist_ok=True)
        stdout_path = logs / "head_segmentation_stdout.log"
        stderr_path = logs / "head_segmentation_stderr.log"
        command_path = logs / "head_segmentation_command.txt"
        command = [
            str(self.python_path),
            "-u",
            str(self.worker_path),
            "--input-json",
            str(input_json),
        ]
        environment = os.environ.copy()
        environment.update({
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
        })
        started_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
        command_record = {
            "command": command,
            "command_line": subprocess.list2cmdline(command),
            "cwd": str(self.project_root),
            "python_path": str(self.python_path),
            "worker_path": str(self.worker_path),
            "input_json_path": str(input_json),
            "started_at": started_at,
            "ended_at": None,
            "return_code": None,
            "duration_seconds": None,
            "timeout_seconds": timeout,
        }
        atomic_write_json(command_path, command_record)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        started = time.perf_counter()
        return_code = -1
        try:
            with stdout_path.open("w", encoding="utf-8", newline="\n") as stdout_handle:
                with stderr_path.open("w", encoding="utf-8", newline="\n") as stderr_handle:
                    process = analysis_process_registry.register(subprocess.Popen(
                        command,
                        cwd=str(self.project_root),
                        env=environment,
                        stdout=stdout_handle,
                        stderr=stderr_handle,
                        creationflags=creationflags,
                    ))
                    try:
                        return_code = int(process.wait(timeout=timeout))
                    except subprocess.TimeoutExpired:
                        analysis_process_registry._terminate_tree(process.pid, process)
                        process.wait()
                        raise
                    finally:
                        analysis_process_registry.unregister(process)
        finally:
            duration = time.perf_counter() - started
            ended_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
            command_record.update({
                "ended_at": ended_at,
                "return_code": return_code,
                "duration_seconds": duration,
            })
            atomic_write_json(command_path, command_record)
        return DirectCellposeRunResult(
            command=command,
            cwd=str(self.project_root),
            python_path=str(self.python_path),
            worker_path=str(self.worker_path),
            input_json_path=str(input_json),
            started_at=started_at,
            ended_at=ended_at,
            return_code=return_code,
            duration_seconds=duration,
            command_log_path=str(command_path),
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            worker_result_path=str(Path(worker_result_path).resolve()),
        )
