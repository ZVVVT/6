import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLineEdit,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
    QHeaderView,
)

from app.case_edit_dialog import CaseEditDialog


class CaseManagerWindow(QWidget):
    case_selected = Signal(dict)

    def __init__(self, database, parent=None):
        super().__init__(parent)

        self.database = database

        self.init_ui()
        self.load_cases()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        title_label = QLabel("病例管理")
        title_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        main_layout.addWidget(title_label)

        toolbar_layout = QHBoxLayout()

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("按病历号、姓名、样本号、日期、联系方式搜索")

        self.btn_search = QPushButton("搜索")
        self.btn_refresh = QPushButton("刷新")
        self.btn_add = QPushButton("新建病例")
        self.btn_edit = QPushButton("编辑病例")
        self.btn_delete = QPushButton("删除病例")

        toolbar_layout.addWidget(self.search_edit, 1)
        toolbar_layout.addWidget(self.btn_search)
        toolbar_layout.addWidget(self.btn_refresh)
        toolbar_layout.addWidget(self.btn_add)
        toolbar_layout.addWidget(self.btn_edit)
        toolbar_layout.addWidget(self.btn_delete)

        main_layout.addLayout(toolbar_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(11)
        self.table.setHorizontalHeaderLabels([
            "ID",
            "病历号",
            "姓名",
            "年龄",
            "性别",
            "联系方式",
            "样本号",
            "检查日期",
            "报告路径",
            "创建时间",
            "更新时间",
        ])

        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnHidden(0, True)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)

        main_layout.addWidget(self.table, 1)

        self.info_label = QLabel("提示：双击病例可进入蛋白分析流程。")
        self.info_label.setStyleSheet("color: #666666;")
        main_layout.addWidget(self.info_label)

        self.btn_search.clicked.connect(self.search_cases)
        self.btn_refresh.clicked.connect(self.load_cases)
        self.btn_add.clicked.connect(self.add_case)
        self.btn_edit.clicked.connect(self.edit_case)
        self.btn_delete.clicked.connect(self.delete_case)

        self.table.doubleClicked.connect(self.open_selected_case)

    def load_cases(self):
        self.search_edit.clear()
        self._load_cases_by_keyword("")

    def search_cases(self):
        keyword = self.search_edit.text().strip()
        self._load_cases_by_keyword(keyword)

    def _load_cases_by_keyword(self, keyword):
        try:
            cases = self.database.get_cases(keyword)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载病例失败：\n{e}")
            return

        self.table.setRowCount(len(cases))

        for row_index, case in enumerate(cases):
            values = [
                case.get("id", ""),
                case.get("case_no", ""),
                case.get("patient_name", ""),
                case.get("age", ""),
                case.get("sex", ""),
                case.get("phone", ""),
                case.get("sample_no", ""),
                case.get("test_date", ""),
                case.get("report_path", "") or "",
                case.get("created_at", ""),
                case.get("updated_at", ""),
            ]

            for col_index, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_index, col_index, item)

        self.info_label.setText(f"当前病例数量：{len(cases)}")

    def get_selected_case_id(self):
        selected_rows = self.table.selectionModel().selectedRows()

        if not selected_rows:
            QMessageBox.information(self, "提示", "请先选择一条病例记录。")
            return None

        row = selected_rows[0].row()
        case_id_item = self.table.item(row, 0)

        if case_id_item is None:
            QMessageBox.warning(self, "提示", "无法获取病例 ID。")
            return None

        try:
            return int(case_id_item.text())
        except ValueError:
            QMessageBox.warning(self, "提示", "病例 ID 格式异常。")
            return None

    def add_case(self):
        dialog = CaseEditDialog(self)

        if dialog.exec() != CaseEditDialog.Accepted:
            return

        data = dialog.get_data()

        try:
            self.database.create_case(**data)
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "提示", "病历号已存在，请更换病历号。")
            return
        except Exception as e:
            QMessageBox.critical(self, "错误", f"新建病例失败：\n{e}")
            return

        QMessageBox.information(self, "成功", "病例创建成功。")
        self.load_cases()

    def edit_case(self):
        case_id = self.get_selected_case_id()

        if case_id is None:
            return

        case_data = self.database.get_case(case_id)

        if not case_data:
            QMessageBox.warning(self, "提示", "未找到该病例记录。")
            return

        dialog = CaseEditDialog(self, case_data=case_data)

        if dialog.exec() != CaseEditDialog.Accepted:
            return

        data = dialog.get_data()

        try:
            self.database.update_case(case_id=case_id, **data)
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "提示", "病历号已存在，请更换病历号。")
            return
        except Exception as e:
            QMessageBox.critical(self, "错误", f"编辑病例失败：\n{e}")
            return

        QMessageBox.information(self, "成功", "病例修改成功。")
        self.load_cases()

    def delete_case(self):
        case_id = self.get_selected_case_id()

        if case_id is None:
            return

        case_data = self.database.get_case(case_id)

        if not case_data:
            QMessageBox.warning(self, "提示", "未找到该病例记录。")
            return

        case_no = case_data.get("case_no", "")
        patient_name = case_data.get("patient_name", "")

        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除病例吗？\n\n病历号：{case_no}\n姓名：{patient_name}\n\n删除后不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        try:
            self.database.delete_case(case_id)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"删除病例失败：\n{e}")
            return

        QMessageBox.information(self, "成功", "病例已删除。")
        self.load_cases()

    def open_selected_case(self):
        case_id = self.get_selected_case_id()

        if case_id is None:
            return

        case_data = self.database.get_case(case_id)

        if not case_data:
            QMessageBox.warning(self, "提示", "未找到该病例记录。")
            return

        self.case_selected.emit(case_data)