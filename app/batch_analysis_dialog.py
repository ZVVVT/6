# -*- coding: utf-8 -*-
from __future__ import annotations

import configparser
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from app.long_message_dialog import show_long_message_dialog
from core.analysis_v2.batch_input_adapter import (
    AnalysisV2BatchInputError,
    FORMAL_PROTEIN_PARTS,
    build_batch_task_request,
)
from core.analysis_v2.result_completion_service import (
    AnalysisV2CompletionPublishError,
    publish_measured_completion,
)
from core.analysis_v2.task_runner import (
    AnalysisV2TaskCancelled,
    AnalysisV2TaskError,
    AnalysisV2TaskRunner,
)
from core.config_manager import ConfigManager, get_application_root
from core.image_channel_matcher import ImageChannelMatcher
from core.mvimageid_runner import MvImageIDRunner


# =========================
# 批量文件夹匹配规则
# =========================

DEFAULT_FOLDER_ALIASES: Dict[str, List[str]] = {
    "protein1": ["Q9BYW3", "HEL-1", "HEL1", "A", "蛋白1", "protein1", "protein1_Q9BYW3"],
    "protein2": ["P10323", "HEL-2", "HEL2", "B", "蛋白2", "protein2", "protein2_P10323"],
    "protein3": ["Q96P56", "HEL-3", "HEL3", "C", "蛋白3", "protein3", "protein3_Q96P56"],
    "protein4": ["Q8IYV9", "HEL-4", "HEL4", "D", "蛋白4", "protein4", "protein4_Q8IYV9"],
    "protein5": ["W5XKT8", "HEL-5", "HEL5", "E", "蛋白5", "protein5", "protein5_W5XKT8"],
}

PROTEIN_DISPLAY_FALLBACK: Dict[str, str] = {
    "protein1": "Q9BYW3",
    "protein2": "P10323",
    "protein3": "Q96P56",
    "protein4": "Q8IYV9",
    "protein5": "W5XKT8",
}

ANALYSIS_V2_HEAD_ASSETS: Tuple[Tuple[str, str], ...] = (
    ("Head Measurement pipeline", "pipelines/analysis_v2/measure_head_from_labels.cppipe"),
    ("Direct Cellpose worker", "tools/analysis_v2/direct_cellpose_worker.py"),
)

ANALYSIS_V2_TAIL_ASSETS: Tuple[Tuple[str, str], ...] = (
    ("Tail Measurement pipeline", "pipelines/analysis_v2/measure_tail_from_labels.cppipe"),
    ("C18B Python", ".venv-c18b/python.exe"),
    ("C18B adapter", "tools/analysis_v2/c18b_score015_adapter.py"),
    ("C18B editor adapter", "tools/analysis_v2/c18b_tail_editor_adapter.py"),
    ("C18B tail editor", "tools/analysis_v2/tail_legacy/tail_result_editor_v2_3_draft_mvp.py"),
    ("C18B __init__", "tools/analysis_v2/c18b_score015/__init__.py"),
    ("C18B candidate merging", "tools/analysis_v2/c18b_score015/candidate_merging.py"),
    ("C18B candidate scoring", "tools/analysis_v2/c18b_score015/candidate_scoring.py"),
    ("C18B candidate validation", "tools/analysis_v2/c18b_score015/candidate_validation.py"),
    ("C18B extreme fragment filter", "tools/analysis_v2/c18b_score015/extreme_fragment_filter.py"),
    ("C18B FITC processing", "tools/analysis_v2/c18b_score015/fitc_processing.py"),
    ("C18B graph separation", "tools/analysis_v2/c18b_score015/graph_constrained_instance_separation.py"),
    ("C18B graph region growing", "tools/analysis_v2/c18b_score015/graph_seeded_region_growing.py"),
    ("C18B identity graph", "tools/analysis_v2/c18b_score015/identity_graph_v3.py"),
    ("C18B pipeline runner", "tools/analysis_v2/c18b_score015/run_pipeline.py"),
    ("C18B graph experiment", "tools/analysis_v2/c18b_score015/tail_graph_experiment.py"),
    ("C18B frozen parameters", "tools/analysis_v2/c18b_score015/config/frozen_parameters.json"),
)

MODERN_MESSAGE_BOX_QSS = """
    QMessageBox#ModernMessageBox {
        background-color: #F5F8FC;
        color: #1F2D3D;
        font-family: "Microsoft YaHei";
        font-size: 13px;
    }

    QMessageBox#ModernMessageBox QWidget {
        background-color: #F5F8FC;
        color: #1F2D3D;
    }

    QMessageBox#ModernMessageBox QLabel {
        background-color: transparent;
        color: #1F2D3D;
        font-size: 13px;
        line-height: 1.5;
    }

    QMessageBox#ModernMessageBox QLabel#qt_msgbox_label {
        min-width: 260px;
        padding: 4px 4px 8px 4px;
    }

    QMessageBox#ModernMessageBox QPushButton {
        min-width: 72px;
        min-height: 32px;
        padding: 5px 16px;
        border: 1px solid #DDE6F2;
        border-radius: 6px;
        background-color: #FFFFFF;
        color: #1F2D3D;
        font-weight: 500;
    }

    QMessageBox#ModernMessageBox QPushButton:hover {
        background-color: #F2F7FF;
        border-color: #BCD7FF;
        color: #1769E0;
    }

    QMessageBox#ModernMessageBox QPushButton:pressed {
        background-color: #EAF2FF;
        border-color: #1769E0;
    }

    QMessageBox#ModernMessageBox QPushButton:default {
        background-color: #1769E0;
        border-color: #1769E0;
        color: #FFFFFF;
        font-weight: 600;
    }

    QMessageBox#ModernMessageBox QPushButton:default:hover {
        background-color: #0F5ED7;
        border-color: #0F5ED7;
        color: #FFFFFF;
    }
"""


def show_modern_message(
    parent,
    icon: QMessageBox.Icon,
    title: str,
    text: str,
    buttons=QMessageBox.Ok,
    default_button=QMessageBox.NoButton,
):
    """显示浅色统一风格消息弹窗，避免继承父窗口透明/暗色样式导致黑底。"""
    box = QMessageBox(parent)
    box.setObjectName("ModernMessageBox")
    box.setAttribute(Qt.WA_StyledBackground, True)
    box.setWindowTitle(title)
    box.setIcon(icon)
    box.setText(text)
    box.setStandardButtons(buttons)
    if default_button != QMessageBox.NoButton:
        box.setDefaultButton(default_button)
    box.setMinimumWidth(380)
    box.setStyleSheet(MODERN_MESSAGE_BOX_QSS)

    for button in box.findChildren(QPushButton):
        button.setMinimumHeight(32)
        button.setCursor(Qt.PointingHandCursor)

    return box.exec()


def show_batch_information(parent, title: str, text: str):
    return show_modern_message(parent, QMessageBox.Information, title, text, QMessageBox.Ok, QMessageBox.Ok)


def show_batch_warning(parent, title: str, text: str):
    return show_modern_message(parent, QMessageBox.Warning, title, text, QMessageBox.Ok, QMessageBox.Ok)


def show_batch_question(
    parent,
    title: str,
    text: str,
    buttons=QMessageBox.Yes | QMessageBox.No,
    default_button=QMessageBox.No,
):
    return show_modern_message(parent, QMessageBox.Question, title, text, buttons, default_button)



