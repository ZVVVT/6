import sqlite3
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from app.case_edit_dialog import CaseEditDialog
from app.theme import DEFAULT_THEME_KEY, get_theme
from app.ui_components import (
    CardFrame,
    StatusBadge,
    apply_shadow,
    create_danger_button,
    create_primary_button,
    create_secondary_button,
    set_badge_to_table,
    setup_table,
)


class CaseStatCard(CardFrame):
    """病例管理顶部统计卡片。

    说明：
    1. 这里使用主题颜色，不在页面里写死主色。
    2. 图标先用文字符号承载，避免额外引入图标依赖。
    3. 后续如需换成 SVG 图标，只需要替换本类内部实现。
    """

    def __init__(
        self,
        title: str,
        value: str = "0",
        unit: str = "例",
        icon_text: str = "",
        accent: str = "primary",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(
            parent=parent,
            object_name="StatCard",
            margins=(16, 14, 16, 14),
            spacing=0,
            shadow=True,
        )
        self.theme = get_theme(DEFAULT_THEME_KEY)
        self.accent = accent

        root = QHBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        self.icon_label = QLabel(icon_text)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setFixedSize(48, 48)
        self.icon_label.setObjectName("StatIconBox")
        self._apply_icon_style()

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("SectionHint")
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(6)

        self.value_label = QLabel(str(value))
        self.value_label.setObjectName("PageTitle")
        self.value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.unit_label = QLabel(unit)
        self.unit_label.setObjectName("CurrentCaseLabel")
        self.unit_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        value_row.addWidget(self.value_label, 0, Qt.AlignBottom)
        value_row.addWidget(self.unit_label, 0, Qt.AlignBottom)
        value_row.addStretch(1)

        text_layout.addWidget(self.title_label)
        text_layout.addLayout(value_row)

        root.addWidget(self.icon_label, 0, Qt.AlignVCenter)
        root.addLayout(text_layout, 1)
        self.body_layout.addLayout(root)
        self.setMinimumHeight(92)

    def _accent_colors(self) -> Tuple[str, str, str]:
        theme = self.theme
        if self.accent == "success":
            return theme.get("success", "#16A34A"), theme.get("success_bg", "#EAF8EF"), theme.get("success_border", "#BDEACB")
        if self.accent == "warning":
            return theme.get("warning", "#F59E0B"), theme.get("warning_bg", "#FFF5E5"), theme.get("warning_border", "#FAD89A")
        if self.accent == "danger":
            return theme.get("danger", "#EF4444"), theme.get("danger_bg", "#FDECEC"), theme.get("danger_border", "#F6BFC0")
        if self.accent == "purple":
            return theme.get("purple", "#7C3AED"), theme.get("purple_bg", "#F2ECFF"), theme.get("purple_border", "#D8C8FF")
        return theme.get("primary", "#1769E0"), theme.get("primary_light", "#EAF2FF"), theme.get("primary_border", "#BCD7FF")

    def _apply_icon_style(self) -> None:
        color, bg, border = self._accent_colors()
        self.icon_label.setStyleSheet(
            f"""
            QLabel#StatIconBox {{
                color: {color};
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 12px;
                font-size: 22px;
                font-weight: 700;
            }}
            """
        )

    def set_value(self, value) -> None:
        self.value_label.setText(str(value))


class CaseManagerWindow(QWidget):
    case_selected = Signal(dict)

    TABLE_HEADERS = [
        "ID",
        "状态",
        "病历号",
        "姓名",
        "年龄",
        "性别",
        "联系方式",
        "样本号",
        "检测日期",
        "报告状态",
        "创建时间",
        "更新时间",
    ]

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.database = database
        self.current_cases: List[Dict] = []
        self.init_ui()
        self.load_cases()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 16, 18, 14)
        main_layout.setSpacing(14)

        # 顶部统计卡片
        stats_layout = QHBoxLayout()
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setSpacing(14)

        self.card_total = CaseStatCard("病例总数", "0", "例", "＋", "primary")
        self.card_today = CaseStatCard("今日新增", "0", "例", "人", "success")
        self.card_report = CaseStatCard("已生成报告", "0", "例", "文", "purple")
        self.card_waiting = CaseStatCard("待分析", "0", "例", "待", "warning")

        stats_layout.addWidget(self.card_total, 1)
        stats_layout.addWidget(self.card_today, 1)
        stats_layout.addWidget(self.card_report, 1)
        stats_layout.addWidget(self.card_waiting, 1)
        main_layout.addLayout(stats_layout)

        # 搜索与操作工具栏
        toolbar_card = CardFrame(
            object_name="Card",
            margins=(14, 12, 14, 12),
            spacing=0,
            shadow=True,
        )
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(10)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("按病历号、姓名、样本号、日期、联系方式搜索")
        self.search_edit.setMinimumHeight(36)
        self.search_edit.returnPressed.connect(self.search_cases)

        self.btn_search = create_primary_button("搜索", min_width=86)
        self.btn_refresh = create_secondary_button("刷新", min_width=86)
        self.btn_add = create_primary_button("＋ 新建病例", min_width=112)
        self.btn_edit = create_secondary_button("编辑病例", min_width=100)
        self.btn_delete = create_danger_button("删除病例", min_width=100)

        toolbar_layout.addWidget(self.search_edit, 1)
        toolbar_layout.addWidget(self.btn_search)
        toolbar_layout.addWidget(self.btn_refresh)
        toolbar_layout.addWidget(self.btn_add)
        toolbar_layout.addWidget(self.btn_edit)
        toolbar_layout.addWidget(self.btn_delete)
        toolbar_card.addLayout(toolbar_layout)
        main_layout.addWidget(toolbar_card)

        # 表格卡片
        table_card = CardFrame(
            object_name="Card",
            margins=(12, 12, 12, 10),
            spacing=8,
            shadow=True,
        )

        self.table = QTableWidget()
        self.table.setObjectName("CaseTable")
        self.table.setColumnCount(len(self.TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(self.TABLE_HEADERS)
        self.table.setColumnHidden(0, True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        setup_table(
            self.table,
            row_height=42,
            alternating=True,
            stretch_last_section=False,
            selection_behavior=QAbstractItemView.SelectRows,
        )

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)   # 状态
        header.setSectionResizeMode(2, QHeaderView.Stretch)            # 病历号
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)   # 姓名
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)   # 年龄
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)   # 性别
        header.setSectionResizeMode(6, QHeaderView.Stretch)            # 联系方式
        header.setSectionResizeMode(7, QHeaderView.Stretch)            # 样本号
        header.setSectionResizeMode(8, QHeaderView.ResizeToContents)   # 检测日期
        header.setSectionResizeMode(9, QHeaderView.ResizeToContents)   # 报告状态
        header.setSectionResizeMode(10, QHeaderView.ResizeToContents)  # 创建时间
        header.setSectionResizeMode(11, QHeaderView.ResizeToContents)  # 更新时间

        table_card.addWidget(self.table, 1)

        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(2, 4, 2, 0)
        footer_layout.setSpacing(8)

        self.info_label = QLabel("当前病例数量：0")
        self.info_label.setObjectName("CurrentCaseLabel")
        self.info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.page_label = QLabel("共 0 条    1 / 1")
        self.page_label.setObjectName("CurrentCaseLabel")
        self.page_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        footer_layout.addWidget(self.info_label)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.page_label)
        table_card.addWidget(footer)

        main_layout.addWidget(table_card, 1)

        self.btn_search.clicked.connect(self.search_cases)
        self.btn_refresh.clicked.connect(self.load_cases)
        self.btn_add.clicked.connect(self.add_case)
        self.btn_edit.clicked.connect(self.edit_case)
        self.btn_delete.clicked.connect(self.delete_case)
        self.table.doubleClicked.connect(self.open_selected_case)

    # ------------------------------------------------------------------
    # 数据加载与统计
    # ------------------------------------------------------------------
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

        self.current_cases = cases
        self._fill_table(cases)
        self._update_summary_cards(cases)
        self._update_footer(len(cases))

    def _fill_table(self, cases: List[Dict]) -> None:
        self.table.setRowCount(len(cases))

        for row_index, case in enumerate(cases):
            case_id = case.get("id", "")
            report_path = self._safe_text(case.get("report_path", ""))
            status_text = "已完成" if report_path else "待分析"
            status_type = "success" if report_path else "warning"
            report_status = "查看报告" if report_path else "待生成报告"

            values = [
                case_id,
                "",  # 状态列使用 cell widget
                case.get("case_no", ""),
                case.get("patient_name", ""),
                case.get("age", ""),
                case.get("sex", ""),
                case.get("phone", ""),
                case.get("sample_no", ""),
                case.get("test_date", ""),
                report_status,
                case.get("created_at", ""),
                case.get("updated_at", ""),
            ]

            for col_index, value in enumerate(values):
                item = QTableWidgetItem(self._safe_text(value))
                item.setTextAlignment(Qt.AlignCenter)
                if col_index == 0:
                    item.setData(Qt.UserRole, case_id)
                if col_index == 9 and report_path:
                    item.setForeground(Qt.blue)
                    item.setToolTip(report_path)
                self.table.setItem(row_index, col_index, item)

            set_badge_to_table(self.table, row_index, 1, status_text, status_type)

        self.table.clearSelection()

    def _update_summary_cards(self, cases: List[Dict]) -> None:
        total_count = len(cases)
        today_count = 0
        report_count = 0
        waiting_count = 0
        today_text = date.today().strftime("%Y-%m-%d")

        for case in cases:
            report_path = self._safe_text(case.get("report_path", ""))
            if report_path:
                report_count += 1
            else:
                waiting_count += 1

            created_at = self._safe_text(case.get("created_at", ""))
            test_date = self._safe_text(case.get("test_date", ""))
            if created_at.startswith(today_text) or test_date == today_text:
                today_count += 1

        self.card_total.set_value(total_count)
        self.card_today.set_value(today_count)
        self.card_report.set_value(report_count)
        self.card_waiting.set_value(waiting_count)

    def _update_footer(self, count: int) -> None:
        self.info_label.setText(f"当前病例数量：{count}")
        self.page_label.setText(f"共 {count} 条    1 / 1")

    # ------------------------------------------------------------------
    # 选择与操作
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 工具函数
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_text(value) -> str:
        if value is None:
            return ""
        return str(value)
