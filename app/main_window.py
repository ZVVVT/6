import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QIcon
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
from app.ui_style import get_app_stylesheet
from core.config_manager import ConfigManager
from core.database import Database


class MainWindow(QMainWindow):
    """
    主窗口统一页面骨架。

    这版重点修复：
    1. 窗口标题不再硬编码。
    2. 启动时从 config.ini 的 [AppInfo] app_name 读取软件名称。
    3. 启动时从 config.ini 的 [AppInfo] logo_path 读取窗口 LOGO。
    4. 系统设置保存后立即重新应用软件名称和 LOGO，无需重启。
    5. config.ini、assets、data 路径统一按项目根目录解析，避免当前工作目录不同导致读写错文件。
    """

    def __init__(self):
        super().__init__()

        # 项目根目录：F:\sperm_protein_analyzer
        self.project_root = Path(__file__).resolve().parents[1]

        # 统一使用项目根目录下的 config.ini，避免从不同目录启动时读错配置。
        self.config_manager = ConfigManager(str(self.project_root / "config.ini"))
        self.config_manager.ensure_default_config()

        # 先应用界面字体与品牌信息，后续保存设置时也会再次调用。
        self.apply_app_font()
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
        path = Path(str(path_value or "").strip())
        if path.is_absolute():
            return path
        return self.project_root / path

    def load_configured_font_family(self):
        """
        读取并加载配置中的软件界面字体。

        返回：
        - 字体族名：font_path 指向有效字体文件且 Qt 加载成功
        - None：font_path 为空、文件不存在或加载失败，此时使用系统默认字体
        """
        font_path_text = str(self.config_manager.get_app_font_path() or "").strip()
        if not font_path_text:
            return None

        font_path = self.resolve_project_path(font_path_text)
        if not font_path.exists() or not font_path.is_file():
            return None

        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id == -1:
            return None

        families = QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            return None

        return families[0]

    def _system_font(self) -> QFont:
        """返回启动时记录的系统默认字体，用于从自定义字体恢复。"""
        app = QApplication.instance()
        if app is None:
            return QFont()

        family = app.property("system_font_family") or app.font().family()
        point_size = app.property("system_font_point_size") or app.font().pointSize()
        try:
            point_size = int(point_size)
        except Exception:
            point_size = 10

        font = QFont(str(family))
        if point_size > 0:
            font.setPointSize(point_size)
        return font

    def _normalize_stylesheet_font(self, stylesheet: str, font_family):
        """
        自定义字体有效时，替换 QSS 中的 Microsoft YaHei；
        没有自定义字体时，删除这些字体声明，让 Qt 使用系统默认字体。
        """
        if font_family:
            safe_family = str(font_family).replace('"', "")
            stylesheet = stylesheet.replace('font-family: "Microsoft YaHei";', f'font-family: "{safe_family}";')
            stylesheet = stylesheet.replace("font-family: Microsoft YaHei;", f'font-family: "{safe_family}";')
            return stylesheet

        return re.sub(
            r'\s*font-family\s*:\s*(?:"Microsoft YaHei"|Microsoft YaHei)\s*;\s*',
            '\n',
            stylesheet,
        )

    def apply_app_font(self):
        """应用配置中的界面字体，并刷新全局 QSS 与主窗口内部样式。"""
        try:
            self.config_manager.load()
        except Exception:
            pass

        app = QApplication.instance()
        if app is None:
            return

        custom_family = self.load_configured_font_family()
        if custom_family:
            font_size = self.config_manager.get_app_font_size()
            app.setFont(QFont(custom_family, font_size))
            app.setProperty("app_font_custom", True)
            app.setProperty("app_font_family", custom_family)
            app.setProperty("app_font_size", font_size)
        else:
            system_font = self._system_font()
            app.setFont(system_font)
            app.setProperty("app_font_custom", False)
            app.setProperty("app_font_family", system_font.family())
            app.setProperty("app_font_size", system_font.pointSize())

        # 重新应用全局 QSS。无自定义字体时，去掉写死字体，使用系统默认字体。
        try:
            stylesheet = get_app_stylesheet()
            app.setStyleSheet(self._normalize_stylesheet_font(stylesheet, custom_family))
        except Exception:
            pass

        # 主窗口自己的标题栏 / 左侧菜单样式也要重新应用。
        try:
            self.apply_unified_style()
        except Exception:
            pass

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

        # 上方：日常业务流程入口
        business_buttons = [
            self.btn_cases,
            self.btn_detail,
            self.btn_analysis,
            self.btn_reports,
        ]

        for btn in business_buttons:
            btn.setObjectName("SideButton")
            btn.setCheckable(True)
            side_layout.addWidget(btn)

        # 中间留白，把低频的“系统设置”固定到底部
        side_layout.addStretch(1)

        # 底部：系统设置入口
        self.btn_settings.setObjectName("SideButton")
        self.btn_settings.setCheckable(True)
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

        # 防止某些页面初始化过程中间接覆盖标题，最后再应用一次。
        self.apply_app_branding()

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
        app = QApplication.instance()
        font_family = "Microsoft YaHei"
        if app is not None:
            font_family = app.font().family() or font_family
        font_family = font_family.replace('"', "")

        stylesheet = """
            QFrame#SideMenu {
                background-color: #f2f6fb;
                border-right: 1px solid #d9e2ef;
            }

            QLabel#SideTitle {
                color: #1f2d3d;
                font-family: "__APP_FONT_FAMILY__";
                font-size: 16px;
                font-weight: 700;
                padding: 0 0 10px 18px;
            }

            QPushButton#SideButton {
                min-height: 40px;
                border: none;
                border-left: 4px solid transparent;
                border-radius: 0px;
                background-color: transparent;
                color: #26384d;
                font-family: "__APP_FONT_FAMILY__";
                font-size: 14px;
                font-weight: 500;
                text-align: left;
                padding-left: 14px;
                padding-right: 8px;
            }

            QPushButton#SideButton:hover {
                background-color: #eaf1f9;
                color: #1f4e79;
            }

            QPushButton#SideButton:checked {
                background-color: #dcecff;
                color: #0f4c81;
                font-weight: 700;
                border-left: 4px solid #2f80ed;
            }

            QPushButton#SideButton:checked:hover {
                background-color: #d6e8ff;
                color: #0f4c81;
                border-left: 4px solid #2f80ed;
            }

            QPushButton#SideButton:disabled {
                background-color: transparent;
                color: #9aa8b5;
                border-left: 4px solid transparent;
            }

            QFrame#ContentFrame {
                background-color: #f5f7fb;
            }

            QFrame#UnifiedPageHeader {
                background-color: #f5f7fb;
                border-bottom: 1px solid #d9e2ef;
            }

            QLabel#UnifiedPageTitle {
                color: #1f4e79;
                font-family: "__APP_FONT_FAMILY__";
                font-size: 22px;
                font-weight: 700;
            }

            QLabel#UnifiedPageContext {
                color: #4d5b6a;
                font-family: "__APP_FONT_FAMILY__";
                font-size: 13px;
            }
            """
        self.setStyleSheet(stylesheet.replace("__APP_FONT_FAMILY__", font_family))

    # ------------------------------------------------------------------
    # 配置刷新
    # ------------------------------------------------------------------
    def on_config_saved(self):
        # 保存设置后立即重新读取 [AppInfo]，刷新字体、窗口标题和 LOGO。
        self.config_manager.load()
        self.apply_app_font()
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
