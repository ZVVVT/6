from pathlib import Path

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QFileDialog,
    QMessageBox,
    QGroupBox,
    QTextEdit,
)

from core.config_manager import ConfigManager


class SettingsWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.config = ConfigManager()
        self.config.ensure_default_config()

        self.init_ui()
        self.load_config()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        title_label = QLabel("系统设置")
        title_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        main_layout.addWidget(title_label)

        # MvImageID 设置
        cp_group = QGroupBox("MvImageID / CellProfiler 源码环境")
        cp_layout = QFormLayout(cp_group)

        self.powershell_edit = QLineEdit()
        self.source_project_dir_edit = QLineEdit()
        self.venv_activate_edit = QLineEdit()
        self.module_name_edit = QLineEdit()
        self.head_pipeline_edit = QLineEdit()
        self.tail_pipeline_edit = QLineEdit()
        self.pna_pipeline_edit = QLineEdit()
        self.plugins_directory_edit = QLineEdit()
        self.log_file_edit = QLineEdit()

        cp_layout.addRow("PowerShell：", self._with_button(self.powershell_edit, "选择", self.select_powershell))
        cp_layout.addRow("源码目录：", self._with_button(self.source_project_dir_edit, "选择", self.select_source_project_dir))
        cp_layout.addRow("虚拟环境 Activate.ps1：", self._with_button(self.venv_activate_edit, "选择", self.select_venv_activate))
        cp_layout.addRow("模块名称：", self.module_name_edit)
        cp_layout.addRow("头部 Pipeline：", self._with_button(self.head_pipeline_edit, "选择", self.select_head_pipeline))
        cp_layout.addRow("尾部 Pipeline：", self._with_button(self.tail_pipeline_edit, "选择", self.select_tail_pipeline))
        cp_layout.addRow("PNA Pipeline：", self._with_button(self.pna_pipeline_edit, "选择", self.select_pna_pipeline))
        cp_layout.addRow("插件目录：", self._with_button(self.plugins_directory_edit, "选择", self.select_plugins_directory))
        cp_layout.addRow("日志文件：", self._with_button(self.log_file_edit, "选择", self.select_log_file))

        main_layout.addWidget(cp_group)

        # 工作目录设置
        workspace_group = QGroupBox("工作目录与报告设置")
        workspace_layout = QFormLayout(workspace_group)

        self.workspace_root_edit = QLineEdit()
        self.database_edit = QLineEdit()
        self.report_dir_edit = QLineEdit()
        self.logo_path_edit = QLineEdit()

        workspace_layout.addRow("病例工作目录：", self._with_button(self.workspace_root_edit, "选择", self.select_workspace_root))
        workspace_layout.addRow("数据库文件：", self._with_button(self.database_edit, "选择", self.select_database_file))
        workspace_layout.addRow("报告目录：", self._with_button(self.report_dir_edit, "选择", self.select_report_dir))
        workspace_layout.addRow("LOGO 图片：", self._with_button(self.logo_path_edit, "选择", self.select_logo_path))

        main_layout.addWidget(workspace_group)

        # 图片命名规则
        image_rule_group = QGroupBox("图片命名规则")
        image_rule_layout = QFormLayout(image_rule_group)

        self.r_suffix_edit = QLineEdit()
        self.g_suffix_edit = QLineEdit()
        self.dic_suffix_edit = QLineEdit()
        self.merge_suffix_edit = QLineEdit()
        self.image_ext_edit = QLineEdit()

        image_rule_layout.addRow("R 通道后缀：", self.r_suffix_edit)
        image_rule_layout.addRow("G 通道后缀：", self.g_suffix_edit)
        image_rule_layout.addRow("DIC 通道后缀：", self.dic_suffix_edit)
        image_rule_layout.addRow("Merge 通道后缀：", self.merge_suffix_edit)
        image_rule_layout.addRow("默认图片扩展名：", self.image_ext_edit)

        main_layout.addWidget(image_rule_group)

        # 按钮
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

    def _with_button(self, line_edit: QLineEdit, button_text: str, callback):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        button = QPushButton(button_text)
        button.setFixedWidth(60)
        button.clicked.connect(callback)

        layout.addWidget(line_edit, 1)
        layout.addWidget(button)

        return widget

    def load_config(self):
        self.config.load()
        self.config.ensure_default_config()

        self.powershell_edit.setText(self.config.get("CellProfiler", "powershell_exe", "powershell.exe"))
        self.source_project_dir_edit.setText(self.config.get("CellProfiler", "source_project_dir", ""))
        self.venv_activate_edit.setText(self.config.get("CellProfiler", "venv_activate", ""))
        self.module_name_edit.setText(self.config.get("CellProfiler", "module_name", "MvImageID"))
        self.head_pipeline_edit.setText(self.config.get("CellProfiler", "head_pipeline", ""))
        self.tail_pipeline_edit.setText(self.config.get("CellProfiler", "tail_pipeline", ""))
        self.pna_pipeline_edit.setText(self.config.get("CellProfiler", "pna_pipeline", ""))
        self.plugins_directory_edit.setText(self.config.get("CellProfiler", "plugins_directory", ""))
        self.log_file_edit.setText(self.config.get("CellProfiler", "log_file", ""))

        self.workspace_root_edit.setText(self.config.get("Workspace", "root_dir", "workspace\\cases"))
        self.database_edit.setText(self.config.get("Workspace", "database", "data\\analysis.db"))
        self.report_dir_edit.setText(self.config.get("Workspace", "report_dir", "reports"))
        self.logo_path_edit.setText(self.config.get("Report", "logo_path", "assets\\logo.png"))

        self.r_suffix_edit.setText(self.config.get("ImageRule", "r_suffix", "_R"))
        self.g_suffix_edit.setText(self.config.get("ImageRule", "g_suffix", "_G"))
        self.dic_suffix_edit.setText(self.config.get("ImageRule", "dic_suffix", "_DIC"))
        self.merge_suffix_edit.setText(self.config.get("ImageRule", "merge_suffix", "_Merge"))
        self.image_ext_edit.setText(self.config.get("ImageRule", "image_ext", ".tif"))

        self.append_log("配置已重新加载。")

    def save_config(self):
        self.config.set("CellProfiler", "run_mode", "source")
        self.config.set("CellProfiler", "powershell_exe", self.powershell_edit.text().strip())
        self.config.set("CellProfiler", "source_project_dir", self.source_project_dir_edit.text().strip())
        self.config.set("CellProfiler", "venv_activate", self.venv_activate_edit.text().strip())
        self.config.set("CellProfiler", "module_name", self.module_name_edit.text().strip())
        self.config.set("CellProfiler", "head_pipeline", self.head_pipeline_edit.text().strip())
        self.config.set("CellProfiler", "tail_pipeline", self.tail_pipeline_edit.text().strip())
        self.config.set("CellProfiler", "pna_pipeline", self.pna_pipeline_edit.text().strip())
        self.config.set("CellProfiler", "plugins_directory", self.plugins_directory_edit.text().strip())
        self.config.set("CellProfiler", "log_file", self.log_file_edit.text().strip())

        self.config.set("Workspace", "root_dir", self.workspace_root_edit.text().strip())
        self.config.set("Workspace", "database", self.database_edit.text().strip())
        self.config.set("Workspace", "report_dir", self.report_dir_edit.text().strip())

        self.config.set("Report", "logo_path", self.logo_path_edit.text().strip())

        self.config.set("ImageRule", "r_suffix", self.r_suffix_edit.text().strip())
        self.config.set("ImageRule", "g_suffix", self.g_suffix_edit.text().strip())
        self.config.set("ImageRule", "dic_suffix", self.dic_suffix_edit.text().strip())
        self.config.set("ImageRule", "merge_suffix", self.merge_suffix_edit.text().strip())
        self.config.set("ImageRule", "image_ext", self.image_ext_edit.text().strip())

        try:
            self.config.save()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存配置失败：\n{e}")
            return

        QMessageBox.information(self, "成功", "系统设置已保存。")
        self.append_log("系统设置已保存到 config.ini。")

    def check_paths(self):
        self.log_edit.clear()

        checks = [
            ("源码目录", self.source_project_dir_edit.text().strip(), "dir"),
            ("虚拟环境 Activate.ps1", self.venv_activate_edit.text().strip(), "file"),
            ("头部 Pipeline", self.head_pipeline_edit.text().strip(), "file"),
            ("尾部 Pipeline", self.tail_pipeline_edit.text().strip(), "file"),
            ("PNA Pipeline", self.pna_pipeline_edit.text().strip(), "file"),
            ("插件目录", self.plugins_directory_edit.text().strip(), "dir"),
            ("病例工作目录", self.workspace_root_edit.text().strip(), "dir_create"),
            ("数据库文件", self.database_edit.text().strip(), "parent_create"),
            ("报告目录", self.report_dir_edit.text().strip(), "dir_create"),
            ("LOGO 图片", self.logo_path_edit.text().strip(), "file_optional"),
        ]

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
        if not path_text:
            if check_type == "file_optional":
                return True, "未设置，已跳过。"
            return False, "路径为空。"

        path = Path(path_text)

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

    # -------------------------
    # 文件/文件夹选择
    # -------------------------

    def select_powershell(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 PowerShell",
            "",
            "PowerShell (*.exe);;所有文件 (*.*)",
        )
        if path:
            self.powershell_edit.setText(path)

    def select_source_project_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择 MvImageID 源码目录")
        if path:
            self.source_project_dir_edit.setText(path)

    def select_venv_activate(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Activate.ps1",
            "",
            "PowerShell Script (*.ps1);;所有文件 (*.*)",
        )
        if path:
            self.venv_activate_edit.setText(path)

    def select_head_pipeline(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择头部 Pipeline",
            "",
            "CellProfiler Pipeline (*.cppipe);;所有文件 (*.*)",
        )
        if path:
            self.head_pipeline_edit.setText(path)

    def select_tail_pipeline(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择尾部 Pipeline",
            "",
            "CellProfiler Pipeline (*.cppipe);;所有文件 (*.*)",
        )
        if path:
            self.tail_pipeline_edit.setText(path)

    def select_pna_pipeline(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 PNA Pipeline",
            "",
            "CellProfiler Pipeline (*.cppipe);;所有文件 (*.*)",
        )
        if path:
            self.pna_pipeline_edit.setText(path)

    def select_plugins_directory(self):
        path = QFileDialog.getExistingDirectory(self, "选择插件目录")
        if path:
            self.plugins_directory_edit.setText(path)

    def select_log_file(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "选择日志文件",
            "run.log",
            "Log File (*.log);;Text File (*.txt);;所有文件 (*.*)",
        )
        if path:
            self.log_file_edit.setText(path)

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
            "选择 LOGO 图片",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp);;所有文件 (*.*)",
        )
        if path:
            self.logo_path_edit.setText(path)

    def append_log(self, message: str):
        self.log_edit.append(str(message))