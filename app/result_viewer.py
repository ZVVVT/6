from pathlib import Path

import pandas as pd

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
)

from core.result_parser import ResultParser


class ResultViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.output_dir = None
        self.files = []

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        top_layout = QHBoxLayout()

        self.output_dir_label = QLabel("输出目录：未设置")
        self.btn_refresh = QPushButton("刷新结果")
        self.btn_open_dir = QPushButton("打开输出目录")
        self.btn_open_file = QPushButton("打开选中文件")

        top_layout.addWidget(self.output_dir_label, 1)
        top_layout.addWidget(self.btn_refresh)
        top_layout.addWidget(self.btn_open_dir)
        top_layout.addWidget(self.btn_open_file)

        main_layout.addLayout(top_layout)

        self.summary_label = QLabel("结果统计：-")
        self.summary_label.setStyleSheet("color: #666666;")
        main_layout.addWidget(self.summary_label)

        splitter = QSplitter(Qt.Horizontal)

        self.file_table = QTableWidget()
        self.file_table.setColumnCount(6)
        self.file_table.setHorizontalHeaderLabels([
            "类型",
            "文件名",
            "大小KB",
            "修改时间",
            "后缀",
            "完整路径",
        ])

        self.file_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.file_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.file_table.setAlternatingRowColors(True)
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.setColumnHidden(5, True)

        header = self.file_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        splitter.addWidget(self.file_table)

        self.preview_table = QTableWidget()
        self.preview_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.preview_table.setAlternatingRowColors(True)

        self.preview_label = QLabel("点击左侧结果文件进行预览")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet("color: #666666; border: 1px solid #dddddd;")

        self.preview_container = QWidget()
        preview_layout = QVBoxLayout(self.preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)

        self.preview_title = QLabel("预览")
        self.preview_title.setStyleSheet("font-weight: bold;")

        preview_layout.addWidget(self.preview_title)
        preview_layout.addWidget(self.preview_label, 1)
        preview_layout.addWidget(self.preview_table, 1)

        self.preview_table.hide()

        splitter.addWidget(self.preview_container)
        splitter.setSizes([500, 600])

        main_layout.addWidget(splitter, 1)

        self.btn_refresh.clicked.connect(self.refresh_results)
        self.btn_open_dir.clicked.connect(self.open_output_dir)
        self.btn_open_file.clicked.connect(self.open_selected_file)
        self.file_table.itemSelectionChanged.connect(self.preview_selected_file)

    def set_output_dir(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir_label.setText(f"输出目录：{self.output_dir}")

    def refresh_results(self):
        if not self.output_dir:
            QMessageBox.information(self, "提示", "还没有设置输出目录。")
            return

        parser = ResultParser(str(self.output_dir))
        self.files = parser.scan_files()
        summary = parser.get_summary()

        self.file_table.setRowCount(len(self.files))

        for row_index, item in enumerate(self.files):
            values = [
                item["type"],
                item["name"],
                item["size_kb"],
                item["modified_time"],
                item["suffix"],
                item["path"],
            ]

            for col_index, value in enumerate(values):
                table_item = QTableWidgetItem(str(value))
                table_item.setTextAlignment(Qt.AlignCenter)
                self.file_table.setItem(row_index, col_index, table_item)

        self.summary_label.setText(
            "结果统计："
            f"总文件 {summary['total']} 个；"
            f"图片 {summary['image']} 个；"
            f"表格 {summary['table']} 个；"
            f"日志 {summary['log']} 个；"
            f"PDF {summary['pdf']} 个；"
            f"脚本 {summary['script']} 个；"
            f"其他 {summary['other']} 个"
        )

        if not self.files:
            self.show_text_preview("未扫描到输出文件。请检查 CellProfiler 输出目录。")

    def get_selected_file_path(self):
        selected_rows = self.file_table.selectionModel().selectedRows()

        if not selected_rows:
            return None

        row = selected_rows[0].row()
        item = self.file_table.item(row, 5)

        if item is None:
            return None

        return Path(item.text())

    def preview_selected_file(self):
        file_path = self.get_selected_file_path()

        if not file_path:
            return

        suffix = file_path.suffix.lower()

        if suffix in {".png", ".jpg", ".jpeg", ".bmp"}:
            self.preview_image(file_path)
        elif suffix in {".tif", ".tiff"}:
            self.show_text_preview(
                f"当前文件是 TIFF 图像：\n{file_path}\n\n"
                "第一版暂不直接预览 TIFF，后续可用 tifffile/Pillow 转换显示。"
            )
        elif suffix == ".csv":
            self.preview_csv(file_path)
        elif suffix in {".log", ".txt", ".ps1"}:
            self.preview_text(file_path)
        elif suffix in {".xlsx", ".xls"}:
            self.show_text_preview(
                f"当前文件是 Excel 表格：\n{file_path}\n\n"
                "第一版暂不直接预览 Excel，可点击“打开选中文件”。"
            )
        elif suffix == ".pdf":
            self.show_text_preview(
                f"当前文件是 PDF：\n{file_path}\n\n"
                "可点击“打开选中文件”查看。"
            )
        else:
            self.show_text_preview(f"当前文件暂不支持预览：\n{file_path}")

    def preview_image(self, file_path: Path):
        pixmap = QPixmap(str(file_path))

        if pixmap.isNull():
            self.show_text_preview(f"图片预览失败：\n{file_path}")
            return

        self.preview_table.hide()
        self.preview_label.show()

        scaled_pixmap = pixmap.scaled(
            self.preview_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        self.preview_title.setText(f"图片预览：{file_path.name}")
        self.preview_label.setPixmap(scaled_pixmap)

    def preview_csv(self, file_path: Path):
        try:
            df = pd.read_csv(file_path, encoding="utf-8")
        except UnicodeDecodeError:
            df = pd.read_csv(file_path, encoding="gbk")
        except Exception as e:
            self.show_text_preview(f"CSV 读取失败：\n{file_path}\n\n错误信息：{e}")
            return

        preview_df = df.head(100)

        self.preview_label.hide()
        self.preview_table.show()

        self.preview_title.setText(f"CSV 预览：{file_path.name}，前 {len(preview_df)} 行")

        self.preview_table.clear()
        self.preview_table.setRowCount(len(preview_df))
        self.preview_table.setColumnCount(len(preview_df.columns))
        self.preview_table.setHorizontalHeaderLabels([str(c) for c in preview_df.columns])

        for row_idx, (_, row) in enumerate(preview_df.iterrows()):
            for col_idx, value in enumerate(row):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                self.preview_table.setItem(row_idx, col_idx, item)

        self.preview_table.resizeColumnsToContents()

    def preview_text(self, file_path: Path):
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            self.show_text_preview(f"文本读取失败：\n{file_path}\n\n错误信息：{e}")
            return

        if len(text) > 10000:
            text = text[-10000:]

        self.show_text_preview(text, title=f"文本预览：{file_path.name}")

    def show_text_preview(self, text: str, title: str = "预览"):
        self.preview_table.hide()
        self.preview_label.show()

        self.preview_title.setText(title)
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText(text)

    def open_output_dir(self):
        if not self.output_dir:
            QMessageBox.information(self, "提示", "还没有设置输出目录。")
            return

        if not self.output_dir.exists():
            QMessageBox.warning(self, "提示", f"输出目录不存在：\n{self.output_dir}")
            return

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir)))

    def open_selected_file(self):
        file_path = self.get_selected_file_path()

        if not file_path:
            QMessageBox.information(self, "提示", "请先选择一个结果文件。")
            return

        if not file_path.exists():
            QMessageBox.warning(self, "提示", f"文件不存在：\n{file_path}")
            return

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(file_path)))