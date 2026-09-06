"""Analysis V2 联合尾部校准与测量后台线程。"""

import json
import os
import subprocess
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.analysis_v2.tail_measurement_service import TailMeasurementService
from core.config_manager import ConfigManager
from core.analysis_process_registry import analysis_process_registry


WINDOWS_CREATION_FLAGS = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if os.name == "nt"
    else 0
)


from core.analysis_v2.c18b_execution import (
    C18BExecution, _task_protein_key, _field_fitc_path, _field_merge_path,
    _c18b_output_dir, _c18b_instances_path, _c18b_filtered_instances_path,
)
from core.analysis_v2.task_process_context import TaskProcessContext


class TailPathWorker(QThread, C18BExecution):
    """Prepare the formal C18B editor payload for protein3 tail analysis."""

    log_signal = Signal(str)
    finished_signal = Signal(bool, object, str)

    def __init__(
        self,
        project_root: Path,
        task_root: Path,
        python_executable: Path,
        candidate_path_mode: str = "graph_preserving",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.task_root = Path(task_root).resolve()
        self.python_executable = Path(python_executable).resolve()
        self.candidate_path_mode = str(
            candidate_path_mode or "graph_preserving"
        )
        self._cancel_requested = False
        self._process = None

    def request_cancel(self) -> None:
        self._cancel_requested = True
        self.requestInterruption()
        process = self._process
        if process is not None:
            analysis_process_registry._terminate_tree(process.pid, process)

    def _log(self, message):
        self.log_signal.emit(message)

    def run(self) -> None:
        started = time.perf_counter()
        result_path = self.task_root / "c18b_tail_ui_worker_result.json"
        log_path = self.task_root / "logs" / "c18b_tail_ui_worker.log"
        try:
            if not self.python_executable.is_file():
                raise FileNotFoundError(
                    "MvImageID Python 不存在：{}".format(self.python_executable)
                )

            if _task_protein_key(self.task_root) != "protein3":
                raise RuntimeError("TailPathWorker 仅支持 protein3 C18B 尾部流程。")

            fields = self._discover_fields()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8", newline="\n") as log_handle:
                payload = self._run_c18b_workflow(fields, log_handle, started)
                result_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            self.log_signal.emit(
                "Analysis V2：C18B editor payload生成完成，等待人工校准。"
            )
            self.finished_signal.emit(True, payload, "")
        except BaseException as exception:
            detail = "".join(
                traceback.format_exception(
                    type(exception),
                    exception,
                    exception.__traceback__,
                )
            )
            failure = {
                "success": False,
                "workflow": "c18b_tail_editor",
                "tail_backend": "C18B",
                "task_root": str(self.task_root),
                "error": str(exception),
                "traceback": detail,
                "elapsed_seconds": float(time.perf_counter() - started),
            }
            try:
                result_path.write_text(
                    json.dumps(failure, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except BaseException:
                pass
            self.finished_signal.emit(False, {}, detail)


class TailFieldPrepareWorker(QThread):
    """Prepare one field's automatic tail draft after its head is finalized."""

    log_signal = Signal(str)
    finished_signal = Signal(bool, str, object, str)

    def __init__(
        self,
        project_root: Path,
        task_root: Path,
        python_executable: Path,
        field_id: str,
        display_max_dim: int = 1400,
        candidate_path_mode: str = "graph_preserving",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.task_root = Path(task_root).resolve()
        self.python_executable = Path(python_executable).resolve()
        self.field_id = str(field_id or "").strip()
        self.display_max_dim = max(600, int(display_max_dim))
        self.candidate_path_mode = str(
            candidate_path_mode or "graph_preserving"
        )
        self._cancel_requested = False
        self._process = None
        self.process_context = TaskProcessContext()

    @staticmethod
    def _read_json(path: Path):
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError("JSON 顶层必须是对象：{}".format(path))
        return value

    def _cancel_payload(self, started: float):
        return {
            "success": False,
            "cancelled": True,
            "field_id": self.field_id,
            "task_root": str(self.task_root),
            "elapsed_seconds": float(time.perf_counter() - started),
        }

    @staticmethod
    def _terminate_process_tree(process) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.run(
                    [
                        "taskkill",
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    creationflags=creationflags,
                )
            else:
                process.terminate()
        except BaseException:
            try:
                process.kill()
            except BaseException:
                pass

    def request_cancel(self) -> None:
        """Stop the active per-field preparation and its child scripts."""
        self._cancel_requested = True
        self.requestInterruption()
        self.process_context.cancel()

    def run(self) -> None:
        started = time.perf_counter()
        try:
            if self._cancel_requested or self.isInterruptionRequested():
                self.finished_signal.emit(
                    False, self.field_id, self._cancel_payload(started), ""
                )
                return
            if not self.field_id:
                raise ValueError("尾部视野编号不能为空。")
            if not self.python_executable.is_file():
                raise FileNotFoundError(
                    "MvImageID Python 不存在：{}".format(self.python_executable)
                )
            head_labels = (
                self.task_root
                / "calibration"
                / "head"
                / (self.field_id + "_HeadFinalLabels.tif")
            )
            if not head_labels.is_file():
                raise FileNotFoundError(
                    "当前视野最终头部标签尚未生成：{}".format(head_labels)
                )
            if _task_protein_key(self.task_root) != "protein3":
                raise RuntimeError(
                    "TailFieldPrepareWorker 仅支持 protein3 C18B 尾部流程。"
                )
            execution = C18BExecution(
                self.project_root, self.task_root, self.python_executable,
                self.candidate_path_mode, self.log_signal.emit, self.process_context,
            )
            log_path = self.task_root / "logs" / ("tail_field_prepare_{}.log".format(self.field_id))
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8", newline="\n") as log_handle:
                instances_path = execution._ensure_c18b_result(self.field_id, log_handle)
            self.process_context.check_cancelled()
            payload = {
                "success": True,
                "tail_backend": "C18B",
                "field_id": self.field_id,
                "task_root": str(self.task_root),
                "c18b_instances": str(instances_path),
                "elapsed_seconds": float(time.perf_counter() - started),
            }
            self.finished_signal.emit(True, self.field_id, payload, "")
        except BaseException as exception:
            self._process = None
            if self._cancel_requested or self.isInterruptionRequested():
                self.finished_signal.emit(
                    False, self.field_id, self._cancel_payload(started), ""
                )
                return
            detail = "".join(
                traceback.format_exception(
                    type(exception), exception, exception.__traceback__
                )
            )
            self.finished_signal.emit(False, self.field_id, {}, detail)


class TailMeasurementWorker(QThread):
    """Measure calibrated Analysis V2 tail labels without publishing them."""

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
                / "measure_tail_from_labels.cppipe"
            ).resolve()

            if not pipeline.is_file():
                raise FileNotFoundError(
                    "尾部测量管道不存在：{}".format(pipeline)
                )

            self.log_signal.emit(
                (
                    "Analysis V2：开始测量C18B尾部结果。"
                    if _task_protein_key(self.task_root) == "protein3"
                    else "Analysis V2：开始测量人工校准后的尾部标签。"
                )
            )

            service = TailMeasurementService(
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
                "candidate_output_dir": str(service.output_dir),
                "measurement_result_path": str(service.result_path),
                "measurement_manifest_path": str(
                    service.measurement_manifest_path
                ),
            }

            self.log_signal.emit(
                "Analysis V2：尾部测量和严格校验完成，用时 {:.2f} 秒。".format(
                    elapsed
                )
            )
            self.finished_signal.emit(True, elapsed, payload, "")

        except BaseException as exception:
            elapsed = time.perf_counter() - started
            detail = "".join(
                traceback.format_exception(
                    type(exception),
                    exception,
                    exception.__traceback__,
                )
            )
            self.finished_signal.emit(False, elapsed, {}, detail)
