# -*- coding: utf-8 -*-
"""
修复蛋白默认配置和历史名称映射。

用途：
1. 把“恢复默认蛋白配置”的默认名称从 HEL-1~HEL-5 改为标准蛋白编号。
2. 把 ConfigManager 的默认 ProteinNames 改为标准蛋白编号。
3. 增加旧名称 HEL-1~HEL-5 与新名称 Q9BYW3/P10323/... 的兼容映射，避免历史数据对应不上。
4. 同步 config.ini 和 data/analysis.db 中已经保存的旧显示名称。

使用方法：
    cd /d F:\sperm_protein_analyzer
    .venv\Scripts\activate
    python fix_protein_defaults_and_aliases.py
"""

from __future__ import annotations

import configparser
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TS = datetime.now().strftime("%Y%m%d_%H%M%S")

PROTEIN_NAMES = {
    "protein1": "Q9BYW3",
    "protein2": "P10323",
    "protein3": "Q96P56",
    "protein4": "Q8IYV9",
    "protein5": "W5XKT8",
}

OLD_NAMES = {
    "HEL-1": "Q9BYW3",
    "HEL-2": "P10323",
    "HEL-3": "Q96P56",
    "HEL-4": "Q8IYV9",
    "HEL-5": "W5XKT8",
}

NAME_TO_KEY = {
    "HEL-1": "protein1",
    "HEL-2": "protein2",
    "HEL-3": "protein3",
    "HEL-4": "protein4",
    "HEL-5": "protein5",
    "Q9BYW3": "protein1",
    "P10323": "protein2",
    "Q96P56": "protein3",
    "Q8IYV9": "protein4",
    "W5XKT8": "protein5",
}


def backup(path: Path) -> None:
    if path.exists():
        bak = path.with_name(path.name + f".bak_{TS}")
        shutil.copy2(path, bak)
        print(f"已备份：{path} -> {bak}")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    print(f"已修改：{path}")


def fix_config_manager() -> None:
    path = ROOT / "core" / "config_manager.py"
    if not path.exists():
        print(f"跳过：未找到 {path}")
        return

    text = read_text(path)
    original = text
    backup(path)

    # 1) 替换 ensure_default_config 里的默认 ProteinNames
    replacements = {
        '"protein1": "HEL-1"': '"protein1": "Q9BYW3"',
        '"protein2": "HEL-2"': '"protein2": "P10323"',
        '"protein3": "HEL-3"': '"protein3": "Q96P56"',
        '"protein4": "HEL-4"': '"protein4": "Q8IYV9"',
        '"protein5": "HEL-5"': '"protein5": "W5XKT8"',
        "'protein1': 'HEL-1'": "'protein1': 'Q9BYW3'",
        "'protein2': 'HEL-2'": "'protein2': 'P10323'",
        "'protein3': 'HEL-3'": "'protein3': 'Q96P56'",
        "'protein4': 'HEL-4'": "'protein4': 'Q8IYV9'",
        "'protein5': 'HEL-5'": "'protein5': 'W5XKT8'",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    # 2) 替换 normalize_protein_key，增加旧名/新名 alias 兼容
    new_func = '''def normalize_protein_key(self, protein_name_or_key: str) -> str:
        value = str(protein_name_or_key or "").strip()
        if not value:
            return ""

        # 直接传入内部编号 protein1~protein5 时，直接返回
        if self.get("Protein", value, ""):
            return value

        # 兼容历史显示名称和当前标准蛋白编号，避免恢复默认或历史数据导致结果无法对应
        alias_map = {
            "HEL-1": "protein1",
            "HEL-2": "protein2",
            "HEL-3": "protein3",
            "HEL-4": "protein4",
            "HEL-5": "protein5",
            "Q9BYW3": "protein1",
            "P10323": "protein2",
            "Q96P56": "protein3",
            "Q8IYV9": "protein4",
            "W5XKT8": "protein5",
        }
        if value in alias_map:
            return alias_map[value]

        value_upper = value.upper()
        for name, key in alias_map.items():
            if value_upper == name.upper():
                return key

        for key in self.get_protein_keys():
            if value == self.get_protein_display_name(key):
                return key

        return value.lower()
'''

    # 匹配从 def normalize_protein_key 到下一个 def get_protein_display_name 之前的内容
    pattern = r"def normalize_protein_key\(self, protein_name_or_key: str\) -> str:\n.*?\n(?=    def get_protein_display_name)"
    text2, count = re.subn(pattern, new_func, text, flags=re.S)

    if count == 0:
        print("警告：未能自动替换 normalize_protein_key，请手动检查 core/config_manager.py。")
    else:
        text = text2

    if text != original:
        write_text(path, text)
    else:
        print(f"未变化：{path}")


def fix_settings_window() -> None:
    path = ROOT / "app" / "settings_window.py"
    if not path.exists():
        print(f"跳过：未找到 {path}")
        return

    text = read_text(path)
    original = text
    backup(path)

    # 替换 reset_protein_defaults 中的默认显示名称
    for old, new in OLD_NAMES.items():
        text = text.replace(f'"{old}"', f'"{new}"')
        text = text.replace(f"'{old}'", f"'{new}'")

    # 增加一行时不要再默认 HEL-x，避免后续误会；额外行用 proteinX 即可
    text = text.replace('name=f"HEL-{next_index}"', 'name=f"protein{next_index}"')
    text = text.replace("name=f'HEL-{next_index}'", "name=f'protein{next_index}'")

    if text != original:
        write_text(path, text)
    else:
        print(f"未变化：{path}")


def fix_config_ini() -> None:
    path = ROOT / "config.ini"
    if not path.exists():
        print(f"跳过：未找到 {path}")
        return

    backup(path)
    cfg = configparser.ConfigParser()
    cfg.read(path, encoding="utf-8")

    if not cfg.has_section("ProteinNames"):
        cfg.add_section("ProteinNames")

    for key, name in PROTEIN_NAMES.items():
        cfg.set("ProteinNames", key, name)

    if not cfg.has_section("ProteinOrder"):
        cfg.add_section("ProteinOrder")
    cfg.set("ProteinOrder", "keys", "protein1,protein2,protein3,protein4,protein5")

    with path.open("w", encoding="utf-8") as f:
        cfg.write(f)

    print(f"已修改：{path}")


def fix_database_names() -> None:
    db_path = ROOT / "data" / "analysis.db"
    if not db_path.exists():
        print(f"跳过：未找到 {db_path}")
        return

    backup(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}

        if "protein_analysis" not in tables:
            print("数据库中未找到 protein_analysis 表，跳过历史结果名称同步。")
            return

        for old, new in OLD_NAMES.items():
            cur.execute(
                "UPDATE protein_analysis SET protein_name=? WHERE protein_name=?",
                (new, old),
            )

        conn.commit()
        print("已同步数据库 protein_analysis 中的旧蛋白显示名称。")
    finally:
        conn.close()


def main() -> None:
    print("开始修复蛋白默认配置与历史名称兼容……")
    fix_config_manager()
    fix_settings_window()
    fix_config_ini()
    fix_database_names()
    print("完成。请重新运行：python -m compileall -q core\\config_manager.py app\\settings_window.py")


if __name__ == "__main__":
    main()
