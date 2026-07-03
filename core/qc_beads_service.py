# -*- coding: utf-8 -*-
"""
qc_beads_service.py

质控微球荧光强度测试服务。

定位：
- 这是低频质控功能，不进入病例数据库，不进入 PDF 报告；
- 只负责复制微球图片、调用 MvImageID、把结果输出到独立文件夹；
- 默认输出结构：workspace/qc/YYYYMMDD_01/input 与 workspace/qc/YYYYMMDD_01/output。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QThread, Signal

from core.config_manager import ConfigManager
from core.mvimageid_runner import MvImageIDRunner, MvImageIDRunResult


LogCallback = Optional[Callable[[str], None]]
CancelCallback = Optional[Callable[[], bool]]


@dataclass
class QCBeadsRunResult:
    success: bool
    elapsed_seconds: float
    message: str
    run_dir: Path
    input_dir: Path
    output_dir: Path
    copied_count: int
    runner_result: Optional[MvImageIDRunResult] = None


class QCBeadsService:
    """微球荧光强度质控服务。"""

    IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or ConfigManager()
        self.config.ensure_default_config()
        self.project_root = self.config.config_path.resolve().parent

    # ------------------------------------------------------------------
    # 路径工具
    # ------------------------------------------------------------------
    def resolve_project_path(self, path_text: str) -> Path:
        text = str(path_text or "").strip()
        if not text:
            return Path("")
        path = Path(text)
        if path.is_absolute():
            return path
        return self.project_root / path

    def get_qc_root_dir(self) -> Path:
        root_text = self.config.get("QC", "root_dir", r"workspace\qc")
        return self.resolve_project_path(root_text)

    def get_qc_pipeline_path(self, override_pipeline: str = "") -> Path:
        pipeline_text = str(override_pipeline or "").strip()
        if not pipeline_text:
            pipeline_text = self.config.get_mvimageid("qc_pipeline", r"pipelines\pipeline_qc.cppipe")
        return self.resolve_project_path(pipeline_text)

    def get_next_run_dir(self) -> Path:
        """生成 workspace/qc/YYYYMMDD_01 形式的下一个输出目录。"""
        qc_root = self.get_qc_root_dir()
        date_text = datetime.now().strftime("%Y%m%d")

        for index in range(1, 1000):
            candidate = qc_root / f"{date_text}_{index:02d}"
            if not candidate.exists():
                return candidate

        return qc_root / f"{date_text}_{datetime.now().strftime('%H%M%S')}"

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def run(
        self,
        source_folder: str,
        run_dir: str = "",
        qc_pipeline: str = "",
        overwrite: bool = True,
        log_callback: LogCallback = None,
        cancel_callback: CancelCallback = None,
    ) -> QCBeadsRunResult:
        source_path = Path(str(source_folder or "")).expanduser()
        if not source_path.is_absolute():
            source_path = self.resolve_project_path(str(source_path))
        source_path = source_path.resolve()

        if not source_path.exists() or not source_path.is_dir():
            raise NotADirectoryError(f"微球图片目录不存在：{source_path}")

        pipeline_path = self.get_qc_pipeline_path(qc_pipeline).resolve()
        if not pipeline_path.exists() or not pipeline_path.is_file():
            raise FileNotFoundError(f"质控 Pipeline 不存在：{pipeline_path}")

        if str(run_dir or "").strip():
            run_path = Path(str(run_dir)).expanduser()
            if not run_path.is_absolute():
                run_path = self.resolve_project_path(str(run_path))
            run_path = run_path.resolve()
        else:
            run_path = self.get_next_run_dir().resolve()

        input_dir = run_path / "input"
        output_dir = run_path / "output"

        self._log(log_callback, "开始质控微球荧光强度测试...")
        self._log(log_callback, f"微球图片目录：{source_path}")
        self._log(log_callback, f"质控 Pipeline：{pipeline_path}")
        self._log(log_callback, f"质控输出目录：{run_path}")

        self.prepare_run_folders(input_dir, output_dir, overwrite=overwrite)
        copied_count = self.copy_images(source_path, input_dir)

        if copied_count <= 0:
            raise RuntimeError("未找到可用于质控测试的图片文件。支持 tif/tiff/png/jpg/jpeg/bmp。")

        self._log(log_callback, f"已复制微球图片：{copied_count} 张。")

        runner = MvImageIDRunner(
            source_project_dir=str(self.config.get_source_project_dir()),
            python_exe=str(self.config.get_python_exe()),
            module_name=self.config.get_module_name(),
            plugins_directory=str(self.config.get_plugins_directory()),
            log_file="",
        )

        runner_result = runner.run(
            pipeline_file=str(pipeline_path),
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            log_callback=log_callback,
            cancel_callback=cancel_callback,
            log_file="",
        )

        if runner_result.success:
            message = (
                f"质控微球测试完成，用时 {runner_result.elapsed_seconds:.2f} 秒。\n"
                f"输出目录：{output_dir}"
            )
        else:
            message = runner_result.error_message or "质控微球测试失败。"

        return QCBeadsRunResult(
            success=runner_result.success,
            elapsed_seconds=runner_result.elapsed_seconds,
            message=message,
            run_dir=run_path,
            input_dir=input_dir,
            output_dir=output_dir,
            copied_count=copied_count,
            runner_result=runner_result,
        )

    def prepare_run_folders(self, input_dir: Path, output_dir: Path, overwrite: bool = True) -> None:
        input_dir.parent.mkdir(parents=True, exist_ok=True)

        if overwrite:
            if input_dir.exists():
                shutil.rmtree(input_dir)
            if output_dir.exists():
                shutil.rmtree(output_dir)
        else:
            if input_dir.exists() and any(input_dir.iterdir()):
                raise FileExistsError(f"输入目录已有文件：{input_dir}")
            if output_dir.exists() and any(output_dir.iterdir()):
                raise FileExistsError(f"输出目录已有文件：{output_dir}")

        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

    def copy_images(self, source_folder: Path, input_dir: Path) -> int:
        image_files = [
            path for path in sorted(source_folder.rglob("*"))
            if path.is_file() and path.suffix.lower() in self.IMAGE_EXTS
        ]

        copied_count = 0
        used_names = set()

        for source in image_files:
            target_name = source.name
            if target_name in used_names or (input_dir / target_name).exists():
                stem = source.stem
                suffix = source.suffix
                i = 2
                while True:
                    candidate = f"{stem}_{i}{suffix}"
                    if candidate not in used_names and not (input_dir / candidate).exists():
                        target_name = candidate
                        break
                    i += 1

            target = input_dir / target_name
            shutil.copy2(source, target)
            used_names.add(target_name)
            copied_count += 1

        return copied_count

    @staticmethod
    def _log(log_callback: LogCallback, message: str) -> None:
        if log_callback:
            log_callback(str(message))


class QCBeadsWorker(QThread):
    """质控微球测试后台线程。"""

    log_signal = Signal(str)
    finished_signal = Signal(bool, float, str, str, str)

    def __init__(
        self,
        config_path: str,
        source_folder: str,
        run_dir: str = "",
        qc_pipeline: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.config_path = config_path
        self.source_folder = source_folder
        self.run_dir = run_dir
        self.qc_pipeline = qc_pipeline
        self._cancel_requested = False

    def request_cancel(self):
        self._cancel_requested = True

    def run(self):
        try:
            config = ConfigManager(self.config_path)
            config.ensure_default_config()
            service = QCBeadsService(config)
            result = service.run(
                source_folder=self.source_folder,
                run_dir=self.run_dir,
                qc_pipeline=self.qc_pipeline,
                overwrite=True,
                log_callback=self.log_signal.emit,
                cancel_callback=lambda: self._cancel_requested,
            )
            self.finished_signal.emit(
                result.success,
                result.elapsed_seconds,
                result.message,
                str(result.run_dir),
                str(result.output_dir),
            )
        except Exception as e:
            self.finished_signal.emit(False, 0.0, f"质控微球测试异常：{e}", str(self.run_dir or ""), "")
