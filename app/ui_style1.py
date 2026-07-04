"""
全局界面样式。

本文件只负责根据主题生成 QSS。
颜色统一来自 app/theme.py，页面里尽量不要再直接写死颜色。

本版重点：
1. 左侧导航栏改为浅色渐变背景。
2. 左侧导航按钮选中态改为蓝色横向渐变。
3. 渐变颜色仍然从主题变量推导，后续换主题不需要改页面代码。
"""

from __future__ import annotations

from typing import Dict, Optional

from app.theme import DEFAULT_THEME_KEY, get_theme


def _qss_value(theme: Dict[str, str], key: str, default: str = "") -> str:
    return theme.get(key, default)


def _side_navigation_qss(theme: Dict[str, str]) -> str:
    """左侧导航专用 QSS。

    关键说明：
    1. 选中项只保留渐变背景，不画任何外圈边框。
    2. hover / checked / focus / disabled 全状态都显式 border: none。
    3. 不再使用 border: 1px solid transparent，因为 Qt 仍可能产生可见描边。
    """
    primary = theme["primary"]
    primary_hover = theme["primary_hover"]
    primary_pressed = theme["primary_pressed"]
    primary_light = theme["primary_light"]
    primary_lighter = theme["primary_lighter"]
    background = theme["background"]
    background_alt = theme["background_alt"]
    surface = theme["surface"]
    surface_hover = theme["surface_hover"]
    border = theme["border"]
    text_primary = theme["text_primary"]
    text_muted = theme["text_muted"]
    text_inverse = theme["text_inverse"]

    return f"""
QFrame#SideMenu {{
    background: qlineargradient(
        x1: 0, y1: 0,
        x2: 1, y2: 0,
        stop: 0 {surface},
        stop: 0.58 {background},
        stop: 1 {background_alt}
    );
    border-right: 1px solid {border};
}}

/* 左侧导航：基础态。这里必须写 border: none，不能写 1px transparent。 */
QPushButton#SideButton {{
    min-height: 42px;
    padding: 0px 14px 0px 18px;
    margin: 0px 10px 0px 8px;
    border: none;
    outline: none;
    border-radius: 7px;
    background: transparent;
    color: {text_primary};
    font-size: 14px;
    font-weight: 500;
    text-align: left;
}}

/* 未选中悬停：只给浅背景，不给任何描边。 */
QPushButton#SideButton:hover {{
    border: none;
    outline: none;
    background: qlineargradient(
        x1: 0, y1: 0,
        x2: 1, y2: 0,
        stop: 0 {primary_lighter},
        stop: 1 {surface_hover}
    );
    color: {primary_pressed};
}}

/* 选中态：只有渐变背景，无外圈边框。 */
QPushButton#SideButton:checked {{
    border: none;
    outline: none;
    background: qlineargradient(
        x1: 0, y1: 0,
        x2: 1, y2: 0,
        stop: 0 {primary},
        stop: 0.56 {primary_hover},
        stop: 1 {primary_light}
    );
    color: {text_inverse};
    font-weight: 700;
}}

/* 选中 + 悬停：仍然无边框，只稍微加深左侧渐变。 */
QPushButton#SideButton:checked:hover {{
    border: none;
    outline: none;
    background: qlineargradient(
        x1: 0, y1: 0,
        x2: 1, y2: 0,
        stop: 0 {primary_pressed},
        stop: 0.56 {primary},
        stop: 1 {primary_light}
    );
    color: {text_inverse};
}}

/* 焦点态强制取消外框，避免 Tab/鼠标点击后出现焦点描边。 */
QPushButton#SideButton:focus,
QPushButton#SideButton:hover:focus,
QPushButton#SideButton:checked:focus,
QPushButton#SideButton:checked:hover:focus {{
    border: none;
    outline: none;
}}

/* 不可用：文字灰一些，图标仍由 main_window.py 使用普通深色 SVG。 */
QPushButton#SideButton:disabled,
QPushButton#SideButton:disabled:hover,
QPushButton#SideButton:disabled:focus {{
    background: transparent;
    border: none;
    outline: none;
    color: {text_muted};
}}
"""


