"""Analysis V2 任务状态管理。

本模块负责：

1. 创建 state.json；
2. 读取当前任务状态；
3. 更新任务阶段；
4. 记录状态变更历史；
5. 使用原子替换避免写入过程中损坏 state.json。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .task_paths import AnalysisTaskPaths


ALLOWED_STATUSES = {
    "created",
    "input_ready",
    "head_segmenting",
    "head_segmented",
    "head_calibration_required",
    "head_calibrated",
    "head_measuring",
    "head_measured",
    "tail_segmenting",
    "tail_segmented",
    "tail_calibration_required",
    "tail_calibrated",
    "measuring",
    "completed",
    "failed",
    "cancelled",
}

_TARGET_LOCKS_GUARD = threading.Lock()
_TARGET_LOCKS: Dict[str, threading.Lock] = {}
_REPLACE_RETRY_DELAYS = (0.05, 0.10, 0.20, 0.40, 0.80)


def _target_lock(path: Path) -> threading.Lock:
    """返回规范化目标路径对应的进程内线程锁。"""
    normalized = os.path.normcase(os.path.abspath(str(path)))
    with _TARGET_LOCKS_GUARD:
        lock = _TARGET_LOCKS.get(normalized)
        if lock is None:
            lock = threading.Lock()
            _TARGET_LOCKS[normalized] = lock
        return lock


def current_timestamp() -> str:
    """返回包含本地时区的ISO 8601时间。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """使用临时文件和os.replace原子写入JSON。

    写入过程中即使程序异常退出，也不会留下只写了一半的正式文件。
    """
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with _target_lock(target_path):
        temporary_path = None
        original_exception = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=".{}.".format(target_path.name),
                suffix=".tmp",
                dir=str(target_path.parent),
                delete=False,
            ) as file:
                temporary_path = Path(file.name)
                json.dump(
                    data,
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())

            for attempt in range(len(_REPLACE_RETRY_DELAYS) + 1):
                try:
                    os.replace(str(temporary_path), str(target_path))
                    break
                except PermissionError:
                    if attempt >= len(_REPLACE_RETRY_DELAYS):
                        raise
                    time.sleep(_REPLACE_RETRY_DELAYS[attempt])

        except BaseException as exception:
            original_exception = exception
            raise

        finally:
            if temporary_path is not None and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    if original_exception is None:
                        raise


@dataclass
class TaskStateStore:
    """单个Analysis V2任务的状态文件管理器。"""

    state_path: Path
    task_id: str

    @classmethod
    def from_task_paths(
        cls,
        paths: AnalysisTaskPaths,
    ) -> "TaskStateStore":
        """根据AnalysisTaskPaths创建状态管理器。"""
        return cls(
            state_path=paths.state_path,
            task_id=paths.run_id,
        )

    def exists(self) -> bool:
        """判断state.json是否存在。"""
        return self.state_path.is_file()

    def initialize(
        self,
        case_no: Optional[str] = None,
        protein_key: Optional[str] = None,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """创建初始state.json。

        默认不允许覆盖已有状态文件，防止误删历史状态。
        """
        if self.exists() and not overwrite:
            raise FileExistsError(
                "状态文件已经存在：{}".format(self.state_path)
            )

        timestamp = current_timestamp()

        data: Dict[str, Any] = {
            "schema_version": 1,
            "task_id": self.task_id,
            "case_no": case_no,
            "protein_key": protein_key,
            "status": "created",
            "stage": "task",
            "message": "任务状态已创建",
            "created_at": timestamp,
            "updated_at": timestamp,
            "error": None,
            "history": [
                {
                    "timestamp": timestamp,
                    "status": "created",
                    "stage": "task",
                    "message": "任务状态已创建",
                }
            ],
        }

        atomic_write_json(self.state_path, data)
        return data

    def load(self) -> Dict[str, Any]:
        """读取并做基础校验。"""
        if not self.exists():
            raise FileNotFoundError(
                "状态文件不存在：{}".format(self.state_path)
            )

        with self.state_path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError("state.json顶层内容必须是JSON对象")

        stored_task_id = data.get("task_id")
        if stored_task_id != self.task_id:
            raise ValueError(
                "任务ID不一致：文件为{}，当前为{}".format(
                    stored_task_id,
                    self.task_id,
                )
            )

        status = data.get("status")
        if status not in ALLOWED_STATUSES:
            raise ValueError(
                "state.json包含未知状态：{}".format(status)
            )

        history = data.get("history")
        if not isinstance(history, list):
            raise ValueError("state.json的history必须是数组")

        return data

    def update(
        self,
        status: str,
        stage: str,
        message: str = "",
        error: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """更新当前状态，并追加一条状态历史。"""
        if status not in ALLOWED_STATUSES:
            raise ValueError("不支持的任务状态：{}".format(status))

        if not str(stage).strip():
            raise ValueError("stage不能为空")

        data = self.load()
        timestamp = current_timestamp()

        history_item: Dict[str, Any] = {
            "timestamp": timestamp,
            "status": status,
            "stage": str(stage),
            "message": str(message),
        }

        if error is not None:
            history_item["error"] = error

        data["status"] = status
        data["stage"] = str(stage)
        data["message"] = str(message)
        data["updated_at"] = timestamp
        data["error"] = error
        data["history"].append(history_item)

        atomic_write_json(self.state_path, data)
        return data

    def mark_failed(
        self,
        stage: str,
        exception: BaseException,
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """记录异常和完整Python堆栈。"""
        error = {
            "exception_type": type(exception).__name__,
            "exception_message": str(exception),
            "traceback": "".join(
                traceback.format_exception(
                    type(exception),
                    exception,
                    exception.__traceback__,
                )
            ),
        }

        final_message = message or "任务执行失败：{}".format(
            exception
        )

        return self.update(
            status="failed",
            stage=stage,
            message=final_message,
            error=error,
        )
