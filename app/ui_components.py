"""
通用 UI 组件。

设计目标：
1. 页面文件只负责业务布局，不直接写死颜色。
2. 颜色由 app/theme.py 和 app/ui_style.py 统一控制。
3. 病例管理、病例详情、蛋白分析、报告管理、系统设置共用同一套组件。

使用方式示例：
    from app.ui_components import CardFrame, PageHeader, StatCard, StatusBadge

    header = PageHeader("病例管理", "管理病例信息、样本记录与检测报告")
    card = CardFrame()
    badge = StatusBadge("已完成", "success")
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Tuple, Union

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

try:
    from app.theme import DEFAULT_THEME_KEY, get_theme
except Exception:  # pragma: no cover - 仅用于极端导入兜底
    DEFAULT_THEME_KEY = "medical_blue"

    def get_theme(theme_key: str = DEFAULT_THEME_KEY) -> Dict[str, str]:
        return {
            "primary": "#1769E0",
            "text_primary": "#1F2D3D",
            "text_secondary": "#5E6B7A",
            "text_muted": "#8A97A8",
            "surface": "#FFFFFF",
            "border": "#DDE6F2",
            "shadow": "#D6DEE9",
        }


StatusType = Union[str, None]


STATUS_ALIAS: Dict[str, str] = {
    # 成功 / 完成
    "success": "success",
    "完成": "success",
    "已完成": "success",
    "已分析": "success",
    "分析完成": "success",
    "已生成": "success",
    "已生成报告": "success",
    "正常": "success",
    "通过": "success",
    "已匹配": "success",

    # 警告 / 待处理
    "warning": "warning",
    "待分析": "warning",
    "未分析": "warning",
    "待处理": "warning",
    "未匹配": "warning",
    "部分完成": "warning",
    "待生成": "warning",

    # 失败 / 错误
    "danger": "danger",
    "失败": "danger",
    "分析失败": "danger",
    "错误": "danger",
    "异常": "danger",
    "缺失": "danger",
    "输出缺失": "danger",

    # 信息 / 当前
    "info": "info",
    "当前": "info",
    "当前病例": "info",
    "分析中": "info",
    "运行中": "info",
    "处理中": "info",
    "已选择": "info",

    # 紫色扩展
    "purple": "purple",
    "PNA": "purple",
}


def normalize_status(status: StatusType) -> str:
    """把中文状态或英文状态统一转换成 QSS 属性值。"""
    if status is None:
        return "info"
    text = str(status).strip()
    if not text:
        return "info"
    return STATUS_ALIAS.get(text, STATUS_ALIAS.get(text.lower(), "info"))


def refresh_style(widget: QWidget) -> None:
    """
    刷新动态属性样式。

    Qt 对 setProperty 后的 QSS 不一定自动刷新，尤其是状态标签切换 status 时。
    """
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def apply_shadow(
    widget: QWidget,
    blur_radius: int = 18,
    x_offset: int = 0,
    y_offset: int = 4,
    color: Optional[str] = None,
    alpha: int = 36,
    theme_key: str = DEFAULT_THEME_KEY,
) -> QGraphicsDropShadowEffect:
    """
    给卡片添加轻阴影。

    注意：Qt QSS 不支持真正的 box-shadow，所以阴影必须用 QGraphicsDropShadowEffect。
    """
    theme = get_theme(theme_key)
    shadow_color = QColor(color or theme.get("shadow", "#D6DEE9"))
    shadow_color.setAlpha(max(0, min(255, int(alpha))))

    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur_radius)
    effect.setOffset(x_offset, y_offset)
    effect.setColor(shadow_color)
    widget.setGraphicsEffect(effect)
    return effect


def create_hline(object_name: str = "HLine") -> QFrame:
    line = QFrame()
    line.setObjectName(object_name)
    line.setFrameShape(QFrame.NoFrame)
    line.setFixedHeight(1)
    return line


def create_vline(object_name: str = "VLine") -> QFrame:
    line = QFrame()
    line.setObjectName(object_name)
    line.setFrameShape(QFrame.NoFrame)
    line.setFixedWidth(1)
    return line


def create_spacer(width: int = 0, height: int = 0, horizontal: bool = True) -> QSpacerItem:
    if horizontal:
        return QSpacerItem(width, height, QSizePolicy.Expanding, QSizePolicy.Minimum)
    return QSpacerItem(width, height, QSizePolicy.Minimum, QSizePolicy.Expanding)


class CardFrame(QFrame):
    """白色圆角卡片容器。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        object_name: str = "Card",
        margins: Tuple[int, int, int, int] = (14, 14, 14, 14),
        spacing: int = 10,
        shadow: bool = False,
    ):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(*margins)
        self._layout.setSpacing(spacing)

        if shadow:
            apply_shadow(self)

    @property
    def body_layout(self) -> QVBoxLayout:
        return self._layout

    def addWidget(self, widget: QWidget, stretch: int = 0, alignment: Qt.AlignmentFlag = Qt.Alignment()) -> None:
        self._layout.addWidget(widget, stretch, alignment)

    def addLayout(self, layout, stretch: int = 0) -> None:
        self._layout.addLayout(layout, stretch)

    def addStretch(self, stretch: int = 1) -> None:
        self._layout.addStretch(stretch)


