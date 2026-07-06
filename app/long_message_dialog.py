# -*- coding: utf-8 -*-
"""
long_message_dialog.py

用于显示长日志/长错误信息的通用弹窗。

QMessageBox 在文本很长时会被内容撑得过高，甚至超过屏幕。
这个弹窗采用固定窗口 + 可滚动 QTextEdit：
1. 顶部只显示摘要；
2. 详细内容放入滚动文本框；
3. 支持一键复制详情；
4. 避免长日志撑爆界面。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)


class LongMessageDialog(QDialog):
    def __init__(
        self,
        parent=None,
        title: str = "提示",
        summary: str = "",
        detail: str = "",
        level: str = "info",
    ):
        super().__init__(parent)
        self.detail_text = str(detail or "")
        self.level = str(level or "info").lower()

        self.setWindowTitle(title)
        self.setObjectName("LongMessageDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setModal(True)

        self.init_ui(summary=summary, detail=detail)
        self.apply_style()
        self.adjust_dialog_size()

    def init_ui(self, summary: str, detail: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)

        self.icon_label = QLabel(self.icon_text())
        self.icon_label.setObjectName("DialogIcon")
        self.icon_label.setFixedSize(34, 34)
        self.icon_label.setAlignment(Qt.AlignCenter)

        self.summary_label = QLabel(str(summary or ""))
        self.summary_label.setObjectName("SummaryLabel")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        header_layout.addWidget(self.icon_label, 0, Qt.AlignTop)
        header_layout.addWidget(self.summary_label, 1)
        root.addLayout(header_layout)

        self.detail_edit = QTextEdit()
        self.detail_edit.setObjectName("DetailText")
        self.detail_edit.setReadOnly(True)
        self.detail_edit.setPlainText(str(detail or ""))
        self.detail_edit.setLineWrapMode(QTextEdit.WidgetWidth)
        self.detail_edit.setMinimumHeight(220)
        root.addWidget(self.detail_edit, 1)

        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)

        self.btn_copy = QPushButton("复制详情")
        self.btn_ok = QPushButton("确定")
        self.btn_ok.setDefault(True)

        button_layout.addStretch()
        button_layout.addWidget(self.btn_copy)
        button_layout.addWidget(self.btn_ok)
        root.addLayout(button_layout)

        self.btn_copy.clicked.connect(self.copy_detail)
        self.btn_ok.clicked.connect(self.accept)

    def icon_text(self) -> str:
        if self.level in {"warning", "warn"}:
            return "!"
        if self.level in {"error", "critical"}:
            return "×"
        if self.level in {"success", "ok"}:
            return "✓"
        return "i"

    def adjust_dialog_size(self):
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(700, max(560, int(available.width() * 0.50)))
            height = min(500, max(380, int(available.height() * 0.58)))
        else:
            width, height = 660, 460

        self.resize(width, height)
        self.setMinimumSize(560, 360)
        self.setMaximumSize(900, 650)

    def copy_detail(self):
        QApplication.clipboard().setText(self.detail_text)
        self.btn_copy.setText("已复制")
        self.btn_copy.setEnabled(False)

    def apply_style(self):
        if self.level in {"warning", "warn"}:
            icon_bg = "#FFF5E5"
            icon_border = "#FAD89A"
            icon_color = "#F59E0B"
        elif self.level in {"error", "critical"}:
            icon_bg = "#FDECEC"
            icon_border = "#F6BFC0"
            icon_color = "#EF4444"
        elif self.level in {"success", "ok"}:
            icon_bg = "#EAF8EF"
            icon_border = "#BDEACB"
            icon_color = "#16A34A"
        else:
            icon_bg = "#EAF2FF"
            icon_border = "#BCD7FF"
            icon_color = "#1769E0"

        self.setStyleSheet(f"""
            QDialog#LongMessageDialog {{
                background-color: #F5F8FC;
                color: #1F2D3D;
                font-family: "Microsoft YaHei";
                font-size: 13px;
            }}

            QLabel#DialogIcon {{
                background-color: {icon_bg};
                border: 1px solid {icon_border};
                border-radius: 17px;
                color: {icon_color};
                font-size: 20px;
                font-weight: 700;
            }}

            QLabel#SummaryLabel {{
                background-color: transparent;
                color: #1F2D3D;
                font-size: 13px;
                line-height: 1.5;
            }}

            QTextEdit#DetailText {{
                background-color: #FFFFFF;
                border: 1px solid #DDE6F2;
                border-radius: 8px;
                padding: 8px;
                color: #1F2D3D;
                selection-background-color: #DCEBFF;
                font-family: "Consolas", "Microsoft YaHei";
                font-size: 12px;
            }}

            QPushButton {{
                min-width: 86px;
                min-height: 32px;
                padding: 5px 16px;
                border: 1px solid #DDE6F2;
                border-radius: 6px;
                background-color: #FFFFFF;
                color: #1F2D3D;
                font-weight: 500;
            }}

            QPushButton:hover {{
                background-color: #F2F7FF;
                border-color: #BCD7FF;
                color: #1769E0;
            }}

            QPushButton:pressed {{
                background-color: #EAF2FF;
                border-color: #1769E0;
            }}

            QPushButton:default {{
                background-color: #1769E0;
                border-color: #1769E0;
                color: #FFFFFF;
                font-weight: 600;
            }}

            QPushButton:default:hover {{
                background-color: #0F5ED7;
                border-color: #0F5ED7;
                color: #FFFFFF;
            }}

            QPushButton:disabled {{
                background-color: #F8FAFD;
                border-color: #E8EEF6;
                color: #8A97A8;
            }}

            QScrollBar:vertical {{
                background-color: #EEF4FB;
                width: 10px;
                margin: 0px;
                border: none;
            }}

            QScrollBar::handle:vertical {{
                background-color: #DDE6F2;
                min-height: 30px;
                border-radius: 5px;
            }}

            QScrollBar::handle:vertical:hover {{
                background-color: #BCD7FF;
            }}

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
                background: none;
                border: none;
            }}
        """)


def show_long_message_dialog(
    parent,
    title: str,
    summary: str,
    detail: str,
    level: str = "info",
):
    dialog = LongMessageDialog(
        parent=parent,
        title=title,
        summary=summary,
        detail=detail,
        level=level,
    )
    return dialog.exec()
