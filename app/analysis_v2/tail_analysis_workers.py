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


def _task_protein_key(task_root: Path) -> str:
    manifest_path = Path(task_root) / "manifest.json"
    if not manifest_path.is_file():
        return ""
    with manifest_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return str(payload.get("protein_key", "") or "").strip()


def _field_fitc_path(task_root: Path, field_id: str) -> Path:
    input_dir = Path(task_root) / "input"
    matches = sorted(input_dir.glob("{}_FITC.*".format(field_id)))
    matches = [path.resolve() for path in matches if path.is_file()]
    if len(matches) != 1:
        raise FileNotFoundError(
            "视野 {} 的FITC输入数量必须为1，实际={}。".format(
                field_id,
                [str(path) for path in matches],
            )
        )
    return matches[0]


def _field_merge_path(task_root: Path, field_id: str) -> Path:
    input_dir = Path(task_root) / "input"
    matches = sorted(input_dir.glob("{}_Merge.*".format(field_id)))
    matches = [path.resolve() for path in matches if path.is_file()]
    if len(matches) != 1:
        raise FileNotFoundError(
            "视野 {} 的Merge输入数量必须为1，实际={}。".format(
                field_id,
                [str(path) for path in matches],
            )
        )
    return matches[0]


def _c18b_output_dir(task_root: Path, field_id: str) -> Path:
    return Path(task_root) / "segmentation" / "c18b_score015" / field_id


def _c18b_instances_path(task_root: Path, field_id: str) -> Path:
    fitc_path = _field_fitc_path(task_root, field_id)
    return (
        _c18b_output_dir(task_root, field_id)
        / fitc_path.stem
        / "06_final_tail_instances.tif"
    ).resolve()


def _c18b_filtered_instances_path(task_root: Path, field_id: str) -> Path:
    return (
        _c18b_instances_path(task_root, field_id).parent
        / "07_extreme_fragment_filtered_labels.tif"
    ).resolve()


