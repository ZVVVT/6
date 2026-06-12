# -*- coding: utf-8 -*-
"""
一次性修改显示名称：
1. 病例详情 / 报告管理：阳性/共定位数 -> 共定位数
2. 蛋白分析结果表：阳性数 -> 共定位数
3. 蛋白分析导入图片列表：R/PI -> R，G/FITC -> G，DIC/相差 -> DIC
4. 蛋白分析导入图片列表顺序：视野、G、R、DIC、Merge、状态

用法：把本文件放到项目根目录 F:\sperm_protein_analyzer 下运行：
python apply_label_and_order_changes.py
"""
from pathlib import Path
import datetime

ROOT = Path(__file__).resolve().parent
BACKUP_SUFFIX = datetime.datetime.now().strftime(".bak_%Y%m%d_%H%M%S")


def backup(path: Path):
    backup_path = path.with_suffix(path.suffix + BACKUP_SUFFIX)
    backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup_path


def replace_text(path: Path, replacements):
    if not path.exists():
        print(f"[跳过] 文件不存在：{path}")
        return
    text = path.read_text(encoding="utf-8")
    old_text = text
    for old, new in replacements:
        text = text.replace(old, new)
    if text != old_text:
        backup_path = backup(path)
        path.write_text(text, encoding="utf-8")
        print(f"[已修改] {path}，备份：{backup_path.name}")
    else:
        print(f"[无变化] {path}")


def patch_analysis_window():
    path = ROOT / "app" / "analysis_window.py"
    if not path.exists():
        print(f"[跳过] 文件不存在：{path}")
        return
    text = path.read_text(encoding="utf-8")
    old_text = text

    header_old = """self.table.setHorizontalHeaderLabels([\n            \"视野\",\n            \"R/PI\",\n            \"G/FITC\",\n            \"DIC/相差\",\n            \"Merge\",\n            \"状态\",\n        ])"""
    header_new = """self.table.setHorizontalHeaderLabels([\n            \"视野\",\n            \"G\",\n            \"R\",\n            \"DIC\",\n            \"Merge\",\n            \"状态\",\n        ])"""
    text = text.replace(header_old, header_new)

    header_old2 = """self.table.setHorizontalHeaderLabels([\n            \"视野\",\n            \"R\",\n            \"G\",\n            \"DIC\",\n            \"Merge\",\n            \"状态\",\n        ])"""
    text = text.replace(header_old2, header_new)

    values_old = """values = [\n                item.get(\"field_no\", \"\"),\n                self._short_path(item.get(\"R\", \"\")),\n                self._short_path(item.get(\"G\", \"\")),\n                self._short_path(item.get(\"DIC\", \"\")),\n                self._short_path(item.get(\"Merge\", \"\")),\n                item.get(\"status\", \"\"),\n            ]"""
    values_new = """values = [\n                item.get(\"field_no\", \"\"),\n                self._short_path(item.get(\"G\", \"\")),\n                self._short_path(item.get(\"R\", \"\")),\n                self._short_path(item.get(\"DIC\", \"\")),\n                self._short_path(item.get(\"Merge\", \"\")),\n                item.get(\"status\", \"\"),\n            ]"""
    text = text.replace(values_old, values_new)

    text = text.replace("R/PI", "R")
    text = text.replace("G/FITC", "G")
    text = text.replace("DIC/相差", "DIC")
    text = text.replace("阳性/共定位数", "共定位数")
    text = text.replace("阳性数", "共定位数")

    if text != old_text:
        backup_path = backup(path)
        path.write_text(text, encoding="utf-8")
        print(f"[已修改] {path}，备份：{backup_path.name}")
    else:
        print(f"[无变化] {path}")


def main():
    patch_analysis_window()

    common_replacements = [
        ("阳性/共定位数", "共定位数"),
        ("阳性数", "共定位数"),
        ("R/PI", "R"),
        ("G/FITC", "G"),
        ("DIC/相差", "DIC"),
    ]

    for rel in [
        "app/result_viewer.py",
        "app/case_detail_window.py",
        "app/report_window.py",
        "core/report_generator.py",
    ]:
        replace_text(ROOT / rel, common_replacements)

    print("\n完成。建议运行：")
    print("python -m compileall -q app\\analysis_window.py app\\result_viewer.py app\\case_detail_window.py app\\report_window.py core\\report_generator.py")


if __name__ == "__main__":
    main()
