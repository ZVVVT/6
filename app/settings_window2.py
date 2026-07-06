import shutil
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
    QDoubleSpinBox,
    QScrollArea,
)

from core.config_manager import ConfigManager
from core.qc_beads_service import QCBeadsService, QCBeadsWorker
from core.pipeline_parameter_manager import PipelineParameterManager


class SettingsWindow(QWidget):
    config_saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project_root = Path(__file__).resolve().parents[1]
        self.config = ConfigManager(str(self.project_root / "config.ini"))
        self.config.ensure_default_config()
        self.pipeline_param_manager = PipelineParameterManager(self.project_root)
        self.pipeline_param_manager.ensure_default_params()
        self.init_ui()
        self.load_config()

    def resolve_project_path(self, path_text: str) -> Path:
        """把配置中的相对路径解析为项目根目录下的绝对路径。

        空字符串和 "." 不应被解析成项目根目录。
        """
        text = str(path_text or "").strip()
        if not text or text == ".":
            return Path("")
        path = Path(text)
        if path.is_absolute():
            return path
        return self.project_root / path

    def to_project_relative_text(self, path: Path) -> str:
        """尽量把项目内路径保存为相对路径，方便打包和迁移。"""
        try:
            return str(path.resolve().relative_to(self.project_root.resolve()))
        except Exception:
            return str(path)

    def init_ui(self):
        self.setObjectName("SettingsWindow")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(10)

        title_label = QLabel("系统设置")
        title_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        main_layout.addWidget(title_label)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs, 1)

        self.init_app_info_tab()
        self.init_runtime_tab()
        self.init_qc_tab()
        self.init_pipeline_params_tab()
        self.init_workspace_tab()
        self.init_image_rule_tab()
        self.init_protein_tab()

        btn_layout = QHBoxLayout()
        self.btn_reload = QPushButton("重新加载")
        self.btn_test = QPushButton("检查路径")
        self.btn_save = QPushButton("保存设置")

        btn_layout.addWidget(self.btn_reload)
        btn_layout.addWidget(self.btn_test)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_save)
        main_layout.addLayout(btn_layout)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumHeight(130)
        self.log_edit.setPlaceholderText("检查结果")
        main_layout.addWidget(self.log_edit)

        self.btn_reload.clicked.connect(self.load_config)
        self.btn_test.clicked.connect(self.check_paths)
        self.btn_save.clicked.connect(self.save_config)

        self.apply_settings_style()

    # ------------------------------------------------------------------
    # Tab 初始化
    # ------------------------------------------------------------------
    def init_app_info_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        group = QGroupBox("软件名称与 LOGO")
        form = QFormLayout(group)

        self.app_name_edit = QLineEdit()
        self.app_logo_path_edit = QLineEdit()
        self.app_logo_path_edit.setReadOnly(True)
        self.app_font_path_edit = QLineEdit()
        self.app_font_path_edit.setReadOnly(True)
        self.logo_preview_label = QLabel()
        self.logo_preview_label.setFixedSize(80, 80)
        self.logo_preview_label.setAlignment(Qt.AlignCenter)
        self.logo_preview_label.setStyleSheet(
            "border: 1px solid #d9e2ef; background: white; color: #999999;"
        )
        self.logo_preview_label.setText("无 LOGO")

        logo_row = QWidget()
        logo_layout = QHBoxLayout(logo_row)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.setSpacing(8)
        self.btn_select_app_logo = QPushButton("选择 LOGO")
        self.btn_reset_app_info = QPushButton("恢复默认")
        logo_layout.addWidget(self.app_logo_path_edit, 1)
        logo_layout.addWidget(self.btn_select_app_logo)
        logo_layout.addWidget(self.btn_reset_app_info)

        font_row = QWidget()
        font_layout = QHBoxLayout(font_row)
        font_layout.setContentsMargins(0, 0, 0, 0)
        font_layout.setSpacing(8)
        self.btn_select_app_font = QPushButton("选择字体")
        self.btn_reset_app_font = QPushButton("默认字体")
        font_layout.addWidget(self.app_font_path_edit, 1)
        font_layout.addWidget(self.btn_select_app_font)
        font_layout.addWidget(self.btn_reset_app_font)

        form.addRow("软件名称：", self.app_name_edit)
        form.addRow("LOGO 预览：", self.logo_preview_label)
        form.addRow("LOGO 图片：", logo_row)
        form.addRow("界面字体：", font_row)

        hint = QLabel(
            "说明：这里控制窗口标题、任务栏图标和软件界面字体。"
            "字体路径为空或字体文件找不到时，会使用系统默认字体；报告字体暂不受这里影响。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666666;")

        layout.addWidget(group)
        layout.addWidget(hint)
        layout.addStretch()

        self.btn_select_app_logo.clicked.connect(self.select_app_logo_path)
        self.btn_select_app_font.clicked.connect(self.select_app_font_path)
        self.btn_reset_app_font.clicked.connect(self.reset_app_font)
        self.btn_reset_app_info.clicked.connect(self.reset_app_info)

        self.tabs.addTab(tab, "软件信息")

    def init_runtime_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        group = QGroupBox("MvImageID 源码环境")
        form = QFormLayout(group)

        self.source_project_dir_edit = QLineEdit()
        self.python_exe_edit = QLineEdit()
        self.module_name_edit = QLineEdit()
        self.head_pipeline_edit = QLineEdit()
        self.tail_pipeline_edit = QLineEdit()
        self.plugins_directory_edit = QLineEdit()

        form.addRow("源码目录：", self._with_button(self.source_project_dir_edit, "选择", self.select_source_project_dir))
        form.addRow("Python解释器：", self._with_button(self.python_exe_edit, "选择", self.select_python_exe))
        form.addRow("模块名称：", self.module_name_edit)
        form.addRow("头部 Pipeline：", self._with_button(self.head_pipeline_edit, "选择", self.select_head_pipeline))
        form.addRow("尾部 Pipeline：", self._with_button(self.tail_pipeline_edit, "选择", self.select_tail_pipeline))
        form.addRow("插件目录：", self._with_button(self.plugins_directory_edit, "选择", self.select_plugins_directory))

        hint = QLabel(
            "说明："
            "每次分析日志会自动写入对应蛋白输出目录的 run_mvimageid.log。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666666;")

        layout.addWidget(group)
        layout.addWidget(hint)
        layout.addStretch()
        self.tabs.addTab(tab, "运行环境")

    def init_qc_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        group = QGroupBox("质控微球荧光强度测试")
        form = QFormLayout(group)

        self.qc_source_folder_edit = QLineEdit()
        self.qc_output_dir_edit = QLineEdit()
        self.qc_pipeline_edit = QLineEdit()

        source_row = self._with_button(self.qc_source_folder_edit, "选择", self.select_qc_source_folder)
        output_row_widget = QWidget()
        output_row_layout = QHBoxLayout(output_row_widget)
        output_row_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_select_qc_output = QPushButton("选择")
        self.btn_reset_qc_output = QPushButton("默认")
        self.btn_select_qc_output.setFixedWidth(60)
        self.btn_reset_qc_output.setFixedWidth(60)
        self.btn_select_qc_output.clicked.connect(self.select_qc_output_dir)
        self.btn_reset_qc_output.clicked.connect(self.reset_qc_output_dir)
        output_row_layout.addWidget(self.qc_output_dir_edit, 1)
        output_row_layout.addWidget(self.btn_select_qc_output)
        output_row_layout.addWidget(self.btn_reset_qc_output)

        form.addRow("微球图片文件夹：", source_row)
        form.addRow("本次输出目录：", output_row_widget)
        form.addRow("质控 Pipeline：", self._with_button(self.qc_pipeline_edit, "选择", self.select_qc_pipeline))

        button_layout = QHBoxLayout()
        self.btn_run_qc = QPushButton("运行质控测试")
        self.btn_open_qc_output = QPushButton("打开输出目录")
        button_layout.addWidget(self.btn_run_qc)
        button_layout.addWidget(self.btn_open_qc_output)
        button_layout.addStretch()

        hint = QLabel(
            "说明：质控微球测试不进入病例数据库，也不进入 PDF 报告。"
            "默认输出到 workspace\\qc\\YYYYMMDD_01，目录内包含 input 和 output 两个文件夹。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666666;")

        layout.addWidget(group)
        layout.addLayout(button_layout)
        layout.addWidget(hint)
        layout.addStretch()

        self.btn_run_qc.clicked.connect(self.run_qc_beads_test)
        self.btn_open_qc_output.clicked.connect(self.open_qc_output_dir)

        self.tabs.addTab(tab, "质控微球测试")

    def init_pipeline_params_tab(self):
        """管道参数设置页。

        参数保存到 pipeline_params.ini。
        用户低频修改参数后，点击“生成管道”，再覆盖 pipelines 目录下实际运行的 .cppipe。
        蛋白分析、批量分析和质控测试运行时仍直接读取生成后的 .cppipe。
        """
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)

        hint = QLabel(
            "说明：管道参数保存在 pipeline_params.ini 中。"
            "修改参数后请点击“生成管道”，软件会根据 pipelines\\templates 中的母版生成实际运行的 pipelines\\pipeline_*.cppipe。"
            "运行分析时不会动态改管道，只会直接读取已生成的 .cppipe。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666666;")
        layout.addWidget(hint)

        self.pipeline_param_widgets = {}

        self.pipeline_param_tabs = QTabWidget()
        self.pipeline_param_tabs.addTab(self.create_head_pipeline_param_page(), "头部蛋白")
        self.pipeline_param_tabs.addTab(self.create_tail_pipeline_param_page(), "尾部蛋白")
        self.pipeline_param_tabs.addTab(self.create_qc_pipeline_param_page(), "质控微球")
        layout.addWidget(self.pipeline_param_tabs, 1)

        button_layout = QHBoxLayout()
        self.btn_save_pipeline_params = QPushButton("保存参数")
        self.btn_generate_pipelines = QPushButton("生成管道")
        self.btn_restore_pipeline_backup = QPushButton("恢复最近备份")
        self.btn_reset_pipeline_params = QPushButton("恢复默认参数")
        self.btn_open_pipeline_dir = QPushButton("打开管道目录")
        self.btn_open_pipeline_param_file = QPushButton("打开参数文件")

        button_layout.addWidget(self.btn_save_pipeline_params)
        button_layout.addWidget(self.btn_generate_pipelines)
        button_layout.addWidget(self.btn_restore_pipeline_backup)
        button_layout.addWidget(self.btn_reset_pipeline_params)
        button_layout.addWidget(self.btn_open_pipeline_dir)
        button_layout.addWidget(self.btn_open_pipeline_param_file)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        self.btn_save_pipeline_params.clicked.connect(self.save_pipeline_params)
        self.btn_generate_pipelines.clicked.connect(self.generate_pipeline_files)
        self.btn_restore_pipeline_backup.clicked.connect(self.restore_latest_pipeline_backup)
        self.btn_reset_pipeline_params.clicked.connect(self.reset_pipeline_params)
        self.btn_open_pipeline_dir.clicked.connect(self.open_pipeline_dir)
        self.btn_open_pipeline_param_file.clicked.connect(self.open_pipeline_param_file)

        self.tabs.addTab(tab, "管道参数")

    def create_head_pipeline_param_page(self):
        group = QGroupBox("头部蛋白管道参数")
        form = QFormLayout(group)

        self.pipeline_param_widgets["Head"] = {
            "red_diameter": self._make_double_spin(1, 500, 1, 0),
            "red_flow_threshold": self._make_double_spin(-10, 10, 0.1, 2),
            "red_cellprob_threshold": self._make_double_spin(-10, 10, 0.1, 2),
            "red_min_size": self._make_double_spin(0, 100000, 1, 0),
            "red_formfactor_min": self._make_double_spin(0, 1, 0.01, 2),
            "green_expand_pixels": self._make_double_spin(0, 500, 1, 0),
            "green_intensity_min": self._make_double_spin(0, 100000000, 1, 2),
        }

        form.addRow("红色识别直径：", self.pipeline_param_widgets["Head"]["red_diameter"])
        form.addRow("红色流阈值：", self.pipeline_param_widgets["Head"]["red_flow_threshold"])
        form.addRow("红色细胞概率阈值：", self.pipeline_param_widgets["Head"]["red_cellprob_threshold"])
        form.addRow("红色最小尺寸：", self.pipeline_param_widgets["Head"]["red_min_size"])
        form.addRow("红色圆度下限：", self.pipeline_param_widgets["Head"]["red_formfactor_min"])
        form.addRow("绿色匹配扩张像素：", self.pipeline_param_widgets["Head"]["green_expand_pixels"])
        form.addRow("共定位绿色强度阈值：", self.pipeline_param_widgets["Head"]["green_intensity_min"])

        return self._wrap_pipeline_param_group(group)

    def create_tail_pipeline_param_page(self):
        group = QGroupBox("尾部蛋白管道参数")
        form = QFormLayout(group)

        self.pipeline_param_widgets["Tail"] = {
            "green_diameter": self._make_double_spin(1, 500, 1, 0),
            "green_flow_threshold": self._make_double_spin(-10, 10, 0.1, 2),
            "green_distance_threshold": self._make_double_spin(-10, 10, 0.1, 2),
            "green_min_size": self._make_double_spin(0, 100000, 1, 0),
            "green_eccentricity_min": self._make_double_spin(0, 1, 0.01, 2),
            "red_diameter": self._make_double_spin(1, 500, 1, 0),
            "red_flow_threshold": self._make_double_spin(-10, 10, 0.1, 2),
            "red_cellprob_threshold": self._make_double_spin(-10, 10, 0.1, 2),
            "red_min_size": self._make_double_spin(0, 100000, 1, 0),
            "red_formfactor_min": self._make_double_spin(0, 1, 0.01, 2),
            "red_search_radius": self._make_double_spin(0, 1000, 1, 0),
            "colocalized_child_count_min": self._make_double_spin(0, 1000, 1, 0),
        }

        form.addRow("绿色尾部识别直径：", self.pipeline_param_widgets["Tail"]["green_diameter"])
        form.addRow("绿色尾部流阈值：", self.pipeline_param_widgets["Tail"]["green_flow_threshold"])
        form.addRow("绿色尾部距离场阈值：", self.pipeline_param_widgets["Tail"]["green_distance_threshold"])
        form.addRow("绿色尾部最小尺寸：", self.pipeline_param_widgets["Tail"]["green_min_size"])
        form.addRow("绿色尾部细长度下限：", self.pipeline_param_widgets["Tail"]["green_eccentricity_min"])
        form.addRow("红色头部识别直径：", self.pipeline_param_widgets["Tail"]["red_diameter"])
        form.addRow("红色头部流阈值：", self.pipeline_param_widgets["Tail"]["red_flow_threshold"])
        form.addRow("红色头部细胞概率阈值：", self.pipeline_param_widgets["Tail"]["red_cellprob_threshold"])
        form.addRow("红色头部最小尺寸：", self.pipeline_param_widgets["Tail"]["red_min_size"])
        form.addRow("红色头部圆度下限：", self.pipeline_param_widgets["Tail"]["red_formfactor_min"])
        form.addRow("红色头部搜索半径：", self.pipeline_param_widgets["Tail"]["red_search_radius"])
        form.addRow("共定位最小绿色对象数：", self.pipeline_param_widgets["Tail"]["colocalized_child_count_min"])

        return self._wrap_pipeline_param_group(group)

    def create_qc_pipeline_param_page(self):
        group = QGroupBox("质控微球管道参数")
        form = QFormLayout(group)

        self.pipeline_param_widgets["QC"] = {
            "bead_diameter": self._make_double_spin(1, 500, 1, 0),
            "bead_flow_threshold": self._make_double_spin(-10, 10, 0.1, 2),
            "bead_cellprob_threshold": self._make_double_spin(-10, 10, 0.1, 2),
            "bead_min_size": self._make_double_spin(0, 100000, 1, 0),
            "bead_formfactor_min": self._make_double_spin(0, 1, 0.01, 2),
        }

        form.addRow("微球识别直径：", self.pipeline_param_widgets["QC"]["bead_diameter"])
        form.addRow("微球流阈值：", self.pipeline_param_widgets["QC"]["bead_flow_threshold"])
        form.addRow("微球细胞概率阈值：", self.pipeline_param_widgets["QC"]["bead_cellprob_threshold"])
        form.addRow("微球最小尺寸：", self.pipeline_param_widgets["QC"]["bead_min_size"])
        form.addRow("微球圆度下限：", self.pipeline_param_widgets["QC"]["bead_formfactor_min"])

        return self._wrap_pipeline_param_group(group)

    def _wrap_pipeline_param_group(self, group: QGroupBox):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.addWidget(group)
        content_layout.addStretch()

        scroll.setWidget(content)
        page_layout.addWidget(scroll, 1)
        return page

    @staticmethod
    def _make_double_spin(min_value, max_value, step, decimals):
        spin = QDoubleSpinBox()
        spin.setRange(float(min_value), float(max_value))
        spin.setSingleStep(float(step))
        spin.setDecimals(int(decimals))
        spin.setMinimumHeight(30)
        spin.setButtonSymbols(QDoubleSpinBox.UpDownArrows)
        return spin

    def load_pipeline_params(self):
        try:
            self.pipeline_param_manager.load()
            self.pipeline_param_manager.ensure_default_params()
        except Exception as e:
            self.append_log(f"读取管道参数失败：{e}")
            return

        for section, widgets in getattr(self, "pipeline_param_widgets", {}).items():
            params = self.pipeline_param_manager.get_params(section)
            for key, widget in widgets.items():
                try:
                    widget.setValue(float(params.get(key, 0)))
                except Exception:
                    widget.setValue(0)

    def collect_pipeline_params_from_ui(self):
        data = {}
        for section, widgets in getattr(self, "pipeline_param_widgets", {}).items():
            data[section] = {}
            for key, widget in widgets.items():
                data[section][key] = widget.value()
        return data

    def save_pipeline_params(self):
        try:
            data = self.collect_pipeline_params_from_ui()
            for section, values in data.items():
                self.pipeline_param_manager.set_params(section, values)
            self.append_log(f"管道参数已保存：{self.pipeline_param_manager.get_param_file()}")
            QMessageBox.information(self, "成功", "管道参数已保存到 pipeline_params.ini。")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存管道参数失败：\n{e}")

    def reset_pipeline_params(self):
        reply = QMessageBox.question(
            self,
            "确认恢复默认",
            "确定要恢复默认管道参数吗？\n\n这只会重置 pipeline_params.ini，不会立即覆盖现有 .cppipe。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            self.pipeline_param_manager.reset_defaults()
            self.load_pipeline_params()
            self.append_log("管道参数已恢复默认。")
            QMessageBox.information(self, "成功", "管道参数已恢复默认。")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"恢复默认参数失败：\n{e}")

    def generate_pipeline_files(self):
        reply = QMessageBox.question(
            self,
            "确认生成管道",
            "将根据当前参数生成并覆盖：\n"
            "pipelines\\pipeline_head.cppipe\n"
            "pipelines\\pipeline_tail.cppipe\n"
            "pipelines\\pipeline_qc.cppipe\n\n"
            "模板位于 pipelines\\templates。首次生成时会自动从当前管道创建模板。\n\n"
            "是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            data = self.collect_pipeline_params_from_ui()
            for section, values in data.items():
                self.pipeline_param_manager.set_params(section, values)

            messages = self.pipeline_param_manager.generate_all_pipelines()
            for message in messages:
                self.append_log(message)

            QMessageBox.information(
                self,
                "生成完成",
                "管道已根据当前参数生成。\n\n"
                "后续蛋白分析、批量分析和质控测试会直接读取 pipelines 目录下的 .cppipe。",
            )
        except Exception as e:
            QMessageBox.critical(self, "错误", f"生成管道失败：\n{e}")


    def restore_latest_pipeline_backup(self):
        latest_backup = self.pipeline_param_manager.get_latest_backup_dir()

        if not latest_backup:
            QMessageBox.information(
                self,
                "提示",
                "当前没有可恢复的管道备份。\n\n"
                "点击“生成管道”后，系统会自动在 pipelines\\backups 中备份旧管道。",
            )
            return

        reply = QMessageBox.question(
            self,
            "确认恢复最近备份",
            "将恢复最近一次管道备份，并覆盖当前实际运行管道：\n\n"
            f"{latest_backup}\n\n"
            "恢复前系统会先自动备份当前管道，便于回退。\n\n"
            "是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            messages = self.pipeline_param_manager.restore_latest_backup()
            for message in messages:
                self.append_log(message)

            QMessageBox.information(
                self,
                "恢复完成",
                "已恢复最近一次管道备份。\n\n"
                "后续蛋白分析、批量分析和质控测试会直接读取恢复后的 pipelines 目录下 .cppipe。",
            )
        except Exception as e:
            QMessageBox.critical(self, "错误", f"恢复管道备份失败：\n{e}")


    def open_pipeline_dir(self):
        path = self.pipeline_param_manager.get_pipelines_dir()
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def open_pipeline_param_file(self):
        path = self.pipeline_param_manager.get_param_file()
        if not path.exists():
            self.pipeline_param_manager.ensure_default_params()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def init_workspace_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        group = QGroupBox("工作目录与报告设置")
        form = QFormLayout(group)

        self.workspace_root_edit = QLineEdit()
        self.database_edit = QLineEdit()
        self.report_dir_edit = QLineEdit()
        self.logo_path_edit = QLineEdit()

        form.addRow("病例工作目录：", self._with_button(self.workspace_root_edit, "选择", self.select_workspace_root))
        form.addRow("数据库文件：", self._with_button(self.database_edit, "选择", self.select_database_file))
        form.addRow("报告目录：", self._with_button(self.report_dir_edit, "选择", self.select_report_dir))
        form.addRow("报告 LOGO 图片：", self._with_button(self.logo_path_edit, "选择", self.select_logo_path))

        layout.addWidget(group)
        layout.addStretch()
        self.tabs.addTab(tab, "工作目录")

    def init_image_rule_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        group = QGroupBox("图片命名规则")
        form = QFormLayout(group)

        self.r_suffix_edit = QLineEdit()
        self.g_suffix_edit = QLineEdit()
        self.dic_suffix_edit = QLineEdit()
        self.merge_suffix_edit = QLineEdit()
        self.image_ext_edit = QLineEdit()

        form.addRow("R 通道后缀：", self.r_suffix_edit)
        form.addRow("G 通道后缀：", self.g_suffix_edit)
        form.addRow("DIC 通道后缀：", self.dic_suffix_edit)
        form.addRow("Merge 通道后缀：", self.merge_suffix_edit)
        form.addRow("默认图片扩展名：", self.image_ext_edit)

        layout.addWidget(group)
        layout.addStretch()
        self.tabs.addTab(tab, "图片规则")

    def init_protein_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        tips = QLabel(
            "说明：内部编号用于工作目录和配置索引；显示名称用于界面和报告。"
            "自定义 Pipeline 为空时，会按表达部位自动使用头部、尾部 Pipeline。"
        )
        tips.setWordWrap(True)
        tips.setStyleSheet("color: #666666;")
        layout.addWidget(tips)

        self.protein_table = QTableWidget()
        self.protein_table.setColumnCount(7)
        self.protein_table.setHorizontalHeaderLabels(
            [
                "内部编号",
                "显示名称",
                "表达部位",
                "自定义 Pipeline",
                "荧光强度下限",
                "标定率下限(%)",
                "选择",
            ]
        )
        self.protein_table.setObjectName("ProteinConfigTable")
        self.protein_table.setAlternatingRowColors(True)
        self.protein_table.verticalHeader().setVisible(False)
        self.protein_table.verticalHeader().setDefaultSectionSize(38)
        self.protein_table.setShowGrid(True)
        self.protein_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.protein_table.setFocusPolicy(Qt.NoFocus)

        header = self.protein_table.horizontalHeader()
        header.setFixedHeight(36)
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        header.setSectionResizeMode(6, QHeaderView.Fixed)
        self.protein_table.setColumnWidth(0, 78)
        self.protein_table.setColumnWidth(1, 160)
        self.protein_table.setColumnWidth(2, 96)
        self.protein_table.setColumnWidth(4, 110)
        self.protein_table.setColumnWidth(5, 120)
        self.protein_table.setColumnWidth(6, 104)

        layout.addWidget(self.protein_table, 1)

        # 蛋白配置维护按钮暂时隐藏。
        # 保留控件和信号连接，后续如需恢复，只需把 setVisible(False) 改为 True。
        self.protein_action_bar = QWidget()
        button_layout = QHBoxLayout(self.protein_action_bar)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)

        self.btn_reset_protein_defaults = QPushButton("恢复默认蛋白配置")
        self.btn_add_protein_row = QPushButton("增加一行")
        self.btn_remove_protein_row = QPushButton("删除选中行")

        button_layout.addWidget(self.btn_reset_protein_defaults)
        button_layout.addWidget(self.btn_add_protein_row)
        button_layout.addWidget(self.btn_remove_protein_row)
        button_layout.addStretch()
        layout.addWidget(self.protein_action_bar)
        self.protein_action_bar.setVisible(False)

        self.btn_reset_protein_defaults.clicked.connect(self.reset_protein_defaults)
        self.btn_add_protein_row.clicked.connect(self.add_empty_protein_row)
        self.btn_remove_protein_row.clicked.connect(self.remove_selected_protein_row)

        self.tabs.addTab(tab, "蛋白配置")

    def _with_button(self, line_edit: QLineEdit, button_text: str, callback):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        button = QPushButton(button_text)
        button.setObjectName("SettingsSmallButton")
        button.setFixedSize(64, 32)
        button.clicked.connect(callback)

        layout.addWidget(line_edit, 1)
        layout.addWidget(button)
        return widget

    def make_cell_widget(self, inner_widget: QWidget, margin_left: int = 4, margin_right: int = 4) -> QWidget:
        wrapper = QWidget()
        wrapper.setObjectName("SettingsTableCellWidget")
        wrapper.setAttribute(Qt.WA_StyledBackground, True)
        wrapper.setStyleSheet("QWidget#SettingsTableCellWidget { background: transparent; border: none; }")
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(margin_left, 0, margin_right, 0)
        layout.setSpacing(0)
        layout.addWidget(inner_widget, 0, Qt.AlignCenter)
        return wrapper

    def style_part_combo(self, combo: QComboBox) -> None:
        combo.setObjectName("ProteinPartCombo")
        combo.setFixedSize(76, 28)
        combo.setFocusPolicy(Qt.NoFocus)
        combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        view = QListView(combo)
        view.setUniformItemSizes(True)
        combo.setView(view)
        arrow_icon = (self.project_root / "assets" / "icons" / "form_chevron_down.svg").as_posix()
        combo.setStyleSheet(f'''
            QComboBox#ProteinPartCombo {{
                background-color: #FFFFFF;
                border: 1px solid #DDE6F2;
                border-radius: 5px;
                padding: 0px 22px 0px 9px;
                color: #1F2D3D;
                min-height: 28px;
                max-height: 28px;
            }}
            QComboBox#ProteinPartCombo:hover {{
                background-color: #F8FBFF;
                border-color: #BCD7FF;
            }}
            QComboBox#ProteinPartCombo::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 22px;
                border: none;
                background: transparent;
            }}
            QComboBox#ProteinPartCombo::down-arrow {{
                image: url("{arrow_icon}");
                width: 10px;
                height: 10px;
            }}
        ''')
        view.setStyleSheet('''
            QListView {
                background-color: #FFFFFF;
                border: 1px solid #DDE6F2;
                color: #1F2D3D;
                outline: none;
                padding: 3px;
                selection-background-color: #DCEBFF;
                selection-color: #1F2D3D;
            }
            QListView::item {
                min-height: 28px;
                padding: 4px 8px;
                background-color: #FFFFFF;
                color: #1F2D3D;
            }
            QListView::item:hover {
                background-color: #F2F7FF;
                color: #1769E0;
            }
            QListView::item:selected {
                background-color: #DCEBFF;
                color: #1F2D3D;
            }
        ''')

    def get_part_combo_from_row(self, row: int) -> Optional[QComboBox]:
        cell = self.protein_table.cellWidget(row, 2)
        if isinstance(cell, QComboBox):
            return cell
        if isinstance(cell, QWidget):
            return cell.findChild(QComboBox)
        return None

    def get_button_from_row(self, row: int) -> Optional[QPushButton]:
        cell = self.protein_table.cellWidget(row, 6)
        if isinstance(cell, QPushButton):
            return cell
        if isinstance(cell, QWidget):
            return cell.findChild(QPushButton)
        return None

    def apply_settings_style(self):
        self.setStyleSheet('''
            QWidget#SettingsWindow {
                background-color: #F5F8FC;
                color: #1F2D3D;
            }

            QTabWidget::pane {
                border: 1px solid #DDE6F2;
                background-color: #FFFFFF;
            }

            QTabBar::tab {
                min-width: 82px;
                min-height: 30px;
                padding: 0px 12px;
                border: 1px solid #DDE6F2;
                background-color: #F8FAFD;
                color: #1F2D3D;
            }

            QTabBar::tab:selected {
                background-color: #FFFFFF;
                color: #1769E0;
                font-weight: 600;
                border-bottom-color: #FFFFFF;
            }

            QGroupBox {
                background-color: #FFFFFF;
                border: 1px solid #DDE6F2;
                border-radius: 8px;
                margin-top: 12px;
                padding: 14px 12px 12px 12px;
                color: #102A43;
                font-weight: 700;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                top: 0px;
                padding: 0px 6px;
                color: #102A43;
                background-color: #FFFFFF;
            }

            QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox {
                background-color: #FFFFFF;
                border: 1px solid #DDE6F2;
                border-radius: 5px;
                color: #1F2D3D;
                selection-background-color: #DCEBFF;
            }

            QLineEdit {
                min-height: 30px;
                padding: 0px 9px;
            }

            QTextEdit {
                padding: 6px 8px;
            }

            QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border-color: #1769E0;
            }

            QPushButton {
                min-height: 32px;
                padding: 0px 14px;
                border: 1px solid #DDE6F2;
                border-radius: 6px;
                background-color: #FFFFFF;
                color: #1F2D3D;
                font-weight: 500;
            }

            QPushButton:hover {
                background-color: #F2F7FF;
                border-color: #BCD7FF;
                color: #1769E0;
            }

            QPushButton:pressed {
                background-color: #EAF2FF;
                border-color: #1769E0;
            }

            QPushButton:disabled {
                background-color: #F8FAFD;
                border-color: #E8EEF6;
                color: #9AA6B2;
            }

            QTableWidget#ProteinConfigTable {
                background-color: #FFFFFF;
                alternate-background-color: #F8FAFD;
                gridline-color: #DDE6F2;
                border: 1px solid #DDE6F2;
                border-radius: 6px;
                color: #1F2D3D;
                outline: none;
                selection-background-color: #DCEBFF;
                selection-color: #1F2D3D;
            }

            QTableWidget#ProteinConfigTable::item {
                padding: 0px 6px;
            }

            QTableWidget#ProteinConfigTable::item:selected {
                background-color: #DCEBFF;
                color: #1F2D3D;
                outline: none;
            }

            QHeaderView::section {
                background-color: #EEF4FB;
                color: #1F2D3D;
                font-weight: 700;
                border: none;
                border-right: 1px solid #DDE6F2;
                border-bottom: 1px solid #DDE6F2;
                padding: 6px 6px;
            }
        ''')

    # ------------------------------------------------------------------
    # 加载 / 保存
    # ------------------------------------------------------------------
    def load_config(self):
        self.config.load()
        self.config.ensure_default_config()

        self.app_name_edit.setText(self.config.get_app_name())
        self.app_logo_path_edit.setText(str(self.config.get_app_logo_path()))
        # 字体路径为空表示使用系统默认字体。不能显示为 "."。
        self.app_font_path_edit.setText(str(self.config.get_app_font_path() or ""))
        self.update_logo_preview(self.app_logo_path_edit.text().strip())

        self.source_project_dir_edit.setText(self.config.get_mvimageid("source_project_dir", ""))
        self.python_exe_edit.setText(str(self.config.get_python_exe()))
        self.module_name_edit.setText(self.config.get_mvimageid("module_name", "MvImageID"))
        self.head_pipeline_edit.setText(self.config.get_mvimageid("head_pipeline", ""))
        self.tail_pipeline_edit.setText(self.config.get_mvimageid("tail_pipeline", ""))
        self.plugins_directory_edit.setText(self.config.get_mvimageid("plugins_directory", ""))

        self.qc_pipeline_edit.setText(self.config.get_mvimageid("qc_pipeline", r"pipelines\pipeline_qc.cppipe"))
        self.qc_source_folder_edit.setText("")
        try:
            qc_default_dir = QCBeadsService(self.config).get_next_run_dir()
            self.qc_output_dir_edit.setText(self.to_project_relative_text(qc_default_dir))
        except Exception:
            self.qc_output_dir_edit.setText(r"workspace\qc")

        self.workspace_root_edit.setText(self.config.get("Workspace", "root_dir", "workspace\\cases"))
        self.database_edit.setText(self.config.get("Workspace", "database", "data\\analysis.db"))
        self.report_dir_edit.setText(self.config.get("Workspace", "report_dir", "reports"))
        self.logo_path_edit.setText(self.config.get("Report", "logo_path", "assets\\logo.png"))

        self.r_suffix_edit.setText(self.config.get("ImageRule", "r_suffix", "_R"))
        self.g_suffix_edit.setText(self.config.get("ImageRule", "g_suffix", "_G"))
        self.dic_suffix_edit.setText(self.config.get("ImageRule", "dic_suffix", "_DIC"))
        self.merge_suffix_edit.setText(self.config.get("ImageRule", "merge_suffix", "_Merge"))
        self.image_ext_edit.setText(self.config.get("ImageRule", "image_ext", ".tif"))

        self.load_protein_table()
        self.load_pipeline_params()
        self.append_log("配置已重新加载。")

    def save_config(self):
        app_name = self.app_name_edit.text().strip()
        if not app_name:
            QMessageBox.warning(self, "提示", "软件名称不能为空。")
            return

        app_logo_path = self.prepare_app_logo_for_save(self.app_logo_path_edit.text().strip())
        app_font_path = self.prepare_app_font_for_save(self.app_font_path_edit.text().strip())
        self.config.set("AppInfo", "app_name", app_name)
        self.config.set("AppInfo", "logo_path", app_logo_path)
        self.config.set("AppInfo", "font_path", app_font_path)
        # 界面字号功能已取消，保留默认字号，避免旧配置继续影响界面。
        self.config.set("AppInfo", "font_size", "10")
        # 同步旧字段，避免其他旧代码仍读取 [Software] name。
        self.config.set("Software", "name", app_name)

        self.config.set("MvImageID", "run_mode", "source")
        self.config.set("MvImageID", "source_project_dir", self.source_project_dir_edit.text().strip())
        self.config.set("MvImageID", "python_exe", self.python_exe_edit.text().strip())
        self.config.set("MvImageID", "module_name", self.module_name_edit.text().strip())
        self.config.set("MvImageID", "head_pipeline", self.head_pipeline_edit.text().strip())
        self.config.set("MvImageID", "tail_pipeline", self.tail_pipeline_edit.text().strip())
        self.config.set("MvImageID", "qc_pipeline", self.qc_pipeline_edit.text().strip())
        self.config.set("MvImageID", "plugins_directory", self.plugins_directory_edit.text().strip())
        if not self.config.get("QC", "root_dir", "").strip():
            self.config.set("QC", "root_dir", r"workspace\qc")

        self.config.set("Workspace", "root_dir", self.workspace_root_edit.text().strip())
        self.config.set("Workspace", "database", self.database_edit.text().strip())
        self.config.set("Workspace", "report_dir", self.report_dir_edit.text().strip())
        self.config.set("Report", "logo_path", self.logo_path_edit.text().strip())

        self.config.set("ImageRule", "r_suffix", self.r_suffix_edit.text().strip())
        self.config.set("ImageRule", "g_suffix", self.g_suffix_edit.text().strip())
        self.config.set("ImageRule", "dic_suffix", self.dic_suffix_edit.text().strip())
        self.config.set("ImageRule", "merge_suffix", self.merge_suffix_edit.text().strip())
        self.config.set("ImageRule", "image_ext", self.image_ext_edit.text().strip())

        ok, message = self.save_protein_table_to_config()
        if not ok:
            QMessageBox.warning(self, "提示", message)
            return

        try:
            self.config.save()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存配置失败：\n{e}")
            return

        QMessageBox.information(
            self,
            "成功",
            "系统设置已保存。\n\n软件名称、LOGO、蛋白分析、报告管理等页面已自动刷新配置。",
        )
        self.append_log("系统设置已保存到 config.ini。")
        self.config_saved.emit()

    # ------------------------------------------------------------------
    # 软件信息
    # ------------------------------------------------------------------
    def select_app_logo_path(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择软件 LOGO 图片",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp *.ico);;所有文件 (*.*)",
        )
        if path:
            self.app_logo_path_edit.setText(path)
            self.update_logo_preview(path)

    def reset_app_info(self):
        reply = QMessageBox.question(
            self,
            "确认恢复",
            "确定要恢复默认软件名称、LOGO 和界面字体吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.app_name_edit.setText("人精子蛋白质量分析软件")
        self.app_logo_path_edit.setText(r"assets\logo.png")
        self.app_font_path_edit.setText("")
        self.update_logo_preview(self.app_logo_path_edit.text().strip())

    def prepare_app_logo_for_save(self, logo_path_text: str) -> str:
        if not logo_path_text:
            return r"assets\logo.png"

        src = Path(logo_path_text)
        if not src.is_absolute():
            src_abs = self.resolve_project_path(str(src))
        else:
            src_abs = src

        if not src_abs.exists() or not src_abs.is_file():
            # 不强制阻止保存，主窗口会自动回退为无图标显示。
            return logo_path_text

        assets_dir = self.project_root / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        suffix = src_abs.suffix.lower() or ".png"
        if suffix not in [".png", ".jpg", ".jpeg", ".bmp", ".ico"]:
            suffix = ".png"

        dst = assets_dir / f"custom_app_logo{suffix}"

        try:
            if src_abs.resolve() != dst.resolve():
                shutil.copy2(src_abs, dst)
            return self.to_project_relative_text(dst)
        except Exception as e:
            QMessageBox.warning(
                self,
                "提示",
                f"复制 LOGO 到 assets 目录失败，将继续使用原路径：\n{e}",
            )
            return logo_path_text

    def update_logo_preview(self, path_text: str):
        path = Path(path_text or "")
        if path and not path.is_absolute():
            path = self.resolve_project_path(str(path))

        if not path.exists() or not path.is_file():
            self.logo_preview_label.setPixmap(QPixmap())
            self.logo_preview_label.setText("无 LOGO")
            return

        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.logo_preview_label.setPixmap(QPixmap())
            self.logo_preview_label.setText("无法预览")
            return

        pixmap = pixmap.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.logo_preview_label.setText("")
        self.logo_preview_label.setPixmap(pixmap)

    def select_app_font_path(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择软件界面字体",
            "",
            "Font Files (*.ttf *.otf *.ttc);;所有文件 (*.*)",
        )
        if path:
            self.app_font_path_edit.setText(path)

    def reset_app_font(self):
        self.app_font_path_edit.setText("")

    def prepare_app_font_for_save(self, font_path_text: str) -> str:
        if not font_path_text or font_path_text.strip() in (".", "系统默认字体"):
            # 空路径 / . / “系统默认字体”表示使用系统默认字体，不复制、不强制使用内置字体。
            return ""

        src = Path(font_path_text)
        if not src.is_absolute():
            src_abs = self.resolve_project_path(str(src))
        else:
            src_abs = src

        if not src_abs.exists() or not src_abs.is_file():
            # 不阻止保存；启动时会自动回退默认字体。
            return font_path_text

        suffix = src_abs.suffix.lower()
        if suffix not in [".ttf", ".otf", ".ttc"]:
            QMessageBox.warning(self, "提示", "字体文件建议使用 .ttf、.otf 或 .ttc 格式。")
            return font_path_text

        fonts_dir = self.project_root / "assets" / "fonts"
        fonts_dir.mkdir(parents=True, exist_ok=True)

        # 内置默认字体不重复复制。
        try:
            if src_abs.resolve().is_relative_to(fonts_dir.resolve()):
                return self.to_project_relative_text(src_abs)
        except Exception:
            try:
                src_abs.resolve().relative_to(fonts_dir.resolve())
                return self.to_project_relative_text(src_abs)
            except Exception:
                pass

        dst = fonts_dir / f"custom_app_font{suffix}"
        try:
            if src_abs.resolve() != dst.resolve():
                shutil.copy2(src_abs, dst)
            return self.to_project_relative_text(dst)
        except Exception as e:
            QMessageBox.warning(
                self,
                "提示",
                f"复制字体到 assets/fonts 目录失败，将继续使用原路径：\n{e}",
            )
            return font_path_text

    # ------------------------------------------------------------------
    # 蛋白配置表
    # ------------------------------------------------------------------
    def load_protein_table(self):
        self.protein_table.setRowCount(0)
        for item in self.config.get_protein_items():
            self.add_protein_row(
                key=item.get("key", ""),
                name=item.get("name", ""),
                part=item.get("part", ""),
                pipeline=item.get("custom_pipeline", ""),
                intensity_min=item.get("intensity_min", 26.0),
                rate_min=item.get("rate_min", 82.88),
            )

    def add_protein_row(
        self,
        key="",
        name="",
        part="head",
        pipeline="",
        intensity_min="26.0",
        rate_min="82.88",
    ):
        row = self.protein_table.rowCount()
        self.protein_table.insertRow(row)
        self.protein_table.setRowHeight(row, 38)

        self.protein_table.setItem(row, 0, QTableWidgetItem(str(key)))
        self.protein_table.setItem(row, 1, QTableWidgetItem(str(name)))

        part_combo = QComboBox()
        part_combo.addItems(["head", "tail"])
        if part not in ["head", "tail"]:
            part = "head"
        part_combo.setCurrentText(part)
        self.style_part_combo(part_combo)
        self.protein_table.setCellWidget(row, 2, self.make_cell_widget(part_combo))

        self.protein_table.setItem(row, 3, QTableWidgetItem(str(pipeline)))
        self.protein_table.setItem(row, 4, QTableWidgetItem(str(intensity_min)))
        self.protein_table.setItem(row, 5, QTableWidgetItem(str(rate_min)))

        btn_select = QPushButton("选择")
        btn_select.setObjectName("ProteinSelectButton")
        btn_select.setFixedSize(68, 28)
        btn_select.clicked.connect(
            lambda checked=False, button=btn_select: self.select_protein_pipeline_for_button(button)
        )
        self.protein_table.setCellWidget(row, 6, self.make_cell_widget(btn_select, margin_left=8, margin_right=8))

    def add_empty_protein_row(self):
        next_index = self.protein_table.rowCount() + 1
        self.add_protein_row(
            key=f"protein{next_index}",
            name=f"protein{next_index}",
            part="head",
            pipeline="",
            intensity_min="26.0",
            rate_min="82.88",
        )

    def remove_selected_protein_row(self):
        selected_rows = self.protein_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.information(self, "提示", "请先选择要删除的蛋白行。")
            return

        for index in sorted(selected_rows, key=lambda x: x.row(), reverse=True):
            self.protein_table.removeRow(index.row())

    def reset_protein_defaults(self):
        reply = QMessageBox.question(
            self,
            "确认恢复",
            "确定要恢复默认蛋白配置吗？\n\n这会覆盖当前蛋白表格中的内容。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.protein_table.setRowCount(0)
        defaults = [
            ("protein1", "Q9BYW3", "head", "", "26.0", "82.88"),
            ("protein2", "P10323", "head", "", "26.0", "82.88"),
            ("protein3", "Q96P56", "tail", "", "26.0", "82.88"),
            ("protein4", "Q8IYV9", "head", "", "26.0", "82.88"),
            ("protein5", "W5XKT8", "head", "", "26.0", "82.88"),
        ]
        for item in defaults:
            self.add_protein_row(*item)

    def select_protein_pipeline_for_button(self, button):
        row = self.find_button_row(button)
        if row < 0:
            QMessageBox.warning(self, "提示", "无法定位当前蛋白行。")
            return

        current_path = self.get_table_text(row, 3)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择该蛋白的 Pipeline",
            current_path,
            "MvImageID Pipeline (*.cppipe);;所有文件 (*.*)",
        )
        if path:
            self.protein_table.setItem(row, 3, QTableWidgetItem(path))

    def find_button_row(self, button):
        for row in range(self.protein_table.rowCount()):
            cell_button = self.get_button_from_row(row)
            if cell_button is button:
                return row
        return -1

    def save_protein_table_to_config(self):
        keys = []
        seen_keys = set()

        for row in range(self.protein_table.rowCount()):
            key = self.get_table_text(row, 0).strip()
            name = self.get_table_text(row, 1).strip()
            pipeline = self.get_table_text(row, 3).strip()
            intensity_min = self.get_table_text(row, 4).strip()
            rate_min = self.get_table_text(row, 5).strip()

            part_widget = self.get_part_combo_from_row(row)
            part = part_widget.currentText().strip() if isinstance(part_widget, QComboBox) else "head"

            if not key:
                return False, f"第 {row + 1} 行内部编号不能为空。"
            if key in seen_keys:
                return False, f"内部编号重复：{key}"
            if not name:
                return False, f"第 {row + 1} 行显示名称不能为空。"
            if part not in ["head", "tail"]:
                return False, f"第 {row + 1} 行表达部位必须是 head 或 tail。"

            try:
                float(intensity_min)
            except Exception:
                return False, f"第 {row + 1} 行荧光强度下限不是数字。"

            try:
                float(rate_min)
            except Exception:
                return False, f"第 {row + 1} 行标定率下限不是数字。"

            keys.append(key)
            seen_keys.add(key)

            self.config.set("Protein", key, part)
            self.config.set("ProteinNames", key, name)
            self.config.set("ProteinPipelines", key, pipeline)
            self.config.set("ProteinReferenceIntensityMin", key, intensity_min)
            self.config.set("ProteinReferenceRateMin", key, rate_min)

        if not keys:
            return False, "蛋白配置不能为空。"

        self.config.set("ProteinOrder", "keys", ",".join(keys))
        return True, "蛋白配置保存成功。"

    def get_table_text(self, row, col):
        item = self.protein_table.item(row, col)
        return item.text() if item else ""

    # ------------------------------------------------------------------
    # 路径检查
    # ------------------------------------------------------------------
    def check_paths(self):
        self.log_edit.clear()

        checks = [
            ("软件 LOGO", self.app_logo_path_edit.text().strip(), "file_optional"),
            ("界面字体", self.app_font_path_edit.text().strip(), "font_optional"),
            ("源码目录", self.source_project_dir_edit.text().strip(), "dir"),
            ("Python解释器", self.python_exe_edit.text().strip(), "file"),
            ("头部 Pipeline", self.head_pipeline_edit.text().strip(), "file"),
            ("尾部 Pipeline", self.tail_pipeline_edit.text().strip(), "file"),
            ("质控 Pipeline", self.qc_pipeline_edit.text().strip(), "file"),
            ("插件目录", self.plugins_directory_edit.text().strip(), "dir"),
            ("病例工作目录", self.workspace_root_edit.text().strip(), "dir_create"),
            ("数据库文件", self.database_edit.text().strip(), "parent_create"),
            ("报告目录", self.report_dir_edit.text().strip(), "dir_create"),
            ("报告 LOGO 图片", self.logo_path_edit.text().strip(), "file_optional"),
        ]

        for row in range(self.protein_table.rowCount()):
            key = self.get_table_text(row, 0).strip()
            pipeline = self.get_table_text(row, 3).strip()
            if pipeline:
                checks.append((f"{key} 自定义 Pipeline", pipeline, "file"))

        all_ok = True
        for name, path_text, check_type in checks:
            ok, message = self._check_one_path(path_text, check_type)
            if ok:
                self.append_log(f"√ {name}：{message}")
            else:
                all_ok = False
                self.append_log(f"× {name}：{message}")

        if all_ok:
            QMessageBox.information(self, "检查完成", "所有关键路径检查通过。")
        else:
            QMessageBox.warning(self, "检查完成", "部分路径存在问题，请查看检查结果。")

    def _check_one_path(self, path_text: str, check_type: str):
        path_text = str(path_text or "").strip()

        if check_type == "font_optional":
            if not path_text or path_text in (".", "系统默认字体"):
                return True, "系统默认字体"
            path = Path(path_text)
            if not path.is_absolute():
                path = self.resolve_project_path(str(path))
            if path.exists() and path.is_file():
                return True, str(path)
            return False, f"字体文件不存在：{path}"

        if not path_text:
            if check_type == "file_optional":
                return True, "未设置，已跳过。"
            return False, "路径为空。"

        path = Path(path_text)
        if not path.is_absolute():
            path = self.resolve_project_path(str(path))

        if check_type == "file":
            if path.exists() and path.is_file():
                return True, str(path)
            return False, f"文件不存在：{path}"

        if check_type == "file_optional":
            if path.exists() and path.is_file():
                return True, str(path)
            return False, f"文件不存在：{path}"

        if check_type == "dir":
            if path.exists() and path.is_dir():
                return True, str(path)
            return False, f"目录不存在：{path}"

        if check_type == "dir_create":
            try:
                path.mkdir(parents=True, exist_ok=True)
                return True, str(path)
            except Exception as e:
                return False, f"目录创建失败：{path}，{e}"

        if check_type == "parent_create":
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                return True, f"父目录可用：{path.parent}"
            except Exception as e:
                return False, f"父目录创建失败：{path.parent}，{e}"

        return False, "未知检查类型。"

    # ------------------------------------------------------------------
    # 文件/文件夹选择
    # ------------------------------------------------------------------
    def select_qc_source_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择微球图片文件夹")
        if path:
            self.qc_source_folder_edit.setText(path)

    def select_qc_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择本次质控输出目录")
        if path:
            self.qc_output_dir_edit.setText(path)

    def reset_qc_output_dir(self):
        try:
            qc_default_dir = QCBeadsService(self.config).get_next_run_dir()
            self.qc_output_dir_edit.setText(self.to_project_relative_text(qc_default_dir))
        except Exception:
            self.qc_output_dir_edit.setText(r"workspace\qc")

    def select_qc_pipeline(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择质控微球 Pipeline",
            self.qc_pipeline_edit.text().strip(),
            "MvImageID Pipeline (*.cppipe);;所有文件 (*.*)",
        )
        if path:
            self.qc_pipeline_edit.setText(path)

    def run_qc_beads_test(self):
        source_folder = self.qc_source_folder_edit.text().strip()
        run_dir = self.qc_output_dir_edit.text().strip()
        qc_pipeline = self.qc_pipeline_edit.text().strip()

        if not source_folder:
            QMessageBox.warning(self, "提示", "请先选择微球图片文件夹。")
            return
        if not qc_pipeline:
            QMessageBox.warning(self, "提示", "请先设置质控 Pipeline。")
            return
        if not run_dir:
            self.reset_qc_output_dir()
            run_dir = self.qc_output_dir_edit.text().strip()

        source_path = Path(source_folder)
        if not source_path.is_absolute():
            source_path = self.resolve_project_path(str(source_path))
        if not source_path.exists() or not source_path.is_dir():
            QMessageBox.warning(self, "提示", f"微球图片文件夹不存在：\n{source_path}")
            return

        pipeline_path = Path(qc_pipeline)
        if not pipeline_path.is_absolute():
            pipeline_path = self.resolve_project_path(str(pipeline_path))
        if not pipeline_path.exists() or not pipeline_path.is_file():
            QMessageBox.warning(self, "提示", f"质控 Pipeline 不存在：\n{pipeline_path}")
            return

        reply = QMessageBox.question(
            self,
            "确认运行质控测试",
            "将运行微球荧光强度质控测试。\n\n"
            f"图片目录：\n{source_path}\n\n"
            f"输出目录：\n{self.resolve_project_path(run_dir) if not Path(run_dir).is_absolute() else Path(run_dir)}\n\n"
            "该功能不会写入病例数据库，也不会影响报告结果。\n\n是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        self.log_edit.clear()
        self.append_log("准备运行质控微球测试...")
        self.set_qc_running(True)

        self.qc_worker = QCBeadsWorker(
            config_path=str(self.project_root / "config.ini"),
            source_folder=source_folder,
            run_dir=run_dir,
            qc_pipeline=qc_pipeline,
            parent=self,
        )
        self.qc_worker.log_signal.connect(self.append_log)
        self.qc_worker.finished_signal.connect(self.on_qc_beads_finished)
        self.qc_worker.start()

    def on_qc_beads_finished(self, success: bool, elapsed: float, message: str, run_dir: str, output_dir: str):
        self.set_qc_running(False)
        if run_dir:
            self.qc_output_dir_edit.setText(run_dir)
        self.current_qc_output_dir = output_dir or run_dir
        self.append_log(message)

        if success:
            QMessageBox.information(self, "质控完成", message)
        else:
            QMessageBox.warning(self, "质控失败", message)

    def set_qc_running(self, running: bool):
        self.btn_run_qc.setEnabled(not running)
        self.btn_run_qc.setText("正在运行..." if running else "运行质控测试")
        self.btn_open_qc_output.setEnabled(not running)

    def open_qc_output_dir(self):
        path_text = ""
        if hasattr(self, "current_qc_output_dir") and self.current_qc_output_dir:
            path_text = str(self.current_qc_output_dir)
        else:
            path_text = self.qc_output_dir_edit.text().strip()

        if not path_text:
            QMessageBox.information(self, "提示", "当前没有质控输出目录。")
            return

        path = Path(path_text)
        if not path.is_absolute():
            path = self.resolve_project_path(str(path))

        # 如果用户填的是本次运行目录，优先打开 output；如果 output 不存在，就打开运行目录本身。
        output_candidate = path / "output"
        if output_candidate.exists() and output_candidate.is_dir():
            path = output_candidate

        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def select_python_exe(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 MvImageID Python解释器",
            "",
            "Python解释器 (python.exe);;Executable (*.exe);;所有文件 (*.*)",
        )
        if path:
            self.python_exe_edit.setText(path)

    def select_source_project_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择 MvImageID 源码目录")
        if path:
            self.source_project_dir_edit.setText(path)

    def select_head_pipeline(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择头部 Pipeline",
            "",
            "MvImageID Pipeline (*.cppipe);;所有文件 (*.*)",
        )
        if path:
            self.head_pipeline_edit.setText(path)

    def select_tail_pipeline(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择尾部 Pipeline",
            "",
            "MvImageID Pipeline (*.cppipe);;所有文件 (*.*)",
        )
        if path:
            self.tail_pipeline_edit.setText(path)

    def select_plugins_directory(self):
        path = QFileDialog.getExistingDirectory(self, "选择插件目录")
        if path:
            self.plugins_directory_edit.setText(path)

    def select_workspace_root(self):
        path = QFileDialog.getExistingDirectory(self, "选择病例工作目录")
        if path:
            self.workspace_root_edit.setText(path)

    def select_database_file(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "选择数据库文件",
            "analysis.db",
            "SQLite Database (*.db);;所有文件 (*.*)",
        )
        if path:
            self.database_edit.setText(path)

    def select_report_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择报告目录")
        if path:
            self.report_dir_edit.setText(path)

    def select_logo_path(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择报告 LOGO 图片",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp);;所有文件 (*.*)",
        )
        if path:
            self.logo_path_edit.setText(path)

    def append_log(self, message: str):
        self.log_edit.append(str(message))
