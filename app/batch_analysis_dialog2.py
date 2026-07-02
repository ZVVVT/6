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
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from core.config_manager import ConfigManager
from core.image_importer import ImageImporter
from core.mvimageid_runner import MvImageIDRunner
from core.result_parser import ResultParser


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


class FolderAliasStore:
    """把批量文件夹匹配名保存到 config.ini 的 [BatchFolderAliases] 中。"""

    SECTION_NAME = "BatchFolderAliases"

    def __init__(self, config_path: Optional[Path] = None):
        if config_path is None:
            # app/batch_analysis_dialog.py -> 项目根目录/config.ini
            config_path = Path(__file__).resolve().parents[1] / "config.ini"
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
        self.resize(900, 420)
        self.setMinimumSize(800, 360)
        self.init_ui()
        self.load_table()

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
            QMessageBox.warning(
                self,
                "匹配名冲突",
                "以下匹配名同时配置到了多个蛋白，请修改后再保存：\n" + "\n".join(conflicts[:20]),
            )
            return

        self.alias_store.save_aliases(new_aliases)
        QMessageBox.information(self, "提示", "批量文件夹匹配规则已保存。")
        self.accept()


# =========================
# 批量分析后台线程
# =========================

class BatchProteinWorker(QThread):
    log_signal = Signal(str)
    progress_signal = Signal(int, int, str)
    task_status_signal = Signal(str, str)
    finished_signal = Signal(list, list)

    def __init__(self, case_data: dict, tasks: List[dict], config: ConfigManager, parent=None):
        super().__init__(parent)
        self.case_data = case_data
        self.tasks = tasks
        self.config = config
        self.cancel_after_current = False

    def request_cancel_after_current(self):
        self.cancel_after_current = True
        self.log_signal.emit("已请求取消后续分析：当前正在运行的蛋白会尽量完成，后续未开始项目将跳过。")

    def run(self):
        results = []
        errors = []
        total = len(self.tasks)

        for index, task in enumerate(self.tasks, start=1):
            protein_key = task["protein_key"]
            protein_name = task["protein_name"]

            if self.cancel_after_current:
                self.task_status_signal.emit(protein_key, "已取消")
                errors.append({
                    "protein_key": protein_key,
                    "protein_name": protein_name,
                    "message": "用户取消后续分析。",
                })
                continue

            self.progress_signal.emit(index, total, protein_name)
            self.task_status_signal.emit(protein_key, "分析中")
            self.log_signal.emit(f"========== 开始分析 {protein_name}（{protein_key}）[{index}/{total}] ==========")

            try:
                result = self.run_one_protein(task)
                results.append(result)
                self.task_status_signal.emit(protein_key, "已完成")
                self.log_signal.emit(f"{protein_name} 分析完成。")
            except Exception as e:
                message = str(e)
                self.task_status_signal.emit(protein_key, "失败")
                errors.append({
                    "protein_key": protein_key,
                    "protein_name": protein_name,
                    "message": message,
                })
                self.log_signal.emit(f"{protein_name} 分析失败：{message}")

        self.finished_signal.emit(results, errors)

    def run_one_protein(self, task: dict) -> dict:
        case_no = str(self.case_data.get("case_no", "") or "").strip()
        if not case_no:
            raise RuntimeError("当前病例编号为空。")

        protein_key = task["protein_key"]
        protein_name = task["protein_name"]
        protein_part = self.config.get_protein_part(protein_key)
        source_folder = Path(task["folder"])

        workspace_root = self.config.get_workspace_root()
        raw_folder = workspace_root / case_no / "raw_images" / protein_key
        cp_input_dir = workspace_root / case_no / "cp_input" / protein_key
        cp_output_dir = workspace_root / case_no / "cp_output" / protein_key

        self.log_signal.emit(f"{protein_name} 源图片目录：{source_folder}")
        self.log_signal.emit(f"{protein_name} 原始导入目录：{raw_folder}")
        self.log_signal.emit(f"{protein_name} 分析输入目录：{cp_input_dir}")
        self.log_signal.emit(f"{protein_name} 分析输出目录：{cp_output_dir}")

        # 1. 清空当前蛋白 raw_images，重新导入。
        if raw_folder.exists():
            shutil.rmtree(raw_folder)
        raw_folder.mkdir(parents=True, exist_ok=True)

        importer = ImageImporter(self.config.get_image_rule())
        imported_images = importer.copy_to_workspace(
            source_folder=str(source_folder),
            target_folder=str(raw_folder),
            protein_name=protein_key,
        )
        complete_items = [item for item in imported_images if item.get("status") == "完整"]
        if not complete_items:
            raise RuntimeError("没有完整的 R/G 视野，无法运行分析。")
        self.log_signal.emit(f"{protein_name} 导入完成：共 {len(imported_images)} 个视野，完整视野 {len(complete_items)} 个。")

        # 2. 清空并准备 cp_input。当前 Pipeline 只需要 R/G，DIC/Merge 作为原始记录保存即可。
        if cp_input_dir.exists():
            shutil.rmtree(cp_input_dir)
        cp_input_dir.mkdir(parents=True, exist_ok=True)

        copied_count = 0
        for item in complete_items:
            for channel in ["R", "G"]:
                source_path = item.get(channel, "")
                if not source_path:
                    continue
                source = Path(source_path)
                if not source.exists():
                    raise FileNotFoundError(f"输入图像不存在：{source}")
                target = cp_input_dir / source.name
                shutil.copy2(source, target)
                copied_count += 1

        if copied_count <= 0:
            raise RuntimeError("没有复制任何 R/G 图像到分析输入目录。")
        self.log_signal.emit(f"{protein_name} 已准备分析输入图像：{copied_count} 张。")

        # 3. 清空 cp_output，避免覆盖分析时旧图片混入新结果。
        if cp_output_dir.exists():
            shutil.rmtree(cp_output_dir)
        cp_output_dir.mkdir(parents=True, exist_ok=True)

        # 4. 运行 MvImageID。
        self.run_mvimageid(
            protein_key=protein_key,
            protein_name=protein_name,
            cp_input_dir=cp_input_dir.resolve(),
            cp_output_dir=cp_output_dir.resolve(),
        )

        # 5. 解析结果，返回给主线程入库。
        parser = ResultParser(str(cp_output_dir))
        summary_result = parser.parse_image_summary()
        if not summary_result.get("success"):
            raise RuntimeError(summary_result.get("message", "解析分析结果失败。"))

        total = summary_result.get("total", {})
        rows = summary_result.get("rows", [])
        image_csv = summary_result.get("image_csv", "")

        return {
            "case_id": self.case_data.get("id"),
            "protein_key": protein_key,
            "protein_name": protein_name,
            "protein_part": protein_part,
            "image_folder": str(raw_folder),
            "output_folder": str(cp_output_dir),
            "total": total,
            "rows": rows,
            "image_csv": image_csv,
        }

    def run_mvimageid(self, protein_key: str, protein_name: str, cp_input_dir: Path, cp_output_dir: Path):
        """
        统一调用 core.mvimageid_runner.MvImageIDRunner。

        目的：
        - 批量分析不再自己拼接 subprocess 命令；
        - 单蛋白分析和批量分析使用同一个 MvImageID 执行器；
        - 统一生成 run_mvimageid.log 和 run_mvimageid_command.txt；
        - 统一错误信息和日志格式。
        """
        pipeline_file = self.config.get_pipeline_by_protein(protein_key).resolve()

        runner = MvImageIDRunner(
            source_project_dir=str(self.config.get_source_project_dir()),
            venv_activate=str(self.config.get_venv_activate()),
            module_name=self.config.get_module_name(),
            plugins_directory=str(self.config.get_plugins_directory()),
            log_file="",
        )

        self.log_signal.emit(f"{protein_name} Pipeline：{pipeline_file}")
        self.log_signal.emit(f"{protein_name} 开始运行 MvImageID ...")

        result = runner.run(
            pipeline_file=str(pipeline_file),
            input_dir=str(cp_input_dir),
            output_dir=str(cp_output_dir),
            log_callback=self.log_signal.emit,
            cancel_callback=lambda: self.cancel_after_current,
            log_file="",
        )

        if not result.success:
            raise RuntimeError(result.error_message or "MvImageID 运行失败。")

        self.log_signal.emit(
            f"{protein_name} MvImageID 运行完成，用时：{result.elapsed_seconds:.2f} 秒。"
        )


