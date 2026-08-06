"""Analysis V2 head workflow background workers."""

from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Any, Dict, Sequence

from PySide6.QtCore import QThread, Signal

from core.analysis_v2 import (
    AnalysisTaskPaths,
    HeadMeasurementService,
    run_head_segmentation,
)
from core.config_manager import ConfigManager
from core.analysis_process_registry import analysis_process_registry


class HeadSegmentationWorker(QThread):
    """Run Analysis V2 direct Cellpose head segmentation."""

    log_signal = Signal(str)
    finished_signal = Signal(bool, float, object, str)

    def __init__(
        self,
        project_root: Path,
        case_data: Dict[str, Any],
        protein_key: str,
        paired_fields: Sequence[Dict[str, Any]],
        config: ConfigManager,
        timeout_seconds: float = 600.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.case_data = dict(case_data or {})
        self.protein_key = str(protein_key or "").strip()
        self.paired_fields = [
            dict(item)
            for item in paired_fields
        ]
        self.config = config
        self.timeout_seconds = float(timeout_seconds)

    def request_cancel(self) -> None:
        self.requestInterruption()
        analysis_process_registry.terminate_all()

    def run(self) -> None:
        started = time.perf_counter()

        try:
            case_no = str(
                self.case_data.get("case_no", "") or ""
            ).strip()

            if not case_no:
                raise RuntimeError(
                    "\u5f53\u524d\u75c5\u4f8b\u7f16\u53f7\u4e3a\u7a7a\u3002"
                )

            if not self.protein_key:
                raise RuntimeError(
                    "\u86cb\u767d\u5185\u90e8\u7f16\u53f7\u4e3a\u7a7a\u3002"
                )

            if not self.paired_fields:
                raise RuntimeError(
                    "\u6ca1\u6709\u53ef\u7528\u4e8e\u5934\u90e8\u8bc6\u522b\u7684 R/G \u89c6\u91ce\u3002"
                )

            paths = AnalysisTaskPaths.for_case(
                project_root=self.project_root,
                case_no=case_no,
                protein_key=self.protein_key,
            )

            self.log_signal.emit(
                "Analysis V2\uff1a\u5f00\u59cb\u5934\u90e8\u5206\u5272\uff0c"
                "\u89c6\u91ce\u6570 {} \u3002".format(
                    len(self.paired_fields)
                )
            )

            result = run_head_segmentation(
                paths=paths,
                paired_fields=self.paired_fields,
                mvimageid_root=self.config.get_source_project_dir(),
                mvimageid_python=self.config.get_python_exe(),
                worker_path=(
                    self.project_root
                    / "tools"
                    / "analysis_v2"
                    / "direct_cellpose_worker.py"
                ),
                timeout_seconds=self.timeout_seconds,
                case_no=case_no,
                protein_key=self.protein_key,
            )

            elapsed = time.perf_counter() - started

            payload = {
                "task_root": str(paths.task_root),
                "paths": paths.as_dict(),
                "segmentation_result": result,
                "case_no": case_no,
                "protein_key": self.protein_key,
                "field_count": len(self.paired_fields),
            }

            self.log_signal.emit(
                "Analysis V2\uff1a\u5934\u90e8\u5206\u5272\u5b8c\u6210\uff0c"
                "\u7528\u65f6 {:.2f} \u79d2\u3002".format(elapsed)
            )
            self.finished_signal.emit(
                True,
                elapsed,
                payload,
                "",
            )

        except BaseException as exception:
            elapsed = time.perf_counter() - started
            detail = "".join(
                traceback.format_exception(
                    type(exception),
                    exception,
                    exception.__traceback__,
                )
            )
            self.finished_signal.emit(
                False,
                elapsed,
                {},
                detail,
            )


class HeadMeasurementWorker(QThread):
    """Measure calibrated Analysis V2 head labels."""

    log_signal = Signal(str)
    finished_signal = Signal(bool, float, object, str)

    def __init__(
        self,
        project_root: Path,
        task_root: Path,
        config: ConfigManager,
        timeout_seconds: float = 900.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.task_root = Path(task_root).resolve()
        self.config = config
        self.timeout_seconds = float(timeout_seconds)

    def request_cancel(self) -> None:
        self.requestInterruption()
        analysis_process_registry.terminate_all()

    def run(self) -> None:
        started = time.perf_counter()

        try:
            pipeline = (
                self.project_root
                / "pipelines"
                / "analysis_v2"
                / "measure_head_from_labels.cppipe"
            ).resolve()

            if not pipeline.is_file():
                raise FileNotFoundError(
                    "\u5934\u90e8\u6d4b\u91cf\u7ba1\u9053\u4e0d\u5b58\u5728\uff1a{}".format(
                        pipeline
                    )
                )

            self.log_signal.emit(
                "Analysis V2\uff1a\u5f00\u59cb\u6d4b\u91cf\u4eba\u5de5\u6821\u51c6\u540e\u7684\u5934\u90e8\u6807\u7b7e\u3002"
            )

            service = HeadMeasurementService(
                task_root=self.task_root,
                pipeline=pipeline,
                mvimageid_root=self.config.get_source_project_dir(),
                python_exe=self.config.get_python_exe(),
                plugins_directory=self.config.get_plugins_directory(),
                timeout_seconds=self.timeout_seconds,
            )

            result = service.run()
            elapsed = time.perf_counter() - started

            payload = {
                "task_root": str(self.task_root),
                "measurement_result": result,
                "measurement_output_dir": str(
                    service.output_dir
                ),
                "measurement_result_path": str(
                    service.result_path
                ),
            }

            self.log_signal.emit(
                "Analysis V2\uff1a\u5934\u90e8\u6d4b\u91cf\u5b8c\u6210\uff0c"
                "\u7528\u65f6 {:.2f} \u79d2\u3002".format(elapsed)
            )
            self.finished_signal.emit(
                True,
                elapsed,
                payload,
                "",
            )

        except BaseException as exception:
            elapsed = time.perf_counter() - started
            detail = "".join(
                traceback.format_exception(
                    type(exception),
                    exception,
                    exception.__traceback__,
                )
            )
            self.finished_signal.emit(
                False,
                elapsed,
                {},
                detail,
            )
