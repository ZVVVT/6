import shutil
import time
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
    QApplication,
)

from app.analysis_v2.head_analysis_workers import (
    HeadMeasurementWorker,
    HeadSegmentationWorker,
)
from app.analysis_v2.head_calibration_window import (
    HeadCalibrationWindow,
)
from app.analysis_v2.tail_analysis_workers import (
    TailFieldPrepareWorker,
    TailMeasurementWorker,
    TailPathWorker,
)
from app.analysis_v2.c18b_tail_calibration_window import (
    C18BTailCalibrationController,
)
from app.long_message_dialog import show_long_message_dialog
from app.result_viewer import ResultViewer
from core.config_manager import ConfigManager
from core.analysis_v2.head_input_adapter import (
    build_head_segmentation_fields,
)
from core.analysis_v2.head_result_publisher import (
    stage_head_measurement_output,
)
from core.analysis_v2.tail_result_publisher import (
    stage_tail_measurement_output,
)
from core.analysis_v2.tail_calibration_service import mark_tail_stage
from core.analysis_v2.task_state import TaskStateStore
from core.analysis_v2.tail_calibration_service import task_paths_from_root
from core.image_channel_matcher import ImageChannelMatcher
from core.result_parser import ResultParser
from core.protein_analysis_service import ProteinAnalysisService
from core.analysis_process_registry import analysis_process_registry


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
        self._cancel_requested = False

    def request_cancel(self):
        self._cancel_requested = True
        self.requestInterruption()
        analysis_process_registry.terminate_all()

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
                cancel_callback=lambda: self._cancel_requested,
            )
            elapsed = float(result.get("runner_elapsed_seconds", 0) or 0)
            self.finished_signal.emit(True, elapsed, result, "")
        except Exception as e:
            self.finished_signal.emit(False, 0.0, {}, str(e))


