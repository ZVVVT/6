"""Synchronous automatic Analysis V2 orchestration, ending at measured.

One runner executes one task at a time. Cancellation is sticky: after cancel or
shutdown create a new runner. The caller owns the thread that invokes run().
"""

import threading
import time
import math
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .c18b_execution import C18BExecution
from .completion import build_completion_result
from .head_calibration_service import HeadCalibrationService
from .head_input_adapter import build_head_segmentation_fields
from .input_manifest_checkpoint import write_and_verify_input_manifest_checkpoint
from .head_measurement_service import HeadMeasurementService
from .segmentation_service import (
    prepare_common_task_input, run_head_segmentation, _validate_field_id,
)
from .tail_calibration_service import (
    save_initial_c18b_tail_workset, build_automatic_tail_final_contract,
    register_tail_final_contract, complete_tail_calibration,
)
from .tail_measurement_service import TailMeasurementService
from .task_paths import AnalysisTaskPaths, _sanitize_identifier
from .task_process_context import TaskProcessContext, TaskProcessCancelled
from .task_supervisor import TaskSupervisor
from .stage_logger import StageLogger
from .task_state import TaskStateStore


@dataclass(frozen=True)
class AnalysisV2TaskRequest:
    """Matched rows use field_no/R/G/Merge, or field_id/*_path service keys.

    workspace_root denotes the cases directory, as ConfigManager does.
    protein_part is the frozen head/tail value read for this request.
    """

    case_no: str
    protein_key: str
    matched_fields: Sequence[Mapping[str, Any]]
    protein_part: Optional[str] = None
    case_id: Optional[Any] = None
    workspace_root: Optional[Path] = None
    raw_image_folder: Optional[str] = None
    candidate_path_mode: str = "graph_preserving"
    # Explicit-only Phase 3C recovery inputs.  Keys are field IDs; values carry
    # only an immutable TailCore checkpoint generation identity/path.
    tail_core_recovery_sources: Optional[Mapping[str, Mapping[str, Any]]] = None


class AnalysisV2TaskError(Exception):
    def __init__(self, message, *, stage, case_no=None, protein_key=None,
                 task_root=None, field_id=None, cause=None, return_code=None,
                 log_path=None):
        super().__init__(message)
        self.message = message
        self.stage = stage
        self.case_no = case_no
        self.protein_key = protein_key
        self.task_root = str(task_root) if task_root is not None else None
        self.field_id = field_id
        self.cause = cause
        self.return_code = return_code
        self.log_path = str(log_path) if log_path is not None else None


class AnalysisV2TaskCancelled(AnalysisV2TaskError):
    """Explicit task cancellation; callers must handle before TaskError."""


