from pathlib import Path

from app.ui_style import get_main_window_stylesheet
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
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
from core.config_manager import ConfigManager
from core.database import Database


class MainWindow(QMainWindow):
    """
    主窗口统一页面骨架。

    本版包含：
    1. 软件名称 / LOGO 从 config.ini 的 [AppInfo] 读取。
    2. 系统设置固定到左侧菜单底部。
    3. 左侧菜单增加 SVG 图标。
    4. 右侧统一标题栏增加当前页面图标。
    5. 左侧菜单选中态：浅蓝背景 + 深蓝文字 + 左侧蓝色竖条。
    6. 鼠标悬停态：浅灰蓝背景。
    """

    PAGE_ICONS = {
        "病例管理": "case_manager.svg",
        "病例详情": "case_detail.svg",
        "蛋白分析": "protein_analysis.svg",
        "报告管理": "report.svg",
        "系统设置": "settings.svg",
    }

    def __init__(self):
        super().__init__()

        # 项目根目录：F:\sperm_protein_analyzer
        self.project_root = Path(__file__).resolve().parents[1]

        # 统一使用项目根目录下的 config.ini，避免从不同目录启动时读错配置。
        self.config_manager = ConfigManager(str(self.project_root / "config.ini"))
        self.config_manager.ensure_default_config()

        # 先应用品牌信息，后续保存设置时也会再次调用。
        self.apply_app_branding()

        # 默认窗口尺寸按蛋白分析页面设置，避免页面切换时窗口跳变。
        self.resize(1650, 1000)
        self.setMinimumSize(1650, 1000)

        # 数据库路径也按项目根目录解析。
        database_path = self.resolve_project_path(self.config_manager.get_database_path())
        self.database = Database(str(database_path))

        self.current_case = None

        self._page_title_map = {}
        self._page_button_map = {}

        self.init_ui()

    # ------------------------------------------------------------------
    # 路径与品牌信息
    # ------------------------------------------------------------------
    def resolve_project_path(self, path_value) -> Path:
        """把配置中的相对路径解析为项目根目录下的绝对路径。"""
        path_text = str(path_value or "").strip()
        if not path_text or path_text == ".":
            return self.project_root
        path = Path(path_text)
        if path.is_absolute():
            return path
        return self.project_root / path

    def icon_path(self, icon_file: str) -> Path:
        """返回 assets/icons 下图标文件路径。"""
        return self.project_root / "assets" / "icons" / icon_file

    def load_icon(self, icon_file: str) -> QIcon:
        """安全加载图标。文件不存在时返回空 QIcon，不影响程序运行。"""
        path = self.icon_path(icon_file)
        if path.exists():
            return QIcon(str(path))
        return QIcon()

    def apply_app_branding(self):
        """
        应用软件名称和窗口 LOGO。

        来源：config.ini
        [AppInfo]
        app_name = xxx
        logo_path = assets\logo.png
        """
        try:
            self.config_manager.load()
        except Exception:
            pass

        app_name = self.config_manager.get_app_name().strip() or "人精子蛋白质量分析软件"
        self.setWindowTitle(app_name)

        app = QApplication.instance()
        if app is not None:
            app.setApplicationName(app_name)

        logo_path = self.resolve_project_path(self.config_manager.get_app_logo_path())
        if not logo_path.exists():
            logo_path = self.project_root / "assets" / "logo.png"

        if logo_path.exists():
            icon = QIcon(str(logo_path))
            if not icon.isNull():
                self.setWindowIcon(icon)
                if app is not None:
                    app.setWindowIcon(icon)

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
        side_layout.setContentsMargins(0, 16, 0, 12)
        side_layout.setSpacing(4)

        # 左侧导航不再显示“功能菜单”标题，减少重复信息。
        # 保留顶部留白，让第一个菜单项不会贴边。

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

        # 图标绑定。
        self.apply_side_button_icon(self.btn_cases, "病例管理")
        self.apply_side_button_icon(self.btn_detail, "病例详情")
        self.apply_side_button_icon(self.btn_analysis, "蛋白分析")
        self.apply_side_button_icon(self.btn_reports, "报告管理")
        self.apply_side_button_icon(self.btn_settings, "系统设置")

        # 上方：日常业务流程入口。
        business_buttons = [
            self.btn_cases,
            self.btn_detail,
            self.btn_analysis,
            self.btn_reports,
        ]

        for btn in business_buttons:
            self.prepare_side_button(btn)
            side_layout.addWidget(btn)

        # 中间留白，把低频的“系统设置”固定到左侧底部。
        side_layout.addStretch(1)

        # 底部：系统设置入口。
        self.prepare_side_button(self.btn_settings)
        side_layout.addWidget(self.btn_settings)

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
        header_layout.setSpacing(10)

        self.header_icon_label = QLabel()
        self.header_icon_label.setObjectName("UnifiedPageIcon")
        self.header_icon_label.setFixedSize(26, 26)
        self.header_icon_label.setAlignment(Qt.AlignCenter)

        self.header_title_label = QLabel("病例管理")
        self.header_title_label.setObjectName("UnifiedPageTitle")
        self.header_title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.header_context_label = QLabel("")
        self.header_context_label.setObjectName("UnifiedPageContext")
        self.header_context_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.header_context_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        header_layout.addWidget(self.header_icon_label)
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

        # 防止某些页面初始化过程中间接覆盖标题，最后再应用一次。
        self.apply_app_branding()

    def prepare_side_button(self, button: QPushButton):
        """统一左侧菜单按钮属性。"""
        button.setObjectName("SideButton")
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setIconSize(QSize(18, 18))
        button.setMinimumHeight(42)

    def apply_side_button_icon(self, button: QPushButton, page_title: str):
        icon_file = self.PAGE_ICONS.get(page_title, "")
        if not icon_file:
            return
        icon = self.load_icon(icon_file)
        if not icon.isNull():
            button.setIcon(icon)
            button.setIconSize(QSize(18, 18))

    def set_header_icon(self, page_title: str):
        icon_file = self.PAGE_ICONS.get(page_title, "")
        icon = self.load_icon(icon_file) if icon_file else QIcon()
        if icon.isNull():
            self.header_icon_label.clear()
            self.header_icon_label.hide()
            return

        pixmap = icon.pixmap(QSize(24, 24))
        self.header_icon_label.setPixmap(pixmap)
        self.header_icon_label.show()

    def register_page(self, page: QWidget, button: QPushButton, title: str):
        self.stack.addWidget(page)
        self._page_title_map[page] = title
        self._page_button_map[page] = button
        self.prepare_embedded_page(page, title)

    def prepare_embedded_page(self, page: QWidget, page_title: str):
        """
        统一页面主体边距，并隐藏页面内部旧标题。标题统一由 MainWindow 绘制。
        """
        layout = page.layout()
        if layout is not None:
            layout.setContentsMargins(18, 10, 18, 18)
            layout.setSpacing(10)

        for label in page.findChildren(QLabel):
            text = (label.text() or "").strip()
            if text == page_title:
                label.hide()

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
        """
        应用主窗口局部样式。

        颜色不再写死，统一由 app/theme.py 控制。
        后续做主题切换时，只需要让这里传入不同 theme_key。
        """
        self.setStyleSheet(get_main_window_stylesheet())

    # ------------------------------------------------------------------
    # 配置刷新
    # ------------------------------------------------------------------
    def on_config_saved(self):
        # 保存设置后立即重新读取 [AppInfo] 并刷新窗口标题 / LOGO。
        self.config_manager.load()
        self.apply_app_branding()

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
        self.set_header_icon(title)
        self.refresh_header_context()

    def refresh_header_context(self):
        page = self.stack.currentWidget()
        title = self._page_title_map.get(page, "")

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
