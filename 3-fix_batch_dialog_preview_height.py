# -*- coding: utf-8 -*-
"""
调整批量蛋白分析窗口布局：
1. 预检查结果表格区域加高，默认完整显示 5 个蛋白，不需要滚动。
2. 运行日志区域变小，日志仍可在文本框内滚动查看。

用法：
    cd /d F:\sperm_protein_analyzer
    .venv\Scripts\activate
    python fix_batch_dialog_preview_height.py
    python -m compileall -q app\batch_analysis_dialog.py
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "app" / "batch_analysis_dialog.py"


def backup_file(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak_{ts}")
    shutil.copy2(path, backup)
    return backup


def replace_once(text: str, old: str, new: str) -> tuple[str, bool]:
    if old in text:
        return text.replace(old, new, 1), True
    return text, False


def main():
    if not TARGET.exists():
        raise FileNotFoundError(f"找不到文件：{TARGET}")

    text = TARGET.read_text(encoding="utf-8")
    original = text
    backup = backup_file(TARGET)

    # 1. 窗口稍微加高一点，给预检查表留足空间。
    text = text.replace("self.resize(980, 680)", "self.resize(980, 720)")
    text = text.replace("self.setMinimumSize(900, 620)", "self.setMinimumSize(900, 680)")

    # 2. 表格固定到适合 5 个蛋白完整显示的高度。
    marker = "        self.table.verticalHeader().setVisible(False)\n"
    insert = (
        "        self.table.verticalHeader().setVisible(False)\n"
        "        # 预检查表固定为适合 5 个蛋白完整显示的高度，避免用户还要上下滚动查看。\n"
        "        self.table.verticalHeader().setDefaultSectionSize(28)\n"
        "        self.table.setMinimumHeight(205)\n"
        "        self.table.setMaximumHeight(235)\n"
        "        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)\n"
    )
    if "预检查表固定为适合 5 个蛋白完整显示的高度" not in text:
        if marker not in text:
            raise RuntimeError("未找到 table.verticalHeader().setVisible(False) 位置，无法插入表格高度设置。")
        text = text.replace(marker, insert, 1)

    # 3. 预检查结果区域权重稍高。
    text = text.replace("layout.addWidget(table_group, 1)", "layout.addWidget(table_group, 2)")

    # 4. 日志区域缩小，文本框自身仍然可滚动。
    old_log = "        self.log_edit.setMinimumHeight(150)\n"
    new_log = (
        "        # 日志区域压缩高度，保留滚动查看；把更多空间让给上方预检查表。\n"
        "        self.log_edit.setMinimumHeight(90)\n"
        "        self.log_edit.setMaximumHeight(140)\n"
    )
    if old_log in text:
        text = text.replace(old_log, new_log, 1)
    elif "日志区域压缩高度" not in text:
        raise RuntimeError("未找到 self.log_edit.setMinimumHeight(150) 位置，无法调整日志高度。")

    # 5. 可选：让预检查表分组也有明确高度，避免 QGroupBox 把表格压扁。
    marker2 = "        table_group = QGroupBox(\"预检查结果\")\n        table_layout = QVBoxLayout(table_group)\n"
    insert2 = (
        "        table_group = QGroupBox(\"预检查结果\")\n"
        "        table_group.setMinimumHeight(250)\n"
        "        table_group.setMaximumHeight(290)\n"
        "        table_layout = QVBoxLayout(table_group)\n"
    )
    if "table_group.setMinimumHeight(250)" not in text:
        if marker2 not in text:
            raise RuntimeError("未找到 table_group 创建位置，无法设置预检查区域高度。")
        text = text.replace(marker2, insert2, 1)

    if text == original:
        print("文件内容没有变化，可能已经修复过。")
    else:
        TARGET.write_text(text, encoding="utf-8")
        print(f"已备份：{backup}")
        print(f"已修复：{TARGET}")
        print("调整完成：预检查结果区域加高，运行日志区域变小。")


if __name__ == "__main__":
    main()