# =========================
# 批量分析主窗口
# =========================

class BatchAnalysisDialog(QDialog):
    batch_finished = Signal()

    def __init__(self, database, case_data: dict, parent=None):
        super().__init__(parent)
        self.database = database
        self.case_data = case_data
        self.config = ConfigManager()
        self.config.ensure_default_config()

        self.alias_store = FolderAliasStore()
        self.parent_folder: Optional[Path] = None
        self.available_folders: List[Path] = []
        self.scan_rows: List[dict] = []
        self.worker: Optional[BatchProteinWorker] = None
        self._refreshing_table = False

        self.setWindowTitle("批量蛋白分析")
        self.resize(980, 760)
        self.setMinimumSize(920, 700)
        self.init_ui()
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
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666666;")
        layout.addWidget(hint)

        table_group = QGroupBox("预检查结果")
        table_group.setMinimumHeight(280)
        table_group.setMaximumHeight(330)
        table_layout = QVBoxLayout(table_group)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["蛋白", "匹配文件夹", "G", "R", "DIC", "Merge", "状态"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setMinimumHeight(225)
        self.table.setMaximumHeight(270)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
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

    # ---------- 匹配规则 ----------

    @staticmethod
    def normalize_text(text: str) -> str:
        return FolderAliasStore.normalize_text(text)

    def get_protein_items(self) -> List[dict]:
        items = self.config.get_protein_items()
        fixed_items = []
        for item in items:
            key = str(item.get("key", "") or "").strip()
            if not key:
                continue
            name = str(item.get("name", "") or "").strip() or PROTEIN_DISPLAY_FALLBACK.get(key, key)
            fixed = dict(item)
            fixed["key"] = key
            fixed["name"] = name
            fixed_items.append(fixed)
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
            QMessageBox.information(self, "提示", f"已保存 {changed} 条当前匹配关系到批量匹配规则。")
        else:
            QMessageBox.information(self, "提示", "当前匹配关系已经在规则中，无需重复保存。")
        self.scan_parent_folder()

    # ---------- 扫描与表格 ----------

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择包含蛋白子文件夹的上一级目录", "")
        if not folder:
            return
        self.folder_edit.setText(folder)
        self.parent_folder = Path(folder)
        self.scan_parent_folder()

    def scan_parent_folder(self):
        folder_text = self.folder_edit.text().strip()
        if folder_text:
            self.parent_folder = Path(folder_text)

        self.available_folders = []
        if self.parent_folder and self.parent_folder.exists():
            self.available_folders = sorted(
                [child for child in self.parent_folder.iterdir() if child.is_dir()],
                key=lambda p: p.name.lower(),
            )

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

        old_selection = {
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
            status = self.get_status_by_folder_and_channels(folder, channels, status_note)

            self.scan_rows.append({
                "protein_key": key,
                "protein_name": name,
                "folder": str(folder) if folder else "",
                "channels": channels,
                "status": status,
            })

        self.refresh_table()

    def get_status_by_folder_and_channels(self, folder: Optional[Path], channels: Dict[str, int], status_note: str = "") -> str:
        if status_note:
            return status_note
        if folder is None:
            return "未匹配"
        if channels.get("G", 0) > 0 and channels.get("R", 0) > 0:
            return "可分析"
        return "缺少G或R"

    def scan_channels(self, folder: Optional[Path]) -> Dict[str, int]:
        counts = {"G": 0, "R": 0, "DIC": 0, "Merge": 0}
        if not folder or not folder.exists():
            return counts

        suffixes = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}
        for path in folder.iterdir():
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            name = path.stem.lower()
            if "merge" in name or "mer" in name or "融合" in name:
                counts["Merge"] += 1
            elif "dic" in name or "phase" in name or "相差" in name:
                counts["DIC"] += 1
            elif name.endswith("_g") or "_g_" in name or "fitc" in name or "green" in name or "绿色" in name:
                counts["G"] += 1
            elif name.endswith("_r") or "_r_" in name or "pi" in name or "red" in name or "红色" in name:
                counts["R"] += 1
        return counts

    def refresh_table(self):
        self._refreshing_table = True
        try:
            self.table.setRowCount(len(self.scan_rows))
            folder_names = [folder.name for folder in self.available_folders]

            for row_index, row in enumerate(self.scan_rows):
                channels = row.get("channels", {})

                protein_item = QTableWidgetItem(str(row.get("protein_name", "")))
                protein_item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_index, 0, protein_item)

                combo = QComboBox()
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
                self.table.setCellWidget(row_index, 1, combo)

                values = [
                    self.flag_text(channels.get("G", 0)),
                    self.flag_text(channels.get("R", 0)),
                    self.optional_text(channels.get("DIC", 0)),
                    self.optional_text(channels.get("Merge", 0)),
                    row.get("status", ""),
                ]
                for offset, value in enumerate(values, start=2):
                    item = QTableWidgetItem(str(value))
                    item.setTextAlignment(Qt.AlignCenter)
                    if offset == 6:
                        self.apply_status_color(item, str(value))
                    self.table.setItem(row_index, offset, item)
        finally:
            self._refreshing_table = False

    def on_folder_combo_changed(self, row_index: int):
        if self._refreshing_table:
            return
        if row_index < 0 or row_index >= len(self.scan_rows):
            return

        combo = self.table.cellWidget(row_index, 1)
        folder_name = combo.currentData() if isinstance(combo, QComboBox) else ""
        folder_path: Optional[Path] = None
        if folder_name and self.parent_folder:
            folder_path = self.parent_folder / str(folder_name)

        channels = self.scan_channels(folder_path)
        self.scan_rows[row_index]["folder"] = str(folder_path) if folder_path else ""
        self.scan_rows[row_index]["channels"] = channels
        self.scan_rows[row_index]["status"] = self.get_status_by_folder_and_channels(folder_path, channels)
        self.refresh_table()

    @staticmethod
    def apply_status_color(item: QTableWidgetItem, status: str):
        if status in ["可分析", "已完成"]:
            item.setForeground(Qt.darkGreen)
        elif status in ["分析中"]:
            item.setForeground(Qt.blue)
        elif status in ["失败", "缺少G或R", "匹配多个文件夹", "匹配名冲突", "文件夹重复"]:
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
            QMessageBox.information(self, "提示", "当前病例无效，请先选择病例。")
            return

        duplicates = self.validate_duplicate_folders()
        if duplicates:
            QMessageBox.warning(
                self,
                "文件夹重复",
                "同一个文件夹不能同时分配给多个蛋白，请调整后再开始分析：\n" + "\n".join(duplicates[:20]),
            )
            return

        tasks = self.get_ready_tasks()
        if not tasks:
            QMessageBox.information(self, "提示", "没有可分析的蛋白文件夹。请先选择正确的上级目录，或手动选择匹配文件夹。")
            return

        existing_names = self.get_existing_protein_names(tasks)
        if existing_names:
            reply = QMessageBox.question(
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

        reply = QMessageBox.question(
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

        self.worker = BatchProteinWorker(self.case_data, tasks, self.config, self)
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
        saved_count = 0
        for result in results:
            ok, message = self.save_result_to_database(result)
            if ok:
                saved_count += 1
                self.append_log(message)
            else:
                errors.append({
                    "protein_key": result.get("protein_key", ""),
                    "protein_name": result.get("protein_name", ""),
                    "message": message,
                })
                self.append_log(f"结果入库失败：{message}")

        self.set_running_state(False)
        self.progress_bar.setValue(100)
        self.progress_label.setText(f"批量分析完成：成功 {saved_count} 个，失败/跳过 {len(errors)} 个")
        self.batch_finished.emit()

        if errors:
            error_text = "\n".join([f"{e.get('protein_name', '')}：{e.get('message', '')}" for e in errors])
            QMessageBox.warning(self, "批量分析完成", f"成功 {saved_count} 个，失败/跳过 {len(errors)} 个。\n\n{error_text}")
        else:
            QMessageBox.information(self, "批量分析完成", f"已完成 {saved_count} 个蛋白分析。")

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
            f"标定率 {total.get('expression_rate', 0)}%，"
            f"荧光强度 {total.get('mean_intensity', 0)}。"
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

    def append_log(self, message: str):
        self.log_edit.append(str(message))

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "提示", "批量分析正在运行，暂时不能关闭窗口。")
            event.ignore()
            return
        super().closeEvent(event)
