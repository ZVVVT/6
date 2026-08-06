"""单视野头部校准模型及局部撤销/重做。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from .label_image_io import validate_label_image
from .opencv_compat import cv2


@dataclass
class LabelEditCommand:
    action: str
    bbox: Tuple[int, int, int, int]
    before: np.ndarray
    after: np.ndarray
    object_id: int
    object_ids: Tuple[int, ...] = ()

    def apply(self, labels: np.ndarray, use_after: bool) -> None:
        x, y, width, height = self.bbox
        labels[y : y + height, x : x + width] = self.after if use_after else self.before

    def summary(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "object_id": self.object_id,
            "object_ids": list(
                self.object_ids
                or ((self.object_id,) if self.object_id else ())
            ),
            "bbox": list(self.bbox),
            "changed_pixels": int(np.count_nonzero(self.before != self.after)),
        }


class HeadCalibrationModel:
    """维护一个视野的工作标签和最近 100 步局部操作。"""

    def __init__(
        self,
        field_id: str,
        initial_labels: np.ndarray,
        working_labels: Optional[np.ndarray] = None,
        revision: int = 0,
        history_limit: int = 100,
    ) -> None:
        validate_label_image(initial_labels)
        self.field_id = str(field_id)
        self.initial_labels = np.asarray(initial_labels).copy()
        self.labels = (
            np.asarray(working_labels).copy()
            if working_labels is not None
            else self.initial_labels.copy()
        )
        validate_label_image(self.labels, expected_shape=tuple(self.initial_labels.shape))
        self.selected_object_id = 0
        self.selected_object_ids = set()  # type: Set[int]
        self.revision = int(revision)
        self.history_limit = int(history_limit)
        self.undo_stack = []  # type: List[LabelEditCommand]
        self.redo_stack = []  # type: List[LabelEditCommand]
        self.operation_summaries = []  # type: List[Dict[str, Any]]

    @property
    def object_ids(self) -> np.ndarray:
        values = np.unique(self.labels)
        return values[values > 0]

    @property
    def object_count(self) -> int:
        return int(self.object_ids.size)

    def object_id_at(self, x: float, y: float) -> int:
        column, row = int(x), int(y)
        if row < 0 or column < 0 or row >= self.labels.shape[0] or column >= self.labels.shape[1]:
            return 0
        return int(self.labels[row, column])

    def select_at(self, x: float, y: float, toggle: bool = False) -> int:
        object_id = self.object_id_at(x, y)
        if object_id <= 0:
            self.clear_selection()
            return 0
        if toggle:
            if object_id in self.selected_object_ids:
                self.selected_object_ids.remove(object_id)
                self.selected_object_id = (
                    max(self.selected_object_ids)
                    if self.selected_object_ids
                    else 0
                )
            else:
                self.selected_object_ids.add(object_id)
                self.selected_object_id = object_id
        else:
            self.selected_object_ids = {object_id}
            self.selected_object_id = object_id
        return self.selected_object_id

    def clear_selection(self) -> None:
        self.selected_object_id = 0
        self.selected_object_ids.clear()

    def selected_statistics(self) -> Optional[Dict[str, Any]]:
        if len(self.selected_object_ids) != 1:
            return None
        object_id = int(self.selected_object_id)
        if object_id <= 0:
            return None
        rows, columns = np.nonzero(self.labels == object_id)
        if rows.size == 0:
            self.clear_selection()
            return None
        return {
            "object_id": object_id,
            "area": int(rows.size),
            "centroid_x": float(columns.mean()),
            "centroid_y": float(rows.mean()),
            "bbox": [
                int(columns.min()),
                int(rows.min()),
                int(columns.max() - columns.min() + 1),
                int(rows.max() - rows.min() + 1),
            ],
        }

    def _record(self, command: LabelEditCommand) -> None:
        command.apply(self.labels, use_after=True)
        self.undo_stack.append(command)
        if len(self.undo_stack) > self.history_limit:
            del self.undo_stack[0]
        self.redo_stack.clear()
        self.revision += 1
        summary = command.summary()
        summary["revision"] = self.revision
        self.operation_summaries.append(summary)
        if len(self.operation_summaries) > self.history_limit:
            del self.operation_summaries[0]

    def delete_selected(self) -> Optional[LabelEditCommand]:
        object_ids = tuple(sorted(
            int(value)
            for value in self.selected_object_ids
            if int(value) > 0
        ))
        if not object_ids:
            return None
        selected_mask = np.isin(
            self.labels,
            np.asarray(object_ids, dtype=self.labels.dtype),
        )
        rows, columns = np.nonzero(selected_mask)
        if rows.size == 0:
            self.clear_selection()
            return None
        x, y = int(columns.min()), int(rows.min())
        width = int(columns.max() - x + 1)
        height = int(rows.max() - y + 1)
        before = self.labels[y : y + height, x : x + width].copy()
        after = before.copy()
        after[np.isin(after, np.asarray(object_ids, dtype=after.dtype))] = 0
        command = LabelEditCommand(
            "delete",
            (x, y, width, height),
            before,
            after,
            object_ids[0] if len(object_ids) == 1 else 0,
            object_ids,
        )
        self._record(command)
        self.clear_selection()
        return command

    def add_ellipse(
        self,
        start: Tuple[float, float],
        end: Tuple[float, float],
        minimum_area: int = 20,
    ) -> LabelEditCommand:
        left = max(0, int(round(min(start[0], end[0]))))
        top = max(0, int(round(min(start[1], end[1]))))
        right = min(self.labels.shape[1] - 1, int(round(max(start[0], end[0]))))
        bottom = min(self.labels.shape[0] - 1, int(round(max(start[1], end[1]))))
        width, height = right - left + 1, bottom - top + 1
        if width < 2 or height < 2:
            raise ValueError("椭圆外接矩形过小")
        mask = np.zeros((height, width), dtype=np.uint8)
        center = ((width - 1) // 2, (height - 1) // 2)
        axes = (max(1, (width - 1) // 2), max(1, (height - 1) // 2))
        cv2.ellipse(mask, center, axes, 0, 0, 360, 255, thickness=-1)
        before = self.labels[top : top + height, left : left + width].copy()
        writable = (mask > 0) & (before == 0)
        area = int(np.count_nonzero(writable))
        if area < int(minimum_area):
            raise ValueError("新增椭圆有效面积过小：{} 像素".format(area))
        component_count, _components = cv2.connectedComponents(
            writable.astype(np.uint8),
            connectivity=8,
        )
        if component_count != 2:
            raise ValueError("新增椭圆被已有对象分割为多个区域，请调整位置")
        new_id = int(self.labels.max()) + 1
        if new_id > np.iinfo(np.uint16).max:
            raise ValueError("对象编号超过 uint16 上限")
        after = before.copy()
        after[writable] = new_id
        command = LabelEditCommand("add_ellipse", (left, top, width, height), before, after, new_id)
        self._record(command)
        self.selected_object_id = new_id
        self.selected_object_ids = {new_id}
        return command

    def undo(self) -> Optional[LabelEditCommand]:
        if not self.undo_stack:
            return None
        command = self.undo_stack.pop()
        command.apply(self.labels, use_after=False)
        self.redo_stack.append(command)
        self.revision += 1
        self.clear_selection()
        self.operation_summaries.append({
            "action": "undo",
            "target": command.action,
            "object_id": command.object_id,
            "object_ids": list(command.object_ids),
            "revision": self.revision,
        })
        return command

    def redo(self) -> Optional[LabelEditCommand]:
        if not self.redo_stack:
            return None
        command = self.redo_stack.pop()
        command.apply(self.labels, use_after=True)
        self.undo_stack.append(command)
        self.revision += 1
        self.clear_selection()
        self.operation_summaries.append({
            "action": "redo",
            "target": command.action,
            "object_id": command.object_id,
            "object_ids": list(command.object_ids),
            "revision": self.revision,
        })
        return command
