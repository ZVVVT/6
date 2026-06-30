from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QStackedWidget,
    QFrame,
)

from app.analysis_window import AnalysisWindow
from app.case_detail_window import CaseDetailWindow
from app.case_manager_window import CaseManagerWindow
from app.report_window import ReportWindow
from app.settings_window import SettingsWindow
from core.database import Database


class MainWindow(QMainWindow):
    """
    主窗口统一页面骨架。

    V1 目标：
    1. 左侧菜单固定。
    2. 右侧所有页面共用统一标题栏。
    3. 隐藏各页面内部原来的标题，避免切换页面时标题位置跳动。
    4. 统一各页面主体边距，让内容起点更一致。

    注意：
    - 不改各页面原有业务逻辑。
    - 不改蛋白分析、病例详情、报告生成等功能。
    - 只在 MainWindow 层做统一外壳。
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("人精子蛋白质量分析软件")

        # 默认窗口尺寸按蛋白分析页面设置，避免页面切换时窗口跳变
        self.resize(1650, 1000)
        self.setMinimumSize(1650, 1000)

        self.database = Database("data/analysis.db")
        self.current_case = None

        self._page_title_map = {}
        self._page_button_map = {}

        self.init_ui()

    # ------------------------------------------------------------------
    # UI 初始化
    # ------------------------------------------------------------------
    def init_ui(self):
        central_widget = QWidget()
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # -------------------------
        # 左侧功能菜单
        # -------------------------
        side_frame = QFrame()
        side_frame.setObjectName("SideMenu")
        side_frame.setFixedWidth(180)

        side_layout = QVBoxLayout(side_frame)
        side_layout.setContentsMargins(0, 16, 0, 16)
        side_layout.setSpacing(4)

        title_label = QLabel("功能菜单")
        title_label.setObjectName("SideTitle")
        side_layout.addWidget(title_label)

        self.btn_cases = QPushButton("病例管理")
        self.btn_detail = QPushButton("病例详情")
        self.btn_analysis = QPushButton("蛋白分析")
        self.btn_reports = QPushButton("报告管理")
        self.btn_settings = QPushButton("系统设置")

        self.menu_buttons = [
            self.btn_cases,
            self.btn_detail,
            self.btn_analysis,
            self.btn_reports,
            self.btn_settings,
        ]

        for btn in self.menu_buttons:
            btn.setObjectName("SideButton")
            btn.setCheckable(True)
            side_layout.addWidget(btn)

        side_layout.addStretch()

        # -------------------------
        # 右侧统一页面区域
        # -------------------------
        content_frame = QFrame()
        content_frame.setObjectName("ContentFrame")
        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.header_frame = QFrame()
        self.header_frame.setObjectName("UnifiedPageHeader")
        self.header_frame.setFixedHeight(58)

        header_layout = QHBoxLayout(self.header_frame)
        header_layout.setContentsMargins(18, 0, 18, 0)
        header_layout.setSpacing(12)

        self.header_title_label = QLabel("病例管理")
        self.header_title_label.setObjectName("UnifiedPageTitle")
        self.header_title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.header_context_label = QLabel("")
        self.header_context_label.setObjectName("UnifiedPageContext")
        self.header_context_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.header_context_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        header_layout.addWidget(self.header_title_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.header_context_label)

        self.stack = QStackedWidget()
        self.stack.setObjectName("PageStack")

        content_layout.addWidget(self.header_frame)
        content_layout.addWidget(self.stack, 1)

        # -------------------------
        # 页面实例
        # -------------------------
        self.page_cases = CaseManagerWindow(self.database)
        self.page_detail = CaseDetailWindow(self.database)
        self.page_analysis = AnalysisWindow(self.database)
        self.page_reports = ReportWindow(self.database)
        self.page_settings = SettingsWindow()

        self.page_settings.config_saved.connect(self.on_config_saved)

        self.register_page(self.page_cases, self.btn_cases, "病例管理")
        self.register_page(self.page_detail, self.btn_detail, "病例详情")
        self.register_page(self.page_analysis, self.btn_analysis, "蛋白分析")
        self.register_page(self.page_reports, self.btn_reports, "报告管理")
        self.register_page(self.page_settings, self.btn_settings, "系统设置")

        # -------------------------
        # 信号绑定
        # -------------------------
        self.page_cases.case_selected.connect(self.on_case_selected)
        self.page_detail.start_analysis_requested.connect(self.open_analysis_for_case)
        self.page_detail.report_requested.connect(self.open_report_for_case)

        self.btn_cases.clicked.connect(lambda: self.switch_page(self.page_cases, self.btn_cases))
        self.btn_detail.clicked.connect(lambda: self.switch_page(self.page_detail, self.btn_detail))
        self.btn_analysis.clicked.connect(lambda: self.open_analysis_for_case(self.current_case))
        self.btn_reports.clicked.connect(lambda: self.open_report_for_case(self.current_case))
        self.btn_settings.clicked.connect(lambda: self.switch_page(self.page_settings, self.btn_settings))

        main_layout.addWidget(side_frame)
        main_layout.addWidget(content_frame, 1)

        self.setCentralWidget(central_widget)
        self.apply_unified_style()

        self.set_case_related_buttons_enabled(False)
        self.statusBar().showMessage("系统就绪")
        self.switch_page(self.page_cases, self.btn_cases)

    def register_page(self, page: QWidget, button: QPushButton, title: str):
        self.stack.addWidget(page)
        self._page_title_map[page] = title
        self._page_button_map[page] = button
        self.prepare_embedded_page(page, title)

    def prepare_embedded_page(self, page: QWidget, page_title: str):
        """
        统一页面主体边距，并隐藏页面内部旧标题。

        原来每个页面自己绘制标题，所以标题位置不一致。
        现在标题由 MainWindow 统一绘制，页面内部的同名标题需要隐藏。
        """
        layout = page.layout()
        if layout is not None:
            layout.setContentsMargins(18, 10, 18, 18)
            layout.setSpacing(10)

        # 隐藏页面内部标题。部分页面标题是局部变量，无法通过属性访问，
        # 所以这里递归查找 QLabel 文本。
        for label in page.findChildren(QLabel):
            text = (label.text() or "").strip()
            if text == page_title:
                label.hide()

        # 部分页有顶部“当前病例：xxx”的旧状态标签，统一放到主标题栏右侧显示。
        for attr_name in (
            "status_label",
            "case_status_label",
            "case_info_label",
            "current_case_label",
            "top_case_label",
        ):
            label = getattr(page, attr_name, None)
            if isinstance(label, QLabel):
                label.hide()

    # ------------------------------------------------------------------
    # 统一样式
    # ------------------------------------------------------------------
    def apply_unified_style(self):
        self.setStyleSheet(
            """
            QFrame#ContentFrame {
                background-color: #f5f7fb;
            }

            QFrame#UnifiedPageHeader {
                background-color: #f5f7fb;
                border-bottom: 1px solid #d9e2ef;
            }

            QLabel#UnifiedPageTitle {
                color: #1f4e79;
                font-family: Microsoft YaHei;
                font-size: 22px;
                font-weight: 700;
            }

            QLabel#UnifiedPageContext {
                color: #4d5b6a;
                font-family: Microsoft YaHei;
                font-size: 13px;
            }
            """
        )

    # ------------------------------------------------------------------
    # 配置刷新
    # ------------------------------------------------------------------
    def on_config_saved(self):
        if hasattr(self.page_analysis, "reload_config"):
            self.page_analysis.reload_config()
        if hasattr(self.page_reports, "reload_config"):
            self.page_reports.reload_config()
        if hasattr(self.page_detail, "reload_config"):
            self.page_detail.reload_config()
        self.statusBar().showMessage("系统配置已刷新，无需重启。")
        self.refresh_header_context()

    # ------------------------------------------------------------------
    # 页面切换
    # ------------------------------------------------------------------
    def set_case_related_buttons_enabled(self, enabled: bool):
        self.btn_detail.setEnabled(enabled)
        self.btn_analysis.setEnabled(enabled)
        self.btn_reports.setEnabled(enabled)

    def switch_page(self, page, active_button):
        self.stack.setCurrentWidget(page)
        for btn in self.menu_buttons:
            btn.setChecked(btn == active_button)
        self.refresh_header()
        self.hide_old_context_labels(page)

    def refresh_header(self):
        page = self.stack.currentWidget()
        title = self._page_title_map.get(page, "")
        self.header_title_label.setText(title)
        self.refresh_header_context()

    def refresh_header_context(self):
        page = self.stack.currentWidget()
        title = self._page_title_map.get(page, "")

        # 病例管理、系统设置不绑定当前病例，右侧不显示病例上下文。
        if title in ("病例管理", "系统设置"):
            self.header_context_label.setText("")
            return

        if not self.current_case:
            self.header_context_label.setText("未选择病例")
            return

        case_no = self.current_case.get("case_no", "")
        patient_name = self.current_case.get("patient_name", "")
        sample_no = self.current_case.get("sample_no", "")
        test_date = self.current_case.get("test_date", "")

        if title == "蛋白分析":
            self.header_context_label.setText(
                f"当前病例：{case_no}    姓名：{patient_name}    样本号：{sample_no}    检测日期：{test_date}"
            )
        elif title == "报告管理":
            self.header_context_label.setText(f"当前病例：{case_no} - {patient_name}")
        else:
            self.header_context_label.setText(f"当前病例：{case_no} - {patient_name}")

    def hide_old_context_labels(self, page: QWidget):
        """
        页面切换或病例刷新后，页面内部旧的顶部病例状态可能被重新 setText。
        这里再次隐藏，避免和统一标题栏重复。
        """
        if page is None:
            return

        for label in page.findChildren(QLabel):
            text = (label.text() or "").strip()
            if text.startswith("当前病例："):
                label.hide()

    # ------------------------------------------------------------------
    # 病例选择与跨页面跳转
    # ------------------------------------------------------------------
    def on_case_selected(self, case_data: dict):
        self.set_current_case(case_data)
        self.switch_page(self.page_detail, self.btn_detail)

    def set_current_case(self, case_data: dict):
        if not case_data:
            return

        case_id = case_data.get("id")
        if case_id:
            fresh_case = self.database.get_case(case_id)
            if fresh_case:
                case_data = fresh_case

        self.current_case = case_data

        self.page_detail.set_case(case_data)
        self.page_analysis.set_case(case_data)
        self.page_reports.set_case(case_data)

        self.set_case_related_buttons_enabled(True)

        case_no = case_data.get("case_no", "")
        patient_name = case_data.get("patient_name", "")
        sample_no = case_data.get("sample_no", "")
        self.statusBar().showMessage(
            f"当前选择病例：{case_no} - {patient_name} 样本号：{sample_no}"
        )

        self.refresh_header_context()
        self.hide_old_context_labels(self.page_detail)
        self.hide_old_context_labels(self.page_analysis)
        self.hide_old_context_labels(self.page_reports)

    def open_analysis_for_case(self, case_data):
        if not case_data:
            self.statusBar().showMessage("请先在病例管理中选择病例")
            return
        self.set_current_case(case_data)
        self.switch_page(self.page_analysis, self.btn_analysis)

    def open_report_for_case(self, case_data):
        if not case_data:
            self.statusBar().showMessage("请先在病例管理中选择病例")
            return
        self.set_current_case(case_data)
        self.page_reports.refresh_analysis_results()
        self.switch_page(self.page_reports, self.btn_reports)

    # ------------------------------------------------------------------
    # 旧占位方法保留，避免外部调用报错
    # ------------------------------------------------------------------
    def create_placeholder_page(self, title: str, message: str):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 10, 18, 18)
        layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("PageTitle")
        message_label = QLabel(message)
        message_label.setObjectName("SectionHint")
        message_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        message_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addSpacing(20)
        layout.addWidget(message_label)
        layout.addStretch()
        return page
