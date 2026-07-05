import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
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
    QSplitter,
)

from app.result_viewer import ResultViewer
from core.config_manager import ConfigManager
from core.image_channel_matcher import ImageChannelMatcher
from core.result_parser import ResultParser
from core.protein_analysis_service import ProteinAnalysisService


class SingleProteinAnalysisWorker(QThread):
    """单蛋白分析后台线程。

    第四步开始，蛋白分析页不再自己维护 MvImageID 执行细节，
    而是调用 core.protein_analysis_service.ProteinAnalysisService。
    """

    log_signal = Signal(str)
    finished_signal = Signal(bool, float, object, str)

    def __init__(self, case_data: dict, protein_key: str, protein_name: str, config: ConfigManager, parent=None):
        super().__init__(parent)
        self.case_data = case_data
        self.protein_key = protein_key
        self.protein_name = protein_name
        self.config = config

    def run(self):
        try:
            service = ProteinAnalysisService(self.config)
            result = service.run_one_protein(
                case_data=self.case_data,
                protein_key=self.protein_key,
                protein_name=self.protein_name,
                source_folder="",
                overwrite=True,
                reuse_existing_raw=True,
                log_callback=self.log_signal.emit,
                cancel_callback=None,
            )
            elapsed = float(result.get("runner_elapsed_seconds", 0) or 0)
            self.finished_signal.emit(True, elapsed, result, "")
        except Exception as e:
            self.finished_signal.emit(False, 0.0, {}, str(e))


