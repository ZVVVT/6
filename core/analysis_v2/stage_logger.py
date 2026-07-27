"""Analysis V2 阶段日志管理。

本模块负责：

1. 写入供人工阅读的 task.log；
2. 写入结构化 events.jsonl；
3. 记录异常类型、异常消息和完整堆栈；
4. 保证每条日志写入后立即刷新到磁盘。
"""

from __future__ import annotations

import json
import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .task_paths import AnalysisTaskPaths
from .task_state import current_timestamp


@dataclass
class StageLogger:
    """单个 Analysis V2 任务的日志管理器。"""

    logs_dir: Path
    task_id: str
    case_no: Optional[str] = None
    protein_key: Optional[str] = None

    def __post_init__(self) -> None:
        self.logs_dir = Path(self.logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_task_paths(
        cls,
        paths: AnalysisTaskPaths,
        case_no: Optional[str] = None,
        protein_key: Optional[str] = None,
    ) -> "StageLogger":
        """根据任务路径创建日志管理器。"""
        return cls(
            logs_dir=paths.logs_dir,
            task_id=paths.run_id,
            case_no=case_no,
            protein_key=protein_key,
        )

    @property
    def task_log_path(self) -> Path:
        """人工阅读日志路径。"""
        return self.logs_dir / "task.log"

    @property
    def events_path(self) -> Path:
        """结构化事件日志路径。"""
        return self.logs_dir / "events.jsonl"

    @staticmethod
    def _single_line(value: Any) -> str:
        """将内容压缩为单行，避免破坏task.log格式。"""
        return (
            str(value)
            .replace("\r\n", "\\n")
            .replace("\n", "\\n")
            .replace("\r", "\\n")
        )

    @staticmethod
    def _append_text_line(path: Path, line: str) -> None:
        """追加一行文本，并立即刷新到磁盘。"""
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as file:
            file.write(line)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

    def log(
        self,
        level: str,
        stage: str,
        message: str,
    ) -> None:
        """写入task.log。"""
        clean_level = self._single_line(level).upper()
        clean_stage = self._single_line(stage)
        clean_message = self._single_line(message)

        line = (
            "{timestamp} | {level} | task={task_id} | "
            "case={case_no} | protein={protein_key} | "
            "stage={stage} | {message}"
        ).format(
            timestamp=current_timestamp(),
            level=clean_level,
            task_id=self._single_line(self.task_id),
            case_no=self._single_line(self.case_no or "-"),
            protein_key=self._single_line(
                self.protein_key or "-"
            ),
            stage=clean_stage,
            message=clean_message,
        )

        self._append_text_line(self.task_log_path, line)

    def info(self, stage: str, message: str) -> None:
        """写入INFO日志。"""
        self.log("INFO", stage, message)

    def warning(self, stage: str, message: str) -> None:
        """写入WARNING日志。"""
        self.log("WARNING", stage, message)

    def error(self, stage: str, message: str) -> None:
        """写入ERROR日志。"""
        self.log("ERROR", stage, message)

    def event(
        self,
        event_name: str,
        stage: str,
        status: str,
        message: str = "",
        duration_seconds: Optional[float] = None,
        return_code: Optional[int] = None,
        exception_type: Optional[str] = None,
        exception_message: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """追加一个结构化JSON事件。"""
        payload: Dict[str, Any] = {
            "timestamp": current_timestamp(),
            "event": str(event_name),
            "task_id": self.task_id,
            "case_no": self.case_no,
            "protein_key": self.protein_key,
            "stage": str(stage),
            "status": str(status),
            "message": str(message),
            "duration_seconds": duration_seconds,
            "return_code": return_code,
            "exception_type": exception_type,
            "exception_message": exception_message,
        }

        if extra is not None:
            if not isinstance(extra, dict):
                raise TypeError("extra必须是字典或None")
            payload["extra"] = extra

        json_line = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        self._append_text_line(self.events_path, json_line)
        return payload

    def record_exception(
        self,
        stage: str,
        exception: BaseException,
        message: Optional[str] = None,
        event_name: str = "stage_failed",
        status: str = "failed",
    ) -> Dict[str, Any]:
        """同时记录异常文本日志和结构化事件。"""
        exception_type = type(exception).__name__
        exception_message = str(exception)

        final_message = message or "阶段执行失败：{}".format(
            exception_message
        )

        traceback_text = "".join(
            traceback.format_exception(
                type(exception),
                exception,
                exception.__traceback__,
            )
        )

        self.error(
            stage=stage,
            message="{} | {}: {}".format(
                final_message,
                exception_type,
                exception_message,
            ),
        )

        return self.event(
            event_name=event_name,
            stage=stage,
            status=status,
            message=final_message,
            exception_type=exception_type,
            exception_message=exception_message,
            extra={
                "traceback": traceback_text,
            },
        )