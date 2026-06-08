from datetime import datetime

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QDateEdit,
    QTextEdit,
    QDialogButtonBox,
    QMessageBox,
)


class CaseEditDialog(QDialog):
    def __init__(self, parent=None, case_data=None):
        super().__init__(parent)

        self.case_data = case_data or {}
        self.setWindowTitle("新建病例" if not case_data else "编辑病例")
        self.resize(420, 360)

        self.init_ui()
        self.load_data()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        form_layout = QFormLayout()

        self.case_no_edit = QLineEdit()
        self.case_no_edit.setPlaceholderText("例如：CASE20260608001")

        self.patient_name_edit = QLineEdit()
        self.patient_name_edit.setPlaceholderText("请输入姓名")

        self.age_spin = QSpinBox()
        self.age_spin.setRange(0, 120)
        self.age_spin.setValue(30)

        self.sample_no_edit = QLineEdit()
        self.sample_no_edit.setPlaceholderText("例如：SAMPLE001")

        self.test_date_edit = QDateEdit()
        self.test_date_edit.setCalendarPopup(True)
        self.test_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.test_date_edit.setDate(QDate.currentDate())

        self.remark_edit = QTextEdit()
        self.remark_edit.setPlaceholderText("备注信息，可为空")

        form_layout.addRow("病例编号：", self.case_no_edit)
        form_layout.addRow("姓名：", self.patient_name_edit)
        form_layout.addRow("年龄：", self.age_spin)
        form_layout.addRow("样本编号：", self.sample_no_edit)
        form_layout.addRow("检测日期：", self.test_date_edit)
        form_layout.addRow("备注：", self.remark_edit)

        main_layout.addLayout(form_layout)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.button_box.button(QDialogButtonBox.Ok).setText("确定")
        self.button_box.button(QDialogButtonBox.Cancel).setText("取消")

        self.button_box.accepted.connect(self.on_accept)
        self.button_box.rejected.connect(self.reject)

        main_layout.addWidget(self.button_box)

    def load_data(self):
        if not self.case_data:
            # 新建病例时，自动生成一个默认病例编号
            now_text = datetime.now().strftime("%Y%m%d%H%M%S")
            self.case_no_edit.setText(f"CASE{now_text}")
            return

        self.case_no_edit.setText(str(self.case_data.get("case_no", "")))
        self.patient_name_edit.setText(str(self.case_data.get("patient_name", "")))

        age = self.case_data.get("age", 0)
        try:
            self.age_spin.setValue(int(age))
        except (TypeError, ValueError):
            self.age_spin.setValue(0)

        self.sample_no_edit.setText(str(self.case_data.get("sample_no", "")))

        test_date = self.case_data.get("test_date", "")
        date_obj = QDate.fromString(str(test_date), "yyyy-MM-dd")
        if date_obj.isValid():
            self.test_date_edit.setDate(date_obj)

        self.remark_edit.setPlainText(str(self.case_data.get("remark", "")))

    def on_accept(self):
        case_no = self.case_no_edit.text().strip()
        patient_name = self.patient_name_edit.text().strip()

        if not case_no:
            QMessageBox.warning(self, "提示", "病例编号不能为空。")
            return

        if not patient_name:
            QMessageBox.warning(self, "提示", "姓名不能为空。")
            return

        self.accept()

    def get_data(self):
        return {
            "case_no": self.case_no_edit.text().strip(),
            "patient_name": self.patient_name_edit.text().strip(),
            "age": self.age_spin.value(),
            "sample_no": self.sample_no_edit.text().strip(),
            "test_date": self.test_date_edit.date().toString("yyyy-MM-dd"),
            "remark": self.remark_edit.toPlainText().strip(),
        }