class AnalysisWindow(QWidget):
    analysis_activity_changed = Signal(bool)

    def __init__(self, database, parent=None):
        super().__init__(parent)

        self.database = database
        self.config = ConfigManager()
        self.config.ensure_default_config()

        self.current_case = None
        self.imported_images = []
        self.analysis_worker = None

        # Analysis V2 head workflow state.
        self.head_segmentation_worker = None
        self.head_measurement_worker = None
        self.head_calibration_window = None
        self.tail_path_worker = None
        self.tail_field_prepare_worker = None
        self.tail_field_prepare_workers = {}
        self.tail_field_prepare_max_workers = 2
        self.tail_field_prepare_queue = []
        self.tail_field_prepare_results = {}
        self.tail_field_order = []
        self._tail_head_calibration_finished = False
        self._tail_path_start_pending = False
        self.tail_measurement_worker = None
        self.tail_calibration_controller = None
        self.current_analysis_v2_task_root = None
        self.current_analysis_v2_context = None
        self._analysis_running = False
        self._analysis_v2_finish_pending = False
        self._analysis_v2_select_next_pending = False
        self.current_output_dir = None
        self.current_raw_image_folder = None
        self._suspend_protein_changed = False
        self._shutdown_cancel_requested = False
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
        self.result_viewer = ResultViewer(database=self.database, config=self.config)
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
            f"当前病例：{case_no}    姓名：{patient_name}    样本号：{sample_no}"
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

        protein_key = str(protein_key)

        if (
            self._analysis_running
            and self.current_protein_key
            and self.current_protein_key != protein_key
        ):
            QMessageBox.information(
                self,
                "分析进行中",
                "当前分析尚未结束，暂时不能切换检测项目。",
            )
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

        self.protein_part_label.setText(f"表达部位：{protein_part}")

        if protein_key == "protein3" and protein_part == "tail":
            self.pipeline_label.setText(
                "Pipeline：Analysis V2：头部人工校准 → C18B尾部处理 → 尾部测量"
            )
        elif str(protein_part).strip().lower() == "head":
            self.pipeline_label.setText(
                "Pipeline：Analysis V2（Cellpose → 人工校准 → 校准后测量）"
            )
        else:
            pipeline_path = self.config.get_pipeline_by_protein(protein_key)
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
        incoming = dict(case_data or {})

        if self._analysis_running and self.current_case:
            current_id = self.current_case.get("id")
            incoming_id = incoming.get("id")
            current_no = self.get_current_case_no()
            incoming_no = str(incoming.get("case_no", "") or "").strip()

            if (
                (current_id and incoming_id and current_id != incoming_id)
                or (current_no and incoming_no and current_no != incoming_no)
            ):
                QMessageBox.information(
                    self,
                    "分析进行中",
                    "当前分析尚未结束，暂时不能切换病例。",
                )
                return

        self.current_case = incoming

        self.case_no_label.setText(str(incoming.get("case_no", "")))
        self.patient_name_label.setText(str(incoming.get("patient_name", "")))
        self.sample_no_label.setText(str(incoming.get("sample_no", "")))
        self.test_date_label.setText(str(incoming.get("test_date", "")))

        self.update_case_summary_label()
        self.refresh_protein_status()
        self.on_protein_changed()

        self.append_log(
            f"已载入病例：{incoming.get('case_no', '')} - {incoming.get('patient_name', '')}"
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

        self.result_viewer.set_result_context(
            case_id=self.current_case.get("id"),
            protein_key=protein_key,
            protein_part=self.config.get_protein_part(protein_key),
        )

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

    def _worker_is_running(self, worker) -> bool:
        try:
            return bool(worker and worker.isRunning())
        except RuntimeError:
            return False

    def is_analysis_active(self) -> bool:
        """Return whether legacy or Analysis V2 work is still active."""
        calibration_open = False
        if self.head_calibration_window is not None:
            try:
                calibration_open = self.head_calibration_window.isVisible()
            except RuntimeError:
                calibration_open = False

        return bool(
            self._analysis_running
            or self._worker_is_running(self.analysis_worker)
            or self._worker_is_running(self.head_segmentation_worker)
            or self._worker_is_running(self.head_measurement_worker)
            or self._worker_is_running(self.tail_path_worker)
            or any(
                self._worker_is_running(worker)
                for worker in getattr(
                    self, "tail_field_prepare_workers", {}
                ).values()
            )
            or self._worker_is_running(self.tail_field_prepare_worker)
            or self._worker_is_running(self.tail_measurement_worker)
            or self.tail_calibration_controller is not None
            or calibration_open
        )

    def _analysis_context_matches_current(
        self,
        context,
    ) -> tuple:
        context = dict(context or {})
        current_case = dict(self.current_case or {})

        expected_case_id = context.get("case_id")
        current_case_id = current_case.get("id")

        expected_case_no = str(
            context.get("case_no", "") or ""
        ).strip()
        current_case_no = self.get_current_case_no()

        expected_protein_key = str(
            context.get("protein_key", "") or ""
        ).strip()
        current_protein_key = self.get_current_protein_key()

        if (
            expected_case_id
            and current_case_id
            and expected_case_id != current_case_id
        ):
            return False, "当前病例已发生变化。"

        if (
            expected_case_no
            and current_case_no != expected_case_no
        ):
            return False, "当前病例编号已发生变化。"

        if (
            expected_protein_key
            and current_protein_key != expected_protein_key
        ):
            return False, "当前检测项目已发生变化。"

        return True, ""

    def _clear_analysis_v2_state(self) -> None:
        self.current_analysis_v2_task_root = None
        self.current_analysis_v2_context = None
        self._analysis_v2_finish_pending = False
        self._analysis_v2_select_next_pending = False
        self.tail_field_prepare_queue = []
        self.tail_field_prepare_results = {}
        self.tail_field_prepare_workers = {}
        self.tail_field_prepare_worker = None
        self.tail_field_prepare_max_workers = 2
        self._tail_head_calibration_finished = False
        self._tail_path_start_pending = False

    def _finish_analysis_v2_ui(
        self,
        select_next: bool = False,
    ) -> None:
        self.set_running_state(False)
        self._clear_analysis_v2_state()

        if select_next:
            self.select_next_unanalyzed_protein()

    def _show_analysis_v2_error(
        self,
        title: str,
        summary: str,
        detail: str,
    ) -> None:
        message = str(detail or "未知错误")
        self.append_log("{}：{}".format(title, message))
        show_long_message_dialog(
            self,
            title=title,
            summary=summary,
            detail=message,
            level="error",
        )

    def _start_head_analysis_v2(
        self,
        complete_items,
        protein_key: str,
        protein_name: str,
        workflow: str = "head",
    ) -> None:
        """Prepare and start Analysis V2 head segmentation."""
        if self.is_analysis_active():
            raise RuntimeError("已有分析任务正在运行。")

        case_snapshot = dict(self.current_case or {})
        case_no = str(
            case_snapshot.get("case_no", "") or ""
        ).strip()

        if not case_no:
            raise RuntimeError("当前病例编号为空。")

        protein_key = str(protein_key or "").strip()
        protein_name = str(protein_name or "").strip()

        if not protein_key:
            raise RuntimeError("当前蛋白内部编号为空。")

        paired_fields = build_head_segmentation_fields(
            complete_items
        )

        project_root = Path(
            getattr(self.config, "app_root", Path(__file__).resolve().parents[1])
        ).resolve()

        workspace_root = Path(
            self.config.get_workspace_root()
        )
        if not workspace_root.is_absolute():
            workspace_root = (
                project_root / workspace_root
            ).resolve()
        else:
            workspace_root = workspace_root.resolve()

        target_output_dir = (
            workspace_root
            / case_no
            / "cp_output"
            / protein_key
        )

        self.current_analysis_v2_task_root = None
        self._analysis_v2_finish_pending = False
        self._analysis_v2_select_next_pending = False
        self.current_analysis_v2_context = {
            "case": case_snapshot,
            "case_id": case_snapshot.get("id"),
            "case_no": case_no,
            "protein_key": protein_key,
            "protein_name": protein_name,
            "protein_part": (
                "tail" if workflow == "protein3_tail" else "head"
            ),
            "workflow": str(workflow),
            "field_count": len(paired_fields),
            "project_root": str(project_root),
            "raw_image_folder": str(
                self.current_raw_image_folder or ""
            ),
            "target_output_dir": str(target_output_dir),
            "started_perf_counter": time.perf_counter(),
        }

        worker = HeadSegmentationWorker(
            project_root=project_root,
            case_data=case_snapshot,
            protein_key=protein_key,
            paired_fields=paired_fields,
            config=self.config,
            parent=self,
        )
        worker.log_signal.connect(self.append_log)
        worker.finished_signal.connect(
            self._on_head_segmentation_finished
        )
        worker.finished.connect(
            self._on_head_segmentation_thread_finished
        )
        worker.finished.connect(worker.deleteLater)

        self.head_segmentation_worker = worker
        self.set_running_state(True)
        self.btn_run_analysis.setText("正在识别头部...")

        self.append_log(
            "Analysis V2：开始头部分析，视野数 {}，Merge 可选。".format(
                len(paired_fields)
            )
        )

        try:
            worker.start()
        except BaseException:
            self.head_segmentation_worker = None
            worker.deleteLater()
            self._clear_analysis_v2_state()
            self.set_running_state(False)
            raise

    def _prepare_protein3_formal_inputs(
        self,
        complete_items,
        protein_name: str,
    ):
        """Prepare and return ordered protein3 fields sourced only from cp_input."""
        project_root = Path(
            getattr(self.config, "app_root", Path(__file__).resolve().parents[1])
        ).resolve()
        workspace_root = Path(self.config.get_workspace_root())
        if not workspace_root.is_absolute():
            workspace_root = (project_root / workspace_root).resolve()
        else:
            workspace_root = workspace_root.resolve()
        cp_input_dir = (
            workspace_root
            / self.get_current_case_no()
            / "cp_input"
            / "protein3"
        ).resolve()
        service = ProteinAnalysisService(self.config)
        service.prepare_input_folder(
            complete_items=list(complete_items),
            cp_input_dir=cp_input_dir,
            protein_name=protein_name,
            log_callback=self.append_log,
        )

        formal_items = []
        for item in complete_items:
            field_no = str(item.get("field_no", "") or "").strip()
            formal = {
                "field_no": field_no,
                "status": "完整",
                "G": "",
                "R": "",
                "Merge": "",
            }
            for channel in ("G", "R", "Merge"):
                source_text = str(item.get(channel, "") or "").strip()
                if not source_text:
                    if channel == "Merge":
                        raise RuntimeError(
                            "Q96P56 视野 {} 缺少 Merge 输入。".format(field_no)
                        )
                    raise RuntimeError(
                        "Q96P56 视野 {} 缺少 {} 输入。".format(
                            field_no, channel
                        )
                    )
                target_name = service._standard_input_name(
                    field_no=field_no,
                    channel=channel,
                    source=Path(source_text),
                )
                target = (cp_input_dir / target_name).resolve()
                if not target.is_file():
                    raise FileNotFoundError(
                        "正式输入未生成：{}".format(target)
                    )
                formal[channel] = str(target)
            formal_items.append(formal)
        self.append_log(
            "Q96P56 正式输入：{}（视野数 {}）。".format(
                cp_input_dir, len(formal_items)
            )
        )
        return formal_items

    def _on_head_segmentation_finished(
        self,
        success: bool,
        elapsed: float,
        result: object,
        error_message: str,
    ) -> None:
        """Open manual calibration after head segmentation."""
        if self._shutdown_cancel_requested:
            return
        payload = dict(result) if isinstance(result, dict) else {}

        if not success:
            self._analysis_v2_finish_pending = True
            self.current_analysis_v2_context = None
            self.current_analysis_v2_task_root = None
            self._show_analysis_v2_error(
                "头部识别失败",
                "Analysis V2 头部识别失败，旧分析结果没有被修改。",
                str(error_message or "头部识别失败。"),
            )
            return

        try:
            context = dict(self.current_analysis_v2_context or {})
            task_root_text = str(
                payload.get("task_root", "") or ""
            ).strip()

            if not task_root_text:
                raise RuntimeError(
                    "头部识别结果缺少 task_root。"
                )

            task_root = Path(task_root_text).resolve()
            if not task_root.is_dir():
                raise FileNotFoundError(
                    "头部识别任务目录不存在：{}".format(
                        task_root
                    )
                )

            expected_case_no = str(
                context.get("case_no", "") or ""
            ).strip()
            expected_protein_key = str(
                context.get("protein_key", "") or ""
            ).strip()
            actual_case_no = str(
                payload.get("case_no", "") or ""
            ).strip()
            actual_protein_key = str(
                payload.get("protein_key", "") or ""
            ).strip()

            if expected_case_no != actual_case_no:
                raise RuntimeError(
                    "识别结果病例编号与启动时不一致。"
                )
            if expected_protein_key != actual_protein_key:
                raise RuntimeError(
                    "识别结果蛋白编号与启动时不一致。"
                )
            if self.head_calibration_window is not None:
                raise RuntimeError(
                    "已有头部校准窗口未关闭。"
                )

            self.current_analysis_v2_task_root = task_root
            context["segmentation_payload"] = payload
            context["segmentation_elapsed_seconds"] = float(
                elapsed or 0.0
            )
            self.current_analysis_v2_context = context

            is_tail_workflow = (
                str(context.get("workflow", "") or "") == "protein3_tail"
            )
            self.tail_field_prepare_queue = []
            self.tail_field_prepare_results = {}
            self.tail_field_order = []
            self._tail_head_calibration_finished = False
            self._tail_path_start_pending = False

            window = HeadCalibrationWindow(
                task_root=task_root,
                progressive_tail=is_tail_workflow,
                parent=self,
            )
            window.setAttribute(
                Qt.WA_DeleteOnClose,
                True,
            )
            window.field_calibration_completed.connect(
                self._on_head_field_calibration_completed
            )
            window.calibration_completed.connect(
                self._on_head_calibration_completed
            )
            window.calibration_closed.connect(
                self._on_head_calibration_closed
            )

            self.head_calibration_window = window
            self.btn_run_analysis.setText("等待人工校准...")
            self.append_log(
                "Analysis V2：头部识别完成，已打开人工校准窗口。"
            )
            if is_tail_workflow:
                self.append_log(
                    "Analysis V2：已启用逐视野流水线；完成一个头部视野后，"
                    "系统立即在后台准备该视野尾部。"
                )

            window.show()
            window.raise_()
            window.activateWindow()

        except BaseException as exception:
            self._analysis_v2_finish_pending = True
            self._show_analysis_v2_error(
                "打开头部校准失败",
                "头部识别已经结束，但无法打开人工校准窗口。旧结果没有被修改。",
                str(exception),
            )

    def _on_head_segmentation_thread_finished(
        self,
    ) -> None:
        worker = self.sender()

        if self.head_segmentation_worker is worker:
            self.head_segmentation_worker = None

        if self._analysis_v2_finish_pending:
            self._finish_analysis_v2_ui()

    def _on_head_field_calibration_completed(
        self,
        field_id: str,
        result: object,
    ) -> None:
        """Queue one field for tail preparation as soon as its head is final."""
        window = self.sender()
        if window is not self.head_calibration_window:
            self.append_log(
                "Analysis V2：忽略过期的单视野头部完成信号。"
            )
            return
        field_id = str(field_id or "").strip()
        if not field_id:
            self.append_log(
                "Analysis V2：单视野头部完成信号缺少视野编号。"
            )
            return
        context = dict(self.current_analysis_v2_context or {})
        if context.get("workflow") != "protein3_tail":
            return
        if (
            field_id not in self.tail_field_prepare_queue
            and field_id not in self.tail_field_prepare_results
            and field_id not in getattr(
                self, "tail_field_prepare_workers", {}
            )
        ):
            self.tail_field_prepare_queue.append(field_id)
        self.append_log(
            "Analysis V2：视野 {} 头部已锁定，已加入尾部后台准备队列。".format(
                field_id
            )
        )
        self._start_next_tail_field_prepare()

    def _start_next_tail_field_prepare(self) -> None:
        context = dict(self.current_analysis_v2_context or {})
        if context.get("workflow") != "protein3_tail":
            self.tail_field_prepare_queue = []
            return
        task_root = Path(self.current_analysis_v2_task_root).resolve()
        project_root = Path(
            str(context.get("project_root", "") or "")
        ).resolve()
        workers = getattr(self, "tail_field_prepare_workers", None)
        if workers is None:
            workers = {}
            self.tail_field_prepare_workers = workers
        max_workers = max(
            1,
            min(
                2,
                int(getattr(self, "tail_field_prepare_max_workers", 2) or 1),
            )
        )
        while self.tail_field_prepare_queue and len(workers) < max_workers:
            field_id = str(self.tail_field_prepare_queue.pop(0))
            worker = TailFieldPrepareWorker(
                project_root=project_root,
                task_root=task_root,
                python_executable=Path(self.config.get_python_exe()),
                field_id=field_id,
                display_max_dim=1400,
                parent=self,
            )
            worker.log_signal.connect(self.append_log)
            worker.finished_signal.connect(
                self._on_tail_field_prepare_finished
            )
            worker.finished.connect(
                self._on_tail_field_prepare_thread_finished
            )
            worker.finished.connect(worker.deleteLater)
            workers[field_id] = worker
            self.tail_field_prepare_worker = next(iter(workers.values()), None)
            self.append_log(
                "Analysis V2：后台开始视野 {} 的C18B尾部处理；当前并发 {}/{}。".format(
                    field_id,
                    len(workers),
                    max_workers,
                )
            )
            try:
                worker.start()
            except BaseException:
                workers.pop(field_id, None)
                self.tail_field_prepare_queue.insert(0, field_id)
                if max_workers > 1:
                    self.tail_field_prepare_max_workers = 1
                    self.append_log(
                        "Analysis V2：C18B并行启动失败，当前任务回退为串行模式。"
                    )
                    break
                raise
        self.tail_field_prepare_worker = next(iter(workers.values()), None)
        if not self.tail_field_prepare_queue and not workers:
            self._maybe_start_tail_path_after_field_prepare()

    def _on_tail_field_prepare_finished(
        self,
        success: bool,
        field_id: str,
        result: object,
        error_message: str,
    ) -> None:
        if self._shutdown_cancel_requested:
            return
        field_id = str(field_id or "").strip()
        payload = dict(result) if isinstance(result, dict) else {}
        if bool(payload.get("cancelled")):
            self.append_log(
                "Analysis V2：视野 {} 尾部后台准备已取消。".format(
                    field_id or "当前"
                )
            )
            return
        self.tail_field_prepare_results[field_id] = {
            "success": bool(success),
            "payload": payload,
            "error": str(error_message or ""),
        }
        if success:
            elapsed = float(payload.get("elapsed_seconds", 0.0) or 0.0)
            self.append_log(
                "Analysis V2：视野 {} 的C18B尾部处理完成，耗时 {:.1f}s。".format(
                    field_id,
                    elapsed,
                )
            )
        else:
            self.append_log(
                "Analysis V2：视野 {} 的C18B尾部处理失败；全部头部完成后将自动重试。".format(
                    field_id
                )
            )
            if error_message:
                self.append_log(str(error_message)[-4000:])

    def _on_tail_field_prepare_thread_finished(self) -> None:
        worker = self.sender()
        workers = getattr(self, "tail_field_prepare_workers", {})
        field_id = str(getattr(worker, "field_id", "") or "").strip()
        if field_id and workers.get(field_id) is worker:
            workers.pop(field_id, None)
        else:
            for active_field, active_worker in list(workers.items()):
                if active_worker is worker:
                    workers.pop(active_field, None)
                    break
        self.tail_field_prepare_worker = next(iter(workers.values()), None)
        self._start_next_tail_field_prepare()
        self._maybe_start_tail_path_after_field_prepare()

    def _maybe_start_tail_path_after_field_prepare(self) -> None:
        """Start the final-contract flow after parallel field preparation."""
        if not self._tail_head_calibration_finished:
            return
        if not self._tail_path_start_pending:
            return
        if self._worker_is_running(self.tail_path_worker):
            return

        context = dict(self.current_analysis_v2_context or {})
        if context.get("workflow") != "protein3_tail":
            return

        ordered_fields = [
            str(value or "").strip()
            for value in list(self.tail_field_order or [])
            if str(value or "").strip()
        ]
        if not ordered_fields:
            return

        no_prepare_work_left = (
            not getattr(self, "tail_field_prepare_workers", {})
            and not self.tail_field_prepare_queue
        )
        if not no_prepare_work_left:
            return

        task_root = Path(self.current_analysis_v2_task_root).resolve()
        project_root = Path(
            str(context.get("project_root", "") or "")
        ).resolve()
        self._tail_path_start_pending = False

        failed_fields = [
            field_id
            for field_id in ordered_fields
            if not bool(
                dict(self.tail_field_prepare_results.get(field_id) or {}).get(
                    "success"
                )
            )
        ]
        if not failed_fields:
            self.append_log(
                "Analysis V2：全部视野C18B并行准备完成；开始生成尾部结果。"
            )
        else:
            self.append_log(
                "Analysis V2：部分C18B并行任务未成功，进入串行安全补算模式：{}。".format(
                    ", ".join(failed_fields)
                )
            )
        self._start_tail_path_worker(project_root, task_root)

    def _on_head_calibration_completed(
        self,
        result: object,
    ) -> None:
        """Close calibration and route to head measurement or protein3 tail path."""
        window = self.sender()

        if window is not self.head_calibration_window:
            self.append_log(
                "Analysis V2：忽略过期的校准完成信号。"
            )
            return

        worker = None

        try:
            task_root = Path(
                self.current_analysis_v2_task_root
            ).resolve()
            context = dict(
                self.current_analysis_v2_context or {}
            )

            if not task_root.is_dir():
                raise FileNotFoundError(
                    "Analysis V2 任务目录不存在：{}".format(
                        task_root
                    )
                )
            if self._worker_is_running(
                self.head_measurement_worker
            ):
                raise RuntimeError(
                    "头部测量任务已经在运行。"
                )

            matches, reason = (
                self._analysis_context_matches_current(
                    context
                )
            )
            if not matches:
                raise RuntimeError(reason)

            project_root = Path(
                str(context.get("project_root", "") or "")
            ).resolve()
            if not project_root.is_dir():
                raise FileNotFoundError(
                    "项目根目录不存在：{}".format(
                        project_root
                    )
                )

            context["calibration_result"] = (
                result if isinstance(result, dict) else {}
            )
            self.current_analysis_v2_context = context

            if not window.close():
                raise RuntimeError(
                    "人工校准窗口未能安全关闭。"
                )

            if context.get("workflow") == "protein3_tail":
                completed_fields = []
                for item in list(
                    dict(result or {}).get("fields") or []
                ):
                    if not isinstance(item, dict):
                        continue
                    field_id = str(item.get("field_id", "") or "").strip()
                    if field_id and field_id not in completed_fields:
                        completed_fields.append(field_id)
                active_fields = set(
                    getattr(self, "tail_field_prepare_workers", {}).keys()
                )
                for field_id in completed_fields:
                    if (
                        field_id not in self.tail_field_prepare_results
                        and field_id not in self.tail_field_prepare_queue
                        and field_id not in active_fields
                    ):
                        self.tail_field_prepare_queue.append(field_id)
                self.tail_field_order = list(completed_fields)
                self._tail_head_calibration_finished = True
                self._tail_path_start_pending = True
                self.btn_run_analysis.setText("正在进行C18B尾部处理...")
                self.append_log(
                    "Analysis V2：全部头部校准完成；头部窗口立即关闭。"
                    "开始C18B尾部处理，完成后直接生成尾部结果。"
                )
                self._start_next_tail_field_prepare()
                self._maybe_start_tail_path_after_field_prepare()
                return

            worker = HeadMeasurementWorker(
                project_root=project_root,
                task_root=task_root,
                config=self.config,
                parent=self,
            )
            worker.log_signal.connect(self.append_log)
            worker.finished_signal.connect(
                self._on_head_measurement_finished
            )
            worker.finished.connect(
                self._on_head_measurement_thread_finished
            )
            worker.finished.connect(worker.deleteLater)

            self.head_measurement_worker = worker
            self.btn_run_analysis.setText("正在测量头部...")
            self.append_log(
                "Analysis V2：人工校准完成，开始测量最终头部标签。"
            )
            worker.start()

        except BaseException as exception:
            if worker is not None:
                try:
                    worker.deleteLater()
                except RuntimeError:
                    pass
            self.head_measurement_worker = None
            task_root_text = str(
                self.current_analysis_v2_task_root or ""
            )
            is_tail_route = (
                dict(self.current_analysis_v2_context or {}).get("workflow")
                == "protein3_tail"
            )
            self._show_analysis_v2_error(
                (
                    "尾部自动处理启动失败"
                    if is_tail_route
                    else "头部测量启动失败"
                ),
                (
                    "头部校准数据已保留，但无法启动尾部自动处理。旧结果没有被修改。"
                    if is_tail_route
                    else "人工校准数据已保留，但无法启动头部测量。旧结果没有被修改。"
                ),
                "{}\n\n任务目录：{}".format(
                    exception,
                    task_root_text,
                ),
            )
            self._finish_analysis_v2_ui()

    def _start_tail_path_worker(
        self,
        project_root: Path,
        task_root: Path,
    ) -> None:
        if self._worker_is_running(self.tail_path_worker):
            raise RuntimeError("尾部自动路径任务已经在运行。")
        context = dict(self.current_analysis_v2_context or {})
        is_c18b = context.get("workflow") == "protein3_tail"
        mark_tail_stage(
            task_root,
            "tail_segmenting",
            (
                "正在执行C18B尾部处理和结果生成"
                if is_c18b
                else "正在执行联合尾部候选、人工校准和原子提升"
            ),
        )
        worker = TailPathWorker(
            project_root=project_root,
            task_root=task_root,
            python_executable=Path(self.config.get_python_exe()),
            parent=self,
        )
        worker.log_signal.connect(self.append_log)
        worker.finished_signal.connect(self._on_tail_path_finished)
        worker.finished.connect(self._on_tail_path_thread_finished)
        worker.finished.connect(worker.deleteLater)
        self.tail_path_worker = worker
        self.btn_run_analysis.setText(
            "正在生成C18B尾部结果..." if is_c18b else "正在校准尾部..."
        )
        self.append_log(
            "Analysis V2：开始C18B尾部处理和结果生成。"
            if is_c18b
            else (
                "Analysis V2：开始实时尾部人工流水线；"
                "当前视野就绪即打开，后续视野继续后台准备。"
            )
        )
        worker.start()

    def _on_tail_path_finished(
        self,
        success: bool,
        result: object,
        error_message: str,
    ) -> None:
        if self._shutdown_cancel_requested:
            return
        if not success:
            self._analysis_v2_finish_pending = True
            context = dict(self.current_analysis_v2_context or {})
            is_c18b = context.get("workflow") == "protein3_tail"
            self._show_analysis_v2_error(
                "C18B尾部处理失败" if is_c18b else "尾部自动处理失败",
                (
                    "C18B尾部结果未生成；头部校准和已有C18B结果均保留，可再次运行。"
                    if is_c18b
                    else "联合尾部流程未完成；任务目录、自动候选和已保存的人工结果均保留，可再次运行续接。"
                ),
                str(
                    error_message
                    or ("C18B尾部处理失败。" if is_c18b else "尾部自动处理失败。")
                ),
            )
            return
        try:
            payload = dict(result) if isinstance(result, dict) else {}
            if (
                payload.get("workflow") != "c18b_tail_editor"
                or payload.get("tail_backend") != "C18B"
            ):
                raise RuntimeError(
                    "不支持的旧尾部 workflow：{}（backend={}）。".format(
                        payload.get("workflow") or "<missing>",
                        payload.get("tail_backend") or "<missing>",
                    )
                )
            fields = list(payload.get("fields") or [])
            context = dict(self.current_analysis_v2_context or {})
            expected_count = int(context.get("field_count", 0) or 0)
            if not fields or len(fields) != expected_count:
                raise RuntimeError(
                    "尾部视野数不一致：期望 {}，实际 {}。".format(
                        expected_count, len(fields)
                    )
                )
            task_root = Path(self.current_analysis_v2_task_root).resolve()

            mark_tail_stage(task_root, "tail_segmented", "全部视野尾部自动路径完成")
            mark_tail_stage(
                task_root,
                "tail_calibration_required",
                "等待人工尾部校准",
            )
            controller = C18BTailCalibrationController(
                task_root=task_root,
                field_payloads=fields,
                parent=self,
            )
            controller.log_signal.connect(self.append_log)
            controller.calibration_completed.connect(
                self._on_tail_calibration_completed
            )
            controller.calibration_aborted.connect(
                self._on_tail_calibration_aborted
            )
            self.tail_calibration_controller = controller
            self.btn_run_analysis.setText("等待尾部校准...")
            controller.start()
        except BaseException as exception:
            self._analysis_v2_finish_pending = True
            self._show_analysis_v2_error(
                "打开尾部编辑器失败",
                "尾部自动结果已保留，但无法开始人工尾部校准。",
                str(exception),
            )

    def _on_tail_path_thread_finished(self) -> None:
        worker = self.sender()
        if self.tail_path_worker is worker:
            self.tail_path_worker = None
        if self._analysis_v2_finish_pending:
            self._finish_analysis_v2_ui()

    def _on_tail_calibration_completed(self, result: object) -> None:
        """Start the validated tail measurement after all editors are saved."""
        self.tail_calibration_controller = None
        worker = None

        try:
            payload = dict(result) if isinstance(result, dict) else {}
            fields = list(payload.get("fields") or [])
            context = dict(self.current_analysis_v2_context or {})
            task_root = Path(self.current_analysis_v2_task_root).resolve()

            if context.get("workflow") != "protein3_tail":
                raise RuntimeError("当前任务不是 protein3_tail 流程。")
            if not task_root.is_dir():
                raise FileNotFoundError(
                    "Analysis V2 任务目录不存在：{}".format(task_root)
                )
            if self._worker_is_running(self.tail_measurement_worker):
                raise RuntimeError("尾部测量任务已经在运行。")

            matches, reason = self._analysis_context_matches_current(context)
            if not matches:
                raise RuntimeError(reason)

            expected_count = int(context.get("field_count", 0) or 0)
            if not fields or len(fields) != expected_count:
                raise RuntimeError(
                    "尾部校准视野数不一致：期望 {}，实际 {}。".format(
                        expected_count, len(fields)
                    )
                )

            project_root = Path(
                str(context.get("project_root", "") or "")
            ).resolve()
            if not project_root.is_dir():
                raise FileNotFoundError(
                    "项目根目录不存在：{}".format(project_root)
                )

            context["tail_calibration_result"] = payload
            self.current_analysis_v2_context = context

            worker = TailMeasurementWorker(
                project_root=project_root,
                task_root=task_root,
                config=self.config,
                parent=self,
            )
            worker.log_signal.connect(self.append_log)
            worker.finished_signal.connect(
                self._on_tail_measurement_finished
            )
            worker.finished.connect(
                self._on_tail_measurement_thread_finished
            )
            worker.finished.connect(worker.deleteLater)

            self.tail_measurement_worker = worker
            self.btn_run_analysis.setText("正在测量尾部...")
            self.append_log(
                "Analysis V2：C18B尾部结果已完成，开始新版尾部测量。"
                if str(payload.get("tail_backend") or "") == "C18B"
                else "Analysis V2：全部视野尾部校准完成，开始新版尾部测量。"
            )
            worker.start()

        except BaseException as exception:
            if worker is not None:
                try:
                    worker.deleteLater()
                except RuntimeError:
                    pass
            self.tail_measurement_worker = None
            task_root_text = str(self.current_analysis_v2_task_root or "")
            is_c18b = str(payload.get("tail_backend") or "") == "C18B"
            self._show_analysis_v2_error(
                "尾部测量启动失败",
                (
                    "C18B尾部结果已保留，旧正式结果没有被修改。"
                    if is_c18b
                    else "人工校准结果已保留，旧正式结果没有被修改。"
                ),
                "{}\n\n任务目录：{}".format(
                    exception, task_root_text
                ),
            )
            self._finish_analysis_v2_ui()

    def _save_tail_analysis_v2_to_database(
        self,
        context,
        output_dir: Path,
        summary_result,
    ) -> str:
        """Atomically replace protein3 summary and field rows."""
        context = dict(context or {})
        summary_result = dict(summary_result or {})

        case_id = context.get("case_id")
        protein_name = str(
            context.get("protein_name", "") or ""
        ).strip()
        image_folder = str(
            context.get("raw_image_folder", "") or ""
        )
        output_folder = str(Path(output_dir).resolve())

        if not case_id:
            raise RuntimeError(
                "当前 Analysis V2 上下文缺少数据库病例 ID。"
            )
        if not protein_name:
            raise RuntimeError(
                "当前 Analysis V2 上下文缺少蛋白名称。"
            )
        if not hasattr(
            self.database,
            "replace_protein_analysis_with_fields",
        ):
            raise RuntimeError(
                "数据库组件缺少原子结果保存接口。"
            )
        if not summary_result.get("success"):
            raise RuntimeError(
                summary_result.get(
                    "message",
                    "尾部结果解析失败。",
                )
            )
        if (
            summary_result.get("calculation_mode")
            != "head_equivalent"
        ):
            raise RuntimeError(
                "尾部数据库保存拒绝非 head_equivalent 结果。"
            )

        total = dict(summary_result.get("total") or {})
        rows = list(summary_result.get("rows") or [])
        image_csv = str(
            summary_result.get("image_csv", "") or ""
        )

        field_results = []
        for item in rows:
            field_results.append({
                "field_no": str(
                    item.get("image_number", "") or ""
                ),
                "sperm_count": item.get("sperm_count", 0),
                "positive_count": item.get("positive_count", 0),
                "mean_intensity": item.get("mean_intensity", 0),
                "expression_rate": item.get("expression_rate", 0),
                "overlay_image_path": "",
                "csv_path": image_csv,
            })

        self.database.replace_protein_analysis_with_fields(
            case_id=case_id,
            protein_name=protein_name,
            protein_part="tail",
            image_folder=image_folder,
            output_folder=output_folder,
            total_fields=total.get("field_count", 0),
            total_sperm_count=total.get("sperm_count", 0),
            positive_count=total.get("positive_count", 0),
            mean_intensity=total.get("mean_intensity", 0),
            expression_rate=total.get("expression_rate", 0),
            field_results=field_results,
            status="完成",
        )

        return (
            f"{protein_name} 结果已保存到数据库："
            f"视野数 {total.get('field_count', 0)}，"
            f"精子总数 {total.get('sperm_count', 0)}，"
            f"有效尾部数 {total.get('positive_count', 0)}，"
            f"标定率 {self.format_rate_for_display(total.get('expression_rate', 0))}，"
            f"C 荧光强度 {total.get('mean_intensity_raw', total.get('mean_intensity', 0))}。"
        )

    def _on_tail_measurement_finished(
        self,
        success: bool,
        elapsed: float,
        result: object,
        error_message: str,
    ) -> None:
        """Safely publish validated tail output, save DB, and refresh UI."""
        if self._shutdown_cancel_requested:
            return
        payload = dict(result) if isinstance(result, dict) else {}

        if not success:
            self._analysis_v2_finish_pending = True
            task_root_text = str(self.current_analysis_v2_task_root or "")
            context = dict(self.current_analysis_v2_context or {})
            calibration = dict(context.get("tail_calibration_result") or {})
            is_c18b = str(calibration.get("tail_backend") or "") == "C18B"
            self._show_analysis_v2_error(
                "尾部测量失败",
                (
                    "新版尾部测量未完成，C18B尾部结果已保留，旧正式结果没有被修改。"
                    if is_c18b
                    else "新版尾部测量未完成，人工校准数据已保留，旧正式结果没有被修改。"
                ),
                "{}\n\n任务目录：{}".format(
                    error_message or "尾部测量失败。",
                    task_root_text,
                ),
            )
            return

        publication = None
        transaction_committed = False

        try:
            context = dict(self.current_analysis_v2_context or {})
            matches, reason = self._analysis_context_matches_current(context)
            if not matches:
                raise RuntimeError(
                    "{} 为避免写入错误病例，本次结果未发布。".format(
                        reason
                    )
                )

            measurement = dict(payload.get("measurement_result") or {})
            validation = dict(measurement.get("validation") or {})
            parsed_result = dict(validation.get("result_parser") or {})

            source_text = str(
                payload.get("candidate_output_dir", "") or ""
            ).strip()
            target_text = str(
                context.get("target_output_dir", "") or ""
            ).strip()

            if not parsed_result.get("success"):
                raise RuntimeError(
                    parsed_result.get("message") or "尾部结果解析失败。"
                )
            if parsed_result.get("calculation_mode") != "head_equivalent":
                raise RuntimeError("尾部测量未使用 head_equivalent 公式。")
            if not source_text:
                raise RuntimeError(
                    "尾部测量结果缺少 candidate_output_dir。"
                )
            if not target_text:
                raise RuntimeError(
                    "Analysis V2 上下文缺少正式输出目录。"
                )

            source_dir = Path(source_text).resolve()
            target_dir = Path(target_text).resolve()
            expected_field_count = int(
                context.get("field_count", 0) or 0
            )

            self.append_log(
                "Analysis V2：尾部测量通过严格校验，正在安全发布正式结果。"
            )

            publication = stage_tail_measurement_output(
                source_dir=source_dir,
                target_dir=target_dir,
                expected_field_count=expected_field_count,
            )

            save_message = self._save_tail_analysis_v2_to_database(
                context=context,
                output_dir=target_dir,
                summary_result=publication.summary,
            )

            cleanup_warning = publication.commit()
            publication = None
            transaction_committed = True

            context["tail_measurement_payload"] = payload
            context["tail_measurement_elapsed_seconds"] = float(elapsed or 0.0)
            self.current_analysis_v2_context = context

            task_root = Path(self.current_analysis_v2_task_root).resolve()
            try:
                TaskStateStore.from_task_paths(
                    task_paths_from_root(task_root)
                ).update(
                    "completed",
                    "tail_publication",
                    "尾部正式结果和数据库保存完成",
                )
            except BaseException as state_exception:
                self.append_log(
                    "Analysis V2：正式结果已保存，但任务状态更新失败：{}".format(
                        state_exception
                    )
                )

            self.current_output_dir = target_dir
            raw_folder_text = str(
                context.get("raw_image_folder", "") or ""
            ).strip()
            if raw_folder_text:
                self.current_raw_image_folder = Path(raw_folder_text)

            self.result_viewer.set_output_dir(str(target_dir))
            self.result_viewer.refresh_results()
            self.append_log(save_message)
            self.append_log(
                "Analysis V2 正式输出：{}".format(target_dir)
            )
            if cleanup_warning:
                self.append_log(cleanup_warning)

            self.refresh_protein_status()
            self.refresh_current_protein_workspace()

            started = float(
                context.get("started_perf_counter", 0) or 0
            )
            total_elapsed = (
                time.perf_counter() - started
                if started > 0
                else float(elapsed or 0.0)
            )
            # publication 已提交并释放，使用严格验证的解析结果显示。
            total = dict(parsed_result.get("total") or {})

            self._analysis_v2_select_next_pending = True
            self._analysis_v2_finish_pending = True
            if not self._worker_is_running(self.tail_measurement_worker):
                self._finish_analysis_v2_ui(select_next=True)

            QMessageBox.information(
                self,
                "尾部分析完成",
                "Analysis V2 尾部分析完成。\n"
                "总用时（含人工校准）：{:.2f} 秒\n\n"
                "视野数：{}\n"
                "精子总数：{}\n"
                "有效尾部数：{}\n"
                "C 荧光强度：{}\n"
                "标定率：{}%\n\n"
                "正式输出：\n{}\n\n{}".format(
                    total_elapsed,
                    total.get("field_count", 0),
                    total.get("sperm_count", 0),
                    total.get("positive_count", 0),
                    total.get(
                        "mean_intensity_raw",
                        total.get("mean_intensity", 0),
                    ),
                    total.get("expression_rate", 0),
                    target_dir,
                    save_message,
                ),
            )

        except BaseException as exception:
            task_root_text = str(self.current_analysis_v2_task_root or "")
            self._analysis_v2_finish_pending = True

            if transaction_committed:
                self._show_analysis_v2_error(
                    "结果已保存但界面刷新失败",
                    "新尾部文件和数据库记录已经保存成功，但结果界面刷新失败。重新进入蛋白分析页即可重新加载结果。",
                    "{}\n\nAnalysis V2 任务目录：{}".format(
                        exception,
                        task_root_text,
                    ),
                )
                return

            rollback_detail = ""
            if publication is not None:
                try:
                    publication.rollback()
                except BaseException as rollback_exception:
                    rollback_detail = (
                        "\n\n文件回滚异常：{}".format(
                            rollback_exception
                        )
                    )

            self._show_analysis_v2_error(
                "尾部结果发布失败",
                "新结果未能完成发布，系统已尽力恢复旧文件和旧数据库记录。",
                "{}{}\n\nAnalysis V2 任务目录：{}".format(
                    exception,
                    rollback_detail,
                    task_root_text,
                ),
            )

    def _on_tail_measurement_thread_finished(self) -> None:
        worker = self.sender()
        if self.tail_measurement_worker is worker:
            self.tail_measurement_worker = None
        if self._analysis_v2_finish_pending:
            self._finish_analysis_v2_ui()

    def _on_tail_calibration_aborted(self, message: str) -> None:
        self.tail_calibration_controller = None
        task_root = str(self.current_analysis_v2_task_root or "")
        self.append_log(
            "Analysis V2：尾部校准未完成：{}；任务目录：{}".format(
                message, task_root
            )
        )
        QMessageBox.warning(
            self,
            "尾部校准未完成",
            "{}\n\n任务状态和编辑数据已保留：\n{}".format(
                message, task_root
            ),
        )
        self._finish_analysis_v2_ui()

    def _on_head_calibration_closed(
        self,
        completed: bool,
    ) -> None:
        window = self.sender()

        if window is self.head_calibration_window:
            self.head_calibration_window = None

        if completed:
            self.append_log(
                "Analysis V2：人工校准窗口已关闭。"
            )
            return

        self.append_log(
            "Analysis V2：用户取消人工校准，本次头部分析已结束；旧结果保持不变。"
        )
        self._finish_analysis_v2_ui()

    def _save_head_analysis_v2_to_database(
        self,
        context,
        output_dir: Path,
        summary_result,
    ) -> str:
        context = dict(context or {})
        summary_result = dict(summary_result or {})

        case_id = context.get("case_id")
        protein_name = str(
            context.get("protein_name", "") or ""
        ).strip()
        image_folder = str(
            context.get("raw_image_folder", "") or ""
        )
        output_folder = str(Path(output_dir).resolve())

        if not case_id:
            raise RuntimeError(
                "当前 Analysis V2 上下文缺少数据库病例 ID。"
            )
        if not protein_name:
            raise RuntimeError(
                "当前 Analysis V2 上下文缺少蛋白名称。"
            )
        if not hasattr(
            self.database,
            "replace_protein_analysis_with_fields",
        ):
            raise RuntimeError(
                "数据库组件缺少原子结果保存接口。"
            )
        if not summary_result.get("success"):
            raise RuntimeError(
                summary_result.get(
                    "message",
                    "头部结果解析失败。",
                )
            )

        total = dict(summary_result.get("total") or {})
        rows = list(summary_result.get("rows") or [])
        image_csv = str(
            summary_result.get("image_csv", "") or ""
        )

        field_results = []
        for item in rows:
            field_results.append({
                "field_no": str(
                    item.get("image_number", "") or ""
                ),
                "sperm_count": item.get("sperm_count", 0),
                "positive_count": item.get("positive_count", 0),
                "mean_intensity": item.get("mean_intensity", 0),
                "expression_rate": item.get("expression_rate", 0),
                "overlay_image_path": "",
                "csv_path": image_csv,
            })

        self.database.replace_protein_analysis_with_fields(
            case_id=case_id,
            protein_name=protein_name,
            protein_part="head",
            image_folder=image_folder,
            output_folder=output_folder,
            total_fields=total.get("field_count", 0),
            total_sperm_count=total.get("sperm_count", 0),
            positive_count=total.get("positive_count", 0),
            mean_intensity=total.get("mean_intensity", 0),
            expression_rate=total.get("expression_rate", 0),
            field_results=field_results,
            status="完成",
        )

        return (
            f"{protein_name} 结果已保存到数据库："
            f"视野数 {total.get('field_count', 0)}，"
            f"精子总数 {total.get('sperm_count', 0)}，"
            f"共定位数 {total.get('positive_count', 0)}，"
            f"标定率 {self.format_rate_for_display(total.get('expression_rate', 0))}，"
            f"荧光强度 {self.format_int_for_display(total.get('mean_intensity', 0))}。"
        )

    def _on_head_measurement_finished(
        self,
        success: bool,
        elapsed: float,
        result: object,
        error_message: str,
    ) -> None:
        """Publish measured output, atomically save DB rows, then commit files."""
        if self._shutdown_cancel_requested:
            return
        payload = dict(result) if isinstance(result, dict) else {}

        if not success:
            self._analysis_v2_finish_pending = True
            task_root_text = str(
                self.current_analysis_v2_task_root or ""
            )
            self._show_analysis_v2_error(
                "头部测量失败",
                "Analysis V2 头部测量失败，人工校准数据已保留，旧结果没有被修改。",
                "{}\n\n任务目录：{}".format(
                    error_message or "头部测量失败。",
                    task_root_text,
                ),
            )
            return

        publication = None
        transaction_committed = False

        try:
            context = dict(
                self.current_analysis_v2_context or {}
            )
            matches, reason = (
                self._analysis_context_matches_current(
                    context
                )
            )
            if not matches:
                raise RuntimeError(
                    "{} 为避免写入错误病例，结果未发布。".format(
                        reason
                    )
                )

            source_text = str(
                payload.get(
                    "measurement_output_dir",
                    "",
                )
                or ""
            ).strip()
            target_text = str(
                context.get(
                    "target_output_dir",
                    "",
                )
                or ""
            ).strip()

            if not source_text:
                raise RuntimeError(
                    "测量结果缺少 measurement_output_dir。"
                )
            if not target_text:
                raise RuntimeError(
                    "Analysis V2 上下文缺少正式输出目录。"
                )

            source_dir = Path(source_text).resolve()
            target_dir = Path(target_text).resolve()
            expected_field_count = int(
                context.get("field_count", 0) or 0
            )

            self.append_log(
                "Analysis V2：测量完成，正在安全发布正式结果。"
            )

            publication = stage_head_measurement_output(
                source_dir=source_dir,
                target_dir=target_dir,
                expected_field_count=expected_field_count,
            )

            save_message = (
                self._save_head_analysis_v2_to_database(
                    context=context,
                    output_dir=target_dir,
                    summary_result=publication.summary,
                )
            )

            cleanup_warning = publication.commit()
            publication = None
            transaction_committed = True

            context["measurement_payload"] = payload
            context["measurement_elapsed_seconds"] = float(
                elapsed or 0.0
            )
            self.current_analysis_v2_context = context

            self.current_output_dir = target_dir
            raw_folder_text = str(
                context.get("raw_image_folder", "") or ""
            ).strip()
            if raw_folder_text:
                self.current_raw_image_folder = Path(
                    raw_folder_text
                )

            self.result_viewer.set_output_dir(
                str(target_dir)
            )
            self.result_viewer.refresh_results()
            self.append_log(save_message)
            self.append_log(
                "Analysis V2 正式输出：{}".format(
                    target_dir
                )
            )
            if cleanup_warning:
                self.append_log(cleanup_warning)

            self.refresh_protein_status()
            self.refresh_current_protein_workspace()

            started = float(
                context.get("started_perf_counter", 0) or 0
            )
            total_elapsed = (
                time.perf_counter() - started
                if started > 0
                else float(elapsed or 0.0)
            )

            # 先登记结束请求，再显示模态提示框。
            # QThread 的 finished 信号可能在 QMessageBox 的嵌套事件循环中到达；
            # 若先显示提示框，finished 可能在 finish_pending 仍为 False 时被消费，
            # 导致页面永久停留在“正在测量头部”。
            self._analysis_v2_select_next_pending = True
            self._analysis_v2_finish_pending = True

            # 工作线程通常已经返回，但 Qt 的 finished 槽可能尚未被主线程处理。
            # 此时直接恢复 UI；稍后到达的 finished 只负责清理线程引用，不会重复结束。
            if not self._worker_is_running(
                self.head_measurement_worker
            ):
                self._finish_analysis_v2_ui(
                    select_next=True
                )

            QMessageBox.information(
                self,
                "头部分析完成",
                "Analysis V2 头部分析完成。\n"
                "总用时（含人工校准）：{:.2f} 秒\n\n"
                "输出目录：\n{}\n\n{}".format(
                    total_elapsed,
                    target_dir,
                    save_message,
                ),
            )

        except BaseException as exception:
            task_root_text = str(
                self.current_analysis_v2_task_root or ""
            )
            self._analysis_v2_finish_pending = True

            if transaction_committed:
                self._show_analysis_v2_error(
                    "结果已保存但界面刷新失败",
                    "新文件和数据库记录已经保存成功，但结果界面刷新失败。重新进入蛋白分析页即可重新加载结果。",
                    "{}\n\nAnalysis V2 任务目录：{}".format(
                        exception,
                        task_root_text,
                    ),
                )
                return

            rollback_detail = ""

            if publication is not None:
                try:
                    publication.rollback()
                except BaseException as rollback_exception:
                    rollback_detail = (
                        "\n\n文件回滚异常：{}".format(
                            rollback_exception
                        )
                    )

            self._show_analysis_v2_error(
                "头部结果发布失败",
                "新结果未能完成发布，系统已尽力恢复旧文件和旧数据库记录。",
                "{}{}\n\nAnalysis V2 任务目录：{}".format(
                    exception,
                    rollback_detail,
                    task_root_text,
                ),
            )

    def _on_head_measurement_thread_finished(
        self,
    ) -> None:
        worker = self.sender()

        if self.head_measurement_worker is worker:
            self.head_measurement_worker = None

        if self._analysis_v2_finish_pending:
            select_next = bool(
                self._analysis_v2_select_next_pending
            )
            self._finish_analysis_v2_ui(
                select_next=select_next
            )

    def run_analysis(self):
        if not self.current_case:
            QMessageBox.information(self, "提示", "请先选择病例。")
            return

        if self.is_analysis_active():
            QMessageBox.information(
                self,
                "提示",
                "已有分析任务正在运行。",
            )
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
        protein_part = str(
            self.config.get_protein_part(protein_key) or ""
        ).strip().lower()

        if not protein_key:
            QMessageBox.warning(self, "提示", "当前蛋白配置为空，无法运行分析。")
            return

        if protein_part not in {"head", "tail"}:
            QMessageBox.warning(
                self,
                "提示",
                "当前蛋白表达部位未正确配置为 head 或 tail。",
            )
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
            expression_rate = self.format_rate_for_display(existing_result.get("expression_rate", 0))

            reply = QMessageBox.question(
                self,
                "确认重新分析",
                f"{protein_name} 当前已经有分析结果。\n\n"
                f"分析时间：{created_at}\n"
                f"视野数：{total_fields}\n"
                f"精子总数：{total_sperm_count}\n"
                f"共定位数：{positive_count}\n"
                f"标定率：{expression_rate}\n\n"
                "继续运行会用新的分析结果替换该蛋白旧结果。\n"
                "新结果完全成功前，旧文件和旧数据库记录会继续保留。\n"
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

        if protein_key == "protein3" and protein_part == "tail":
            try:
                formal_items = self._prepare_protein3_formal_inputs(
                    complete_items=complete_items,
                    protein_name=protein_name,
                )
                self._start_head_analysis_v2(
                    complete_items=formal_items,
                    protein_key=protein_key,
                    protein_name=protein_name,
                    workflow="protein3_tail",
                )
            except BaseException as exception:
                self._show_analysis_v2_error(
                    "Q96P56 组合流程启动失败",
                    "Analysis V2 组合流程未能启动，旧结果没有被修改。",
                    str(exception),
                )
                self._finish_analysis_v2_ui()
            return

        if protein_part == "head":
            try:
                self._start_head_analysis_v2(
                    complete_items=complete_items,
                    protein_key=protein_key,
                    protein_name=protein_name,
                )
            except BaseException as exception:
                self._show_analysis_v2_error(
                    "头部分析启动失败",
                    "Analysis V2 头部分析未能启动，旧结果没有被修改。",
                    str(exception),
                )
                self._finish_analysis_v2_ui()
            return

        # 尾部仍使用已验证的旧 ProteinAnalysisService 流程。
        self.set_running_state(True)
        self.analysis_worker = SingleProteinAnalysisWorker(
            case_data=self.current_case,
            protein_key=protein_key,
            protein_name=protein_name,
            config=self.config,
        )
        self.analysis_worker.log_signal.connect(self.append_log)
        self.analysis_worker.finished_signal.connect(self.on_analysis_finished)
        self.analysis_worker.finished.connect(self.on_analysis_thread_finished)
        self.analysis_worker.start()

    def imported_images_match_current_protein(self, image_items, protein_key: str):
        """兼容旧调用。

        现在 raw_images 保留原始文件名，不再要求文件名以 proteinX_ 开头。
        是否属于当前蛋白由 raw_images/proteinX 目录决定。
        """
        return True

    def on_analysis_finished(self, success: bool, elapsed: float, result: object, error_message: str):
        if self._shutdown_cancel_requested:
            return
        self.set_running_state(False)

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
            show_long_message_dialog(
                self,
                title="分析失败",
                summary="分析失败。\n\n详细错误信息请在下方日志中查看，可滚动浏览，也可以复制。",
                detail=message,
                level="error",
            )


    def on_analysis_thread_finished(self):
        """在线程真正结束后释放 QThread 对象。

        不能在 SingleProteinAnalysisWorker.finished_signal 的槽函数中直接
        self.analysis_worker = None，因为 finished_signal 是在 run() 末尾手动 emit 的，
        此时 Qt 线程对象可能还没有完全退出。过早销毁会导致：
        QThread: Destroyed while thread is still running。
        """
        worker = self.sender()
        if worker is not None:
            worker.deleteLater()
        if self.analysis_worker is worker:
            self.analysis_worker = None

    # -------------------------
    # 入库
    # -------------------------

    @staticmethod
    def format_int_for_display(value):
        try:
            return str(int(round(float(value))))
        except Exception:
            return str(value)

    @staticmethod
    def format_rate_for_display(value):
        try:
            return f"{float(value):.2f}%"
        except Exception:
            return f"{value}%" if value not in [None, ""] else "0.00%"

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

        parser = ResultParser(str(self.current_output_dir), protein_part=protein_part)
        summary_result = parser.parse_image_summary(protein_part=protein_part)

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
            f"标定率 {self.format_rate_for_display(total.get('expression_rate', 0))}，"
            f"荧光强度 {self.format_int_for_display(total.get('mean_intensity', 0))}。"
        )

    # -------------------------
    # 通用
    # -------------------------

    def closeEvent(self, event):
        if self.is_analysis_active():
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Warning)
            dialog.setWindowTitle("确认终止分析并退出")
            dialog.setText(
                "当前分析正在运行或等待人工校准。\n"
                "终止后，本次尚未发布的分析结果不会写入数据库；"
                "原有已完成结果将继续保留。\n"
                "是否终止本次分析并退出软件？"
            )
            continue_button = dialog.addButton("继续分析", QMessageBox.RejectRole)
            stop_button = dialog.addButton(
                "终止分析并退出", QMessageBox.DestructiveRole
            )
            dialog.setDefaultButton(continue_button)
            dialog.exec()
            if dialog.clickedButton() is not stop_button:
                event.ignore()
                return
            self._cancel_analysis_for_shutdown()
        super().closeEvent(event)

    def _cancel_analysis_for_shutdown(self):
        """Cancel owned work and preserve all previously published results."""
        self._shutdown_cancel_requested = True
        self.btn_run_analysis.setText("正在终止分析并关闭后台任务，请稍候……")
        self.btn_run_analysis.setEnabled(False)
        QApplication.processEvents()

        window = self.head_calibration_window
        if window is not None:
            try:
                window.close_for_shutdown()
            except RuntimeError:
                pass
        self.head_calibration_window = None

        controller = self.tail_calibration_controller
        if controller is not None:
            try:
                controller.stop()
            except RuntimeError:
                pass
        self.tail_calibration_controller = None

        prepare_workers = list(
            getattr(self, "tail_field_prepare_workers", {}).values()
        )
        if (
            self.tail_field_prepare_worker is not None
            and self.tail_field_prepare_worker not in prepare_workers
        ):
            prepare_workers.append(self.tail_field_prepare_worker)
        workers = [
            self.analysis_worker,
            self.head_segmentation_worker,
            self.head_measurement_worker,
            *prepare_workers,
            self.tail_path_worker,
            self.tail_measurement_worker,
        ]
        for worker in workers:
            if not self._worker_is_running(worker):
                continue
            try:
                cancel = getattr(worker, "request_cancel", None)
                if cancel is not None:
                    cancel()
                else:
                    worker.requestInterruption()
            except RuntimeError:
                pass

        analysis_process_registry.terminate_all()
        deadline = time.monotonic() + 8.0
        for worker in workers:
            if not self._worker_is_running(worker):
                continue
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if remaining_ms:
                worker.wait(remaining_ms)

        # A second precise tree kill is the bounded fallback after cooperative cancel.
        analysis_process_registry.terminate_all()
        for worker in workers:
            if self._worker_is_running(worker):
                worker.wait(2000)
        analysis_process_registry.clear_finished()

        task_root_text = str(self.current_analysis_v2_task_root or "").strip()
        if task_root_text:
            task_root = Path(task_root_text).resolve()
            try:
                TaskStateStore.from_task_paths(
                    task_paths_from_root(task_root)
                ).update(
                    "cancelled",
                    "shutdown",
                    "用户终止当前分析并退出软件",
                )
            except BaseException as exception:
                self.append_log(
                    "Analysis V2：记录用户取消状态失败：{}".format(exception)
                )

        self._analysis_running = False

    def set_running_state(self, running: bool):
        running = bool(running)
        changed = self._analysis_running != running
        self._analysis_running = running

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
            self.btn_run_analysis.setEnabled(
                self.get_complete_image_count(
                    self.imported_images
                ) > 0
            )

        if changed:
            self.analysis_activity_changed.emit(running)

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