class PageHeader(QWidget):
    """页面内部标题区。主窗口已有统一标题时，也可用于卡片内二级标题。"""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        right_widget: Optional[QWidget] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("PageHeader")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("PageTitle")
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("PageSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.subtitle_label.setVisible(bool(subtitle))

        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.subtitle_label)

        root.addLayout(text_layout, 1)
        if right_widget is not None:
            root.addWidget(right_widget, 0, Qt.AlignRight | Qt.AlignVCenter)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_subtitle(self, subtitle: str) -> None:
        self.subtitle_label.setText(subtitle)
        self.subtitle_label.setVisible(bool(subtitle))


class SectionTitle(QWidget):
    """卡片内分区标题。"""

    def __init__(
        self,
        title: str,
        hint: str = "",
        right_widget: Optional[QWidget] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("SectionTitleContainer")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("SectionTitle")
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("SectionHint")
        self.hint_label.setWordWrap(True)
        self.hint_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.hint_label.setVisible(bool(hint))

        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.hint_label)

        root.addLayout(text_layout, 1)
        if right_widget is not None:
            root.addWidget(right_widget, 0, Qt.AlignRight | Qt.AlignVCenter)

    def set_hint(self, hint: str) -> None:
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))


class StatusBadge(QLabel):
    """状态胶囊标签。"""

    def __init__(
        self,
        text: str = "",
        status: StatusType = "info",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(text, parent)
        self.setObjectName("StatusBadge")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(24)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.set_status(status or text)

    def set_status(self, status: StatusType, text: Optional[str] = None) -> None:
        if text is not None:
            self.setText(text)
        self.setProperty("status", normalize_status(status))
        refresh_style(self)

    def set_text_and_status(self, text: str, status: Optional[str] = None) -> None:
        self.setText(text)
        self.set_status(status or text)


class StatCard(CardFrame):
    """顶部统计卡片。"""

    def __init__(
        self,
        title: str,
        value: Union[str, int, float] = "--",
        subtitle: str = "",
        status: StatusType = "info",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent=parent, object_name="StatCard", margins=(14, 12, 14, 12), spacing=6)

        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("SectionHint")
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.badge = StatusBadge("", status)
        self.badge.setFixedSize(10, 10)
        self.badge.setText("")

        top_layout.addWidget(self.title_label, 1)
        top_layout.addWidget(self.badge, 0, Qt.AlignRight | Qt.AlignTop)

        self.value_label = QLabel(str(value))
        self.value_label.setObjectName("PageTitle")
        self.value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("SectionHint")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.subtitle_label.setVisible(bool(subtitle))

        self.body_layout.addLayout(top_layout)
        self.body_layout.addWidget(self.value_label)
        self.body_layout.addWidget(self.subtitle_label)

    def set_value(self, value: Union[str, int, float]) -> None:
        self.value_label.setText(str(value))

    def set_subtitle(self, subtitle: str) -> None:
        self.subtitle_label.setText(subtitle)
        self.subtitle_label.setVisible(bool(subtitle))

    def set_status(self, status: StatusType) -> None:
        self.badge.set_status(status)


class InfoGrid(CardFrame):
    """信息网格卡片，适合病例详情、样本信息、报告信息。"""

    def __init__(
        self,
        title: str = "",
        columns: int = 2,
        parent: Optional[QWidget] = None,
        object_name: str = "InfoCard",
    ):
        super().__init__(parent=parent, object_name=object_name, margins=(14, 14, 14, 14), spacing=10)
        self.columns = max(1, int(columns))
        self._row = 0
        self._col = 0

        if title:
            self.body_layout.addWidget(SectionTitle(title))

        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(10)
        self.body_layout.addLayout(self.grid)

    def add_item(self, label: str, value: Union[str, int, float, QWidget, None] = "") -> None:
        cell = QWidget()
        cell_layout = QVBoxLayout(cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        cell_layout.setSpacing(3)

        label_widget = QLabel(str(label))
        label_widget.setObjectName("SectionHint")
        label_widget.setTextInteractionFlags(Qt.TextSelectableByMouse)

        if isinstance(value, QWidget):
            value_widget = value
        else:
            value_widget = QLabel("--" if value is None or value == "" else str(value))
            value_widget.setObjectName("CurrentCaseLabel")
            value_widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value_widget.setWordWrap(True)

        cell_layout.addWidget(label_widget)
        cell_layout.addWidget(value_widget)

        self.grid.addWidget(cell, self._row, self._col)
        self._col += 1
        if self._col >= self.columns:
            self._col = 0
            self._row += 1

    def add_items(self, items: Iterable[Tuple[str, Union[str, int, float, QWidget, None]]]) -> None:
        for label, value in items:
            self.add_item(label, value)


class CurrentCaseBar(CardFrame):
    """当前病例提示条。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        title: str = "当前病例",
    ):
        super().__init__(parent=parent, object_name="SubCard", margins=(12, 10, 12, 10), spacing=8)
        self._title = title

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("SectionTitle")

        self.info_label = QLabel("未选择病例")
        self.info_label.setObjectName("CurrentCaseLabel")
        self.info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        row.addWidget(self.title_label)
        row.addWidget(self.info_label, 1)
        self.body_layout.addLayout(row)

    def set_case(self, case_data: Optional[Dict[str, object]]) -> None:
        if not case_data:
            self.info_label.setText("未选择病例")
            return

        case_no = case_data.get("case_no", "")
        patient_name = case_data.get("patient_name", "")
        sample_no = case_data.get("sample_no", "")
        test_date = case_data.get("test_date", "")

        parts = []
        if case_no:
            parts.append(f"病例编号：{case_no}")
        if patient_name:
            parts.append(f"姓名：{patient_name}")
        if sample_no:
            parts.append(f"样本号：{sample_no}")
        if test_date:
            parts.append(f"检测日期：{test_date}")

        self.info_label.setText("    ".join(parts) if parts else "未选择病例")


def create_button(
    text: str,
    object_name: str = "",
    tooltip: str = "",
    min_width: int = 0,
    parent: Optional[QWidget] = None,
) -> QPushButton:
    button = QPushButton(text, parent)
    if object_name:
        button.setObjectName(object_name)
    if tooltip:
        button.setToolTip(tooltip)
    if min_width > 0:
        button.setMinimumWidth(min_width)
    button.setCursor(Qt.PointingHandCursor)
    return button


def create_primary_button(text: str, tooltip: str = "", min_width: int = 0) -> QPushButton:
    return create_button(text, "PrimaryButton", tooltip, min_width)


def create_secondary_button(text: str, tooltip: str = "", min_width: int = 0) -> QPushButton:
    return create_button(text, "SecondaryButton", tooltip, min_width)


def create_danger_button(text: str, tooltip: str = "", min_width: int = 0) -> QPushButton:
    return create_button(text, "DangerButton", tooltip, min_width)


def create_warning_button(text: str, tooltip: str = "", min_width: int = 0) -> QPushButton:
    return create_button(text, "WarningButton", tooltip, min_width)


def create_action_bar(
    buttons: Iterable[QPushButton],
    align_right: bool = False,
    spacing: int = 8,
) -> QWidget:
    """创建按钮操作栏。"""
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)

    if align_right:
        layout.addStretch(1)

    for button in buttons:
        layout.addWidget(button)

    if not align_right:
        layout.addStretch(1)

    return widget


def make_label(
    text: str,
    object_name: str = "",
    bold: bool = False,
    selectable: bool = True,
    word_wrap: bool = False,
) -> QLabel:
    label = QLabel(text)
    if object_name:
        label.setObjectName(object_name)
    if selectable:
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setWordWrap(word_wrap)
    if bold:
        font = QFont(label.font())
        font.setBold(True)
        label.setFont(font)
    return label


def setup_table(
    table: QTableWidget,
    row_height: int = 34,
    alternating: bool = True,
    stretch_last_section: bool = True,
    selection_behavior=None,
) -> None:
    """统一表格基础属性。"""
    table.setAlternatingRowColors(alternating)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(stretch_last_section)
    table.setShowGrid(True)
    table.setSortingEnabled(False)
    table.setWordWrap(False)

    if selection_behavior is not None:
        table.setSelectionBehavior(selection_behavior)

    table.verticalHeader().setDefaultSectionSize(row_height)


def set_badge_to_table(
    table: QTableWidget,
    row: int,
    column: int,
    text: str,
    status: Optional[str] = None,
) -> StatusBadge:
    """在表格单元格中放入居中的状态标签。"""
    wrapper = QWidget()
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(4, 3, 4, 3)
    layout.setSpacing(0)

    badge = StatusBadge(text, status or text)
    layout.addStretch(1)
    layout.addWidget(badge)
    layout.addStretch(1)

    table.setCellWidget(row, column, wrapper)
    return badge


def clear_layout(layout) -> None:
    """清空布局中的所有控件。"""
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif child_layout is not None:
            clear_layout(child_layout)
