"""Analysis V2 直接 Cellpose 头部识别服务。"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .direct_cellpose_runner import DirectCellposeRunner
from .environment_snapshot import EnvironmentSnapshotWriter
from .manifest_store import ManifestStore
from .stage_logger import StageLogger
from .task_paths import AnalysisTaskPaths
from .task_state import TaskStateStore, atomic_write_json


BASELINE_PARAMETERS = {
    "model": "cpsam",
    "channels": [0, 0],
    "diameter": None,
    "flow_threshold": None,
    "cellprob_threshold": None,
    "normalize": False,
    "do_3D": False,
    "min_area": 20.0,
    "max_area": 5000.0,
    "min_circularity": 0.2,
    "remove_edge_masks": False,
    "max_equivalent_diameter": None,
    "overlay_mode": "gray",
    "outline_color": "00ffff",
    "input_normalization": "percentile_1_99_to_float32_0_1",
}


def _validate_field_id(field_id: str) -> str:
    value = str(field_id).strip()
    if not value or not re.match(r"^[\w.-]+$", value, flags=re.UNICODE):
        raise ValueError("field_id 包含不安全字符：{}".format(field_id))
    if value in (".", "..") or Path(value).name != value:
        raise ValueError("field_id 不是安全文件名：{}".format(field_id))
    return value


def _copy_fields(
    paired_fields: Sequence[Dict[str, Any]],
    paths: AnalysisTaskPaths,
) -> List[Dict[str, str]]:
    if not paired_fields:
        raise ValueError("至少需要一个配对视野")
    copied = []
    seen = set()
    for source in paired_fields:
        field_id = _validate_field_id(source["field_id"])
        if field_id.lower() in seen:
            raise ValueError("field_id 重复：{}".format(field_id))
        seen.add(field_id.lower())
        item = {"field_id": field_id}
        for channel in ("tritc", "fitc", "merge"):
            source_path = Path(source["{}_path".format(channel)]).resolve()
            if not source_path.is_file():
                raise FileNotFoundError("{} 文件不存在：{}".format(channel, source_path))
            destination = paths.input_dir / "{}_{}{}".format(
                field_id,
                channel.upper() if channel != "merge" else "Merge",
                source_path.suffix.lower(),
            )
            shutil.copy2(str(source_path), str(destination))
            item["{}_path".format(channel)] = str(destination.resolve())
        copied.append(item)
    return copied


def _build_worker_input(
    copied_fields: Sequence[Dict[str, str]],
    paths: AnalysisTaskPaths,
) -> Dict[str, Any]:
    worker_result_path = paths.task_root / "worker_result.json"
    fields = []
    for copied in copied_fields:
        field_id = copied["field_id"]
        fields.append({
            "field_id": field_id,
            "tritc_path": copied["tritc_path"],
            "fitc_path": copied["fitc_path"],
            "merge_path": copied["merge_path"],
            "labels_output_path": str(
                (paths.segmentation_head_dir / "{}_HeadInitialLabels.tif".format(field_id)).resolve()
            ),
            "overlay_output_path": str(
                (paths.segmentation_head_dir / "{}_HeadInitialOverlay.png".format(field_id)).resolve()
            ),
            "objects_output_path": str(
                (paths.segmentation_head_dir / "{}_HeadInitialObjects.json".format(field_id)).resolve()
            ),
        })
    return {
        "schema_version": "analysis_v2_direct_cellpose_input_v1",
        "task_id": paths.run_id,
        "gpu": True,
        "parameters": dict(BASELINE_PARAMETERS),
        "worker_result_path": str(worker_result_path.resolve()),
        "fields": fields,
    }


def validate_worker_field(field: Dict[str, Any]) -> Dict[str, Any]:
    if field.get("error") is not None:
        raise ValueError("视野 {} worker 失败：{}".format(field.get("field_id"), field["error"]))
    required_files = (
        "labels_output_path",
        "overlay_output_path",
        "objects_output_path",
    )
    for key in required_files:
        path = Path(field[key])
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError("视野 {} 缺少有效输出：{}".format(field.get("field_id"), key))
    if field.get("labels_dtype") != "uint16":
        raise ValueError("视野 {} 标签 dtype 不是 uint16".format(field.get("field_id")))
    source_shape = field.get("source_shape")
    labels_shape = field.get("labels_shape")
    if not isinstance(source_shape, list) or labels_shape != source_shape[:2]:
        raise ValueError("视野 {} 标签尺寸与 TRITC 不一致".format(field.get("field_id")))
    object_count = int(field.get("object_count", 0))
    if object_count <= 0:
        raise ValueError("视野 {} 对象数不大于 0".format(field.get("field_id")))
    if int(field.get("minimum_label", -1)) != 0:
        raise ValueError("视野 {} 背景不是 0".format(field.get("field_id")))
    if bool(field.get("is_binary")):
        raise ValueError("视野 {} 标签是普通二值图".format(field.get("field_id")))
    if int(field.get("maximum_label", -1)) != object_count:
        raise ValueError("视野 {} max_label 与 object_count 不一致".format(field.get("field_id")))
    if int(field.get("positive_unique_count", -1)) != object_count:
        raise ValueError("视野 {} 正标签数与 object_count 不一致".format(field.get("field_id")))
    with Path(field["objects_output_path"]).open("r", encoding="utf-8") as handle:
        objects_payload = json.load(handle)
    if len(objects_payload.get("objects", [])) != object_count:
        raise ValueError("视野 {} JSON 对象数量不一致".format(field.get("field_id")))
    return field


def run_head_segmentation(
    paths: AnalysisTaskPaths,
    paired_fields: Sequence[Dict[str, Any]],
    mvimageid_root: Path,
    mvimageid_python: Path,
    worker_path: Path,
    timeout_seconds: float = 120.0,
    case_no: Optional[str] = None,
    protein_key: Optional[str] = None,
) -> Dict[str, Any]:
    """复制输入并在一个 worker 进程中完成一个批次的全部 TRITC。"""
    paths.create_directories()
    logger = StageLogger.from_task_paths(paths, case_no=case_no, protein_key=protein_key)
    state = TaskStateStore.from_task_paths(paths)
    manifest = ManifestStore.from_task_paths(paths)
    try:
        copied_fields = _copy_fields(paired_fields, paths)
        state.initialize(case_no=case_no, protein_key=protein_key)
        manifest.initialize(case_no=case_no, protein_key=protein_key)
        state.update("input_ready", "head_segmentation", "配对视野已复制到任务 input")
        for item in copied_fields:
            for channel in ("tritc", "fitc", "merge"):
                manifest.add_file(
                    Path(item["{}_path".format(channel)]),
                    role="{}_input".format(channel),
                    stage="head_segmentation",
                    metadata={"field_id": item["field_id"]},
                )

        environment_writer = EnvironmentSnapshotWriter(
            paths=paths,
            mvimageid_root=Path(mvimageid_root),
            mvimageid_python=Path(mvimageid_python),
            worker_path=Path(worker_path),
            input_dir=paths.input_dir,
            output_dir=paths.segmentation_head_dir,
        )
        environment_writer.write()
        worker_input = _build_worker_input(copied_fields, paths)
        worker_input_path = paths.task_root / "worker_input.json"
        worker_result_path = paths.task_root / "worker_result.json"
        atomic_write_json(worker_input_path, worker_input)
        logger.info("head_segmentation", "开始直接 Cellpose 批量头部识别")
        logger.event("stage_started", "head_segmentation", "running", message="直接 Cellpose worker 启动")
        state.update("head_segmenting", "head_segmentation", "正在使用直接 Cellpose 识别头部")

        runner = DirectCellposeRunner(
            python_path=Path(mvimageid_python),
            worker_path=Path(worker_path),
            project_root=paths.project_root,
            timeout_seconds=timeout_seconds,
        )
        run_result = runner.run(
            input_json_path=worker_input_path,
            logs_dir=paths.logs_dir,
            worker_result_path=worker_result_path,
        )
        if not run_result.success:
            raise RuntimeError(
                "直接 Cellpose worker 失败，return_code={}".format(run_result.return_code)
            )
        with worker_result_path.open("r", encoding="utf-8") as handle:
            worker_result = json.load(handle)
        if not worker_result.get("success"):
            raise ValueError("worker_result.json 未标记成功")
        if int(worker_result.get("field_count", -1)) != len(copied_fields):
            raise ValueError("worker_result 视野数量不一致")
        validated_fields = [validate_worker_field(field) for field in worker_result["fields"]]

        for field in validated_fields:
            for key, role, media_type in (
                ("labels_output_path", "head_initial_labels", "image/tiff"),
                ("overlay_output_path", "head_initial_overlay", "image/png"),
                ("objects_output_path", "head_initial_objects", "application/json"),
            ):
                manifest.add_file(
                    Path(field[key]),
                    role=role,
                    stage="head_segmentation",
                    media_type=media_type,
                    metadata={
                        "field_id": field["field_id"],
                        "object_count": field["object_count"],
                    },
                )
        manifest.add_file(worker_input_path, "worker_input", "head_segmentation", "application/json")
        manifest.add_file(worker_result_path, "worker_result", "head_segmentation", "application/json")
        manifest.add_file(environment_writer.environment_path, "environment", "head_segmentation", "application/json")
        manifest.add_file(Path(run_result.command_log_path), "command_log", "head_segmentation", "text/plain")
        manifest.add_file(Path(run_result.stdout_path), "stdout_log", "head_segmentation", "text/plain")
        manifest.add_file(Path(run_result.stderr_path), "stderr_log", "head_segmentation", "text/plain")

        state.update("head_segmented", "head_segmentation", "全部视野头部标签已生成并验证")
        logger.info("head_segmentation", "全部视野直接 Cellpose 验证通过")
        logger.event(
            "stage_finished",
            "head_segmentation",
            "succeeded",
            duration_seconds=run_result.duration_seconds,
            return_code=run_result.return_code,
            extra={"field_count": len(validated_fields)},
        )
        manifest.add_file(logger.task_log_path, "task_log", "head_segmentation", "text/plain")
        manifest.add_file(logger.events_path, "events_log", "head_segmentation", "application/x-ndjson")
        return {
            "state": state.load(),
            "manifest": manifest.load(),
            "worker_result": worker_result,
            "run_result": run_result.as_dict(),
            "fields": validated_fields,
        }
    except BaseException as exception:
        logger.record_exception("head_segmentation", exception, "头部识别阶段失败")
        if state.exists():
            try:
                state.mark_failed("head_segmentation", exception)
            except BaseException as state_exception:
                logger.record_exception("head_segmentation", state_exception, "写入 failed 状态失败")
        raise
