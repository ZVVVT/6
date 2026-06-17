# -*- coding: utf-8 -*-
"""
一键把蛋白显示名称从 HEL-1~HEL-5 改为指定蛋白编号。
运行位置：F:\\sperm_protein_analyzer 项目根目录

会修改：
1. config.ini 的 [ProteinNames]
2. core/config_manager.py 默认配置里的 HEL 名称
3. data/analysis.db 里已经保存过的 protein_analysis.protein_name

不会修改：
- protein1/protein2/protein3/protein4/protein5 内部编号
- head/tail 表达部位
- 已分析结果文件夹 protein1~protein5
"""

from pathlib import Path
import configparser
import sqlite3
import re

ROOT = Path(__file__).resolve().parent

NAME_MAP = {
    "protein1": "Q9BYW3",
    "protein2": "P10323",
    "protein3": "Q96P56",
    "protein4": "Q8IYV9",
    "protein5": "W5XKT8",
}

OLD_TO_NEW = {
    "HEL-1": "Q9BYW3",
    "HEL-2": "P10323",
    "HEL-3": "Q96P56",
    "HEL-4": "Q8IYV9",
    "HEL-5": "W5XKT8",
}


def backup_file(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + ".bak_before_protein_rename")
    if path.exists() and not bak.exists():
        bak.write_bytes(path.read_bytes())
    return bak


def update_config_ini():
    path = ROOT / "config.ini"
    if not path.exists():
        print(f"[跳过] 未找到 {path}")
        return

    backup_file(path)

    config = configparser.ConfigParser()
    config.optionxform = str
    config.read(path, encoding="utf-8")

    if not config.has_section("ProteinNames"):
        config.add_section("ProteinNames")

    for key, name in NAME_MAP.items():
        config.set("ProteinNames", key, name)

    with path.open("w", encoding="utf-8") as f:
        config.write(f)

    print("[完成] 已更新 config.ini -> [ProteinNames]")


def update_config_manager_defaults():
    path = ROOT / "core" / "config_manager.py"
    if not path.exists():
        print(f"[跳过] 未找到 {path}")
        return

    backup_file(path)
    text = path.read_text(encoding="utf-8")

    # 只替换字符串字面量中的显示名称，不改 protein1/protein2 内部编号
    for old, new in OLD_TO_NEW.items():
        text = text.replace(f'"{old}"', f'"{new}"')
        text = text.replace(f"'{old}'", f"'{new}'")

    path.write_text(text, encoding="utf-8")
    print("[完成] 已更新 core/config_manager.py 默认蛋白显示名称")


def get_database_path_from_config() -> Path:
    config_path = ROOT / "config.ini"
    if not config_path.exists():
        return ROOT / "data" / "analysis.db"

    config = configparser.ConfigParser()
    config.read(config_path, encoding="utf-8")
    db_text = config.get("Workspace", "database", fallback=r"data\analysis.db")
    return ROOT / Path(db_text)


def migrate_database_names():
    db_path = get_database_path_from_config()
    if not db_path.exists():
        print(f"[跳过] 未找到数据库 {db_path}")
        return

    backup_file(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.cursor()

        # protein_analysis 里保存的是显示名称，因此需要同步迁移旧结果
        for old, new in OLD_TO_NEW.items():
            cur.execute(
                "UPDATE protein_analysis SET protein_name = ? WHERE protein_name = ?",
                (new, old),
            )

        conn.commit()

    print("[完成] 已迁移数据库中历史分析结果 protein_name")


def main():
    update_config_ini()
    update_config_manager_defaults()
    migrate_database_names()

    print("\n全部完成。新的蛋白显示名称：")
    for key, name in NAME_MAP.items():
        print(f"  {key} -> {name}")
    print("\n请重新启动软件后检查：蛋白分析页、病例详情、报告管理、PDF报告。")


if __name__ == "__main__":
    main()
