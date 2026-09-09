"""Shared formal C18B execution. No UI or thread creation."""

import json
import os
import subprocess
import time
from pathlib import Path

from core.analysis_process_registry import analysis_process_registry
from .task_process_context import TaskProcessCancelled
from .tail_core_checkpoint import (
    PROBABILITY_NAME,
    PROBABILITY_ROLE,
    recover_and_verify_tail_core_checkpoint,
    write_and_verify_tail_core_checkpoint,
)
from .association_checkpoint import write_and_verify_association_checkpoint

WINDOWS_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


class C18BExecutionError(RuntimeError):
    def __init__(self, message, return_code=None, log_path=None, field_id=None):
        super().__init__(message)
        self.return_code = return_code
        self.log_path = log_path
        self.field_id = field_id


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


class C18BExecution:
    def __init__(self, project_root, task_root, python_executable,
                 candidate_path_mode="graph_preserving", log_callback=None,
                 process_context=None, tail_core_recovery_sources=None):
        self.project_root = Path(project_root).resolve()
        self.task_root = Path(task_root).resolve()
        self.python_executable = Path(python_executable).resolve()
        self.candidate_path_mode = candidate_path_mode
        self.log_callback = log_callback
        self.process_context = process_context
        self._process = None
        self.field_id = None
        self.tail_core_recovery_sources = dict(tail_core_recovery_sources or {})

    def _log(self, message):
        if self.log_callback is not None:
            self.log_callback(message)

    def _check_cancelled(self):
        context = getattr(self, "process_context", None)
        if context is not None:
            context.check_cancelled()
        if getattr(self, "_cancel_requested", False):
            raise TaskProcessCancelled("C18B cancelled")
        interrupted = getattr(self, "isInterruptionRequested", None)
        if interrupted is not None and interrupted():
            raise TaskProcessCancelled("C18B interrupted")

    def run(self):
        self._check_cancelled()
        if not self.python_executable.is_file():
            raise FileNotFoundError(str(self.python_executable))
        if _task_protein_key(self.task_root) != "protein3":
            raise ValueError("C18B requires protein3")
        log_path = self.task_root / "logs" / "c18b_tail_ui_worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            return self._run_c18b_workflow(self._discover_fields(), handle, time.perf_counter())

    def _add_phase_timing(self, phase, elapsed):
        timings = getattr(self, "_phase_timings_seconds", None)
        if timings is None:
            timings = {}
            self._phase_timings_seconds = timings
        timings[phase] = timings.get(phase, 0.0) + float(elapsed)

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
        launch_stage=None,
    ):
        self._check_cancelled()
        command = [str(value) for value in command]
        self._log("Analysis V2：开始{}。".format(label))
        log_handle.write("\n===== {} =====\n".format(label))
        log_handle.write(subprocess.list2cmdline(command) + "\n")
        log_handle.flush()

        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUNBUFFERED"] = "1"

        process_context = getattr(self, "process_context", None)
        registry = process_context or analysis_process_registry
        popen_kwargs = {
            "cwd": str(self.project_root),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
            "env": child_env,
            "creationflags": WINDOWS_CREATION_FLAGS,
        }
        self._check_cancelled()
        if process_context is not None:
            # spawn_atomic owns suspended creation, Job assignment, direct
            # registration, sticky cancellation and resume.  Do not register
            # this Popen-compatible result a second time.
            process = process_context.spawn_atomic(command, **popen_kwargs)
            launch_provenance = {
                "stage": str(launch_stage or label),
                "field_id": getattr(self, "field_id", None),
                "mode": "atomic",
                "worker_pid": int(process.pid),
                "worker_python": str(self.python_executable),
                "job_owned": True,
                "assign_completed": True,
                "resume_completed": True,
            }
        else:
            # Explicit transition compatibility for callers without a task Job.
            process = analysis_process_registry.register(
                subprocess.Popen(command, **popen_kwargs)
            )
            launch_provenance = {
                "stage": str(launch_stage or label),
                "field_id": getattr(self, "field_id", None),
                "mode": "legacy",
                "worker_pid": int(process.pid),
                "worker_python": str(self.python_executable),
                "job_owned": False,
            }
        # This is deliberately after spawn_atomic returns: no disk I/O occurs
        # between AssignProcessToJobObject and ResumeThread.
        log_handle.write(
            "C18B launch_provenance="
            + json.dumps(launch_provenance, ensure_ascii=False, sort_keys=True)
            + "\n"
        )
        log_handle.flush()
        self._process = process
        output_lines = []
        try:
            self._check_cancelled()
            if process.stdout is None:
                raise RuntimeError("Missing subprocess stdout: {}".format(label))
            for line in process.stdout:
                self._check_cancelled()
                output_lines.append(line)
                log_handle.write(line)
                log_handle.flush()
                text = line.rstrip("\r\n")
                if text:
                    self._log(text)
            return_code = process.wait()
            self._check_cancelled()
        except BaseException:
            if process.poll() is None:
                analysis_process_registry._terminate_tree(process.pid, process)
            process.wait()
            raise
        finally:
            self._process = None
            registry.unregister(process)
        output = "".join(output_lines)
        log_handle.write(
            "===== {} return_code={} =====\n".format(label, return_code)
        )
        log_handle.flush()
        allowed_codes = {int(value) for value in allowed_return_codes}
        if return_code not in allowed_codes:
            raise C18BExecutionError(
                "{}执行失败，return_code={}。\n{}".format(
                    label,
                    return_code,
                    output[-12000:],
                ), return_code=return_code, log_path=getattr(log_handle, "name", None),
                field_id=getattr(self, "field_id", None),
            )
        return output, return_code

    def _ensure_c18b_result(self, field_id, log_handle):
        self.field_id = field_id
        self._check_cancelled()
        message = "C18B: field={} candidate_path_mode={}".format(
            field_id, self.candidate_path_mode
        )
        self._log(message)
        log_handle.write(message + "\n")
        log_handle.flush()
        instances_path = _c18b_instances_path(self.task_root, field_id)
        if instances_path.is_file() and instances_path.stat().st_size > 0:
            message = "[C18B backend] {} 使用已有实例结果：{}".format(
                field_id,
                instances_path,
            )
            self._log(message)
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
        self._log(
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
            launch_stage="c18b_tail_core",
        )
        if not instances_path.is_file() or instances_path.stat().st_size <= 0:
            raise FileNotFoundError("C18B未生成实例标签：{}".format(instances_path))
        return instances_path

    def _prepare_c18b_editor_payload(self, field_id, instances_path, log_handle,
                                      checkpoint=None, apply_filter=True):
        if apply_filter:
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
            filter_started = time.perf_counter()
            self._run_streaming_command(
                [
                    str(self.python_executable),
                    "-u",
                    str(filter_script),
                    str(Path(instances_path).resolve().parent),
                ],
                "{} C18B extreme fragment filter".format(field_id),
                log_handle,
                launch_stage="extreme_fragment_filter",
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
        if apply_filter:
            self._add_phase_timing(
                "fragment_filter",
                time.perf_counter() - filter_started,
            )
            checkpoint = write_and_verify_tail_core_checkpoint(
                self.task_root, self.project_root, Path(instances_path).resolve().parent,
                field_id, self.candidate_path_mode,
            )
            self._add_phase_timing(
                "tail_core_checkpoint",
                checkpoint["tail_core_checkpoint_seconds"],
            )
        elif checkpoint is None:
            raise ValueError("recovery adapter preparation 缺少 TailCore checkpoint")
        self._check_cancelled()
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
        adapter_started = time.perf_counter()
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
            launch_stage="tail_editor_adapter",
        )

        self._add_phase_timing(
            "association_editor_adapter",
            time.perf_counter() - adapter_started,
        )

        # The adapter has now completed its automatic one-to-one decision, but
        # no editor/workset semantics have started.  Preserve that exact fact
        # independently; the current editor continues to consume its old files.
        association_checkpoint = write_and_verify_association_checkpoint(
            self.task_root, self.project_root, field_id, output_dir, head_labels,
            checkpoint)
        self._add_phase_timing(
            "association_checkpoint",
            association_checkpoint["association_checkpoint_seconds"],
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
            "tail_core_checkpoint": checkpoint,
            "association_checkpoint": association_checkpoint,
        }

    def _recovery_source_for_field(self, field_id):
        source = getattr(self, "tail_core_recovery_sources", {}).get(str(field_id))
        if source is None:
            return None
        if not isinstance(source, dict):
            raise ValueError("TailCore recovery source 必须是对象：{}".format(field_id))
        if str(source.get("field_id", field_id)) != str(field_id):
            raise ValueError("TailCore recovery source field_id 不匹配：{}".format(field_id))
        generation = source.get("checkpoint_generation_path")
        if not generation:
            raise ValueError(
                "TailCore recovery source 必须显式提供 checkpoint_generation_path：{}".format(field_id)
            )
        return {"generation": Path(generation)}

    def _materialize_recovered_editor_inputs(self, field_id, checkpoint, source):
        """Create current-run ephemeral adapter inputs; checkpoint remains truth."""
        started = time.perf_counter()
        generation = Path(checkpoint["generation"])
        files = dict((item["role"], item) for item in checkpoint["checkpoint_manifest"]["files"])
        probability_item = files.get(PROBABILITY_ROLE)
        if probability_item is None:
            raise FileNotFoundError("recovery checkpoint 缺少 probability payload")
        probability_source = generation / probability_item["relative_path"]
        output_dir = _c18b_instances_path(self.task_root, field_id).parent
        output_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        for name in ("06_final_tail_instances.tif", "07_extreme_fragment_filtered_labels.tif"):
            shutil.copyfile(str(generation / "labels" / name), str(output_dir / name))
        probability_target = (self.task_root / "segmentation" / "c18b_runner_contract"
                              / field_id / PROBABILITY_NAME)
        probability_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(probability_source), str(probability_target))
        return output_dir / "06_final_tail_instances.tif", time.perf_counter() - started

    def _run_c18b_workflow(self, fields, log_handle, started):
        self._log("Analysis V2：开始C18B尾部处理。")
        self._phase_timings_seconds = {}
        editor_payloads = []
        for field_id in fields:
            self.field_id = field_id
            self._check_cancelled()
            source = self._recovery_source_for_field(field_id)
            if source is None:
                core_started = time.perf_counter()
                instances_path = self._ensure_c18b_result(field_id, log_handle)
                self._add_phase_timing("tail_core", time.perf_counter() - core_started)
                payload = self._prepare_c18b_editor_payload(field_id, instances_path, log_handle)
                if payload.get("tail_core_checkpoint") is not None:
                    payload["tail_core_checkpoint"]["tail_core_mode"] = "computed"
            else:
                try:
                    validation_started = time.perf_counter()
                    checkpoint = recover_and_verify_tail_core_checkpoint(
                        self.task_root, self.project_root, field_id,
                        self.candidate_path_mode, source["generation"],
                    )
                    validation_seconds = time.perf_counter() - validation_started
                    instances_path, materialization_seconds = self._materialize_recovered_editor_inputs(
                        field_id, checkpoint, source)
                    checkpoint["tail_core_mode"] = "reused"
                    checkpoint["tail_core_recovery_validation_seconds"] = validation_seconds
                    checkpoint["tail_core_reuse_materialization_seconds"] = materialization_seconds
                    self._add_phase_timing("tail_core_recovery_validation", validation_seconds)
                    self._add_phase_timing("tail_core_reuse_materialization", materialization_seconds)
                    self._log("TailCoreResult recovered/reused: field={} C18B compute skipped".format(field_id))
                    payload = self._prepare_c18b_editor_payload(
                        field_id, instances_path, log_handle, checkpoint=checkpoint,
                        apply_filter=False)
                except Exception as error:
                    self._log("TailCoreResult recovery rejected: field={} cause={}".format(field_id, error))
                    raise
            editor_payloads.append(payload)
            self._check_cancelled()
        elapsed_seconds = float(time.perf_counter() - started)
        phase_timings = dict(self._phase_timings_seconds)
        phase_timings["c18b_orchestration_overhead"] = max(
            0.0,
            elapsed_seconds - sum(phase_timings.values()),
        )
        return {
            "success": True,
            "workflow": "c18b_tail_editor",
            "tail_backend": "C18B",
            "manual_calibration_completed": False,
            "ready_for_measurement": False,
            "task_root": str(self.task_root),
            "fields": editor_payloads,
            "elapsed_seconds": elapsed_seconds,
            "phase_timings_seconds": phase_timings,
        }
