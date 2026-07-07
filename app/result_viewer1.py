from pathlib import Path
import re

from PySide6.QtCore import Qt, QUrl, QTimer, QEvent
from PySide6.QtGui import QPixmap, QDesktopServices
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QSplitter,
    QGroupBox,
    QScrollArea,
    QSizePolicy,
)

from core.result_parser import ResultParser


class ResultViewer(QWidget):
    """
    蛋白分析页图像核查工作区。

    设计目标：
    1. 左侧以识别 Overlay 大图为主；
    2. 右侧集中放图片切换、视图控制、结果摘要和文件操作；
    3. 默认不显示文件列表，文件扫描只用于自动找 Overlay 图片。
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.output_dir = None
        self.files = []
        self.summary_data = None
        self.summary_rows = []
        self.summary_total = {}

        self.image_files = []
        self.image_groups = {}
        self.field_order = []
        self.current_field_no = None
        self.current_image_path = None
        self.current_pixmap = None
        self.current_image_mode = "g"
        self.zoom_factor = 1.0
        self.current_display_scale = 1.0
        self.view_mode = "fit"  # fit / original / zoom；height 模式保留为兼容旧逻辑，不再显示入口

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setChildrenCollapsible(False)

        # -------------------------
        # 左侧：识别图片核查卡片
        # -------------------------
        canvas_group = QGroupBox("识别图片核查")
        canvas_layout = QVBoxLayout(canvas_group)
        canvas_layout.setContentsMargins(8, 8, 8, 8)
        canvas_layout.setSpacing(6)

        image_top_bar = QHBoxLayout()
        image_top_bar.setContentsMargins(0, 0, 0, 0)
        image_top_bar.setSpacing(6)

        self.btn_show_g = QPushButton("G识别图")
        self.btn_show_r = QPushButton("R识别图")
        self.btn_show_colocalized = QPushButton("共定位图")

        for btn in [
            self.btn_show_g,
            self.btn_show_r,
            self.btn_show_colocalized,
        ]:
            btn.setObjectName("imageModeButton")
            btn.setMinimumHeight(28)
            btn.setMinimumWidth(86)

        self.image_info_label = QLabel("当前暂无识别图片")
        self.image_info_label.setMinimumHeight(30)
        self.image_info_label.setAlignment(Qt.AlignCenter)
        self.image_info_label.setStyleSheet("""
            QLabel {
                background-color: #f7f9fc;
                color: #333333;
                padding-left: 10px;
                padding-right: 10px;
                font-weight: bold;
                border: 1px solid #d9e2ef;
                border-radius: 4px;
            }
        """)

        image_top_bar.addWidget(self.btn_show_g)
        image_top_bar.addWidget(self.btn_show_r)
        image_top_bar.addWidget(self.btn_show_colocalized)
        image_top_bar.addWidget(self.image_info_label, 1)

        canvas_layout.addLayout(image_top_bar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        # 默认核查模式隐藏滚动条，只有 1:1 / 放大缩小时再显示
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: #f5f7fb;
                border: 1px solid #d9e2ef;
                border-radius: 4px;
            }
            QScrollBar:vertical {
                background: #f1f5fa;
                width: 9px;
                margin: 0px;
                border-radius: 4px;
            }
            QScrollBar:horizontal {
                background: #f1f5fa;
                height: 9px;
                margin: 0px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
                background: #b9c7d8;
                border-radius: 4px;
                min-height: 24px;
                min-width: 24px;
            }
            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
                background: #8fa9c5;
            }
            QScrollBar::add-line, QScrollBar::sub-line {
                width: 0px;
                height: 0px;
            }
        """)

        self.image_label = QLabel("当前蛋白暂无识别图片。")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setWordWrap(True)
        self.image_label.setMinimumSize(1100, 650)
        self.image_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.image_label.setStyleSheet("""
            QLabel {
                background-color: #ffffff;
                color: #666666;
                font-size: 16px;
            }
        """)

        self.scroll_area.setWidget(self.image_label)
        # 监听图像画布尺寸变化：首次进入页面、从其他页面切回、窗口大小变化时，自动重新适配图片。
        self.scroll_area.viewport().installEventFilter(self)
        canvas_layout.addWidget(self.scroll_area, 1)

        main_splitter.addWidget(canvas_group)

        # -------------------------
        # 右侧：结果与控制卡片区
        # -------------------------
        right_panel = QWidget()
        right_panel.setObjectName("rightPanel")
        right_panel.setMaximumWidth(340)
        right_panel.setMinimumWidth(305)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(8)

        # 当前输出
        output_group = QGroupBox("当前输出")
        output_layout = QVBoxLayout(output_group)
        output_layout.setContentsMargins(8, 8, 8, 8)
        output_layout.setSpacing(6)

        self.output_dir_label = QLabel("输出目录：未设置")
        self.output_dir_label.setWordWrap(True)
        self.output_dir_label.setStyleSheet("color: #555555;")
        output_layout.addWidget(self.output_dir_label)

        output_btn_row1 = QHBoxLayout()
        output_btn_row2 = QHBoxLayout()

        self.btn_refresh = QPushButton("刷新结果")
        self.btn_open_dir = QPushButton("打开输出目录")
        self.btn_open_image = QPushButton("打开当前图片")

        self.btn_refresh.setToolTip("刷新当前输出结果")
        self.btn_open_dir.setToolTip("打开输出目录")
        self.btn_open_image.setToolTip("打开当前图片")

        output_btn_row1.addWidget(self.btn_refresh)
        output_btn_row2.addWidget(self.btn_open_dir)
        output_btn_row2.addWidget(self.btn_open_image)

        output_layout.addLayout(output_btn_row1)
        output_layout.addLayout(output_btn_row2)

        right_layout.addWidget(output_group)

        # 视图控制
        view_group = QGroupBox("视图控制")
        view_layout = QVBoxLayout(view_group)
        view_layout.setContentsMargins(8, 8, 8, 8)
        view_layout.setSpacing(6)

        self.btn_fit = QPushButton("适应窗口")
        self.btn_1_1 = QPushButton("1:1")

        zoom_row = QHBoxLayout()
        self.btn_zoom_in = QPushButton("放大")
        self.btn_zoom_out = QPushButton("缩小")
        zoom_row.addWidget(self.btn_zoom_in)
        zoom_row.addWidget(self.btn_zoom_out)

        nav_row = QHBoxLayout()
        self.btn_prev_image = QPushButton("上一视野")
        self.btn_next_image = QPushButton("下一视野")
        nav_row.addWidget(self.btn_prev_image)
        nav_row.addWidget(self.btn_next_image)

        view_layout.addWidget(self.btn_fit)
        view_layout.addWidget(self.btn_1_1)
        view_layout.addLayout(zoom_row)
        view_layout.addLayout(nav_row)

        right_layout.addWidget(view_group)

        # 当前结果摘要
        result_group = QGroupBox("当前结果摘要")
        result_layout = QVBoxLayout(result_group)
        result_layout.setContentsMargins(8, 8, 8, 8)
        result_layout.setSpacing(6)

        # 表格已经包含当前视野和合计数据，文字摘要不再常驻显示，避免重复占空间。
        self.summary_label = QLabel("当前还没有结果。")
        self.summary_label.setWordWrap(True)
        self.summary_label.setVisible(False)

        self.summary_table = QTableWidget()
        self.summary_table.setColumnCount(5)
        self.summary_table.setHorizontalHeaderLabels([
            "视野",
            "精子数",
            "共定位数",
            "标定率",
            "荧光",
        ])
        self.summary_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.summary_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.summary_table.setAlternatingRowColors(True)
        self.summary_table.verticalHeader().setVisible(False)
        self.summary_table.setVisible(True)
        self.summary_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.summary_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.summary_table.setSizeAdjustPolicy(QTableWidget.AdjustToContents)
        self.summary_table.setMaximumHeight(190)

        summary_header = self.summary_table.horizontalHeader()
        summary_header.setSectionResizeMode(QHeaderView.Stretch)

        result_layout.addWidget(self.summary_table)

        # 文件统计属于开发/排查信息，界面不再常驻显示；需要查看文件时打开输出目录即可。
        self.file_summary_label = QLabel("文件统计：-")
        self.file_summary_label.setWordWrap(True)
        self.file_summary_label.setVisible(False)

        right_layout.addWidget(result_group, 1)
        right_layout.addStretch()

        main_splitter.addWidget(right_panel)
        main_splitter.setSizes([1480, 320])

        main_layout.addWidget(main_splitter, 1)

        self.setStyleSheet("""
            QWidget {
                font-family: Microsoft YaHei;
                font-size: 13px;
                background-color: #f5f7fb;
            }
            QWidget#rightPanel {
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
            QPushButton {
                min-height: 28px;
                background-color: #ffffff;
                border: 1px solid #b8c7da;
                border-radius: 4px;
                padding: 2px 8px;
            }
            QPushButton#imageModeButton {
                min-height: 26px;
                background-color: #ffffff;
                border: 1px solid #9db8d3;
                border-radius: 4px;
                padding: 2px 10px;
                font-weight: normal;
            }
            QPushButton#imageModeButton:hover {
                background-color: #eaf3ff;
                border-color: #5f9bd6;
            }
            QPushButton:hover {
                background-color: #eef5ff;
                border-color: #7aa7d9;
            }
            QPushButton:pressed {
                background-color: #dfeeff;
            }
            QTableWidget {
                background-color: #ffffff;
                alternate-background-color: #f7f9fc;
                gridline-color: #d9e2ef;
                border: 1px solid #d9e2ef;
            }
            QTableWidget QScrollBar:vertical {
                background: #f1f5fa;
                width: 7px;
                margin: 0px;
            }
            QTableWidget QScrollBar::handle:vertical {
                background: #c3cfdd;
                border-radius: 3px;
                min-height: 20px;
            }
            QTableWidget QScrollBar::add-line:vertical,
            QTableWidget QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QHeaderView::section {
                background-color: #eef3f9;
                border: 1px solid #d9e2ef;
                padding: 3px;
                font-weight: bold;
            }
        """)

        # -------------------------
        # 信号
        # -------------------------
        self.btn_refresh.clicked.connect(self.refresh_results)
        self.btn_open_dir.clicked.connect(self.open_output_dir)
        self.btn_open_image.clicked.connect(self.open_current_image)

        self.btn_show_g.clicked.connect(lambda: self.switch_image_mode("g"))
        self.btn_show_r.clicked.connect(lambda: self.switch_image_mode("r"))
        self.btn_show_colocalized.clicked.connect(lambda: self.switch_image_mode("colocalized"))

        self.btn_fit.clicked.connect(self.show_fit_to_window)
        self.btn_1_1.clicked.connect(self.show_1_to_1)
        self.btn_zoom_in.clicked.connect(self.zoom_in)
        self.btn_zoom_out.clicked.connect(self.zoom_out)
        self.btn_prev_image.clicked.connect(self.show_prev_image)
        self.btn_next_image.clicked.connect(self.show_next_image)

    # -------------------------
    # 对外接口
    # -------------------------

    def set_output_dir(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir_label.setText(f"输出目录：{self.output_dir}")

    def clear_results(self, message: str = "当前还没有结果。"):
        self.output_dir = None
        self.files = []
        self.summary_data = None
        self.summary_rows = []
        self.summary_total = {}

        self.image_files = []
        self.image_groups = {}
        self.field_order = []
        self.current_field_no = None
        self.current_image_path = None
        self.current_pixmap = None
        self.zoom_factor = 1.0
        self.current_display_scale = 1.0
        self.view_mode = "fit"

        self.summary_label.setText(message)
        self.summary_table.setRowCount(0)
        self.summary_table.setVisible(True)
        self.adjust_summary_table_height()

        self.output_dir_label.setText("输出目录：未设置")
        self.file_summary_label.setText("文件统计：-")

        self.image_info_label.setText("当前暂无识别图片")
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText(message)
        self.image_label.resize(1100, 650)

    def show_message(self, message: str):
        self.clear_results(message)

    def refresh_results(self):
        if not self.output_dir:
            QMessageBox.information(self, "提示", "还没有设置输出目录。")
            return

        if not self.output_dir.exists():
            self.clear_results(f"输出目录不存在：\n{self.output_dir}")
            return

        parser = ResultParser(str(self.output_dir))

        self.refresh_summary(parser)
        self.refresh_file_list(parser)
        self.refresh_image_list()

    # -------------------------
    # 汇总结果
    # -------------------------

    def refresh_summary(self, parser: ResultParser):
        summary_result = parser.parse_image_summary()
        self.summary_data = summary_result
        self.summary_rows = []
        self.summary_total = {}

        if not summary_result.get("success"):
            self.summary_label.setText(f"分析结果汇总：\n{summary_result.get('message')}")
            self.summary_table.setRowCount(0)
            self.adjust_summary_table_height()
            return

        rows = summary_result.get("rows", [])
        total = summary_result.get("total", {})

        self.summary_rows = rows
        self.summary_total = total

        self.summary_table.setRowCount(len(rows) + 1)

        for row_index, item in enumerate(rows):
            values = [
                item.get("image_number", ""),
                item.get("sperm_count", 0),
                item.get("positive_count", 0),
                item.get("expression_rate", 0),
                item.get("mean_intensity", 0),
            ]

            for col_index, value in enumerate(values):
                table_item = QTableWidgetItem(str(value))
                table_item.setTextAlignment(Qt.AlignCenter)
                self.summary_table.setItem(row_index, col_index, table_item)

        total_row = len(rows)
        total_values = [
            "合计",
            total.get("sperm_count", 0),
            total.get("positive_count", 0),
            total.get("expression_rate", 0),
            total.get("mean_intensity", 0),
        ]

        for col_index, value in enumerate(total_values):
            table_item = QTableWidgetItem(str(value))
            table_item.setTextAlignment(Qt.AlignCenter)
            table_item.setBackground(Qt.lightGray)
            self.summary_table.setItem(total_row, col_index, table_item)

        self.update_summary_label()
        self.adjust_summary_table_height()

    def adjust_summary_table_height(self):
        """
        右侧每视野明细表按行数自动调整高度。
        少量视野时不显示难看的滚动条；视野较多时再允许滚动。
        """
        row_count = self.summary_table.rowCount()

        if row_count <= 0:
            self.summary_table.setFixedHeight(0)
            self.summary_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            return

        header_height = self.summary_table.horizontalHeader().height()
        row_height = self.summary_table.verticalHeader().defaultSectionSize()
        visible_rows = min(row_count, 5)
        target_height = header_height + row_height * visible_rows + 8

        if row_count <= 5:
            self.summary_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        else:
            self.summary_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.summary_table.setFixedHeight(max(70, min(target_height, 190)))

    # -------------------------
    # 文件扫描：不显示文件列表，只用于找图片
    # -------------------------

    def refresh_file_list(self, parser: ResultParser):
        self.files = parser.scan_files()
        file_summary = parser.get_file_summary()

        self.file_summary_label.setText(
            "文件统计："
            f"总 {file_summary['total']}；"
            f"图 {file_summary['image']}；"
            f"表 {file_summary['table']}；"
            f"PDF {file_summary['pdf']}；"
            f"日志 {file_summary['log']}"
        )

    # -------------------------
    # 图片核查
    # -------------------------

    def refresh_image_list(self):
        self.image_files = []
        self.image_groups = {}
        self.field_order = []

        image_suffixes = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}

        for item in self.files:
            path = Path(item.get("path", ""))

            if path.suffix.lower() not in image_suffixes:
                continue

            mode = self.classify_image_mode(path.name)

            # 只显示 G识别图、R识别图、共定位图；其他图片不进入核查列表
            if mode not in {"g", "r", "colocalized"}:
                continue

            field_no = self.extract_field_no(path.name)
            priority = self.get_image_priority(path.name)

            image_item = {
                "path": path,
                "name": path.name,
                "mode": mode,
                "field_no": field_no,
                "priority": priority,
            }

            self.image_files.append(image_item)
            self.image_groups.setdefault(field_no, {}).setdefault(mode, []).append(image_item)

        for field_no, mode_map in self.image_groups.items():
            for mode, items in mode_map.items():
                items.sort(key=lambda x: (x["priority"], x["name"]))

        self.field_order = sorted(self.image_groups.keys(), key=self.natural_sort_key)
        self.image_files.sort(key=lambda x: (self.natural_sort_key(x["field_no"]), x["priority"], x["name"]))

        if not self.image_files:
            self.current_field_no = None
            self.current_image_path = None
            self.current_pixmap = None
            self.image_info_label.setText("当前暂无识别图片")
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText("当前蛋白暂无识别图片。")
            self.image_label.resize(1100, 650)
            self.update_summary_label()
            return

        if self.current_field_no not in self.image_groups:
            self.current_field_no = self.field_order[0] if self.field_order else None

        self.show_current_image()

    def classify_image_mode(self, file_name: str):
        name = file_name.lower()

        if "colocalized" in name:
            return "colocalized"

        if "_r_r_objects" in name or "_r_objects" in name or "_r_" in name:
            return "r"

        if "_g_g_objects" in name or "_g_objects" in name or "_g_" in name:
            return "g"

        return None

    def get_image_priority(self, file_name: str):
        name = file_name.lower()

        # 图片查看顺序：G识别图 → R识别图 → 共定位图
        if ("_g_g_objects" in name or "_g_objects" in name) and "origoverlay" in name:
            return 0

        if ("_r_r_objects" in name or "_r_objects" in name) and "origoverlay" in name:
            return 1

        if "colocalized" in name and "origoverlay" in name:
            return 2

        if "_g_" in name:
            return 3

        if "_r_" in name:
            return 4

        if "colocalized" in name:
            return 5

        return 9

    def extract_field_no(self, file_name: str):
        stem = Path(file_name).stem

        markers = [
            "_G_G_colocalized",
            "_G_G_objects",
            "_R_R_objects",
            "_G_objects",
            "_R_objects",
            "_DIC",
            "_Merge",
            "_G",
            "_R",
        ]

        left = stem
        for marker in markers:
            if marker in stem:
                left = stem.split(marker)[0]
                break

        if "_" in left:
            return left.split("_", 1)[1].strip() or left

        return left.strip() or stem

    @staticmethod
    def natural_sort_key(value):
        text = str(value or "")
        parts = re.split(r"(\d+)", text)
        return [int(p) if p.isdigit() else p.lower() for p in parts]

    def switch_image_mode(self, mode: str):
        self.current_image_mode = mode
        self.show_current_image()

    def set_current_field(self, field_no):
        field_text = str(field_no or "").strip()

        if not field_text:
            return

        if field_text not in self.image_groups:
            # 兼容 Image.csv 用 1.0/2.0，而文件名用 1024/2048 的情况。
            # 这里不强制切换，避免点到无对应图片的行时报错。
            return

        self.current_field_no = field_text
        self.show_current_image()

    def show_current_image(self):
        if not self.image_groups or not self.current_field_no:
            self.image_label.setPixmap(QPixmap())
            self.image_info_label.setText("当前暂无识别图片")
            self.image_label.setText("当前蛋白暂无识别图片。")
            self.image_label.resize(1100, 650)
            self.update_summary_label()
            return

        field_map = self.image_groups.get(self.current_field_no, {})
        candidates = field_map.get(self.current_image_mode, [])

        if not candidates:
            mode_name = self.get_mode_display_name(self.current_image_mode)
            self.current_image_path = None
            self.current_pixmap = None
            self.image_label.setPixmap(QPixmap())
            self.image_info_label.setText(
                f"当前视野：{self.current_field_no}　当前类型：{mode_name}　文件：暂无"
            )
            self.image_label.setText(f"当前视野没有 {mode_name}。")
            self.image_label.resize(1100, 650)
            self.update_summary_label()
            return

        self.load_image(candidates[0]["path"])

    def load_image(self, image_path: Path):
        if not image_path.exists():
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText(f"图片不存在：\n{image_path}")
            self.image_label.resize(1100, 650)
            return

        pixmap = QPixmap(str(image_path))

        if pixmap.isNull():
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText(f"图片读取失败：\n{image_path}")
            self.image_label.resize(1100, 650)
            return

        self.current_image_path = image_path
        self.current_pixmap = pixmap

        field_no = self.current_field_no or self.extract_field_no(image_path.name)
        mode_name = self.get_mode_display_name(self.current_image_mode)
        self.image_info_label.setText(
            f"当前视野：{field_no}　当前类型：{mode_name}　文件：{image_path.name}"
        )
        self.image_label.setText("")

        self.update_summary_label()
        self.schedule_image_refit()

    def calculate_view_scale(self, mode: str = None):
        """计算当前画布下的真实显示比例。

        适应窗口模式下，图片实际不是 1:1 显示，
        放大和缩小应当从这个真实比例继续变化，而不是从 1.0 开始。
        """
        if self.current_pixmap is None or self.current_pixmap.isNull():
            return 1.0

        mode = mode or self.view_mode

        viewport_size = self.scroll_area.viewport().size()
        viewport_width = max(viewport_size.width(), 100)
        viewport_height = max(viewport_size.height(), 100)

        image_width = max(self.current_pixmap.width(), 1)
        image_height = max(self.current_pixmap.height(), 1)

        if mode == "height":
            target_height = max(viewport_height - 12, 100)
            return target_height / image_height

        if mode == "fit":
            target_width = max(viewport_width - 12, 100)
            target_height = max(viewport_height - 12, 100)
            return min(target_width / image_width, target_height / image_height)

        if mode == "original":
            return 1.0

        return max(float(self.zoom_factor or 1.0), 0.1)

    def get_current_zoom_base(self):
        """返回放大/缩小时应该使用的当前比例。"""
        if self.view_mode in {"height", "fit"}:
            return self.calculate_view_scale(self.view_mode)

        if self.view_mode == "original":
            return 1.0

        return max(float(self.zoom_factor or self.current_display_scale or 1.0), 0.1)

    def update_image_display(self):
        if self.current_pixmap is None:
            return

        viewport_size = self.scroll_area.viewport().size()
        viewport_width = max(viewport_size.width(), 100)
        viewport_height = max(viewport_size.height(), 100)

        # 默认核查模式：不显示滚动条，图片始终在画布内居中。
        # height 模式允许图片按高度铺满，横向超出部分会居中裁切，而不是出现难看的滚动条。
        if self.view_mode in {"height", "fit"}:
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.image_label.setAlignment(Qt.AlignCenter)

            if self.view_mode == "height":
                target_height = max(viewport_height - 12, 100)
                scale = self.calculate_view_scale("height")
                target_width = max(int(self.current_pixmap.width() * scale), 1)
                scaled = self.current_pixmap.scaled(
                    target_width,
                    target_height,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            else:
                target_width = max(viewport_width - 12, 100)
                target_height = max(viewport_height - 12, 100)
                scale = self.calculate_view_scale("fit")
                scaled = self.current_pixmap.scaled(
                    target_width,
                    target_height,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )

            self.zoom_factor = scale
            self.current_display_scale = scale

            # QLabel 大小固定为当前画布大小，pixmap 由 QLabel 居中绘制。
            # 这样窗口从全屏变小后，不会保留旧滚动偏移，图片仍然居中。
            self.image_label.setMinimumSize(0, 0)
            self.image_label.resize(viewport_width, viewport_height)
            self.image_label.setPixmap(scaled)
            self.center_scrollbars()
            return

        # 1:1 / 放大缩小：允许滚动条，用于查看局部细节。
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.image_label.setAlignment(Qt.AlignCenter)

        if self.view_mode == "original":
            self.zoom_factor = 1.0
            width = self.current_pixmap.width()
            height = self.current_pixmap.height()
        else:
            self.zoom_factor = max(float(self.zoom_factor or 1.0), 0.1)
            width = int(self.current_pixmap.width() * self.zoom_factor)
            height = int(self.current_pixmap.height() * self.zoom_factor)

        self.current_display_scale = self.zoom_factor

        scaled = self.current_pixmap.scaled(
            max(width, 1),
            max(height, 1),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.image_label.setMinimumSize(0, 0)
        self.image_label.setPixmap(scaled)
        self.image_label.resize(scaled.size())

        # 延迟到布局更新后再居中滚动条。
        QTimer.singleShot(0, self.center_scrollbars)

    def center_scrollbars(self):
        h_bar = self.scroll_area.horizontalScrollBar()
        v_bar = self.scroll_area.verticalScrollBar()

        h_bar.setValue((h_bar.maximum() + h_bar.minimum()) // 2)
        v_bar.setValue((v_bar.maximum() + v_bar.minimum()) // 2)

    def show_height_fit(self):
        self.view_mode = "height"
        self.zoom_factor = 1.0
        self.update_image_display()

    def show_fit_to_window(self):
        self.view_mode = "fit"
        self.zoom_factor = 1.0
        self.update_image_display()

    def show_1_to_1(self):
        self.view_mode = "original"
        self.zoom_factor = 1.0
        self.update_image_display()

    def zoom_in(self):
        if self.current_pixmap is None:
            return

        # 从当前实际显示比例继续放大。
        # 例如当前是“适应窗口”0.62 倍，点击放大后应为 0.62 × 1.25，
        # 而不是直接跳到 1.25 倍。
        self.zoom_factor = self.get_current_zoom_base() * 1.25
        self.view_mode = "zoom"
        self.update_image_display()

    def zoom_out(self):
        if self.current_pixmap is None:
            return

        # 从当前实际显示比例继续缩小，避免从 1:1 重新计算造成跳变。
        self.zoom_factor = self.get_current_zoom_base() / 1.25

        if self.zoom_factor < 0.1:
            self.zoom_factor = 0.1

        self.view_mode = "zoom"
        self.update_image_display()

    def show_prev_image(self):
        self.show_neighbor_field(-1)

    def show_next_image(self):
        self.show_neighbor_field(1)

    def show_neighbor_field(self, step: int):
        if not self.field_order or not self.current_field_no:
            return

        try:
            index = self.field_order.index(self.current_field_no)
        except ValueError:
            index = 0

        new_index = (index + step) % len(self.field_order)
        self.current_field_no = self.field_order[new_index]
        self.show_current_image()

    def get_current_summary_row(self):
        if not self.summary_rows or not self.current_field_no:
            return None

        # 优先按当前视野号直接匹配。
        for row in self.summary_rows:
            image_number = str(row.get("image_number", "")).strip()
            if image_number == str(self.current_field_no):
                return row

        # 再按图片文件排序顺序与 Image.csv 行顺序匹配。
        try:
            index = self.field_order.index(self.current_field_no)
        except ValueError:
            index = -1

        if 0 <= index < len(self.summary_rows):
            return self.summary_rows[index]

        return None

    def update_summary_label(self):
        total = self.summary_total or {}
        current_row = self.get_current_summary_row()

        if current_row:
            current_text = (
                f"当前视野：{self.current_field_no}\n"
                f"精子数：{current_row.get('sperm_count', 0)}\n"
                f"共定位数：{current_row.get('positive_count', 0)}\n"
                f"标定率：{current_row.get('expression_rate', 0)}%\n"
                f"荧光强度：{current_row.get('mean_intensity', 0)}"
            )
        else:
            current_text = f"当前视野：{self.current_field_no or '-'}\n暂无当前视野统计。"

        if total:
            total_text = (
                f"\n\n合计：\n"
                f"视野数：{total.get('field_count', 0)}\n"
                f"精子总数：{total.get('sperm_count', 0)}\n"
                f"共定位数：{total.get('positive_count', 0)}\n"
                f"标定率：{total.get('expression_rate', 0)}%\n"
                f"荧光强度：{total.get('mean_intensity', 0)}"
            )
        else:
            total_text = ""

        self.summary_label.setText(current_text + total_text)

    def get_mode_display_name(self, mode: str):
        if mode == "colocalized":
            return "共定位图"
        if mode == "r":
            return "R识别图"
        if mode == "g":
            return "G识别图"
        return ""

    def schedule_image_refit(self):
        """
        延迟刷新图片适配。
        QStackedWidget 页面切换、首次进入蛋白分析页、窗口从全屏变为窗口时，
        QScrollArea 的 viewport 尺寸可能还没更新完成。
        因此连续延迟几次重新计算，保证默认视图下图片最终按当前窗口大小居中适配。
        """
        if self.current_pixmap is None:
            return

        QTimer.singleShot(0, self.update_image_display)
        QTimer.singleShot(60, self.update_image_display)
        QTimer.singleShot(160, self.update_image_display)

    def showEvent(self, event):
        super().showEvent(event)
        self.schedule_image_refit()

    def eventFilter(self, obj, event):
        if obj == self.scroll_area.viewport() and event.type() == QEvent.Resize:
            if self.current_pixmap is not None and self.view_mode in {"height", "fit"}:
                self.schedule_image_refit()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)

        if self.current_pixmap is not None and self.view_mode in {"height", "fit"}:
            self.schedule_image_refit()

    # -------------------------
    # 打开文件
    # -------------------------

    def open_output_dir(self):
        if not self.output_dir:
            QMessageBox.information(self, "提示", "还没有设置输出目录。")
            return

        if not self.output_dir.exists():
            QMessageBox.warning(self, "提示", f"输出目录不存在：\n{self.output_dir}")
            return

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir)))

    def open_current_image(self):
        if self.current_image_path and self.current_image_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.current_image_path)))
            return

        QMessageBox.information(self, "提示", "当前没有可打开的图片。")

    def open_selected_file(self):
        self.open_current_image()
