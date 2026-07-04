import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal, QTimer, QSize, QByteArray, QRectF
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

try:
    from PySide6.QtSvg import QSvgRenderer
except Exception:  # pragma: no cover - QtSvg 缺失时回退 QIcon 直接加载
    QSvgRenderer = None

from app.case_edit_dialog import CaseEditDialog
from app.theme import DEFAULT_THEME_KEY, get_theme
from app.ui_components import (
    CardFrame,
    StatusBadge,
    apply_shadow,
    create_danger_button,
    create_primary_button,
    create_secondary_button,
    set_badge_to_table,
    setup_table,
)


class CaseStatCard(CardFrame):
    """病例管理顶部统计卡片。

    说明：
    1. 这里使用主题颜色，不在页面里写死主色。
    2. 图标先用文字符号承载，避免额外引入图标依赖。
    3. 后续如需换成 SVG 图标，只需要替换本类内部实现。
    """

    def __init__(
        self,
        title: str,
        value: str = "0",
        unit: str = "例",
        icon_text: str = "",
        accent: str = "primary",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(
            parent=parent,
            object_name="StatCard",
            margins=(16, 14, 16, 14),
            spacing=0,
            shadow=True,
        )
        self.theme = get_theme(DEFAULT_THEME_KEY)
        self.accent = accent

        root = QHBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        self.icon_label = QLabel(icon_text)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setFixedSize(48, 48)
        self.icon_label.setObjectName("StatIconBox")
        self._apply_icon_style()

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("SectionHint")
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(6)

        self.value_label = QLabel(str(value))
        self.value_label.setObjectName("PageTitle")
        self.value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.unit_label = QLabel(unit)
        self.unit_label.setObjectName("CurrentCaseLabel")
        self.unit_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        value_row.addWidget(self.value_label, 0, Qt.AlignBottom)
        value_row.addWidget(self.unit_label, 0, Qt.AlignBottom)
        value_row.addStretch(1)

        text_layout.addWidget(self.title_label)
        text_layout.addLayout(value_row)

        root.addWidget(self.icon_label, 0, Qt.AlignVCenter)
        root.addLayout(text_layout, 1)
        self.body_layout.addLayout(root)
        self.setMinimumHeight(92)

    def _accent_colors(self) -> Tuple[str, str, str]:
        theme = self.theme
        if self.accent == "success":
            return theme.get("success", "#16A34A"), theme.get("success_bg", "#EAF8EF"), theme.get("success_border", "#BDEACB")
        if self.accent == "warning":
            return theme.get("warning", "#F59E0B"), theme.get("warning_bg", "#FFF5E5"), theme.get("warning_border", "#FAD89A")
        if self.accent == "danger":
            return theme.get("danger", "#EF4444"), theme.get("danger_bg", "#FDECEC"), theme.get("danger_border", "#F6BFC0")
        if self.accent == "purple":
            return theme.get("purple", "#7C3AED"), theme.get("purple_bg", "#F2ECFF"), theme.get("purple_border", "#D8C8FF")
        return theme.get("primary", "#1769E0"), theme.get("primary_light", "#EAF2FF"), theme.get("primary_border", "#BCD7FF")

    def _apply_icon_style(self) -> None:
        color, bg, border = self._accent_colors()
        self.icon_label.setStyleSheet(
            f"""
            QLabel#StatIconBox {{
                color: {color};
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 12px;
                font-size: 22px;
                font-weight: 700;
            }}
            """
        )

    def set_value(self, value) -> None:
        self.value_label.setText(str(value))


class CaseManagerWindow(QWidget):
    case_selected = Signal(dict)

    TABLE_HEADERS = [
        "ID",
        "状态",
        "病历号",
        "姓名",
        "年龄",
        "性别",
        "联系方式",
        "样本号",
        "检测日期",
        "报告状态",
        "创建时间",
    ]

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.database = database
        self.current_cases: List[Dict] = []
        self.current_page = 0
        self._page_size = 1
        self._table_row_height = 38
        self._min_page_size = 3
        self._resize_refresh_timer: Optional[QTimer] = None
        self.init_ui()
        self.load_cases()

    @staticmethod
    def _project_root() -> Path:
        """返回项目根目录。

        当前文件位于 app/case_manager_window.py，项目根目录应为 app 的上一级。
        """
        return Path(__file__).resolve().parents[1]

    @classmethod
    def _icons_dir(cls) -> Path:
        """返回图标目录。

        项目结构：
            app/case_manager_window.py
            assets/icons/*.svg
        """
        return cls._project_root() / "assets" / "icons"

    def _icon_path(self, icon_name: str) -> Optional[Path]:
        """查找图标文件。

        这里不用相对当前工作目录，避免从 IDE、命令行或打包环境启动时路径不同。
        """
        if not icon_name:
            return None

        candidates = [
            self._icons_dir() / icon_name,
            Path.cwd() / "assets" / "icons" / icon_name,
            Path.cwd() / icon_name,
        ]

        for path in candidates:
            try:
                if path.exists():
                    return path
            except Exception:
                continue
        return None

    @staticmethod
    def _replace_svg_color(svg_text: str, color: str) -> str:
        """修正 SVG 颜色。

        根本原因通常是从图标站下载的 SVG 使用了 currentColor、CSS class、黑色默认描边等写法，
        Qt 的 QIcon 在某些环境下不能按网页规则解析这些样式，导致按钮图标看起来没有显示。
        这里在加载按钮图标时把颜色显式写进 SVG，保证 PySide6 能稳定渲染。
        """
        if not color:
            return svg_text

        text = svg_text
        text = re.sub(r'(?i)currentColor', color, text)
        text = re.sub(r'(?i)color\s*:\s*[^;\"\']+', f'color:{color}', text)

        # 替换 stroke 颜色，但保留 none / transparent / url(...)。
        text = re.sub(
            r'(?i)stroke=("|\')(?!none\1|transparent\1|url\()[^"\']*("|\')',
            f'stroke="{color}"',
            text,
        )

        # 替换 fill 颜色，但保留 none / transparent / url(...)。
        text = re.sub(
            r'(?i)fill=("|\')(?!none\1|transparent\1|url\()[^"\']*("|\')',
            f'fill="{color}"',
            text,
        )

        # 如果 SVG 没有显式颜色，给根节点补充 color，供 currentColor 兜底。
        if "<svg" in text and "color=" not in text[:300] and "color:" not in text[:500]:
            text = re.sub(r'<svg\b', f'<svg color="{color}"', text, count=1)
        return text

    def _load_svg_icon(
        self,
        icon_name: str,
        canvas_size: int = 20,
        color: str = "",
        glyph_size: Optional[int] = None,
    ) -> QIcon:
        """稳定加载 SVG 为 QIcon，并把图标统一放进固定画布。

        修正点：
        1. 不直接把不同 SVG 原始尺寸塞给按钮，避免搜索、刷新、编辑、删除视觉大小不一致。
        2. 先渲染到固定 canvas，再把真实图形居中绘制到 glyph 区域。
        3. 颜色统一在这里处理，刷新/编辑为中性灰，删除为红色，主按钮为白色。
        """
        icon_path = self._icon_path(icon_name)
        if icon_path is None:
            return QIcon()

        canvas_size = max(12, int(canvas_size))
        glyph_size = int(glyph_size or round(canvas_size * 0.82))
        glyph_size = max(10, min(canvas_size, glyph_size))

        if QSvgRenderer is not None and icon_path.suffix.lower() == ".svg":
            try:
                svg_text = icon_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                svg_text = icon_path.read_text(encoding="utf-8-sig", errors="ignore")
            except Exception:
                svg_text = ""

            if svg_text:
                svg_text = self._replace_svg_color(svg_text, color)
                renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
                if renderer.isValid():
                    pixmap = QPixmap(canvas_size, canvas_size)
                    pixmap.fill(Qt.transparent)

                    margin = (canvas_size - glyph_size) / 2
                    target_rect = QRectF(margin, margin, glyph_size, glyph_size)

                    painter = QPainter(pixmap)
                    painter.setRenderHint(QPainter.Antialiasing, True)
                    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
                    renderer.render(painter, target_rect)
                    painter.end()

                    if not pixmap.isNull():
                        return QIcon(pixmap)

        icon = QIcon(str(icon_path))
        if not icon.isNull():
            return icon
        return QIcon()

    def _set_button_icon(
        self,
        button: QPushButton,
        icon_name: str,
        canvas_size: int = 20,
        glyph_size: int = 16,
        color: str = "",
    ) -> None:
        """给按钮设置统一规格 SVG 图标。"""
        icon = self._load_svg_icon(
            icon_name,
            canvas_size=canvas_size,
            glyph_size=glyph_size,
            color=color,
        )
        if icon.isNull():
            return
        button.setIcon(icon)
        button.setIconSize(QSize(canvas_size, canvas_size))
        button.setLayoutDirection(Qt.LeftToRight)

    def _create_neutral_button(self, text: str, min_width: int = 0) -> QPushButton:
        """创建中性按钮。

        不使用 SecondaryButton，避免全局 QSS 把文字设成主题蓝。
        字体继续由全局样式控制，不在这里单独 setFont。
        """
        button = QPushButton(text)
        button.setObjectName("NeutralButton")
        if min_width > 0:
            button.setMinimumWidth(min_width)
        button.setCursor(Qt.PointingHandCursor)

        # 只改颜色，不设置字体，字体继续跟随软件全局设置。
        theme = get_theme(DEFAULT_THEME_KEY)
        neutral = theme.get("text_secondary", "#5E6B7A")
        hover_bg = theme.get("surface_hover", "#F2F7FF")
        border = theme.get("border", "#DDE6F2")
        border_hover = theme.get("primary_border", "#BCD7FF")
        button.setStyleSheet(
            f"""
            QPushButton#NeutralButton {{
                color: {neutral};
                border-color: {border};
            }}
            QPushButton#NeutralButton:hover {{
                color: {neutral};
                background-color: {hover_bg};
                border-color: {border_hover};
            }}
            QPushButton#NeutralButton:pressed {{
                color: {neutral};
            }}
            """
        )
        return button


    def _create_danger_outline_button(self, text: str, min_width: int = 0) -> QPushButton:
        """创建危险操作按钮。

        不使用全局 DangerButton，避免 font-weight: 600 导致“删除病例”视觉上比其他按钮更粗。
        字体族、字号继续跟随软件全局设置，只在这里覆盖颜色、边框和字重。
        """
        button = QPushButton(text)
        button.setObjectName("CaseDangerButton")
        if min_width > 0:
            button.setMinimumWidth(min_width)
        button.setCursor(Qt.PointingHandCursor)

        theme = get_theme(DEFAULT_THEME_KEY)
        danger = theme.get("danger", "#EF4444")
        danger_bg = theme.get("danger_bg", "#FDECEC")
        danger_border = theme.get("danger_border", "#F6BFC0")
        surface = theme.get("surface", "#FFFFFF")

        button.setStyleSheet(
            f"""
            QPushButton#CaseDangerButton {{
                background-color: {surface};
                border: 1px solid {danger_border};
                color: {danger};
                font-weight: 500;
            }}
            QPushButton#CaseDangerButton:hover {{
                background-color: {danger_bg};
                border-color: {danger};
                color: {danger};
                font-weight: 500;
            }}
            QPushButton#CaseDangerButton:pressed {{
                background-color: {danger_bg};
                border-color: {danger};
                color: {danger};
                font-weight: 500;
            }}
            """
        )
        return button

    def _create_page_button(self, text: str) -> QPushButton:
        """创建底部分页小按钮。"""
        button = QPushButton(text)
        button.setObjectName("PaginationButton")
        button.setFixedSize(30, 26)
        button.setCursor(Qt.PointingHandCursor)

        theme = get_theme(DEFAULT_THEME_KEY)
        text_color = theme.get("text_secondary", "#5E6B7A")
        text_muted = theme.get("text_muted", "#8A97A8")
        border = theme.get("border", "#DDE6F2")
        hover_bg = theme.get("surface_hover", "#F2F7FF")
        surface = theme.get("surface", "#FFFFFF")
        button.setStyleSheet(
            f"""
            QPushButton#PaginationButton {{
                background-color: {surface};
                border: 1px solid {border};
                border-radius: 6px;
                color: {text_color};
                padding: 0px;
            }}
            QPushButton#PaginationButton:hover {{
                background-color: {hover_bg};
                color: {text_color};
            }}
            QPushButton#PaginationButton:disabled {{
                background-color: {surface};
                border-color: {border};
                color: {text_muted};
            }}
            """
        )
        return button

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 16, 18, 14)
        main_layout.setSpacing(14)

        # 顶部统计卡片
        stats_layout = QHBoxLayout()
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setSpacing(14)

        self.card_total = CaseStatCard("病例总数", "0", "例", "＋", "primary")
        self.card_today = CaseStatCard("今日新增", "0", "例", "人", "success")
        self.card_report = CaseStatCard("已生成报告", "0", "例", "文", "purple")
        self.card_waiting = CaseStatCard("待分析", "0", "例", "待", "warning")

        stats_layout.addWidget(self.card_total, 1)
        stats_layout.addWidget(self.card_today, 1)
        stats_layout.addWidget(self.card_report, 1)
        stats_layout.addWidget(self.card_waiting, 1)
        main_layout.addLayout(stats_layout)

        # 搜索与操作工具栏
        toolbar_card = CardFrame(
            object_name="Card",
            margins=(14, 12, 14, 12),
            spacing=0,
            shadow=True,
        )
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(10)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("按病历号、姓名、样本号、日期、联系方式搜索")
        self.search_edit.setMinimumHeight(36)
        self.search_edit.returnPressed.connect(self.search_cases)

        self.btn_search = create_primary_button("搜索", min_width=90)
        self.btn_refresh = self._create_neutral_button("刷新", min_width=90)
        self.btn_add = create_primary_button("新建病例", min_width=118)
        self.btn_edit = self._create_neutral_button("编辑病例", min_width=108)
        self.btn_delete = self._create_danger_outline_button("删除病例", min_width=108)

        # 按钮图标统一使用 20px 画布、16px 图形区，避免不同 SVG 视觉大小不一致。
        theme = get_theme(DEFAULT_THEME_KEY)
        neutral_icon = theme.get("text_secondary", "#5E6B7A")
        danger_icon = theme.get("danger", "#EF4444")

        self._set_button_icon(self.btn_search, "search_s.svg", canvas_size=20, glyph_size=16, color="#FFFFFF")
        self._set_button_icon(self.btn_refresh, "refresh.svg", canvas_size=20, glyph_size=16, color=neutral_icon)
        self._set_button_icon(self.btn_add, "add_s.svg", canvas_size=20, glyph_size=16, color="#FFFFFF")
        self._set_button_icon(self.btn_edit, "edit.svg", canvas_size=20, glyph_size=16, color=neutral_icon)
        self._set_button_icon(self.btn_delete, "delete_danger.svg", canvas_size=20, glyph_size=16, color=danger_icon)

        toolbar_layout.addWidget(self.search_edit, 1)
        toolbar_layout.addWidget(self.btn_search)
        toolbar_layout.addWidget(self.btn_refresh)
        toolbar_layout.addWidget(self.btn_add)
        toolbar_layout.addWidget(self.btn_edit)
        toolbar_layout.addWidget(self.btn_delete)
        toolbar_card.addLayout(toolbar_layout)
        main_layout.addWidget(toolbar_card)

        # 表格卡片
        table_card = CardFrame(
            object_name="Card",
            margins=(12, 12, 12, 10),
            spacing=8,
            shadow=True,
        )

        self.table = QTableWidget()
        self.table.setObjectName("CaseTable")
        self.table.setColumnCount(len(self.TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(self.TABLE_HEADERS)
        self.table.setColumnHidden(0, True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        # 病例列表由分页控制行数，避免窗口化时出现半行或竖向滚动。
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)
        setup_table(
            self.table,
            row_height=self._table_row_height,
            alternating=True,
            stretch_last_section=False,
            selection_behavior=QAbstractItemView.SelectRows,
        )

        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(42)

        # 列宽说明：
        # 1. 不再使用单一 Stretch，也不写固定像素列宽。
        # 2. 采用“最小宽度 + 剩余空间按权重分配”的响应式列宽。
        # 3. 窗口变宽时，病历号、联系方式、样本号、创建时间会自动变宽；
        #    窗口变窄时，先压缩到最小宽度，仍不够时允许横向滚动。
        for column in range(1, self.table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.Interactive)

        self._column_min_widths = {
            1: 100,  # 状态：保证“待分析/已完成”胶囊标签完整
            2: 190,  # 病历号
            3: 60,   # 姓名
            4: 48,   # 年龄
            5: 48,   # 性别
            6: 108,  # 联系方式
            7: 122,  # 样本号
            8: 92,   # 检测日期
            9: 92,   # 报告状态
            10: 150, # 创建时间
        }
        self._column_stretch_weights = {
            1: 0.6,
            2: 4.0,
            3: 0.7,
            4: 0.35,
            5: 0.35,
            6: 1.6,
            7: 1.8,
            8: 1.0,
            9: 1.0,
            10: 2.2,
        }
        QTimer.singleShot(0, self._apply_responsive_table_columns)

        table_card.addWidget(self.table, 1)

        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(2, 4, 2, 0)
        footer_layout.setSpacing(8)

        self.info_label = QLabel("当前病例数量：0")
        self.info_label.setObjectName("CurrentCaseLabel")
        self.info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.page_label = QLabel("共 0 条    每页 0 条    1 / 1")
        self.page_label.setObjectName("CurrentCaseLabel")
        self.page_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.btn_prev_page = self._create_page_button("‹")
        self.btn_next_page = self._create_page_button("›")
        self.btn_prev_page.clicked.connect(self.goto_prev_page)
        self.btn_next_page.clicked.connect(self.goto_next_page)

        footer_layout.addWidget(self.info_label)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.page_label)
        footer_layout.addWidget(self.btn_prev_page)
        footer_layout.addWidget(self.btn_next_page)
        table_card.addWidget(footer)

        self._resize_refresh_timer = QTimer(self)
        self._resize_refresh_timer.setSingleShot(True)
        self._resize_refresh_timer.timeout.connect(self._refresh_page_for_current_geometry)

        main_layout.addWidget(table_card, 1)

        self.btn_search.clicked.connect(self.search_cases)
        self.btn_refresh.clicked.connect(self.load_cases)
        self.btn_add.clicked.connect(self.add_case)
        self.btn_edit.clicked.connect(self.edit_case)
        self.btn_delete.clicked.connect(self.delete_case)
        self.table.doubleClicked.connect(self.open_selected_case)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_table_columns()
        self._schedule_page_refresh()

    def _schedule_page_refresh(self) -> None:
        """窗口尺寸变化后延迟刷新分页，避免拖拽窗口时频繁重绘。"""
        if getattr(self, "_resize_refresh_timer", None) is not None:
            self._resize_refresh_timer.start(80)

    def _apply_responsive_table_columns(self) -> None:
        """根据表格可视宽度动态计算列宽。

        这里不是简单百分比，而是更适合业务表格的方案：
        - 每列先保留一个最小宽度，避免状态标签、日期、时间被压坏；
        - 剩余空间再按权重分配，窗口越宽，主要字段自动变宽；
        - 如果窗口太窄，则使用最小宽度并允许横向滚动。
        """
        if not hasattr(self, "table"):
            return
        if not hasattr(self, "_column_min_widths"):
            return

        viewport_width = max(0, self.table.viewport().width() - 2)
        visible_columns = [column for column in range(1, self.table.columnCount()) if not self.table.isColumnHidden(column)]
        if not visible_columns:
            return

        min_widths = self._column_min_widths
        weights = self._column_stretch_weights
        min_total = sum(min_widths.get(column, 60) for column in visible_columns)

        if viewport_width <= 0:
            return

        if viewport_width <= min_total:
            for column in visible_columns:
                self.table.setColumnWidth(column, min_widths.get(column, 60))
            return

        extra_width = viewport_width - min_total
        total_weight = sum(weights.get(column, 1.0) for column in visible_columns)
        used_width = 0

        for column in visible_columns[:-1]:
            base_width = min_widths.get(column, 60)
            weight = weights.get(column, 1.0)
            width = int(base_width + extra_width * weight / total_weight)
            self.table.setColumnWidth(column, width)
            used_width += width

        # 最后一列吸收取整误差，避免右侧出现细小空隙。
        last_column = visible_columns[-1]
        last_width = max(min_widths.get(last_column, 60), viewport_width - used_width)
        self.table.setColumnWidth(last_column, last_width)

    # ------------------------------------------------------------------
    # 数据加载与统计
    # ------------------------------------------------------------------
    def load_cases(self):
        self.search_edit.clear()
        self._load_cases_by_keyword("")

    def search_cases(self):
        keyword = self.search_edit.text().strip()
        self._load_cases_by_keyword(keyword)

    def _load_cases_by_keyword(self, keyword):
        try:
            cases = self.database.get_cases(keyword)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载病例失败：\n{e}")
            return

        self.current_cases = cases
        self.current_page = 0
        self._update_summary_cards(cases)
        self._refresh_page_for_current_geometry()
        self._apply_responsive_table_columns()

    def _calculate_page_size(self) -> int:
        """根据当前表格可视高度计算每页行数。"""
        if not hasattr(self, "table"):
            return max(1, getattr(self, "_page_size", 1))

        row_height = max(24, int(getattr(self, "_table_row_height", 38)))
        viewport_height = max(0, self.table.viewport().height())

        if viewport_height <= 0:
            return max(1, getattr(self, "_page_size", 1))

        # 预留 2px，避免底部出现半截行或横向滚动条挤压造成的误差。
        page_size = int(max(1, (viewport_height - 2) // row_height))
        return max(getattr(self, "_min_page_size", 3), page_size)

    def _refresh_page_for_current_geometry(self) -> None:
        """根据当前窗口高度刷新当前页。"""
        if not hasattr(self, "table"):
            return

        old_page_size = max(1, int(getattr(self, "_page_size", 1)))
        old_first_index = max(0, int(getattr(self, "current_page", 0)) * old_page_size)

        new_page_size = self._calculate_page_size()
        self._page_size = new_page_size

        total_count = len(self.current_cases)
        total_pages = self._total_pages(total_count)

        if total_count <= 0:
            self.current_page = 0
        else:
            self.current_page = min(total_pages - 1, max(0, old_first_index // new_page_size))

        self._fill_current_page()
        self._update_footer(total_count)
        self._apply_responsive_table_columns()

    def _total_pages(self, count: Optional[int] = None) -> int:
        total_count = len(self.current_cases) if count is None else int(count)
        if total_count <= 0:
            return 1
        return max(1, (total_count + self._page_size - 1) // self._page_size)

    def _current_page_cases(self) -> List[Dict]:
        total_count = len(self.current_cases)
        if total_count <= 0:
            return []

        total_pages = self._total_pages(total_count)
        self.current_page = min(max(0, self.current_page), total_pages - 1)
        start = self.current_page * self._page_size
        end = min(total_count, start + self._page_size)
        return self.current_cases[start:end]

    def _fill_current_page(self) -> None:
        self._fill_table(self._current_page_cases())

    def _fill_table(self, cases: List[Dict]) -> None:
        self.table.setRowCount(len(cases))
        self.table.verticalHeader().setDefaultSectionSize(self._table_row_height)

        for row_index, case in enumerate(cases):
            case_id = case.get("id", "")
            report_path = self._safe_text(case.get("report_path", ""))
            status_text = "已完成" if report_path else "待分析"
            status_type = "success" if report_path else "warning"
            report_status = "查看报告" if report_path else "待生成报告"

            values = [
                case_id,
                "",  # 状态列使用 cell widget
                case.get("case_no", ""),
                case.get("patient_name", ""),
                case.get("age", ""),
                case.get("sex", ""),
                case.get("phone", ""),
                case.get("sample_no", ""),
                case.get("test_date", ""),
                report_status,
                case.get("created_at", ""),
            ]

            for col_index, value in enumerate(values):
                item = QTableWidgetItem(self._safe_text(value))
                item.setTextAlignment(Qt.AlignCenter)
                if col_index == 0:
                    item.setData(Qt.UserRole, case_id)
                if col_index == 9 and report_path:
                    item.setForeground(Qt.blue)
                    item.setToolTip(report_path)
                self.table.setItem(row_index, col_index, item)

            set_badge_to_table(self.table, row_index, 1, status_text, status_type)

        self.table.clearSelection()

    def _update_summary_cards(self, cases: List[Dict]) -> None:
        total_count = len(cases)
        today_count = 0
        report_count = 0
        waiting_count = 0
        today_text = date.today().strftime("%Y-%m-%d")

        for case in cases:
            report_path = self._safe_text(case.get("report_path", ""))
            if report_path:
                report_count += 1
            else:
                waiting_count += 1

            created_at = self._safe_text(case.get("created_at", ""))
            test_date = self._safe_text(case.get("test_date", ""))
            if created_at.startswith(today_text) or test_date == today_text:
                today_count += 1

        self.card_total.set_value(total_count)
        self.card_today.set_value(today_count)
        self.card_report.set_value(report_count)
        self.card_waiting.set_value(waiting_count)

    def _update_footer(self, count: int) -> None:
        total_pages = self._total_pages(count)
        current_page_display = min(self.current_page + 1, total_pages)
        self.info_label.setText(f"当前病例数量：{count}")
        self.page_label.setText(f"共 {count} 条    每页 {self._page_size} 条    {current_page_display} / {total_pages}")

        has_prev = count > 0 and self.current_page > 0
        has_next = count > 0 and self.current_page < total_pages - 1
        if hasattr(self, "btn_prev_page"):
            self.btn_prev_page.setEnabled(has_prev)
        if hasattr(self, "btn_next_page"):
            self.btn_next_page.setEnabled(has_next)

    def goto_prev_page(self) -> None:
        if self.current_page <= 0:
            return
        self.current_page -= 1
        self._fill_current_page()
        self._update_footer(len(self.current_cases))
        self._apply_responsive_table_columns()

    def goto_next_page(self) -> None:
        total_pages = self._total_pages()
        if self.current_page >= total_pages - 1:
            return
        self.current_page += 1
        self._fill_current_page()
        self._update_footer(len(self.current_cases))
        self._apply_responsive_table_columns()

    # ------------------------------------------------------------------
    # 选择与操作
    # ------------------------------------------------------------------
    def get_selected_case_id(self):
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.information(self, "提示", "请先选择一条病例记录。")
            return None

        row = selected_rows[0].row()
        case_id_item = self.table.item(row, 0)
        if case_id_item is None:
            QMessageBox.warning(self, "提示", "无法获取病例 ID。")
            return None

        try:
            return int(case_id_item.text())
        except ValueError:
            QMessageBox.warning(self, "提示", "病例 ID 格式异常。")
            return None

    def add_case(self):
        dialog = CaseEditDialog(self)
        if dialog.exec() != CaseEditDialog.Accepted:
            return

        data = dialog.get_data()
        try:
            self.database.create_case(**data)
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "提示", "病历号已存在，请更换病历号。")
            return
        except Exception as e:
            QMessageBox.critical(self, "错误", f"新建病例失败：\n{e}")
            return

        QMessageBox.information(self, "成功", "病例创建成功。")
        self.load_cases()

    def edit_case(self):
        case_id = self.get_selected_case_id()
        if case_id is None:
            return

        case_data = self.database.get_case(case_id)
        if not case_data:
            QMessageBox.warning(self, "提示", "未找到该病例记录。")
            return

        dialog = CaseEditDialog(self, case_data=case_data)
        if dialog.exec() != CaseEditDialog.Accepted:
            return

        data = dialog.get_data()
        try:
            self.database.update_case(case_id=case_id, **data)
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "提示", "病历号已存在，请更换病历号。")
            return
        except Exception as e:
            QMessageBox.critical(self, "错误", f"编辑病例失败：\n{e}")
            return

        QMessageBox.information(self, "成功", "病例修改成功。")
        self.load_cases()

    def delete_case(self):
        case_id = self.get_selected_case_id()
        if case_id is None:
            return

        case_data = self.database.get_case(case_id)
        if not case_data:
            QMessageBox.warning(self, "提示", "未找到该病例记录。")
            return

        case_no = case_data.get("case_no", "")
        patient_name = case_data.get("patient_name", "")
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除病例吗？\n\n病历号：{case_no}\n姓名：{patient_name}\n\n删除后不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            self.database.delete_case(case_id)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"删除病例失败：\n{e}")
            return

        QMessageBox.information(self, "成功", "病例已删除。")
        self.load_cases()

    def open_selected_case(self):
        case_id = self.get_selected_case_id()
        if case_id is None:
            return

        case_data = self.database.get_case(case_id)
        if not case_data:
            QMessageBox.warning(self, "提示", "未找到该病例记录。")
            return

        self.case_selected.emit(case_data)

    # ------------------------------------------------------------------
    # 工具函数
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_text(value) -> str:
        if value is None:
            return ""
        return str(value)
