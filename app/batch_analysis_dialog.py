# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
import subprocess
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QFileDialog,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QMessageBox,
    QHeaderView,
    QProgressBar,
    QGroupBox,
)

from core.config_manager import ConfigManager
from core.image_importer import ImageImporter
from core.result_parser import ResultParser


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

        # 1. 清空当前蛋白 raw_images，重新导入
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

        # 2. 清空并准备 cp_input
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

        # 3. 清空 cp_output，避免覆盖分析时旧图片混入新结果
        if cp_output_dir.exists():
            shutil.rmtree(cp_output_dir)
        cp_output_dir.mkdir(parents=True, exist_ok=True)

        # 4. 运行 MvImageID / CellProfiler
        self.run_mvimageid(
            protein_key=protein_key,
            protein_name=protein_name,
            cp_input_dir=cp_input_dir.resolve(),
            cp_output_dir=cp_output_dir.resolve(),
        )

        # 5. 解析结果，返回给主线程入库
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
        """直接调用 MvImageID 虚拟环境 python.exe，避免 PowerShell NativeCommandError 遮挡真实错误。"""
        source_project_dir = self.config.get_source_project_dir().resolve()
        venv_activate = self.config.get_venv_activate().resolve()
        module_name = self.config.get_module_name()
        pipeline_file = self.config.get_pipeline_by_protein(protein_key).resolve()
        plugins_directory = self.config.get_plugins_directory().resolve()

        if not source_project_dir.exists():
            raise FileNotFoundError(f"MvImageID 源码目录不存在：{source_project_dir}")
        if not venv_activate.exists():
            raise FileNotFoundError(f"虚拟环境激活脚本不存在：{venv_activate}")
        if not pipeline_file.exists():
            raise FileNotFoundError(f"Pipeline 文件不存在：{pipeline_file}")
        if not plugins_directory.exists():
            raise FileNotFoundError(f"插件目录不存在：{plugins_directory}")

        # Activate.ps1 所在目录通常就是 Scripts，里面有 python.exe。
        scripts_dir = venv_activate.parent
        python_exe = scripts_dir / "python.exe"
        if not python_exe.exists():
            python_exe = scripts_dir / "python"
        if not python_exe.exists():
            raise FileNotFoundError(f"虚拟环境 Python 不存在：{scripts_dir / 'python.exe'}")

        local_log_file = cp_output_dir / "run_mvimageid_batch.log"
        command_file = cp_output_dir / "run_mvimageid_batch_command.txt"

        command = [
            str(python_exe),
            "-u",
            "-m",
            module_name,
            "-c",
            "-r",
            "-p",
            str(pipeline_file),
            "-i",
            str(cp_input_dir),
            "-o",
            str(cp_output_dir),
            "--plugins-directory",
            str(plugins_directory),
        ]

        command_file.write_text("\n".join(command), encoding="utf-8")

        self.log_signal.emit(f"{protein_name} Pipeline：{pipeline_file}")
        self.log_signal.emit(f"{protein_name} Python：{python_exe}")
        self.log_signal.emit(f"{protein_name} 开始运行 MvImageID / CellProfiler ...")
        self.log_signal.emit(f"{protein_name} 运行日志：{local_log_file}")

        start_time = time.time()

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PATH"] = str(scripts_dir) + os.pathsep + env.get("PATH", "")

        last_lines = []
        with local_log_file.open("w", encoding="utf-8", errors="replace") as log_fp:
            log_fp.write("COMMAND:\n")
            log_fp.write(" ".join(command) + "\n\n")
            log_fp.write("CWD:\n")
            log_fp.write(str(source_project_dir) + "\n\n")
            log_fp.write("OUTPUT:\n")
            log_fp.flush()

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(source_project_dir),
                env=env,
            )

            assert process.stdout is not None
            for line in process.stdout:
                line = line.rstrip()
                if not line:
                    continue
                log_fp.write(line + "\n")
                log_fp.flush()
                last_lines.append(line)
                if len(last_lines) > 60:
                    last_lines.pop(0)
                self.log_signal.emit(line)

            return_code = process.wait()

        elapsed = time.time() - start_time

        if return_code != 0:
            tail = "\n".join(last_lines[-40:])
            raise RuntimeError(
                f"MvImageID / CellProfiler 运行失败，退出码：{return_code}，用时：{elapsed:.2f} 秒。\n"
                f"完整日志：{local_log_file}\n"
                f"最后日志：\n{tail}"
            )

        self.log_signal.emit(f"{protein_name} MvImageID / CellProfiler 运行完成，用时：{elapsed:.2f} 秒。")

    @staticmethod
    def ps_quote(path: Path) -> str:
        text = str(path)
        return "'" + text.replace("'", "''") + "'"

    def build_powershell_script(
        self,
        source_project_dir: Path,
        venv_activate: Path,
        module_name: str,
        pipeline_file: Path,
        input_dir: Path,
        output_dir: Path,
        plugins_directory: Path,
        log_file: Path,
    ) -> str:
        return f"""
$ErrorActionPreference = "Stop"
Add-Type -Namespace Win -Name K -PassThru -MemberDefinition '[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern System.IntPtr GetStdHandle(int nStdHandle);[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetConsoleMode(System.IntPtr h, out int m);[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern bool SetConsoleMode(System.IntPtr h, int m);' | Out-Null
$h = [Win.K]::GetStdHandle(-10)
$m = 0
if ($h -ne [IntPtr]::Zero -and [Win.K]::GetConsoleMode($h, [ref]$m)) {{
    $m = ($m -bor 0x80 -bor 0x10) -band (-bnot 0x40)
    [void][Win.K]::SetConsoleMode($h, $m)
}}
Set-Location {self.ps_quote(source_project_dir)}
. {self.ps_quote(venv_activate)}
python -u -m {module_name} -c -r -p {self.ps_quote(pipeline_file)} -i {self.ps_quote(input_dir)} -o {self.ps_quote(output_dir)} --plugins-directory {self.ps_quote(plugins_directory)} 2>&1 | Tee-Object -FilePath {self.ps_quote(log_file)} -Append
exit $LASTEXITCODE
""".strip()