class AnalysisV2TaskRunner:
    def __init__(self, config, log_callback=None):
        self.config = config
        self.log_callback = log_callback
        self._supervisor = TaskSupervisor(TaskProcessContext())
        self._process_context = self._supervisor.process_context
        self.cancel_event = self._supervisor.cancel_event
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._done.set()
        self._running = False
        self._closed = False
        self._request = None
        self._paths = None
        self._stage = "validation"
        self._field_id = None

    def _error(self, cls, message, cause=None):
        return cls(
            message, stage=self._stage,
            case_no=getattr(self._request, "case_no", None),
            protein_key=getattr(self._request, "protein_key", None),
            task_root=self._paths.task_root if self._paths else None,
            field_id=getattr(cause, "field_id", None) or self._field_id,
            cause=cause, return_code=getattr(cause, "return_code", None),
            log_path=getattr(cause, "log_path", None),
        )

    def _enter(self, stage):
        self._process_context.check_cancelled()
        self._stage = stage
        self._field_id = None
        if self.log_callback is not None:
            self.log_callback("Analysis V2: {}".format(stage))
        self._process_context.check_cancelled()

    def _flush_branch_logs(self, messages):
        """Invoke an application callback only on the caller's runner thread.

        The public callback can be a Qt bridge, but C18B runs in a pool worker
        for tail tasks.  Buffering avoids calling an arbitrary GUI callback
        from that worker while retaining its log lines after the JOIN.
        """
        if self.log_callback is not None:
            for message in messages:
                self.log_callback(message)

    def _run_tail_parallel_branches(self, paths, fields, project_root, request,
                                    common_prepared):
        """Run Head and the strictly Head-independent C18B backend together."""
        branch_logs = []
        branch_log_lock = threading.Lock()
        first_failure = {"error": None, "stage": None, "field_id": None}
        first_failure_lock = threading.Lock()

        def buffered_log(message):
            with branch_log_lock:
                branch_logs.append(message)

        execution = C18BExecution(
            project_root, paths.task_root, self.config.get_python_exe(),
            request.candidate_path_mode, buffered_log, self._process_context,
            request.tail_core_recovery_sources,
        )

        def run_head_branch():
            started = time.perf_counter()
            run_head_segmentation(
                paths=paths, paired_fields=fields,
                mvimageid_root=self.config.get_source_project_dir(),
                mvimageid_python=self.config.get_python_exe(),
                worker_path=project_root / "tools" / "analysis_v2" / "direct_cellpose_worker.py",
                timeout_seconds=600.0, case_no=request.case_no,
                protein_key=request.protein_key, process_context=self._process_context,
                prepared_input=common_prepared,
            )
            self._process_context.check_cancelled()
            HeadCalibrationService(paths.task_root, interactive=False).complete(
                process_context=self._process_context,
            )
            return time.perf_counter() - started

        def run_branch(stage, callable_):
            try:
                return callable_()
            except BaseException as error:
                # The worker that observes the business exception linearizes it
                # immediately.  Secondary cancellation/termination exceptions
                # cannot replace this root failure in TaskSupervisor.
                if not self.cancel_event.is_set() and not isinstance(error, TaskProcessCancelled):
                    with first_failure_lock:
                        if first_failure["error"] is None:
                            field_id = getattr(error, "field_id", None) or execution.field_id
                            first_failure.update(error=error, stage=stage, field_id=field_id)
                            self._supervisor.record_failure(
                                stage, field_id, error,
                                details={"case_no": request.case_no,
                                         "protein_key": request.protein_key},
                            )
                            self._supervisor.request_cancel(
                                "parallel failure at {}".format(stage),
                                failure_triggered=True,
                            )
                raise

        parallel_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="analysis-v2-p2c") as executor:
            futures = {
                executor.submit(run_branch, "head_segmentation", run_head_branch): "head_segmentation",
                executor.submit(run_branch, "c18b", execution.run_backend): "c18b",
            }
            pending = set(futures)
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    stage = futures[future]
                    try:
                        future.result()
                    except BaseException:
                        pass
                # Do not leave a successfully completed future unobserved, and
                # always reap a running sibling after cancellation.
            # All futures have completed before executor shutdown / context.finish.
            if first_failure["error"] is not None:
                self._stage = first_failure["stage"]
                self._field_id = first_failure["field_id"]
                raise first_failure["error"]
            self._process_context.check_cancelled()
            head_future = next(item for item, stage in futures.items()
                               if stage == "head_segmentation")
            backend_future = next(item for item, stage in futures.items() if stage == "c18b")
            head_seconds = float(head_future.result())
            backend = backend_future.result()
        parallel_window_seconds = time.perf_counter() - parallel_started
        self._flush_branch_logs(branch_logs)
        return execution, backend, head_seconds, parallel_window_seconds

    def cancel(self):
        self._supervisor.request_cancel("user requested cancellation")

    def shutdown(self, timeout_seconds=10.0):
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout_seconds must be finite and nonnegative")
        deadline = time.monotonic() + timeout
        with self._lock:
            self._closed = True
        self._supervisor.request_cancel("runner shutdown", deadline=deadline)
        # Reap owned children before waiting for the worker.  A worker can be
        # blocked in its subprocess wait and cannot set _done until its child
        # has exited; waiting for _done first therefore consumed the complete
        # shutdown budget without giving the process context a chance to reap.
        resources_finished = self._process_context.wait(deadline)
        finished = self._done.wait(max(0, deadline - time.monotonic()))
        # A process can be registered while cancellation is already sticky.
        # The register contract terminates it immediately, but verify/reap it
        # after the worker exits as well, using the same absolute deadline.
        resources_finished = self._process_context.wait(deadline) and resources_finished
        if not finished or not resources_finished:
            remaining = self._process_context.live_processes()
            message = "Task shutdown timed out: remaining process count={}, stage={}, waiting_phase={}, processes={}".format(
                len(remaining), self._stage,
                "process_reap" if remaining else "worker_done", remaining,
            )
            error = self._error(AnalysisV2TaskError, message)
            error.stage = "shutdown"
            raise error
        return True

    def _validate(self, request):
        if not isinstance(request, AnalysisV2TaskRequest):
            raise TypeError("Expected AnalysisV2TaskRequest")
        key = request.protein_key
        if key not in ("protein1", "protein2", "protein3", "protein4", "protein5"):
            raise ValueError("Unsupported protein_key: {}".format(key))
        part = str(request.protein_part or "").strip().lower()
        if part not in ("head", "tail"):
            raise ValueError("protein_part must be head or tail")
        if part == "tail" and key != "protein3":
            raise ValueError("tail is supported only for protein3")
        if _sanitize_identifier(request.case_no, "case_no") != request.case_no:
            raise ValueError("case_no must be a safe, nonempty directory name")
        if request.candidate_path_mode not in ("graph_preserving", "ordered"):
            raise ValueError("Unsupported candidate_path_mode")
        rows = []
        for field in request.matched_fields:
            row = dict(field)
            if "field_id" in row:
                row = {"field_no": row["field_id"], "R": row.get("tritc_path"),
                       "G": row.get("fitc_path"), "Merge": row.get("merge_path")}
            self._field_id = str(row.get("field_no") or "")
            _validate_field_id(self._field_id)
            if part == "tail" and not row.get("Merge"):
                raise ValueError("protein3 requires G/R/Merge: {}".format(self._field_id))
            rows.append(row)
        return part, build_head_segmentation_fields(rows)

    def run(self, request):
        with self._lock:
            if self._running:
                raise AnalysisV2TaskError("Runner is already running", stage="validation")
            if self._closed:
                raise AnalysisV2TaskCancelled("Runner is shut down", stage="validation")
            if self.cancel_event.is_set():
                raise AnalysisV2TaskCancelled("Runner is cancelled", stage="validation")
            if not self._supervisor.register_worker("analysis-v2-task-runner"):
                raise AnalysisV2TaskCancelled("Runner is cancelled", stage="validation")
            self._running = True
            self._done.clear()
            self._request = request
            self._paths = None
            self._stage = "validation"
            self._field_id = None
        started = time.perf_counter()
        try:
            self._enter("validation")
            part, fields = self._validate(request)
            project_root = Path(self.config.app_root).resolve()
            workspace = Path(request.workspace_root if request.workspace_root is not None
                             else self.config.get_workspace_root())
            if not workspace.is_absolute():
                workspace = project_root / workspace
            self._paths = AnalysisTaskPaths.for_case(
                project_root, request.case_no, request.protein_key,
                workspace_root=workspace,
            )
            paths = self._paths
            self._enter("common_preparation")
            common_preparation_started = time.perf_counter()
            common_prepared = prepare_common_task_input(
                paths=paths,
                paired_fields=fields,
                case_no=request.case_no,
                protein_key=request.protein_key,
                process_context=self._process_context,
            )
            common_preparation_seconds = time.perf_counter() - common_preparation_started
            if part == "tail":
                performance_logger = StageLogger.from_task_paths(
                    paths,
                    case_no=request.case_no,
                    protein_key=request.protein_key,
                )
                performance_started = time.perf_counter()
                performance_logger.event(
                    "tail_v3_timing_started",
                    "performance",
                    "running",
                    extra={
                        "clock": "time.perf_counter",
                        "boundary": "prepared_request_to_tail_measured",
                        "interactive": False,
                    },
                )
                self._enter("input_manifest")
                input_checkpoints = []
                for field in fields:
                    self._field_id = field["field_id"]
                    checkpoint = write_and_verify_input_manifest_checkpoint(
                        paths.task_root, field, request.protein_key,
                    )
                    input_checkpoints.append(checkpoint)
                    performance_logger.event(
                        "input_manifest_checkpoint",
                        "input_manifest",
                        "succeeded",
                        duration_seconds=checkpoint["input_checkpoint_seconds"],
                        extra={
                            "field_id": checkpoint["field_id"],
                            "generation": checkpoint["generation"],
                            "hash_seconds": checkpoint["hash_seconds"],
                            "write_seconds": checkpoint["write_seconds"],
                            "validate_seconds": checkpoint["validate_seconds"],
                            "input_checkpoint_seconds": checkpoint["input_checkpoint_seconds"],
                        },
                    )
                input_checkpoint_seconds = sum(
                    item["input_checkpoint_seconds"] for item in input_checkpoints
                )
            else:
                input_checkpoints = []
                input_checkpoint_seconds = 0.0
            if part == "tail":
                self._enter("parallel_head_c18b")
                # Preserve the established C18B cancellation boundary while
                # the following call owns the actual two-branch window.
                self._enter("c18b")
                execution, backend, head_seconds, parallel_window_seconds = (
                    self._run_tail_parallel_branches(
                        paths, fields, project_root, request, common_prepared,
                    )
                )
                c18b_backend_seconds = float(backend.get("elapsed_seconds", 0.0))
                self._enter("c18b_head_dependent_finalize")
                finalize_with_head_started = time.perf_counter()
                try:
                    prepared = execution.finalize_with_head(backend)
                except Exception:
                    self._field_id = execution.field_id
                    raise
                finalize_with_head_seconds = time.perf_counter() - finalize_with_head_started
                c18b_seconds = float(prepared.get("elapsed_seconds", 0.0))
                c18b_phases = dict(prepared.get("phase_timings_seconds") or {})
                tail_core_checkpoints = [
                    field.get("tail_core_checkpoint") for field in prepared["fields"]
                    if field.get("tail_core_checkpoint") is not None
                ]
                for checkpoint in tail_core_checkpoints:
                    performance_logger.event(
                        "tail_core_checkpoint",
                        "tail_core",
                        "succeeded",
                        duration_seconds=checkpoint["tail_core_checkpoint_seconds"],
                        extra={
                            "field_id": checkpoint["field_id"],
                            "generation": checkpoint["generation"],
                            "tail_core_checkpoint_seconds": checkpoint[
                                "tail_core_checkpoint_seconds"
                            ],
                            "tail_core_mode": checkpoint.get("tail_core_mode", "computed"),
                            "tail_core_recovery_validation_seconds": checkpoint.get(
                                "tail_core_recovery_validation_seconds", 0.0),
                            "tail_core_reuse_materialization_seconds": checkpoint.get(
                                "tail_core_reuse_materialization_seconds", 0.0),
                            "payload_bytes": checkpoint["payload_bytes"],
                        },
                    )
                association_checkpoints = [
                    field.get("association_checkpoint") for field in prepared["fields"]
                    if field.get("association_checkpoint") is not None
                ]
                for checkpoint in association_checkpoints:
                    performance_logger.event(
                        "association_checkpoint",
                        "association",
                        "succeeded",
                        duration_seconds=checkpoint["association_checkpoint_seconds"],
                        extra={
                            "field_id": checkpoint["field_id"],
                            "generation": checkpoint["generation"],
                            "association_checkpoint_seconds": checkpoint[
                                "association_checkpoint_seconds"
                            ],
                            "payload_bytes": checkpoint["payload_bytes"],
                        },
                    )
                revision_checkpoints = [
                    field.get("tail_objects_revision_checkpoint") for field in prepared["fields"]
                    if field.get("tail_objects_revision_checkpoint") is not None
                ]
                for checkpoint in revision_checkpoints:
                    performance_logger.event(
                        "tail_objects_revision_checkpoint", "tail_objects_revision", "succeeded",
                        duration_seconds=checkpoint["tail_objects_revision_checkpoint_seconds"],
                        extra={
                            "field_id": checkpoint["field_id"],
                            "generation": checkpoint["generation"],
                            "revision_id": checkpoint["tail_objects_revision"]["revision_id"],
                            "tail_objects_revision_checkpoint_seconds": checkpoint[
                                "tail_objects_revision_checkpoint_seconds"],
                            "payload_bytes": checkpoint["payload_bytes"],
                        },
                    )
                self._enter("tail_calibration")
                finalizer_started = time.perf_counter()
                results = []
                for field in prepared["fields"]:
                    self._process_context.check_cancelled()
                    payload = dict(field, task_root=str(paths.task_root))
                    self._field_id = payload["field_id"]
                    output_dir = Path(payload["output_dir"])
                    head_labels = Path(payload["head_labels"])
                    save_initial_c18b_tail_workset(
                        Path(payload["fragments"]).parent, head_labels, output_dir,
                    )
                    self._process_context.check_cancelled()
                    contract = build_automatic_tail_final_contract(
                        payload["field_id"], output_dir, head_labels,
                    )
                    self._process_context.check_cancelled()
                    results.append(register_tail_final_contract(payload, contract))
                self._process_context.check_cancelled()
                complete_tail_calibration(paths.task_root, results, automatic=True)
                finalizer_seconds = time.perf_counter() - finalizer_started
            else:
                head_started = time.perf_counter()
                self._enter("head_segmentation")
                run_head_segmentation(
                    paths=paths, paired_fields=fields,
                    mvimageid_root=self.config.get_source_project_dir(),
                    mvimageid_python=self.config.get_python_exe(),
                    worker_path=project_root / "tools" / "analysis_v2" / "direct_cellpose_worker.py",
                    timeout_seconds=600.0, case_no=request.case_no,
                    protein_key=request.protein_key, process_context=self._process_context,
                    prepared_input=common_prepared,
                )
                self._enter("head_calibration")
                HeadCalibrationService(paths.task_root, interactive=False).complete(
                    process_context=self._process_context,
                )
                head_seconds = time.perf_counter() - head_started
            self._enter("{}_measurement".format(part))
            service_class = TailMeasurementService if part == "tail" else HeadMeasurementService
            service = service_class(
                task_root=paths.task_root,
                pipeline=project_root / "pipelines" / "analysis_v2" / (
                    "measure_{}_from_labels.cppipe".format(part)),
                mvimageid_root=self.config.get_source_project_dir(),
                python_exe=self.config.get_python_exe(),
                plugins_directory=self.config.get_plugins_directory(), timeout_seconds=900.0,
            )
            measurement_started = time.perf_counter()
            measurement = service.run(process_context=self._process_context)
            measurement_seconds = time.perf_counter() - measurement_started
            measured_at = time.perf_counter()
            if part == "tail":
                measured_seconds = measured_at - performance_started
                accounted_seconds = (
                    input_checkpoint_seconds + head_seconds + c18b_seconds
                    + finalizer_seconds + measurement_seconds
                )
                performance_logger.event(
                    "tail_v3_timing_finished",
                    "performance",
                    "succeeded",
                    duration_seconds=measured_seconds,
                    extra={
                        "clock": "time.perf_counter",
                        "boundary": "prepared_request_to_tail_measured",
                        "interactive": False,
                        "machine_wall_seconds": measured_seconds,
                        "common_preparation_seconds": common_preparation_seconds,
                        "head_branch_seconds": head_seconds,
                        "c18b_backend_branch_seconds": c18b_backend_seconds,
                        "parallel_window_seconds": parallel_window_seconds,
                        "parallel_join_overhead_seconds": max(
                            0.0, parallel_window_seconds - max(
                                head_seconds, c18b_backend_seconds,
                            ),
                        ),
                        "parallel_overlap_saved_seconds": max(
                            0.0, head_seconds + c18b_backend_seconds
                            - parallel_window_seconds,
                        ),
                        "finalize_with_head_seconds": finalize_with_head_seconds,
                        "input_checkpoint_seconds": input_checkpoint_seconds,
                        "input_checkpoints": [{
                            "field_id": item["field_id"],
                            "input_checkpoint_seconds": item["input_checkpoint_seconds"],
                        } for item in input_checkpoints],
                        "tail_core_checkpoints": [{
                            "field_id": item["field_id"],
                            "tail_core_checkpoint_seconds": item[
                                "tail_core_checkpoint_seconds"
                            ],
                            "tail_core_mode": item.get("tail_core_mode", "computed"),
                            "tail_core_recovery_validation_seconds": item.get(
                                "tail_core_recovery_validation_seconds", 0.0),
                            "tail_core_reuse_materialization_seconds": item.get(
                                "tail_core_reuse_materialization_seconds", 0.0),
                            "payload_bytes": item["payload_bytes"],
                        } for item in tail_core_checkpoints],
                        "association_checkpoints": [{
                            "field_id": item["field_id"],
                            "association_checkpoint_seconds": item[
                                "association_checkpoint_seconds"
                            ],
                            "payload_bytes": item["payload_bytes"],
                        } for item in association_checkpoints],
                        "tail_objects_revision_checkpoints": [{
                            "field_id": item["field_id"],
                            "revision_id": item["tail_objects_revision"]["revision_id"],
                            "tail_objects_revision_checkpoint_seconds": item[
                                "tail_objects_revision_checkpoint_seconds"],
                            "payload_bytes": item["payload_bytes"],
                        } for item in revision_checkpoints],
                        "human_wait_seconds": 0.0,
                        "stages_seconds": {
                            "input_checkpoint": input_checkpoint_seconds,
                            "head": head_seconds,
                            "tail_core": c18b_phases.get("tail_core", 0.0),
                            "fragment_filter": c18b_phases.get("fragment_filter", 0.0),
                            "tail_core_checkpoint": c18b_phases.get(
                                "tail_core_checkpoint", 0.0
                            ),
                            "tail_core_recovery_validation": c18b_phases.get(
                                "tail_core_recovery_validation", 0.0
                            ),
                            "tail_core_reuse_materialization": c18b_phases.get(
                                "tail_core_reuse_materialization", 0.0
                            ),
                            "association_editor_adapter": c18b_phases.get(
                                "association_editor_adapter", 0.0
                            ),
                            "association_checkpoint": c18b_phases.get(
                                "association_checkpoint", 0.0
                            ),
                            "tail_objects_revision_checkpoint": c18b_phases.get(
                                "tail_objects_revision_checkpoint", 0.0
                            ),
                            "c18b_orchestration_overhead": c18b_phases.get(
                                "c18b_orchestration_overhead", 0.0
                            ),
                            "finalizer": finalizer_seconds,
                            "measurement": measurement_seconds,
                            "checkpoint_overhead": max(
                                0.0,
                                measured_seconds - accounted_seconds,
                            ),
                            "publisher_db": 0.0,
                        },
                        "publisher_db_included": False,
                    },
                )
            self._enter("completion")
            payload = {
                "task_root": str(paths.task_root), "measurement_result": measurement,
                "measurement_result_path": str(service.result_path),
                "candidate_output_dir" if part == "tail" else "measurement_output_dir": str(service.output_dir),
            }
            if part == "tail":
                payload["measurement_manifest_path"] = str(service.measurement_manifest_path)
            context = {
                "case_no": request.case_no, "case_id": request.case_id,
                "protein_key": request.protein_key,
                "protein_name": self.config.get_protein_display_name(request.protein_key),
                "protein_part": part, "interactive": False, "field_count": len(fields),
                "workflow": "protein3_tail" if part == "tail" else "head",
                "project_root": str(project_root),
                "raw_image_folder": request.raw_image_folder or "",
                "target_output_dir": str(workspace / request.case_no / "cp_output" / request.protein_key),
            }
            completion = build_completion_result(
                part, payload, context, paths.task_root, time.perf_counter() - started,
            )
            self._process_context.check_cancelled()
            return completion
        except Exception as cause:
            if ((self.cancel_event.is_set() or isinstance(cause, TaskProcessCancelled))
                    and self._supervisor.root_failure is None):
                cancelled = self._error(AnalysisV2TaskCancelled, "Analysis V2 task cancelled", cause)
                if self._paths and self._paths.state_path.is_file():
                    try:
                        TaskStateStore.from_task_paths(self._paths).update(
                            "cancelled", self._stage, "Automatic task cancelled",
                        )
                    except Exception as state_error:
                        cancelled.state_error = state_error
                raise cancelled from cause
            root_failure = self._supervisor.record_failure(
                self._stage, getattr(cause, "field_id", None) or self._field_id, cause,
                details={"case_no": getattr(self._request, "case_no", None),
                         "protein_key": getattr(self._request, "protein_key", None)},
            )
            self._supervisor.request_cancel(
                "failure at {}".format(root_failure.stage), failure_triggered=True,
            )
            raise self._error(AnalysisV2TaskError, str(cause), cause) from cause
        finally:
            self._supervisor.mark_worker_done("analysis-v2-task-runner")
            self._supervisor.finalize()
            with self._lock:
                self._running = False
                self._done.set()
