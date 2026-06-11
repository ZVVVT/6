from pathlib import Path

from PySide6.QtCore import Qt, QUrl
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

        self.image_files = []
        self.current_image_path = None
        self.current_pixmap = None
        self.current_image_mode = "colocalized"
        self.zoom_factor = 1.0
        self.view_mode = "height"  # height / fit / original / zoom

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        main_splitter = QSplitter(Qt.Horizontal)

        # -------------------------
        # 左侧：图像主画布
        # -------------------------
        canvas_widget = QWidget()
        canvas_layout = QVBoxLayout(canvas_widget)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(0)

        self.image_info_label = QLabel("当前暂无识别图片")
        self.image_info_label.setMinimumHeight(26)
        self.image_info_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.image_info_label.setStyleSheet("""
            QLabel {
                background-color: #2b2b2b;
                color: #e6e6e6;
                padding-left: 10px;
                font-weight: bold;
                border-bottom: 1px solid #444444;
            }
        """)
        canvas_layout.addWidget(self.image_info_label)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: #202020;
                border: 1px solid #333333;
            }
            QScrollBar:vertical, QScrollBar:horizontal {
                background: #2b2b2b;
            }
        """)

        self.image_label = QLabel("当前蛋白暂无识别图片。")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setWordWrap(True)
        self.image_label.setMinimumSize(1100, 650)
        self.image_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.image_label.setStyleSheet("""
            QLabel {
                background-color: #111111;
                color: #cccccc;
                font-size: 16px;
            }
        """)

        self.scroll_area.setWidget(self.image_label)
        canvas_layout.addWidget(self.scroll_area, 1)

        main_splitter.addWidget(canvas_widget)

        # -------------------------
        # 右侧：控制 + 结果摘要
        # -------------------------
        right_panel = QWidget()
        right_panel.setMaximumWidth(310)
        right_panel.setMinimumWidth(270)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(6)

        # 输出目录
        output_group = QGroupBox("当前输出")
        output_layout = QVBoxLayout(output_group)
        output_layout.setContentsMargins(8, 8, 8, 8)

        self.output_dir_label = QLabel("输出目录：未设置")
        self.output_dir_label.setWordWrap(True)
        self.output_dir_label.setStyleSheet("color: #555555;")
        output_layout.addWidget(self.output_dir_label)

        output_btn_layout = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新")
        self.btn_open_dir = QPushButton("打开目录")
        self.btn_open_image = QPushButton("打开图片")
        output_btn_layout.addWidget(self.btn_refresh)
        output_btn_layout.addWidget(self.btn_open_dir)
        output_btn_layout.addWidget(self.btn_open_image)
        output_layout.addLayout(output_btn_layout)

        right_layout.addWidget(output_group)

        # 图片类型
        mode_group = QGroupBox("图片类型")
        mode_layout = QVBoxLayout(mode_group)
        mode_layout.setContentsMargins(8, 8, 8, 8)
        mode_layout.setSpacing(5)

        self.btn_show_colocalized = QPushButton("共定位图")
        self.btn_show_r = QPushButton("R识别图")
        self.btn_show_g = QPushButton("G识别图")
        self.btn_show_other = QPushButton("其他图片")

        mode_layout.addWidget(self.btn_show_colocalized)
        mode_layout.addWidget(self.btn_show_r)
        mode_layout.addWidget(self.btn_show_g)
        mode_layout.addWidget(self.btn_show_other)

        right_layout.addWidget(mode_group)

        # 视图控制
        view_group = QGroupBox("视图控制")
        view_layout = QVBoxLayout(view_group)
        view_layout.setContentsMargins(8, 8, 8, 8)
        view_layout.setSpacing(5)

        self.btn_height_fit = QPushButton("按高度铺满")
        self.btn_fit = QPushButton("适应窗口")
        self.btn_1_1 = QPushButton("1:1")

        zoom_row = QHBoxLayout()
        self.btn_zoom_in = QPushButton("放大")
        self.btn_zoom_out = QPushButton("缩小")
        zoom_row.addWidget(self.btn_zoom_in)
        zoom_row.addWidget(self.btn_zoom_out)

        nav_row = QHBoxLayout()
        self.btn_prev_image = QPushButton("上一张")
        self.btn_next_image = QPushButton("下一张")
        nav_row.addWidget(self.btn_prev_image)
        nav_row.addWidget(self.btn_next_image)

        view_layout.addWidget(self.btn_height_fit)
        view_layout.addWidget(self.btn_fit)
        view_layout.addWidget(self.btn_1_1)
        view_layout.addLayout(zoom_row)
        view_layout.addLayout(nav_row)

        right_layout.addWidget(view_group)

        # 结果摘要
        result_group = QGroupBox("当前结果摘要")
        result_layout = QVBoxLayout(result_group)
        result_layout.setContentsMargins(8, 8, 8, 8)
        result_layout.setSpacing(6)

        self.summary_label = QLabel("当前还没有结果。")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("font-size: 14px; color: #333333;")
        result_layout.addWidget(self.summary_label)

        self.btn_toggle_summary_table = QPushButton("查看每视野明细")
        result_layout.addWidget(self.btn_toggle_summary_table)

        self.summary_table = QTableWidget()
        self.summary_table.setColumnCount(5)
        self.summary_table.setHorizontalHeaderLabels([
            "视野",
            "精子总数",
            "阳性数",
            "标定率",
            "荧光强度",
        ])
        self.summary_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.summary_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.summary_table.setAlternatingRowColors(True)
        self.summary_table.verticalHeader().setVisible(False)
        self.summary_table.setMaximumHeight(150)
        self.summary_table.setVisible(False)

        summary_header = self.summary_table.horizontalHeader()
        summary_header.setSectionResizeMode(QHeaderView.Stretch)

        result_layout.addWidget(self.summary_table)

        self.file_summary_label = QLabel("文件统计：-")
        self.file_summary_label.setWordWrap(True)
        self.file_summary_label.setStyleSheet("color: #666666;")
        result_layout.addWidget(self.file_summary_label)

        right_layout.addWidget(result_group, 1)
        right_layout.addStretch()

        main_splitter.addWidget(right_panel)
        main_splitter.setSizes([1500, 290])

        main_layout.addWidget(main_splitter, 1)

        self.setStyleSheet("""
            QGroupBox {
                border: 1px solid #d9d9d9;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 8px;
                background-color: #ffffff;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
                color: #333333;
            }
            QPushButton {
                min-height: 24px;
            }
        """)

        # -------------------------
        # 信号
        # -------------------------
        self.btn_refresh.clicked.connect(self.refresh_results)
        self.btn_open_dir.clicked.connect(self.open_output_dir)
        self.btn_open_image.clicked.connect(self.open_current_image)

        self.btn_show_colocalized.clicked.connect(lambda: self.switch_image_mode("colocalized"))
        self.btn_show_r.clicked.connect(lambda: self.switch_image_mode("r"))
        self.btn_show_g.clicked.connect(lambda: self.switch_image_mode("g"))
        self.btn_show_other.clicked.connect(lambda: self.switch_image_mode("other"))

        self.btn_height_fit.clicked.connect(self.show_height_fit)
        self.btn_fit.clicked.connect(self.show_fit_to_window)
        self.btn_1_1.clicked.connect(self.show_1_to_1)
        self.btn_zoom_in.clicked.connect(self.zoom_in)
        self.btn_zoom_out.clicked.connect(self.zoom_out)
        self.btn_prev_image.clicked.connect(self.show_prev_image)
        self.btn_next_image.clicked.connect(self.show_next_image)
        self.btn_toggle_summary_table.clicked.connect(self.toggle_summary_table)

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

        self.image_files = []
        self.current_image_path = None
        self.current_pixmap = None

        self.summary_label.setText(message)
        self.summary_table.setRowCount(0)
        self.summary_table.setVisible(False)
        self.btn_toggle_summary_table.setText("查看每视野明细")

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

        if not summary_result.get("success"):
            self.summary_label.setText(f"分析结果汇总：\n{summary_result.get('message')}")
            self.summary_table.setRowCount(0)
            return

        rows = summary_result.get("rows", [])
        total = summary_result.get("total", {})

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

        self.summary_label.setText(
            "分析结果汇总：\n\n"
            f"视野数：{total.get('field_count', 0)}\n"
            f"精子总数：{total.get('sperm_count', 0)}\n"
            f"阳性/共定位数：{total.get('positive_count', 0)}\n"
            f"标定率：{total.get('expression_rate', 0)}%\n"
            f"荧光强度：{total.get('mean_intensity', 0)}"
        )

    def toggle_summary_table(self):
        visible = not self.summary_table.isVisible()
        self.summary_table.setVisible(visible)

        if visible:
            self.btn_toggle_summary_table.setText("隐藏每视野明细")
        else:
            self.btn_toggle_summary_table.setText("查看每视野明细")

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

        image_suffixes = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}

        for item in self.files:
            path = Path(item.get("path", ""))

            if path.suffix.lower() not in image_suffixes:
                continue

            self.image_files.append({
                "path": path,
                "name": path.name,
                "mode": self.classify_image_mode(path.name),
                "priority": self.get_image_priority(path.name),
            })

        self.image_files.sort(key=lambda x: (x["priority"], x["name"]))

        if not self.image_files:
            self.current_image_path = None
            self.current_pixmap = None
            self.image_info_label.setText("当前暂无识别图片")
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText("当前蛋白暂无识别图片。")
            self.image_label.resize(1100, 650)
            return

        self.show_best_image_for_mode(self.current_image_mode)

    def classify_image_mode(self, file_name: str):
        name = file_name.lower()

        if "colocalized" in name:
            return "colocalized"

        if "_r_r_objects" in name or "_r_objects" in name or "_r_" in name:
            return "r"

        if "_g_g_objects" in name or "_g_objects" in name or "_g_" in name:
            return "g"

        return "other"

    def get_image_priority(self, file_name: str):
        name = file_name.lower()

        if "colocalized" in name and "origoverlay" in name:
            return 0

        if "_r_r_objects" in name and "origoverlay" in name:
            return 1

        if "_g_g_objects" in name and "origoverlay" in name:
            return 2

        if "origoverlay" in name:
            return 3

        if name.endswith(".png"):
            return 4

        return 9

    def switch_image_mode(self, mode: str):
        self.current_image_mode = mode
        self.show_best_image_for_mode(mode)

    def show_best_image_for_mode(self, mode: str):
        if not self.image_files:
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText("当前蛋白暂无识别图片。")
            self.image_label.resize(1100, 650)
            return

        candidates = [item for item in self.image_files if item["mode"] == mode]

        if not candidates:
            mode_name = self.get_mode_display_name(mode)
            self.image_label.setPixmap(QPixmap())
            self.image_info_label.setText(f"{mode_name}：暂无图片")
            self.image_label.setText(f"当前输出目录没有 {mode_name}。")
            self.image_label.resize(1100, 650)
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

        self.image_info_label.setText(f"当前图片：{image_path.name}")
        self.image_label.setText("")

        self.update_image_display()

    def update_image_display(self):
        if self.current_pixmap is None:
            return

        viewport_size = self.scroll_area.viewport().size()

        if self.view_mode == "height":
            target_height = max(viewport_size.height() - 12, 100)
            scale = target_height / max(self.current_pixmap.height(), 1)
            width = max(int(self.current_pixmap.width() * scale), 1)
            height = max(int(self.current_pixmap.height() * scale), 1)
        elif self.view_mode == "fit":
            target_width = max(viewport_size.width() - 12, 100)
            target_height = max(viewport_size.height() - 12, 100)
            scaled = self.current_pixmap.scaled(
                target_width,
                target_height,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self.image_label.setPixmap(scaled)
            self.image_label.resize(scaled.size())
            return
        elif self.view_mode == "original":
            width = self.current_pixmap.width()
            height = self.current_pixmap.height()
        else:
            width = int(self.current_pixmap.width() * self.zoom_factor)
            height = int(self.current_pixmap.height() * self.zoom_factor)

        scaled = self.current_pixmap.scaled(
            width,
            height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)
        self.image_label.resize(scaled.size())

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
        if self.view_mode in {"height", "fit", "original"}:
            self.zoom_factor = 1.0
        self.view_mode = "zoom"
        self.zoom_factor *= 1.25
        self.update_image_display()

    def zoom_out(self):
        if self.view_mode in {"height", "fit", "original"}:
            self.zoom_factor = 1.0
        self.view_mode = "zoom"
        self.zoom_factor /= 1.25

        if self.zoom_factor < 0.1:
            self.zoom_factor = 0.1

        self.update_image_display()

    def show_prev_image(self):
        self.show_neighbor_image(-1)

    def show_next_image(self):
        self.show_neighbor_image(1)

    def show_neighbor_image(self, step: int):
        if not self.image_files or not self.current_image_path:
            return

        paths = [item["path"] for item in self.image_files]

        try:
            index = paths.index(self.current_image_path)
        except ValueError:
            index = 0

        new_index = (index + step) % len(paths)
        self.load_image(paths[new_index])

    def get_mode_display_name(self, mode: str):
        if mode == "colocalized":
            return "共定位图"
        if mode == "r":
            return "R识别图"
        if mode == "g":
            return "G识别图"
        return "其他图片"

    def resizeEvent(self, event):
        super().resizeEvent(event)

        if self.current_pixmap is not None and self.view_mode in {"height", "fit"}:
            self.update_image_display()

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
