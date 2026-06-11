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
        self.config.ensure_default_config()

        self.current_case = None
        self.imported_images = []
        self.cp_worker = None
        self.current_cp_output_dir = None
        self.current_raw_image_folder = None
        self._suspend_protein_changed = False

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 10, 14, 10)
        main_layout.setSpacing(8)

        # -------------------------
        # 顶部：标题 + 病例摘要，统一病例详情页风格
        # -------------------------
        header_layout = QHBoxLayout()

        title_label = QLabel("蛋白分析")
        title_label.setStyleSheet("font-size: 22px; font-weight: bold; color: #1f4e79;")

        self.case_summary_label = QLabel("当前病例：未选择")
        self.case_summary_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.case_summary_label.setStyleSheet("color: #666666;")

        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.case_summary_label)

        main_layout.addLayout(header_layout)

        # 保留这些 QLabel，兼容原有 set_case / 其他逻辑
        self.case_no_label = QLabel("未选择")
        self.patient_name_label = QLabel("-")
        self.sample_no_label = QLabel("-")
        self.test_date_label = QLabel("-")

        # -------------------------
        # 分析控制卡片
        # -------------------------
        operation_group = QGroupBox("分析控制")
        operation_layout = QVBoxLayout(operation_group)
        operation_layout.setContentsMargins(10, 10, 10, 8)
        operation_layout.setSpacing(6)

        row1_layout = QHBoxLayout()

        self.protein_combo = QComboBox()
        self.load_protein_combo()

        self.protein_part_label = QLabel("表达部位：-")
        self.pipeline_label = QLabel("Pipeline：-")
        self.protein_status_label = QLabel("已分析：-")
        self.protein_status_label.setStyleSheet("color: #666666;")

        self.btn_next_protein = QPushButton("下一个未分析")

        self.protein_combo.currentIndexChanged.connect(self.on_protein_changed)
        self.btn_next_protein.clicked.connect(self.select_next_unanalyzed_protein)

        row1_layout.addWidget(QLabel("蛋白名称："))
        row1_layout.addWidget(self.protein_combo)
        row1_layout.addWidget(self.protein_part_label)
        row1_layout.addWidget(self.pipeline_label, 1)
        row1_layout.addWidget(self.btn_next_protein)

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

        row3_layout = QHBoxLayout()

        self.import_status_label = QLabel("图片状态：当前蛋白暂无导入图片")
        self.import_status_label.setStyleSheet("color: #555555;")

        self.last_log_label = QLabel("最近状态：-")
        self.last_log_label.setStyleSheet("color: #666666;")

        row3_layout.addWidget(self.protein_status_label)
        row3_layout.addSpacing(18)
        row3_layout.addWidget(self.import_status_label)
        row3_layout.addSpacing(18)
        row3_layout.addWidget(self.last_log_label, 1)

        operation_layout.addLayout(row3_layout)

        main_layout.addWidget(operation_group)

        # -------------------------
        # 图像核查工作区：页面主体
        # -------------------------
        self.result_viewer = ResultViewer()
        self.result_viewer.setMinimumHeight(560)
        main_layout.addWidget(self.result_viewer, 10)

        # -------------------------
        # 底部：导入图片列表，常驻但限制高度
        # -------------------------
        import_group = QGroupBox("导入图片列表")
        import_layout = QVBoxLayout(import_group)
        import_layout.setContentsMargins(8, 8, 8, 8)
        import_layout.setSpacing(4)

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
        self.table.setMaximumHeight(92)

        table_header = self.table.horizontalHeader()
        table_header.setSectionResizeMode(QHeaderView.Stretch)
        table_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table_header.setSectionResizeMode(5, QHeaderView.ResizeToContents)

        import_layout.addWidget(self.table)
        main_layout.addWidget(import_group)

        # -------------------------
        # 底部：运行日志，常驻但限制高度
        # -------------------------
        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_layout.setSpacing(4)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumHeight(78)
        self.log_edit.setPlaceholderText("运行日志")

        log_layout.addWidget(self.log_edit)
        main_layout.addWidget(log_group)

        self.set_common_style()

        self.btn_select_folder.clicked.connect(self.select_folder)
        self.btn_import.clicked.connect(self.import_images)
        self.btn_run_cp.clicked.connect(self.run_cellprofiler)

        self.on_protein_changed()

    def set_common_style(self):
        self.setStyleSheet("""
            QWidget {
                font-family: Microsoft YaHei;
                font-size: 13px;
                background-color: #f5f7fb;
            }
            QGroupBox {
                border: 1px solid #d9e2ef;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 12px;
                background-color: #ffffff;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: #1f4e79;
            }
            QLabel {
                background-color: transparent;
            }
            QLineEdit, QComboBox, QTextEdit, QTableWidget {
                background-color: #ffffff;
                border: 1px solid #cfd8e6;
                border-radius: 3px;
            }
            QPushButton {
                min-height: 28px;
                min-width: 82px;
                background-color: #ffffff;
                border: 1px solid #b8c7da;
                border-radius: 4px;
                padding: 2px 8px;
            }
            QPushButton:hover {
                background-color: #eef5ff;
                border-color: #7aa7d9;
            }
            QPushButton:pressed {
                background-color: #dfeeff;
            }
            QPushButton:disabled {
                color: #999999;
                background-color: #f0f0f0;
                border-color: #dddddd;
            }
            QHeaderView::section {
                background-color: #eef3f9;
                border: 1px solid #d9e2ef;
                padding: 3px;
                font-weight: bold;
            }
            QTableWidget {
                alternate-background-color: #f7f9fc;
                gridline-color: #d9e2ef;
                selection-background-color: #d8eaff;
            }
        """)

    def update_case_summary_label(self):
        if not self.current_case:
            self.case_summary_label.setText("当前病例：未选择")
            return

        case_no = str(self.current_case.get("case_no", "") or "")
        patient_name = str(self.current_case.get("patient_name", "") or "")
        sample_no = str(self.current_case.get("sample_no", "") or "")
        test_date = str(self.current_case.get("test_date", "") or "")

        self.case_summary_label.setText(
            f"当前病例：{case_no}    姓名：{patient_name}    样本号：{sample_no}    检测日期：{test_date}"
        )

    def update_import_status_label(self, image_items=None):
        if image_items is None:
            image_items = self.imported_images

        total_count = len(image_items)
        complete_count = self.get_complete_image_count(image_items)

        if total_count <= 0:
            self.import_status_label.setText("图片状态：当前蛋白暂无导入图片")
        else:
            self.import_status_label.setText(
                f"图片状态：已导入 {total_count} 个视野，完整视野 {complete_count} 个"
            )

    def load_protein_combo(self):
        self.protein_combo.clear()

        protein_items = self.config.get_protein_items()

        for item in protein_items:
            key = item.get("key", "")
            name = item.get("name", key)
            part = item.get("part", "")

            if part:
                text = f"{name}（{part}）"
            else:
                text = name

            self.protein_combo.addItem(text, key)

    def get_current_protein_key(self):
        key = self.protein_combo.currentData()

        if key:
            return str(key)

        return self.config.normalize_protein_key(self.protein_combo.currentText())

    def get_current_protein_name(self):
        key = self.get_current_protein_key()
        return self.config.get_protein_display_name(key)

    def on_protein_changed(self, *args):
        if self._suspend_protein_changed:
            return

        protein_key = self.get_current_protein_key()

        if not protein_key:
            self.clear_current_protein_display("当前没有可用蛋白配置。")
            return

        protein_name = self.config.get_protein_display_name(protein_key)
        protein_part = self.config.get_protein_part(protein_key) or "未配置"
        pipeline_path = self.config.get_pipeline_by_protein(protein_key)

        self.protein_part_label.setText(f"表达部位：{protein_part}")
        self.pipeline_label.setText(f"Pipeline：{pipeline_path}")

        self.append_log(f"当前选择蛋白：{protein_name}，内部编号：{protein_key}")

        self.refresh_current_protein_workspace()

    def get_analyzed_protein_name_set(self):
        if not self.current_case:
            return set()

        case_id = self.current_case.get("id")

        if not case_id:
            return set()

        try:
            rows = self.database.get_protein_analysis_by_case(case_id)
        except Exception:
            return set()

        name_set = set()

        for row in rows:
            name = str(row.get("protein_name", "") or "").strip()
            if name:
                name_set.add(name)

        return name_set


    def get_existing_analysis_result_for_current_protein(self):
        if not self.current_case:
            return None

        case_id = self.current_case.get("id")

        if not case_id:
            return None

        protein_key = self.get_current_protein_key()
        protein_name = self.get_current_protein_name()

        try:
            rows = self.database.get_protein_analysis_by_case(case_id)
        except Exception:
            return None

        for row in rows:
            row_name = str(row.get("protein_name", "") or "").strip()

            if not row_name:
                continue

            row_key = self.config.normalize_protein_key(row_name)

            if row_name == protein_name or row_key == protein_key:
                return row

        return None

    def refresh_protein_status(self):
        if not self.current_case:
            self.protein_status_label.setText("已分析：-")
            return

        analyzed_names = self.get_analyzed_protein_name_set()

        status_items = []

        for item in self.config.get_protein_items():
            key = item.get("key", "")
            name = item.get("name", key)

            if name in analyzed_names or key in analyzed_names:
                status_items.append(f"{name} √")
            else:
                status_items.append(f"{name} -")

        self.protein_status_label.setText("已分析：" + "　".join(status_items))

    def select_next_unanalyzed_protein(self):
        if self.protein_combo.count() <= 0:
            return

        analyzed_names = self.get_analyzed_protein_name_set()

        current_index = self.protein_combo.currentIndex()
        total = self.protein_combo.count()

        for offset in range(1, total + 1):
            index = (current_index + offset) % total
            key = str(self.protein_combo.itemData(index))
            name = self.config.get_protein_display_name(key)

            if name not in analyzed_names and key not in analyzed_names:
                self.protein_combo.setCurrentIndex(index)
                self.append_log(f"已切换到下一个未分析蛋白：{name}")
                return

        QMessageBox.information(self, "提示", "当前病例所有配置蛋白均已有分析结果。")

    # -------------------------
    # 病例
    # -------------------------

    def reload_config(self):
        current_key = self.get_current_protein_key()

        self.config.load()
        self.config.ensure_default_config()

        self._suspend_protein_changed = True
        try:
            self.load_protein_combo()

            if current_key:
                for index in range(self.protein_combo.count()):
                    if self.protein_combo.itemData(index) == current_key:
                        self.protein_combo.setCurrentIndex(index)
                        break
        finally:
            self._suspend_protein_changed = False

        self.on_protein_changed()
        self.refresh_protein_status()

        self.append_log("系统配置已刷新。")

    def set_case(self, case_data: dict):
        self.current_case = case_data

        self.case_no_label.setText(str(case_data.get("case_no", "")))
        self.patient_name_label.setText(str(case_data.get("patient_name", "")))
        self.sample_no_label.setText(str(case_data.get("sample_no", "")))
        self.test_date_label.setText(str(case_data.get("test_date", "")))

        self.update_case_summary_label()
        self.refresh_protein_status()
        self.on_protein_changed()

        self.append_log(
            f"已载入病例：{case_data.get('case_no', '')} - {case_data.get('patient_name', '')}"
        )

    def get_current_case_no(self):
        if not self.current_case:
            return ""

        return str(self.current_case.get("case_no", "") or "").strip()

    # -------------------------
    # 当前蛋白工作区刷新
    # -------------------------

    def clear_current_protein_display(self, message: str = ""):
        self.imported_images = []
        self.current_raw_image_folder = None
        self.current_cp_output_dir = None

        self.refresh_table([])
        self.btn_run_cp.setEnabled(False)

        if hasattr(self.result_viewer, "clear_results"):
            self.result_viewer.clear_results(message or "当前蛋白暂无分析结果。")

    def refresh_current_protein_workspace(self):
        protein_key = self.get_current_protein_key()
        protein_name = self.get_current_protein_name()

        if not self.current_case:
            self.clear_current_protein_display("请先在病例管理中选择病例。")
            return

        case_no = self.get_current_case_no()

        if not case_no:
            self.clear_current_protein_display("当前病例编号为空。")
            return

        workspace_root = self.config.get_workspace_root()

        raw_folder = workspace_root / case_no / "raw_images" / protein_key
        output_folder = workspace_root / case_no / "cp_output" / protein_key

        self.current_raw_image_folder = raw_folder
        self.current_cp_output_dir = output_folder

        if raw_folder.exists():
            self.imported_images = self.load_images_from_raw_folder(raw_folder, protein_key)
            self.refresh_table(self.imported_images)

            complete_count = self.get_complete_image_count(self.imported_images)
            total_count = len(self.imported_images)

            if total_count > 0:
                self.append_log(
                    f"{protein_name} 已加载历史导入图片：共 {total_count} 个视野，完整视野 {complete_count} 个。"
                )
            else:
                self.append_log(f"{protein_name} 暂无导入图片。")

            self.btn_run_cp.setEnabled(complete_count > 0)
        else:
            self.imported_images = []
            self.refresh_table([])
            self.btn_run_cp.setEnabled(False)
            self.append_log(f"{protein_name} 暂无导入图片。")

        if output_folder.exists() and self.folder_has_files(output_folder):
            self.result_viewer.set_output_dir(str(output_folder))
            self.result_viewer.refresh_results()
            self.append_log(f"{protein_name} 已加载历史分析结果：{output_folder}")
        else:
            if hasattr(self.result_viewer, "clear_results"):
                self.result_viewer.clear_results(f"{protein_name} 暂无分析结果。")
            self.append_log(f"{protein_name} 暂无分析结果。")

    def load_images_from_raw_folder(self, raw_folder: Path, protein_key: str):
        support_exts = {
            ".tif",
            ".tiff",
            ".png",
            ".jpg",
            ".jpeg",
            ".bmp",
        }

        groups = {}

        for image_path in raw_folder.iterdir():
            if not image_path.is_file():
                continue

            if image_path.suffix.lower() not in support_exts:
                continue

            channel_info = self.parse_workspace_image_channel(image_path, protein_key)

            if channel_info is None:
                continue

            field_no, channel = channel_info

            if field_no not in groups:
                groups[field_no] = {
                    "field_no": field_no,
                    "R": "",
                    "G": "",
                    "DIC": "",
                    "Merge": "",
                    "status": "未完整",
                }

            groups[field_no][channel] = str(image_path)

        results = list(groups.values())

        for item in results:
            required_ok = bool(item["R"]) and bool(item["G"])
            item["status"] = "完整" if required_ok else "缺少R或G"

        results.sort(key=lambda x: str(x.get("field_no", "")))

        return results

    def parse_workspace_image_channel(self, image_path: Path, protein_key: str):
        stem = image_path.stem

        suffix_map = {
            "_R": "R",
            "_G": "G",
            "_DIC": "DIC",
            "_Merge": "Merge",
        }

        for suffix, channel in suffix_map.items():
            if not stem.endswith(suffix):
                continue

            base = stem[:-len(suffix)]

            prefix = f"{protein_key}_"
            if base.startswith(prefix):
                field_no = base[len(prefix):]
            else:
                field_no = base

            field_no = field_no.strip("_- ")

            if not field_no:
                field_no = base

            return field_no, channel

        return None

    @staticmethod
    def folder_has_files(folder: Path):
        if not folder.exists() or not folder.is_dir():
            return False

        for item in folder.iterdir():
            if item.is_file():
                return True

        return False

    @staticmethod
    def get_complete_image_count(image_items):
        return sum(1 for item in image_items if item.get("status") == "完整")

    # -------------------------
    # 图片导入
    # -------------------------

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

        protein_key = self.get_current_protein_key()
        protein_name = self.get_current_protein_name()
        case_no = self.get_current_case_no()

        if not case_no:
            QMessageBox.warning(self, "提示", "当前病例编号为空，无法建立工作目录。")
            return

        workspace_root = self.config.get_workspace_root()
        target_folder = workspace_root / case_no / "raw_images" / protein_key

        if target_folder.exists() and self.folder_has_files(target_folder):
            reply = QMessageBox.question(
                self,
                "确认重新导入",
                f"{protein_name} 已经有导入图片。\n\n"
                "继续导入会清空当前蛋白的旧导入图片，并复制新的图片。\n"
                "不会删除已经生成的分析结果。\n\n"
                "是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if reply != QMessageBox.Yes:
                return

            try:
                shutil.rmtree(target_folder)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"清理旧导入图片失败：\n{e}")
                return

        self.current_raw_image_folder = target_folder

        try:
            importer = ImageImporter(self.config.get_image_rule())
            self.imported_images = importer.copy_to_workspace(
                source_folder=source_folder,
                target_folder=str(target_folder),
                protein_name=protein_key,
            )
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导入图片失败：\n{e}")
            return

        self.refresh_table(self.imported_images)

        complete_count = self.get_complete_image_count(self.imported_images)
        total_count = len(self.imported_images)

        self.append_log(
            f"{protein_name} 图片导入完成：共识别 {total_count} 个视野，完整视野 {complete_count} 个。"
        )
        self.append_log(f"图片已复制到：{target_folder}")

        self.btn_run_cp.setEnabled(complete_count > 0)

        QMessageBox.information(
            self,
            "导入完成",
            f"蛋白：{protein_name}\n"
            f"共识别 {total_count} 个视野。\n"
            f"完整视野：{complete_count} 个。\n\n"
            f"已复制到：\n{target_folder}"
        )

    def refresh_table(self, image_items):
        self.table.setRowCount(len(image_items))
        self.update_import_status_label(image_items)

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
            QMessageBox.information(self, "提示", "请先导入当前蛋白的图片。")
            return

        complete_items = [
            item for item in self.imported_images
            if item.get("status") == "完整"
        ]

        if not complete_items:
            QMessageBox.warning(self, "提示", "没有完整的 R/G 视野，无法运行分析。")
            return

        protein_key = self.get_current_protein_key()
        protein_name = self.get_current_protein_name()
        case_no = self.get_current_case_no()

        if not self.imported_images_match_current_protein(complete_items, protein_key):
            reply = QMessageBox.question(
                self,
                "图片来源提示",
                f"当前图片文件名不完全符合 {protein_name} / {protein_key} 的导入命名。\n\n"
                "如果你确认要复用这组图片进行当前蛋白分析，可以继续。\n\n"
                "是否继续运行分析？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )

            if reply != QMessageBox.Yes:
                return
        
        existing_result = self.get_existing_analysis_result_for_current_protein()

        if existing_result:
            created_at = existing_result.get("created_at", "")
            total_fields = existing_result.get("total_fields", "")
            total_sperm_count = existing_result.get("total_sperm_count", "")
            positive_count = existing_result.get("positive_count", "")
            expression_rate = existing_result.get("expression_rate", "")

            reply = QMessageBox.question(
                self,
                "确认重新分析",
                f"{protein_name} 当前已经有分析结果。\n\n"
                f"分析时间：{created_at}\n"
                f"视野数：{total_fields}\n"
                f"精子总数：{total_sperm_count}\n"
                f"阳性/共定位数：{positive_count}\n"
                f"标定率：{expression_rate}%\n\n"
                "继续运行会用新的分析结果替换该蛋白旧结果。\n"
                "不会影响当前病例下其他蛋白的结果。\n\n"
                "是否继续重新分析？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if reply != QMessageBox.Yes:
                self.append_log(f"用户取消重新分析：{protein_name}")
                return
        

        source_project_dir = self.config.get_source_project_dir().resolve()
        venv_activate = self.config.get_venv_activate().resolve()
        module_name = self.config.get_module_name()
        pipeline_file = self.config.get_pipeline_by_protein(protein_key).resolve()
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
        cp_input_dir = workspace_root / case_no / "cp_input" / protein_key
        cp_output_dir = workspace_root / case_no / "cp_output" / protein_key

        cp_input_dir = cp_input_dir.resolve()
        cp_output_dir = cp_output_dir.resolve()

        try:
            self.prepare_cp_input(complete_items, cp_input_dir)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"准备输入目录失败：\n{e}")
            return

        cp_output_dir.mkdir(parents=True, exist_ok=True)
        self.current_cp_output_dir = cp_output_dir

        self.append_log(f"准备以源码环境方式运行分析：{protein_name}")
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

    def imported_images_match_current_protein(self, image_items, protein_key: str):
        prefix = f"{protein_key}_"

        checked_count = 0

        for item in image_items:
            for channel in ["R", "G"]:
                path_text = item.get(channel, "")

                if not path_text:
                    continue

                checked_count += 1
                file_name = Path(path_text).name

                if not file_name.startswith(prefix):
                    return False

        return checked_count > 0

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

            self.refresh_protein_status()

            QMessageBox.information(
                self,
                "分析完成",
                f"分析完成。\n用时：{elapsed:.2f} 秒\n\n"
                f"输出目录：\n{self.current_cp_output_dir}\n\n{save_message}"
            )

            self.append_log(f"输出目录：{self.current_cp_output_dir}")

            self.select_next_unanalyzed_protein()

        else:
            QMessageBox.critical(
                self,
                "分析失败",
                f"分析失败。\n用时：{elapsed:.2f} 秒\n\n请查看日志。"
            )

    # -------------------------
    # 入库
    # -------------------------

    def save_analysis_result_to_database(self):
        if not self.current_case:
            return False, "当前病例为空。"

        if not self.current_cp_output_dir:
            return False, "当前输出目录为空。"

        case_id = self.current_case.get("id")

        if not case_id:
            return False, "当前病例缺少数据库 ID。"

        protein_key = self.get_current_protein_key()
        protein_name = self.get_current_protein_name()
        protein_part = self.config.get_protein_part(protein_key)

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
            f"{protein_name} 结果已保存到数据库："
            f"视野数 {total.get('field_count', 0)}，"
            f"精子总数 {total.get('sperm_count', 0)}，"
            f"阳性/共定位数 {total.get('positive_count', 0)}，"
            f"标定率 {total.get('expression_rate', 0)}%，"
            f"荧光强度 {total.get('mean_intensity', 0)}。"
        )

    # -------------------------
    # 通用
    # -------------------------

    def set_running_state(self, running: bool):
        self.btn_select_folder.setEnabled(not running)
        self.btn_import.setEnabled(not running)
        self.btn_next_protein.setEnabled(not running)
        self.protein_combo.setEnabled(not running)

        if running:
            self.btn_run_cp.setEnabled(False)
            self.btn_run_cp.setText("正在分析...")
        else:
            self.btn_run_cp.setText("运行分析")
            self.btn_run_cp.setEnabled(self.get_complete_image_count(self.imported_images) > 0)

    def append_log(self, message: str):
        message = str(message)
        self.log_edit.append(message)

        if hasattr(self, "last_log_label"):
            short_message = message.replace("\n", " ").strip()

            if len(short_message) > 120:
                short_message = short_message[:120] + "..."

            self.last_log_label.setText(f"最近状态：{short_message}")

    def _short_path(self, path_text: str):
        if not path_text:
            return ""

        path = Path(path_text)
        return path.name