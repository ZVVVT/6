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
from app.case_manager_window import CaseManagerWindow
from app.report_window import ReportWindow
from core.database import Database


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("人精子蛋白质量分析软件")
        self.resize(1200, 800)

        self.database = Database("data/analysis.db")
        self.current_case = None

        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        side_frame = QFrame()
        side_frame.setFixedWidth(180)
        side_frame.setStyleSheet("""
            QFrame {
                background-color: #f4f6f8;
                border-right: 1px solid #dcdfe6;
            }
            QPushButton {
                height: 36px;
                text-align: left;
                padding-left: 18px;
                border: none;
                background-color: transparent;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #e6f0ff;
            }
            QPushButton:checked {
                background-color: #d8e8ff;
                font-weight: bold;
            }
            QLabel {
                padding-left: 14px;
            }
        """)

        side_layout = QVBoxLayout(side_frame)
        side_layout.setContentsMargins(0, 16, 0, 16)
        side_layout.setSpacing(6)

        title_label = QLabel("功能菜单")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; padding-bottom: 8px;")
        side_layout.addWidget(title_label)

        self.btn_cases = QPushButton("病例管理")
        self.btn_analysis = QPushButton("蛋白分析")
        self.btn_reports = QPushButton("报告管理")
        self.btn_settings = QPushButton("系统设置")

        for btn in [
            self.btn_cases,
            self.btn_analysis,
            self.btn_reports,
            self.btn_settings,
        ]:
            btn.setCheckable(True)
            side_layout.addWidget(btn)

        side_layout.addStretch()

        self.stack = QStackedWidget()

        self.page_cases = CaseManagerWindow(self.database)
        self.page_analysis = AnalysisWindow(self.database)
        self.page_reports = ReportWindow(self.database)

        self.page_settings = self.create_placeholder_page(
            title="系统设置",
            message="后续用于配置 CellProfiler/MvImageID 路径、pipeline 路径、报告路径等。"
        )

        self.stack.addWidget(self.page_cases)
        self.stack.addWidget(self.page_analysis)
        self.stack.addWidget(self.page_reports)
        self.stack.addWidget(self.page_settings)

        self.page_cases.case_selected.connect(self.on_case_selected)

        self.btn_cases.clicked.connect(lambda: self.switch_page(self.page_cases, self.btn_cases))
        self.btn_analysis.clicked.connect(lambda: self.switch_page(self.page_analysis, self.btn_analysis))
        self.btn_reports.clicked.connect(lambda: self.switch_page(self.page_reports, self.btn_reports))
        self.btn_settings.clicked.connect(lambda: self.switch_page(self.page_settings, self.btn_settings))

        main_layout.addWidget(side_frame)
        main_layout.addWidget(self.stack, 1)

        self.setCentralWidget(central_widget)

        self.statusBar().showMessage("系统就绪")
        self.switch_page(self.page_cases, self.btn_cases)

    def create_placeholder_page(self, title: str, message: str):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 22px; font-weight: bold;")

        message_label = QLabel(message)
        message_label.setStyleSheet("font-size: 14px; color: #666666;")
        message_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        message_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addSpacing(20)
        layout.addWidget(message_label)
        layout.addStretch()

        return page

    def switch_page(self, page, active_button):
        self.stack.setCurrentWidget(page)

        for btn in [
            self.btn_cases,
            self.btn_analysis,
            self.btn_reports,
            self.btn_settings,
        ]:
            btn.setChecked(btn == active_button)

    def on_case_selected(self, case_data: dict):
        self.current_case = case_data

        case_no = case_data.get("case_no", "")
        patient_name = case_data.get("patient_name", "")

        self.page_analysis.set_case(case_data)
        self.page_reports.set_case(case_data)

        self.statusBar().showMessage(f"当前选择病例：{case_no} - {patient_name}")
        self.switch_page(self.page_analysis, self.btn_analysis)