class FolderAliasStore:
    """把批量文件夹匹配名保存到 config.ini 的 [BatchFolderAliases] 中。"""

    SECTION_NAME = "BatchFolderAliases"

    def __init__(self, config_path: Optional[Path] = None):
        if config_path is None:
            # 统一复用 ConfigManager 的配置路径解析逻辑：
            # 源码运行读项目根目录 config.ini，打包后读 exe 同级 config.ini。
            config_path = ConfigManager().config_path
        self.config_path = Path(config_path)

    @staticmethod
    def normalize_text(text: str) -> str:
        return (
            str(text or "")
            .strip()
            .lower()
            .replace(" ", "")
            .replace("-", "")
            .replace("_", "")
        )

    @staticmethod
    def split_aliases(text: str) -> List[str]:
        raw = str(text or "").replace("，", ",").replace(";", ",").replace("；", ",")
        result: List[str] = []
        seen = set()
        for part in raw.split(","):
            item = part.strip()
            if not item:
                continue
            norm = FolderAliasStore.normalize_text(item)
            if norm in seen:
                continue
            seen.add(norm)
            result.append(item)
        return result

    @staticmethod
    def join_aliases(items: List[str]) -> str:
        result: List[str] = []
        seen = set()
        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            norm = FolderAliasStore.normalize_text(text)
            if norm in seen:
                continue
            seen.add(norm)
            result.append(text)
        return ",".join(result)

    def _read_config(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        parser.optionxform = str
        if self.config_path.exists():
            parser.read(self.config_path, encoding="utf-8")
        return parser

    def load_aliases(self) -> Dict[str, List[str]]:
        parser = self._read_config()
        aliases: Dict[str, List[str]] = {}
        for key, default_values in DEFAULT_FOLDER_ALIASES.items():
            values = list(default_values)
            if parser.has_section(self.SECTION_NAME) and parser.has_option(self.SECTION_NAME, key):
                custom = self.split_aliases(parser.get(self.SECTION_NAME, key, fallback=""))
                # 自定义内容放前面，默认兼容内容放后面。
                values = custom + values
            aliases[key] = self._unique(values)
        return aliases

    def save_aliases(self, aliases: Dict[str, List[str]]) -> None:
        parser = self._read_config()
        if not parser.has_section(self.SECTION_NAME):
            parser.add_section(self.SECTION_NAME)
        for key in sorted(DEFAULT_FOLDER_ALIASES.keys()):
            parser.set(self.SECTION_NAME, key, self.join_aliases(aliases.get(key, [])))
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config_path.open("w", encoding="utf-8") as f:
            parser.write(f)

    def add_alias(self, protein_key: str, alias: str) -> bool:
        alias = str(alias or "").strip()
        if not protein_key or not alias:
            return False
        aliases = self.load_aliases()
        items = aliases.setdefault(protein_key, [])
        norm = self.normalize_text(alias)
        for old in items:
            if self.normalize_text(old) == norm:
                return False
        items.insert(0, alias)
        self.save_aliases(aliases)
        return True

    def add_current_mapping(self, mapping: Dict[str, str]) -> int:
        aliases = self.load_aliases()
        changed = 0
        for protein_key, folder_name in mapping.items():
            folder_name = str(folder_name or "").strip()
            if not protein_key or not folder_name:
                continue
            items = aliases.setdefault(protein_key, [])
            norm = self.normalize_text(folder_name)
            if any(self.normalize_text(old) == norm for old in items):
                continue
            items.insert(0, folder_name)
            changed += 1
        if changed:
            self.save_aliases(aliases)
        return changed

    @staticmethod
    def _unique(items: List[str]) -> List[str]:
        result: List[str] = []
        seen = set()
        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            norm = FolderAliasStore.normalize_text(text)
            if norm in seen:
                continue
            seen.add(norm)
            result.append(text)
        return result


class FolderAliasDialog(QDialog):
    """编辑每个蛋白可匹配的文件夹名称。"""

    def __init__(self, protein_items: List[dict], alias_store: FolderAliasStore, parent=None):
        super().__init__(parent)
        self.protein_items = protein_items
        self.alias_store = alias_store
        self.aliases = self.alias_store.load_aliases()

        self.setWindowTitle("批量文件夹匹配规则")
        self.resize(980, 520)
        self.setMinimumSize(900, 460)
        self.init_ui()
        self.load_table()
        self.apply_modern_style()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel("批量文件夹匹配规则")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1f4e79;")
        layout.addWidget(title)

        desc = QLabel(
            "每个蛋白可以配置多个文件夹匹配名，用英文逗号或中文逗号分隔。"
            "例如：Q9BYW3,HEL-1,A,蛋白1。批量分析选择上一级目录后，会按这些名称自动匹配子文件夹。"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #555555;")
        layout.addWidget(desc)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["内部编号", "显示名称", "批量文件夹匹配名"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        btn_layout = QHBoxLayout()
        self.btn_defaults = QPushButton("恢复推荐匹配名")
        self.btn_save = QPushButton("保存规则")
        self.btn_cancel = QPushButton("取消")
        btn_layout.addWidget(self.btn_defaults)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        self.btn_defaults.clicked.connect(self.restore_defaults)
        self.btn_save.clicked.connect(self.save_rules)
        self.btn_cancel.clicked.connect(self.reject)

    def apply_primary_button_styles(self):
        """强制主按钮保持蓝底白字。

        这两个按钮在父窗口/全局 QSS 影响下，普通状态可能被渲染成白底，
        只有 hover 才变蓝。这里给按钮本身设置局部样式，确保普通状态也
        始终为蓝色主按钮。
        """
        primary_css = """
            QPushButton#BatchPrimaryButton {
                min-height: 34px;
                padding: 5px 16px;
                border: 1px solid #1769E0;
                border-radius: 6px;
                background-color: #1769E0;
                color: #FFFFFF;
                font-weight: 600;
            }
            QPushButton#BatchPrimaryButton:hover {
                background-color: #0F5ED7;
                border-color: #0F5ED7;
                color: #FFFFFF;
            }
            QPushButton#BatchPrimaryButton:pressed {
                background-color: #0B4DB5;
                border-color: #0B4DB5;
                color: #FFFFFF;
            }
            QPushButton#BatchPrimaryButton:disabled {
                background-color: #EEF4FB;
                border-color: #DDE6F2;
                color: #8A97A8;
            }
        """
        for name in ["btn_select_folder", "btn_start"]:
            if not hasattr(self, name):
                continue
            button = getattr(self, name)
            button.setObjectName("BatchPrimaryButton")
            button.setFlat(False)
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setMinimumHeight(34)
            button.setStyleSheet(primary_css)

    def apply_modern_style(self):
        """匹配规则弹窗独立样式，避免继承父窗口透明/黑底样式。"""
        self.setObjectName("FolderAliasDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)

        for label in self.findChildren(QLabel):
            text = label.text().strip()
            if text == "批量文件夹匹配规则":
                label.setObjectName("AliasDialogTitle")
            else:
                label.setObjectName("AliasDialogHint")

        self.table.setObjectName("AliasRulesTable")
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setShowGrid(True)
        self.table.setAlternatingRowColors(True)

        self.btn_save.setObjectName("AliasPrimaryButton")
        self.btn_defaults.setObjectName("AliasNeutralButton")
        self.btn_cancel.setObjectName("AliasNeutralButton")
        for button in [self.btn_defaults, self.btn_save, self.btn_cancel]:
            button.setMinimumHeight(34)
            button.setCursor(Qt.PointingHandCursor)

        self.setStyleSheet("""
            QDialog#FolderAliasDialog {
                background-color: #F5F8FC;
                color: #1F2D3D;
                font-family: "Microsoft YaHei";
                font-size: 13px;
            }

            QDialog#FolderAliasDialog QWidget {
                color: #1F2D3D;
                background: transparent;
            }

            QLabel#AliasDialogTitle {
                color: #102A43;
                font-size: 20px;
                font-weight: 700;
                padding: 2px 0 0 0;
            }

            QLabel#AliasDialogHint {
                color: #5E6B7A;
                font-size: 13px;
                line-height: 1.4;
            }

            QTableWidget#AliasRulesTable {
                background-color: #FFFFFF;
                alternate-background-color: #F8FAFD;
                gridline-color: #DDE6F2;
                border: 1px solid #DDE6F2;
                border-radius: 8px;
                selection-background-color: #DCEBFF;
                selection-color: #1F2D3D;
                color: #1F2D3D;
            }

            QTableWidget#AliasRulesTable::item {
                padding: 6px 8px;
            }

            QTableWidget#AliasRulesTable::item:selected {
                background-color: #DCEBFF;
                color: #1F2D3D;
            }

            QTableWidget#AliasRulesTable QLineEdit {
                background-color: #FFFFFF;
                color: #1F2D3D;
                border: 1px solid #BCD7FF;
                border-radius: 4px;
                padding: 4px 8px;
                selection-background-color: #DCEBFF;
            }

            QHeaderView::section {
                background-color: #EEF4FB;
                color: #1F2D3D;
                font-weight: 700;
                border: none;
                border-right: 1px solid #DDE6F2;
                border-bottom: 1px solid #DDE6F2;
                padding: 7px 6px;
                min-height: 30px;
            }

            QPushButton#AliasPrimaryButton {
                min-height: 34px;
                padding: 5px 18px;
                border: 1px solid #1769E0;
                border-radius: 6px;
                background-color: #1769E0;
                color: #FFFFFF;
                font-weight: 600;
            }

            QPushButton#AliasPrimaryButton:hover {
                background-color: #0F5ED7;
                border-color: #0F5ED7;
            }

            QPushButton#AliasNeutralButton {
                min-height: 34px;
                padding: 5px 16px;
                border: 1px solid #DDE6F2;
                border-radius: 6px;
                background-color: #FFFFFF;
                color: #1F2D3D;
                font-weight: 500;
            }

            QPushButton#AliasNeutralButton:hover {
                background-color: #F2F7FF;
                border-color: #BCD7FF;
                color: #1769E0;
            }

            QScrollBar:vertical {
                background-color: #EEF4FB;
                width: 10px;
                margin: 0px;
                border: none;
            }

            QScrollBar::handle:vertical {
                background-color: #DDE6F2;
                min-height: 30px;
                border-radius: 5px;
            }

            QScrollBar::handle:vertical:hover {
                background-color: #BCD7FF;
            }

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
                background: none;
                border: none;
            }
        """)

    def load_table(self):
        self.table.setRowCount(len(self.protein_items))
        for row, protein in enumerate(self.protein_items):
            key = str(protein.get("key", "") or "").strip()
            name = str(protein.get("name", "") or "").strip() or PROTEIN_DISPLAY_FALLBACK.get(key, key)

            key_item = QTableWidgetItem(key)
            key_item.setFlags(key_item.flags() & ~Qt.ItemIsEditable)
            name_item = QTableWidgetItem(name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            alias_item = QTableWidgetItem(FolderAliasStore.join_aliases(self.aliases.get(key, [])))

            self.table.setItem(row, 0, key_item)
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, alias_item)

    def restore_defaults(self):
        self.aliases = {key: list(values) for key, values in DEFAULT_FOLDER_ALIASES.items()}
        self.load_table()

    def save_rules(self):
        new_aliases: Dict[str, List[str]] = {}
        conflict_map: Dict[str, List[str]] = {}

        for row in range(self.table.rowCount()):
            key_item = self.table.item(row, 0)
            alias_item = self.table.item(row, 2)
            if not key_item:
                continue
            key = key_item.text().strip()
            aliases = FolderAliasStore.split_aliases(alias_item.text() if alias_item else "")
            new_aliases[key] = aliases
            for alias in aliases:
                norm = FolderAliasStore.normalize_text(alias)
                conflict_map.setdefault(norm, []).append(key)

        conflicts = []
        for alias_norm, keys in conflict_map.items():
            unique_keys = sorted(set(keys))
            if alias_norm and len(unique_keys) > 1:
                conflicts.append(f"{alias_norm} → {'、'.join(unique_keys)}")

        if conflicts:
            show_batch_warning(
                self,
                "匹配名冲突",
                "以下匹配名同时配置到了多个蛋白，请修改后再保存：\n" + "\n".join(conflicts[:20]),
            )
            return

        self.alias_store.save_aliases(new_aliases)
        show_batch_information(self, "提示", "批量文件夹匹配规则已保存。")
        self.accept()


# =========================
# 批量分析后台线程
# =========================

class BatchProteinWorker(QThread):
    log_signal = Signal(str)
    progress_signal = Signal(int, int, str)
    task_status_signal = Signal(str, str)
    finished_signal = Signal(list, list)

    def __init__(self, case_data: dict, tasks: List[dict], config: ConfigManager, database, parent=None):
        super().__init__(parent)
        self.case_data = case_data
        self.tasks = tasks
        self.config = config
        self.database = database
        self.cancel_after_current = False
        self.current_runner: Optional[AnalysisV2TaskRunner] = None

    def request_cancel_after_current(self):
        self.cancel_after_current = True
        runner = self.current_runner
        if runner is not None:
            runner.cancel()
        self.log_signal.emit("已请求取消批量分析：正在停止当前蛋白，后续未开始项目将跳过。")

    def run(self):
        results = []
        errors = []
        total = len(self.tasks)

        for index, task in enumerate(self.tasks, start=1):
            protein_key = task["protein_key"]
            protein_name = task["protein_name"]

            if self.cancel_after_current:
                self._append_cancelled_tasks(errors, index - 1)
                break

            self.progress_signal.emit(index, total, protein_name)
            self.task_status_signal.emit(protein_key, "分析中")
            self.log_signal.emit(f"========== 开始分析 {protein_name}（{protein_key}）[{index}/{total}] ==========")

            try:
                result = self.run_one_protein(task)
                results.append(result)
                self.task_status_signal.emit(protein_key, "已完成")
                self.log_signal.emit(f"{protein_name} 分析完成。")
            except AnalysisV2TaskCancelled:
                self.cancel_after_current = True
                self.task_status_signal.emit(protein_key, "已取消")
                errors.append({
                    "protein_key": protein_key,
                    "protein_name": protein_name,
                    "message": "用户取消分析。",
                    "kind": "cancelled",
                })
                self.log_signal.emit(f"{protein_name} 分析已取消。")
                self._append_cancelled_tasks(errors, index)
                break
            except AnalysisV2BatchInputError as e:
                self._append_failure(errors, task, "输入错误", e)
            except AnalysisV2CompletionPublishError as e:
                label = "数据库失败" if e.stage == "database" else "发布失败"
                self._append_failure(errors, task, label, e)
            except AnalysisV2TaskError as e:
                self._append_failure(errors, task, "分析失败", e)
            except Exception as e:
                self._append_failure(errors, task, "分析失败", e)

        self.finished_signal.emit(results, errors)

    def _append_cancelled_tasks(self, errors: list, start_index: int) -> None:
        for task in self.tasks[start_index:]:
            self.task_status_signal.emit(task["protein_key"], "已取消")
            errors.append({
                "protein_key": task["protein_key"],
                "protein_name": task["protein_name"],
                "message": "用户取消后续分析。",
                "kind": "cancelled",
            })

    def _append_failure(self, errors: list, task: dict, label: str, error: Exception) -> None:
        message = "{}：{}".format(label, error)
        self.task_status_signal.emit(task["protein_key"], "失败")
        errors.append({
            "protein_key": task["protein_key"],
            "protein_name": task["protein_name"],
            "message": message,
            "kind": "failed",
        })
        self.log_signal.emit("{} {}".format(task["protein_name"], message))

    def run_one_protein(self, task: dict):
        """
        批量分析中的单个蛋白执行入口。

        使用现有 Batch 匹配结果构造 Analysis V2 请求，在当前 Batch worker
        线程同步完成分析与公共发布。这里不创建第二层 QThread。
        """
        protein_key = task["protein_key"]
        source_folder = task["folder"]

        request = build_batch_task_request(
            case_data=self.case_data,
            protein_key=protein_key,
            protein_folder=source_folder,
            config=self.config,
        )
        runner = AnalysisV2TaskRunner(self.config, log_callback=self.log_signal.emit)
        self.current_runner = runner
        run_error = None
        try:
            if self.cancel_after_current:
                runner.cancel()
            completion = runner.run(request)
            if completion.get("status") != "measured":
                raise AnalysisV2TaskError(
                    "Analysis V2 未返回 measured CompletionResult。",
                    stage="completion",
                    case_no=request.case_no,
                    protein_key=request.protein_key,
                )
            if self.cancel_after_current:
                raise AnalysisV2TaskCancelled(
                    "Analysis V2 task cancelled",
                    stage="completion",
                    case_no=request.case_no,
                    protein_key=request.protein_key,
                )
            published = publish_measured_completion(completion, self.database)
            self.log_signal.emit(published.database_message)
            if published.cleanup_warning:
                self.log_signal.emit("结果发布清理提示：{}".format(published.cleanup_warning))
            return published
        except BaseException as error:
            run_error = error
            raise
        finally:
            try:
                runner.shutdown()
            except Exception as shutdown_error:
                if run_error is None:
                    raise
                self.log_signal.emit("Analysis V2 runner 清理失败：{}".format(shutdown_error))
            finally:
                self.current_runner = None


# =========================
# 批量分析主窗口
# =========================

class BatchAnalysisDialog(QDialog):
    batch_finished = Signal()

    def __init__(self, database, case_data: dict, parent=None):
        super().__init__(parent)
        self.database = database
        self.case_data = case_data
        # 批量分析必须和单蛋白分析读取同一个 config.ini。
        # 不能用 __file__ 推导项目根目录，否则 PyInstaller 打包后可能读到 _internal/config.ini，
        # 导致自定义工作目录在批量分析中不生效。
        self.config = ConfigManager()
        self.config.ensure_default_config()

        self.alias_store = FolderAliasStore(self.config.config_path)
        self.parent_folder: Optional[Path] = None
        # 记录上一次真正完成预检查的总文件夹。
        # 用于判断用户是否换了一个总文件夹；一旦换文件夹，不能再沿用旧文件夹的手动匹配结果。
        self._last_scanned_parent_folder: Optional[Path] = None
        self.available_folders: List[Path] = []
        self.scan_rows: List[dict] = []
        self.worker: Optional[BatchProteinWorker] = None
        self._refreshing_table = False

        self.setWindowTitle("批量蛋白分析")
        self.resize(1060, 780)
        self.setMinimumSize(980, 720)
        self.init_ui()

        self.apply_modern_style()

        self.scan_parent_folder()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("批量蛋白分析")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #1f4e79;")
        layout.addWidget(title)

        case_no = self.case_data.get("case_no", "")
        patient_name = self.case_data.get("patient_name", "")
        sample_no = self.case_data.get("sample_no", "")
        self.case_label = QLabel(f"当前病例：{case_no}    姓名：{patient_name}    样本号：{sample_no}")
        self.case_label.setStyleSheet("color: #555555;")
        layout.addWidget(self.case_label)

        folder_group = QGroupBox("选择总文件夹")
        folder_layout = QHBoxLayout(folder_group)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("请选择包含多个蛋白子文件夹的上一级目录")
        self.btn_select_folder = QPushButton("选择文件夹")
        self.btn_scan = QPushButton("重新扫描")
        self.btn_alias_rules = QPushButton("匹配规则")
        self.btn_save_mapping = QPushButton("保存当前匹配")
        folder_layout.addWidget(self.folder_edit, 1)
        folder_layout.addWidget(self.btn_select_folder)
        folder_layout.addWidget(self.btn_scan)
        folder_layout.addWidget(self.btn_alias_rules)
        folder_layout.addWidget(self.btn_save_mapping)
        layout.addWidget(folder_group)

        hint = QLabel(
            "说明：软件会根据内部编号、显示名称和“匹配规则”自动识别子文件夹；"
            "如果自动匹配不对，可以直接在“匹配文件夹”列手动选择。"
            "预检查会同时检查 R/G 图片、Analysis V2 资源、MvImageID 环境和插件目录。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666666;")
        layout.addWidget(hint)

        table_group = QGroupBox("预检查结果")
        self.table_group = table_group
        self.preview_row_height = 38
        self.preview_header_height = 36
        table_group.setMinimumHeight(282)
        table_group.setMaximumHeight(282)
        table_layout = QVBoxLayout(table_group)
        table_layout.setContentsMargins(12, 14, 12, 10)
        table_layout.setSpacing(0)

        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(["蛋白", "匹配文件夹", "G", "R", "DIC", "Merge", "V2资源", "环境", "状态"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(self.preview_row_height)
        self.table.setMinimumHeight(228)
        self.table.setMaximumHeight(228)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setFocusPolicy(Qt.NoFocus)

        header = self.table.horizontalHeader()
        header.setFixedHeight(self.preview_header_height)
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in [2, 3, 4, 5, 6, 7, 8]:
            header.setSectionResizeMode(column, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 78)
        self.table.setColumnWidth(2, 44)
        self.table.setColumnWidth(3, 44)
        self.table.setColumnWidth(4, 56)
        self.table.setColumnWidth(5, 66)
        self.table.setColumnWidth(6, 82)
        self.table.setColumnWidth(7, 66)
        self.table.setColumnWidth(8, 82)
        table_layout.addWidget(self.table)
        layout.addWidget(table_group, 2)

        progress_layout = QHBoxLayout()
        self.progress_label = QLabel("等待开始")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar, 1)
        layout.addLayout(progress_layout)

        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_group)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMinimumHeight(90)
        self.log_edit.setMaximumHeight(150)
        log_layout.addWidget(self.log_edit)
        layout.addWidget(log_group)

        button_layout = QHBoxLayout()
        self.btn_start = QPushButton("开始批量分析")
        self.btn_cancel_next = QPushButton("取消后续分析")
        self.btn_close = QPushButton("关闭")
        self.btn_cancel_next.setEnabled(False)
        button_layout.addStretch()
        button_layout.addWidget(self.btn_start)
        button_layout.addWidget(self.btn_cancel_next)
        button_layout.addWidget(self.btn_close)
        layout.addLayout(button_layout)

        self.btn_select_folder.clicked.connect(self.select_folder)
        self.btn_scan.clicked.connect(self.scan_parent_folder)
        self.btn_alias_rules.clicked.connect(self.open_alias_rules)
        self.btn_save_mapping.clicked.connect(self.save_current_mapping_as_rules)
        self.btn_start.clicked.connect(self.start_batch_analysis)
        self.btn_cancel_next.clicked.connect(self.cancel_after_current)
        self.btn_close.clicked.connect(self.close)

    def apply_primary_button_styles(self):
        """强制主按钮保持蓝底白字。

        这两个按钮在父窗口/全局 QSS 影响下，普通状态可能被渲染成白底，
        只有 hover 才变蓝。这里给按钮本身设置局部样式，确保普通状态也
        始终为蓝色主按钮。
        """
        primary_css = """
            QPushButton#BatchPrimaryButton {
                min-height: 34px;
                padding: 5px 16px;
                border: 1px solid #1769E0;
                border-radius: 6px;
                background-color: #1769E0;
                color: #FFFFFF;
                font-weight: 600;
            }
            QPushButton#BatchPrimaryButton:hover {
                background-color: #0F5ED7;
                border-color: #0F5ED7;
                color: #FFFFFF;
            }
            QPushButton#BatchPrimaryButton:pressed {
                background-color: #0B4DB5;
                border-color: #0B4DB5;
                color: #FFFFFF;
            }
            QPushButton#BatchPrimaryButton:disabled {
                background-color: #EEF4FB;
                border-color: #DDE6F2;
                color: #8A97A8;
            }
        """
        for name in ["btn_select_folder", "btn_start"]:
            if not hasattr(self, name):
                continue
            button = getattr(self, name)
            button.setObjectName("BatchPrimaryButton")
            button.setFlat(False)
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setMinimumHeight(34)
            button.setStyleSheet(primary_css)

    def apply_modern_style(self):
        """应用批量分析弹窗局部样式。

        说明：
        1. 此弹窗从病例详情页打开，必须显式设置 QDialog/QWidget 背景，
           避免父页面透明背景样式传递后出现黑底。
        2. 只处理 UI 显示，不修改任何批量分析业务逻辑。
        """
        self.setObjectName("BatchAnalysisDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)

        # 标题、当前病例、说明文字对象名。
        for label in self.findChildren(QLabel):
            text = label.text().strip()
            if text == "批量蛋白分析":
                label.setObjectName("BatchDialogTitle")
            elif text.startswith("当前病例："):
                label.setObjectName("BatchCaseInfo")
            elif text.startswith("说明："):
                label.setObjectName("BatchHintLabel")

        # 分组卡片。
        for group in self.findChildren(QGroupBox):
            group.setObjectName("BatchGroup")
            group.setAttribute(Qt.WA_StyledBackground, True)

        # 关键控件。
        if hasattr(self, "folder_edit"):
            self.folder_edit.setObjectName("BatchPathEdit")
            self.folder_edit.setMinimumHeight(34)

        if hasattr(self, "table"):
            self.table.setObjectName("BatchPreviewTable")
            self.table.verticalHeader().setDefaultSectionSize(getattr(self, "preview_row_height", 38))
            self.table.horizontalHeader().setFixedHeight(getattr(self, "preview_header_height", 38))
            self.table.setShowGrid(True)
            self.table.setAlternatingRowColors(True)
            self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setFocusPolicy(Qt.NoFocus)

        if hasattr(self, "log_edit"):
            self.log_edit.setObjectName("BatchLogEdit")
            self.log_edit.setMinimumHeight(96)

        if hasattr(self, "progress_bar"):
            self.progress_bar.setObjectName("BatchProgressBar")
            self.progress_bar.setTextVisible(True)
            self.progress_bar.setMinimumHeight(18)

        # 按钮对象名。
        for name in ["btn_select_folder", "btn_start"]:
            if hasattr(self, name):
                getattr(self, name).setObjectName("BatchPrimaryButton")

        for name in ["btn_scan", "btn_alias_rules", "btn_save_mapping", "btn_cancel_next", "btn_close"]:
            if hasattr(self, name):
                getattr(self, name).setObjectName("BatchNeutralButton")

        for button in self.findChildren(QPushButton):
            button.setMinimumHeight(34)
            button.setCursor(Qt.PointingHandCursor if button.isEnabled() else Qt.ArrowCursor)

        self.setStyleSheet("""
            QDialog#BatchAnalysisDialog {
                background-color: #F5F8FC;
                color: #1F2D3D;
                font-family: "Microsoft YaHei";
                font-size: 13px;
            }

            QDialog#BatchAnalysisDialog QWidget {
                color: #1F2D3D;
                background: transparent;
            }

            QLabel#BatchDialogTitle {
                color: #102A43;
                font-size: 22px;
                font-weight: 700;
                padding: 2px 0 0 0;
            }

            QLabel#BatchCaseInfo {
                color: #5E6B7A;
                font-size: 13px;
                padding: 2px 0 4px 0;
            }

            QLabel#BatchHintLabel {
                color: #5E6B7A;
                font-size: 12px;
                line-height: 1.4;
            }

            QGroupBox#BatchGroup {
                background-color: #FFFFFF;
                border: 1px solid #DDE6F2;
                border-radius: 8px;
                margin-top: 12px;
                padding: 14px 12px 12px 12px;
                color: #102A43;
                font-weight: 700;
            }

            QGroupBox#BatchGroup::title {
                subcontrol-origin: margin;
                left: 12px;
                top: 0px;
                padding: 0px 6px;
                color: #1769E0;
                background-color: #F5F8FC;
            }

            QLineEdit#BatchPathEdit {
                background-color: #FFFFFF;
                border: 1px solid #DDE6F2;
                border-radius: 6px;
                padding: 5px 10px;
                color: #1F2D3D;
                selection-background-color: #DCEBFF;
            }

            QLineEdit#BatchPathEdit:focus {
                border-color: #1769E0;
            }

            QPushButton#BatchPrimaryButton {
                min-height: 34px;
                padding: 5px 16px;
                border: 1px solid #1769E0;
                border-radius: 6px;
                background-color: #1769E0;
                color: #FFFFFF;
                font-weight: 600;
            }

            QPushButton#BatchPrimaryButton:hover {
                background-color: #0F5ED7;
                border-color: #0F5ED7;
            }

            QPushButton#BatchPrimaryButton:pressed {
                background-color: #0B4DB5;
                border-color: #0B4DB5;
            }

            QPushButton#BatchPrimaryButton:disabled {
                background-color: #EEF4FB;
                border-color: #DDE6F2;
                color: #8A97A8;
            }

            QPushButton#BatchNeutralButton {
                min-height: 34px;
                padding: 5px 14px;
                border: 1px solid #DDE6F2;
                border-radius: 6px;
                background-color: #FFFFFF;
                color: #1F2D3D;
                font-weight: 500;
            }

            QPushButton#BatchNeutralButton:hover {
                background-color: #F2F7FF;
                border-color: #BCD7FF;
                color: #1769E0;
            }

            QPushButton#BatchNeutralButton:pressed {
                background-color: #EAF2FF;
                border-color: #1769E0;
            }

            QPushButton#BatchNeutralButton:disabled {
                background-color: #F8FAFD;
                border-color: #E8EEF6;
                color: #9AA6B2;
            }

            QTableWidget#BatchPreviewTable {
                background-color: #FFFFFF;
                alternate-background-color: #F8FAFD;
                gridline-color: #DDE6F2;
                border: 1px solid #DDE6F2;
                border-radius: 6px;
                selection-background-color: #DCEBFF;
                selection-color: #1F2D3D;
                color: #1F2D3D;
                outline: none;
            }

            QTableWidget#BatchPreviewTable::item {
                padding: 0px 4px;
            }

            QTableWidget#BatchPreviewTable::item:selected {
                background-color: #DCEBFF;
                color: #1F2D3D;
                outline: none;
            }

            QTableWidget#BatchPreviewTable::item:focus {
                border: none;
                outline: none;
            }

            QHeaderView::section {
                background-color: #EEF4FB;
                color: #1F2D3D;
                font-weight: 700;
                border: none;
                border-right: 1px solid #DDE6F2;
                border-bottom: 1px solid #DDE6F2;
                padding: 6px 5px;
                min-height: 26px;
            }

            QComboBox {
                background-color: #FFFFFF;
                border: 1px solid #DDE6F2;
                border-radius: 5px;
                padding: 0px 26px 0px 8px;
                color: #1F2D3D;
                min-height: 28px;
            }

            QComboBox:hover, QComboBox#BatchFolderCombo:hover {
                background-color: #F8FBFF;
                border-color: #BFD5F5;
            }

            QComboBox:focus, QComboBox#BatchFolderCombo:focus {
                border-color: #1769E0;
            }

            QComboBox::drop-down, QComboBox#BatchFolderCombo::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 24px;
                border: none;
                background: transparent;
            }

            QComboBox QAbstractItemView, QComboBox#BatchFolderCombo QAbstractItemView {
                background-color: #FFFFFF;
                border: 1px solid #DDE6F2;
                selection-background-color: #DCEBFF;
                selection-color: #1F2D3D;
                color: #1F2D3D;
                outline: none;
                padding: 4px;
            }

            QProgressBar#BatchProgressBar {
                background-color: #EEF4FB;
                border: 1px solid #DDE6F2;
                border-radius: 6px;
                min-height: 18px;
                text-align: center;
                color: #1F2D3D;
            }

            QProgressBar#BatchProgressBar::chunk {
                background-color: #1769E0;
                border-radius: 5px;
            }

            QTextEdit#BatchLogEdit {
                background-color: #FFFFFF;
                border: 1px solid #DDE6F2;
                border-radius: 6px;
                color: #1F2D3D;
                padding: 6px;
            }

            QScrollBar:vertical {
                background-color: #EEF4FB;
                width: 10px;
                margin: 0px;
                border: none;
            }

            QScrollBar::handle:vertical {
                background-color: #DDE6F2;
                min-height: 30px;
                border-radius: 5px;
            }

            QScrollBar::handle:vertical:hover {
                background-color: #BCD7FF;
            }

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
                background: none;
                border: none;
            }
        """)

        self.apply_primary_button_styles()


    # ---------- 匹配规则 ----------

    @staticmethod
    def normalize_text(text: str) -> str:
        return FolderAliasStore.normalize_text(text)

    def get_protein_items(self) -> List[dict]:
        fixed_items = []
        for configured_key in self.config.get_protein_keys():
            key = str(configured_key or "").strip()
            if not key:
                continue
            name = str(self.config.get_protein_display_name(key) or "").strip()
            fixed_items.append({
                "key": key,
                "name": name or PROTEIN_DISPLAY_FALLBACK.get(key, key),
            })
        return fixed_items

    def build_folder_alias_map(self) -> Dict[str, List[str]]:
        """返回 norm_alias -> [protein_key]，保留冲突信息。"""
        alias_map: Dict[str, List[str]] = {}
        stored_aliases = self.alias_store.load_aliases()

        for item in self.get_protein_items():
            key = str(item.get("key", "") or "").strip()
            name = str(item.get("name", "") or "").strip()
            if not key:
                continue

            candidates = [key, name, name.replace("-", ""), name.replace("_", "")]
            candidates.extend(stored_aliases.get(key, []))
            candidates.extend(DEFAULT_FOLDER_ALIASES.get(key, []))

            for candidate in candidates:
                norm = self.normalize_text(candidate)
                if not norm:
                    continue
                alias_map.setdefault(norm, [])
                if key not in alias_map[norm]:
                    alias_map[norm].append(key)

        return alias_map

    def match_folder_to_keys(self, folder_name: str, alias_map: Dict[str, List[str]]) -> List[str]:
        child_norm = self.normalize_text(folder_name)
        if not child_norm:
            return []

        # 第一优先级：精确匹配。
        exact = alias_map.get(child_norm, [])
        if exact:
            return list(exact)

        # 第二优先级：包含匹配，例如 protein1_Q9BYW3 可以匹配 Q9BYW3。
        matched: List[str] = []
        for alias, keys in alias_map.items():
            if not alias:
                continue
            if alias in child_norm:
                for key in keys:
                    if key not in matched:
                        matched.append(key)
        return matched

    def open_alias_rules(self):
        dialog = FolderAliasDialog(self.get_protein_items(), self.alias_store, self)
        if dialog.exec() == QDialog.Accepted:
            self.scan_parent_folder()

    def save_current_mapping_as_rules(self):
        mapping: Dict[str, str] = {}
        for row in self.scan_rows:
            folder = row.get("folder", "")
            if folder:
                mapping[row.get("protein_key", "")] = Path(folder).name
        changed = self.alias_store.add_current_mapping(mapping)
        if changed:
            show_batch_information(self, "提示", f"已保存 {changed} 条当前匹配关系到批量匹配规则。")
        else:
            show_batch_information(self, "提示", "当前匹配关系已经在规则中，无需重复保存。")
        self.scan_parent_folder()

    # ---------- 扫描与表格 ----------

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择包含蛋白子文件夹的上一级目录", "")
        if not folder:
            return

        new_parent = Path(folder)

        # 如果用户重新选择了另一个总文件夹，必须清空上一次预检查保留的手动选择。
        # 否则 scan_parent_folder() 会优先沿用旧路径，导致界面路径变了但预检查结果不刷新。
        if self.is_different_parent_folder(new_parent, self._last_scanned_parent_folder):
            self.scan_rows = []

        self.folder_edit.setText(folder)
        self.parent_folder = new_parent
        self.scan_parent_folder()

    def is_different_parent_folder(self, left: Optional[Path], right: Optional[Path]) -> bool:
        """判断两个总文件夹是否不同。兼容 Windows 大小写和不存在路径。"""
        if left is None and right is None:
            return False
        if left is None or right is None:
            return True
        try:
            return str(left.resolve()).lower() != str(right.resolve()).lower()
        except Exception:
            return str(left.absolute()).lower() != str(right.absolute()).lower()

    # ---------- Pipeline / 环境预检查 ----------

    def get_project_root(self) -> Path:
        return get_application_root()

    def resolve_project_path(self, path_value) -> Path:
        text = str(path_value or "").strip()
        if not text:
            return Path("")
        path = Path(text)
        if path.is_absolute():
            return path
        return self.get_project_root() / path

    def check_pipeline_for_protein(self, protein_key: str) -> dict:
        key = str(protein_key or "").strip()
        formal = FORMAL_PROTEIN_PARTS.get(key)
        display_name = formal[0] if formal is not None else key or "<empty>"
        if formal is None:
            return {
                "ok": False,
                "text": "缺失",
                "path": "",
                "detail": "{}（{}）：不支持的正式 protein_key。".format(display_name, key),
            }

        assets = list(ANALYSIS_V2_HEAD_ASSETS)
        if formal[1] == "tail":
            assets.extend(ANALYSIS_V2_TAIL_ASSETS)

        project_root = self.get_project_root()
        missing = []
        checked_paths = []
        for label, relative_path in assets:
            path = project_root / Path(relative_path)
            checked_paths.append(str(path))
            if not path.is_file():
                missing.append("缺少 {}：{}".format(label, path))

        if missing:
            return {
                "ok": False,
                "text": "缺失",
                "path": "",
                "detail": "{}（{}）：{}".format(display_name, key, "；".join(missing)),
            }
        return {
            "ok": True,
            "text": "正常",
            "path": "；".join(checked_paths),
            "detail": "{}（{}）：Analysis V2 运行资源正常。".format(display_name, key),
        }

    def check_mvimageid_environment(self) -> dict:
        source_dir = self.config.get_source_project_dir()
        python_exe = self.config.get_python_exe()
        plugins_dir = self.config.get_plugins_directory()
        module_name = str(self.config.get_module_name() or "").strip()

        details: List[str] = []
        ok = True

        if not module_name:
            ok = False
            details.append("MvImageID module_name 未配置")

        source_path = Path(str(source_dir or "")).expanduser()
        if not source_path.exists():
            ok = False
            details.append(f"源码目录不存在：{source_path}")

        python_path = Path(str(python_exe or "")).expanduser()
        if not python_path.exists() or not python_path.is_file():
            ok = False
            details.append(f"Python解释器不存在：{python_path}")

        try:
            runner = MvImageIDRunner(
                source_project_dir=str(source_dir),
                python_exe=str(python_exe),
                module_name=module_name,
                plugins_directory=str(plugins_dir),
                log_file="",
            )
            resolved_python = runner.get_python_executable()
            if not resolved_python.exists():
                ok = False
                details.append(f"Python解释器不存在：{resolved_python}")
        except Exception as e:
            ok = False
            details.append(str(e))

        plugins_text = str(plugins_dir or "").strip()
        get_mvimageid = getattr(self.config, "get_mvimageid", None)
        if get_mvimageid is not None:
            configured_plugins = str(
                get_mvimageid("plugins_directory", "") or ""
            ).strip()
            if not configured_plugins:
                ok = False
                details.append("MvImageID plugins_directory 未配置")
        if plugins_text:
            plugins_path = Path(plugins_text).expanduser()
            if not plugins_path.exists():
                ok = False
                details.append(f"插件目录不存在：{plugins_path}")

        if ok:
            return {"ok": True, "text": "正常", "detail": "MvImageID 环境正常。"}
        return {"ok": False, "text": "异常", "detail": "；".join(details) if details else "MvImageID 环境异常。"}

    def scan_parent_folder(self):
        folder_text = self.folder_edit.text().strip()

        new_parent: Optional[Path] = self.parent_folder
        if folder_text:
            new_parent = Path(folder_text)

        parent_changed = self.is_different_parent_folder(new_parent, self._last_scanned_parent_folder)
        self.parent_folder = new_parent

        self.available_folders = []
        if self.parent_folder and self.parent_folder.exists():
            self.available_folders = sorted(
                [child for child in self.parent_folder.iterdir() if child.is_dir()],
                key=lambda p: p.name.lower(),
            )

        env_check = self.check_mvimageid_environment()

        alias_map = self.build_folder_alias_map()
        candidates_by_key: Dict[str, List[Path]] = {}
        ambiguous_by_key: Dict[str, List[str]] = {}

        for child in self.available_folders:
            matched_keys = self.match_folder_to_keys(child.name, alias_map)
            if len(matched_keys) == 1:
                candidates_by_key.setdefault(matched_keys[0], []).append(child)
            elif len(matched_keys) > 1:
                for key in matched_keys:
                    ambiguous_by_key.setdefault(key, []).append(child.name)

        # 同一个总文件夹内，允许保留用户手动选择；
        # 一旦切换到另一个总文件夹，必须丢弃旧选择，重新自动匹配新目录。
        old_selection = {} if parent_changed else {
            row.get("protein_key", ""): row.get("folder", "")
            for row in self.scan_rows
            if row.get("folder")
        }

        self.scan_rows = []
        for protein in self.get_protein_items():
            key = str(protein.get("key", "") or "").strip()
            name = str(protein.get("name", "") or "").strip() or key

            folder: Optional[Path] = None
            status_note = ""

            # 如果用户之前手动选过，重新扫描后优先保留这个选择。
            old_folder_text = old_selection.get(key, "")
            if old_folder_text and Path(old_folder_text).exists():
                folder = Path(old_folder_text)

            if folder is None:
                candidates = candidates_by_key.get(key, [])
                ambiguous_names = ambiguous_by_key.get(key, [])
                if len(candidates) == 1 and not ambiguous_names:
                    folder = candidates[0]
                elif len(candidates) > 1:
                    status_note = "匹配多个文件夹"
                elif ambiguous_names:
                    status_note = "匹配名冲突"

            channels = self.scan_channels(folder) if folder else {"G": 0, "R": 0, "DIC": 0, "Merge": 0}
            pipeline_check = self.check_pipeline_for_protein(key)
            status = self.get_status_by_folder_and_channels(folder, channels, pipeline_check, env_check, status_note)

            self.scan_rows.append({
                "protein_key": key,
                "protein_name": name,
                "folder": str(folder) if folder else "",
                "channels": channels,
                "pipeline": pipeline_check,
                "environment": env_check,
                "status": status,
            })

        self._last_scanned_parent_folder = self.parent_folder
        self.refresh_table()

    def get_status_by_folder_and_channels(
        self,
        folder: Optional[Path],
        channels: Dict[str, int],
        pipeline_check: Optional[dict] = None,
        env_check: Optional[dict] = None,
        status_note: str = "",
    ) -> str:
        if status_note:
            return status_note
        if folder is None:
            return "未匹配"

        # 统一使用 ImageChannelMatcher 的视野级结果判断是否可分析。
        # G/R 总数大于 0 但不在同一视野时，也不能认为可分析。
        if channels.get("_duplicate_fields", 0) > 0:
            return "图片重复"
        if channels.get("_complete_fields", 0) <= 0:
            return "缺少G或R"

        if pipeline_check is not None and not pipeline_check.get("ok", False):
            return "V2资源缺失"
        if env_check is not None and not env_check.get("ok", False):
            return "环境异常"
        return "可分析"

    def scan_channels(self, folder: Optional[Path]) -> Dict[str, int]:
        """
        批量预检查统一使用 core.image_channel_matcher.ImageChannelMatcher。

        不再在批量窗口里单独写一套 G/R/DIC/Merge 判断逻辑，避免出现：
        预检查显示可分析，但实际导入/分析时识别规则不一致。
        """
        counts = {
            "G": 0,
            "R": 0,
            "DIC": 0,
            "Merge": 0,
            "_total_fields": 0,
            "_complete_fields": 0,
            "_duplicate_fields": 0,
            "_unmatched_files": 0,
        }
        if not folder or not folder.exists():
            return counts

        matcher = ImageChannelMatcher(self.config.get_image_rule())
        result = matcher.scan_folder(folder)

        counts["G"] = result.channel_count("G")
        counts["R"] = result.channel_count("R")
        counts["DIC"] = result.channel_count("DIC")
        counts["Merge"] = result.channel_count("Merge")
        counts["_total_fields"] = result.total_fields
        counts["_complete_fields"] = result.complete_count
        counts["_duplicate_fields"] = sum(1 for item in result.fields if item.duplicates)
        counts["_unmatched_files"] = len(result.unmatched_files)
        return counts

    def style_folder_combo(self, combo: QComboBox) -> None:
        """统一预检表格中的文件夹下拉框样式。

        这里不再让下拉框占满整行高度。QComboBox 本身的文字绘制基线和
        QTableWidgetItem 不完全一致，控件过高时视觉上会偏下。现在采用：
        表格行高 38px，下拉框高度 28px，由单元格容器做垂直居中。
        """
        combo.setObjectName("BatchFolderCombo")
        combo.setFixedHeight(28)
        combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        combo.setMaxVisibleItems(10)
        combo.setFocusPolicy(Qt.NoFocus)
        combo.setContentsMargins(0, 0, 0, 0)
        try:
            combo.setFrame(False)
        except Exception:
            pass

        # 强制使用独立 QListView，避免继承父窗口/系统暗色弹出列表。
        view = QListView(combo)
        view.setUniformItemSizes(True)
        combo.setView(view)

        arrow_icon = (Path(__file__).resolve().parents[1] / "assets" / "icons" / "form_chevron_down.svg").as_posix()
        combo.setStyleSheet(f"""
            QComboBox#BatchFolderCombo {{
                background-color: transparent;
                border: none;
                border-radius: 0px;
                padding: 0px 24px 0px 14px;
                color: #1F2D3D;
                min-height: 28px;
                max-height: 28px;
            }}
            QComboBox#BatchFolderCombo:hover {{
                background-color: #F2F7FF;
                border: none;
            }}
            QComboBox#BatchFolderCombo:focus {{
                background-color: #F2F7FF;
                border: none;
                outline: none;
            }}
            QComboBox#BatchFolderCombo::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 24px;
                border: none;
                background: transparent;
            }}
            QComboBox#BatchFolderCombo::down-arrow {{
                image: url("{arrow_icon}");
                width: 10px;
                height: 10px;
            }}
        """)
        view.setStyleSheet("""
            QListView {
                background-color: #FFFFFF;
                border: 1px solid #D8E3F0;
                color: #1F2D3D;
                outline: none;
                padding: 3px;
                selection-background-color: #DCEBFF;
                selection-color: #1F2D3D;
            }
            QListView::item {
                min-height: 28px;
                padding: 4px 8px;
                color: #1F2D3D;
                background-color: #FFFFFF;
            }
            QListView::item:hover {
                background-color: #F2F7FF;
                color: #1769E0;
            }
            QListView::item:selected {
                background-color: #DCEBFF;
                color: #1F2D3D;
            }
        """)

    def make_combo_cell_widget(self, combo: QComboBox) -> QWidget:
        wrapper = QWidget()
        wrapper.setObjectName("BatchComboCell")
        wrapper.setAttribute(Qt.WA_StyledBackground, True)
        wrapper.setStyleSheet("""
            QWidget#BatchComboCell {
                background-color: transparent;
                border: none;
            }
        """)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(combo)
        layout.setAlignment(combo, Qt.AlignVCenter)
        return wrapper

    def adjust_preview_table_height(self) -> None:
        """按实际蛋白行数精确计算预检表格高度，避免底部空白或行被裁切。"""
        if not hasattr(self, "table"):
            return

        row_count = max(self.table.rowCount(), 1)
        row_height = getattr(self, "preview_row_height", 38)
        header_height = getattr(self, "preview_header_height", 38)

        for row in range(self.table.rowCount()):
            self.table.setRowHeight(row, row_height)

        table_height = header_height + row_count * row_height + 2
        self.table.setFixedHeight(table_height)

        if hasattr(self, "table_group"):
            group_height = table_height + 54
            self.table_group.setMinimumHeight(group_height)
            self.table_group.setMaximumHeight(group_height)

    def get_folder_combo_from_row(self, row_index: int) -> Optional[QComboBox]:
        cell_widget = self.table.cellWidget(row_index, 1)
        if isinstance(cell_widget, QComboBox):
            return cell_widget
        if isinstance(cell_widget, QWidget):
            return cell_widget.findChild(QComboBox)
        return None

    def refresh_table(self):
        self._refreshing_table = True
        try:
            self.table.setRowCount(len(self.scan_rows))
            self.table.verticalHeader().setDefaultSectionSize(getattr(self, "preview_row_height", 38))
            folder_names = [folder.name for folder in self.available_folders]

            for row_index, row in enumerate(self.scan_rows):
                self.table.setRowHeight(row_index, getattr(self, "preview_row_height", 38))
                channels = row.get("channels", {})

                protein_item = QTableWidgetItem(str(row.get("protein_name", "")))
                protein_item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_index, 0, protein_item)

                combo = QComboBox()
                self.style_folder_combo(combo)
                combo.addItem("- 未选择 -", "")
                for name in folder_names:
                    combo.addItem(name, name)
                current_folder = row.get("folder", "")
                if current_folder:
                    current_name = Path(current_folder).name
                    idx = combo.findData(current_name)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                combo.currentIndexChanged.connect(lambda _idx, r=row_index: self.on_folder_combo_changed(r))
                self.table.setCellWidget(row_index, 1, self.make_combo_cell_widget(combo))

                pipeline_check = row.get("pipeline", {}) or {}
                env_check = row.get("environment", {}) or {}
                values = [
                    self.flag_text(channels.get("G", 0)),
                    self.flag_text(channels.get("R", 0)),
                    self.optional_text(channels.get("DIC", 0)),
                    self.optional_text(channels.get("Merge", 0)),
                    str(pipeline_check.get("text", "")),
                    str(env_check.get("text", "")),
                    row.get("status", ""),
                ]
                for offset, value in enumerate(values, start=2):
                    item = QTableWidgetItem(str(value))
                    item.setTextAlignment(Qt.AlignCenter)
                    if offset == 6:
                        item.setToolTip(str(pipeline_check.get("detail", pipeline_check.get("path", ""))))
                        self.apply_check_color(item, bool(pipeline_check.get("ok", False)))
                    elif offset == 7:
                        item.setToolTip(str(env_check.get("detail", "")))
                        self.apply_check_color(item, bool(env_check.get("ok", False)))
                    elif offset == 8:
                        self.apply_status_color(item, str(value))
                    self.table.setItem(row_index, offset, item)

            self.adjust_preview_table_height()
        finally:
            self._refreshing_table = False

    def on_folder_combo_changed(self, row_index: int):
        if self._refreshing_table:
            return
        if row_index < 0 or row_index >= len(self.scan_rows):
            return

        combo = self.get_folder_combo_from_row(row_index)
        folder_name = combo.currentData() if isinstance(combo, QComboBox) else ""
        folder_path: Optional[Path] = None
        if folder_name and self.parent_folder:
            folder_path = self.parent_folder / str(folder_name)

        channels = self.scan_channels(folder_path)
        protein_key = self.scan_rows[row_index].get("protein_key", "")
        pipeline_check = self.check_pipeline_for_protein(protein_key)
        env_check = self.check_mvimageid_environment()
        self.scan_rows[row_index]["folder"] = str(folder_path) if folder_path else ""
        self.scan_rows[row_index]["channels"] = channels
        self.scan_rows[row_index]["pipeline"] = pipeline_check
        self.scan_rows[row_index]["environment"] = env_check
        self.scan_rows[row_index]["status"] = self.get_status_by_folder_and_channels(folder_path, channels, pipeline_check, env_check)
        self.refresh_table()

    @staticmethod
    def apply_check_color(item: QTableWidgetItem, ok: bool):
        item.setForeground(Qt.darkGreen if ok else Qt.red)

    @staticmethod
    def apply_status_color(item: QTableWidgetItem, status: str):
        if status in ["可分析", "已完成"]:
            item.setForeground(Qt.darkGreen)
        elif status in ["分析中"]:
            item.setForeground(Qt.blue)
        elif status in ["失败", "缺少G或R", "图片重复", "V2资源缺失", "环境异常", "匹配多个文件夹", "匹配名冲突", "文件夹重复"]:
            item.setForeground(Qt.red)
        else:
            item.setForeground(Qt.gray)

    @staticmethod
    def flag_text(count: int) -> str:
        return f"√ {count}" if count > 0 else "-"

    @staticmethod
    def optional_text(count: int) -> str:
        return f"可选 {count}" if count > 0 else "可选"

    def validate_duplicate_folders(self) -> List[str]:
        folder_to_keys: Dict[str, List[str]] = {}
        for row in self.scan_rows:
            folder = row.get("folder", "")
            if folder:
                folder_to_keys.setdefault(str(Path(folder).resolve()), []).append(row.get("protein_key", ""))
        duplicates = []
        duplicate_paths = {path for path, keys in folder_to_keys.items() if len(keys) > 1}
        for row in self.scan_rows:
            folder = row.get("folder", "")
            if folder and str(Path(folder).resolve()) in duplicate_paths:
                row["status"] = "文件夹重复"
                duplicates.append(f"{row.get('protein_name', '')} → {Path(folder).name}")
        if duplicates:
            self.refresh_table()
        return duplicates

    def get_ready_tasks(self) -> List[dict]:
        duplicates = self.validate_duplicate_folders()
        if duplicates:
            return []

        tasks = []
        for row in self.scan_rows:
            if row.get("status") == "可分析":
                tasks.append({
                    "protein_key": row["protein_key"],
                    "protein_name": row["protein_name"],
                    "folder": row["folder"],
                })
        return tasks

    # ---------- 启动分析 ----------

    def start_batch_analysis(self):
        if not self.case_data or not self.case_data.get("id"):
            show_batch_information(self, "提示", "当前病例无效，请先选择病例。")
            return

        duplicates = self.validate_duplicate_folders()
        if duplicates:
            show_batch_warning(
                self,
                "文件夹重复",
                "同一个文件夹不能同时分配给多个蛋白，请调整后再开始分析：\n" + "\n".join(duplicates[:20]),
            )
            return

        tasks = self.get_ready_tasks()
        if not tasks:
            show_batch_information(self, "提示", "没有可分析的蛋白文件夹。请检查文件夹匹配、R/G 图片、Analysis V2 运行资源和 MvImageID 环境。")
            return

        existing_names = self.get_existing_protein_names(tasks)
        if existing_names:
            reply = show_batch_question(
                self,
                "确认覆盖分析",
                "当前病例已有以下蛋白分析结果：\n"
                + "、".join(existing_names)
                + "\n\n继续批量分析会覆盖这些蛋白旧结果，并清空对应 cp_input / cp_output。\n"
                "不会影响其他未参与批量分析的蛋白。\n\n是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        reply = show_batch_question(
            self,
            "开始批量分析",
            f"将按顺序分析 {len(tasks)} 个蛋白。\n批量分析期间主界面暂时不可操作。\n\n是否开始？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        self.set_running_state(True)
        self.log_edit.clear()
        self.progress_bar.setValue(0)
        self.progress_label.setText("开始批量分析...")

        self.worker = BatchProteinWorker(self.case_data, tasks, self.config, self.database, self)
        self.worker.log_signal.connect(self.append_log)
        self.worker.progress_signal.connect(self.on_progress)
        self.worker.task_status_signal.connect(self.on_task_status)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def get_existing_protein_names(self, tasks: List[dict]) -> List[str]:
        existing: List[str] = []
        case_id = self.case_data.get("id")
        try:
            rows = self.database.get_protein_analysis_by_case(case_id)
        except Exception:
            return existing

        existing_keys = set()
        for row in rows:
            row_name = str(row.get("protein_name", "") or "").strip()
            row_key = self.config.normalize_protein_key(row_name)
            if row_key:
                existing_keys.add(row_key)

        for task in tasks:
            if task["protein_key"] in existing_keys:
                existing.append(task["protein_name"])
        return existing

    def on_progress(self, index: int, total: int, protein_name: str):
        percent = int(index / max(total, 1) * 100)
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"正在分析：{protein_name}（{index}/{total}）")

    def on_task_status(self, protein_key: str, status: str):
        for row in self.scan_rows:
            if row.get("protein_key") == protein_key:
                row["status"] = status
                break
        self.refresh_table()

    def on_finished(self, results: list, errors: list):
        # Analysis V2 worker 只有在公共 publication 与数据库事务成功后才返回结果。
        saved_count = len(results)

        self.set_running_state(False)
        self.progress_bar.setValue(100)
        self.progress_label.setText(f"批量分析完成：成功 {saved_count} 个，失败/跳过 {len(errors)} 个")
        self.batch_finished.emit()

        if errors:
            detail_text = self.build_finished_detail(errors)
            summary_text = (
                f"批量分析完成。\n\n"
                f"成功：{saved_count} 个\n"
                f"失败/跳过：{len(errors)} 个\n\n"
                "详细信息请在下方日志中查看，可滚动浏览，也可以复制。"
            )
            show_long_message_dialog(
                self,
                title="批量分析完成",
                summary=summary_text,
                detail=detail_text,
                level="warning",
            )
        else:
            show_batch_information(self, "批量分析完成", f"已完成 {saved_count} 个蛋白分析。")

    @staticmethod
    def build_finished_detail(errors: list) -> str:
        """生成批量完成弹窗的详细日志。

        注意：详细内容放在可滚动文本框中，不再直接塞进 QMessageBox 主文本，
        避免长日志把弹窗撑到超过屏幕。
        """
        lines: List[str] = []
        for index, error in enumerate(errors, start=1):
            protein_name = str(error.get("protein_name", "") or "")
            protein_key = str(error.get("protein_key", "") or "")
            message = str(error.get("message", "") or "")

            title = protein_name
            if protein_key and protein_key not in protein_name:
                title = f"{protein_name}（{protein_key}）" if protein_name else protein_key

            lines.append(f"[{index}] {title}")
            lines.append(message or "未知错误。")
            lines.append("-" * 72)

        return "\n".join(lines).strip()

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

    def save_result_to_database(self, result: dict) -> Tuple[bool, str]:
        case_id = result.get("case_id")
        if not case_id:
            return False, "当前病例缺少数据库 ID。"

        total = result.get("total", {})
        rows = result.get("rows", [])
        image_csv = result.get("image_csv", "")

        try:
            analysis_id = self.database.save_protein_analysis(
                case_id=case_id,
                protein_name=result.get("protein_name", ""),
                protein_part=result.get("protein_part", ""),
                image_folder=result.get("image_folder", ""),
                output_folder=result.get("output_folder", ""),
                total_fields=total.get("field_count", 0),
                total_sperm_count=total.get("sperm_count", 0),
                positive_count=total.get("positive_count", 0),
                mean_intensity=total.get("mean_intensity", 0),
                expression_rate=total.get("expression_rate", 0),
                status="完成",
            )

            for item in rows:
                self.database.save_field_result(
                    analysis_id=analysis_id,
                    field_no=str(item.get("image_number", "")),
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
            f"{result.get('protein_name', '')} 结果已保存："
            f"视野数 {total.get('field_count', 0)}，"
            f"精子总数 {total.get('sperm_count', 0)}，"
            f"共定位数 {total.get('positive_count', 0)}，"
            f"标定率 {self.format_rate_for_display(total.get('expression_rate', 0))}，"
            f"荧光强度 {self.format_int_for_display(total.get('mean_intensity', 0))}。"
        )

    def cancel_after_current(self):
        if self.worker and self.worker.isRunning():
            self.worker.request_cancel_after_current()
            self.btn_cancel_next.setEnabled(False)

    def set_running_state(self, running: bool):
        self.btn_select_folder.setEnabled(not running)
        self.btn_scan.setEnabled(not running)
        self.btn_alias_rules.setEnabled(not running)
        self.btn_save_mapping.setEnabled(not running)
        self.table.setEnabled(not running)
        self.btn_start.setEnabled(not running)
        self.btn_cancel_next.setEnabled(running)
        self.btn_close.setEnabled(not running)
        for button in [self.btn_select_folder, self.btn_scan, self.btn_alias_rules, self.btn_save_mapping, self.btn_start, self.btn_cancel_next, self.btn_close]:
            button.setCursor(Qt.PointingHandCursor if button.isEnabled() else Qt.ArrowCursor)
        self.apply_primary_button_styles()

    def append_log(self, message: str):
        self.log_edit.append(str(message))

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            show_batch_information(self, "提示", "批量分析正在运行，暂时不能关闭窗口。")
            event.ignore()
            return
        super().closeEvent(event)
