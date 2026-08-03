"""人工头部校准窗口的轻量控制组件。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)


class HeadCalibrationControls(QWidget):
    previousRequested = Signal()
    nextRequested = Signal()
    fieldChanged = Signal(str)
    channelChanged = Signal(str)
    selectRequested = Signal()
    addRequested = Signal()
    deleteRequested = Signal()
    undoRequested = Signal()
    redoRequested = Signal()
    fitRequested = Signal()
    actualSizeRequested = Signal()
    zoomInRequested = Signal()
    zoomOutRequested = Signal()
    completeRequested = Signal()

    def __init__(self, field_ids, channels=None, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.previous_button = QPushButton("上一视野")
        self.next_button = QPushButton("下一视野")
        self.field_combo = QComboBox()
        self.field_combo.addItems(field_ids)
        self.channel_combo = QComboBox()
        self.channel_combo.addItems(list(channels or ["TRITC", "FITC"]))
        self.select_button = QPushButton("选择")
        self.add_button = QPushButton("新增头部")
        self.delete_button = QPushButton("删除选中头部")
        self.undo_button = QPushButton("撤销")
        self.redo_button = QPushButton("重做")
        self.fit_button = QPushButton("适应窗口")
        self.actual_button = QPushButton("1:1")
        self.zoom_in_button = QPushButton("放大")
        self.zoom_out_button = QPushButton("缩小")
        self.complete_button = QPushButton("完成头部校准")
        for widget in (
            self.previous_button,
            self.next_button,
            QLabel("视野："),
            self.field_combo,
            QLabel("底图："),
            self.channel_combo,
            self.select_button,
            self.add_button,
            self.delete_button,
            self.undo_button,
            self.redo_button,
            self.fit_button,
            self.actual_button,
            self.zoom_in_button,
            self.zoom_out_button,
            self.complete_button,
        ):
            layout.addWidget(widget)
        self.previous_button.clicked.connect(self.previousRequested)
        self.next_button.clicked.connect(self.nextRequested)
        self.field_combo.currentTextChanged.connect(self.fieldChanged)
        self.channel_combo.currentTextChanged.connect(self.channelChanged)
        self.select_button.clicked.connect(self.selectRequested)
        self.add_button.clicked.connect(self.addRequested)
        self.delete_button.clicked.connect(self.deleteRequested)
        self.undo_button.clicked.connect(self.undoRequested)
        self.redo_button.clicked.connect(self.redoRequested)
        self.fit_button.clicked.connect(self.fitRequested)
        self.actual_button.clicked.connect(self.actualSizeRequested)
        self.zoom_in_button.clicked.connect(self.zoomInRequested)
        self.zoom_out_button.clicked.connect(self.zoomOutRequested)
        self.complete_button.clicked.connect(self.completeRequested)

    def set_channels(
        self,
        channels,
        current_channel=None,
    ) -> None:
        normalized = [
            str(channel)
            for channel in channels
            if str(channel)
        ]

        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        self.channel_combo.addItems(normalized)

        if (
            current_channel
            and current_channel in normalized
        ):
            self.channel_combo.setCurrentText(
                current_channel
            )
        elif normalized:
            self.channel_combo.setCurrentIndex(0)

        self.channel_combo.blockSignals(False)

    def set_history_enabled(self, can_undo: bool, can_redo: bool) -> None:
        self.undo_button.setEnabled(can_undo)
        self.redo_button.setEnabled(can_redo)

    def set_progressive_mode(self, enabled: bool) -> None:
        """Configure the controls for sequential per-field completion."""
        enabled = bool(enabled)
        self.previous_button.setVisible(not enabled)
        self.field_combo.setEnabled(not enabled)
        if enabled:
            self.complete_button.setText("完成最后视野和头部校准")

    def set_progressive_position(self, index: int, total: int) -> None:
        """Update button state for a 0-based progressive field position."""
        index = max(0, int(index))
        total = max(1, int(total))
        is_last = index >= total - 1
        if is_last:
            self.next_button.setText("已是最后视野")
            self.next_button.setEnabled(False)
            self.complete_button.setEnabled(True)
        else:
            self.next_button.setText("完成当前视野并进入下一视野")
            self.next_button.setEnabled(True)
            self.complete_button.setEnabled(False)