class TailPathWorker(QThread):
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

    @staticmethod
    def _read_json(path: Path):
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError("JSON 顶层必须是对象：{}".format(path))
        return value

    def _discover_fields(self):
        worker_input = self.task_root / "worker_input.json"
        if not worker_input.is_file():
            raise FileNotFoundError(
                "Analysis V2 缺少 worker_input.json：{}".format(worker_input)
            )
        payload = self._read_json(worker_input)
        fields = []
        for item in list(payload.get("fields") or []):
            if not isinstance(item, dict):
                continue
            field_id = str(item.get("field_id", "") or "").strip()
            if field_id and field_id not in fields:
                fields.append(field_id)
        if not fields:
            raise RuntimeError("worker_input.json 中没有有效视野。")
        return fields

    def _run_streaming_command(
        self,
        command,
        label,
        log_handle,
        allowed_return_codes=(0,),
    ):
        command = [str(value) for value in command]
        self.log_signal.emit("Analysis V2：开始{}。".format(label))
        log_handle.write("\n===== {} =====\n".format(label))
        log_handle.write(subprocess.list2cmdline(command) + "\n")
        log_handle.flush()

        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUNBUFFERED"] = "1"

        process = analysis_process_registry.register(subprocess.Popen(
            command,
            cwd=str(self.project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=child_env,
            creationflags=WINDOWS_CREATION_FLAGS,
        ))
        self._process = process
        if process.stdout is None:
            raise RuntimeError("{} 未能创建标准输出管道。".format(label))

        output_lines = []
        try:
            for line in process.stdout:
                if self._cancel_requested or self.isInterruptionRequested():
                    analysis_process_registry._terminate_tree(process.pid, process)
                    break
                output_lines.append(line)
                log_handle.write(line)
                log_handle.flush()
                text = line.rstrip("\r\n")
                if text:
                    self.log_signal.emit(text)
            return_code = process.wait()
        except BaseException:
            if process.poll() is None:
                process.kill()
            process.wait()
            raise
        finally:
            self._process = None
            analysis_process_registry.unregister(process)
        output = "".join(output_lines)
        log_handle.write(
            "===== {} return_code={} =====\n".format(label, return_code)
        )
        log_handle.flush()
        allowed_codes = {int(value) for value in allowed_return_codes}
        if return_code not in allowed_codes:
            raise RuntimeError(
                "{}执行失败，return_code={}。\n{}".format(
                    label,
                    return_code,
                    output[-12000:],
                )
            )
        return output, return_code

    def _ensure_c18b_result(self, field_id, log_handle):
        message = "C18B: field={} candidate_path_mode={}".format(
            field_id, self.candidate_path_mode
        )
        self.log_signal.emit(message)
        log_handle.write(message + "\n")
        log_handle.flush()
        instances_path = _c18b_instances_path(self.task_root, field_id)
        if instances_path.is_file() and instances_path.stat().st_size > 0:
            message = "[C18B backend] {} 使用已有实例结果：{}".format(
                field_id,
                instances_path,
            )
            self.log_signal.emit(message)
            log_handle.write(message + "\n")
            log_handle.flush()
            return instances_path

        fitc_path = _field_fitc_path(self.task_root, field_id)
        head_labels = (
            self.task_root
            / "calibration"
            / "head"
            / (field_id + "_HeadFinalLabels.tif")
        )
        runner = (
            self.project_root
            / "tools"
            / "analysis_v2"
            / "c18b_score015_adapter.py"
        ).resolve()
        if not runner.is_file():
            raise FileNotFoundError("C18B运行器不存在：{}".format(runner))
        compatibility_dir = (
            self.task_root
            / "segmentation"
            / "c18b_runner_contract"
            / field_id
        )
        self.log_signal.emit(
            "[C18B backend] {} 开始生成尾部实例。".format(field_id)
        )
        self._run_streaming_command(
            [
                str(self.python_executable),
                "-u",
                str(runner),
                "--green",
                str(fitc_path),
                "--head-labels",
                str(head_labels),
                "--output-dir",
                str(compatibility_dir),
                "--c18b-output-dir",
                str(_c18b_output_dir(self.task_root, field_id)),
                "--candidate-path-mode",
                self.candidate_path_mode,
            ],
            "{} C18B backend".format(field_id),
            log_handle,
        )
        if not instances_path.is_file() or instances_path.stat().st_size <= 0:
            raise FileNotFoundError("C18B未生成实例标签：{}".format(instances_path))
        return instances_path

    def _prepare_c18b_editor_payload(self, field_id, instances_path, log_handle):
        filter_script = (
            self.project_root
            / "tools"
            / "analysis_v2"
            / "c18b_score015"
            / "extreme_fragment_filter.py"
        ).resolve()
        if not filter_script.is_file():
            raise FileNotFoundError(
                "C18B极短碎片过滤器不存在：{}".format(filter_script)
            )
        self._run_streaming_command(
            [
                str(self.python_executable),
                "-u",
                str(filter_script),
                str(Path(instances_path).resolve().parent),
            ],
            "{} C18B extreme fragment filter".format(field_id),
            log_handle,
        )
        filtered_instances_path = _c18b_filtered_instances_path(
            self.task_root, field_id
        )
        if (not filtered_instances_path.is_file()
                or filtered_instances_path.stat().st_size <= 0):
            raise FileNotFoundError(
                "C18B极短碎片过滤未生成labels：{}".format(
                    filtered_instances_path
                )
            )
        adapter = (
            self.project_root
            / "tools"
            / "analysis_v2"
            / "c18b_tail_editor_adapter.py"
        ).resolve()
        if not adapter.is_file():
            raise FileNotFoundError("C18B editor adapter不存在：{}".format(adapter))
        head_labels = (
            self.task_root
            / "calibration"
            / "head"
            / (field_id + "_HeadFinalLabels.tif")
        )
        fitc_path = _field_fitc_path(self.task_root, field_id)
        merge_path = _field_merge_path(self.task_root, field_id)
        probability_path = (
            self.task_root
            / "segmentation"
            / "c18b_runner_contract"
            / field_id
            / "02_probability_uint16.tif"
        )
        output_dir = self.task_root / "calibration" / "tail" / field_id
        editor_script = (
            self.project_root
            / "tools"
            / "analysis_v2"
            / "tail_legacy"
            / "tail_result_editor_v2_3_draft_mvp.py"
        ).resolve()
        if not editor_script.is_file():
            raise FileNotFoundError("尾部editor不存在：{}".format(editor_script))
        self._run_streaming_command(
            [
                str(self.python_executable),
                "-u",
                str(adapter),
                "--instances", str(filtered_instances_path),
                "--head-labels", str(head_labels),
                "--fitc", str(fitc_path),
                "--merge", str(merge_path),
                "--probability", str(probability_path),
                "--output-dir", str(output_dir),
            ],
            "{} C18B editor payload".format(field_id),
            log_handle,
        )

        return {
            "field_id": field_id,
            "merge": str(merge_path),
            "green": str(fitc_path),
            "probability": str((output_dir / "probability.tif").resolve()),
            "fragments": str((output_dir / "fragments.tif").resolve()),
            "head_labels": str(head_labels.resolve()),
            "entries": str((output_dir / "entries.json").resolve()),
            "paths": str((output_dir / "paths.json").resolve()),
            "global_results": str((output_dir / "global_results.json").resolve()),
            "unassigned_candidates": str(
                (output_dir / "unassigned_tail_candidates.json").resolve()
            ),
            "output_dir": str(output_dir.resolve()),
            "python_executable": str(self.python_executable),
            "editor_script": str(editor_script),
            "c18b_baseline_instances": str(Path(instances_path).resolve()),
            "c18b_filtered_instances": str(filtered_instances_path),
        }

    def _run_c18b_workflow(self, fields, log_handle, started):
        self.log_signal.emit("Analysis V2：开始C18B尾部处理。")
        editor_payloads = []
        for field_id in fields:
            instances_path = self._ensure_c18b_result(field_id, log_handle)
            editor_payloads.append(
                self._prepare_c18b_editor_payload(
                    field_id,
                    instances_path,
                    log_handle,
                )
            )
        return {
            "success": True,
            "workflow": "c18b_tail_editor",
            "tail_backend": "C18B",
            "manual_calibration_completed": False,
            "ready_for_measurement": False,
            "task_root": str(self.task_root),
            "fields": editor_payloads,
            "elapsed_seconds": float(time.perf_counter() - started),
        }

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
        self._terminate_process_tree(self._process)

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
            self.log_signal.emit(
                "C18B: field={} candidate_path_mode={}".format(
                    self.field_id, self.candidate_path_mode
                )
            )
            instances_path = _c18b_instances_path(
                self.task_root,
                self.field_id,
            )
            if instances_path.is_file() and instances_path.stat().st_size > 0:
                self.log_signal.emit(
                    "[C18B backend] 视野 {} 已有实例结果，跳过重复准备。".format(
                        self.field_id
                    )
                )
                payload = {
                    "success": True,
                    "tail_backend": "C18B",
                    "field_id": self.field_id,
                    "task_root": str(self.task_root),
                    "c18b_instances": str(instances_path),
                    "elapsed_seconds": float(time.perf_counter() - started),
                }
                self.finished_signal.emit(True, self.field_id, payload, "")
                return
            c18b_runner = (
                self.project_root
                / "tools"
                / "analysis_v2"
                / "c18b_score015_adapter.py"
            ).resolve()
            if not c18b_runner.is_file():
                raise FileNotFoundError("C18B运行器不存在：{}".format(c18b_runner))
            fitc_path = _field_fitc_path(self.task_root, self.field_id)
            command = [
                str(self.python_executable),
                "-u",
                str(c18b_runner),
                "--green",
                str(fitc_path),
                "--head-labels",
                str(head_labels),
                "--output-dir",
                str(
                    self.task_root
                    / "segmentation"
                    / "c18b_runner_contract"
                    / self.field_id
                ),
                "--c18b-output-dir",
                str(_c18b_output_dir(self.task_root, self.field_id)),
                "--candidate-path-mode",
                self.candidate_path_mode,
            ]

            log_path = (
                self.task_root
                / "logs"
                / ("tail_field_prepare_{}.log".format(self.field_id))
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)
            child_env = os.environ.copy()
            child_env["PYTHONIOENCODING"] = "utf-8"
            child_env["PYTHONUNBUFFERED"] = "1"
            self.log_signal.emit(
                "[C18B backend] 视野 {} 头部已完成，后台生成C18B实例。".format(
                    self.field_id
                )
            )
            with log_path.open("a", encoding="utf-8", newline="\n") as log_handle:
                log_handle.write(subprocess.list2cmdline(command) + "\n")
                log_handle.flush()
                process = analysis_process_registry.register(subprocess.Popen(
                    command,
                    cwd=str(self.project_root),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=child_env,
                    creationflags=WINDOWS_CREATION_FLAGS,
                ))
                self._process = process
                if self._cancel_requested or self.isInterruptionRequested():
                    self._terminate_process_tree(process)
                output_lines = []
                if process.stdout is None:
                    raise RuntimeError("尾部后台准备未能创建标准输出管道。")
                try:
                    for line in process.stdout:
                        output_lines.append(line)
                        log_handle.write(line)
                        log_handle.flush()
                        text = line.rstrip("\r\n")
                        if text:
                            self.log_signal.emit(
                                "[{} 尾部后台] {}".format(self.field_id, text)
                            )
                    return_code = process.wait()
                except BaseException:
                    self._terminate_process_tree(process)
                    process.wait()
                    raise
                finally:
                    self._process = None
                    analysis_process_registry.unregister(process)
            if self._cancel_requested or self.isInterruptionRequested():
                self.finished_signal.emit(
                    False, self.field_id, self._cancel_payload(started), ""
                )
                return
            if return_code != 0:
                raise RuntimeError(
                    "视野 {} 尾部后台准备失败，return_code={}。\n{}".format(
                        self.field_id,
                        return_code,
                        "".join(output_lines)[-12000:],
                    )
                )

            instances_path = _c18b_instances_path(
                self.task_root,
                self.field_id,
            )
            if not instances_path.is_file() or instances_path.stat().st_size <= 0:
                raise FileNotFoundError(
                    "C18B后台准备缺少实例结果：{}".format(instances_path)
                )
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