def get_app_stylesheet(theme_key: Optional[str] = None) -> str:
    """
    返回全局 QSS。

    兼容旧调用：
        main.py 里原来是 get_app_stylesheet()
        这里保留无参数调用，默认使用 medical_blue。
    """
    theme = get_theme(theme_key or DEFAULT_THEME_KEY)

    primary = _qss_value(theme, "primary")
    primary_hover = _qss_value(theme, "primary_hover")
    primary_pressed = _qss_value(theme, "primary_pressed")
    primary_light = _qss_value(theme, "primary_light")
    primary_lighter = _qss_value(theme, "primary_lighter")
    primary_border = _qss_value(theme, "primary_border")

    background = _qss_value(theme, "background")
    background_alt = _qss_value(theme, "background_alt")
    surface = _qss_value(theme, "surface")
    surface_alt = _qss_value(theme, "surface_alt")
    surface_hover = _qss_value(theme, "surface_hover")
    border = _qss_value(theme, "border")
    border_light = _qss_value(theme, "border_light")
    divider = _qss_value(theme, "divider")

    text_primary = _qss_value(theme, "text_primary")
    text_secondary = _qss_value(theme, "text_secondary")
    text_muted = _qss_value(theme, "text_muted")
    text_inverse = _qss_value(theme, "text_inverse")
    title = _qss_value(theme, "title")

    success = _qss_value(theme, "success")
    success_bg = _qss_value(theme, "success_bg")
    success_border = _qss_value(theme, "success_border")

    warning = _qss_value(theme, "warning")
    warning_bg = _qss_value(theme, "warning_bg")
    warning_border = _qss_value(theme, "warning_border")

    danger = _qss_value(theme, "danger")
    danger_bg = _qss_value(theme, "danger_bg")
    danger_border = _qss_value(theme, "danger_border")

    info = _qss_value(theme, "info")
    info_bg = _qss_value(theme, "info_bg")
    info_border = _qss_value(theme, "info_border")

    purple = _qss_value(theme, "purple")
    purple_bg = _qss_value(theme, "purple_bg")
    purple_border = _qss_value(theme, "purple_border")

    table_header_bg = _qss_value(theme, "table_header_bg")
    table_alt_bg = _qss_value(theme, "table_alt_bg")
    table_grid = _qss_value(theme, "table_grid")
    table_selected_bg = _qss_value(theme, "table_selected_bg")

    side_navigation_qss = _side_navigation_qss(theme)

    return f"""
/* -------------------------
   全局基础
------------------------- */
QWidget {{
    font-family: "Microsoft YaHei";
    font-size: 13px;
    color: {text_primary};
    background-color: {background};
}}

QMainWindow {{
    background-color: {background};
}}

QLabel {{
    background: transparent;
    color: {text_primary};
}}

QFrame {{
    background: transparent;
}}

QToolTip {{
    background-color: {surface};
    color: {text_primary};
    border: 1px solid {border};
    padding: 6px 8px;
    border-radius: 4px;
}}

/* -------------------------
   页面标题与提示
------------------------- */
QLabel#PageTitle {{
    font-size: 22px;
    font-weight: 700;
    color: {title};
    padding: 0px;
    margin: 0px;
}}

QLabel#PageSubtitle {{
    color: {text_secondary};
    font-size: 13px;
}}

QLabel#CurrentCaseLabel {{
    color: {text_secondary};
    font-size: 13px;
}}

QLabel#SectionHint {{
    color: {text_muted};
    font-size: 12px;
}}

QLabel#SectionTitle {{
    color: {title};
    font-size: 15px;
    font-weight: 700;
}}

/* -------------------------
   主窗口骨架
------------------------- */
QFrame#ContentFrame {{
    background-color: {background};
}}

QFrame#UnifiedPageHeader {{
    background-color: {background};
    border-bottom: 1px solid {border};
}}

QLabel#UnifiedPageTitle {{
    color: {title};
    font-size: 22px;
    font-weight: 700;
}}

QLabel#UnifiedPageContext {{
    color: {text_secondary};
    font-size: 13px;
}}

QLabel#UnifiedPageIcon {{
    background-color: transparent;
}}

QStackedWidget#PageStack {{
    background-color: {background};
}}

/* -------------------------
   左侧导航
------------------------- */
{side_navigation_qss}

/* -------------------------
   卡片 / 分组
------------------------- */
QFrame#Card,
QFrame#InfoCard,
QFrame#StatCard,
QFrame#PanelCard {{
    background-color: {surface};
    border: 1px solid {border};
    border-radius: 10px;
}}

QFrame#SubCard {{
    background-color: {surface_alt};
    border: 1px solid {border_light};
    border-radius: 8px;
}}

QGroupBox {{
    background-color: {surface};
    border: 1px solid {border};
    border-radius: 8px;
    margin-top: 10px;
    padding: 14px 10px 10px 10px;
    font-weight: 700;
    color: {title};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    top: 0px;
    padding: 0px 6px;
    color: {title};
    background-color: {background};
}}

/* -------------------------
   按钮
------------------------- */
QPushButton {{
    min-height: 30px;
    padding: 5px 14px;
    border: 1px solid {border};
    border-radius: 6px;
    background-color: {surface};
    color: {text_primary};
}}

QPushButton:hover {{
    background-color: {surface_hover};
    border-color: {primary_border};
    color: {primary_pressed};
}}

QPushButton:pressed {{
    background-color: {primary_light};
    border-color: {primary};
}}

QPushButton:disabled {{
    color: {text_muted};
    background-color: {surface_alt};
    border-color: {border_light};
}}

QPushButton#PrimaryButton {{
    background-color: {primary};
    border-color: {primary};
    color: {text_inverse};
    font-weight: 700;
}}

QPushButton#PrimaryButton:hover {{
    background-color: {primary_hover};
    border-color: {primary_hover};
    color: {text_inverse};
}}

QPushButton#PrimaryButton:pressed {{
    background-color: {primary_pressed};
    border-color: {primary_pressed};
    color: {text_inverse};
}}

QPushButton#SecondaryButton {{
    background-color: {surface};
    border-color: {primary_border};
    color: {primary};
    font-weight: 600;
}}

QPushButton#SecondaryButton:hover {{
    background-color: {primary_lighter};
    border-color: {primary};
}}

QPushButton#DangerButton {{
    background-color: {surface};
    border-color: {danger_border};
    color: {danger};
    font-weight: 600;
}}

QPushButton#DangerButton:hover {{
    background-color: {danger_bg};
    border-color: {danger};
}}

QPushButton#WarningButton {{
    background-color: {surface};
    border-color: {warning_border};
    color: {warning};
    font-weight: 600;
}}

QPushButton#WarningButton:hover {{
    background-color: {warning_bg};
    border-color: {warning};
}}

QPushButton#CollapseButton {{
    min-width: 24px;
    max-width: 28px;
    min-height: 22px;
    max-height: 24px;
    padding: 0px;
    font-weight: 700;
}}

QPushButton#ProteinButton {{
    min-width: 82px;
    min-height: 30px;
    border-radius: 15px;
    padding: 4px 14px;
}}

QPushButton#ProteinButton:checked {{
    background-color: {primary};
    border-color: {primary};
    color: {text_inverse};
    font-weight: 700;
}}

/* -------------------------
   输入框 / 下拉框
------------------------- */
QLineEdit,
QTextEdit,
QPlainTextEdit,
QComboBox,
QSpinBox,
QDoubleSpinBox,
QDateEdit,
QTimeEdit,
QDateTimeEdit {{
    background-color: {surface};
    border: 1px solid {border};
    border-radius: 6px;
    min-height: 28px;
    padding: 4px 8px;
    color: {text_primary};
    selection-background-color: {table_selected_bg};
}}

QLineEdit:focus,
QTextEdit:focus,
QPlainTextEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QDateEdit:focus,
QTimeEdit:focus,
QDateTimeEdit:focus {{
    border-color: {primary};
}}

QLineEdit:disabled,
QTextEdit:disabled,
QPlainTextEdit:disabled,
QComboBox:disabled {{
    background-color: {surface_alt};
    color: {text_muted};
}}

QComboBox {{
    padding-left: 8px;
    padding-right: 24px;
}}

QComboBox QAbstractItemView {{
    background-color: {surface};
    border: 1px solid {border};
    selection-background-color: {table_selected_bg};
    selection-color: {text_primary};
    outline: none;
}}

/* -------------------------
   表格
------------------------- */
QTableWidget,
QTableView {{
    background-color: {surface};
    alternate-background-color: {table_alt_bg};
    gridline-color: {table_grid};
    border: 1px solid {border};
    border-radius: 6px;
    selection-background-color: {table_selected_bg};
    selection-color: {text_primary};
}}

QTableWidget::item,
QTableView::item {{
    padding: 4px;
}}

QTableWidget::item:selected,
QTableView::item:selected {{
    background-color: {table_selected_bg};
    color: {text_primary};
}}

QHeaderView::section {{
    background-color: {table_header_bg};
    color: {text_primary};
    font-weight: 700;
    border: none;
    border-right: 1px solid {table_grid};
    border-bottom: 1px solid {table_grid};
    padding: 6px 5px;
    min-height: 28px;
}}

QTableCornerButton::section {{
    background-color: {table_header_bg};
    border: none;
    border-right: 1px solid {table_grid};
    border-bottom: 1px solid {table_grid};
}}

/* -------------------------
   状态标签
------------------------- */
QLabel#StatusBadge {{
    border-radius: 5px;
    padding: 3px 8px;
    font-size: 12px;
    font-weight: 700;
}}

QLabel#StatusBadge[status="success"] {{
    color: {success};
    background-color: {success_bg};
    border: 1px solid {success_border};
}}

QLabel#StatusBadge[status="warning"] {{
    color: {warning};
    background-color: {warning_bg};
    border: 1px solid {warning_border};
}}

QLabel#StatusBadge[status="danger"] {{
    color: {danger};
    background-color: {danger_bg};
    border: 1px solid {danger_border};
}}

QLabel#StatusBadge[status="info"] {{
    color: {info};
    background-color: {info_bg};
    border: 1px solid {info_border};
}}

QLabel#StatusBadge[status="purple"] {{
    color: {purple};
    background-color: {purple_bg};
    border: 1px solid {purple_border};
}}

/* 兼容后续可能直接命名的状态标签 */
QLabel#StatusSuccess {{
    color: {success};
    background-color: {success_bg};
    border: 1px solid {success_border};
    border-radius: 5px;
    padding: 3px 8px;
    font-weight: 700;
}}

QLabel#StatusWarning {{
    color: {warning};
    background-color: {warning_bg};
    border: 1px solid {warning_border};
    border-radius: 5px;
    padding: 3px 8px;
    font-weight: 700;
}}

QLabel#StatusDanger {{
    color: {danger};
    background-color: {danger_bg};
    border: 1px solid {danger_border};
    border-radius: 5px;
    padding: 3px 8px;
    font-weight: 700;
}}

QLabel#StatusInfo {{
    color: {info};
    background-color: {info_bg};
    border: 1px solid {info_border};
    border-radius: 5px;
    padding: 3px 8px;
    font-weight: 700;
}}

/* -------------------------
   Tab
------------------------- */
QTabWidget::pane {{
    border: 1px solid {border};
    background-color: {surface};
    border-radius: 6px;
}}

QTabBar::tab {{
    background-color: {background};
    border: 1px solid {border};
    border-bottom: none;
    padding: 7px 16px;
    margin-right: 2px;
    color: {text_primary};
}}

QTabBar::tab:selected {{
    background-color: {surface};
    color: {primary};
    font-weight: 700;
}}

QTabBar::tab:hover {{
    background-color: {surface_hover};
}}

/* -------------------------
   进度条
------------------------- */
QProgressBar {{
    background-color: {surface_alt};
    border: 1px solid {border};
    border-radius: 6px;
    min-height: 16px;
    text-align: center;
    color: {text_primary};
}}

QProgressBar::chunk {{
    background-color: {primary};
    border-radius: 5px;
}}

/* -------------------------
   滚动条
------------------------- */
QScrollBar:vertical {{
    background-color: {background_alt};
    width: 10px;
    margin: 0px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background-color: {border};
    min-height: 30px;
    border-radius: 5px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {primary_border};
}}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0px;
    background: none;
    border: none;
}}

QScrollBar:horizontal {{
    background-color: {background_alt};
    height: 10px;
    margin: 0px;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background-color: {border};
    min-width: 30px;
    border-radius: 5px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {primary_border};
}}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0px;
    background: none;
    border: none;
}}

/* -------------------------
   状态栏
------------------------- */
QStatusBar {{
    background-color: {surface};
    border-top: 1px solid {border};
    color: {text_primary};
    min-height: 24px;
}}

QStatusBar::item {{
    border: none;
}}

/* -------------------------
   分割线
------------------------- */
QFrame#HLine {{
    background-color: {divider};
    max-height: 1px;
    min-height: 1px;
}}

QFrame#VLine {{
    background-color: {divider};
    max-width: 1px;
    min-width: 1px;
}}
"""


def get_main_window_stylesheet(theme_key: Optional[str] = None) -> str:
    """
    主窗口局部样式。

    当前 main_window.py 会调用这个函数给主窗口设置局部样式。
    这里重点覆盖左侧导航栏、右侧页面背景和统一标题栏。
    """
    theme = get_theme(theme_key or DEFAULT_THEME_KEY)

    background = theme["background"]
    border = theme["border"]
    text_secondary = theme["text_secondary"]
    title = theme["title"]

    return f"""
{_side_navigation_qss(theme)}

QFrame#ContentFrame {{
    background-color: {background};
}}

QFrame#UnifiedPageHeader {{
    background-color: {background};
    border-bottom: 1px solid {border};
}}

QLabel#UnifiedPageTitle {{
    color: {title};
    font-size: 22px;
    font-weight: 700;
}}

QLabel#UnifiedPageContext {{
    color: {text_secondary};
    font-size: 13px;
}}
"""