class BatchAnalysisDialog(QDialog):
    batch_finished = Signal()

    def __init__(self, database, case_data: dict, parent=None):
        super().__init__(parent)
        self.database = database
        self.case_data = case_data
        self.config = ConfigManager()
        self.config.ensure_default_config()

        self.parent_folder: Optional[Path] = None
        self.scan_rows: List[dict] = []
        self.worker: Optional[BatchProteinWorker] = None

        self.setWindowTitle("批量蛋白分析")
        self.resize(980, 720)
        self.setMinimumSize(900, 680)
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
        self.folder_edit.setPlaceholderText("请选择包含 5 个蛋白子文件夹的上一级目录")
        self.btn_select_folder = QPushButton("选择文件夹")
        self.btn_scan = QPushButton("重新扫描")
        folder_layout.addWidget(self.folder_edit, 1)
        folder_layout.addWidget(self.btn_select_folder)
        folder_layout.addWidget(self.btn_scan)
        layout.addWidget(folder_group)

        table_group = QGroupBox("预检查结果")
        table_group.setMinimumHeight(250)
        table_group.setMaximumHeight(290)
        table_layout = QVBoxLayout(table_group)
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["蛋白", "匹配文件夹", "G", "R", "DIC", "Merge", "状态"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        # 预检查表固定为适合 5 个蛋白完整显示的高度，避免用户还要上下滚动查看。
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setMinimumHeight(205)
        self.table.setMaximumHeight(235)
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
        # 日志区域压缩高度，保留滚动查看；把更多空间让给上方预检查表。
        self.log_edit.setMinimumHeight(90)
        self.log_edit.setMaximumHeight(140)
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
        self.btn_start.clicked.connect(self.start_batch_analysis)
        self.btn_cancel_next.clicked.connect(self.cancel_after_current)
        self.btn_close.clicked.connect(self.close)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择包含 5 个蛋白子文件夹的上一级目录", "")
        if not folder:
            return
        self.folder_edit.setText(folder)
        self.parent_folder = Path(folder)
        self.scan_parent_folder()

    def build_folder_alias_map(self) -> Dict[str, str]:
        alias_map: Dict[str, str] = {}
        old_hel_aliases = {
            "protein1": ["hel1", "hel-1", "hel_1", "q9byw3"],
            "protein2": ["hel2", "hel-2", "hel_2", "p10323"],
            "protein3": ["hel3", "hel-3", "hel_3", "q96p56"],
            "protein4": ["hel4", "hel-4", "hel_4", "q8iyv9"],
            "protein5": ["hel5", "hel-5", "hel_5", "w5xkt8"],
        }

        for item in self.config.get_protein_items():
            key = str(item.get("key", "") or "").strip()
            name = str(item.get("name", "") or "").strip()
            if not key:
                continue
            candidates = [key, name, name.replace("-", ""), name.replace("_", "")]
            candidates.extend(old_hel_aliases.get(key, []))
            for candidate in candidates:
                norm = self.normalize_text(candidate)
                if norm:
                    alias_map[norm] = key
        return alias_map

    @staticmethod
    def normalize_text(text: str) -> str:
        return str(text or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")

    def scan_parent_folder(self):
        folder_text = self.folder_edit.text().strip()
        if folder_text:
            self.parent_folder = Path(folder_text)

        self.scan_rows = []
        protein_items = self.config.get_protein_items()
        alias_map = self.build_folder_alias_map()
        folder_map: Dict[str, Path] = {}

        if self.parent_folder and self.parent_folder.exists():
            for child in self.parent_folder.iterdir():
                if not child.is_dir():
                    continue
                child_norm = self.normalize_text(child.name)
                matched_key = alias_map.get(child_norm)
                if matched_key:
                    folder_map[matched_key] = child
                else:
                    # 允许 folder 名中包含蛋白名，例如 protein1_Q9BYW3
                    for alias, key in alias_map.items():
                        if alias and alias in child_norm:
                            folder_map[key] = child
                            break

        for protein in protein_items:
            key = str(protein.get("key", ""))
            name = str(protein.get("name", key))
            folder = folder_map.get(key)
            channels = self.scan_channels(folder) if folder else {"G": 0, "R": 0, "DIC": 0, "Merge": 0}
            has_gr = channels.get("G", 0) > 0 and channels.get("R", 0) > 0

            if folder is None:
                status = "未找到"
            elif has_gr:
                status = "可分析"
            else:
                status = "缺少G或R"

            self.scan_rows.append({
                "protein_key": key,
                "protein_name": name,
                "folder": str(folder) if folder else "",
                "channels": channels,
                "status": status,
            })

        self.refresh_table()

    def scan_channels(self, folder: Optional[Path]) -> Dict[str, int]:
        counts = {"G": 0, "R": 0, "DIC": 0, "Merge": 0}
        if not folder or not folder.exists():
            return counts

        suffixes = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}
        for path in folder.iterdir():
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            name = path.stem.lower()
            if "merge" in name:
                counts["Merge"] += 1
            elif "dic" in name or "phase" in name or "相差" in name:
                counts["DIC"] += 1
            elif name.endswith("_g") or "_g_" in name or "fitc" in name or "green" in name:
                counts["G"] += 1
            elif name.endswith("_r") or "_r_" in name or "pi" in name or "red" in name:
                counts["R"] += 1
        return counts

    def refresh_table(self):
        self.table.setRowCount(len(self.scan_rows))
        for row_index, row in enumerate(self.scan_rows):
            channels = row.get("channels", {})
            values = [
                row.get("protein_name", ""),
                Path(row.get("folder", "")).name if row.get("folder") else "-",
                self.flag_text(channels.get("G", 0)),
                self.flag_text(channels.get("R", 0)),
                self.optional_text(channels.get("DIC", 0)),
                self.optional_text(channels.get("Merge", 0)),
                row.get("status", ""),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                if col == 6:
                    status = str(value)
                    if status == "可分析":
                        item.setForeground(Qt.darkGreen)
                    elif status in ["分析中"]:
                        item.setForeground(Qt.blue)
                    elif status in ["失败", "缺少G或R"]:
                        item.setForeground(Qt.red)
                    else:
                        item.setForeground(Qt.gray)
                self.table.setItem(row_index, col, item)

    @staticmethod
    def flag_text(count: int) -> str:
        return f"√ {count}" if count > 0 else "-"

    @staticmethod
    def optional_text(count: int) -> str:
        return f"可选 {count}" if count > 0 else "可选"

    def get_ready_tasks(self) -> List[dict]:
        tasks = []
        for row in self.scan_rows:
            if row.get("status") == "可分析":
                tasks.append({
                    "protein_key": row["protein_key"],
                    "protein_name": row["protein_name"],
                    "folder": row["folder"],
                })
        return tasks

    def start_batch_analysis(self):
        if not self.case_data or not self.case_data.get("id"):
            QMessageBox.information(self, "提示", "当前病例无效，请先选择病例。")
            return

        tasks = self.get_ready_tasks()
        if not tasks:
            QMessageBox.information(self, "提示", "没有可分析的蛋白文件夹。请先选择正确的上级目录。")
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
        existing = []
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
