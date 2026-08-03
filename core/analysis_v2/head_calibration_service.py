"""Analysis V2 三视野人工头部校准服务。"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from .head_calibration_model import HeadCalibrationModel
from .label_image_io import (
    atomic_save_label_image,
    read_color_image,
    read_label_image,
    relabel_consecutive,
    validate_label_image,
)
from .manifest_store import ManifestStore
from .opencv_compat import cv2
from .stage_logger import StageLogger
from .task_state import TaskStateStore, atomic_write_json, current_timestamp


@dataclass
class HeadCalibrationField:
    field_id: str
    tritc_path: Path
    fitc_path: Path
    merge_path: Optional[Path]
    initial_labels_path: Path
    initial_objects_path: Path
    working_labels_path: Path
    calibration_state_path: Path
    final_labels_path: Path
    final_objects_path: Path
    final_overlay_path: Path
    model: Optional[HeadCalibrationModel] = None


class HeadCalibrationService:
    """加载 Stage 1 任务、自动保存并生成 Stage 2A 最终输出。"""

    def __init__(self, task_root: Path) -> None:
        self.task_root = Path(task_root).resolve()
        self.input_dir = self.task_root / "input"
        self.initial_dir = self.task_root / "segmentation" / "head"
        self.output_dir = self.task_root / "calibration" / "head"
        self.logs_dir = self.task_root / "logs"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        state_payload = self._read_json(self.task_root / "state.json")
        self.task_id = str(state_payload["task_id"])
        self.state_store = TaskStateStore(
            state_path=self.task_root / "state.json",
            task_id=self.task_id,
        )
        self.manifest_store = ManifestStore(
            manifest_path=self.task_root / "manifest.json",
            task_root=self.task_root,
            task_id=self.task_id,
        )
        self.logger = StageLogger(logs_dir=self.logs_dir, task_id=self.task_id)
        self.fields = self._discover_fields()
        self._image_cache = {}  # type: Dict[str, Dict[str, np.ndarray]]
        self._completed_field_results = {}  # type: Dict[str, Dict[str, Any]]
        self.logger.info("head_calibration", "人工头部校准工具已打开")
        self.logger.event(
            "head_calibration_opened",
            "head_calibration",
            "opened",
            extra={"field_count": len(self.fields)},
        )

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("JSON 顶层必须是对象：{}".format(path))
        return payload

    def _find_input(
        self,
        field_id: str,
        channel: str,
        required: bool = True,
    ) -> Optional[Path]:
        expected = (
            self.input_dir
            / "{}_{}.tif".format(
                field_id,
                channel,
            )
        )

        if expected.is_file():
            return expected

        matches = sorted(
            self.input_dir.glob(
                "{}_{}.*".format(
                    field_id,
                    channel,
                )
            )
        )

        if len(matches) == 1:
            return matches[0]

        if not matches and not required:
            return None

        raise ValueError(
            "\u89c6\u91ce {} \u7684 {} "
            "\u8f93\u5165\u6570\u91cf\u4e0d\u662f 1\uff1a{}".format(
                field_id,
                channel,
                matches,
            )
        )

    def _discover_fields(self) -> List[HeadCalibrationField]:
        label_paths = sorted(self.initial_dir.glob("*_HeadInitialLabels.tif"))
        if not label_paths:
            raise ValueError("Stage 1 任务不包含 HeadInitialLabels")
        fields = []
        for label_path in label_paths:
            field_id = label_path.name[: -len("_HeadInitialLabels.tif")]
            initial_objects = self.initial_dir / "{}_HeadInitialObjects.json".format(field_id)
            if not initial_objects.is_file():
                raise FileNotFoundError("初始对象 JSON 不存在：{}".format(initial_objects))
            fields.append(
                HeadCalibrationField(
                    field_id=field_id,
                    tritc_path=self._find_input(field_id, "TRITC"),
                    fitc_path=self._find_input(field_id, "FITC"),
                    merge_path=self._find_input(
                        field_id,
                        "Merge",
                        required=False,
                    ),
                    initial_labels_path=label_path,
                    initial_objects_path=initial_objects,
                    working_labels_path=self.output_dir / "{}_HeadWorkingLabels.tif".format(field_id),
                    calibration_state_path=self.output_dir / "{}_HeadCalibrationState.json".format(field_id),
                    final_labels_path=self.output_dir / "{}_HeadFinalLabels.tif".format(field_id),
                    final_objects_path=self.output_dir / "{}_HeadFinalObjects.json".format(field_id),
                    final_overlay_path=self.output_dir / "{}_HeadCalibrationOverlay.png".format(field_id),
                )
            )
        return fields

    def field_ids(self) -> List[str]:
        return [field.field_id for field in self.fields]

    def field_by_id(self, field_id: str) -> HeadCalibrationField:
        for field in self.fields:
            if field.field_id == field_id:
                return field
        raise KeyError("未知视野：{}".format(field_id))

    def available_channels(
        self,
        field_id: str,
    ) -> List[str]:
        field = self.field_by_id(field_id)
        channels = []

        if field.merge_path is not None:
            channels.append("Merge")

        channels.extend([
            "TRITC",
            "FITC",
        ])

        return channels

    def default_channel(
        self,
        field_id: str,
    ) -> str:
        channels = self.available_channels(field_id)

        if "Merge" in channels:
            return "Merge"

        return "TRITC"
    def load_field(self, field_id: str) -> HeadCalibrationField:
        field = self.field_by_id(field_id)
        if field.model is None:
            initial = read_label_image(field.initial_labels_path)
            revision = 0
            working = None
            if field.working_labels_path.is_file():
                working = read_label_image(
                    field.working_labels_path,
                    expected_shape=tuple(initial.shape),
                )
            if field.calibration_state_path.is_file():
                saved_state = self._read_json(field.calibration_state_path)
                revision = int(saved_state.get("revision", 0))
            field.model = HeadCalibrationModel(
                field_id=field.field_id,
                initial_labels=initial,
                working_labels=working,
                revision=revision,
            )
        self.logger.event(
            "field_loaded",
            "head_calibration",
            "loaded",
            extra={"field_id": field.field_id, "object_count": field.model.object_count},
        )
        return field

    def image(
        self,
        field_id: str,
        channel: str = "Merge",
    ) -> np.ndarray:
        requested = str(
            channel or ""
        ).strip().upper()

        aliases = {
            "R": "TRITC",
            "TRITC": "TRITC",
            "G": "FITC",
            "FITC": "FITC",
            "MERGE": "Merge",
        }

        if requested not in aliases:
            raise ValueError(
                "不支持的校准底图通道：{}".format(
                    channel
                )
            )

        normalized = aliases[requested]
        field = self.field_by_id(field_id)

        if (
            normalized == "Merge"
            and field.merge_path is None
        ):
            normalized = "TRITC"

        cached = self._image_cache.setdefault(
            field_id,
            {},
        )

        if normalized not in cached:
            if normalized == "TRITC":
                image_path = field.tritc_path
            elif normalized == "FITC":
                image_path = field.fitc_path
            else:
                image_path = field.merge_path

            if image_path is None:
                raise FileNotFoundError(
                    "视野 {} 不存在 {} 底图".format(
                        field_id,
                        normalized,
                    )
                )

            cached[normalized] = read_color_image(
                image_path
            )

        return cached[normalized]

    def select_object(self, field_id: str, x: float, y: float) -> int:
        field = self.load_field(field_id)
        object_id = field.model.select_at(x, y)
        if object_id:
            statistics = field.model.selected_statistics()
            self.logger.event(
                "object_selected",
                "head_calibration",
                "selected",
                extra={
                    "field_id": field_id,
                    "object_id": object_id,
                    "area": statistics["area"] if statistics else None,
                },
            )
        return object_id

    def save_field(self, field_id: str, completed: bool = False) -> Dict[str, Any]:
        field = self.load_field(field_id)
        statistics = atomic_save_label_image(field.working_labels_path, field.model.labels)
        payload = {
            "schema_version": 1,
            "field_id": field.field_id,
            "initial_labels_path": str(field.initial_labels_path),
            "working_labels_path": str(field.working_labels_path),
            "revision": field.model.revision,
            "object_count": field.model.object_count,
            "updated_at": current_timestamp(),
            "completed": bool(completed),
            "operation_history": list(field.model.operation_summaries[-100:]),
            "label_statistics": statistics,
        }
        atomic_write_json(field.calibration_state_path, payload)
        self.logger.info(
            "head_calibration",
            "视野 {} 已保存，revision={}".format(field.field_id, field.model.revision),
        )
        self.logger.event(
            "field_saved",
            "head_calibration",
            "saved",
            extra={
                "field_id": field.field_id,
                "revision": field.model.revision,
                "object_count": field.model.object_count,
            },
        )
        return payload

    def delete_selected(self, field_id: str) -> bool:
        field = self.load_field(field_id)
        command = field.model.delete_selected()
        if command is None:
            return False
        self.logger.info(
            "head_calibration",
            "视野 {} 删除对象 {}".format(field_id, command.object_id),
        )
        self.logger.event(
            "object_deleted",
            "head_calibration",
            "edited",
            extra={"field_id": field_id, "object_id": command.object_id},
        )
        self.save_field(field_id)
        return True

    def add_ellipse(
        self,
        field_id: str,
        start: Sequence[float],
        end: Sequence[float],
    ) -> int:
        field = self.load_field(field_id)
        command = field.model.add_ellipse(
            (float(start[0]), float(start[1])),
            (float(end[0]), float(end[1])),
        )
        self.logger.info(
            "head_calibration",
            "视野 {} 新增椭圆对象 {}".format(field_id, command.object_id),
        )
        self.logger.event(
            "object_added",
            "head_calibration",
            "edited",
            extra={"field_id": field_id, "object_id": command.object_id},
        )
        self.save_field(field_id)
        return command.object_id

    def undo(self, field_id: str) -> bool:
        field = self.load_field(field_id)
        command = field.model.undo()
        if command is None:
            return False
        self.logger.event(
            "undo",
            "head_calibration",
            "edited",
            extra={"field_id": field_id, "action": command.action},
        )
        self.save_field(field_id)
        return True

    def redo(self, field_id: str) -> bool:
        field = self.load_field(field_id)
        command = field.model.redo()
        if command is None:
            return False
        self.logger.event(
            "redo",
            "head_calibration",
            "edited",
            extra={"field_id": field_id, "action": command.action},
        )
        self.save_field(field_id)
        return True

    @staticmethod
    def _validate_independent_objects(labels: np.ndarray) -> None:
        positive = np.unique(labels[labels > 0])
        expected = np.arange(1, positive.size + 1, dtype=positive.dtype)
        if not np.array_equal(positive, expected):
            raise ValueError("最终对象标签不是连续正整数 1..N")
        height, width = labels.shape
        positions = np.flatnonzero(labels.ravel() > 0)
        object_ids = labels.ravel()[positions].astype(np.int64)
        y_values = (positions // width).astype(np.int32)
        x_values = (positions % width).astype(np.int32)
        minimum_x = np.full(positive.size + 1, width, dtype=np.int32)
        minimum_y = np.full(positive.size + 1, height, dtype=np.int32)
        maximum_x = np.zeros(positive.size + 1, dtype=np.int32)
        maximum_y = np.zeros(positive.size + 1, dtype=np.int32)
        np.minimum.at(minimum_x, object_ids, x_values)
        np.minimum.at(minimum_y, object_ids, y_values)
        np.maximum.at(maximum_x, object_ids, x_values)
        np.maximum.at(maximum_y, object_ids, y_values)
        for object_id in range(1, positive.size + 1):
            x1, y1 = int(minimum_x[object_id]), int(minimum_y[object_id])
            x2, y2 = int(maximum_x[object_id]) + 1, int(maximum_y[object_id]) + 1
            component_count, _components = cv2.connectedComponents(
                (labels[y1:y2, x1:x2] == object_id).astype(np.uint8),
                connectivity=8,
            )
            if component_count != 2:
                raise ValueError("最终对象 {} 包含多个不连通区域".format(object_id))

    @staticmethod
    def _measure_objects(final_labels: np.ndarray, initial_labels: np.ndarray) -> List[Dict[str, Any]]:
        height, width = final_labels.shape
        flat_final = final_labels.ravel().astype(np.int64)
        positive_pixels = flat_final > 0
        object_ids = flat_final[positive_pixels]
        if object_ids.size == 0:
            return []
        positions = np.flatnonzero(positive_pixels)
        y_values = (positions // width).astype(np.int32)
        x_values = (positions % width).astype(np.int32)
        maximum_id = int(object_ids.max())
        counts = np.bincount(object_ids, minlength=maximum_id + 1)
        x_sums = np.bincount(object_ids, weights=x_values, minlength=maximum_id + 1)
        y_sums = np.bincount(object_ids, weights=y_values, minlength=maximum_id + 1)
        minimum_x = np.full(maximum_id + 1, width, dtype=np.int32)
        minimum_y = np.full(maximum_id + 1, height, dtype=np.int32)
        maximum_x = np.zeros(maximum_id + 1, dtype=np.int32)
        maximum_y = np.zeros(maximum_id + 1, dtype=np.int32)
        np.minimum.at(minimum_x, object_ids, x_values)
        np.minimum.at(minimum_y, object_ids, y_values)
        np.maximum.at(maximum_x, object_ids, x_values)
        np.maximum.at(maximum_y, object_ids, y_values)

        flat_initial = initial_labels.ravel().astype(np.int64)[positive_pixels]
        overlapping = flat_initial > 0
        major_overlap = np.zeros(maximum_id + 1, dtype=np.int64)
        if np.any(overlapping):
            initial_base = int(flat_initial[overlapping].max()) + 1
            pair_codes = object_ids[overlapping] * initial_base + flat_initial[overlapping]
            unique_pairs, pair_counts = np.unique(pair_codes, return_counts=True)
            pair_final_ids = unique_pairs // initial_base
            np.maximum.at(major_overlap, pair_final_ids, pair_counts)

        objects = []
        for object_id in range(1, maximum_id + 1):
            area = int(counts[object_id])
            source = "initial" if int(major_overlap[object_id]) >= (area * 0.5) else "manual"
            objects.append({
                "object_id": int(object_id),
                "area": area,
                "centroid_x": float(x_sums[object_id] / area),
                "centroid_y": float(y_sums[object_id] / area),
                "bbox": [
                    int(minimum_x[object_id]),
                    int(minimum_y[object_id]),
                    int(maximum_x[object_id] - minimum_x[object_id] + 1),
                    int(maximum_y[object_id] - minimum_y[object_id] + 1),
                ],
                "source": source,
            })
        return objects

    @staticmethod
    def _atomic_save_png(path: Path, image: np.ndarray) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".{}.".format(path.stem),
            suffix=".tmp.png",
            dir=str(path.parent),
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            if not cv2.imwrite(str(temporary_path), image):
                raise OSError("无法写入临时 overlay")
            if cv2.imread(str(temporary_path), cv2.IMREAD_COLOR) is None:
                raise ValueError("临时 overlay 回读失败")
            os.replace(str(temporary_path), str(path))
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _finish_field(self, field: HeadCalibrationField) -> Dict[str, Any]:
        self.save_field(field.field_id)
        merge = self.image(field.field_id, "Merge")
        expected_shape = (int(merge.shape[0]), int(merge.shape[1]))
        validate_label_image(field.model.labels, expected_shape=expected_shape)
        final_labels, mapping = relabel_consecutive(field.model.labels)
        self._validate_independent_objects(final_labels)
        statistics = atomic_save_label_image(field.final_labels_path, final_labels)
        objects = self._measure_objects(final_labels, field.model.initial_labels)
        atomic_write_json(
            field.final_objects_path,
            {
                "schema_version": 1,
                "field_id": field.field_id,
                "labels_path": str(field.final_labels_path),
                "object_count": len(objects),
                "relabel_mapping": {str(key): value for key, value in mapping.items()},
                "objects": objects,
            },
        )
        overlay = merge.copy()
        contours, _hierarchy = cv2.findContours(
            (final_labels > 0).astype(np.uint8),
            cv2.RETR_LIST,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        cv2.drawContours(overlay, contours, -1, (255, 255, 255), 1, cv2.LINE_AA)
        self._atomic_save_png(field.final_overlay_path, overlay)
        state_payload = self.save_field(field.field_id, completed=True)
        for path, role, media_type in (
            (field.final_labels_path, "head_final_labels", "image/tiff"),
            (field.final_objects_path, "head_final_objects", "application/json"),
            (field.final_overlay_path, "head_calibration_overlay", "image/png"),
            (field.calibration_state_path, "head_calibration_state", "application/json"),
        ):
            self.manifest_store.add_file(
                path,
                role=role,
                stage="head_calibration",
                media_type=media_type,
                metadata={"field_id": field.field_id},
            )
        return {
            "field_id": field.field_id,
            "statistics": statistics,
            "object_count": len(objects),
            "state": state_payload,
        }

    def complete_field(self, field_id: str) -> Dict[str, Any]:
        """Finalize one field so tail preparation can start immediately.

        The result is cached in memory to avoid rewriting a completed field when
        the final all-field completion step runs.
        """
        field_id = str(field_id or "").strip()
        if not field_id:
            raise ValueError("field_id 不能为空")
        cached = self._completed_field_results.get(field_id)
        if cached is not None:
            return dict(cached)
        field = self.load_field(field_id)
        result = self._finish_field(field)
        self._completed_field_results[field_id] = dict(result)
        self.logger.event(
            "head_calibration_field_completed",
            "head_calibration",
            "succeeded",
            extra={
                "field_id": field_id,
                "object_count": int(result.get("object_count", 0)),
            },
        )
        return dict(result)

    def complete(self) -> Dict[str, Any]:
        try:
            results = [self.complete_field(field.field_id) for field in self.fields]
            self.state_store.update(
                "head_calibrated",
                "head_calibration",
                "全部视野人工头部校准已完成",
            )
            self.logger.info("head_calibration", "全部视野人工头部校准完成")
            self.logger.event(
                "head_calibration_completed",
                "head_calibration",
                "succeeded",
                extra={"field_count": len(results)},
            )
            for path, role, media_type in (
                (self.logger.task_log_path, "task_log", "text/plain"),
                (self.logger.events_path, "events_log", "application/x-ndjson"),
            ):
                self.manifest_store.add_file(
                    path,
                    role=role,
                    stage="head_calibration",
                    media_type=media_type,
                )
            return {
                "state": self.state_store.load(),
                "fields": results,
                "manifest": self.manifest_store.load(),
            }
        except BaseException as exception:
            self.logger.record_exception(
                "head_calibration",
                exception,
                "完成人工头部校准失败",
                event_name="head_calibration_failed",
            )
            raise

    def record_failure(self, exception: BaseException, message: str) -> None:
        self.logger.record_exception(
            "head_calibration",
            exception,
            message,
            event_name="head_calibration_failed",
        )
