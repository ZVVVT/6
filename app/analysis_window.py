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

from core.config_manager import ConfigManager
from core.image_importer import ImageImporter


class AnalysisWindow(QWidget):
    def __init__(self, database, parent=None):
        super().__init__(parent)

        self.database = database
        self.config = ConfigManager()
        self.current_case = None
        self.imported_images = []

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

        self.protein_part_label = QLabel("表达部位：head")
        self.protein_combo.currentTextChanged.connect(self.on_protein_changed)

        row1_layout.addWidget(QLabel("蛋白名称："))
        row1_layout.addWidget(self.protein_combo)
        row1_layout.addWidget(self.protein_part_label)
        row1_layout.addStretch()

        operation_layout.addLayout(row1_layout)

        row2_layout = QHBoxLayout()

        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("请选择包含 R / G / DIC / Merge 图片的文件夹")

        self.btn_select_folder = QPushButton("选择文件夹")
        self.btn_import = QPushButton("导入图片")
        self.btn_prepare_cp = QPushButton("下一步：准备 CellProfiler 分析")
        self.btn_prepare_cp.setEnabled(False)

        row2_layout.addWidget(QLabel("图片文件夹："))
        row2_layout.addWidget(self.folder_edit, 1)
        row2_layout.addWidget(self.btn_select_folder)
        row2_layout.addWidget(self.btn_import)
        row2_layout.addWidget(self.btn_prepare_cp)

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
        self.log_edit.setMaximumHeight(120)
        self.log_edit.setPlaceholderText("运行日志")
        main_layout.addWidget(self.log_edit)

        self.btn_select_folder.clicked.connect(self.select_folder)
        self.btn_import.clicked.connect(self.import_images)
        self.btn_prepare_cp.clicked.connect(self.prepare_cellprofiler)

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
        self.protein_part_label.setText(f"表达部位：{protein_part}")

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

        self.btn_prepare_cp.setEnabled(total_count > 0)

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

    def prepare_cellprofiler(self):
        if not self.imported_images:
            QMessageBox.information(self, "提示", "请先导入图片。")
            return

        protein_name = self.protein_combo.currentText()
        complete_items = [item for item in self.imported_images if item["status"] == "完整"]

        if not complete_items:
            QMessageBox.warning(
                self,
                "提示",
                "没有完整的 R/G 视野，暂不能进入 CellProfiler 分析。"
            )
            return

        self.append_log(
            f"下一步将为蛋白 {protein_name} 准备 CellProfiler 输入目录。"
        )

        QMessageBox.information(
            self,
            "下一步",
            "图片导入流程已完成。\n\n下一阶段将开发：\n1. 生成 cp_input 目录\n2. 匹配 pipeline\n3. 后台调用 CellProfiler\n4. 读取 CSV 和 Overlay 结果"
        )

    def append_log(self, message: str):
        self.log_edit.append(message)

    def _short_path(self, path_text: str):
        if not path_text:
            return ""

        path = Path(path_text)
        return path.name