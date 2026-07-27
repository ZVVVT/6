"""Analysis V2 校准后头部测量服务。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List

from .environment_snapshot import EnvironmentSnapshotWriter
from .head_measurement_result import validate_head_measurement_output
from .manifest_store import ManifestStore
from .stage_logger import StageLogger
from .task_paths import AnalysisTaskPaths
from .task_state import TaskStateStore, atomic_write_json


STAGE = "head_measurement"


def _record_path(task_root: Path, record: Dict[str, Any]) -> Path:
    relative_path = str(record.get("relative_path") or "").strip()
    if relative_path:
        current_task_path = (task_root / relative_path).resolve()
        if current_task_path.is_file():
            return current_task_path
    absolute_path = str(record.get("absolute_path") or "").strip()
    if absolute_path and Path(absolute_path).is_file():
        return Path(absolute_path).resolve()
    return Path()


def _unique_source(
    task_root: Path,
    manifest_files: List[Dict[str, Any]],
    field_id: str,
    role: str,
    fallback_directory: Path,
    fallback_matcher: Any,
) -> Path:
    candidates = []
    for record in manifest_files:
        metadata = record.get("metadata") or {}
        if (
            record.get("role") == role
            and metadata.get("field_id") == field_id
        ):
            candidate = _record_path(task_root, record)
            if candidate.is_file() and candidate not in candidates:
                candidates.append(candidate)
    if not candidates:
        candidates = sorted(
            path.resolve()
            for path in fallback_directory.iterdir()
            if path.is_file() and fallback_matcher(path.name)
        )
    if len(candidates) != 1:
        raise ValueError(
            "{} 的 {} 候选数量应为 1，实际为 {}：{}".format(
                field_id,
                role,
                len(candidates),
                [str(path) for path in candidates],
            )
        )
    return candidates[0]


def collect_head_measurement_fields(
    task_root: Path,
) -> List[Dict[str, Any]]:
    """优先通过 manifest 定位各视野源文件，文件名搜索仅作回退。"""
    root = Path(task_root).resolve()
    manifest_path = root / "manifest.json"
    manifest_files = []
    if manifest_path.is_file():
        with manifest_path.open("r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
        manifest_files = list(manifest.get("files") or [])

    field_ids = sorted({
        str((record.get("metadata") or {}).get("field_id") or "")
        for record in manifest_files
        if record.get("role") == "head_final_labels"
        and (record.get("metadata") or {}).get("field_id")
    })
    calibration_head = root / "calibration" / "head"
    if not field_ids:
        suffix = "_HeadFinalLabels.tif"
        field_ids = [
            path.name[:-len(suffix)]
            for path in sorted(calibration_head.glob("*{}".format(suffix)))
        ]
    if not field_ids:
        raise ValueError("未找到已校准头部视野")

    source_input = root / "input"
    fields = []
    for field_id in field_ids:
        def channel_matcher(channel: str) -> Any:
            suffixes = (
                "_{}.tif".format(channel).lower(),
                "_{}.tiff".format(channel).lower(),
            )
            return lambda name: (
                name.lower().startswith(field_id.lower() + "_")
                and name.lower().endswith(suffixes)
            )

        labels_name = "{}_HeadFinalLabels.tif".format(field_id).lower()
        objects_name = "{}_HeadFinalObjects.json".format(field_id).lower()
        fitc = _unique_source(
            root,
            manifest_files,
            field_id,
            "fitc_input",
            source_input,
            channel_matcher("FITC"),
        )
        tritc = _unique_source(
            root,
            manifest_files,
            field_id,
            "tritc_input",
            source_input,
            channel_matcher("TRITC"),
        )
        merge = _unique_source(
            root,
            manifest_files,
            field_id,
            "merge_input",
            source_input,
            channel_matcher("Merge"),
        )
        labels = _unique_source(
            root,
            manifest_files,
            field_id,
            "head_final_labels",
            calibration_head,
            lambda name: name.lower() == labels_name,
        )
        objects = _unique_source(
            root,
            manifest_files,
            field_id,
            "head_final_objects",
            calibration_head,
            lambda name: name.lower() == objects_name,
        )
        with objects.open("r", encoding="utf-8") as objects_file:
            expected_count = int(json.load(objects_file)["object_count"])
        fields.append({
            "field_id": field_id,
            "fitc": fitc,
            "tritc": tritc,
            "merge": merge,
            "labels": labels,
            "objects": objects,
            "expected_object_count": expected_count,
        })
    return fields


def prepare_standardized_head_input(
    fields: List[Dict[str, Any]],
    input_dir: Path,
) -> Dict[str, Any]:
    """复制源文件并统一为测量管道使用的稳定名称。"""
    target_dir = Path(input_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for field in fields:
        field_id = field["field_id"]
        destinations = {
            "fitc": target_dir / "{}_G.tif".format(field_id),
            "tritc": target_dir / "{}_R.tif".format(field_id),
            "merge": target_dir / "{}_Merge.tif".format(field_id),
            "labels": target_dir / "{}_HeadFinalLabels.tif".format(field_id),
        }
        for key, destination in destinations.items():
            shutil.copy2(str(field[key]), str(destination))
        records.append({
            "field_id": field_id,
            "fitc_source_path": str(field["fitc"]),
            "tritc_source_path": str(field["tritc"]),
            "merge_source_path": str(field["merge"]),
            "final_labels_source_path": str(field["labels"]),
            "final_objects_source_path": str(field["objects"]),
            "g_path": str(destinations["fitc"]),
            "r_path": str(destinations["tritc"]),
            "merge_path": str(destinations["merge"]),
            "final_labels_path": str(destinations["labels"]),
            "expected_object_count": field["expected_object_count"],
        })

    field_ids = [field["field_id"] for field in fields]
    actual_names = sorted(
        path.name for path in target_dir.iterdir() if path.is_file()
    )
    expected_names = sorted(
        name
        for field_id in field_ids
        for name in (
            "{}_G.tif".format(field_id),
            "{}_R.tif".format(field_id),
            "{}_Merge.tif".format(field_id),
            "{}_HeadFinalLabels.tif".format(field_id),
        )
    )
    counts = {
        "G": len(list(target_dir.glob("*_G.tif"))),
        "R": len(list(target_dir.glob("*_R.tif"))),
        "Merge": len(list(target_dir.glob("*_Merge.tif"))),
        "HeadFinalLabels": len(
            list(target_dir.glob("*_HeadFinalLabels.tif"))
        ),
    }
    required_count = len(field_ids)
    required_counts = {
        "G": required_count,
        "R": required_count,
        "Merge": required_count,
        "HeadFinalLabels": required_count,
    }
    if (
        len(actual_names) != required_count * 4
        or actual_names != expected_names
        or counts != required_counts
    ):
        raise ValueError(
            "测量输入预检失败；分类数量={}；实际文件={}".format(
                counts,
                actual_names,
            )
        )
    return {
        "records": records,
        "file_names": actual_names,
        "counts": counts,
    }


def _task_paths(task_root: Path) -> AnalysisTaskPaths:
    root = Path(task_root).resolve()
    with (root / "state.json").open("r", encoding="utf-8") as state_file:
        state = json.load(state_file)
    project_root = Path(__file__).resolve().parents[2]
    return AnalysisTaskPaths._build(project_root, root, str(state["task_id"]))


class HeadMeasurementService:
    def __init__(
        self,
        task_root: Path,
        pipeline: Path,
        mvimageid_root: Path,
        python_exe: Path,
        timeout_seconds: float = 120,
        plugins_directory: Path = None,
    ) -> None:
        self.paths = _task_paths(task_root)
        self.pipeline = Path(pipeline).resolve()
        self.mvimageid_root = Path(mvimageid_root).resolve()
        self.python_exe = Path(python_exe).resolve()
        self.plugins_dir = (
            Path(plugins_directory).resolve()
            if plugins_directory is not None
            else self.mvimageid_root / "C-plugins" / "active_plugins"
        )
        self.input_dir = self.paths.measurement_dir / "head" / "input"
        self.output_dir = self.paths.measurement_dir / "head" / "output"
        self.result_path = (
            self.paths.measurement_dir
            / "head"
            / "head_measurement_result.json"
        )
        self.measurement_input_path = (
            self.paths.measurement_dir
            / "head"
            / "measurement_input.json"
        )
        self.state = TaskStateStore.from_task_paths(self.paths)
        self.manifest = ManifestStore.from_task_paths(self.paths)
        self.logger = StageLogger.from_task_paths(self.paths)
        self.timeout_seconds = float(timeout_seconds)
        from core.mvimageid_runner import MvImageIDRunner

        self.runner = MvImageIDRunner(
            source_project_dir=str(self.mvimageid_root),
            python_exe=str(self.python_exe),
            module_name="MvImageID",
            plugins_directory=str(self.plugins_dir),
            log_file="",
        )

    def _fields(self) -> List[Dict[str, Any]]:
        return collect_head_measurement_fields(self.paths.task_root)

    def _prepare_input(self, fields: List[Dict[str, Any]]) -> None:
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        prepared = prepare_standardized_head_input(fields, self.input_dir)
        atomic_write_json(
            self.measurement_input_path,
            {"schema_version": 2, "fields": prepared["records"]},
        )

    def run(self) -> Dict[str, Any]:
        try:
            current = self.state.load()
            calibrated_before = any(
                item.get("status") == "head_calibrated"
                for item in current.get("history", [])
            )
            allowed = {"head_calibrated", "head_measuring", "head_measured"}
            if (
                current["status"] not in allowed
                and not (
                    current["status"] == "failed"
                    and calibrated_before
                )
            ):
                raise ValueError(
                    "当前状态不是 head_calibrated：{}".format(
                        current["status"]
                    )
                )

            fields = self._fields()
            self._prepare_input(fields)
            self.state.update(
                "head_measuring",
                STAGE,
                "正在使用旧正式 MvImageIDRunner 测量校准后头部",
            )
            self.logger.info(
                STAGE,
                "开始一次性测量 {} 个视野".format(len(fields)),
            )
            environment = EnvironmentSnapshotWriter(
                self.paths,
                mvimageid_root=self.mvimageid_root,
                mvimageid_python=self.python_exe,
                plugins_dir=self.plugins_dir,
                pipeline_path=self.pipeline,
                input_dir=self.input_dir,
                output_dir=self.output_dir,
            )
            environment.write()

            run_result = self.runner.run(
                pipeline_file=str(self.pipeline),
                input_dir=str(self.input_dir),
                output_dir=str(self.output_dir),
                log_callback=lambda message: self.logger.info(
                    STAGE,
                    str(message),
                ),
                cancel_callback=None,
                log_file="",
            )
            run_payload = {
                "command": run_result.command,
                "return_code": run_result.return_code,
                "duration_seconds": run_result.elapsed_seconds,
                "command_log_path": str(run_result.command_file or ""),
                "stdout_path": str(run_result.log_file or ""),
                "stderr_path": "",
                "success": run_result.success,
                "runner_class": "core.mvimageid_runner.MvImageIDRunner",
            }
            self.logger.event(
                "mvimageid_head_measurement",
                STAGE,
                "succeeded" if run_result.success else "failed",
                duration_seconds=run_result.elapsed_seconds,
                return_code=run_result.return_code,
                extra=run_payload,
            )
            if not run_result.success:
                raise RuntimeError(
                    run_result.error_message
                    or "MvImageID 测量失败，退出码 {}".format(
                        run_result.return_code
                    )
                )

            field_ids = [item["field_id"] for item in fields]
            validation = validate_head_measurement_output(
                output_dir=self.output_dir,
                field_ids=field_ids,
                expected_object_counts={
                    item["field_id"]: item["expected_object_count"]
                    for item in fields
                },
                validation_path=self.result_path,
            )
            parsed_result = validation["result_parser"]["image_summary"]
            object_result = validation["result_parser"]["object_summary"]

            registrations = [
                (
                    self.measurement_input_path,
                    "head_measurement_input",
                    "application/json",
                ),
                (
                    Path(validation["image_csv_path"]),
                    "head_measurement_image_csv",
                    "text/csv",
                ),
                (
                    Path(validation["g_colocalized_csv_path"]),
                    "head_measurement_object_csv",
                    "text/csv",
                ),
                (
                    self.result_path,
                    "head_measurement_result",
                    "application/json",
                ),
                (
                    environment.environment_path,
                    "environment",
                    "application/json",
                ),
            ]
            for overlay_path in validation["overlay_paths"]:
                registrations.append((
                    Path(overlay_path),
                    "head_measurement_overlay",
                    "image/png",
                ))
            if run_result.command_file is not None:
                registrations.append((
                    run_result.command_file,
                    "head_measurement_command",
                    "text/plain",
                ))
            if run_result.log_file is not None:
                registrations.append((
                    run_result.log_file,
                    "head_measurement_stdout",
                    "text/plain",
                ))
            for path, role, media_type in registrations:
                self.manifest.add_file(path, role, STAGE, media_type)

            final_state = self.state.update(
                "head_measured",
                STAGE,
                "校准后头部测量完成",
            )
            self.logger.info(STAGE, "测量完成")
            return {
                "run": run_payload,
                "validation": validation,
                "parsed_result": parsed_result,
                "object_result": object_result,
                "state": final_state,
                "result": validation,
            }
        except BaseException as exception:
            self.logger.record_exception(STAGE, exception)
            self.state.mark_failed(
                STAGE,
                exception,
                "校准后头部测量失败",
            )
            raise
