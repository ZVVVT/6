"""Analysis V2 任务文件清单管理。

manifest.json用于记录：

1. 当前任务实际使用的输入文件；
2. 自动识别生成的中间文件；
3. 人工校准结果；
4. 最终测量输出；
5. 文件大小、修改时间和SHA256；
6. 文件所属阶段和业务角色。

manifest.json只记录任务目录内部的文件。
管道、解释器和外部环境由environment.json记录。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .environment_snapshot import sha256_file
from .task_paths import AnalysisTaskPaths
from .task_state import atomic_write_json, current_timestamp


def _file_modified_timestamp(path: Path) -> str:
    """返回文件修改时间。"""
    return (
        datetime.fromtimestamp(path.stat().st_mtime)
        .astimezone()
        .isoformat(timespec="seconds")
    )


@dataclass
class ManifestStore:
    """单个Analysis V2任务的manifest.json管理器。"""

    manifest_path: Path
    task_root: Path
    task_id: str

    @classmethod
    def from_task_paths(
        cls,
        paths: AnalysisTaskPaths,
    ) -> "ManifestStore":
        """根据任务路径创建清单管理器。"""
        return cls(
            manifest_path=paths.manifest_path,
            task_root=paths.task_root,
            task_id=paths.run_id,
        )

    def exists(self) -> bool:
        """判断manifest.json是否存在。"""
        return self.manifest_path.is_file()

    def initialize(
        self,
        case_no: Optional[str] = None,
        protein_key: Optional[str] = None,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """创建初始manifest.json。"""
        if self.exists() and not overwrite:
            raise FileExistsError(
                "清单文件已经存在：{}".format(
                    self.manifest_path
                )
            )

        timestamp = current_timestamp()

        data: Dict[str, Any] = {
            "schema_version": 1,
            "task_id": self.task_id,
            "case_no": case_no,
            "protein_key": protein_key,
            "created_at": timestamp,
            "updated_at": timestamp,
            "files": [],
        }

        atomic_write_json(self.manifest_path, data)
        return data

    def load(self) -> Dict[str, Any]:
        """读取并基础校验manifest.json。"""
        if not self.exists():
            raise FileNotFoundError(
                "清单文件不存在：{}".format(
                    self.manifest_path
                )
            )

        import json

        with self.manifest_path.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError(
                "manifest.json顶层必须是JSON对象"
            )

        if data.get("task_id") != self.task_id:
            raise ValueError(
                "任务ID不一致：文件为{}，当前为{}".format(
                    data.get("task_id"),
                    self.task_id,
                )
            )

        files = data.get("files")

        if not isinstance(files, list):
            raise ValueError(
                "manifest.json的files必须是数组"
            )

        return data

    def _resolve_task_file(
        self,
        file_path: Path,
    ) -> tuple:
        """解析并验证任务文件路径。

        返回：
            绝对路径、相对于任务根目录的路径。
        """
        task_root = Path(self.task_root).resolve()
        supplied_path = Path(file_path)

        if supplied_path.is_absolute():
            resolved_path = supplied_path.resolve()
        else:
            resolved_path = (
                task_root / supplied_path
            ).resolve()

        try:
            relative_path = resolved_path.relative_to(
                task_root
            )
        except ValueError:
            raise ValueError(
                "manifest只能记录任务目录内文件：{}".format(
                    resolved_path
                )
            )

        return resolved_path, relative_path

    def add_file(
        self,
        file_path: Path,
        role: str,
        stage: str,
        media_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """添加或更新一个已经存在的任务文件。"""
        clean_role = str(role).strip()
        clean_stage = str(stage).strip()

        if not clean_role:
            raise ValueError("role不能为空")

        if not clean_stage:
            raise ValueError("stage不能为空")

        if metadata is not None and not isinstance(
            metadata,
            dict,
        ):
            raise TypeError(
                "metadata必须是字典或None"
            )

        resolved_path, relative_path = (
            self._resolve_task_file(file_path)
        )

        if not resolved_path.is_file():
            raise FileNotFoundError(
                "待登记文件不存在：{}".format(
                    resolved_path
                )
            )

        relative_text = relative_path.as_posix()
        record_id = "{}::{}".format(
            clean_role,
            relative_text,
        )

        record: Dict[str, Any] = {
            "record_id": record_id,
            "role": clean_role,
            "stage": clean_stage,
            "relative_path": relative_text,
            "absolute_path": str(resolved_path),
            "filename": resolved_path.name,
            "media_type": media_type,
            "size_bytes": resolved_path.stat().st_size,
            "modified_at": _file_modified_timestamp(
                resolved_path
            ),
            "sha256": sha256_file(resolved_path),
            "metadata": metadata or {},
            "registered_at": current_timestamp(),
        }

        data = self.load()
        existing_files: List[Dict[str, Any]] = data["files"]

        replaced = False

        for index, existing_record in enumerate(
            existing_files
        ):
            if existing_record.get("record_id") == record_id:
                existing_files[index] = record
                replaced = True
                break

        if not replaced:
            existing_files.append(record)

        data["updated_at"] = current_timestamp()
        atomic_write_json(self.manifest_path, data)

        return record

    def validate_files(self) -> Dict[str, Any]:
        """重新检查manifest中登记的全部文件。"""
        data = self.load()
        results: List[Dict[str, Any]] = []
        all_valid = True

        for record in data["files"]:
            relative_path = record.get("relative_path")
            expected_size = record.get("size_bytes")
            expected_sha256 = record.get("sha256")

            resolved_path = None
            exists = False
            actual_size = None
            actual_sha256 = None
            size_matches = False
            sha256_matches = False
            error = None

            try:
                if not isinstance(relative_path, str) or not relative_path.strip():
                    raise ValueError(
                        "manifest中的relative_path必须是非空字符串"
                    )

                supplied_path = Path(relative_path)
                if supplied_path.is_absolute():
                    raise ValueError(
                        "manifest中的relative_path不能是绝对路径：{}".format(
                            relative_path
                        )
                    )

                resolved_path, _ = self._resolve_task_file(
                    supplied_path
                )
                exists = resolved_path.is_file()

                if exists:
                    actual_size = resolved_path.stat().st_size
                    actual_sha256 = sha256_file(
                        resolved_path
                    )
                    size_matches = (
                        actual_size == expected_size
                    )
                    sha256_matches = (
                        actual_sha256 == expected_sha256
                    )
            except (OSError, ValueError) as exception:
                error = {
                    "exception_type": (
                        type(exception).__name__
                    ),
                    "exception_message": str(exception),
                }

            valid = (
                exists
                and size_matches
                and sha256_matches
                and error is None
            )

            all_valid = all_valid and valid

            results.append(
                {
                    "record_id": record.get("record_id"),
                    "role": record.get("role"),
                    "relative_path": relative_path,
                    "exists": exists,
                    "expected_size_bytes": expected_size,
                    "actual_size_bytes": actual_size,
                    "size_matches": size_matches,
                    "expected_sha256": expected_sha256,
                    "actual_sha256": actual_sha256,
                    "sha256_matches": sha256_matches,
                    "valid": valid,
                    "error": error,
                }
            )

        return {
            "task_id": self.task_id,
            "checked_at": current_timestamp(),
            "file_count": len(results),
            "all_valid": all_valid,
            "results": results,
        }
