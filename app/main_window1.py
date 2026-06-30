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
    def on_config_saved(self):
        if hasattr(self.page_analysis, "reload_config"):
            self.page_analysis.reload_config()

        if hasattr(self.page_reports, "reload_config"):
            self.page_reports.reload_config()

        if hasattr(self.page_detail, "reload_config"):
            self.page_detail.reload_config()

        self.statusBar().showMessage("系统配置已刷新，无需重启。")

    def __init__(self):
        super().__init__()

        self.setWindowTitle("人精子蛋白质量分析软件")

        # 默认窗口尺寸按蛋白分析页面设置，避免页面切换时窗口跳变
        self.resize(1650, 1000)
        self.setMinimumSize(1650, 1000)

        self.database = Database("data/analysis.db")
        self.current_case = None

        self.init_ui()

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
        # 右侧页面堆栈
        # -------------------------
        self.stack = QStackedWidget()
        self.stack.setObjectName("PageStack")

        self.page_cases = CaseManagerWindow(self.database)
        self.page_detail = CaseDetailWindow(self.database)
        self.page_analysis = AnalysisWindow(self.database)
        self.page_reports = ReportWindow(self.database)
        self.page_settings = SettingsWindow()

        self.page_settings.config_saved.connect(self.on_config_saved)

        self.stack.addWidget(self.page_cases)
        self.stack.addWidget(self.page_detail)
        self.stack.addWidget(self.page_analysis)
        self.stack.addWidget(self.page_reports)
        self.stack.addWidget(self.page_settings)

        self.page_cases.case_selected.connect(self.on_case_selected)
        self.page_detail.start_analysis_requested.connect(self.open_analysis_for_case)
        self.page_detail.report_requested.connect(self.open_report_for_case)

        self.btn_cases.clicked.connect(lambda: self.switch_page(self.page_cases, self.btn_cases))
        self.btn_detail.clicked.connect(lambda: self.switch_page(self.page_detail, self.btn_detail))
        self.btn_analysis.clicked.connect(lambda: self.open_analysis_for_case(self.current_case))
        self.btn_reports.clicked.connect(lambda: self.open_report_for_case(self.current_case))
        self.btn_settings.clicked.connect(lambda: self.switch_page(self.page_settings, self.btn_settings))

        main_layout.addWidget(side_frame)
        main_layout.addWidget(self.stack, 1)

        self.setCentralWidget(central_widget)

        self.set_case_related_buttons_enabled(False)
        self.statusBar().showMessage("系统就绪")
        self.switch_page(self.page_cases, self.btn_cases)

    def set_case_related_buttons_enabled(self, enabled: bool):
        self.btn_detail.setEnabled(enabled)
        self.btn_analysis.setEnabled(enabled)
        self.btn_reports.setEnabled(enabled)

    def switch_page(self, page, active_button):
        self.stack.setCurrentWidget(page)

        for btn in self.menu_buttons:
            btn.setChecked(btn == active_button)

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
            f"当前选择病例：{case_no} - {patient_name}    样本号：{sample_no}"
        )

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

    def create_placeholder_page(self, title: str, message: str):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)

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
