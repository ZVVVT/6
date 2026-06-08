from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QStackedWidget
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("人精子蛋白质量分析软件")
        self.resize(1200, 800)

        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        main_layout = QHBoxLayout(central_widget)

        side_layout = QVBoxLayout()

        self.btn_cases = QPushButton("病例管理")
        self.btn_analysis = QPushButton("蛋白分析")
        self.btn_reports = QPushButton("报告管理")
        self.btn_settings = QPushButton("系统设置")

        side_layout.addWidget(QLabel("功能菜单"))
        side_layout.addWidget(self.btn_cases)
        side_layout.addWidget(self.btn_analysis)
        side_layout.addWidget(self.btn_reports)
        side_layout.addWidget(self.btn_settings)
        side_layout.addStretch()

        self.stack = QStackedWidget()
        self.page_cases = QLabel("病例管理页面")
        self.page_analysis = QLabel("蛋白分析页面")
        self.page_reports = QLabel("报告管理页面")
        self.page_settings = QLabel("系统设置页面")

        self.stack.addWidget(self.page_cases)
        self.stack.addWidget(self.page_analysis)
        self.stack.addWidget(self.page_reports)
        self.stack.addWidget(self.page_settings)

        self.btn_cases.clicked.connect(lambda: self.stack.setCurrentWidget(self.page_cases))
        self.btn_analysis.clicked.connect(lambda: self.stack.setCurrentWidget(self.page_analysis))
        self.btn_reports.clicked.connect(lambda: self.stack.setCurrentWidget(self.page_reports))
        self.btn_settings.clicked.connect(lambda: self.stack.setCurrentWidget(self.page_settings))

        main_layout.addLayout(side_layout, 1)
        main_layout.addWidget(self.stack, 5)

        self.setCentralWidget(central_widget)