class AnalysisWindow(QWidget):
    def __init__(self, database, parent=None):
        super().__init__(parent)

        self.database = database
        self.config = ConfigManager()
        self.config.ensure_default_config()

        self.current_case = None
        self.imported_images = []
        self.analysis_worker = None
        self.current_output_dir = None
        self.current_raw_image_folder = None
        self._suspend_protein_changed = False
        self.current_protein_key = None
        self.protein_buttons = {}

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
        row1_layout.setSpacing(8)

        self.protein_buttons_layout = QHBoxLayout()
        self.protein_buttons_layout.setSpacing(6)

        self.protein_part_label = QLabel("表达部位：-")
        self.pipeline_label = QLabel("Pipeline：-")
        self.protein_status_label = QLabel("已分析：-")
        self.protein_status_label.setStyleSheet("color: #666666;")

        self.btn_next_protein = QPushButton("下一个未分析")
        self.btn_next_protein.clicked.connect(self.select_next_unanalyzed_protein)

        row1_layout.addWidget(QLabel("检测项目："))
        row1_layout.addLayout(self.protein_buttons_layout)
        row1_layout.addSpacing(10)
        row1_layout.addWidget(self.protein_part_label)
        row1_layout.addWidget(self.pipeline_label, 1)
        row1_layout.addWidget(self.btn_next_protein)

        operation_layout.addLayout(row1_layout)

        self.load_protein_combo()

        row2_layout = QHBoxLayout()

        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("请选择包含 R / G / DIC / Merge 图片的文件夹")

        self.btn_select_folder = QPushButton("选择文件夹")
        self.btn_select_folder.setObjectName("PrimarySelectFolderButton")
        self.btn_import = QPushButton("导入图片")
        self.btn_run_analysis = QPushButton("运行分析")
        self.btn_run_analysis.setObjectName("PrimaryRunAnalysisButton")
        self.btn_run_analysis.setEnabled(False)

        row2_layout.addWidget(QLabel("图片文件夹："))
        row2_layout.addWidget(self.folder_edit, 1)
        row2_layout.addWidget(self.btn_select_folder)
        row2_layout.addWidget(self.btn_import)
        row2_layout.addWidget(self.btn_run_analysis)

        operation_layout.addLayout(row2_layout)

        # 下面三个状态信息已经分别在蛋白项目按钮、底部导入图片列表、底部运行日志中显示，
        # 这里保留 QLabel 对象给原有逻辑更新使用，但不再放到界面里，避免信息重复。
        self.import_status_label = QLabel("图片状态：当前蛋白暂无导入图片")
        self.import_status_label.setVisible(False)

        self.last_log_label = QLabel("最近状态：-")
        self.last_log_label.setVisible(False)

        self.protein_status_label.setVisible(False)

        main_layout.addWidget(operation_group)

        # -------------------------
        # 图像核查工作区：页面主体
        # -------------------------
        self.result_viewer = ResultViewer()
        self.result_viewer.setMinimumHeight(600)
        self.tune_result_viewer_side_panel_width()
        main_layout.addWidget(self.result_viewer, 10)

        # -------------------------
        # 底部：导入图片列表，默认折叠
        # 折叠时只保留一行标题，尽量释放图像核查区域空间
        # -------------------------
        self.import_group = QWidget()
        self.import_group.setObjectName("collapsePanel")
        import_layout = QVBoxLayout(self.import_group)
        import_layout.setContentsMargins(8, 4, 8, 4)
        import_layout.setSpacing(3)

        import_header_layout = QHBoxLayout()
        import_header_layout.setContentsMargins(0, 0, 0, 0)
        import_header_layout.setSpacing(6)

        self.import_panel_title_label = QLabel("导入图片列表")
        self.import_panel_title_label.setObjectName("collapsePanelTitle")
        self.import_panel_summary_label = QLabel("已导入 0 个视野，完整视野 0 个")
        self.import_panel_summary_label.setObjectName("collapsePanelSummary")
        self.btn_toggle_import_panel = QPushButton("+")
        self.btn_toggle_import_panel.setObjectName("collapseToggleButton")
        self.btn_toggle_import_panel.setToolTip("展开导入图片列表")
        self.btn_toggle_import_panel.setFixedSize(22, 20)

        import_header_layout.addWidget(self.import_panel_title_label)
        import_header_layout.addSpacing(12)
        import_header_layout.addWidget(self.import_panel_summary_label, 1)
        import_header_layout.addWidget(self.btn_toggle_import_panel, 0, Qt.AlignVCenter)
        import_layout.addLayout(import_header_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "视野",
            "G",
            "R",
            "DIC",
            "Merge",
            "状态",
        ])

        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setMaximumHeight(88)
        self.table.setVisible(False)

        table_header = self.table.horizontalHeader()
        table_header.setSectionResizeMode(QHeaderView.Stretch)
        table_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table_header.setSectionResizeMode(5, QHeaderView.ResizeToContents)

        import_layout.addWidget(self.table)
        self.import_group.setMaximumHeight(32)
        self.import_group.setMinimumHeight(32)
        main_layout.addWidget(self.import_group)

        # -------------------------
        # 底部：运行日志，默认折叠
        # 折叠时只保留一行最近状态
        # -------------------------
        self.log_group = QWidget()
        self.log_group.setObjectName("collapsePanel")
        log_layout = QVBoxLayout(self.log_group)
        log_layout.setContentsMargins(8, 4, 8, 4)
        log_layout.setSpacing(3)

        log_header_layout = QHBoxLayout()
        log_header_layout.setContentsMargins(0, 0, 0, 0)
        log_header_layout.setSpacing(6)

        self.log_panel_title_label = QLabel("运行日志")
        self.log_panel_title_label.setObjectName("collapsePanelTitle")
        self.log_panel_summary_label = QLabel("最近状态：-")
        self.log_panel_summary_label.setObjectName("collapsePanelSummary")
        self.btn_toggle_log_panel = QPushButton("+")
        self.btn_toggle_log_panel.setObjectName("collapseToggleButton")
        self.btn_toggle_log_panel.setToolTip("展开运行日志")
        self.btn_toggle_log_panel.setFixedSize(22, 20)

        log_header_layout.addWidget(self.log_panel_title_label)
        log_header_layout.addSpacing(12)
        log_header_layout.addWidget(self.log_panel_summary_label, 1)
        log_header_layout.addWidget(self.btn_toggle_log_panel, 0, Qt.AlignVCenter)
        log_layout.addLayout(log_header_layout)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumHeight(72)
        self.log_edit.setPlaceholderText("运行日志")
        self.log_edit.setVisible(False)

        log_layout.addWidget(self.log_edit)
        self.log_group.setMaximumHeight(32)
        self.log_group.setMinimumHeight(32)
        main_layout.addWidget(self.log_group)

        self.set_common_style()

        self.btn_select_folder.clicked.connect(self.select_folder)
        self.btn_import.clicked.connect(self.import_images)
        self.btn_run_analysis.clicked.connect(self.run_analysis)
        self.btn_toggle_import_panel.clicked.connect(self.toggle_import_panel)
        self.btn_toggle_log_panel.clicked.connect(self.toggle_log_panel)
        self.table.itemSelectionChanged.connect(self.on_import_table_selection_changed)

        self.on_protein_changed()

    def tune_result_viewer_side_panel_width(self):
        """微调结果查看区右侧信息栏宽度。

        右侧包含“当前输出 / 视图控制 / 当前结果摘要”。这些控件属于
        ResultViewer 内部，AnalysisWindow 这里只做宽度约束，不改结果显示、
        图片切换、按钮功能和分析逻辑。
        """
        if not hasattr(self, "result_viewer") or self.result_viewer is None:
            return

        target_titles = {"当前输出", "视图控制", "当前结果摘要"}
        target_groups = []

        for group in self.result_viewer.findChildren(QGroupBox):
            title = str(group.title() or "").strip()
            if title in target_titles:
                target_groups.append(group)

        side_min_width = 342
        side_max_width = 378

        for group in target_groups:
            group.setMinimumWidth(side_min_width)
            group.setMaximumWidth(side_max_width)

        # 如果三个分组位于同一个右侧容器中，同时约束该容器宽度，
        # 让右侧栏整体变宽一点，并尽量和上方按钮区左边界接近。
        if target_groups:
            ancestors = []
            widget = target_groups[0].parentWidget()
            while widget is not None and widget is not self.result_viewer:
                ancestors.append(widget)
                widget = widget.parentWidget()

            side_panel = None
            for candidate in ancestors:
                try:
                    if all(candidate.isAncestorOf(group) or candidate is group for group in target_groups):
                        side_panel = candidate
                        break
                except Exception:
                    continue

            if side_panel is not None:
                side_panel.setMinimumWidth(side_min_width)
                side_panel.setMaximumWidth(side_max_width)

        # 兼容 ResultViewer 内部使用 QSplitter 的情况：把右侧尺寸调大到约 350px。
        for splitter in self.result_viewer.findChildren(QSplitter):
            try:
                if splitter.orientation() != Qt.Horizontal or splitter.count() < 2:
                    continue
                sizes = splitter.sizes()
                total = sum(sizes) if sizes else 0
                if total <= 0:
                    total = 1200
                right_width = 350
                left_width = max(420, total - right_width)
                splitter.setSizes([left_width, right_width])
            except Exception:
                pass

    def toggle_import_panel(self):
        visible = not self.table.isVisible()
        self.table.setVisible(visible)

        if visible:
            self.btn_toggle_import_panel.setText("-")
            self.btn_toggle_import_panel.setToolTip("折叠导入图片列表")
            self.import_group.setMaximumHeight(128)
            self.import_group.setMinimumHeight(0)
        else:
            self.btn_toggle_import_panel.setText("+")
            self.btn_toggle_import_panel.setToolTip("展开导入图片列表")
            self.import_group.setMaximumHeight(32)
            self.import_group.setMinimumHeight(32)

    def toggle_log_panel(self):
        visible = not self.log_edit.isVisible()
        self.log_edit.setVisible(visible)

        if visible:
            self.btn_toggle_log_panel.setText("-")
            self.btn_toggle_log_panel.setToolTip("折叠运行日志")
            self.log_group.setMaximumHeight(116)
            self.log_group.setMinimumHeight(0)
        else:
            self.btn_toggle_log_panel.setText("+")
            self.btn_toggle_log_panel.setToolTip("展开运行日志")
            self.log_group.setMaximumHeight(32)
            self.log_group.setMinimumHeight(32)

    def on_import_table_selection_changed(self):
        selected_rows = self.table.selectionModel().selectedRows()

        if not selected_rows:
            return

        row = selected_rows[0].row()
        field_item = self.table.item(row, 0)

        if field_item is None:
            return

        field_no = field_item.text().strip()

        if not field_no:
            return

        if hasattr(self.result_viewer, "set_current_field"):
            self.result_viewer.set_current_field(field_no)

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
            QWidget#collapsePanel {
                background-color: #ffffff;
                border: 1px solid #d9e2ef;
                border-radius: 6px;
            }
            QLabel#collapsePanelTitle {
                color: #1f4e79;
                font-weight: bold;
                background-color: transparent;
            }
            QLabel#collapsePanelSummary {
                color: #666666;
                background-color: transparent;
            }
            QPushButton#collapseToggleButton {
                min-width: 22px;
                max-width: 22px;
                min-height: 20px;
                max-height: 20px;
                padding: 0px;
                font-weight: bold;
            }
            QLabel {
                background-color: transparent;
            }
            QPushButton#proteinProjectButton {
                min-height: 28px;
                min-width: 76px;
                padding: 2px 10px;
                border-radius: 14px;
                border: 1px solid #b8c7da;
                background-color: #ffffff;
                color: #333333;
            }
            QPushButton#proteinProjectButton:hover {
                background-color: #eef5ff;
                border-color: #7aa7d9;
            }
            QPushButton#proteinProjectButton[state="current"],
            QPushButton#proteinProjectButton[state="currentDone"] {
                background-color: #d8eaff;
                border: 1px solid #4f8fce;
                color: #1f4e79;
                font-weight: bold;
            }
            QPushButton#proteinProjectButton[state="done"] {
                background-color: #f4fff6;
                border: 1px solid #8fd19e;
                color: #198754;
            }
            QPushButton#proteinProjectButton[state="imported"] {
                background-color: #fff8e1;
                border: 1px solid #f0c36d;
                color: #8a5a00;
            }
            QPushButton#proteinProjectButton[state="failed"],
            QPushButton#proteinProjectButton[state="currentFailed"] {
                background-color: #fff1f0;
                border: 1px solid #ff9a94;
                color: #c62828;
                font-weight: bold;
            }
            QPushButton#proteinProjectButton[state="currentImported"] {
                background-color: #d8eaff;
                border: 1px solid #4f8fce;
                color: #1f4e79;
                font-weight: bold;
            }
            QPushButton#proteinProjectButton[state="todo"] {
                background-color: #ffffff;
                border: 1px solid #cfd8e6;
                color: #666666;
            }
            QLineEdit, QTextEdit, QTableWidget {
                background-color: #ffffff;
                border: 1px solid #cfd8e6;
                border-radius: 3px;
            }
            QComboBox {
                min-height: 26px;
                min-width: 110px;
                background-color: #ffffff;
                border: 1px solid #b8c7da;
                border-radius: 4px;
                padding-left: 8px;
                padding-right: 6px;
            }
            QComboBox:hover {
                border-color: #7aa7d9;
                background-color: #f8fbff;
            }
            QComboBox QAbstractItemView {
                background-color: #ffffff;
                border: 1px solid #b8c7da;
                selection-background-color: #d8eaff;
                outline: 0px;
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

            QPushButton#PrimaryRunAnalysisButton,
            QPushButton#PrimarySelectFolderButton {
                min-height: 28px;
                min-width: 82px;
                background-color: #1769E0;
                border: 1px solid #1769E0;
                border-radius: 4px;
                padding: 2px 8px;
                color: #FFFFFF;
                font-weight: 500;
            }
            QPushButton#PrimaryRunAnalysisButton:hover,
            QPushButton#PrimarySelectFolderButton:hover {
                background-color: #0F5AC8;
                border-color: #0F5AC8;
                color: #FFFFFF;
            }
            QPushButton#PrimaryRunAnalysisButton:pressed,
            QPushButton#PrimarySelectFolderButton:pressed {
                background-color: #0A4EAE;
                border-color: #0A4EAE;
                color: #FFFFFF;
            }
            QPushButton#PrimaryRunAnalysisButton:disabled,
            QPushButton#PrimarySelectFolderButton:disabled {
                background-color: #F0F0F0;
                border-color: #DDDDDD;
                color: #999999;
                font-weight: 400;
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
            text = "当前蛋白暂无导入图片"
            self.import_status_label.setText(f"图片状态：{text}")
        else:
            text = f"已导入 {total_count} 个视野，完整视野 {complete_count} 个"
            self.import_status_label.setText(f"图片状态：{text}")

        if hasattr(self, "import_panel_summary_label"):
            self.import_panel_summary_label.setText(text)

    def load_protein_combo(self):
        """构建 HEL-1~HEL-5 横向检测项目按钮。

        这里保留函数名 load_protein_combo，是为了兼容原来 reload_config()
        的调用；实际界面已经不再使用下拉框。
        """
        # 清空旧按钮
        while self.protein_buttons_layout.count():
            item = self.protein_buttons_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.protein_buttons = {}
        protein_items = self.config.get_protein_items()
        protein_keys = [str(item.get("key", "")) for item in protein_items if item.get("key", "")]

        if protein_keys and self.current_protein_key not in protein_keys:
            self.current_protein_key = protein_keys[0]

        for item in protein_items:
            key = str(item.get("key", "") or "")
            name = str(item.get("name", key) or key)
            part = str(item.get("part", "") or "")

            if not key:
                continue

            button = QPushButton(name)
            button.setObjectName("proteinProjectButton")
            button.setCheckable(True)
            button.setToolTip(f"{name} / {part}" if part else name)
            button.clicked.connect(lambda checked=False, k=key: self.set_current_protein_key(k))

            self.protein_buttons[key] = button
            self.protein_buttons_layout.addWidget(button)

        self.protein_buttons_layout.addStretch()
        self.update_protein_buttons()

    def set_current_protein_key(self, protein_key: str):
        if not protein_key:
            return

        if self.current_protein_key == protein_key and not self._suspend_protein_changed:
            # 当前蛋白重复点击时不重新刷日志，但仍确保按钮状态正确。
            self.update_protein_buttons()
            return

        self.current_protein_key = protein_key
        self.update_protein_buttons()
        self.on_protein_changed()

    def get_current_protein_key(self):
        if self.current_protein_key:
            return str(self.current_protein_key)

        protein_items = self.config.get_protein_items()
        if protein_items:
            key = str(protein_items[0].get("key", "") or "")
            self.current_protein_key = key
            return key

        return ""

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
        analyzed_names = self.get_analyzed_protein_name_set() if self.current_case else set()
        status_items = []

        for item in self.config.get_protein_items():
            key = str(item.get("key", "") or "")
            name = str(item.get("name", key) or key)

            if name in analyzed_names or key in analyzed_names:
                status_items.append(f"{name} √")
            else:
                status_items.append(f"{name} -")

        if not self.current_case:
            self.protein_status_label.setText("已分析：-")
        else:
            self.protein_status_label.setText("已分析：" + "　".join(status_items))

        self.update_protein_buttons()

    def get_failed_protein_key_set(self):
        """返回当前病例中状态为失败/异常的蛋白 key 集合。

        当前版本主要预留给以后保存失败记录时使用；如果数据库暂时没有失败记录，
        这个集合就是空的，不影响现有流程。
        """
        if not self.current_case:
            return set()

        case_id = self.current_case.get("id")

        if not case_id:
            return set()

        try:
            rows = self.database.get_protein_analysis_by_case(case_id)
        except Exception:
            return set()

        failed_keys = set()

        for row in rows:
            status = str(row.get("status", "") or "").strip()
            if status not in {"失败", "异常", "错误", "failed", "error"}:
                continue

            row_name = str(row.get("protein_name", "") or "").strip()
            row_key = self.config.normalize_protein_key(row_name)

            if row_key:
                failed_keys.add(row_key)

        return failed_keys

    def get_protein_import_state(self, protein_key: str):
        """判断某个蛋白是否已经导入图片。

        返回：
        - none：没有导入图片
        - imported：已导入并且至少有一个完整 R/G 视野
        - incomplete：有图片但没有完整 R/G 视野
        """
        if not self.current_case:
            return "none"

        case_no = self.get_current_case_no()

        if not case_no:
            return "none"

        raw_folder = self.config.get_workspace_root() / case_no / "raw_images" / protein_key

        if not raw_folder.exists():
            return "none"

        try:
            image_items = self.load_images_from_raw_folder(raw_folder, protein_key)
        except Exception:
            return "none"

        if not image_items:
            return "none"

        complete_count = self.get_complete_image_count(image_items)

        if complete_count > 0:
            return "imported"

        return "incomplete"

    def update_protein_buttons(self):
        analyzed_names = self.get_analyzed_protein_name_set() if self.current_case else set()
        failed_keys = self.get_failed_protein_key_set() if self.current_case else set()
        current_key = self.get_current_protein_key()

        for item in self.config.get_protein_items():
            key = str(item.get("key", "") or "")
            name = str(item.get("name", key) or key)
            button = self.protein_buttons.get(key)

            if button is None:
                continue

            is_done = name in analyzed_names or key in analyzed_names
            is_failed = key in failed_keys
            import_state = self.get_protein_import_state(key)
            is_imported = import_state in {"imported", "incomplete"}
            is_current = key == current_key

            if is_failed and is_current:
                button.setText(f"{name} 当前!")
                button.setProperty("state", "currentFailed")
            elif is_failed:
                button.setText(f"{name} !")
                button.setProperty("state", "failed")
            elif is_current and is_done:
                button.setText(f"{name} 当前√")
                button.setProperty("state", "currentDone")
            elif is_current and is_imported:
                button.setText(f"{name} 当前待分析")
                button.setProperty("state", "currentImported")
            elif is_current:
                button.setText(f"{name} 当前")
                button.setProperty("state", "current")
            elif is_done:
                button.setText(f"{name} √")
                button.setProperty("state", "done")
            elif is_imported:
                button.setText(f"{name} 待分析")
                button.setProperty("state", "imported")
            else:
                button.setText(f"{name} -")
                button.setProperty("state", "todo")

            button.setChecked(is_current)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def select_next_unanalyzed_protein(self):
        protein_items = self.config.get_protein_items()

        if not protein_items:
            return

        analyzed_names = self.get_analyzed_protein_name_set()
        keys = [str(item.get("key", "") or "") for item in protein_items]
        current_key = self.get_current_protein_key()

        try:
            current_index = keys.index(current_key)
        except ValueError:
            current_index = 0

        total = len(keys)

        for offset in range(1, total + 1):
            index = (current_index + offset) % total
            key = keys[index]
            name = self.config.get_protein_display_name(key)

            if name not in analyzed_names and key not in analyzed_names:
                self.set_current_protein_key(key)
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
            self.current_protein_key = current_key
            self.load_protein_combo()
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
        self.current_output_dir = None

        self.refresh_table([])
        self.btn_run_analysis.setEnabled(False)

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
        self.current_output_dir = output_folder

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

            self.btn_run_analysis.setEnabled(complete_count > 0)
        else:
            self.imported_images = []
            self.refresh_table([])
            self.btn_run_analysis.setEnabled(False)
            self.append_log(f"{protein_name} 暂无导入图片。")

        if output_folder.exists() and self.folder_has_files(output_folder):
            self.result_viewer.set_output_dir(str(output_folder))
            self.result_viewer.refresh_results()
            self.append_log(f"{protein_name} 已加载历史分析结果：{output_folder}")
        else:
            if hasattr(self.result_viewer, "clear_results"):
                self.result_viewer.clear_results(f"{protein_name} 暂无分析结果。")
            self.append_log(f"{protein_name} 暂无分析结果。")

        self.update_protein_buttons()

    def load_images_from_raw_folder(self, raw_folder: Path, protein_key: str):
        """从 raw_images/proteinX 读取历史导入图片。

        统一使用 ImageChannelMatcher，与批量预检查、分析服务保持同一套图片规则。
        raw_images 保留用户原始文件名，不再要求 proteinX_ 前缀。
        """
        if not raw_folder.exists() or not raw_folder.is_dir():
            return []

        matcher = ImageChannelMatcher(self.config.get_image_rule())
        match_result = matcher.scan_folder(raw_folder)
        return self.match_result_to_table_rows(match_result, protein_key)

    def scan_source_images_for_table(self, source_folder: Path, protein_key: str):
        """扫描用户选择的源图片目录，并转换为导入列表表格使用的数据。"""
        matcher = ImageChannelMatcher(self.config.get_image_rule())
        match_result = matcher.scan_folder(source_folder)
        return match_result, self.match_result_to_table_rows(match_result, protein_key)

    def match_result_to_table_rows(self, match_result, protein_key: str):
        """把 ImageChannelMatcher 的结果转换为当前表格使用的行数据。"""
        rows = []
        for field_set in match_result.fields:
            field_no = self.normalize_field_no(field_set.field_id, protein_key)
            status = "完整" if field_set.is_complete else field_set.status_text()
            rows.append({
                "field_no": field_no,
                "G": str(field_set.get("G") or ""),
                "R": str(field_set.get("R") or ""),
                "DIC": str(field_set.get("DIC") or ""),
                "Merge": str(field_set.get("Merge") or ""),
                "status": status,
            })
        rows.sort(key=lambda item: self.natural_sort_key(str(item.get("field_no", ""))))
        return rows

    @staticmethod
    def normalize_field_no(field_id: str, protein_key: str):
        field_no = str(field_id or "").strip()
        prefix = f"{protein_key}_"
        if field_no.startswith(prefix):
            field_no = field_no[len(prefix):]
        return field_no.strip("_- ") or field_no

    @staticmethod
    def natural_sort_key(value: str):
        import re
        return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(value))]

    @staticmethod
    def collect_matched_files(match_result):
        """收集识别到的全部文件，包括重复通道中的文件。"""
        files = []
        seen = set()
        for field_set in match_result.fields:
            for path in field_set.files.values():
                p = Path(path)
                key = str(p.resolve())
                if key not in seen:
                    files.append(p)
                    seen.add(key)
            for duplicate_list in field_set.duplicates.values():
                for path in duplicate_list:
                    p = Path(path)
                    key = str(p.resolve())
                    if key not in seen:
                        files.append(p)
                        seen.add(key)
        return files

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

        source_path = Path(source_folder)
        if not source_path.exists() or not source_path.is_dir():
            QMessageBox.warning(self, "提示", f"图片文件夹不存在或不是文件夹：\n{source_path}")
            return

        protein_key = self.get_current_protein_key()
        protein_name = self.get_current_protein_name()
        case_no = self.get_current_case_no()

        if not case_no:
            QMessageBox.warning(self, "提示", "当前病例编号为空，无法建立工作目录。")
            return

        # 先用统一规则预扫描源目录。只有扫描通过后才清理旧 raw_images，避免误删。
        try:
            match_result, preview_rows = self.scan_source_images_for_table(source_path, protein_key)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"识别图片失败：\n{e}")
            return

        if match_result.total_fields <= 0:
            QMessageBox.warning(
                self,
                "未识别到图片",
                "当前文件夹没有识别到符合系统设置后缀的图片。\n\n"
                "请检查：系统设置 → 图片规则 中的 R/G/DIC/Merge 后缀，\n"
                "以及图片文件名是否符合规则。",
            )
            self.imported_images = []
            self.refresh_table([])
            self.btn_run_analysis.setEnabled(False)
            return

        workspace_root = self.config.get_workspace_root()
        target_folder = workspace_root / case_no / "raw_images" / protein_key

        if target_folder.exists() and self.folder_has_files(target_folder):
            reply = QMessageBox.question(
                self,
                "确认重新导入",
                f"{protein_name} 已经有导入图片。\n\n"
                "继续导入会清空当前蛋白的旧导入图片，并复制新的图片。\n"
                "raw_images 会保留原始文件名，不会强制加 protein 前缀。\n"
                "不会删除已经生成的分析结果。\n\n"
                "是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        try:
            if target_folder.exists():
                shutil.rmtree(target_folder)
            target_folder.mkdir(parents=True, exist_ok=True)

            copied_count = 0
            for source_file in self.collect_matched_files(match_result):
                target = target_folder / source_file.name
                shutil.copy2(source_file, target)
                copied_count += 1

            # 复制完成后从 raw_images 再扫描一次，保证界面显示与后续分析读取完全一致。
            self.imported_images = self.load_images_from_raw_folder(target_folder, protein_key)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导入图片失败：\n{e}")
            return

        self.current_raw_image_folder = target_folder
        self.refresh_table(self.imported_images)

        complete_count = self.get_complete_image_count(self.imported_images)
        total_count = len(self.imported_images)
        unmatched_count = len(match_result.unmatched_files)

        self.append_log(
            f"{protein_name} 图片导入完成：共识别 {total_count} 个视野，完整视野 {complete_count} 个。"
        )
        self.append_log(f"图片已复制到：{target_folder}")
        self.append_log(f"raw_images 保留原始文件名；分析时由服务复制到 cp_input。")
        if unmatched_count:
            self.append_log(f"未识别图片：{unmatched_count} 张。")

        self.btn_run_analysis.setEnabled(complete_count > 0)
        self.update_protein_buttons()

        QMessageBox.information(
            self,
            "导入完成",
            f"蛋白：{protein_name}\n"
            f"共识别 {total_count} 个视野。\n"
            f"完整视野：{complete_count} 个。\n"
            f"复制图片：{copied_count} 张。\n\n"
            f"已复制到：\n{target_folder}"
        )

    def refresh_table(self, image_items):
        self.table.setRowCount(len(image_items))
        self.update_import_status_label(image_items)

        for row_index, item in enumerate(image_items):
            values = [
                item.get("field_no", ""),
                self._short_path(item.get("G", "")),
                self._short_path(item.get("R", "")),
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

    def run_analysis(self):
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

        if not protein_key:
            QMessageBox.warning(self, "提示", "当前蛋白配置为空，无法运行分析。")
            return

        if not self.current_raw_image_folder or not Path(self.current_raw_image_folder).exists():
            QMessageBox.warning(self, "提示", "当前蛋白导入目录不存在，请先重新导入图片。")
            return

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
                f"共定位数：{positive_count}\n"
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

        self.append_log(f"准备运行分析：{protein_name}")
        self.append_log(f"当前蛋白导入目录：{self.current_raw_image_folder}")
        self.set_running_state(True)

        self.analysis_worker = SingleProteinAnalysisWorker(
            case_data=self.current_case,
            protein_key=protein_key,
            protein_name=protein_name,
            config=self.config,
        )
        self.analysis_worker.log_signal.connect(self.append_log)
        self.analysis_worker.finished_signal.connect(self.on_analysis_finished)
        self.analysis_worker.start()

    def imported_images_match_current_protein(self, image_items, protein_key: str):
        """兼容旧调用。

        现在 raw_images 保留原始文件名，不再要求文件名以 proteinX_ 开头。
        是否属于当前蛋白由 raw_images/proteinX 目录决定。
        """
        return True

    def on_analysis_finished(self, success: bool, elapsed: float, result: object, error_message: str):
        self.set_running_state(False)
        self.analysis_worker = None

        result = result or {}

        output_folder = result.get("output_folder", "") if isinstance(result, dict) else ""
        image_folder = result.get("image_folder", "") if isinstance(result, dict) else ""

        if output_folder:
            self.current_output_dir = Path(output_folder)
        if image_folder:
            self.current_raw_image_folder = Path(image_folder)

        if self.current_output_dir:
            self.result_viewer.set_output_dir(str(self.current_output_dir))
            self.result_viewer.refresh_results()

        if success:
            saved_ok, save_message = self.save_analysis_result_to_database()

            if saved_ok:
                self.append_log(save_message)
            else:
                self.append_log(f"结果入库失败：{save_message}")

            self.refresh_protein_status()
            self.refresh_current_protein_workspace()

            QMessageBox.information(
                self,
                "分析完成",
                f"分析完成。\n用时：{elapsed:.2f} 秒\n\n"
                f"输出目录：\n{self.current_output_dir}\n\n{save_message}"
            )

            self.append_log(f"输出目录：{self.current_output_dir}")
            self.select_next_unanalyzed_protein()
        else:
            message = str(error_message or "分析失败，请查看日志。")
            self.append_log(f"分析失败：{message}")
            QMessageBox.critical(
                self,
                "分析失败",
                f"分析失败。\n\n{message}"
            )

    # -------------------------
    # 入库
    # -------------------------

    def save_analysis_result_to_database(self):
        if not self.current_case:
            return False, "当前病例为空。"

        if not self.current_output_dir:
            return False, "当前输出目录为空。"

        case_id = self.current_case.get("id")

        if not case_id:
            return False, "当前病例缺少数据库 ID。"

        protein_key = self.get_current_protein_key()
        protein_name = self.get_current_protein_name()
        protein_part = self.config.get_protein_part(protein_key)

        parser = ResultParser(str(self.current_output_dir))
        summary_result = parser.parse_image_summary()

        if not summary_result.get("success"):
            return False, summary_result.get("message", "解析结果失败。")

        total = summary_result.get("total", {})
        rows = summary_result.get("rows", [])
        image_csv = summary_result.get("image_csv", "")

        image_folder = str(self.current_raw_image_folder or "")
        output_folder = str(self.current_output_dir)

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
            f"共定位数 {total.get('positive_count', 0)}，"
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
        for button in self.protein_buttons.values():
            button.setEnabled(not running)

        if running:
            self.btn_run_analysis.setEnabled(False)
            self.btn_run_analysis.setText("正在分析...")
        else:
            self.btn_run_analysis.setText("运行分析")
            self.btn_run_analysis.setEnabled(self.get_complete_image_count(self.imported_images) > 0)

    def append_log(self, message: str):
        message = str(message)
        self.log_edit.append(message)

        short_message = message.replace("\n", " ").strip()

        if len(short_message) > 120:
            short_message = short_message[:120] + "..."

        if hasattr(self, "last_log_label"):
            self.last_log_label.setText(f"最近状态：{short_message}")

        if hasattr(self, "log_panel_summary_label"):
            self.log_panel_summary_label.setText(f"最近状态：{short_message}")

    def _short_path(self, path_text: str):
        if not path_text:
            return ""

        path = Path(path_text)
        return path.name