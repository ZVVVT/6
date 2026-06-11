"""
全局界面样式。

作用：
1. 统一按钮、表格、输入框、分组框、状态栏的视觉风格。
2. 让病例管理、病例详情、蛋白分析、报告管理、系统设置的基础控件风格一致。
3. 各页面仍然可以保留自己的特殊布局，例如蛋白分析页的图像核查区。
"""


def get_app_stylesheet() -> str:
    return """
    /* -------------------------
       全局基础
    ------------------------- */
    QWidget {
        font-family: "Microsoft YaHei";
        font-size: 13px;
        color: #1f2933;
        background-color: #f5f7fb;
    }

    QMainWindow {
        background-color: #f5f7fb;
    }

    QLabel {
        background: transparent;
    }

    /* 页面标题：各页面如果已有内联样式，会继续以页面自身为准 */
    QLabel#PageTitle {
        font-size: 22px;
        font-weight: 700;
        color: #1f4e79;
        padding: 0px;
        margin: 0px;
    }

    QLabel#CurrentCaseLabel {
        color: #4b5563;
        font-size: 13px;
    }

    QLabel#SectionHint {
        color: #667085;
        font-size: 12px;
    }

    /* -------------------------
       左侧功能菜单
    ------------------------- */
    QFrame#SideMenu {
        background-color: #f4f7fb;
        border-right: 1px solid #d9e2ef;
    }

    QLabel#SideTitle {
        color: #111827;
        font-size: 17px;
        font-weight: 700;
        padding-left: 16px;
        padding-bottom: 8px;
        background: transparent;
    }

    QPushButton#SideButton {
        height: 42px;
        padding-left: 18px;
        text-align: left;
        border: none;
        border-radius: 0px;
        background-color: transparent;
        color: #111827;
        font-size: 14px;
    }

    QPushButton#SideButton:hover {
        background-color: #eaf2ff;
    }

    QPushButton#SideButton:checked {
        background-color: #d8e8ff;
        color: #111827;
        font-weight: 700;
    }

    QPushButton#SideButton:disabled {
        color: #a0a7b2;
        background-color: transparent;
    }

    /* -------------------------
       分组卡片
    ------------------------- */
    QGroupBox {
        background-color: #ffffff;
        border: 1px solid #d9e2ef;
        border-radius: 6px;
        margin-top: 10px;
        padding: 12px 10px 10px 10px;
        font-weight: 700;
        color: #1f4e79;
    }

    QGroupBox::title {
        subcontrol-origin: margin;
        left: 10px;
        top: 0px;
        padding: 0px 6px;
        color: #1f4e79;
        background-color: #f5f7fb;
    }

    /* -------------------------
       按钮
    ------------------------- */
    QPushButton {
        min-height: 28px;
        padding: 4px 12px;
        border: 1px solid #b7c9dd;
        border-radius: 4px;
        background-color: #ffffff;
        color: #111827;
    }

    QPushButton:hover {
        background-color: #eef6ff;
        border-color: #7fb0e6;
    }

    QPushButton:pressed {
        background-color: #dcecff;
        border-color: #5a9bd8;
    }

    QPushButton:disabled {
        color: #a0a7b2;
        background-color: #f1f3f6;
        border-color: #d9dee6;
    }

    /* 强调按钮，后续如果需要可给按钮 setObjectName("PrimaryButton") */
    QPushButton#PrimaryButton {
        background-color: #1f6fbf;
        border-color: #1f6fbf;
        color: #ffffff;
        font-weight: 700;
    }

    QPushButton#PrimaryButton:hover {
        background-color: #2d7fd1;
    }

    /* 折叠按钮 + / - */
    QPushButton#CollapseButton {
        min-width: 24px;
        max-width: 28px;
        min-height: 22px;
        max-height: 24px;
        padding: 0px;
        font-weight: 700;
    }

    /* 蛋白项目切换按钮，可由 analysis_window.py 单独覆盖颜色 */
    QPushButton#ProteinButton {
        min-width: 82px;
        min-height: 30px;
        border-radius: 15px;
        padding: 4px 14px;
    }

    /* -------------------------
       输入框 / 下拉框
    ------------------------- */
    QLineEdit,
    QTextEdit,
    QPlainTextEdit,
    QComboBox {
        background-color: #ffffff;
        border: 1px solid #c7d5e6;
        border-radius: 4px;
        min-height: 26px;
        padding: 3px 7px;
        selection-background-color: #d8e8ff;
    }

    QLineEdit:focus,
    QTextEdit:focus,
    QPlainTextEdit:focus,
    QComboBox:focus {
        border-color: #5a9bd8;
    }

    QComboBox {
        padding-right: 18px;
    }

    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 20px;
        border-left: 1px solid #c7d5e6;
        border-top-right-radius: 4px;
        border-bottom-right-radius: 4px;
        background-color: #f7f9fc;
    }

    QComboBox::down-arrow {
        width: 0px;
        height: 0px;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 6px solid #475467;
        margin-right: 6px;
    }

    QComboBox QAbstractItemView {
        background-color: #ffffff;
        border: 1px solid #c7d5e6;
        selection-background-color: #d8e8ff;
        selection-color: #111827;
        outline: none;
    }

    /* -------------------------
       表格
    ------------------------- */
    QTableWidget,
    QTableView {
        background-color: #ffffff;
        alternate-background-color: #f7f9fc;
        gridline-color: #d9e2ef;
        border: 1px solid #d9e2ef;
        border-radius: 4px;
        selection-background-color: #d8e8ff;
        selection-color: #111827;
    }

    QHeaderView::section {
        background-color: #eef4fb;
        color: #111827;
        font-weight: 700;
        border: none;
        border-right: 1px solid #d9e2ef;
        border-bottom: 1px solid #d9e2ef;
        padding: 5px 4px;
        min-height: 26px;
    }

    QTableCornerButton::section {
        background-color: #eef4fb;
        border: none;
        border-right: 1px solid #d9e2ef;
        border-bottom: 1px solid #d9e2ef;
    }

    /* -------------------------
       Tab
    ------------------------- */
    QTabWidget::pane {
        border: 1px solid #d9e2ef;
        background-color: #ffffff;
        border-radius: 4px;
    }

    QTabBar::tab {
        background-color: #f5f7fb;
        border: 1px solid #d9e2ef;
        border-bottom: none;
        padding: 6px 14px;
        margin-right: 2px;
        color: #111827;
    }

    QTabBar::tab:selected {
        background-color: #ffffff;
        color: #1f4e79;
        font-weight: 700;
    }

    QTabBar::tab:hover {
        background-color: #eef6ff;
    }

    /* -------------------------
       滚动条
    ------------------------- */
    QScrollBar:vertical {
        background-color: #f1f3f6;
        width: 10px;
        margin: 0px;
        border: none;
    }

    QScrollBar::handle:vertical {
        background-color: #c7d5e6;
        min-height: 30px;
        border-radius: 5px;
    }

    QScrollBar::handle:vertical:hover {
        background-color: #a9bdd4;
    }

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {
        height: 0px;
        background: none;
        border: none;
    }

    QScrollBar:horizontal {
        background-color: #f1f3f6;
        height: 10px;
        margin: 0px;
        border: none;
    }

    QScrollBar::handle:horizontal {
        background-color: #c7d5e6;
        min-width: 30px;
        border-radius: 5px;
    }

    QScrollBar::handle:horizontal:hover {
        background-color: #a9bdd4;
    }

    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal {
        width: 0px;
        background: none;
        border: none;
    }

    /* -------------------------
       状态栏
    ------------------------- */
    QStatusBar {
        background-color: #ffffff;
        border-top: 1px solid #d9e2ef;
        color: #111827;
        min-height: 24px;
    }

    QStatusBar::item {
        border: none;
    }
    """
