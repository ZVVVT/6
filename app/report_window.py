# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import re
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
    QHeaderView,
    QFrame,
    QSizePolicy,
)

from core.config_manager import ConfigManager
from core.report_generator import ReportGenerator

try:
    from app.theme import DEFAULT_THEME_KEY, get_theme
except Exception:
    DEFAULT_THEME_KEY = "medical_blue"

    def get_theme(theme_key: str = DEFAULT_THEME_KEY) -> Dict[str, str]:
        return {
            "primary": "#1769E0",
            "primary_hover": "#0F5ED7",
            "success": "#16A34A",
            "warning": "#F59E0B",
            "danger": "#EF4444",
            "purple": "#7C3AED",
            "text_primary": "#1F2D3D",
            "text_secondary": "#5E6B7A",
            "text_muted": "#8A97A8",
            "surface": "#FFFFFF",
            "background": "#F5F8FC",
            "border": "#DDE6F2",
        }

try:
    from app.ui_components import apply_shadow
except Exception:
    def apply_shadow(widget, *args, **kwargs):
        return None


class ReportWindow(QWidget):
    """报告管理页面。"""

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.database = database
        self.config = ConfigManager()
        self.config.ensure_default_config()
        self.current_case = None
        self.current_report_path = ""
        self.theme = get_theme(DEFAULT_THEME_KEY)
        self._report_rows: List[dict] = []
        self.init_ui()

    # ------------------------------------------------------------------
    # 基础路径 / 图标
    # ------------------------------------------------------------------
    @staticmethod
    def project_root() -> Path:
        return Path(__file__).resolve().parent.parent

    @classmethod
    def icon_path(cls, icon_name: str) -> Path:
        return cls.project_root() / "assets" / "icons" / icon_name

    @staticmethod
    def _normalize_svg(svg_text: str, color: str) -> str:
        svg_text = svg_text.replace("currentColor", color)
        svg_text = re.sub(r'stroke="(?!none)[^"]*"', f'stroke="{color}"', svg_text)
        svg_text = re.sub(r"stroke='(?!none)[^']*'", f"stroke='{color}'", svg_text)
        svg_text = re.sub(r'fill="(?!none)[^"]*"', f'fill="{color}"', svg_text)
        svg_text = re.sub(r"fill='(?!none)[^']*'", f"fill='{color}'", svg_text)
        if "stroke=" not in svg_text:
            svg_text = svg_text.replace("<svg", f'<svg stroke="{color}"', 1)
        if "fill=" not in svg_text:
            svg_text = svg_text.replace("<svg", '<svg fill="none"', 1)
        return svg_text

    @classmethod
    def make_svg_icon(cls, icon_name: str, color: str, size: int = 20) -> QIcon:
        icon_file = cls.icon_path(icon_name)
        if not icon_file.exists():
            return QIcon()
        try:
            svg_text = icon_file.read_text(encoding="utf-8", errors="ignore")
            svg_text = cls._normalize_svg(svg_text, color)
            renderer = QSvgRenderer(svg_text.encode("utf-8"))
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing, True)
            renderer.render(painter, pixmap.rect().adjusted(2, 2, -2, -2))
            painter.end()
            return QIcon(pixmap)
        except Exception:
            return QIcon(str(icon_file))

    @classmethod
    def make_svg_icon_original(cls, icon_name: str, size: int = 20) -> QIcon:
        icon_file = cls.icon_path(icon_name)
        if not icon_file.exists():
            return QIcon()
        try:
            svg_text = icon_file.read_text(encoding="utf-8", errors="ignore")
            renderer = QSvgRenderer(svg_text.encode("utf-8"))
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing, True)
            renderer.render(painter, pixmap.rect().adjusted(2, 2, -2, -2))
            painter.end()
            return QIcon(pixmap)
        except Exception:
            return QIcon(str(icon_file))

    def set_button_icon(self, button: QPushButton, icon_name: str, color: str, size: int = 18) -> None:
        icon = self.make_svg_icon(icon_name, color, size + 2)
        if not icon.isNull():
            button.setIcon(icon)
            button.setIconSize(QSize(size, size))

    def set_button_icon_original(self, button: QPushButton, icon_name: str, size: int = 18) -> None:
        icon = self.make_svg_icon_original(icon_name, size + 2)
        if not icon.isNull():
            button.setIcon(icon)
            button.setIconSize(QSize(size, size))

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 16, 18, 14)
        main_layout.setSpacing(14)

        main_layout.addLayout(self.create_header())
        main_layout.addLayout(self.create_stat_cards())
        main_layout.addWidget(self.create_case_card())
        main_layout.addLayout(self.create_action_bar())
        main_layout.addWidget(self.create_report_result_card(), 1)

        self.set_common_style()
        self.update_case_card()
        self.update_buttons_state()

        self.btn_refresh.clicked.connect(self.refresh_analysis_results)
        self.btn_generate.clicked.connect(self.generate_report)
        self.btn_open_report.clicked.connect(self.open_report)
        self.btn_open_report_dir.clicked.connect(self.open_report_dir)

    def create_header(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.current_case_pill = QLabel("未选择病例")
        self.current_case_pill.setObjectName("ReportCurrentCasePill")
        self.current_case_pill.setAlignment(Qt.AlignCenter)

        layout.addStretch()
        layout.addWidget(self.current_case_pill, 0, Qt.AlignRight | Qt.AlignVCenter)
        return layout

    def create_stat_cards(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self.sample_no_stat = self.create_stat_card("样本号", "-", "detail_sample.svg", "info")
        self.test_date_stat = self.create_stat_card("检查日期", "-", "detail_date.svg", "cyan")
        self.completed_stat = self.create_stat_card("已完成蛋白数", "0 / 0", "detail_complete.svg", "success")
        self.report_status_stat = self.create_stat_card("报告状态", "未生成", "detail_report.svg", "purple")

        layout.addWidget(self.sample_no_stat["card"], 1)
        layout.addWidget(self.test_date_stat["card"], 1)
        layout.addWidget(self.completed_stat["card"], 1)
        layout.addWidget(self.report_status_stat["card"], 1)
        return layout

    def create_stat_card(self, title: str, value: str, icon_name: str, tone: str):
        color_map = {
            "info": self.theme.get("primary", "#1769E0"),
            "cyan": "#0EA5A6",
            "success": self.theme.get("success", "#16A34A"),
            "purple": self.theme.get("purple", "#7C3AED"),
            "warning": self.theme.get("warning", "#F59E0B"),
        }
        color = color_map.get(tone, self.theme.get("primary", "#1769E0"))

        card = QFrame()
        card.setObjectName("ReportStatCard")
        card.setMinimumHeight(92)
        card.setMaximumHeight(92)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        apply_shadow(card)

        row = QHBoxLayout(card)
        row.setContentsMargins(16, 14, 16, 14)
        row.setSpacing(14)

        icon_box = QFrame()
        icon_box.setObjectName(f"ReportStatIconBox_{tone}")
        icon_box.setFixedSize(48, 48)
        icon_layout = QHBoxLayout(icon_box)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setPixmap(self.make_svg_icon(icon_name, color, 28).pixmap(28, 28))
        icon_layout.addWidget(icon_label)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("ReportStatTitle")
        value_label = QLabel(value)
        value_label.setObjectName(f"ReportStatValue_{tone}")
        value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text_layout.addStretch(1)
        text_layout.addWidget(title_label)
        text_layout.addWidget(value_label)
        text_layout.addStretch(1)

        row.addWidget(icon_box, 0, Qt.AlignVCenter)
        row.addLayout(text_layout, 1)
        return {"card": card, "title": title_label, "value": value_label, "icon": icon_label, "color": color}

    def create_section_title(self, title: str, icon_name: str = "report.svg") -> QWidget:
        box = QWidget()
        box.setObjectName("ReportSectionTitleBox")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        icon = QLabel()
        icon.setFixedSize(22, 22)
        icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(self.make_svg_icon(icon_name, self.theme.get("primary", "#1769E0"), 20).pixmap(20, 20))

        label = QLabel(title)
        label.setObjectName("ReportSectionTitle")
        layout.addWidget(icon, 0, Qt.AlignVCenter)
        layout.addWidget(label, 0, Qt.AlignVCenter)
        layout.addStretch()
        return box

    def create_case_card(self) -> QFrame:
        """当前病例信息卡片。

        这里改为和病例详情页一致的 3 列信息布局，避免原来“字段块”
        背景、字号和换行不统一导致页面看起来发散。
        """
        card = QFrame()
        card.setObjectName("ReportInfoCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(self.create_section_title("当前病例信息", "section_basic.svg"))

        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(8)

        self.case_no_label = QLabel("-")
        self.patient_name_label = QLabel("-")
        self.sample_no_label = QLabel("-")
        self.test_date_label = QLabel("-")
        self.report_path_label = QLabel("-")
        self.report_path_label.setWordWrap(False)

        self.add_info_row3(
            grid,
            0,
            ("病历编号", self.case_no_label),
            ("姓名", self.patient_name_label),
            ("样本号", self.sample_no_label),
        )
        self.add_info_row3(
            grid,
            1,
            ("检测日期", self.test_date_label),
            ("报告路径", self.report_path_label),
            ("", QLabel("")),
        )

        layout.addLayout(grid)
        return card

    def add_info_row3(self, layout: QGridLayout, row: int, item1, item2, item3) -> None:
        label1, widget1 = item1
        label2, widget2 = item2
        label3, widget3 = item3

        layout.addWidget(self.make_name_label(label1), row, 0)
        layout.addWidget(self.make_value_widget(widget1), row, 1)
        layout.addWidget(self.make_name_label(label2), row, 2)
        layout.addWidget(self.make_value_widget(widget2), row, 3)
        layout.addWidget(self.make_name_label(label3), row, 4)
        layout.addWidget(self.make_value_widget(widget3), row, 5)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)
        layout.setColumnStretch(5, 1)

    def make_name_label(self, text: str) -> QLabel:
        label = QLabel(f"{text}：" if text else "")
        label.setObjectName("ReportNameLabel")
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return label

    def make_value_widget(self, widget: QLabel) -> QLabel:
        widget.setObjectName("ReportValueLabel")
        widget.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return widget

    def create_action_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.btn_refresh = QPushButton("刷新分析结果")
        self.btn_refresh.setObjectName("ReportNeutralButton")
        self.set_button_icon_original(self.btn_refresh, "detail_refresh.svg", 17)

        self.btn_generate = QPushButton("生成 PDF 报告")
        self.btn_generate.setObjectName("ReportPrimaryButton")
        self.set_button_icon(self.btn_generate, "report_s.svg", "#FFFFFF", 17)

        self.btn_open_report = QPushButton("打开报告")
        self.btn_open_report.setObjectName("ReportNeutralButton")
        self.set_button_icon_original(self.btn_open_report, "detail_open_report.svg", 17)

        self.btn_open_report_dir = QPushButton("打开报告目录")
        self.btn_open_report_dir.setObjectName("ReportNeutralButton")
        self.set_button_icon_original(self.btn_open_report_dir, "detail_open_folder.svg", 17)

        for button in [self.btn_refresh, self.btn_generate, self.btn_open_report, self.btn_open_report_dir]:
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(40)
            button.setMinimumWidth(118)
            button.setAutoDefault(False)
            button.setDefault(False)
            layout.addWidget(button)
        layout.addStretch()
        return layout

    def create_report_result_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("ReportResultCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        header.addWidget(self.create_section_title("蛋白分析结果", "section_analysis.svg"), 0, Qt.AlignVCenter)
        self.info_label = QLabel("请先在病例管理中双击选择病例，再生成报告。")
        self.info_label.setObjectName("ReportProgressLabel")
        header.addWidget(self.info_label, 1, Qt.AlignVCenter)
        layout.addLayout(header)

        self.analysis_table = QTableWidget()
        self.analysis_table.setObjectName("ReportResultTable")
        self.analysis_table.setColumnCount(9)
        self.analysis_table.setHorizontalHeaderLabels([
            "蛋白名称", "表达部位", "视野数", "精子总数", "共定位数", "标定率(%)", "荧光强度", "状态", "分析时间"
        ])
        self.analysis_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.analysis_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.analysis_table.setAlternatingRowColors(True)
        self.analysis_table.verticalHeader().setVisible(False)
        self.analysis_table.verticalHeader().setDefaultSectionSize(38)
        self.analysis_table.setShowGrid(True)
        self.analysis_table.setFocusPolicy(Qt.NoFocus)

        header_view = self.analysis_table.horizontalHeader()
        header_view.setFixedHeight(40)
        header_view.setSectionResizeMode(QHeaderView.Stretch)
        header_view.setSectionResizeMode(0, QHeaderView.Fixed)
        header_view.setSectionResizeMode(1, QHeaderView.Fixed)
        header_view.setSectionResizeMode(2, QHeaderView.Fixed)
        header_view.setSectionResizeMode(7, QHeaderView.Fixed)
        self.analysis_table.setColumnWidth(0, 92)
        self.analysis_table.setColumnWidth(1, 84)
        self.analysis_table.setColumnWidth(2, 72)
        self.analysis_table.setColumnWidth(7, 96)

        layout.addWidget(self.analysis_table, 1)
        return card

    def set_common_style(self):
        primary = self.theme.get("primary", "#1769E0")
        primary_hover = self.theme.get("primary_hover", "#0F5ED7")
        self.setStyleSheet(f"""
            QWidget {{
                color: #1F2D3D;
                font-size: 13px;
            }}

            QFrame#ReportInfoCard QWidget,
            QFrame#ReportInfoCard QLabel,
            QFrame#ReportResultCard QWidget,
            QFrame#ReportResultCard QLabel {{
                background: transparent;
            }}

            QLabel#ReportCurrentCasePill {{
                min-height: 28px;
                padding: 7px 14px;
                border: 1px solid #DDE6F2;
                border-radius: 6px;
                background-color: #F3F7FC;
                color: #5E6B7A;
                font-weight: 500;
            }}

            QFrame#ReportStatCard,
            QFrame#ReportInfoCard,
            QFrame#ReportResultCard {{
                background-color: #FFFFFF;
                border: 1px solid #DDE6F2;
                border-radius: 10px;
            }}

            QFrame#ReportStatIconBox_info {{
                background-color: #EAF2FF;
                border: 1px solid #BCD7FF;
                border-radius: 12px;
            }}
            QFrame#ReportStatIconBox_cyan {{
                background-color: #E6FAFA;
                border: 1px solid #B7ECEC;
                border-radius: 12px;
            }}
            QFrame#ReportStatIconBox_success {{
                background-color: #EAF8EF;
                border: 1px solid #BDEACB;
                border-radius: 12px;
            }}
            QFrame#ReportStatIconBox_purple {{
                background-color: #F2ECFF;
                border: 1px solid #D8C8FF;
                border-radius: 12px;
            }}

            QLabel#ReportStatTitle {{
                color: #8A97A8;
                font-size: 12px;
                font-weight: 400;
            }}
            QLabel#ReportStatValue_info,
            QLabel#ReportStatValue_cyan,
            QLabel#ReportStatValue_success,
            QLabel#ReportStatValue_purple,
            QLabel#ReportStatValue_warning {{
                color: #102A43;
                font-size: 22px;
                font-weight: 700;
            }}
            QLabel#ReportStatValue_success {{ color: #0F8A3B; }}
            QLabel#ReportStatValue_purple {{ color: #7C3AED; }}
            QLabel#ReportStatValue_warning {{ color: #F59E0B; }}

            QLabel#ReportSectionTitle {{
                color: #102A43;
                font-size: 15px;
                font-weight: 700;
            }}
            QLabel#ReportNameLabel {{
                color: #5E6B7A;
                font-size: 13px;
                font-weight: 400;
                min-width: 72px;
            }}
            QLabel#ReportValueLabel {{
                color: #1F2D3D;
                font-size: 13px;
                font-weight: 400;
                min-height: 22px;
            }}
            QLabel#ReportProgressLabel {{
                color: #5E6B7A;
                font-size: 13px;
            }}

            QPushButton#ReportPrimaryButton {{
                min-height: 38px;
                max-height: 38px;
                padding: 0px 15px;
                border: 1px solid {primary};
                border-radius: 6px;
                background-color: {primary};
                color: #FFFFFF;
                font-weight: 500;
            }}
            QPushButton#ReportPrimaryButton:hover {{
                background-color: {primary_hover};
                border-color: {primary_hover};
                color: #FFFFFF;
            }}
            QPushButton#ReportPrimaryButton:pressed {{
                background-color: #0B4DB5;
                border-color: #0B4DB5;
                color: #FFFFFF;
            }}
            QPushButton#ReportPrimaryButton:disabled {{
                background-color: #EEF4FB;
                border-color: #DDE6F2;
                color: #9AA6B2;
            }}

            QPushButton#ReportNeutralButton {{
                min-height: 38px;
                max-height: 38px;
                padding: 0px 15px;
                border: 1px solid #DDE6F2;
                border-radius: 6px;
                background-color: #FFFFFF;
                color: #1F2D3D;
                font-weight: 500;
            }}
            QPushButton#ReportNeutralButton:hover {{
                background-color: #F2F7FF;
                border-color: #BCD7FF;
                color: {primary};
            }}
            QPushButton#ReportNeutralButton:disabled {{
                background-color: #F8FAFD;
                border-color: #E8EEF6;
                color: #9AA6B2;
            }}
            QPushButton#ReportNeutralButton:disabled:hover {{
                background-color: #F8FAFD;
                border-color: #E8EEF6;
                color: #9AA6B2;
            }}

            QTableWidget#ReportResultTable {{
                background-color: #FFFFFF;
                alternate-background-color: #F8FAFD;
                gridline-color: #DDE6F2;
                border: 1px solid #DDE6F2;
                border-radius: 6px;
                selection-background-color: #DCEBFF;
                selection-color: #1F2D3D;
                outline: none;
            }}
            QTableWidget#ReportResultTable::item {{
                padding: 6px 5px;
            }}
            QTableWidget#ReportResultTable::item:selected {{
                background-color: #DCEBFF;
                color: #1F2D3D;
                outline: none;
            }}
            QHeaderView::section {{
                background-color: #EEF4FB;
                color: #1F2D3D;
                font-weight: 700;
                border: none;
                border-right: 1px solid #DDE6F2;
                border-bottom: 1px solid #DDE6F2;
                padding: 6px 5px;
                min-height: 30px;
            }}
        """)

    # ------------------------------------------------------------------
    # 数据刷新
    # ------------------------------------------------------------------
    def reload_config(self):
        self.config.load()
        self.config.ensure_default_config()
        if self.current_case:
            case_id = self.current_case.get("id")
            if case_id:
                fresh_case = self.database.get_case(case_id)
                if fresh_case:
                    self.current_case = fresh_case
                    self.current_report_path = str(fresh_case.get("report_path", "") or "")
        self.update_case_card()
        self.refresh_analysis_results()

    def set_case(self, case_data: dict):
        self.current_case = case_data
        self.current_report_path = str(case_data.get("report_path", "") or "")
        self.update_case_card()
        self.refresh_analysis_results()

    @staticmethod
    def v(value, default: str = "-") -> str:
        text = str(value or "").strip()
        return text if text else default

    @staticmethod
    def short_path(path_text: str, max_len: int = 58) -> str:
        text = str(path_text or "").strip()
        if not text or text == "-" or len(text) <= max_len:
            return text or "-"
        prefix = text[:18]
        suffix = text[-(max_len - 21):]
        return f"{prefix}...{suffix}"

    def update_case_card(self):
        case = self.current_case or {}
        case_no = self.v(case.get("case_no"), "未选择")
        patient_name = self.v(case.get("patient_name"))
        sample_no = self.v(case.get("sample_no"))
        test_date = self.v(case.get("test_date"))
        report_path = self.v(case.get("report_path"))

        if not self.current_case:
            self.current_case_pill.setText("未选择病例")
        else:
            self.current_case_pill.setText(
                f"当前病例：{case_no}    姓名：{patient_name}    样本号：{sample_no}"
            )

        self.case_no_label.setText(case_no)
        self.patient_name_label.setText(patient_name)
        self.sample_no_label.setText(sample_no)
        self.test_date_label.setText(test_date)
        self.report_path_label.setText(self.short_path(report_path))
        self.report_path_label.setToolTip(report_path if report_path != "-" else "")

        self.sample_no_stat["value"].setText(sample_no)
        self.test_date_stat["value"].setText(test_date)
        self.report_status_stat["value"].setText("可用" if case.get("report_path") else "未生成")
        self.report_status_stat["value"].setObjectName("ReportStatValue_purple")
        self.report_status_stat["value"].style().unpolish(self.report_status_stat["value"])
        self.report_status_stat["value"].style().polish(self.report_status_stat["value"])
        self.update_buttons_state()

    def refresh_analysis_results(self):
        self.analysis_table.setRowCount(0)
        if not self.current_case:
            self.info_label.setText("请先在病例管理中双击选择病例。")
            self.completed_stat["value"].setText("0 / 0")
            self.update_buttons_state()
            return

        case_id = self.current_case.get("id")
        if not case_id:
            self.info_label.setText("当前病例缺少数据库 ID。")
            self.update_buttons_state()
            return

        try:
            rows = self.database.get_protein_analysis_by_case(case_id)
        except Exception as e:
            self.info_label.setText(f"读取分析结果失败：{e}")
            self.update_buttons_state()
            return

        display_rows = self.build_report_display_rows(rows)
        self._report_rows = display_rows
        self.analysis_table.setRowCount(len(display_rows))

        done_count = 0
        for row_index, row in enumerate(display_rows):
            is_done = bool(row.get("has_result"))
            if is_done:
                done_count += 1
                values = [
                    row.get("protein_name", ""),
                    row.get("protein_part", ""),
                    row.get("total_fields", 0),
                    row.get("total_sperm_count", 0),
                    row.get("positive_count", 0),
                    self._fmt(row.get("expression_rate", 0)),
                    self._fmt(row.get("mean_intensity", 0)),
                    "已完成",
                    row.get("created_at", ""),
                ]
            else:
                values = [
                    row.get("protein_name", ""),
                    row.get("protein_part", ""),
                    "-", "-", "-", "-", "-", "未检测", "-",
                ]

            for col, value in enumerate(values):
                if col == 7:
                    self.analysis_table.setCellWidget(row_index, col, self.create_status_badge(str(value)))
                    continue
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                if col == 8:
                    item.setToolTip(str(value))
                self.analysis_table.setItem(row_index, col, item)
            self.analysis_table.setRowHeight(row_index, 38)

        total_count = len(display_rows)
        pending_count = max(total_count - done_count, 0)
        self.completed_stat["value"].setText(f"{done_count} / {total_count}")
        self.info_label.setText(f"报告检测进度：已完成 {done_count} / {total_count}，未检测 {pending_count}")
        self.update_buttons_state()

    def create_status_badge(self, text: str) -> QWidget:
        status = "success" if text in ["已完成", "完成"] else "warning"
        wrapper = QWidget()
        wrapper.setObjectName("ReportStatusCell")
        wrapper.setStyleSheet("QWidget#ReportStatusCell { background: transparent; border: none; }")
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        label = QLabel(text)
        label.setObjectName(f"ReportStatusBadge_{status}")
        label.setAlignment(Qt.AlignCenter)
        label.setFixedSize(66, 24)
        label.setStyleSheet("""
            QLabel#ReportStatusBadge_success {
                background-color: #EAF8EF;
                border: 1px solid #BFE8CD;
                border-radius: 12px;
                color: #0F8A3B;
                font-weight: 700;
                padding: 0px;
            }
            QLabel#ReportStatusBadge_warning {
                background-color: #FFF7E8;
                border: 1px solid #FFDCA8;
                border-radius: 12px;
                color: #D97706;
                font-weight: 700;
                padding: 0px;
            }
        """)
        layout.addStretch(1)
        layout.addWidget(label, 0, Qt.AlignCenter)
        layout.addStretch(1)
        return wrapper

    def update_buttons_state(self):
        has_case = bool(self.current_case)
        has_report = bool(self.current_report_path)
        for button in [self.btn_refresh, self.btn_generate, self.btn_open_report_dir]:
            button.setEnabled(has_case)
            button.setCursor(Qt.PointingHandCursor if button.isEnabled() else Qt.ArrowCursor)
        self.btn_open_report.setEnabled(has_report)
        self.btn_open_report.setCursor(Qt.PointingHandCursor if self.btn_open_report.isEnabled() else Qt.ArrowCursor)

        if has_report:
            self.set_button_icon_original(self.btn_open_report, "detail_open_report.svg", 17)
        else:
            self.set_button_icon(self.btn_open_report, "detail_open_report.svg", "#B8C2CC", 17)

    # ------------------------------------------------------------------
    # 表格行构建
    # ------------------------------------------------------------------
    def sort_analysis_rows_by_config(self, rows):
        rows = list(rows or [])
        protein_items = []
        try:
            protein_items = self.config.get_protein_items()
        except Exception:
            protein_items = []

        order_map = {}
        name_to_key = {}
        for index, item in enumerate(protein_items):
            key = str(item.get("key", "") or "").strip()
            name = str(item.get("name", "") or "").strip()
            if key:
                order_map[key] = index
                name_to_key[key] = key
            if name:
                name_to_key[name] = key or name
                if name not in order_map:
                    order_map[name] = index

        def row_sort_key(row):
            protein_name = str(row.get("protein_name", "") or "").strip()
            try:
                protein_key = self.config.normalize_protein_key(protein_name)
            except Exception:
                protein_key = name_to_key.get(protein_name, protein_name)
            index = order_map.get(protein_key, order_map.get(protein_name, 9999))
            created_at = str(row.get("created_at", "") or "")
            return (index, created_at, protein_name)

        return sorted(rows, key=row_sort_key)

    def build_report_display_rows(self, analysis_rows):
        analysis_rows = list(analysis_rows or [])
        analysis_map = {}
        for row in analysis_rows:
            protein_name = str(row.get("protein_name", "") or "").strip()
            if not protein_name:
                continue
            protein_key = self.resolve_protein_key(protein_name)
            if protein_key:
                analysis_map[protein_key] = row
            analysis_map[protein_name] = row

        display_rows = []
        try:
            protein_items = self.config.get_protein_items()
        except Exception:
            protein_items = []

        for item in protein_items:
            key = str(item.get("key", "") or "").strip()
            name = str(item.get("name", key) or key).strip()
            part = str(item.get("part", "") or "").strip()
            row = None
            if key:
                row = analysis_map.get(key)
            if row is None and name:
                row = analysis_map.get(name)
            if row:
                display_row = dict(row)
                display_row["has_result"] = True
                display_row["protein_name"] = display_row.get("protein_name", name) or name
                display_row["protein_part"] = display_row.get("protein_part", part) or part
            else:
                display_row = {
                    "has_result": False,
                    "protein_key": key,
                    "protein_name": name,
                    "protein_part": part,
                    "status": "未检测",
                }
            display_rows.append(display_row)

        if display_rows:
            return display_rows

        rows = self.sort_analysis_rows_by_config(analysis_rows)
        for row in rows:
            row = dict(row)
            row["has_result"] = True
            display_rows.append(row)
        return display_rows

    def resolve_protein_key(self, protein_name: str) -> str:
        protein_name = str(protein_name or "").strip()
        if not protein_name:
            return ""
        try:
            return self.config.normalize_protein_key(protein_name)
        except Exception:
            pass
        try:
            for item in self.config.get_protein_items():
                key = str(item.get("key", "") or "").strip()
                name = str(item.get("name", "") or "").strip()
                if protein_name == key or protein_name == name:
                    return key
        except Exception:
            pass
        return protein_name

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def generate_report(self):
        if not self.current_case:
            QMessageBox.information(self, "提示", "请先在病例管理中双击选择病例。")
            return

        case_id = self.current_case.get("id")
        analysis_rows = self.database.get_protein_analysis_by_case(case_id)
        if not analysis_rows:
            QMessageBox.warning(self, "提示", "当前病例暂无蛋白分析结果，无法生成报告。")
            return

        report_dir = self.config.get_report_dir()
        logo_path = self.config.get("Report", "logo_path", "")
        try:
            generator = ReportGenerator(
                database=self.database,
                report_dir=str(report_dir),
                logo_path=logo_path,
            )
            report_path = generator.generate_case_report(case_id)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"生成报告失败：\n{e}")
            return

        self.current_report_path = report_path
        refreshed_case = self.database.get_case(case_id)
        if refreshed_case:
            self.current_case = refreshed_case
        else:
            self.current_case["report_path"] = report_path
        self.update_case_card()
        self.refresh_analysis_results()
        QMessageBox.information(self, "成功", f"报告生成成功：\n{report_path}")

    def open_report(self):
        if not self.current_report_path:
            QMessageBox.information(self, "提示", "当前病例还没有生成报告。")
            return
        report_path = Path(self.current_report_path)
        if not report_path.exists():
            QMessageBox.warning(self, "提示", f"报告文件不存在：\n{report_path}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(report_path)))

    def open_report_dir(self):
        if self.current_report_path:
            folder = Path(self.current_report_path).parent
        else:
            folder = self.config.get_report_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    @staticmethod
    def _fmt(value):
        try:
            return f"{float(value):.2f}"
        except Exception:
            return str(value)
