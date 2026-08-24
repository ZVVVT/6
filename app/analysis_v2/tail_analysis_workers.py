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


class TailPathWorker(QThread):
    """Run the validated joint-tail workflow and prepare formal calibration labels.

    The class name is retained to minimize UI wiring changes.  Unlike the legacy
    implementation, this worker now executes:

    1. joint automatic candidate generation;
    2. sequential manual tail editors;
    3. per-field candidate/staging validation;
    4. three-field atomic promotion to ``calibration/tail``.

    Measurement, publication and database replacement remain in the existing UI
    flow and are not performed here.
    """

    log_signal = Signal(str)
    finished_signal = Signal(bool, object, str)

    def __init__(
        self,
        project_root: Path,
        task_root: Path,
        python_executable: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.task_root = Path(task_root).resolve()
        self.python_executable = Path(python_executable).resolve()
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

    def _validate_formal_calibration(self, fields):
        result = []
        required_suffixes = (
            "_TailFinalLabels.tif",
            "_TailFinalHeadIdLabels.tif",
            "_TailPositiveHeadLabels.tif",
            "_TailFinalObjects.json",
        )
        for field_id in fields:
            field_dir = self.task_root / "calibration" / "tail" / field_id
            missing = [
                str(field_dir / (field_id + suffix))
                for suffix in required_suffixes
                if not (field_dir / (field_id + suffix)).is_file()
            ]
            if missing:
                raise FileNotFoundError(
                    "视野 {} 原子提升后缺少正式尾部文件：\n{}".format(
                        field_id,
                        "\n".join(missing),
                    )
                )
            result.append({
                "field_id": field_id,
                "output_dir": str(field_dir),
                "tail_final_labels": str(
                    field_dir / (field_id + "_TailFinalLabels.tif")
                ),
                "tail_positive_head_labels": str(
                    field_dir / (field_id + "_TailPositiveHeadLabels.tif")
                ),
            })
        return result

    def _ensure_c18b_result(self, field_id, log_handle):
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
            ],
            "{} C18B backend".format(field_id),
            log_handle,
        )
        if not instances_path.is_file() or instances_path.stat().st_size <= 0:
            raise FileNotFoundError("C18B未生成实例标签：{}".format(instances_path))
        return instances_path

    def _prepare_c18b_editor_payload(self, field_id, instances_path, log_handle):
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
                "--instances", str(instances_path),
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
            "output_dir": str(output_dir.resolve()),
            "python_executable": str(self.python_executable),
            "editor_script": str(editor_script),
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
            "joint_workflow_completed": False,
            "manual_calibration_completed": False,
            "ready_for_measurement": False,
            "task_root": str(self.task_root),
            "fields": editor_payloads,
            "elapsed_seconds": float(time.perf_counter() - started),
        }

    def run(self) -> None:
        started = time.perf_counter()
        result_path = self.task_root / "tail_joint_ui_worker_result.json"
        log_path = self.task_root / "logs" / "tail_joint_ui_worker.log"
        try:
            if not self.python_executable.is_file():
                raise FileNotFoundError(
                    "MvImageID Python 不存在：{}".format(self.python_executable)
                )

            oneclick_script = (
                self.project_root
                / "tools"
                / "analysis_v2"
                / "tail_joint_oneclick_v2.py"
            ).resolve()
            promotion_script = (
                self.project_root
                / "tools"
                / "analysis_v2"
                / "tail_joint_promote_measure_v2.py"
            ).resolve()
            is_c18b = _task_protein_key(self.task_root) == "protein3"
            missing_scripts = (
                []
                if is_c18b
                else [
                    str(path)
                    for path in (oneclick_script, promotion_script)
                    if not path.is_file()
                ]
            )
            if missing_scripts:
                raise FileNotFoundError(
                    "联合尾部正式流程缺少脚本：\n{}".format(
                        "\n".join(missing_scripts)
                    )
                )

            fields = self._discover_fields()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8", newline="\n") as log_handle:
                if is_c18b:
                    payload = self._run_c18b_workflow(fields, log_handle, started)
                    result_path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    self.log_signal.emit(
                        "Analysis V2：C18B editor payload生成完成，等待人工校准。"
                    )
                    self.finished_signal.emit(True, payload, "")
                    return
                _, oneclick_return_code = self._run_streaming_command(
                    [
                        str(self.python_executable),
                        "-u",
                        str(oneclick_script),
                        "--task-root",
                        str(self.task_root),
                        "--manual-stream",
                        "--wait-ready-timeout",
                        "300",
                        "--finalize-workers",
                        "1",
                    ],
                    "联合尾部自动候选和人工校准",
                    log_handle,
                    allowed_return_codes=(0, 2),
                )

                oneclick_summary_path = (
                    self.task_root
                    / "logs"
                    / "tail_joint_oneclick_v2_summary.json"
                )
                if not oneclick_summary_path.is_file():
                    raise FileNotFoundError(
                        "联合尾部流程缺少汇总：{}".format(
                            oneclick_summary_path
                        )
                    )
                oneclick_summary = self._read_json(oneclick_summary_path)
                if bool(oneclick_summary.get("cancelled")):
                    cancelled_field = str(
                        oneclick_summary.get("cancelled_field") or ""
                    ).strip()
                    cancel_message = str(
                        oneclick_summary.get("cancel_message")
                        or "用户关闭人工尾部校准窗口且未保存。"
                    ).strip()
                    payload = {
                        "success": False,
                        "cancelled": True,
                        "workflow": "tail_joint_v2",
                        "joint_workflow_completed": False,
                        "manual_calibration_completed": False,
                        "ready_for_measurement": False,
                        "task_root": str(self.task_root),
                        "fields": [],
                        "staged_fields": list(
                            oneclick_summary.get("staged_fields") or []
                        ),
                        "cancelled_field": cancelled_field,
                        "message": cancel_message,
                        "oneclick_return_code": int(oneclick_return_code),
                        "oneclick_summary_path": str(oneclick_summary_path),
                        "elapsed_seconds": float(
                            time.perf_counter() - started
                        ),
                    }
                    result_path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    self.log_signal.emit(
                        "Analysis V2：{}".format(cancel_message)
                    )
                    self.finished_signal.emit(True, payload, "")
                    return
                if not bool(oneclick_summary.get("success")):
                    raise RuntimeError(
                        str(oneclick_summary.get("error") or "联合尾部流程失败。")
                    )
                staged_fields = list(oneclick_summary.get("staged_fields") or [])
                if staged_fields != fields:
                    raise RuntimeError(
                        "联合尾部暂存视野不一致：期望 {}，实际 {}。".format(
                            fields,
                            staged_fields,
                        )
                    )
                if not bool(oneclick_summary.get("all_selected_fields_staged")):
                    raise RuntimeError("联合尾部流程未完成全部视野暂存。")

                self._run_streaming_command(
                    [
                        str(self.python_executable),
                        "-u",
                        str(promotion_script),
                        "--task-root",
                        str(self.task_root),
                        "--project-root",
                        str(self.project_root),
                        "--promote-only",
                    ],
                    "三视野尾部原子提升",
                    log_handle,
                )

            promotion_summary_path = (
                self.task_root
                / "logs"
                / "tail_joint_promote_measure_v2_summary.json"
            )
            if not promotion_summary_path.is_file():
                raise FileNotFoundError(
                    "原子提升缺少汇总：{}".format(promotion_summary_path)
                )
            promotion_summary = self._read_json(promotion_summary_path)
            if not bool(promotion_summary.get("success")):
                raise RuntimeError(
                    str(promotion_summary.get("error") or "原子提升失败。")
                )
            if str(promotion_summary.get("mode") or "") != "promote_only":
                raise RuntimeError("原子提升未运行在 promote_only 模式。")

            formal_fields = self._validate_formal_calibration(fields)
            payload = {
                "success": True,
                "workflow": "tail_joint_v2",
                "joint_workflow_completed": True,
                "manual_calibration_completed": True,
                "ready_for_measurement": True,
                "task_root": str(self.task_root),
                "fields": formal_fields,
                "oneclick_summary_path": str(oneclick_summary_path),
                "promotion_summary_path": str(promotion_summary_path),
                "elapsed_seconds": float(time.perf_counter() - started),
            }
            result_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.log_signal.emit(
                "Analysis V2：联合尾部校准和三视野原子提升完成，准备测量。"
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
                "workflow": (
                    "c18b_tail_editor"
                    if _task_protein_key(self.task_root) == "protein3"
                    else "tail_joint_v2"
                ),
                "tail_backend": (
                    "C18B"
                    if _task_protein_key(self.task_root) == "protein3"
                    else ""
                ),
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
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.task_root = Path(task_root).resolve()
        self.python_executable = Path(python_executable).resolve()
        self.field_id = str(field_id or "").strip()
        self.display_max_dim = max(600, int(display_max_dim))
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
            is_c18b = _task_protein_key(self.task_root) == "protein3"
            if is_c18b:
                instances_path = _c18b_instances_path(
                    self.task_root,
                    self.field_id,
                )
                if instances_path.is_file() and instances_path.stat().st_size > 0:
                    self.log_signal.emit(
                        "[C18B backend] 视野 {} 已有实例结果，跳过旧tail_joint准备。".format(
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
                ]
            else:
                command = None
            oneclick_script = (
                self.project_root
                / "tools"
                / "analysis_v2"
                / "tail_joint_oneclick_v2.py"
            ).resolve()
            if not is_c18b and not oneclick_script.is_file():
                raise FileNotFoundError(
                    "联合尾部脚本不存在：{}".format(oneclick_script)
                )

            log_path = (
                self.task_root
                / "logs"
                / ("tail_field_prepare_{}.log".format(self.field_id))
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)
            safe_field_id = "".join(
                character
                if character.isalnum() or character in "-_."
                else "_"
                for character in self.field_id
            )
            summary_path = (
                self.task_root
                / "logs"
                / ("tail_field_prepare_{}_summary.json".format(safe_field_id))
            )
            oneclick_log_path = (
                self.task_root
                / "logs"
                / ("tail_field_prepare_{}_oneclick.log".format(safe_field_id))
            )
            if command is None:
                command = [
                    str(self.python_executable),
                    "-u",
                    str(oneclick_script),
                    "--task-root",
                    str(self.task_root),
                    "--fields",
                    self.field_id,
                    "--prepare-only",
                    "--display-max-dim",
                    str(self.display_max_dim),
                    "--summary-path",
                    str(summary_path),
                    "--log-path",
                    str(oneclick_log_path),
                ]
            child_env = os.environ.copy()
            child_env["PYTHONIOENCODING"] = "utf-8"
            child_env["PYTHONUNBUFFERED"] = "1"
            self.log_signal.emit(
                (
                    "[C18B backend] 视野 {} 头部已完成，后台生成C18B实例。"
                    if is_c18b
                    else "Analysis V2：视野 {} 头部已完成，后台开始准备对应尾部。"
                ).format(self.field_id)
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

            if is_c18b:
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
                return

            if not summary_path.is_file():
                raise FileNotFoundError(
                    "尾部后台准备缺少汇总：{}".format(summary_path)
                )
            summary = self._read_json(summary_path)
            selected_fields = list(summary.get("selected_fields") or [])
            if not bool(summary.get("success")):
                raise RuntimeError(
                    str(summary.get("error") or "尾部后台准备汇总未标记成功。")
                )
            if not bool(summary.get("prepare_only")):
                raise RuntimeError("尾部后台准备没有运行在 prepare-only 模式。")
            if selected_fields != [self.field_id]:
                raise RuntimeError(
                    "尾部后台准备视野不一致：期望 {}，实际 {}。".format(
                        [self.field_id], selected_fields
                    )
                )
            payload = {
                "success": True,
                "field_id": self.field_id,
                "task_root": str(self.task_root),
                "summary_path": str(summary_path),
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
