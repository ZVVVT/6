"""Analysis V2 任务目录和文件路径管理。

所有 Analysis V2 代码应尽量通过本模块取得任务路径，
避免在业务代码中重复拼接目录。
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


def generate_run_id(now: Optional[datetime] = None) -> str:
    """生成单次运行标识。

    格式：
        YYYYMMDD_HHMMSS_六位随机十六进制字符

    示例：
        20260727_153012_a1b2c3
    """
    current_time = now or datetime.now()
    timestamp = current_time.strftime("%Y%m%d_%H%M%S")
    random_suffix = secrets.token_hex(3)
    return "{}_{}".format(timestamp, random_suffix)


def _sanitize_identifier(value: str, field_name: str) -> str:
    """清理病例号、蛋白标识等目录名。

    保留中文、字母、数字、下划线、短横线和点号，
    其他字符替换为下划线。
    """
    if value is None:
        raise ValueError("{}不能为空".format(field_name))

    cleaned = re.sub(r"[^\w.-]+", "_", str(value).strip(), flags=re.UNICODE)
    cleaned = cleaned.strip("._")

    if not cleaned:
        raise ValueError("{}清理后为空".format(field_name))

    base_name = cleaned.split(".", 1)[0].upper()
    reserved_names = {"CON", "PRN", "AUX", "NUL"}
    reserved_names.update(
        "COM{}".format(index) for index in range(1, 10)
    )
    reserved_names.update(
        "LPT{}".format(index) for index in range(1, 10)
    )

    if base_name in reserved_names:
        raise ValueError(
            "{}不能使用Windows保留设备名：{}".format(
                field_name,
                cleaned,
            )
        )

    return cleaned


@dataclass(frozen=True)
class AnalysisTaskPaths:
    """一项 Analysis V2 运行任务的全部标准路径。"""

    project_root: Path
    task_root: Path
    run_id: str

    input_dir: Path

    segmentation_dir: Path
    segmentation_head_dir: Path
    segmentation_tail_dir: Path

    calibration_dir: Path
    calibration_head_dir: Path
    calibration_tail_dir: Path

    measurement_dir: Path
    logs_dir: Path

    manifest_path: Path
    state_path: Path

    @classmethod
    def for_smoke(
        cls,
        project_root: Path,
        run_id: Optional[str] = None,
    ) -> "AnalysisTaskPaths":
        """创建开发阶段 smoke test 路径。

        目录：
            workspace/analysis_v2_smoke/<run_id>/
        """
        root = Path(project_root).resolve()
        resolved_run_id = _sanitize_identifier(
            run_id or generate_run_id(),
            "run_id",
        )

        task_root = (
            root
            / "workspace"
            / "analysis_v2_smoke"
            / resolved_run_id
        )

        return cls._build(
            project_root=root,
            task_root=task_root,
            run_id=resolved_run_id,
        )

    @classmethod
    def for_case(
        cls,
        project_root: Path,
        case_no: str,
        protein_key: str,
        run_id: Optional[str] = None,
    ) -> "AnalysisTaskPaths":
        """创建正式病例任务路径。

        目录：
            workspace/cases/<case_no>/analysis_v2/
            <protein_key>/runs/<run_id>/
        """
        root = Path(project_root).resolve()

        clean_case_no = _sanitize_identifier(case_no, "case_no")
        clean_protein_key = _sanitize_identifier(
            protein_key,
            "protein_key",
        )
        resolved_run_id = _sanitize_identifier(
            run_id or generate_run_id(),
            "run_id",
        )

        task_root = (
            root
            / "workspace"
            / "cases"
            / clean_case_no
            / "analysis_v2"
            / clean_protein_key
            / "runs"
            / resolved_run_id
        )

        return cls._build(
            project_root=root,
            task_root=task_root,
            run_id=resolved_run_id,
        )

    @classmethod
    def _build(
        cls,
        project_root: Path,
        task_root: Path,
        run_id: str,
    ) -> "AnalysisTaskPaths":
        """根据任务根目录构建全部路径。"""
        input_dir = task_root / "input"

        segmentation_dir = task_root / "segmentation"
        segmentation_head_dir = segmentation_dir / "head"
        segmentation_tail_dir = segmentation_dir / "tail"

        calibration_dir = task_root / "calibration"
        calibration_head_dir = calibration_dir / "head"
        calibration_tail_dir = calibration_dir / "tail"

        measurement_dir = task_root / "measurement"
        logs_dir = task_root / "logs"

        return cls(
            project_root=project_root,
            task_root=task_root,
            run_id=run_id,
            input_dir=input_dir,
            segmentation_dir=segmentation_dir,
            segmentation_head_dir=segmentation_head_dir,
            segmentation_tail_dir=segmentation_tail_dir,
            calibration_dir=calibration_dir,
            calibration_head_dir=calibration_head_dir,
            calibration_tail_dir=calibration_tail_dir,
            measurement_dir=measurement_dir,
            logs_dir=logs_dir,
            manifest_path=task_root / "manifest.json",
            state_path=task_root / "state.json",
        )

    def all_directories(self) -> List[Path]:
        """返回本任务需要创建的全部目录。"""
        return [
            self.task_root,
            self.input_dir,
            self.segmentation_dir,
            self.segmentation_head_dir,
            self.segmentation_tail_dir,
            self.calibration_dir,
            self.calibration_head_dir,
            self.calibration_tail_dir,
            self.measurement_dir,
            self.logs_dir,
        ]

    def create_directories(self) -> None:
        """创建任务目录。

        已存在的目录会保留，不删除其中任何文件。
        """
        for directory in self.all_directories():
            directory.mkdir(parents=True, exist_ok=True)

    def as_dict(self) -> Dict[str, str]:
        """返回便于写入JSON或日志的路径字典。"""
        return {
            "project_root": str(self.project_root),
            "task_root": str(self.task_root),
            "run_id": self.run_id,
            "input_dir": str(self.input_dir),
            "segmentation_dir": str(self.segmentation_dir),
            "segmentation_head_dir": str(
                self.segmentation_head_dir
            ),
            "segmentation_tail_dir": str(
                self.segmentation_tail_dir
            ),
            "calibration_dir": str(self.calibration_dir),
            "calibration_head_dir": str(
                self.calibration_head_dir
            ),
            "calibration_tail_dir": str(
                self.calibration_tail_dir
            ),
            "measurement_dir": str(self.measurement_dir),
            "logs_dir": str(self.logs_dir),
            "manifest_path": str(self.manifest_path),
            "state_path": str(self.state_path),
        }
