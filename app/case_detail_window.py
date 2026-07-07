from pathlib import Path
import re

from PySide6.QtCore import Qt, Signal, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap, QPainter, QColor
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
from app.batch_analysis_dialog import BatchAnalysisDialog
from app.theme import DEFAULT_THEME_KEY, get_theme
from app.ui_components import apply_shadow


class CaseDetailWindow(QWidget):
    start_analysis_requested = Signal(dict)
    report_requested = Signal(dict)

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.database = database
        self.config = ConfigManager()
        self.config.ensure_default_config()
        self.current_case = None
        self.theme = get_theme(DEFAULT_THEME_KEY)
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
        """尽量把不同来源 SVG 统一染色，避免 currentColor/固定色导致 Qt 不显示。"""
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
            # 留出 2px 内边距，统一视觉大小，避免不同 viewBox 图标忽大忽小。
            renderer.render(painter, pixmap.rect().adjusted(2, 2, -2, -2))
            painter.end()
            return QIcon(pixmap)
        except Exception:
            return QIcon(str(icon_file))

    @classmethod
    def make_svg_icon_original(cls, icon_name: str, size: int = 20) -> QIcon:
        """按 SVG 文件原始颜色渲染图标。

        用于按钮“可用状态”：显示设计师准备好的原图颜色。
        强制染色只用于禁用状态或统计卡片等需要主题色的场景。
        """
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

    def reload_config(self):
        self.config.load()
        self.config.ensure_default_config()
        self.refresh_analysis_table()
        self.update_action_buttons_state()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 16, 18, 14)
        main_layout.setSpacing(14)

        main_layout.addLayout(self.create_header())
        main_layout.addLayout(self.create_stat_cards())
        main_layout.addWidget(self.create_basic_card())
        main_layout.addWidget(self.create_sample_card())
        main_layout.addLayout(self.create_action_bar())
        main_layout.addWidget(self.create_analysis_card(), 1)

        self.set_common_style()

        self.btn_refresh.clicked.connect(self.refresh_detail)
        self.btn_start_analysis.clicked.connect(self.start_analysis)
        self.btn_batch_analysis.clicked.connect(self.open_batch_analysis)
        self.btn_report.clicked.connect(self.go_report)
        self.btn_open_report.clicked.connect(self.open_report)
        self.btn_open_workspace.clicked.connect(self.open_workspace)

        self.update_action_buttons_state()

    def create_header(self) -> QHBoxLayout:
        """顶部当前病例提示条。

        主窗口上方已经有“病例详情”页面标题，因此这里不再重复显示
        图标、标题和说明，只保留右侧当前病例信息，避免页面顶部信息重复。
        """
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.status_label = QLabel("未选择病例")
        self.status_label.setObjectName("DetailCurrentCasePill")
        self.status_label.setAlignment(Qt.AlignCenter)

        layout.addStretch()
        layout.addWidget(self.status_label, 0, Qt.AlignRight | Qt.AlignVCenter)
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
        """病例详情顶部统计卡片。

        与病例管理页的统计卡片保持同一套尺寸逻辑：
        - 卡片高度 92px
        - 卡片内边距 16/14/16/14
        - 图标块 48px
        - 卡片间距由 create_stat_cards() 统一设置为 14px
        这样在“病例管理 / 病例详情”之间切换时，顶部卡片不会出现跳动感。
        """
        color_map = {
            "info": self.theme.get("primary", "#1769E0"),
            "cyan": "#0EA5A6",
            "success": self.theme.get("success", "#16A34A"),
            "purple": self.theme.get("purple", "#7C3AED"),
            "warning": self.theme.get("warning", "#F59E0B"),
        }
        color = color_map.get(tone, self.theme.get("primary", "#1769E0"))

        card = QFrame()
        card.setObjectName("DetailStatCard")
        card.setMinimumHeight(92)
        card.setMaximumHeight(92)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        apply_shadow(card)

        row = QHBoxLayout(card)
        row.setContentsMargins(16, 14, 16, 14)
        row.setSpacing(14)

        icon_box = QFrame()
        icon_box.setObjectName(f"DetailStatIconBox_{tone}")
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
        title_label.setObjectName("DetailStatTitle")
        title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        value_label = QLabel(value)
        value_label.setObjectName(f"DetailStatValue_{tone}")
        value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        # 保持和病例管理页一致：标题一行，数值一行，并在垂直方向居中。
        text_layout.addStretch(1)
        text_layout.addWidget(title_label)
        text_layout.addWidget(value_label)
        text_layout.addStretch(1)

        row.addWidget(icon_box, 0, Qt.AlignVCenter)
        row.addLayout(text_layout, 1)
        return {"card": card, "title": title_label, "value": value_label, "icon": icon_label, "color": color}

    def create_basic_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("DetailInfoCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(self.create_section_title("基本信息", "section_basic.svg", self.theme.get("primary", "#1769E0")))

        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(8)

        self.case_no_label = QLabel("-")
        self.patient_name_label = QLabel("-")
        self.sample_no_label = QLabel("-")
        self.age_label = QLabel("-")
        self.sex_label = QLabel("-")
        self.occupation_label = QLabel("-")
        self.phone_label = QLabel("-")
        self.test_date_label = QLabel("-")
        self.report_path_label = QLabel("-")
        self.report_path_label.setWordWrap(True)

        self.add_info_row3(
            grid,
            0,
            ("病历号", self.case_no_label),
            ("姓名", self.patient_name_label),
            ("样本号", self.sample_no_label),
        )
        self.add_info_row3(
            grid,
            1,
            ("年龄", self.age_label),
            ("性别", self.sex_label),
            ("职业", self.occupation_label),
        )
        self.add_info_row3(
            grid,
            2,
            ("联系方式", self.phone_label),
            ("检查日期", self.test_date_label),
            ("报告路径", self.report_path_label),
        )
        layout.addLayout(grid)
        return card

    def create_sample_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("DetailInfoCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(self.create_section_title("样本信息", "section_sample.svg", self.theme.get("primary", "#1769E0")))

        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(7)

        self.collect_time_label = QLabel("-")
        self.receive_time_label = QLabel("-")
        self.semen_volume_label = QLabel("-")
        self.ph_label = QLabel("-")
        self.appearance_label = QLabel("-")
        self.color_label = QLabel("-")
        self.liquefaction_time_label = QLabel("-")
        self.liquefaction_status_label = QLabel("-")
        self.agglutination_label = QLabel("-")
        self.viscosity_label = QLabel("-")
        self.collect_method_label = QLabel("-")
        self.abstinence_days_label = QLabel("-")
        self.smell_label = QLabel("-")
        self.test_temperature_label = QLabel("-")
        self.collect_location_label = QLabel("-")
        self.collect_complete_label = QLabel("-")
        self.dead_sperm_label = QLabel("-")
        self.sperm_concentration_label = QLabel("-")
        self.sperm_total_label = QLabel("-")
        self.forward_motility_label = QLabel("-")
        self.total_motility_label = QLabel("-")

        self.add_info_row3(grid, 0, ("取样时间", self.collect_time_label), ("送检时间", self.receive_time_label), ("精液量", self.semen_volume_label))
        self.add_info_row3(grid, 1, ("PH值", self.ph_label), ("外观", self.appearance_label), ("颜色", self.color_label))
        self.add_info_row3(grid, 2, ("液化时间", self.liquefaction_time_label), ("液化状态", self.liquefaction_status_label), ("凝集程度", self.agglutination_label))
        self.add_info_row3(grid, 3, ("黏稠度", self.viscosity_label), ("取样方式", self.collect_method_label), ("禁欲时间", self.abstinence_days_label))
        self.add_info_row3(grid, 4, ("气味", self.smell_label), ("检测温度", self.test_temperature_label), ("取样地点", self.collect_location_label))
        self.add_info_row3(grid, 5, ("取样完整", self.collect_complete_label), ("死精子症", self.dead_sperm_label), ("精子浓度", self.sperm_concentration_label))
        self.add_info_row3(grid, 6, ("精子总数", self.sperm_total_label), ("前向运动", self.forward_motility_label), ("总活力", self.total_motility_label))

        layout.addLayout(grid)
        return card

    def create_section_title(self, title: str, icon_name: str = "", color: str = "#1769E0") -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        if icon_name:
            icon_label = QLabel()
            icon_label.setFixedSize(22, 22)
            icon_label.setAlignment(Qt.AlignCenter)
            icon_label.setPixmap(self.make_svg_icon(icon_name, color, 20).pixmap(20, 20))
            row.addWidget(icon_label)
        label = QLabel(title)
        label.setObjectName("DetailSectionTitle")
        row.addWidget(label)
        row.addStretch()
        return widget

    def create_action_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.btn_refresh = QPushButton("刷新详情")
        self.btn_refresh.setObjectName("DetailNeutralButton")

        self.btn_start_analysis = QPushButton("开始蛋白分析")
        self.btn_start_analysis.setObjectName("DetailPrimaryButton")

        self.btn_batch_analysis = QPushButton("批量蛋白分析")
        self.btn_batch_analysis.setObjectName("DetailNeutralButton")

        self.btn_report = QPushButton("进入报告管理")
        self.btn_report.setObjectName("DetailNeutralButton")

        self.btn_open_report = QPushButton("打开报告")
        self.btn_open_report.setObjectName("DetailNeutralButton")

        self.btn_open_workspace = QPushButton("打开病例工作目录")
        self.btn_open_workspace.setObjectName("DetailNeutralButton")

        for button in [
            self.btn_refresh,
            self.btn_start_analysis,
            self.btn_batch_analysis,
            self.btn_report,
            self.btn_open_report,
            self.btn_open_workspace,
        ]:
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(40)
            button.setMinimumWidth(118)
            layout.addWidget(button)

        self.update_action_button_icons()
        layout.addStretch()
        return layout

    def update_action_button_icons(self) -> None:
        """根据按钮可用状态刷新图标。

        可用状态：使用 assets/icons 中 SVG 的原始颜色。
        不可用状态：统一转成浅灰，避免看起来像可点击。
        """
        disabled_color = "#B8C2CC"

        action_icons = [
            (self.btn_refresh, "detail_refresh.svg"),
            (self.btn_batch_analysis, "detail_batch.svg"),
            (self.btn_report, "detail_report_manage.svg"),
            (self.btn_open_report, "detail_open_report.svg"),
            (self.btn_open_workspace, "detail_open_folder.svg"),
        ]

        for button, icon_name in action_icons:
            if button.isEnabled():
                self.set_button_icon_original(button, icon_name, 17)
            else:
                self.set_button_icon(button, icon_name, disabled_color, 17)

        if self.btn_start_analysis.isEnabled():
            self.set_button_icon_original(self.btn_start_analysis, "detail_start_s.svg", 17)
        else:
            self.set_button_icon_original(self.btn_start_analysis, "detail_start_disabled.svg", 17)

    def create_analysis_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("DetailInfoCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_widget = self.create_section_title("蛋白检测状态总览", "section_analysis.svg", self.theme.get("primary", "#1769E0"))
        self.analysis_summary_label = QLabel("未选择病例")
        self.analysis_summary_label.setObjectName("DetailSummaryLabel")
        title_row.addWidget(title_widget)
        title_row.addWidget(self.analysis_summary_label, 1)
        layout.addLayout(title_row)

        self.analysis_table = QTableWidget()
        self.analysis_table.setColumnCount(10)
        self.analysis_table.setHorizontalHeaderLabels([
            "蛋白名称", "表达部位", "检测状态", "视野数", "精子总数", "共定位数", "标定率(%)", "荧光强度", "分析时间", "输出目录",
        ])
        self.analysis_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.analysis_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.analysis_table.setAlternatingRowColors(True)
        self.analysis_table.verticalHeader().setVisible(False)
        self.analysis_table.verticalHeader().setDefaultSectionSize(38)
        self.analysis_table.setWordWrap(False)
        self.analysis_table.setShowGrid(True)

        header = self.analysis_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setSectionResizeMode(QHeaderView.Stretch)

        # 关键列使用固定宽度，避免全屏/窗口化时被 Stretch 算法压得过窄。
        # “检测状态”列里放的是状态标签控件，QHeaderView.ResizeToContents
        # 不会稳定参考 setCellWidget() 的真实宽度，所以需要显式给足空间。
        fixed_widths = {
            0: 86,   # 蛋白名称
            1: 74,   # 表达部位
            2: 96,   # 检测状态
            3: 62,   # 视野数
            8: 132,  # 分析时间
        }
        for column, width in fixed_widths.items():
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            self.analysis_table.setColumnWidth(column, width)

        header.setSectionResizeMode(9, QHeaderView.Stretch)

        layout.addWidget(self.analysis_table, 1)
        return card

    # ------------------------------------------------------------------
    # 样式
    # ------------------------------------------------------------------
    def set_common_style(self):
        t = self.theme
        self.setStyleSheet(f"""
            QWidget {{
                background: transparent;
            }}
            QLabel#DetailPageTitle {{
                color: {t.get('title', '#102A43')};
                font-size: 22px;
                font-weight: 700;
            }}
            QLabel#DetailPageSubtitle {{
                color: {t.get('text_secondary', '#5E6B7A')};
                font-size: 13px;
            }}
            QFrame#DetailHeaderIconBox {{
                background-color: {t.get('primary_light', '#EAF2FF')};
                border: 1px solid {t.get('primary_border', '#BCD7FF')};
                border-radius: 12px;
            }}
            QLabel#DetailCurrentCasePill {{
                background-color: {t.get('surface_alt', '#F8FAFD')};
                border: 1px solid {t.get('border', '#DDE6F2')};
                border-radius: 6px;
                padding: 7px 14px;
                color: {t.get('text_secondary', '#5E6B7A')};
            }}
            QFrame#DetailStatCard, QFrame#DetailInfoCard {{
                background-color: {t.get('surface', '#FFFFFF')};
                border: 1px solid {t.get('border', '#DDE6F2')};
                border-radius: 10px;
            }}
            QFrame#DetailStatIconBox_info {{
                background-color: {t.get('primary_light', '#EAF2FF')};
                border: 1px solid {t.get('primary_border', '#BCD7FF')};
                border-radius: 12px;
            }}
            QFrame#DetailStatIconBox_cyan {{
                background-color: #E6FAFA;
                border: 1px solid #B7ECEC;
                border-radius: 12px;
            }}
            QFrame#DetailStatIconBox_success {{
                background-color: {t.get('success_bg', '#EAF8EF')};
                border: 1px solid {t.get('success_border', '#BDEACB')};
                border-radius: 12px;
            }}
            QFrame#DetailStatIconBox_purple {{
                background-color: {t.get('purple_bg', '#F2ECFF')};
                border: 1px solid {t.get('purple_border', '#D8C8FF')};
                border-radius: 12px;
            }}
            QLabel#DetailStatTitle {{
                color: {t.get('text_muted', '#8A97A8')};
                font-size: 12px;
            }}
            QLabel#DetailStatValue_info, QLabel#DetailStatValue_cyan, QLabel#DetailStatValue_success, QLabel#DetailStatValue_purple, QLabel#DetailStatValue_warning {{
                font-size: 22px;
                font-weight: 700;
                color: {t.get('text_primary', '#1F2D3D')};
            }}
            QLabel#DetailStatValue_success {{ color: {t.get('success', '#16A34A')}; }}
            QLabel#DetailStatValue_purple {{ color: {t.get('purple', '#7C3AED')}; }}
            QLabel#DetailSectionTitle {{
                color: {t.get('title', '#102A43')};
                font-size: 15px;
                font-weight: 700;
            }}
            QLabel#DetailNameLabel {{
                color: {t.get('text_secondary', '#5E6B7A')};
                font-size: 13px;
            }}
            QLabel#DetailValueLabel {{
                color: {t.get('text_primary', '#1F2D3D')};
                font-size: 13px;
            }}
            QLabel#DetailSummaryLabel {{
                color: {t.get('text_secondary', '#5E6B7A')};
                font-size: 13px;
            }}
            QPushButton#DetailNeutralButton {{
                min-height: 38px;
                max-height: 38px;
                padding: 0px 15px;
                border: 1px solid {t.get('border', '#DDE6F2')};
                border-radius: 6px;
                background-color: {t.get('surface', '#FFFFFF')};
                color: {t.get('text_primary', '#1F2D3D')};
                font-weight: 500;
            }}
            QPushButton#DetailNeutralButton:hover {{
                background-color: {t.get('surface_hover', '#F2F7FF')};
                border-color: {t.get('primary_border', '#BCD7FF')};
                color: {t.get('primary', '#1769E0')};
            }}
            QPushButton#DetailNeutralButton:disabled {{
                background-color: {t.get('surface_alt', '#F8FAFD')};
                border-color: {t.get('border_light', '#E8EEF6')};
                color: {t.get('text_muted', '#8A97A8')};
            }}
            QPushButton#DetailNeutralButton:disabled:hover {{
                background-color: {t.get('surface_alt', '#F8FAFD')};
                border-color: {t.get('border_light', '#E8EEF6')};
                color: {t.get('text_muted', '#8A97A8')};
            }}
            QPushButton#DetailPrimaryButton {{
                min-height: 38px;
                max-height: 38px;
                padding: 0px 15px;
                border: 1px solid {t.get('primary', '#1769E0')};
                border-radius: 6px;
                background-color: {t.get('primary', '#1769E0')};
                color: {t.get('text_inverse', '#FFFFFF')};
                font-weight: 500;
            }}
            QPushButton#DetailPrimaryButton:hover {{
                background-color: {t.get('primary_hover', '#0F5ED7')};
                border-color: {t.get('primary_hover', '#0F5ED7')};
                color: {t.get('text_inverse', '#FFFFFF')};
            }}
            QPushButton#DetailPrimaryButton:pressed {{
                background-color: {t.get('primary_pressed', '#0B4DB5')};
                border-color: {t.get('primary_pressed', '#0B4DB5')};
                color: {t.get('text_inverse', '#FFFFFF')};
            }}
            QTableWidget {{
                background-color: {t.get('surface', '#FFFFFF')};
                alternate-background-color: {t.get('table_alt_bg', '#F8FAFD')};
                gridline-color: {t.get('table_grid', '#DDE6F2')};
                border: 1px solid {t.get('border', '#DDE6F2')};
                border-radius: 6px;
                selection-background-color: {t.get('table_selected_bg', '#DCEBFF')};
                selection-color: {t.get('text_primary', '#1F2D3D')};
            }}
            QTableWidget::item {{
                padding: 6px 5px;
            }}
            QHeaderView::section {{
                background-color: {t.get('table_header_bg', '#EEF4FB')};
                color: {t.get('text_primary', '#1F2D3D')};
                font-weight: 700;
                border: none;
                border-right: 1px solid {t.get('table_grid', '#DDE6F2')};
                border-bottom: 1px solid {t.get('table_grid', '#DDE6F2')};
                padding: 6px 5px;
                min-height: 30px;
            }}
        """)

    # ------------------------------------------------------------------
    # 通用信息布局
    # ------------------------------------------------------------------
    def add_info_row3(self, layout, row, item1, item2, item3):
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

    def make_name_label(self, text):
        label = QLabel(f"{text}：")
        label.setObjectName("DetailNameLabel")
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return label

    def make_value_widget(self, widget: QLabel):
        widget.setObjectName("DetailValueLabel")
        widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return widget

    # ------------------------------------------------------------------
    # 数据刷新
    # ------------------------------------------------------------------
    def set_case(self, case_data: dict):
        self.current_case = case_data
        self.refresh_detail()

    def refresh_detail(self):
        if not self.current_case:
            self.clear_detail()
            return

        case_id = self.current_case.get("id")
        if case_id:
            fresh_case = self.database.get_case(case_id)
            if fresh_case:
                self.current_case = fresh_case

        case = self.current_case
        self.status_label.setText(
            f"当前病例：{case.get('case_no', '')}    "
            f"姓名：{case.get('patient_name', '')}    "
            f"样本号：{case.get('sample_no', '')}"
        )

        self.case_no_label.setText(self.v(case.get("case_no")))
        self.patient_name_label.setText(self.v(case.get("patient_name")))
        self.sample_no_label.setText(self.v(case.get("sample_no")))
        self.age_label.setText(self.v(case.get("age")))
        self.sex_label.setText(self.v(case.get("sex")))
        self.occupation_label.setText(self.v(case.get("occupation")))
        self.phone_label.setText(self.v(case.get("phone")))
        self.test_date_label.setText(self.v(case.get("test_date")))
        self.report_path_label.setText(self.v(case.get("report_path")))

        self.sample_no_stat["value"].setText(self.v(case.get("sample_no")))
        self.test_date_stat["value"].setText(self.v(case.get("test_date")))
        if case.get("report_path"):
            self.report_status_stat["value"].setText("可用")
        else:
            self.report_status_stat["value"].setText("未生成")

        self.collect_time_label.setText(self.v(case.get("collect_time")))
        self.receive_time_label.setText(self.v(case.get("receive_time")))
        self.semen_volume_label.setText(self.v(case.get("semen_volume")))
        self.ph_label.setText(self.v(case.get("ph_value")))
        self.appearance_label.setText(self.v(case.get("appearance")))
        self.color_label.setText(self.v(case.get("color")))
        self.liquefaction_time_label.setText(self.v(case.get("liquefaction_time")))
        self.liquefaction_status_label.setText(self.v(case.get("liquefaction_status")))
        self.agglutination_label.setText(self.v(case.get("agglutination")))
        self.viscosity_label.setText(self.v(case.get("viscosity")))
        self.collect_method_label.setText(self.v(case.get("collect_method")))
        self.abstinence_days_label.setText(self.v(case.get("abstinence_days")))
        self.smell_label.setText(self.v(case.get("smell")))
        self.test_temperature_label.setText(self.v(case.get("test_temperature")))
        self.collect_location_label.setText(self.v(case.get("collect_location")))
        self.collect_complete_label.setText(self.v(case.get("collect_complete")))
        self.dead_sperm_label.setText(self.v(case.get("dead_sperm")))
        self.sperm_concentration_label.setText(self.v(case.get("sperm_concentration")))
        self.sperm_total_label.setText(self.v(case.get("sperm_total")))
        self.forward_motility_label.setText(self.v(case.get("forward_motility")))
        self.total_motility_label.setText(self.v(case.get("total_motility")))

        self.refresh_analysis_table()
        self.update_action_buttons_state()

    def clear_detail(self):
        self.status_label.setText("未选择病例")
        self.sample_no_stat["value"].setText("-")
        self.test_date_stat["value"].setText("-")
        self.completed_stat["value"].setText("0 / 0")
        self.report_status_stat["value"].setText("未生成")
        self.analysis_summary_label.setText("未选择病例")
        self.analysis_table.setRowCount(0)
        self.update_action_buttons_state()

    def refresh_analysis_table(self):
        if not self.current_case:
            self.analysis_summary_label.setText("未选择病例")
            self.analysis_table.setRowCount(0)
            return

        case_id = self.current_case.get("id")
        if not case_id:
            self.analysis_summary_label.setText("当前病例缺少数据库 ID")
            self.analysis_table.setRowCount(0)
            return

        try:
            analysis_rows = self.database.get_protein_analysis_by_case(case_id)
        except Exception as e:
            self.analysis_summary_label.setText(f"读取蛋白分析结果失败：{e}")
            self.analysis_table.setRowCount(0)
            return

        analysis_map = self.build_analysis_map(analysis_rows)
        protein_items = self.config.get_protein_items()
        self.analysis_table.setRowCount(len(protein_items))

        completed_count = 0
        for row_index, protein in enumerate(protein_items):
            key = protein.get("key", "")
            name = protein.get("name", key)
            part = protein.get("part", "")
            analysis = analysis_map.get(key)

            if analysis:
                completed_count += 1
                values = [
                    name,
                    part,
                    "已完成",
                    analysis.get("total_fields", 0),
                    analysis.get("total_sperm_count", 0),
                    analysis.get("positive_count", 0),
                    self.fmt_rate(analysis.get("expression_rate", 0)),
                    self.fmt_int(analysis.get("mean_intensity", 0)),
                    analysis.get("created_at", ""),
                    analysis.get("output_folder", ""),
                ]
                status = "success"
            else:
                values = [name, part, "未检测", "-", "-", "-", "-", "-", "-", "-"]
                status = "warning"

            for col_index, value in enumerate(values):
                if col_index == 2:
                    self.set_status_badge(row_index, col_index, str(value), status)
                    continue
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                self.analysis_table.setItem(row_index, col_index, item)

        total_count = len(protein_items)
        uncompleted_count = max(total_count - completed_count, 0)
        self.completed_stat["value"].setText(f"{completed_count} / {total_count}")
        self.analysis_summary_label.setText(
            f"蛋白检测进度：已完成 {completed_count} / {total_count}，未检测 {uncompleted_count}"
        )

    def set_status_badge(self, row: int, column: int, text: str, status: str) -> None:
        """在“检测状态”列中绘制状态标签。

        外层 wrapper 填满表格单元格，内部 QLabel 使用固定高度并居中放置。
        这样“已完成 / 未检测”会处在绿色或橙色标签框内的上下正中。
        """
        wrapper = QWidget()
        wrapper.setObjectName("DetailStatusBadgeCell")
        wrapper.setAttribute(Qt.WA_StyledBackground, True)
        wrapper.setStyleSheet("QWidget#DetailStatusBadgeCell { background: transparent; }")

        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setFixedSize(66, 24)
        label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        label.setObjectName("DetailStatusBadge")
        if status == "success":
            label.setProperty("status", "success")
        elif status == "warning":
            label.setProperty("status", "warning")
        else:
            label.setProperty("status", "info")
        label.setStyleSheet(self.status_badge_qss(status))

        layout.addWidget(label, 0, Qt.AlignCenter)
        self.analysis_table.setCellWidget(row, column, wrapper)

    def status_badge_qss(self, status: str) -> str:
        t = self.theme
        base = (
            "border-radius: 12px; "
            "padding: 0px 8px; "
            "font-weight: 700; "
            "font-size: 12px;"
        )
        if status == "success":
            return f"color: {t.get('success', '#16A34A')}; background: {t.get('success_bg', '#EAF8EF')}; border: 1px solid {t.get('success_border', '#BDEACB')}; {base}"
        if status == "warning":
            return f"color: {t.get('warning', '#F59E0B')}; background: {t.get('warning_bg', '#FFF5E5')}; border: 1px solid {t.get('warning_border', '#FAD89A')}; {base}"
        return f"color: {t.get('info', '#2563EB')}; background: {t.get('info_bg', '#EEF4FF')}; border: 1px solid {t.get('info_border', '#C7D8FF')}; {base}"

    def build_analysis_map(self, analysis_rows):
        analysis_map = {}
        for row in analysis_rows:
            protein_name = str(row.get("protein_name", "") or "").strip()
            if not protein_name:
                continue
            protein_key = self.config.normalize_protein_key(protein_name)
            if not protein_key:
                continue
            old_row = analysis_map.get(protein_key)
            if old_row is None:
                analysis_map[protein_key] = row
                continue
            try:
                old_id = int(old_row.get("id", 0))
                new_id = int(row.get("id", 0))
                if new_id >= old_id:
                    analysis_map[protein_key] = row
            except Exception:
                analysis_map[protein_key] = row
        return analysis_map

    def update_action_buttons_state(self) -> None:
        """刷新操作按钮可用状态与对应图标。

        只有“打开报告”依赖报告文件路径，没有报告时禁用。
        其他按钮保持可点击：没有当前病例时，由按钮对应逻辑给出提示。
        """
        has_case = bool(self.current_case)
        has_report = bool(has_case and str(self.current_case.get("report_path", "")).strip())

        self.btn_refresh.setEnabled(True)
        self.btn_start_analysis.setEnabled(True)
        self.btn_batch_analysis.setEnabled(True)
        self.btn_report.setEnabled(True)
        self.btn_open_workspace.setEnabled(True)
        self.btn_open_report.setEnabled(has_report)
        self.btn_open_report.setCursor(Qt.PointingHandCursor if has_report else Qt.ArrowCursor)
        self.update_action_button_icons()

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def start_analysis(self):
        if not self.current_case:
            QMessageBox.information(self, "提示", "请先在病例管理中选择病例。")
            return
        self.start_analysis_requested.emit(self.current_case)

    def open_batch_analysis(self):
        if not self.current_case:
            QMessageBox.information(self, "提示", "请先在病例管理中选择病例。")
            return
        dialog = BatchAnalysisDialog(self.database, self.current_case, self)
        dialog.batch_finished.connect(self.refresh_detail)
        dialog.exec()
        self.refresh_detail()

    def go_report(self):
        if not self.current_case:
            QMessageBox.information(self, "提示", "请先在病例管理中选择病例。")
            return
        self.report_requested.emit(self.current_case)

    def open_report(self):
        if not self.current_case:
            QMessageBox.information(self, "提示", "请先在病例管理中选择病例。")
            return

        report_path = self.current_case.get("report_path", "")
        if not report_path:
            QMessageBox.information(self, "提示", "当前病例还没有生成报告。")
            return

        path = Path(report_path)
        if not path.exists():
            QMessageBox.warning(self, "提示", f"报告文件不存在：\n{path}")
            return

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_workspace(self):
        if not self.current_case:
            QMessageBox.information(self, "提示", "请先在病例管理中选择病例。")
            return

        case_no = str(self.current_case.get("case_no", "")).strip()
        if not case_no:
            QMessageBox.warning(self, "提示", "当前病例没有病历号。")
            return

        folder = self.config.get_workspace_root() / case_no
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    @staticmethod
    def v(value):
        if value is None or value == "":
            return "-"
        return str(value)

    @staticmethod
    def fmt_int(value):
        try:
            return str(int(round(float(value))))
        except Exception:
            return str(value)

    @staticmethod
    def fmt_rate(value):
        try:
            return f"{float(value):.2f}%"
        except Exception:
            return f"{value}%" if value not in [None, ""] else "0.00%"
