from pathlib import Path

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
    QMessageBox,
    QStackedWidget,
    QFrame,
)

from app.ui_style import get_main_window_stylesheet
from app.analysis_window import AnalysisWindow
from app.case_detail_window import CaseDetailWindow
from app.case_manager_window import CaseManagerWindow
from app.report_window import ReportWindow
from app.settings_window import SettingsWindow
from core.config_manager import ConfigManager, get_application_root
from core.database import Database


class MainWindow(QMainWindow):
    """主窗口统一页面骨架。

    本版重点：
    1. 左侧导航使用普通图标 + 选中白色图标两套 SVG。
    2. 普通图标：assets/icons/*.svg
    3. 选中图标：assets/icons/*_s.svg
    4. 不再使用复杂的图标染色补丁，便于维护。
    """

    PAGE_ICONS = {
        "病例管理": "case_manager.svg",
        "病例详情": "case_detail.svg",
        "蛋白分析": "protein_analysis.svg",
        "报告管理": "report.svg",
        "系统设置": "settings.svg",
    }

    PAGE_SELECTED_ICONS = {
        "病例管理": "case_manager_s.svg",
        "病例详情": "case_detail_s.svg",
        "蛋白分析": "protein_analysis_s.svg",
        "报告管理": "report_s.svg",
        "系统设置": "settings_s.svg",
    }

    def __init__(self):
        super().__init__()

        # 项目根目录：F:\sperm_protein_analyzer
        self.project_root = get_application_root()

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
        self._analysis_navigation_locked = False
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
        """应用软件名称和窗口 LOGO。

        来源：config.ini
        [AppInfo]
        app_name = xxx
        logo_path = assets\\logo.png
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

        # 图标绑定。普通状态使用深色图标，选中状态使用 *_s.svg 白色图标。
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
        self.page_analysis.analysis_activity_changed.connect(
            self.on_analysis_activity_changed
        )

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

    def side_icon_file_for_button(self, button: QPushButton, page_title: str) -> str:
        """根据按钮选中状态返回普通图标或白色选中图标。"""
        if button.isChecked():
            return self.PAGE_SELECTED_ICONS.get(page_title) or self.PAGE_ICONS.get(page_title, "")
        return self.PAGE_ICONS.get(page_title, "")

    def apply_side_button_icon(self, button: QPushButton, page_title: str):
        """给左侧按钮设置图标。

        说明：
        - 未选中：使用普通深色图标，例如 case_manager.svg。
        - 选中：使用白色图标，例如 case_manager_s.svg。
        - 如果白色图标不存在，自动回退到普通图标，避免报错。
        """
        icon_file = self.side_icon_file_for_button(button, page_title)
        if not icon_file:
            return

        icon = self.load_icon(icon_file)
        if icon.isNull() and icon_file.endswith("_s.svg"):
            icon = self.load_icon(self.PAGE_ICONS.get(page_title, ""))

        if not icon.isNull():
            button.setIcon(icon)
            button.setIconSize(QSize(18, 18))

    def refresh_side_button_icons(self):
        """根据当前选中状态刷新左侧导航图标。"""
        for button in getattr(self, "menu_buttons", []):
            page_title = button.text().strip()
            self.apply_side_button_icon(button, page_title)

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
        """统一页面主体边距，并隐藏页面内部旧标题。标题统一由 MainWindow 绘制。"""
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
        """应用主窗口局部样式。颜色统一由 app/theme.py 控制。"""
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
        self.refresh_side_button_icons()

    # ------------------------------------------------------------------
    # 页面切换
    # ------------------------------------------------------------------
    def on_analysis_activity_changed(
        self,
        running: bool,
    ):
        """Lock case/config navigation while an analysis owns page context."""
        self._analysis_navigation_locked = bool(
            running
        )

        self.set_case_related_buttons_enabled(
            self.current_case is not None
        )

        if running:
            self.statusBar().showMessage(
                "分析进行中：已锁定病例和页面切换，避免结果写入错误病例。"
            )
        else:
            self.statusBar().showMessage(
                "分析流程已结束。"
            )

    def set_case_related_buttons_enabled(self, enabled: bool):
        has_case = bool(enabled)
        navigation_allowed = (
            has_case
            and not self._analysis_navigation_locked
        )

        # 分析期间锁定其他页面，避免切换病例或把结果写入错误病例。
        self.btn_detail.setEnabled(navigation_allowed)
        self.btn_reports.setEnabled(navigation_allowed)
        self.btn_cases.setEnabled(
            not self._analysis_navigation_locked
        )
        self.btn_settings.setEnabled(
            not self._analysis_navigation_locked
        )

        # 当前正在显示的“蛋白分析”按钮始终保持启用，
        # 避免 Qt 套用 disabled 样式后图标消失、文字居中和按钮变形。
        # 页面切换安全仍由 switch_page() 中的锁定判断负责。
        self.btn_analysis.setEnabled(has_case)

        if hasattr(self, "menu_buttons"):
            self.refresh_side_button_icons()

    def switch_page(self, page, active_button):
        if (
            self._analysis_navigation_locked
            and hasattr(self, "page_analysis")
            and page is not self.page_analysis
        ):
            self.statusBar().showMessage(
                "分析进行中，请先完成或取消当前分析。"
            )
            return

        self.stack.setCurrentWidget(page)
        for btn in self.menu_buttons:
            btn.setChecked(btn == active_button)
        self.refresh_side_button_icons()
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

        if title in ("病例详情", "蛋白分析", "报告管理"):
            self.header_context_label.setText(
                f"当前病例：{case_no}    姓名：{patient_name}    样本号：{sample_no}"
            )
        else:
            self.header_context_label.setText("")

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

        if self._analysis_navigation_locked and self.current_case:
            current_id = self.current_case.get("id")
            incoming_id = case_data.get("id")
            current_no = str(
                self.current_case.get("case_no", "") or ""
            ).strip()
            incoming_no = str(
                case_data.get("case_no", "") or ""
            ).strip()

            if (
                (current_id and incoming_id and current_id != incoming_id)
                or (current_no and incoming_no and current_no != incoming_no)
            ):
                self.statusBar().showMessage(
                    "分析进行中，暂时不能切换病例。"
                )
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
        self.refresh_side_button_icons()

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
    def closeEvent(self, event):
        analysis_page = getattr(
            self,
            "page_analysis",
            None,
        )

        if (
            analysis_page is not None
            and hasattr(
                analysis_page,
                "is_analysis_active",
            )
            and analysis_page.is_analysis_active()
        ):
            message = (
                "分析正在运行或等待人工校准，暂时不能退出软件。"
            )
            self.statusBar().showMessage(message)
            QMessageBox.information(
                self,
                "分析进行中",
                message,
            )
            event.ignore()
            return

        super().closeEvent(event)

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
