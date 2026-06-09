from pathlib import Path

from PySide6.QtCore import Qt
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
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QComboBox,
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

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs, 1)

        self.init_runtime_tab()
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

    def init_runtime_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        group = QGroupBox("MvImageID / CellProfiler 源码环境")
        form = QFormLayout(group)

        self.powershell_edit = QLineEdit()
        self.source_project_dir_edit = QLineEdit()
        self.venv_activate_edit = QLineEdit()
        self.module_name_edit = QLineEdit()
        self.head_pipeline_edit = QLineEdit()
        self.tail_pipeline_edit = QLineEdit()
        self.pna_pipeline_edit = QLineEdit()
        self.plugins_directory_edit = QLineEdit()
        self.log_file_edit = QLineEdit()

        form.addRow("PowerShell：", self._with_button(self.powershell_edit, "选择", self.select_powershell))
        form.addRow("源码目录：", self._with_button(self.source_project_dir_edit, "选择", self.select_source_project_dir))
        form.addRow("虚拟环境 Activate.ps1：", self._with_button(self.venv_activate_edit, "选择", self.select_venv_activate))
        form.addRow("模块名称：", self.module_name_edit)
        form.addRow("头部 Pipeline：", self._with_button(self.head_pipeline_edit, "选择", self.select_head_pipeline))
        form.addRow("尾部 Pipeline：", self._with_button(self.tail_pipeline_edit, "选择", self.select_tail_pipeline))
        form.addRow("PNA Pipeline：", self._with_button(self.pna_pipeline_edit, "选择", self.select_pna_pipeline))
        form.addRow("插件目录：", self._with_button(self.plugins_directory_edit, "选择", self.select_plugins_directory))
        form.addRow("日志文件：", self._with_button(self.log_file_edit, "选择", self.select_log_file))

        layout.addWidget(group)
        layout.addStretch()

        self.tabs.addTab(tab, "运行环境")

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
        form.addRow("LOGO 图片：", self._with_button(self.logo_path_edit, "选择", self.select_logo_path))

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
            "自定义 Pipeline 为空时，会按表达部位自动使用头部/尾部/PNA Pipeline。"
        )
        tips.setWordWrap(True)
        tips.setStyleSheet("color: #666666;")
        layout.addWidget(tips)

        self.protein_table = QTableWidget()
        self.protein_table.setColumnCount(7)
        self.protein_table.setHorizontalHeaderLabels([
            "内部编号",
            "显示名称",
            "表达部位",
            "自定义 Pipeline",
            "荧光强度下限",
            "标定率下限(%)",
            "选择",
        ])
        self.protein_table.setAlternatingRowColors(True)
        self.protein_table.verticalHeader().setVisible(False)

        header = self.protein_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)

        layout.addWidget(self.protein_table, 1)

        btn_layout = QHBoxLayout()

        self.btn_reset_protein_defaults = QPushButton("恢复默认蛋白配置")
        self.btn_add_protein_row = QPushButton("增加一行")
        self.btn_remove_protein_row = QPushButton("删除选中行")

        btn_layout.addWidget(self.btn_reset_protein_defaults)
        btn_layout.addWidget(self.btn_add_protein_row)
        btn_layout.addWidget(self.btn_remove_protein_row)
        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        self.btn_reset_protein_defaults.clicked.connect(self.reset_protein_defaults)
        self.btn_add_protein_row.clicked.connect(self.add_empty_protein_row)
        self.btn_remove_protein_row.clicked.connect(self.remove_selected_protein_row)

        self.tabs.addTab(tab, "蛋白配置")

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

        self.load_protein_table()

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

        ok, message = self.save_protein_table_to_config()
        if not ok:
            QMessageBox.warning(self, "提示", message)
            return

        try:
            self.config.save()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存配置失败：\n{e}")
            return

        QMessageBox.information(self, "成功", "系统设置已保存。建议重启软件，使全部页面重新读取配置。")
        self.append_log("系统设置已保存到 config.ini。")

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

    def add_protein_row(self, key="", name="", part="head", pipeline="", intensity_min="26.0", rate_min="82.88"):
        row = self.protein_table.rowCount()
        self.protein_table.insertRow(row)

        self.protein_table.setItem(row, 0, QTableWidgetItem(str(key)))
        self.protein_table.setItem(row, 1, QTableWidgetItem(str(name)))

        part_combo = QComboBox()
        part_combo.addItems(["head", "tail", "pna"])
        if part not in ["head", "tail", "pna"]:
            part = "head"
        part_combo.setCurrentText(part)
        self.protein_table.setCellWidget(row, 2, part_combo)

        self.protein_table.setItem(row, 3, QTableWidgetItem(str(pipeline)))
        self.protein_table.setItem(row, 4, QTableWidgetItem(str(intensity_min)))
        self.protein_table.setItem(row, 5, QTableWidgetItem(str(rate_min)))

        btn_select = QPushButton("选择")
        btn_select.clicked.connect(lambda checked=False, r=row: self.select_protein_pipeline_for_row(r))
        self.protein_table.setCellWidget(row, 6, btn_select)

    def add_empty_protein_row(self):
        next_index = self.protein_table.rowCount() + 1
        self.add_protein_row(
            key=f"protein{next_index}",
            name=f"HEL-{next_index}",
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
            "确定要恢复默认蛋白配置吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        self.protein_table.setRowCount(0)

        defaults = [
            ("protein1", "HEL-1", "head", "", "26.0", "82.88"),
            ("protein2", "HEL-2", "head", "", "26.0", "82.88"),
            ("protein3", "HEL-3", "tail", "", "26.0", "82.88"),
            ("protein4", "HEL-4", "head", "", "26.0", "82.88"),
            ("protein5", "HEL-5", "head", "", "26.0", "82.88"),
            ("pna", "PNA", "pna", "", "0", "0"),
        ]

        for item in defaults:
            self.add_protein_row(*item)

    def select_protein_pipeline_for_row(self, row):
        current_path = self.get_table_text(row, 3)

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择该蛋白的 Pipeline",
            current_path,
            "CellProfiler Pipeline (*.cppipe);;所有文件 (*.*)",
        )

        if path:
            self.protein_table.setItem(row, 3, QTableWidgetItem(path))

    def save_protein_table_to_config(self):
        keys = []
        seen = set()

        for row in range(self.protein_table.rowCount()):
            key = self.get_table_text(row, 0).strip()
            name = self.get_table_text(row, 1).strip()
            pipeline = self.get_table_text(row, 3).strip()
            intensity_min = self.get_table_text(row, 4).strip()
            rate_min = self.get_table_text(row, 5).strip()

            part_widget = self.protein_table.cellWidget(row, 2)
            part = part_widget.currentText().strip() if isinstance(part_widget, QComboBox) else "head"

            if not key:
                return False, f"第 {row + 1} 行内部编号不能为空。"

            if key in seen:
                return False, f"内部编号重复：{key}"

            if not name:
                return False, f"第 {row + 1} 行显示名称不能为空。"

            if part not in ["head", "tail", "pna"]:
                return False, f"第 {row + 1} 行表达部位必须是 head、tail 或 pna。"

            try:
                float(intensity_min)
            except Exception:
                return False, f"第 {row + 1} 行荧光强度下限不是数字。"

            try:
                float(rate_min)
            except Exception:
                return False, f"第 {row + 1} 行标定率下限不是数字。"

            keys.append(key)
            seen.add(key)

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
        if not path_text:
            if check_type == "file_optional":
                return True, "未设置，已跳过。"
            return False, "路径为空。"

        path = Path(path_text)

        if check_type == "file":
            return (True, str(path)) if path.exists() and path.is_file() else (False, f"文件不存在：{path}")

        if check_type == "file_optional":
            return (True, str(path)) if path.exists() and path.is_file() else (False, f"文件不存在：{path}")

        if check_type == "dir":
            return (True, str(path)) if path.exists() and path.is_dir() else (False, f"目录不存在：{path}")

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

    def select_powershell(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 PowerShell", "", "PowerShell (*.exe);;所有文件 (*.*)")
        if path:
            self.powershell_edit.setText(path)

    def select_source_project_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择 MvImageID 源码目录")
        if path:
            self.source_project_dir_edit.setText(path)

    def select_venv_activate(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 Activate.ps1", "", "PowerShell Script (*.ps1);;所有文件 (*.*)")
        if path:
            self.venv_activate_edit.setText(path)

    def select_head_pipeline(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择头部 Pipeline", "", "CellProfiler Pipeline (*.cppipe);;所有文件 (*.*)")
        if path:
            self.head_pipeline_edit.setText(path)

    def select_tail_pipeline(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择尾部 Pipeline", "", "CellProfiler Pipeline (*.cppipe);;所有文件 (*.*)")
        if path:
            self.tail_pipeline_edit.setText(path)

    def select_pna_pipeline(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 PNA Pipeline", "", "CellProfiler Pipeline (*.cppipe);;所有文件 (*.*)")
        if path:
            self.pna_pipeline_edit.setText(path)

    def select_plugins_directory(self):
        path = QFileDialog.getExistingDirectory(self, "选择插件目录")
        if path:
            self.plugins_directory_edit.setText(path)

    def select_log_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "选择日志文件", "run.log", "Log File (*.log);;Text File (*.txt);;所有文件 (*.*)")
        if path:
            self.log_file_edit.setText(path)

    def select_workspace_root(self):
        path = QFileDialog.getExistingDirectory(self, "选择病例工作目录")
        if path:
            self.workspace_root_edit.setText(path)

    def select_database_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "选择数据库文件", "analysis.db", "SQLite Database (*.db);;所有文件 (*.*)")
        if path:
            self.database_edit.setText(path)

    def select_report_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择报告目录")
        if path:
            self.report_dir_edit.setText(path)

    def select_logo_path(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 LOGO 图片", "", "Image Files (*.png *.jpg *.jpeg *.bmp);;所有文件 (*.*)")
        if path:
            self.logo_path_edit.setText(path)

    def append_log(self, message: str):
        self.log_edit.append(str(message))