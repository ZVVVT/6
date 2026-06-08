import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QFileDialog,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
    QHeaderView,
    QGroupBox,
    QTextEdit,
)

from app.result_viewer import ResultViewer
from core.cellprofiler_runner import CellProfilerWorker
from core.config_manager import ConfigManager
from core.image_importer import ImageImporter
from core.result_parser import ResultParser


class AnalysisWindow(QWidget):
    def __init__(self, database, parent=None):
        super().__init__(parent)

        self.database = database
        self.config = ConfigManager()
        self.current_case = None
        self.imported_images = []
        self.cp_worker = None
        self.current_cp_output_dir = None
        self.current_raw_image_folder = None

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        title_label = QLabel("蛋白分析")
        title_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        main_layout.addWidget(title_label)

        self.case_group = QGroupBox("当前病例")
        case_layout = QFormLayout(self.case_group)

        self.case_no_label = QLabel("未选择")
        self.patient_name_label = QLabel("-")
        self.sample_no_label = QLabel("-")
        self.test_date_label = QLabel("-")

        case_layout.addRow("病例编号：", self.case_no_label)
        case_layout.addRow("姓名：", self.patient_name_label)
        case_layout.addRow("样本编号：", self.sample_no_label)
        case_layout.addRow("检测日期：", self.test_date_label)

        main_layout.addWidget(self.case_group)

        operation_group = QGroupBox("分析设置")
        operation_layout = QVBoxLayout(operation_group)

        row1_layout = QHBoxLayout()

        self.protein_combo = QComboBox()
        self.protein_combo.addItems([
            "protein1",
            "protein2",
            "protein3",
            "protein4",
            "protein5",
            "pna",
        ])

        self.protein_part_label = QLabel("表达部位：-")
        self.pipeline_label = QLabel("Pipeline：-")

        self.protein_combo.currentTextChanged.connect(self.on_protein_changed)

        row1_layout.addWidget(QLabel("蛋白名称："))
        row1_layout.addWidget(self.protein_combo)
        row1_layout.addWidget(self.protein_part_label)
        row1_layout.addWidget(self.pipeline_label)
        row1_layout.addStretch()

        operation_layout.addLayout(row1_layout)

        row2_layout = QHBoxLayout()

        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("请选择包含 R / G / DIC / Merge 图片的文件夹")

        self.btn_select_folder = QPushButton("选择文件夹")
        self.btn_import = QPushButton("导入图片")
        self.btn_run_cp = QPushButton("运行分析")
        self.btn_run_cp.setEnabled(False)

        row2_layout.addWidget(QLabel("图片文件夹："))
        row2_layout.addWidget(self.folder_edit, 1)
        row2_layout.addWidget(self.btn_select_folder)
        row2_layout.addWidget(self.btn_import)
        row2_layout.addWidget(self.btn_run_cp)

        operation_layout.addLayout(row2_layout)

        main_layout.addWidget(operation_group)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "视野",
            "R/PI",
            "G/FITC",
            "DIC/相差",
            "Merge",
            "状态",
        ])

        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)

        main_layout.addWidget(self.table, 1)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumHeight(150)
        self.log_edit.setPlaceholderText("运行日志")
        main_layout.addWidget(self.log_edit)

        self.result_viewer = ResultViewer()
        self.result_viewer.setMinimumHeight(320)
        main_layout.addWidget(self.result_viewer, 2)

        self.btn_select_folder.clicked.connect(self.select_folder)
        self.btn_import.clicked.connect(self.import_images)
        self.btn_run_cp.clicked.connect(self.run_cellprofiler)

        self.on_protein_changed(self.protein_combo.currentText())

    def set_case(self, case_data: dict):
        self.current_case = case_data

        self.case_no_label.setText(str(case_data.get("case_no", "")))
        self.patient_name_label.setText(str(case_data.get("patient_name", "")))
        self.sample_no_label.setText(str(case_data.get("sample_no", "")))
        self.test_date_label.setText(str(case_data.get("test_date", "")))

        self.append_log(
            f"已载入病例：{case_data.get('case_no', '')} - {case_data.get('patient_name', '')}"
        )

    def on_protein_changed(self, protein_name: str):
        protein_part = self.config.get_protein_part(protein_name)
        if not protein_part:
            protein_part = "未配置"

        pipeline_path = self.config.get_pipeline_by_protein(protein_name)

        self.protein_part_label.setText(f"表达部位：{protein_part}")
        self.pipeline_label.setText(f"Pipeline：{pipeline_path}")

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "选择图片文件夹",
            "",
        )

        if folder:
            self.folder_edit.setText(folder)
            self.append_log(f"选择图片文件夹：{folder}")

    def import_images(self):
        if not self.current_case:
            QMessageBox.information(self, "提示", "请先在病例管理中双击选择病例。")
            return

        source_folder = self.folder_edit.text().strip()
        if not source_folder:
            QMessageBox.information(self, "提示", "请先选择图片文件夹。")
            return

        protein_name = self.protein_combo.currentText()
        case_no = str(self.current_case.get("case_no", "")).strip()

        if not case_no:
            QMessageBox.warning(self, "提示", "当前病例编号为空，无法建立工作目录。")
            return

        workspace_root = self.config.get_workspace_root()
        target_folder = workspace_root / case_no / "raw_images" / protein_name
        self.current_raw_image_folder = target_folder

        try:
            importer = ImageImporter(self.config.get_image_rule())
            self.imported_images = importer.copy_to_workspace(
                source_folder=source_folder,
                target_folder=str(target_folder),
                protein_name=protein_name,
            )
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导入图片失败：\n{e}")
            return

        self.refresh_table(self.imported_images)

        complete_count = sum(1 for item in self.imported_images if item["status"] == "完整")
        total_count = len(self.imported_images)

        self.append_log(f"图片导入完成：共识别 {total_count} 个视野，完整视野 {complete_count} 个。")
        self.append_log(f"图片已复制到：{target_folder}")

        self.btn_run_cp.setEnabled(complete_count > 0)

        QMessageBox.information(
            self,
            "导入完成",
            f"共识别 {total_count} 个视野。\n完整视野：{complete_count} 个。\n\n已复制到：\n{target_folder}"
        )

    def refresh_table(self, image_items):
        self.table.setRowCount(len(image_items))

        for row_index, item in enumerate(image_items):
            values = [
                item.get("field_no", ""),
                self._short_path(item.get("R", "")),
                self._short_path(item.get("G", "")),
                self._short_path(item.get("DIC", "")),
                self._short_path(item.get("Merge", "")),
                item.get("status", ""),
            ]

            for col_index, value in enumerate(values):
                table_item = QTableWidgetItem(str(value))
                table_item.setTextAlignment(Qt.AlignCenter)

                if col_index == 5:
                    if value == "完整":
                        table_item.setForeground(Qt.darkGreen)
                    else:
                        table_item.setForeground(Qt.red)

                self.table.setItem(row_index, col_index, table_item)

    def run_cellprofiler(self):
        if not self.current_case:
            QMessageBox.information(self, "提示", "请先选择病例。")
            return

        if not self.imported_images:
            QMessageBox.information(self, "提示", "请先导入图片。")
            return

        complete_items = [
            item for item in self.imported_images
            if item.get("status") == "完整"
        ]

        if not complete_items:
            QMessageBox.warning(self, "提示", "没有完整的 R/G 视野，无法运行分析。")
            return

        protein_name = self.protein_combo.currentText()
        case_no = str(self.current_case.get("case_no", "")).strip()

        source_project_dir = self.config.get_source_project_dir().resolve()
        venv_activate = self.config.get_venv_activate().resolve()
        module_name = self.config.get_module_name()
        pipeline_file = self.config.get_pipeline_by_protein(protein_name).resolve()
        plugins_directory = self.config.get_plugins_directory().resolve()
        log_file = self.config.get_log_file().resolve()
        powershell_exe = self.config.get_powershell_exe()

        if not source_project_dir.exists():
            QMessageBox.critical(
                self,
                "错误",
                f"MvImageID 源码目录不存在：\n{source_project_dir}\n\n请检查 config.ini 中的 source_project_dir。"
            )
            return

        if not venv_activate.exists():
            QMessageBox.critical(
                self,
                "错误",
                f"虚拟环境激活脚本不存在：\n{venv_activate}\n\n请检查 config.ini 中的 venv_activate。"
            )
            return

        if not pipeline_file.exists():
            QMessageBox.critical(
                self,
                "错误",
                f"Pipeline 文件不存在：\n{pipeline_file}\n\n请检查 config.ini 中的 pipeline 路径。"
            )
            return

        if not plugins_directory.exists():
            QMessageBox.critical(
                self,
                "错误",
                f"插件目录不存在：\n{plugins_directory}\n\n请检查 config.ini 中的 plugins_directory。"
            )
            return

        workspace_root = self.config.get_workspace_root()
        cp_input_dir = workspace_root / case_no / "cp_input" / protein_name
        cp_output_dir = workspace_root / case_no / "cp_output" / protein_name

        cp_input_dir = cp_input_dir.resolve()
        cp_output_dir = cp_output_dir.resolve()

        try:
            self.prepare_cp_input(complete_items, cp_input_dir)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"准备输入目录失败：\n{e}")
            return

        cp_output_dir.mkdir(parents=True, exist_ok=True)
        self.current_cp_output_dir = cp_output_dir

        self.append_log("准备以源码环境方式运行分析。")
        self.append_log(f"源码目录：{source_project_dir}")
        self.append_log(f"虚拟环境：{venv_activate}")
        self.append_log(f"模块名称：{module_name}")
        self.append_log(f"Pipeline：{pipeline_file}")
        self.append_log(f"插件目录：{plugins_directory}")
        self.append_log(f"输入目录：{cp_input_dir}")
        self.append_log(f"输出目录：{cp_output_dir}")
        self.append_log(f"日志文件：{log_file}")

        self.set_running_state(True)

        self.cp_worker = CellProfilerWorker(
            powershell_exe=powershell_exe,
            source_project_dir=str(source_project_dir),
            venv_activate=str(venv_activate),
            module_name=module_name,
            pipeline_file=str(pipeline_file),
            input_dir=str(cp_input_dir),
            output_dir=str(cp_output_dir),
            plugins_directory=str(plugins_directory),
            log_file=str(log_file),
        )

        self.cp_worker.log_signal.connect(self.append_log)
        self.cp_worker.finished_signal.connect(self.on_cellprofiler_finished)
        self.cp_worker.start()

    def prepare_cp_input(self, complete_items, cp_input_dir: Path):
        if cp_input_dir.exists():
            shutil.rmtree(cp_input_dir)

        cp_input_dir.mkdir(parents=True, exist_ok=True)

        copied_count = 0

        for item in complete_items:
            for channel in ["R", "G"]:
                source_path = item.get(channel, "")

                if not source_path:
                    continue

                source = Path(source_path)

                if not source.exists():
                    raise FileNotFoundError(f"图像文件不存在：{source}")

                target = cp_input_dir / source.name

                shutil.copy2(source, target)
                copied_count += 1

        if copied_count == 0:
            raise RuntimeError("没有复制任何 R/G 图像到输入目录。")

        self.append_log(f"已生成输入目录：{cp_input_dir}")
        self.append_log(f"已复制 R/G 图像数量：{copied_count}")

    def on_cellprofiler_finished(self, success: bool, elapsed: float, log_text: str):
        self.set_running_state(False)

        if self.current_cp_output_dir:
            self.result_viewer.set_output_dir(str(self.current_cp_output_dir))
            self.result_viewer.refresh_results()

        if success:
            saved_ok, save_message = self.save_analysis_result_to_database()

            if saved_ok:
                self.append_log(save_message)
            else:
                self.append_log(f"结果入库失败：{save_message}")

            QMessageBox.information(
                self,
                "分析完成",
                f"分析完成。\n用时：{elapsed:.2f} 秒\n\n输出目录：\n{self.current_cp_output_dir}\n\n{save_message}"
            )
            self.append_log(f"输出目录：{self.current_cp_output_dir}")
        else:
            QMessageBox.critical(
                self,
                "分析失败",
                f"分析失败。\n用时：{elapsed:.2f} 秒\n\n请查看日志。"
            )

    def save_analysis_result_to_database(self):
        if not self.current_case:
            return False, "当前病例为空。"

        if not self.current_cp_output_dir:
            return False, "当前输出目录为空。"

        case_id = self.current_case.get("id")
        if not case_id:
            return False, "当前病例缺少数据库 ID。"

        protein_name = self.protein_combo.currentText()
        protein_part = self.config.get_protein_part(protein_name)

        parser = ResultParser(str(self.current_cp_output_dir))
        summary_result = parser.parse_image_summary()

        if not summary_result.get("success"):
            return False, summary_result.get("message", "解析结果失败。")

        total = summary_result.get("total", {})
        rows = summary_result.get("rows", [])
        image_csv = summary_result.get("image_csv", "")

        image_folder = str(self.current_raw_image_folder or "")
        output_folder = str(self.current_cp_output_dir)

        try:
            analysis_id = self.database.save_protein_analysis(
                case_id=case_id,
                protein_name=protein_name,
                protein_part=protein_part,
                image_folder=image_folder,
                output_folder=output_folder,
                total_fields=total.get("field_count", 0),
                total_sperm_count=total.get("sperm_count", 0),
                positive_count=total.get("positive_count", 0),
                mean_intensity=total.get("mean_intensity", 0),
                expression_rate=total.get("expression_rate", 0),
                status="完成",
            )

            for item in rows:
                field_no = str(item.get("image_number", ""))

                self.database.save_field_result(
                    analysis_id=analysis_id,
                    field_no=field_no,
                    sperm_count=item.get("sperm_count", 0),
                    positive_count=item.get("positive_count", 0),
                    mean_intensity=item.get("mean_intensity", 0),
                    expression_rate=item.get("expression_rate", 0),
                    overlay_image_path="",
                    csv_path=image_csv,
                )

        except Exception as e:
            return False, f"保存数据库失败：{e}"

        return True, (
            "结果已保存到数据库："
            f"视野数 {total.get('field_count', 0)}，"
            f"精子总数 {total.get('sperm_count', 0)}，"
            f"阳性/共定位数 {total.get('positive_count', 0)}，"
            f"标定率 {total.get('expression_rate', 0)}%，"
            f"荧光强度 {total.get('mean_intensity', 0)}。"
        )

    def set_running_state(self, running: bool):
        self.btn_select_folder.setEnabled(not running)
        self.btn_import.setEnabled(not running)
        self.btn_run_cp.setEnabled(not running)
        self.protein_combo.setEnabled(not running)

        if running:
            self.btn_run_cp.setText("正在分析...")
        else:
            self.btn_run_cp.setText("运行分析")

    def append_log(self, message: str):
        self.log_edit.append(str(message))

    def _short_path(self, path_text: str):
        if not path_text:
            return ""

        path = Path(path_text)
        return path.name