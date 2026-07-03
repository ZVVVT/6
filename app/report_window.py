from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
    QHeaderView,
    QGroupBox,
)

from core.config_manager import ConfigManager
from core.report_generator import ReportGenerator


class ReportWindow(QWidget):
    def __init__(self, database, parent=None):
        super().__init__(parent)

        self.database = database
        self.config = ConfigManager()
        self.current_case = None
        self.current_report_path = ""

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        title_label = QLabel("报告管理")
        title_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        main_layout.addWidget(title_label)

        case_group = QGroupBox("当前病例")
        case_layout = QFormLayout(case_group)

        self.case_no_label = QLabel("未选择")
        self.patient_name_label = QLabel("-")
        self.sample_no_label = QLabel("-")
        self.test_date_label = QLabel("-")
        self.report_path_label = QLabel("-")
        self.report_path_label.setWordWrap(True)

        case_layout.addRow("病例编号：", self.case_no_label)
        case_layout.addRow("姓名：", self.patient_name_label)
        case_layout.addRow("样本编号：", self.sample_no_label)
        case_layout.addRow("检测日期：", self.test_date_label)
        case_layout.addRow("报告路径：", self.report_path_label)

        main_layout.addWidget(case_group)

        btn_layout = QHBoxLayout()

        self.btn_refresh = QPushButton("刷新分析结果")
        self.btn_generate = QPushButton("生成 PDF 报告")
        self.btn_open_report = QPushButton("打开报告")
        self.btn_open_report_dir = QPushButton("打开报告目录")

        btn_layout.addWidget(self.btn_refresh)
        btn_layout.addWidget(self.btn_generate)
        btn_layout.addWidget(self.btn_open_report)
        btn_layout.addWidget(self.btn_open_report_dir)
        btn_layout.addStretch()

        main_layout.addLayout(btn_layout)

        self.analysis_table = QTableWidget()
        self.analysis_table.setColumnCount(9)
        self.analysis_table.setHorizontalHeaderLabels([
            "蛋白名称",
            "表达部位",
            "视野数",
            "精子总数",
            "共定位数",
            "标定率(%)",
            "荧光强度",
            "状态",
            "分析时间",
        ])

        self.analysis_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.analysis_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.analysis_table.setAlternatingRowColors(True)
        self.analysis_table.verticalHeader().setVisible(False)

        header = self.analysis_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)

        main_layout.addWidget(self.analysis_table, 1)

        self.info_label = QLabel("提示：请先在病例管理中双击选择病例，再生成报告。")
        self.info_label.setStyleSheet("color: #666666;")
        main_layout.addWidget(self.info_label)

        self.btn_refresh.clicked.connect(self.refresh_analysis_results)
        self.btn_generate.clicked.connect(self.generate_report)
        self.btn_open_report.clicked.connect(self.open_report)
        self.btn_open_report_dir.clicked.connect(self.open_report_dir)

    def reload_config(self):
        self.config.load()
        self.config.ensure_default_config()

        if self.current_case:
            case_id = self.current_case.get("id")
            if case_id:
                fresh_case = self.database.get_case(case_id)
                if fresh_case:
                    self.current_case = fresh_case
                    self.current_report_path = str(fresh_case.get("report_path", "") or "")
                    self.report_path_label.setText(self.current_report_path or "-")

        self.refresh_analysis_results()

    def set_case(self, case_data: dict):
        self.current_case = case_data

        self.case_no_label.setText(str(case_data.get("case_no", "")))
        self.patient_name_label.setText(str(case_data.get("patient_name", "")))
        self.sample_no_label.setText(str(case_data.get("sample_no", "")))
        self.test_date_label.setText(str(case_data.get("test_date", "")))

        self.current_report_path = str(case_data.get("report_path", "") or "")
        self.report_path_label.setText(self.current_report_path or "-")

        self.refresh_analysis_results()

    def refresh_analysis_results(self):
        """刷新报告管理页分析结果。

        和病例详情页保持一致：
        1. 固定按系统设置中的蛋白顺序显示所有蛋白；
        2. 已分析的蛋白显示数据库结果；
        3. 未分析的蛋白也占位显示“未检测”，避免报告页与病例详情页行数不一致。
        """
        self.analysis_table.setRowCount(0)

        if not self.current_case:
            self.info_label.setText("请先在病例管理中双击选择病例。")
            return

        case_id = self.current_case.get("id")
        if not case_id:
            self.info_label.setText("当前病例缺少数据库 ID。")
            return

        try:
            rows = self.database.get_protein_analysis_by_case(case_id)
        except Exception as e:
            self.info_label.setText(f"读取分析结果失败：{e}")
            return

        display_rows = self.build_report_display_rows(rows)

        self.analysis_table.setRowCount(len(display_rows))

        done_count = 0

        for row_index, row in enumerate(display_rows):
            is_done = bool(row.get("has_result"))

            if is_done:
                done_count += 1
                values = [
                    row.get("protein_name", ""),
                    row.get("protein_part", ""),
                    row.get("total_fields", 0),
                    row.get("total_sperm_count", 0),
                    row.get("positive_count", 0),
                    self._fmt(row.get("expression_rate", 0)),
                    self._fmt(row.get("mean_intensity", 0)),
                    row.get("status", "") or "完成",
                    row.get("created_at", ""),
                ]
            else:
                values = [
                    row.get("protein_name", ""),
                    row.get("protein_part", ""),
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    "未检测",
                    "-",
                ]

            for col, value in enumerate(values):
                self.analysis_table.setItem(row_index, col, QTableWidgetItem(str(value)))

        total_count = len(display_rows)
        pending_count = max(total_count - done_count, 0)
        self.info_label.setText(
            f"当前病例蛋白检测进度：已完成 {done_count} / {total_count}，未检测 {pending_count}"
        )

    def sort_analysis_rows_by_config(self, rows):
        """按照系统设置中的蛋白顺序排序报告管理页结果。

        数据库 get_protein_analysis_by_case() 默认按 id DESC 返回，
        这会导致报告管理页顺序和病例详情页、PDF报告顺序不一致。
        这里按 config.ini 的 ProteinOrder / ProteinNames 统一排序。
        """
        rows = list(rows or [])

        protein_items = []
        try:
            protein_items = self.config.get_protein_items()
        except Exception:
            protein_items = []

        order_map = {}
        name_to_key = {}

        for index, item in enumerate(protein_items):
            key = str(item.get("key", "") or "").strip()
            name = str(item.get("name", "") or "").strip()

            if key:
                order_map[key] = index
                name_to_key[key] = key

            if name:
                name_to_key[name] = key or name
                if name not in order_map:
                    order_map[name] = index

        def row_sort_key(row):
            protein_name = str(row.get("protein_name", "") or "").strip()

            try:
                protein_key = self.config.normalize_protein_key(protein_name)
            except Exception:
                protein_key = name_to_key.get(protein_name, protein_name)

            index = order_map.get(protein_key, order_map.get(protein_name, 9999))
            created_at = str(row.get("created_at", "") or "")

            return (index, created_at, protein_name)

        return sorted(rows, key=row_sort_key)


    def build_report_display_rows(self, analysis_rows):
        """构建报告管理页显示行。

        返回值固定覆盖系统设置中的所有蛋白。
        已分析蛋白合并数据库结果；未分析蛋白生成占位行。
        """
        analysis_rows = list(analysis_rows or [])
        analysis_map = {}

        for row in analysis_rows:
            protein_name = str(row.get("protein_name", "") or "").strip()
            if not protein_name:
                continue

            protein_key = self.resolve_protein_key(protein_name)
            if protein_key:
                analysis_map[protein_key] = row
            analysis_map[protein_name] = row

        display_rows = []

        try:
            protein_items = self.config.get_protein_items()
        except Exception:
            protein_items = []

        for item in protein_items:
            key = str(item.get("key", "") or "").strip()
            name = str(item.get("name", key) or key).strip()
            part = str(item.get("part", "") or "").strip()

            row = None
            if key:
                row = analysis_map.get(key)
            if row is None and name:
                row = analysis_map.get(name)

            if row:
                display_row = dict(row)
                display_row["has_result"] = True
                display_row["protein_name"] = display_row.get("protein_name", name) or name
                display_row["protein_part"] = display_row.get("protein_part", part) or part
            else:
                display_row = {
                    "has_result": False,
                    "protein_key": key,
                    "protein_name": name,
                    "protein_part": part,
                    "status": "未检测",
                }

            display_rows.append(display_row)

        if display_rows:
            return display_rows

        # 兜底：如果配置异常没有蛋白列表，就显示数据库已有结果。
        rows = self.sort_analysis_rows_by_config(analysis_rows)
        for row in rows:
            row = dict(row)
            row["has_result"] = True
            display_rows.append(row)

        return display_rows

    def resolve_protein_key(self, protein_name: str) -> str:
        """把数据库中的 protein_name 尽量映射回 protein1/protein2 等内部编号。"""
        protein_name = str(protein_name or "").strip()

        if not protein_name:
            return ""

        try:
            return self.config.normalize_protein_key(protein_name)
        except Exception:
            pass

        try:
            for item in self.config.get_protein_items():
                key = str(item.get("key", "") or "").strip()
                name = str(item.get("name", "") or "").strip()

                if protein_name == key or protein_name == name:
                    return key
        except Exception:
            pass

        return protein_name


    def generate_report(self):
        if not self.current_case:
            QMessageBox.information(self, "提示", "请先在病例管理中双击选择病例。")
            return

        case_id = self.current_case.get("id")
        analysis_rows = self.database.get_protein_analysis_by_case(case_id)

        if not analysis_rows:
            QMessageBox.warning(self, "提示", "当前病例暂无蛋白分析结果，无法生成报告。")
            return

        report_dir = self.config.get_report_dir()
        logo_path = self.config.get("Report", "logo_path", "")

        try:
            generator = ReportGenerator(
                database=self.database,
                report_dir=str(report_dir),
                logo_path=logo_path,
            )
            report_path = generator.generate_case_report(case_id)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"生成报告失败：\n{e}")
            return

        self.current_report_path = report_path
        self.report_path_label.setText(report_path)

        # 重新读取病例，保证 report_path 更新
        refreshed_case = self.database.get_case(case_id)
        if refreshed_case:
            self.current_case = refreshed_case

        QMessageBox.information(self, "成功", f"报告生成成功：\n{report_path}")

    def open_report(self):
        if not self.current_report_path:
            QMessageBox.information(self, "提示", "当前病例还没有生成报告。")
            return

        report_path = Path(self.current_report_path)

        if not report_path.exists():
            QMessageBox.warning(self, "提示", f"报告文件不存在：\n{report_path}")
            return

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(report_path)))

    def open_report_dir(self):
        if self.current_report_path:
            folder = Path(self.current_report_path).parent
        else:
            folder = self.config.get_report_dir()

        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    @staticmethod
    def _fmt(value):
        try:
            return f"{float(value):.2f}"
        except Exception:
            return str(value)