"""Analysis V2 运行环境快照。

本模块负责生成 logs/environment.json，记录：

1. 当前主程序Python和操作系统信息；
2. 当前任务目录；
3. MvImageID固定环境路径；
4. 当前使用管道的路径和SHA256；
5. 输入、输出目录；
6. 与Python日志编码相关的环境变量。

本模块不启动MvImageID，不导入Cellpose、Omnipose或Torch。
"""

from __future__ import annotations

import hashlib
import os
import platform
import struct
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .task_paths import AnalysisTaskPaths
from .task_state import atomic_write_json, current_timestamp


def sha256_file(
    path: Path,
    chunk_size: int = 1024 * 1024,
) -> str:
    """计算文件SHA256。"""
    file_path = Path(path)
    digest = hashlib.sha256()

    with file_path.open("rb") as file:
        while True:
            chunk = file.read(chunk_size)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def _modified_timestamp(timestamp: float) -> str:
    """将文件时间转换为带本地时区的ISO时间。"""
    return (
        datetime.fromtimestamp(timestamp)
        .astimezone()
        .isoformat(timespec="seconds")
    )


def describe_path(
    path: Optional[Path],
    include_hash: bool = False,
) -> Optional[Dict[str, Any]]:
    """生成路径信息。

    路径不存在时也会返回记录，而不是直接抛出异常。
    """
    if path is None:
        return None

    resolved_path = Path(path).expanduser().resolve()

    result: Dict[str, Any] = {
        "path": str(resolved_path),
        "exists": resolved_path.exists(),
        "is_file": resolved_path.is_file(),
        "is_dir": resolved_path.is_dir(),
        "size_bytes": None,
        "modified_at": None,
        "sha256": None,
        "inspection_error": None,
    }

    if not result["exists"]:
        return result

    try:
        stat_result = resolved_path.stat()

        result["modified_at"] = _modified_timestamp(
            stat_result.st_mtime
        )

        if resolved_path.is_file():
            result["size_bytes"] = stat_result.st_size

            if include_hash:
                result["sha256"] = sha256_file(
                    resolved_path
                )

    except OSError as exception:
        result["inspection_error"] = {
            "exception_type": type(exception).__name__,
            "exception_message": str(exception),
        }

    return result


@dataclass
class EnvironmentSnapshotWriter:
    """为一个Analysis V2任务生成environment.json。"""

    paths: AnalysisTaskPaths

    mvimageid_root: Optional[Path] = None
    mvimageid_python: Optional[Path] = None
    plugins_dir: Optional[Path] = None

    pipeline_path: Optional[Path] = None
    worker_path: Optional[Path] = None
    input_dir: Optional[Path] = None
    output_dir: Optional[Path] = None

    @property
    def environment_path(self) -> Path:
        """环境快照文件路径。"""
        return self.paths.logs_dir / "environment.json"

    def collect(self) -> Dict[str, Any]:
        """收集当前环境信息。"""
        current_python = Path(sys.executable).resolve()

        return {
            "schema_version": 1,
            "collected_at": current_timestamp(),
            "task_id": self.paths.run_id,
            "host": {
                "computer_name": platform.node(),
                "operating_system": platform.system(),
                "operating_system_release": platform.release(),
                "operating_system_version": platform.version(),
                "machine": platform.machine(),
                "processor": platform.processor(),
            },
            "process": {
                "pid": os.getpid(),
                "cwd": str(Path.cwd().resolve()),
                "python_executable": str(current_python),
                "python_version": sys.version,
                "python_version_info": {
                    "major": sys.version_info.major,
                    "minor": sys.version_info.minor,
                    "micro": sys.version_info.micro,
                },
                "python_bitness": struct.calcsize("P") * 8,
            },
            "environment_variables": {
                "PYTHONDONTWRITEBYTECODE": os.environ.get(
                    "PYTHONDONTWRITEBYTECODE"
                ),
                "PYTHONUTF8": os.environ.get("PYTHONUTF8"),
                "PYTHONIOENCODING": os.environ.get(
                    "PYTHONIOENCODING"
                ),
            },
            "task_paths": self.paths.as_dict(),
            "external_environment": {
                "mvimageid_root": describe_path(
                    self.mvimageid_root
                ),
                "mvimageid_python": describe_path(
                    self.mvimageid_python
                ),
                "plugins_directory": describe_path(
                    self.plugins_dir
                ),
            },
            "stage_paths": {
                "pipeline": describe_path(
                    self.pipeline_path,
                    include_hash=True,
                ),
                "worker": describe_path(
                    self.worker_path,
                    include_hash=True,
                ),
                "input_directory": describe_path(
                    self.input_dir
                ),
                "output_directory": describe_path(
                    self.output_dir
                ),
            },
            "runtime_probe": {
                "performed": False,
                "reason": (
                    "本阶段仅生成静态环境快照，"
                    "尚未启动MvImageID或探测GPU。"
                ),
                "mvimageid_python_version": None,
                "torch_version": None,
                "torch_cuda_version": None,
                "cuda_available": None,
                "gpu_name": None,
                "probe_error": None,
            },
        }

    def write(self) -> Dict[str, Any]:
        """收集并原子写入environment.json。"""
        self.paths.create_directories()

        data = self.collect()

        atomic_write_json(
            self.environment_path,
            data,
        )

        return data
