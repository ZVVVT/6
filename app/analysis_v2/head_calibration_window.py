"""Analysis V2 独立人工头部校准主窗口。"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from core.analysis_v2.head_calibration_service import HeadCalibrationService

from .head_calibration_widgets import HeadCalibrationControls
from .image_canvas import ImageCanvas


class HeadCalibrationWindow(QMainWindow):
    calibration_completed = Signal(object)

    def __init__(
        self,
        task_root: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)

        self.setWindowTitle(
            "Analysis V2 - \u4eba\u5de5\u5934\u90e8\u6821\u51c6"
        )
        self.resize(1500, 900)

        self.service = HeadCalibrationService(
            task_root
        )

        field_ids = self.service.field_ids()

        if not field_ids:
            raise ValueError(
                "\u6821\u51c6\u4efb\u52a1\u4e0d\u5305\u542b\u53ef\u7528\u89c6\u91ce"
            )

        first_field_id = field_ids[0]

        self.current_field_id = ""
        self.current_channel = (
            self.service.default_channel(
                first_field_id
            )
        )
        self._switching_field = False

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self.controls = HeadCalibrationControls(
            field_ids,
            self.service.available_channels(
                first_field_id
            ),
        )

        self.controls.set_channels(
            self.service.available_channels(
                first_field_id
            ),
            self.current_channel,
        )

        self.canvas = ImageCanvas()

        layout.addWidget(self.controls)
        layout.addWidget(self.canvas, 1)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())

        self._connect_signals()
        self._install_shortcuts()

        self._load_field(
            first_field_id,
            fit=True,
        )

    def _connect_signals(self) -> None:
        self.controls.previousRequested.connect(lambda: self._move_field(-1))
        self.controls.nextRequested.connect(lambda: self._move_field(1))
        self.controls.fieldChanged.connect(self._field_combo_changed)
        self.controls.channelChanged.connect(self._channel_changed)
        self.controls.selectRequested.connect(self._select_mode)
        self.controls.addRequested.connect(self._add_mode)
        self.controls.deleteRequested.connect(self._delete_selected)
        self.controls.undoRequested.connect(self._undo)
        self.controls.redoRequested.connect(self._redo)
        self.controls.fitRequested.connect(self.canvas.fit_to_window)
        self.controls.actualSizeRequested.connect(self.canvas.actual_size)
        self.controls.zoomInRequested.connect(self.canvas.zoom_in)
        self.controls.zoomOutRequested.connect(self.canvas.zoom_out)
        self.controls.completeRequested.connect(self._complete)
        self.canvas.imageClicked.connect(self._select_at)
        self.canvas.ellipseRequested.connect(self._add_ellipse)

    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence.Delete, self, activated=self._delete_selected)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self._undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self._redo)

    def _show_error(self, title: str, exception: BaseException) -> None:
        self.service.record_failure(exception, title)
        QMessageBox.critical(self, title, "{}：{}".format(type(exception).__name__, exception))
        self.statusBar().showMessage("{}：{}".format(title, exception))

    def _load_field(
        self,
        field_id: str,
        fit: bool = False,
    ) -> None:
        started = time.perf_counter()

        try:
            field = self.service.load_field(
                field_id
            )

            channels = (
                self.service.available_channels(
                    field_id
                )
            )

            if self.current_channel not in channels:
                self.current_channel = (
                    self.service.default_channel(
                        field_id
                    )
                )

            self.controls.set_channels(
                channels,
                self.current_channel,
            )

            image = self.service.image(
                field_id,
                self.current_channel,
            )

            self.current_field_id = field_id

            self.canvas.set_image(
                image,
                fit=False,
            )
            self.canvas.set_labels(
                field.model.labels,
                field.model.selected_object_id,
            )

            if fit:
                self.canvas.fit_to_window()

            self.controls.field_combo.blockSignals(
                True
            )
            self.controls.field_combo.setCurrentText(
                field_id
            )
            self.controls.field_combo.blockSignals(
                False
            )

            self._select_mode()
            self._update_status()

            elapsed = (
                time.perf_counter() - started
            ) * 1000.0

            self.statusBar().showMessage(
                "\u89c6\u91ce {} \u5df2\u52a0\u8f7d\uff0c"
                "\u5bf9\u8c61 {}\uff0c\u5e95\u56fe {}\uff0c"
                "\u8017\u65f6 {:.0f} ms".format(
                    field_id,
                    field.model.object_count,
                    self.current_channel,
                    elapsed,
                )
            )

        except BaseException as exception:
            self._show_error(
                "\u52a0\u8f7d\u89c6\u91ce\u5931\u8d25",
                exception,
            )

    def _save_current(self) -> bool:
        if not self.current_field_id:
            return True
        try:
            self.statusBar().showMessage("正在保存 {}…".format(self.current_field_id))
            self.service.save_field(self.current_field_id)
            return True
        except BaseException as exception:
            self._show_error("保存当前视野失败", exception)
            return False

    def _field_combo_changed(self, field_id: str) -> None:
        if not field_id or field_id == self.current_field_id or self._switching_field:
            return
        previous = self.current_field_id
        if not self._save_current():
            self.controls.field_combo.blockSignals(True)
            self.controls.field_combo.setCurrentText(previous)
            self.controls.field_combo.blockSignals(False)
            return
        self._load_field(field_id, fit=True)

    def _move_field(self, offset: int) -> None:
        field_ids = self.service.field_ids()
        index = field_ids.index(self.current_field_id)
        target = max(0, min(len(field_ids) - 1, index + offset))
        if target != index:
            self.controls.field_combo.setCurrentIndex(target)

    def _channel_changed(self, channel: str) -> None:
        self.current_channel = channel
        if self.current_field_id:
            try:
                self.canvas.set_image(
                    self.service.image(self.current_field_id, channel),
                    fit=False,
                )
            except BaseException as exception:
                self._show_error("切换底图失败", exception)

    def _select_mode(self) -> None:
        self.canvas.set_mode("select")
        self.statusBar().showMessage("选择模式：单击对象可选中，Delete 删除")

    def _add_mode(self) -> None:
        self.canvas.set_mode("add")
        self.statusBar().showMessage("新增模式：在原图上拖动绘制椭圆")

    def _select_at(self, x: float, y: float) -> None:
        try:
            field = self.service.load_field(self.current_field_id)
            object_id = self.service.select_object(self.current_field_id, x, y)
            self.canvas.set_selected_object(object_id)
            self._update_status()
        except BaseException as exception:
            self._show_error("选择对象失败", exception)

    def _delete_selected(self) -> None:
        try:
            if self.service.delete_selected(self.current_field_id):
                self._refresh_labels()
                self.statusBar().showMessage("选中头部已删除并自动保存")
            else:
                self.statusBar().showMessage("当前没有选中头部")
        except BaseException as exception:
            self._show_error("删除对象失败", exception)

    def _add_ellipse(self, x1: float, y1: float, x2: float, y2: float) -> None:
        try:
            object_id = self.service.add_ellipse(
                self.current_field_id,
                (x1, y1),
                (x2, y2),
            )
            self._refresh_labels()
            self._select_mode()
            self.statusBar().showMessage("已新增头部 {} 并自动保存".format(object_id))
        except ValueError as exception:
            self._select_mode()
            QMessageBox.warning(self, "新增头部被拒绝", str(exception))
            self.statusBar().showMessage(str(exception))
        except BaseException as exception:
            self._select_mode()
            self._show_error("新增头部失败", exception)

    def _undo(self) -> None:
        try:
            if self.service.undo(self.current_field_id):
                self._refresh_labels()
                self.statusBar().showMessage("已撤销并自动保存")
            else:
                self.statusBar().showMessage("没有可撤销操作")
        except BaseException as exception:
            self._show_error("撤销失败", exception)

    def _redo(self) -> None:
        try:
            if self.service.redo(self.current_field_id):
                self._refresh_labels()
                self.statusBar().showMessage("已重做并自动保存")
            else:
                self.statusBar().showMessage("没有可重做操作")
        except BaseException as exception:
            self._show_error("重做失败", exception)

    def _refresh_labels(self) -> None:
        field = self.service.load_field(self.current_field_id)
        self.canvas.set_labels(field.model.labels, field.model.selected_object_id)
        self._update_status()

    def _update_status(self) -> None:
        if not self.current_field_id:
            return
        field = self.service.load_field(self.current_field_id)
        statistics = field.model.selected_statistics()
        if statistics:
            message = "视野 {} | 对象 ID {} | 面积 {} | 当前对象总数 {}".format(
                field.field_id,
                statistics["object_id"],
                statistics["area"],
                field.model.object_count,
            )
        else:
            message = "视野 {} | 未选中对象 | 当前对象总数 {}".format(
                field.field_id,
                field.model.object_count,
            )
        self.statusBar().showMessage(message)
        self.controls.set_history_enabled(
            bool(field.model.undo_stack),
            bool(field.model.redo_stack),
        )

    def _complete(self) -> None:
        answer = QMessageBox.question(
            self,
            "完成头部校准",
            "确认完成全部视野头部校准并生成最终标签？",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            if not self._save_current():
                return
            self.statusBar().showMessage("正在生成最终校准结果…")
            result = self.service.complete()
            QMessageBox.information(
                self,
                "头部校准完成",
                "全部 {} 个视野已完成，state={}".format(
                    len(result["fields"]), result["state"]["status"]
                ),
            )
            self.statusBar().showMessage("头部校准已完成")
            self.calibration_completed.emit(result)
        except BaseException as exception:
            self._show_error("完成头部校准失败", exception)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._save_current():
            event.accept()
        else:
            event.ignore()
