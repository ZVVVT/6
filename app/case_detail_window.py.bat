from pathlib import Path

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
    QHeaderView,
    QGroupBox,
)

from core.config_manager import ConfigManager
from app.batch_analysis_dialog import BatchAnalysisDialog


class CaseDetailWindow(QWidget):
    start_analysis_requested = Signal(dict)
    report_requested = Signal(dict)

    def __init__(self, database, parent=None):
        super().__init__(parent)

        self.database = database
        self.config = ConfigManager()
        self.config.ensure_default_config()

        self.current_case = None

        self.init_ui()

    def reload_config(self):
        self.config.load()
        self.config.ensure_default_config()
        self.refresh_analysis_table()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(12)

        title_layout = QHBoxLayout()

        self.title_label = QLabel("病例详情")
        self.title_label.setStyleSheet("font-size: 22px; font-weight: bold; color: #1f4e79;")

        self.status_label = QLabel("未选择病例")
        self.status_label.setStyleSheet("color: #666666;")

        title_layout.addWidget(self.title_label)
        title_layout.addStretch()
        title_layout.addWidget(self.status_label)

        main_layout.addLayout(title_layout)

        # -------------------------
        # 基本信息
        # -------------------------
        basic_group = QGroupBox("基本信息")
        basic_layout = QGridLayout(basic_group)
        basic_layout.setHorizontalSpacing(20)
        basic_layout.setVerticalSpacing(8)

        self.case_no_label = QLabel("-")
        self.patient_name_label = QLabel("-")
        self.sample_no_label = QLabel("-")
        self.age_label = QLabel("-")
        self.sex_label = QLabel("-")
        self.occupation_label = QLabel("-")
        self.phone_label = QLabel("-")
        self.test_date_label = QLabel("-")
        self.report_path_label = QLabel("-")
        self.report_path_label.setWordWrap(True)

        self.add_info_row3(
            basic_layout,
            0,
            ("病历号", self.case_no_label),
            ("姓名", self.patient_name_label),
            ("样本号", self.sample_no_label),
        )
        self.add_info_row3(
            basic_layout,
            1,
            ("年龄", self.age_label),
            ("性别", self.sex_label),
            ("职业", self.occupation_label),
        )
        self.add_info_row3(
            basic_layout,
            2,
            ("联系方式", self.phone_label),
            ("检查日期", self.test_date_label),
            ("报告路径", self.report_path_label),
        )

        main_layout.addWidget(basic_group)

        # -------------------------
        # 样本信息
        # -------------------------
        sample_group = QGroupBox("样本信息")
        sample_layout = QGridLayout(sample_group)
        sample_layout.setHorizontalSpacing(20)
        sample_layout.setVerticalSpacing(8)

        self.collect_time_label = QLabel("-")
        self.receive_time_label = QLabel("-")
        self.semen_volume_label = QLabel("-")
        self.ph_label = QLabel("-")
        self.appearance_label = QLabel("-")
        self.color_label = QLabel("-")
        self.liquefaction_time_label = QLabel("-")
        self.liquefaction_status_label = QLabel("-")
        self.agglutination_label = QLabel("-")
        self.viscosity_label = QLabel("-")
        self.collect_method_label = QLabel("-")
        self.abstinence_days_label = QLabel("-")
        self.smell_label = QLabel("-")
        self.test_temperature_label = QLabel("-")
        self.collect_location_label = QLabel("-")
        self.collect_complete_label = QLabel("-")
        self.dead_sperm_label = QLabel("-")

        self.sperm_concentration_label = QLabel("-")
        self.sperm_total_label = QLabel("-")
        self.forward_motility_label = QLabel("-")
        self.total_motility_label = QLabel("-")

        self.add_info_row3(
            sample_layout,
            0,
            ("取样时间", self.collect_time_label),
            ("送检时间", self.receive_time_label),
            ("精液量", self.semen_volume_label),
        )
        self.add_info_row3(
            sample_layout,
            1,
            ("PH值", self.ph_label),
            ("外观", self.appearance_label),
            ("颜色", self.color_label),
        )
        self.add_info_row3(
            sample_layout,
            2,
            ("液化时间", self.liquefaction_time_label),
            ("液化状态", self.liquefaction_status_label),
            ("凝集程度", self.agglutination_label),
        )
        self.add_info_row3(
            sample_layout,
            3,
            ("黏稠度", self.viscosity_label),
            ("取样方式", self.collect_method_label),
            ("禁欲时间", self.abstinence_days_label),
        )
        self.add_info_row3(
            sample_layout,
            4,
            ("气味", self.smell_label),
            ("检测温度", self.test_temperature_label),
            ("取样地点", self.collect_location_label),
        )
        self.add_info_row3(
            sample_layout,
            5,
            ("取样完整", self.collect_complete_label),
            ("死精子症", self.dead_sperm_label),
            ("精子浓度", self.sperm_concentration_label),
        )
        self.add_info_row3(
            sample_layout,
            6,
            ("精子总数", self.sperm_total_label),
            ("前向运动", self.forward_motility_label),
            ("总活力", self.total_motility_label),
        )

        main_layout.addWidget(sample_group)

        # -------------------------
        # 操作按钮
        # -------------------------
        button_layout = QHBoxLayout()

        self.btn_refresh = QPushButton("刷新详情")
        self.btn_start_analysis = QPushButton("开始蛋白分析")
        self.btn_batch_analysis = QPushButton("批量蛋白分析")
        self.btn_report = QPushButton("进入报告管理")
        self.btn_open_report = QPushButton("打开报告")
        self.btn_open_workspace = QPushButton("打开病例工作目录")

        button_layout.addWidget(self.btn_refresh)
        button_layout.addWidget(self.btn_start_analysis)
        button_layout.addWidget(self.btn_batch_analysis)
        button_layout.addWidget(self.btn_report)
        button_layout.addWidget(self.btn_open_report)
        button_layout.addWidget(self.btn_open_workspace)
        button_layout.addStretch()

        main_layout.addLayout(button_layout)

        # -------------------------
        # 蛋白检测状态总览
        # -------------------------
        analysis_group = QGroupBox("蛋白检测状态总览")
        analysis_layout = QVBoxLayout(analysis_group)

        self.analysis_summary_label = QLabel("未选择病例")
        self.analysis_summary_label.setStyleSheet("color: #666666;")
        analysis_layout.addWidget(self.analysis_summary_label)

        self.analysis_table = QTableWidget()
        self.analysis_table.setColumnCount(10)
        self.analysis_table.setHorizontalHeaderLabels([
            "蛋白名称",
            "表达部位",
            "检测状态",
            "视野数",
            "精子总数",
            "共定位数",
            "标定率(%)",
            "荧光强度",
            "分析时间",
            "输出目录",
        ])

        self.analysis_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.analysis_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.analysis_table.setAlternatingRowColors(True)
        self.analysis_table.verticalHeader().setVisible(False)

        header = self.analysis_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(9, QHeaderView.Stretch)

        analysis_layout.addWidget(self.analysis_table)

        main_layout.addWidget(analysis_group, 1)

        self.set_common_style()

        self.btn_refresh.clicked.connect(self.refresh_detail)
        self.btn_start_analysis.clicked.connect(self.start_analysis)
        self.btn_batch_analysis.clicked.connect(self.open_batch_analysis)
        self.btn_report.clicked.connect(self.go_report)
        self.btn_open_report.clicked.connect(self.open_report)
        self.btn_open_workspace.clicked.connect(self.open_workspace)

    def set_common_style(self):
        self.setStyleSheet("""
            QWidget {
                font-family: Microsoft YaHei;
                font-size: 13px;
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
            QPushButton {
                min-height: 30px;
                min-width: 100px;
            }
            QTableWidget {
                background-color: #ffffff;
                alternate-background-color: #f7f9fc;
                gridline-color: #d9e2ef;
            }
        """)

    def add_info_row3(self, layout, row, item1, item2, item3):
        label1, widget1 = item1
        label2, widget2 = item2
        label3, widget3 = item3

        layout.addWidget(self.make_name_label(label1), row, 0)
        layout.addWidget(widget1, row, 1)
        layout.addWidget(self.make_name_label(label2), row, 2)
        layout.addWidget(widget2, row, 3)
        layout.addWidget(self.make_name_label(label3), row, 4)
        layout.addWidget(widget3, row, 5)

        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)
        layout.setColumnStretch(5, 1)

    def make_name_label(self, text):
        label = QLabel(f"{text}：")
        label.setStyleSheet("color: #555555;")
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return label

    def set_case(self, case_data: dict):
        self.current_case = case_data
        self.refresh_detail()

    def refresh_detail(self):
        if not self.current_case:
            self.clear_detail()
            return

        case_id = self.current_case.get("id")

        if case_id:
            fresh_case = self.database.get_case(case_id)
            if fresh_case:
                self.current_case = fresh_case

        case = self.current_case

        self.status_label.setText(f"当前病例：{case.get('case_no', '')} - {case.get('patient_name', '')}")

        self.case_no_label.setText(self.v(case.get("case_no")))
        self.patient_name_label.setText(self.v(case.get("patient_name")))
        self.sample_no_label.setText(self.v(case.get("sample_no")))
        self.age_label.setText(self.v(case.get("age")))
        self.sex_label.setText(self.v(case.get("sex")))
        self.occupation_label.setText(self.v(case.get("occupation")))
        self.phone_label.setText(self.v(case.get("phone")))
        self.test_date_label.setText(self.v(case.get("test_date")))
        self.report_path_label.setText(self.v(case.get("report_path")))

        self.collect_time_label.setText(self.v(case.get("collect_time")))
        self.receive_time_label.setText(self.v(case.get("receive_time")))
        self.semen_volume_label.setText(self.v(case.get("semen_volume")))
        self.ph_label.setText(self.v(case.get("ph_value")))
        self.appearance_label.setText(self.v(case.get("appearance")))
        self.color_label.setText(self.v(case.get("color")))
        self.liquefaction_time_label.setText(self.v(case.get("liquefaction_time")))
        self.liquefaction_status_label.setText(self.v(case.get("liquefaction_status")))
        self.agglutination_label.setText(self.v(case.get("agglutination")))
        self.viscosity_label.setText(self.v(case.get("viscosity")))
        self.collect_method_label.setText(self.v(case.get("collect_method")))
        self.abstinence_days_label.setText(self.v(case.get("abstinence_days")))
        self.smell_label.setText(self.v(case.get("smell")))
        self.test_temperature_label.setText(self.v(case.get("test_temperature")))
        self.collect_location_label.setText(self.v(case.get("collect_location")))
        self.collect_complete_label.setText(self.v(case.get("collect_complete")))
        self.dead_sperm_label.setText(self.v(case.get("dead_sperm")))

        self.sperm_concentration_label.setText(self.v(case.get("sperm_concentration")))
        self.sperm_total_label.setText(self.v(case.get("sperm_total")))
        self.forward_motility_label.setText(self.v(case.get("forward_motility")))
        self.total_motility_label.setText(self.v(case.get("total_motility")))

        self.refresh_analysis_table()

    def clear_detail(self):
        self.status_label.setText("未选择病例")
        self.analysis_summary_label.setText("未选择病例")
        self.analysis_table.setRowCount(0)

    def refresh_analysis_table(self):
        if not self.current_case:
            self.analysis_summary_label.setText("未选择病例")
            self.analysis_table.setRowCount(0)
            return

        case_id = self.current_case.get("id")
        if not case_id:
            self.analysis_summary_label.setText("当前病例缺少数据库 ID")
            self.analysis_table.setRowCount(0)
            return

        try:
            analysis_rows = self.database.get_protein_analysis_by_case(case_id)
        except Exception as e:
            self.analysis_summary_label.setText(f"读取蛋白分析结果失败：{e}")
            self.analysis_table.setRowCount(0)
            return

        analysis_map = self.build_analysis_map(analysis_rows)
        protein_items = self.config.get_protein_items()

        self.analysis_table.setRowCount(len(protein_items))

        completed_count = 0

        for row_index, protein in enumerate(protein_items):
            key = protein.get("key", "")
            name = protein.get("name", key)
            part = protein.get("part", "")

            analysis = analysis_map.get(key)

            if analysis:
                completed_count += 1

                values = [
                    name,
                    part,
                    "已完成",
                    analysis.get("total_fields", 0),
                    analysis.get("total_sperm_count", 0),
                    analysis.get("positive_count", 0),
                    self.fmt(analysis.get("expression_rate", 0)),
                    self.fmt(analysis.get("mean_intensity", 0)),
                    analysis.get("created_at", ""),
                    analysis.get("output_folder", ""),
                ]
                status_color = Qt.darkGreen
            else:
                values = [
                    name,
                    part,
                    "未检测",
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                ]
                status_color = Qt.gray

            for col_index, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)

                if col_index == 2:
                    item.setForeground(status_color)

                self.analysis_table.setItem(row_index, col_index, item)

        total_count = len(protein_items)
        uncompleted_count = max(total_count - completed_count, 0)

        self.analysis_summary_label.setText(
            f"蛋白检测进度：已完成 {completed_count} / {total_count}，未检测 {uncompleted_count}"
        )

    def build_analysis_map(self, analysis_rows):
        analysis_map = {}

        for row in analysis_rows:
            protein_name = str(row.get("protein_name", "") or "").strip()

            if not protein_name:
                continue

            protein_key = self.config.normalize_protein_key(protein_name)

            if not protein_key:
                continue

            # 如果以后出现同一蛋白多条记录，优先保留 id 最大的新记录
            old_row = analysis_map.get(protein_key)
            if old_row is None:
                analysis_map[protein_key] = row
                continue

            try:
                old_id = int(old_row.get("id", 0))
                new_id = int(row.get("id", 0))
                if new_id >= old_id:
                    analysis_map[protein_key] = row
            except Exception:
                analysis_map[protein_key] = row

        return analysis_map

    def start_analysis(self):
        if not self.current_case:
            QMessageBox.information(self, "提示", "请先在病例管理中选择病例。")
            return

        self.start_analysis_requested.emit(self.current_case)

    def open_batch_analysis(self):
        if not self.current_case:
            QMessageBox.information(self, "提示", "请先在病例管理中选择病例。")
            return

        dialog = BatchAnalysisDialog(self.database, self.current_case, self)
        dialog.batch_finished.connect(self.refresh_detail)
        dialog.exec()
        self.refresh_detail()

    def go_report(self):
        if not self.current_case:
            QMessageBox.information(self, "提示", "请先在病例管理中选择病例。")
            return

        self.report_requested.emit(self.current_case)

    def open_report(self):
        if not self.current_case:
            QMessageBox.information(self, "提示", "请先在病例管理中选择病例。")
            return

        report_path = self.current_case.get("report_path", "")

        if not report_path:
            QMessageBox.information(self, "提示", "当前病例还没有生成报告。")
            return

        path = Path(report_path)

        if not path.exists():
            QMessageBox.warning(self, "提示", f"报告文件不存在：\n{path}")
            return

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_workspace(self):
        if not self.current_case:
            QMessageBox.information(self, "提示", "请先在病例管理中选择病例。")
            return

        case_no = str(self.current_case.get("case_no", "")).strip()
        if not case_no:
            QMessageBox.warning(self, "提示", "当前病例没有病历号。")
            return

        folder = self.config.get_workspace_root() / case_no
        folder.mkdir(parents=True, exist_ok=True)

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    @staticmethod
    def v(value):
        if value is None or value == "":
            return "-"
        return str(value)

    @staticmethod
    def fmt(value):
        try:
            return f"{float(value):.2f}"
        except Exception:
            return str(value)