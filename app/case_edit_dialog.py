from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLineEdit,
    QSpinBox,
    QDateEdit,
    QTimeEdit,
    QTextEdit,
    QDialogButtonBox,
    QMessageBox,
    QComboBox,
    QCheckBox,
    QGroupBox,
    QLabel,
    QWidget,
)


class CaseEditDialog(QDialog):
    def __init__(self, parent=None, case_data=None):
        super().__init__(parent)

        self.case_data = case_data or {}

        self.setWindowTitle("创建病历" if not case_data else "修改病历")
        self.resize(1080, 680)

        self.init_ui()
        self.load_data()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 12, 14, 12)
        main_layout.setSpacing(10)

        title_label = QLabel("创建病历" if not self.case_data else "修改病历")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #2f54eb;")
        title_label.setAlignment(QtAlignCenter())
        main_layout.addWidget(title_label)

        self.basic_group = QGroupBox("基本信息")
        self.basic_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        basic_layout = QGridLayout(self.basic_group)
        basic_layout.setHorizontalSpacing(16)
        basic_layout.setVerticalSpacing(8)

        self.case_no_edit = QLineEdit()
        self.case_no_edit.setPlaceholderText("病历号")

        self.patient_name_edit = QLineEdit()
        self.patient_name_edit.setPlaceholderText("姓名")

        self.sample_no_edit = QLineEdit()
        self.sample_no_edit.setPlaceholderText("样本号")

        self.age_spin = QSpinBox()
        self.age_spin.setRange(0, 120)
        self.age_spin.setValue(30)

        self.sex_combo = self.create_combo(["男", "女", ""])
        self.occupation_combo = self.create_combo(["医学", "计算机", "经济学", "法学", "工学", "农学", ""])
        self.phone_edit = QLineEdit()
        self.phone_edit.setPlaceholderText("联系方式")


        self.add_row3(
            basic_layout,
            0,
            ("病历号", self.case_no_edit),
            ("姓名", self.patient_name_edit),
            ("样本号", self.sample_no_edit),
        )
        self.add_row3(
            basic_layout,
            1,
            ("年龄", self.age_spin),
            ("职业", self.occupation_combo),
            ("联系方式", self.phone_edit),
        )


        main_layout.addWidget(self.basic_group)

        self.sample_group = QGroupBox("样本信息")
        self.sample_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        sample_layout = QGridLayout(self.sample_group)
        sample_layout.setHorizontalSpacing(16)
        sample_layout.setVerticalSpacing(8)

        self.test_date_edit = QDateEdit()
        self.test_date_edit.setCalendarPopup(True)
        self.test_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.test_date_edit.setDate(QDate.currentDate())

        self.collect_time_edit = QTimeEdit()
        self.collect_time_edit.setDisplayFormat("HH:mm:ss")
        self.collect_time_edit.setTime(QTime.currentTime())

        self.receive_time_edit = QTimeEdit()
        self.receive_time_edit.setDisplayFormat("HH:mm:ss")
        self.receive_time_edit.setTime(QTime.currentTime())

        self.semen_volume_combo = self.create_combo(["1.0", "2.0", "3.0", "4.0", "5.0", "6.0", ""])
        self.ph_combo = self.create_combo([
            "6.0", "6.5", "7.0", "7.1", "7.2", "7.3", "7.4", "7.5", "7.6", "7.7", ""
        ])

        self.appearance_combo = self.create_combo(["正常", "不正常", "血精", "脓精", ""])
        self.color_combo = self.create_combo([
            "灰白色", "乳白色", "黄白色", "灰色", "白色", "灰黄色",
            "黄色", "透明", "浅黄色", "红色", "黄褐色", ""
        ])

        self.liquefaction_time_combo = self.create_combo(["10", "20", "30", "40", "50", "60", "70", ""])
        self.liquefaction_status_combo = self.create_combo(["完全液化", "不液化", "液化不良", ""])
        self.agglutination_combo = self.create_combo([
            "1级(<10)", "2级(<50)", "3级(>50)", "4级(所有精子凝集)", ""
        ])
        self.viscosity_combo = self.create_combo(["正常", "+", "++", "+++", ""])

        self.collect_method_combo = self.create_combo(["射精后尿液", "手淫", "性交", ""])
        self.abstinence_days_combo = self.create_combo([
            "1天", "2天", "3天", "4天", "5天", "6天", "7天", "不详", ""
        ])
        self.smell_combo = self.create_combo(["正常", "不正常", ""])
        self.test_temperature_combo = self.create_combo(["室温", "35", "37", ""])
        self.collect_location_combo = self.create_combo(["医院", "住所", ""])
        self.collect_complete_combo = self.create_combo(["完整", "不完整", ""])
        self.dead_sperm_combo = self.create_combo(["否", "是", ""])

        self.sperm_concentration_edit = QLineEdit()
        self.sperm_total_edit = QLineEdit()
        self.forward_motility_edit = QLineEdit()
        self.total_motility_edit = QLineEdit()

        self.add_row3(
            sample_layout,
            0,
            ("检查日期", self.test_date_edit),
            ("取样时间", self.collect_time_edit),
            ("送检时间", self.receive_time_edit),
        )
        self.add_row3(
            sample_layout,
            1,
            ("精液量", self.semen_volume_combo),
            ("PH值", self.ph_combo),
            ("外观", self.appearance_combo),
        )
        self.add_row3(
            sample_layout,
            2,
            ("颜色", self.color_combo),
            ("液化时间", self.liquefaction_time_combo),
            ("液化状态", self.liquefaction_status_combo),
        )
        self.add_row3(
            sample_layout,
            3,
            ("凝集程度", self.agglutination_combo),
            ("黏稠度", self.viscosity_combo),
            ("禁欲时间", self.abstinence_days_combo),
        )
        self.add_row3(
            sample_layout,
            4,
            ("取样方式", self.collect_method_combo),
            ("气味", self.smell_combo),
            ("检测温度", self.test_temperature_combo),
        )
        self.add_row3(
            sample_layout,
            5,
            ("取样地点", self.collect_location_combo),
            ("取样完整", self.collect_complete_combo),
            ("死精子症", self.dead_sperm_combo),
        )
        self.add_row4(
            sample_layout,
            6,
            ("精子浓度", self.sperm_concentration_edit),
            ("精子总数", self.sperm_total_edit),
            ("前向运动", self.forward_motility_edit),
            ("总活力", self.total_motility_edit),
        )

        main_layout.addWidget(self.sample_group)

        conclusion_group = QGroupBox("结论")
        conclusion_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        conclusion_layout = QHBoxLayout(conclusion_group)

        self.conclusion_normal_check = QCheckBox("正常")
        self.conclusion_oligo_check = QCheckBox("少精子症")
        self.conclusion_astheno_check = QCheckBox("弱精子症")
        self.conclusion_oligoastheno_check = QCheckBox("少弱精子症")
        self.conclusion_necro_check = QCheckBox("坏死精子症")

        conclusion_layout.addWidget(self.conclusion_normal_check)
        conclusion_layout.addWidget(self.conclusion_oligo_check)
        conclusion_layout.addWidget(self.conclusion_astheno_check)
        conclusion_layout.addWidget(self.conclusion_oligoastheno_check)
        conclusion_layout.addWidget(self.conclusion_necro_check)
        conclusion_layout.addStretch()

        main_layout.addWidget(conclusion_group)

        bottom_layout = QHBoxLayout()

        doctor_group = QGroupBox("医师信息")
        doctor_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        doctor_layout = QGridLayout(doctor_group)
        doctor_layout.setHorizontalSpacing(12)
        doctor_layout.setVerticalSpacing(8)

        self.checker_combo = self.create_combo([""])
        self.reviewer_combo = self.create_combo([""])
        self.doctor_combo = self.create_combo([""])
        self.department_combo = self.create_combo([""])

        self.add_row2(
            doctor_layout,
            0,
            ("检测者", self.checker_combo),
            ("审核者", self.reviewer_combo),
        )
        self.add_row2(
            doctor_layout,
            1,
            ("送检医生", self.doctor_combo),
            ("送检科室", self.department_combo),
        )

        bottom_layout.addWidget(doctor_group, 1)

        remark_group = QGroupBox("备注")
        remark_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        remark_layout = QVBoxLayout(remark_group)

        self.remark_edit = QTextEdit()
        self.remark_edit.setPlaceholderText("备注信息，可为空")
        self.remark_edit.setMaximumHeight(90)

        remark_layout.addWidget(self.remark_edit)
        bottom_layout.addWidget(remark_group, 1)

        main_layout.addLayout(bottom_layout)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.button(QDialogButtonBox.Ok).setText("提交")
        self.button_box.button(QDialogButtonBox.Cancel).setText("取消")
        self.button_box.accepted.connect(self.on_accept)
        self.button_box.rejected.connect(self.reject)

        main_layout.addWidget(self.button_box)

        self.set_common_style()

    def set_common_style(self):
        """
        只优化控件外观，不改变原有布局。

        注意：
        之前页面错乱的根本原因，是为了美化下拉/日期图标时同时改了弹窗整体布局、
        分组边距和控件高度，导致 680px 高度下“样本信息”区域被压缩重叠。
        这里保留原来的紧凑布局参数，只替换下拉、日期、时间、数字框右侧图标样式。
        """
        icon_dir = Path(__file__).resolve().parent.parent / "assets" / "icons"

        def icon_url(name: str) -> str:
            path = icon_dir / name
            if not path.exists():
                return ""
            return path.as_posix()

        down_icon = icon_url("form_chevron_down.svg")
        up_icon = icon_url("form_chevron_up.svg")
        calendar_icon = icon_url("form_calendar.svg")

        self.setStyleSheet(f"""
            QDialog {{
                background-color: #f7f9fc;
                font-size: 13px;
            }}
            QGroupBox {{
                border: 1px solid #d9e2ef;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 12px;
                background-color: #ffffff;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: #1f4e79;
            }}
            QLabel {{
                color: #333333;
                background: transparent;
            }}
            QLineEdit, QComboBox, QSpinBox, QDateEdit, QTimeEdit {{
                min-height: 24px;
                max-height: 26px;
                border: 1px solid #cfd8e3;
                border-radius: 4px;
                padding-left: 6px;
                padding-right: 24px;
                background-color: #ffffff;
                color: #1f2937;
                selection-background-color: #dbeafe;
            }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus, QTimeEdit:focus {{
                border: 1px solid #2f6fed;
                background-color: #ffffff;
            }}
            QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDateEdit:disabled, QTimeEdit:disabled {{
                background-color: #f3f6fb;
                color: #9aa6b2;
            }}

            QComboBox {{
                padding-right: 26px;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 24px;
                border-left: none;
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
                background: transparent;
            }}
            QComboBox::drop-down:hover {{
                background-color: #eef5ff;
            }}
            QComboBox::down-arrow {{
                image: url("{down_icon}");
                width: 10px;
                height: 10px;
            }}
            QComboBox QAbstractItemView {{
                background-color: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 4px;
                padding: 2px;
                selection-background-color: #eaf2ff;
                selection-color: #1f2937;
                outline: none;
            }}

            QDateEdit, QTimeEdit, QSpinBox {{
                padding-right: 24px;
            }}
            QDateEdit::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 24px;
                border-left: none;
                background: transparent;
            }}
            QDateEdit::down-arrow {{
                image: url("{calendar_icon}");
                width: 13px;
                height: 13px;
            }}
            QDateEdit::drop-down:hover {{
                background-color: #eef5ff;
            }}

            QSpinBox::up-button, QTimeEdit::up-button {{
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 22px;
                border-left: none;
                border-bottom: none;
                background: transparent;
            }}
            QSpinBox::down-button, QTimeEdit::down-button {{
                subcontrol-origin: border;
                subcontrol-position: bottom right;
                width: 22px;
                border-left: none;
                border-top: none;
                background: transparent;
            }}
            QSpinBox::up-button:hover, QTimeEdit::up-button:hover,
            QSpinBox::down-button:hover, QTimeEdit::down-button:hover {{
                background-color: #eef5ff;
            }}
            QSpinBox::up-arrow, QTimeEdit::up-arrow {{
                image: url("{up_icon}");
                width: 8px;
                height: 8px;
            }}
            QSpinBox::down-arrow, QTimeEdit::down-arrow {{
                image: url("{down_icon}");
                width: 8px;
                height: 8px;
            }}

            QTextEdit {{
                border: 1px solid #cfd8e3;
                border-radius: 4px;
                background-color: #ffffff;
                padding: 6px;
                color: #1f2937;
            }}
            QTextEdit:focus {{
                border: 1px solid #2f6fed;
            }}
            QCheckBox {{
                color: #1f2937;
                spacing: 6px;
                background: transparent;
            }}
            QCheckBox::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid #cfd8e3;
                border-radius: 3px;
                background-color: #ffffff;
            }}
            QCheckBox::indicator:hover {{
                border: 1px solid #2f6fed;
            }}
            QCheckBox::indicator:checked {{
                background-color: #2f6fed;
                border: 1px solid #2f6fed;
            }}
            QPushButton {{
                min-width: 78px;
                min-height: 28px;
                border: 1px solid #cfd8e3;
                border-radius: 5px;
                background-color: #ffffff;
                color: #1f2937;
            }}
            QPushButton:hover {{
                background-color: #f3f7ff;
                border-color: #9bbcf7;
            }}
            QPushButton:pressed {{
                background-color: #eaf2ff;
            }}
        """)

    def create_combo(self, items):
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(items)
        return combo

    def add_row2(self, layout, row, item1, item2):
        label1, widget1 = item1
        label2, widget2 = item2

        layout.addWidget(QLabel(f"{label1}："), row, 0)
        layout.addWidget(widget1, row, 1)
        layout.addWidget(QLabel(f"{label2}："), row, 2)
        layout.addWidget(widget2, row, 3)

    def add_row3(self, layout, row, item1, item2, item3):
        label1, widget1 = item1
        label2, widget2 = item2
        label3, widget3 = item3

        layout.addWidget(QLabel(f"{label1}："), row, 0)
        layout.addWidget(widget1, row, 1)
        layout.addWidget(QLabel(f"{label2}："), row, 2)
        layout.addWidget(widget2, row, 3)
        layout.addWidget(QLabel(f"{label3}："), row, 4)
        layout.addWidget(widget3, row, 5)

        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)
        layout.setColumnStretch(5, 1)

    def add_row4(self, layout, row, item1, item2, item3, item4):
        label1, widget1 = item1
        label2, widget2 = item2
        label3, widget3 = item3
        label4, widget4 = item4

        layout.addWidget(QLabel(f"{label1}："), row, 0)
        layout.addWidget(widget1, row, 1)
        layout.addWidget(QLabel(f"{label2}："), row, 2)
        layout.addWidget(widget2, row, 3)
        layout.addWidget(QLabel(f"{label3}："), row, 4)
        layout.addWidget(widget3, row, 5)
        layout.addWidget(QLabel(f"{label4}："), row, 6)
        layout.addWidget(widget4, row, 7)

        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)
        layout.setColumnStretch(5, 1)
        layout.setColumnStretch(7, 1)

    def load_data(self):
        if not self.case_data:
            now_text = datetime.now().strftime("%Y%m%d%H%M%S")
            self.case_no_edit.setText(f"CASE{now_text}")
            self.sample_no_edit.setText(datetime.now().strftime("%Y%m%d001"))
            self.sex_combo.setCurrentText("男")
            return

        self.case_no_edit.setText(str(self.case_data.get("case_no", "")))
        self.patient_name_edit.setText(str(self.case_data.get("patient_name", "")))
        self.sample_no_edit.setText(str(self.case_data.get("sample_no", "")))

        age = self.case_data.get("age", 0)
        try:
            self.age_spin.setValue(int(age))
        except (TypeError, ValueError):
            self.age_spin.setValue(0)

        self.set_combo_text(self.sex_combo, self.case_data.get("sex", "男"))
        self.set_combo_text(self.occupation_combo, self.case_data.get("occupation", ""))
        self.phone_edit.setText(str(self.case_data.get("phone", "")))


        test_date = self.case_data.get("test_date", "")
        date_obj = QDate.fromString(str(test_date), "yyyy-MM-dd")
        if date_obj.isValid():
            self.test_date_edit.setDate(date_obj)

        self.set_time_value(self.collect_time_edit, self.case_data.get("collect_time", ""))
        self.set_time_value(self.receive_time_edit, self.case_data.get("receive_time", ""))

        self.set_combo_text(self.semen_volume_combo, self.case_data.get("semen_volume", ""))
        self.set_combo_text(self.ph_combo, self.case_data.get("ph_value", ""))
        self.set_combo_text(self.appearance_combo, self.case_data.get("appearance", ""))
        self.set_combo_text(self.color_combo, self.case_data.get("color", ""))
        self.set_combo_text(self.liquefaction_time_combo, self.case_data.get("liquefaction_time", ""))
        self.set_combo_text(self.liquefaction_status_combo, self.case_data.get("liquefaction_status", ""))
        self.set_combo_text(self.agglutination_combo, self.case_data.get("agglutination", ""))
        self.set_combo_text(self.viscosity_combo, self.case_data.get("viscosity", ""))
        self.set_combo_text(self.collect_method_combo, self.case_data.get("collect_method", ""))
        self.set_combo_text(self.abstinence_days_combo, self.case_data.get("abstinence_days", ""))
        self.set_combo_text(self.smell_combo, self.case_data.get("smell", ""))
        self.set_combo_text(self.test_temperature_combo, self.case_data.get("test_temperature", ""))
        self.set_combo_text(self.collect_location_combo, self.case_data.get("collect_location", ""))
        self.set_combo_text(self.collect_complete_combo, self.case_data.get("collect_complete", ""))
        self.set_combo_text(self.dead_sperm_combo, self.case_data.get("dead_sperm", ""))

        self.sperm_concentration_edit.setText(str(self.case_data.get("sperm_concentration", "")))
        self.sperm_total_edit.setText(str(self.case_data.get("sperm_total", "")))
        self.forward_motility_edit.setText(str(self.case_data.get("forward_motility", "")))
        self.total_motility_edit.setText(str(self.case_data.get("total_motility", "")))

        self.conclusion_normal_check.setChecked(self.to_bool(self.case_data.get("conclusion_normal", 0)))
        self.conclusion_oligo_check.setChecked(self.to_bool(self.case_data.get("conclusion_oligo", 0)))
        self.conclusion_astheno_check.setChecked(self.to_bool(self.case_data.get("conclusion_astheno", 0)))
        self.conclusion_oligoastheno_check.setChecked(self.to_bool(self.case_data.get("conclusion_oligoastheno", 0)))
        self.conclusion_necro_check.setChecked(self.to_bool(self.case_data.get("conclusion_necro", 0)))

        self.set_combo_text(self.checker_combo, self.case_data.get("checker", ""))
        self.set_combo_text(self.reviewer_combo, self.case_data.get("reviewer", ""))
        self.set_combo_text(self.doctor_combo, self.case_data.get("doctor", ""))
        self.set_combo_text(self.department_combo, self.case_data.get("department", ""))

        self.remark_edit.setPlainText(str(self.case_data.get("remark", "")))

    def set_combo_text(self, combo: QComboBox, value):
        value = "" if value is None else str(value)

        if value and combo.findText(value) < 0:
            combo.addItem(value)

        combo.setCurrentText(value)

    def set_time_value(self, time_edit: QTimeEdit, value):
        value = "" if value is None else str(value)

        if not value:
            return

        time_obj = QTime.fromString(value, "HH:mm:ss")

        if not time_obj.isValid():
            time_obj = QTime.fromString(value, "HH:mm")

        if time_obj.isValid():
            time_edit.setTime(time_obj)

    def on_accept(self):
        case_no = self.case_no_edit.text().strip()
        patient_name = self.patient_name_edit.text().strip()

        if not case_no:
            QMessageBox.warning(self, "提示", "病历号不能为空。")
            return

        if not patient_name:
            QMessageBox.warning(self, "提示", "姓名不能为空。")
            return

        self.accept()

    def get_data(self):
        return {
            "case_no": self.case_no_edit.text().strip(),
            "patient_name": self.patient_name_edit.text().strip(),
            "sample_no": self.sample_no_edit.text().strip(),
            "age": self.age_spin.value(),
            "sex": self.sex_combo.currentText().strip(),
            "occupation": self.occupation_combo.currentText().strip(),
            "phone": self.phone_edit.text().strip(),

            "test_date": self.test_date_edit.date().toString("yyyy-MM-dd"),
            "collect_time": self.collect_time_edit.time().toString("HH:mm:ss"),
            "receive_time": self.receive_time_edit.time().toString("HH:mm:ss"),

            "semen_volume": self.semen_volume_combo.currentText().strip(),
            "ph_value": self.ph_combo.currentText().strip(),
            "appearance": self.appearance_combo.currentText().strip(),
            "color": self.color_combo.currentText().strip(),
            "liquefaction_time": self.liquefaction_time_combo.currentText().strip(),
            "liquefaction_status": self.liquefaction_status_combo.currentText().strip(),
            "agglutination": self.agglutination_combo.currentText().strip(),
            "viscosity": self.viscosity_combo.currentText().strip(),
            "collect_method": self.collect_method_combo.currentText().strip(),
            "abstinence_days": self.abstinence_days_combo.currentText().strip(),
            "smell": self.smell_combo.currentText().strip(),
            "test_temperature": self.test_temperature_combo.currentText().strip(),
            "collect_location": self.collect_location_combo.currentText().strip(),
            "collect_complete": self.collect_complete_combo.currentText().strip(),
            "dead_sperm": self.dead_sperm_combo.currentText().strip(),

            "sperm_concentration": self.sperm_concentration_edit.text().strip(),
            "sperm_total": self.sperm_total_edit.text().strip(),
            "forward_motility": self.forward_motility_edit.text().strip(),
            "total_motility": self.total_motility_edit.text().strip(),

            "checker": self.checker_combo.currentText().strip(),
            "reviewer": self.reviewer_combo.currentText().strip(),
            "doctor": self.doctor_combo.currentText().strip(),
            "department": self.department_combo.currentText().strip(),

            "conclusion_normal": 1 if self.conclusion_normal_check.isChecked() else 0,
            "conclusion_oligo": 1 if self.conclusion_oligo_check.isChecked() else 0,
            "conclusion_astheno": 1 if self.conclusion_astheno_check.isChecked() else 0,
            "conclusion_oligoastheno": 1 if self.conclusion_oligoastheno_check.isChecked() else 0,
            "conclusion_necro": 1 if self.conclusion_necro_check.isChecked() else 0,

            "remark": self.remark_edit.toPlainText().strip(),
        }

    @staticmethod
    def to_bool(value):
        if isinstance(value, bool):
            return value

        if value in [1, "1", "是", "true", "True", "TRUE"]:
            return True

        return False


def QtAlignCenter():
    from PySide6.QtCore import Qt
    return Qt.AlignCenter