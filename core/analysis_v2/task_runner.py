"""Synchronous automatic Analysis V2 orchestration, ending at measured.

One runner executes one task at a time. Cancellation is sticky: after cancel or
shutdown create a new runner. The caller owns the thread that invokes run().
"""

import threading
import time
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .c18b_execution import C18BExecution
from .completion import build_completion_result
from .head_calibration_service import HeadCalibrationService
from .head_input_adapter import build_head_segmentation_fields
from .head_measurement_service import HeadMeasurementService
from .segmentation_service import run_head_segmentation, _validate_field_id
from .tail_calibration_service import (
    save_initial_c18b_tail_workset, build_automatic_tail_final_contract,
    register_tail_final_contract, complete_tail_calibration,
)
from .tail_measurement_service import TailMeasurementService
from .task_paths import AnalysisTaskPaths, _sanitize_identifier
from .task_process_context import TaskProcessContext, TaskProcessCancelled
from .task_state import TaskStateStore


@dataclass(frozen=True)
class AnalysisV2TaskRequest:
    """Matched rows use field_no/R/G/Merge, or field_id/*_path service keys.

    workspace_root denotes the cases directory, as ConfigManager does.
    protein_part is head/tail; when omitted it is derived from protein_key.
    """

    case_no: str
    protein_key: str
    matched_fields: Sequence[Mapping[str, Any]]
    protein_part: Optional[str] = None
    case_id: Optional[Any] = None
    workspace_root: Optional[Path] = None
    raw_image_folder: Optional[str] = None
    candidate_path_mode: str = "graph_preserving"


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
        self._process_context = TaskProcessContext()
        self.cancel_event = self._process_context.cancel_event
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

    def cancel(self):
        self._process_context.cancel()

    def shutdown(self, timeout_seconds=10.0):
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout_seconds must be finite and nonnegative")
        deadline = time.monotonic() + timeout
        with self._lock:
            self._closed = True
        self._process_context.cancel(deadline=deadline)
        finished = self._done.wait(max(0, deadline - time.monotonic()))
        resources_finished = self._process_context.wait(deadline)
        if not finished or not resources_finished:
            error = self._error(AnalysisV2TaskError, "Task shutdown timed out")
            error.stage = "shutdown"
            raise error
        return True

    def _validate(self, request):
        if not isinstance(request, AnalysisV2TaskRequest):
            raise TypeError("Expected AnalysisV2TaskRequest")
        key = request.protein_key
        if key not in ("protein1", "protein2", "protein3", "protein4", "protein5"):
            raise ValueError("Unsupported protein_key: {}".format(key))
        part = "tail" if key == "protein3" else "head"
        if request.protein_part is not None and request.protein_part != part:
            raise ValueError("protein_part does not match protein_key")
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
            self._enter("head_segmentation")
            run_head_segmentation(
                paths=paths, paired_fields=fields,
                mvimageid_root=self.config.get_source_project_dir(),
                mvimageid_python=self.config.get_python_exe(),
                worker_path=project_root / "tools" / "analysis_v2" / "direct_cellpose_worker.py",
                timeout_seconds=600.0, case_no=request.case_no,
                protein_key=request.protein_key, process_context=self._process_context,
            )
            self._enter("head_calibration")
            HeadCalibrationService(paths.task_root, interactive=False).complete(
                process_context=self._process_context,
            )
            if part == "tail":
                self._enter("c18b")
                execution = C18BExecution(
                    project_root, paths.task_root, self.config.get_python_exe(),
                    request.candidate_path_mode, self.log_callback, self._process_context,
                )
                try:
                    prepared = execution.run()
                except Exception:
                    self._field_id = execution.field_id
                    raise
                self._enter("tail_calibration")
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
            measurement = service.run(process_context=self._process_context)
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
            if self.cancel_event.is_set() or isinstance(cause, TaskProcessCancelled):
                cancelled = self._error(AnalysisV2TaskCancelled, "Analysis V2 task cancelled", cause)
                if self._paths and self._paths.state_path.is_file():
                    try:
                        TaskStateStore.from_task_paths(self._paths).update(
                            "cancelled", self._stage, "Automatic task cancelled",
                        )
                    except Exception as state_error:
                        cancelled.state_error = state_error
                raise cancelled from cause
            raise self._error(AnalysisV2TaskError, str(cause), cause) from cause
        finally:
            with self._lock:
                self._running = False
                self._done.set()
