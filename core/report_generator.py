# -*- coding: utf-8 -*-
"""
core/report_generator.py

人精子蛋白质量分析软件 PDF 报告生成器。

当前报告规则：
1. “精子质量分子评价”表格只显示已检测/已有数据库结果的蛋白，不显示未检测占位。
2. “分子标志物荧光图”固定显示系统配置中的前 5 个蛋白槽位，未检测蛋白图片为空。
3. PDF 中蛋白顺序按照 config.ini 的 ProteinOrder / ProteinNames 顺序。
4. 荧光强度、标定率、参考值均来自数据库和 config.ini，不重新读取 Image.csv。
5. 代表性荧光图使用 raw_images/proteinX 中的 Merge 原图；没有 Merge 图则该槽位为空。
6. 不显示页脚备注。
"""

import sys
import hashlib
from pathlib import Path
from datetime import datetime

from PIL import Image as PILImage

from core.config_manager import ConfigManager


# ---------------------------------------------------------
# Python 3.8 兼容补丁
# 某些 reportlab 版本可能调用 hashlib.md5(..., usedforsecurity=False)
# Python 3.8 不支持 usedforsecurity 参数。
# ---------------------------------------------------------
def _patch_hashlib_for_python38():
    if sys.version_info >= (3, 9):
        return

    hash_names = [
        "md5",
        "sha1",
        "sha224",
        "sha256",
        "sha384",
        "sha512",
    ]

    for name in hash_names:
        if not hasattr(hashlib, name):
            continue

        original_func = getattr(hashlib, name)

        def make_wrapper(func):
            def wrapper(*args, **kwargs):
                kwargs.pop("usedforsecurity", None)
                return func(*args, **kwargs)

            return wrapper

        setattr(hashlib, name, make_wrapper(original_func))


_patch_hashlib_for_python38()


from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont


class ReportGenerator:
    """
    PDF 报告生成器。
    """

    def __init__(self, database, report_dir: str = "reports", logo_path: str = ""):
        self.database = database

        self.config = ConfigManager()
        self.config.ensure_default_config()

        if report_dir:
            self.report_dir = Path(report_dir)
        else:
            self.report_dir = self.config.get_report_dir()

        self.report_dir.mkdir(parents=True, exist_ok=True)

        if logo_path:
            self.logo_path = Path(logo_path)
        else:
            self.logo_path = self.config.get_logo_path()

        self.temp_image_dir = self.report_dir / "_temp_images"
        self.temp_image_dir.mkdir(parents=True, exist_ok=True)

        self.font_name = self._register_chinese_font()

    # ------------------------------------------------------------------
    # 字体
    # ------------------------------------------------------------------

    def _register_chinese_font(self):
        font_candidates = [
            # 优先使用软件配置字体
            str(self.config.get_app_font_path() or ""),
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\msyh.ttf",
            r"C:\Windows\Fonts\simsun.ttc",
            r"C:\Windows\Fonts\simsun.ttf",
            r"C:\Windows\Fonts\simhei.ttf",
        ]

        for font_path in font_candidates:
            if not font_path:
                continue

            path = Path(font_path)

            if not path.exists() or not path.is_file():
                continue

            try:
                pdfmetrics.registerFont(TTFont("ChineseFont", str(path)))
                return "ChineseFont"
            except Exception:
                continue

        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light"

    # ------------------------------------------------------------------
    # 对外主函数
    # ------------------------------------------------------------------

    def generate_case_report(self, case_id: int) -> str:
        self.config.load()
        self.config.ensure_default_config()

        case_data = self.database.get_case(case_id)

        if not case_data:
            raise ValueError(f"未找到病例：case_id={case_id}")

        analysis_rows = self.database.get_protein_analysis_by_case(case_id)
        analysis_rows = self._sort_analysis_rows_by_config(analysis_rows)

        case_no = str(case_data.get("case_no", f"case_{case_id}"))
        safe_case_no = self._safe_filename(case_no)

        output_path = self.report_dir / f"{safe_case_no}_人类精液质量检查报告.pdf"

        self._export_pdf(
            pdf_path=str(output_path),
            case_data=case_data,
            analysis_rows=analysis_rows,
        )

        self.database.update_case_report_path(case_id, str(output_path))

        return str(output_path)

    # ------------------------------------------------------------------
    # PDF 绘制
    # ------------------------------------------------------------------

    def _export_pdf(self, pdf_path: str, case_data: dict, analysis_rows: list):
        W, H = A4
        c = canvas.Canvas(pdf_path, pagesize=A4)

        blue = colors.HexColor("#1F4E79")
        black = colors.black

        left = 22 * mm
        right = W - 22 * mm

        gap_big = 14 * mm
        gap_mid = 12 * mm
        gap_line = 10 * mm
        gap_bar = 8 * mm

        y = H - 22 * mm

        # 标题
        c.setFont(self.font_name, 16)
        c.setFillColor(blue)
        c.drawCentredString(W / 2, y, "人类精液质量检查报告")

        # 顶部基础信息
        y -= gap_big
        c.setFillColor(black)
        c.setFont(self.font_name, 12)

        meta = self._build_meta(case_data)

        self._draw_label_value(c, left, y, "姓名：", meta.get("姓名", ""))
        self._draw_label_value(c, W / 2 - 25 * mm, y, "样本号：", meta.get("样本号", ""))
        self._draw_label_value(c, right - 55 * mm, y, "病历号：", meta.get("病历号", ""))

        # 粗横线
        y -= gap_bar
        c.setLineWidth(3)
        c.line(left, y, right, y)
        c.setLineWidth(1)

        # 基本信息
        y -= gap_big
        c.setFont(self.font_name, 13)
        c.setFillColor(blue)
        c.drawString(left, y, "基本信息")

        y -= gap_line
        c.setFillColor(black)
        c.setFont(self.font_name, 11)

        col1 = left
        col2 = W / 2 - 25 * mm
        col3 = right - 55 * mm

        self._draw_label_value(c, col1, y, "年龄：", meta.get("年龄", ""))
        self._draw_label_value(c, col2, y, "性别：", meta.get("性别", ""))
        self._draw_label_value(c, col3, y, "节欲天数：", meta.get("节欲天数", ""))

        y -= gap_line
        self._draw_label_value(c, col1, y, "取样方式：", meta.get("取样方式", ""))
        self._draw_label_value(c, col2, y, "取样时间：", meta.get("取样时间", ""))
        self._draw_label_value(c, col3, y, "检测时间：", meta.get("检测时间", ""))

        # 精液常规检查
        y -= gap_mid
        c.setFont(self.font_name, 13)
        c.setFillColor(blue)
        c.drawString(left, y, "精液常规检查（WHO标准2010版）")

        c.setFillColor(black)
        c.setFont(self.font_name, 11)

        col1 = left
        col2 = W / 2 - 25 * mm
        col3 = right - 53 * mm

        y -= gap_line
        self._draw_label_value(c, col1, y, "外观：", meta.get("外观", ""))
        self._draw_label_value(c, col2, y, "气味：", meta.get("气味", ""))
        self._draw_label_value(c, col3, y, "凝集程度：", meta.get("凝集程度", ""))

        y -= gap_line
        self._draw_label_value(c, col1, y, "粘稠度：", meta.get("粘稠度", ""))
        self._draw_label_value(c, col2, y, "精液量：", meta.get("精液量", ""))
        c.drawString(col2 + 36 * mm, y, "ml")
        self._draw_label_value(c, col3, y, "PH值：", meta.get("PH值", ""))

        y -= gap_line
        self._draw_label_value(c, col1, y, "液化时间：", meta.get("液化时间", ""))
        c.drawString(col1 + 36 * mm, y, "min")
        self._draw_label_value(c, col2, y, "液化效果：", meta.get("液化效果", ""))
        self._draw_label_value(c, col3, y, "颜色：", meta.get("颜色", ""))

        y -= gap_line
        self._draw_label_value(c, col1, y, "精子浓度：", meta.get("精子浓度", ""))
        self._draw_label_value(c, col2, y, "精子总数：", meta.get("精子总数", ""))
        self._draw_label_value(c, col3, y, "前向运动：", meta.get("前向运动", ""))

        y -= gap_line
        self._draw_label_value(c, col1, y, "总活力：", meta.get("总活力", ""))

        # 结论
        y -= gap_big
        c.setFont(self.font_name, 11)
        c.setFillColor(black)
        c.drawString(col1, y, "结论：")

        options = [
            ("正常", bool(meta.get("结论_正常", False))),
            ("少精子症", bool(meta.get("结论_少精子症", False))),
            ("弱精子症", bool(meta.get("结论_弱精子症", False))),
            ("少弱精子症", bool(meta.get("结论_少弱精子症", False))),
            ("坏死精子症", bool(meta.get("结论_坏死精子症", False))),
        ]

        x = col1 + 20 * mm
        for txt, checked in options:
            self._draw_checkbox(c, x, y + 2 * mm, checked)
            c.drawString(x + 6 * mm, y, txt)
            x += 30 * mm

        # 精子质量分子评价
        # 表格只显示已检测项目，不显示未检测占位。
        marker_slots = self._build_marker_slots(analysis_rows)

        if marker_slots:
            y -= gap_big
            c.setFont(self.font_name, 13)
            c.setFillColor(blue)
            c.drawString(left, y, "精子质量分子评价")

            y -= gap_line
            c.setFillColor(black)
            c.setFont(self.font_name, 12)

            headers = ["分子标志物", "荧光强度", "参考值范围", "标定率(%)", "参考值范围"]
            cols_x = [
                left + 10 * mm,
                left + 45 * mm,
                left + 80 * mm,
                left + 120 * mm,
                left + 152 * mm,
            ]

            for hx, htxt in zip(cols_x, headers):
                c.drawCentredString(hx, y, htxt)

            c.setFont(self.font_name, 11)

            for slot in marker_slots:
                y -= gap_line

                intensity_text = self._fmt_with_arrow(
                    slot.get("intensity"),
                    slot.get("intensity_min"),
                    nd=0,
                    unit="",
                )

                rate_text = self._fmt_with_arrow(
                    slot.get("rate"),
                    slot.get("rate_min"),
                    nd=2,
                    unit="%",
                )

                row = [
                    slot.get("name", ""),
                    intensity_text,
                    self._fmt_ref(slot.get("intensity_min")),
                    rate_text,
                    self._fmt_ref(slot.get("rate_min")),
                ]

                for cx, cell in zip(cols_x, row):
                    c.drawCentredString(cx, y, str(cell))

        # 分子标志物荧光图
        # 图片区固定显示 5 个槽位；未检测项目只显示标题，图片为空。
        # 图片来源：raw_images/proteinX 中的 Merge 原图。
        image_slots = self._build_marker_image_slots(analysis_rows)

        if image_slots:
            y -= gap_big
            c.setFont(self.font_name, 13)
            c.setFillColor(blue)
            c.drawString(left, y, "分子标志物荧光图")

            y -= gap_line
            c.setFillColor(black)
            c.setFont(self.font_name, 12)

            box_w = 30 * mm
            gap = 5 * mm
            total_w = box_w * 5 + gap * 4
            start_x = (W - total_w) / 2

            labels_y = y
            img_top_y = y - 6 * mm
            img_h = 34 * mm
            img_w = 34 * mm

            for i, slot in enumerate(image_slots):
                name = slot.get("name", "")
                image_path = slot.get("image_path")

                bx = start_x + i * (box_w + gap)

                c.drawCentredString(bx + box_w / 2, labels_y, name)

                safe_image = self._prepare_image_for_report(
                    image_path,
                    f"marker_image_{i + 1}.png",
                )

                if safe_image:
                    ix = bx + (box_w - img_w) / 2
                    iy = img_top_y - img_h

                    try:
                        c.drawImage(
                            str(safe_image),
                            ix,
                            iy,
                            width=img_w,
                            height=img_h,
                            preserveAspectRatio=True,
                            anchor="c",
                            mask="auto",
                        )
                    except Exception:
                        pass

        # 页脚已取消：不显示备注和生成时间。

        c.showPage()
        c.save()

    # ------------------------------------------------------------------
    # 数据组装
    # ------------------------------------------------------------------

    def _build_meta(self, case_data: dict) -> dict:
        def bool_value(key):
            value = case_data.get(key, 0)
            return value in [1, "1", True, "true", "True", "是"]

        def time_only(value):
            """
            报告中只显示时分秒，不显示日期。

            兼容：
            1. 14:31:04
            2. 2026-06-09 14:31:04
            3. 2026/06/09 14:31:04
            4. 2026-06-09T14:31:04
            """
            text = str(value or "").strip()

            if not text:
                return ""

            text = text.replace("T", " ")

            if " " in text:
                text = text.split()[-1]

            if "." in text:
                text = text.split(".")[0]

            parts = text.split(":")

            if len(parts) >= 3:
                return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(2)}"

            if len(parts) == 2:
                return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:00"

            return text

        collect_time = time_only(case_data.get("collect_time", ""))
        receive_time = time_only(case_data.get("receive_time", ""))

        return {
            "姓名": case_data.get("patient_name", ""),
            "样本号": case_data.get("sample_no", ""),
            "病历号": case_data.get("case_no", ""),
            "年龄": case_data.get("age", ""),
            "性别": case_data.get("sex", "男"),
            "节欲天数": case_data.get("abstinence_days", ""),
            "取样方式": case_data.get("collect_method", ""),
            "取样时间": collect_time,
            "检测时间": receive_time,
            "外观": case_data.get("appearance", ""),
            "气味": case_data.get("smell", ""),
            "凝集程度": case_data.get("agglutination", ""),
            "粘稠度": case_data.get("viscosity", ""),
            "精液量": case_data.get("semen_volume", ""),
            "PH值": case_data.get("ph_value", ""),
            "液化时间": case_data.get("liquefaction_time", ""),
            "液化效果": case_data.get("liquefaction_status", ""),
            "颜色": case_data.get("color", ""),
            "精子浓度": case_data.get("sperm_concentration", ""),
            "精子总数": case_data.get("sperm_total", ""),
            "前向运动": case_data.get("forward_motility", ""),
            "总活力": case_data.get("total_motility", ""),
            "结论_正常": bool_value("conclusion_normal"),
            "结论_少精子症": bool_value("conclusion_oligo"),
            "结论_弱精子症": bool_value("conclusion_astheno"),
            "结论_少弱精子症": bool_value("conclusion_oligoastheno"),
            "结论_坏死精子症": bool_value("conclusion_necro"),
        }

    def _build_marker_slots(self, analysis_rows: list) -> list:
        """
        构建“精子质量分子评价”表格行。

        业务规则：
        1. 只显示已有数据库分析结果的项目。
        2. 不显示未检测占位。
        3. 顺序按照系统设置中的蛋白顺序。
        """
        if not analysis_rows:
            return []

        analysis_map = self._build_analysis_map(analysis_rows)
        protein_items = self._get_report_protein_items()

        slots = []

        for item in protein_items:
            key = item.get("key", "")
            name = item.get("name", key)

            row = analysis_map.get(key) or analysis_map.get(name)

            if not row:
                continue

            intensity_min = self._get_protein_intensity_min(key)
            rate_min = self._get_protein_rate_min(key)

            intensity = self._to_float(row.get("mean_intensity", 0))
            rate = self._to_float(row.get("expression_rate", 0))

            slots.append({
                "key": key,
                "name": name,
                "has_result": True,
                "intensity": intensity,
                "rate": rate,
                "intensity_min": intensity_min,
                "rate_min": rate_min,
            })

        return slots[:5]

    def _build_marker_image_slots(self, analysis_rows: list) -> list:
        """
        构建“分子标志物荧光图”固定五槽位。

        业务规则：
        1. 固定显示系统设置中的前 5 个蛋白名称。
        2. 已检测项目显示 raw_images/proteinX 中的 Merge 图。
        3. 未检测项目只显示标题，图片为空。
        4. 如果已检测但 raw_images 中没有 Merge 图，图片也为空。
        """
        analysis_map = self._build_analysis_map(analysis_rows)
        protein_items = self._get_report_protein_items()

        slots = []

        for item in protein_items:
            key = item.get("key", "")
            name = item.get("name", key)

            row = analysis_map.get(key) or analysis_map.get(name)

            image_path = None

            if row:
                image_folder = row.get("image_folder", "")
                image_path = self._find_merge_image(image_folder)

            slots.append({
                "key": key,
                "name": name,
                "image_path": image_path,
            })

        while len(slots) < 5:
            index = len(slots) + 1
            slots.append({
                "key": f"protein{index}",
                "name": f"protein{index}",
                "image_path": None,
            })

        return slots[:5]

    def _build_analysis_map(self, analysis_rows: list) -> dict:
        analysis_map = {}

        for row in analysis_rows or []:
            protein_name = str(row.get("protein_name", "") or "").strip()

            if not protein_name:
                continue

            protein_key = self._normalize_protein_key(protein_name)

            if protein_key:
                analysis_map[protein_key] = row

            analysis_map[protein_name] = row

        return analysis_map

    def _sort_analysis_rows_by_config(self, analysis_rows: list) -> list:
        rows = list(analysis_rows or [])

        order_map = {}

        for index, item in enumerate(self._get_report_protein_items()):
            key = str(item.get("key", "") or "").strip()
            name = str(item.get("name", "") or "").strip()

            if key:
                order_map[key] = index

            if name:
                order_map[name] = index

        def sort_key(row):
            protein_name = str(row.get("protein_name", "") or "").strip()
            protein_key = self._normalize_protein_key(protein_name)
            index = order_map.get(protein_key, order_map.get(protein_name, 9999))
            return (index, str(row.get("created_at", "") or ""), protein_name)

        return sorted(rows, key=sort_key)

    def _get_report_protein_items(self) -> list:
        items = []

        try:
            config_items = self.config.get_protein_items()
        except Exception:
            config_items = []

        for item in config_items:
            key = str(item.get("key", "") or "").strip()
            name = str(item.get("name", key) or key).strip()
            part = str(item.get("part", "") or "").strip().lower()

            if not key:
                continue

            items.append({
                "key": key,
                "name": name,
                "part": part,
            })

            if len(items) >= 5:
                break

        if not items:
            items = [
                {"key": "protein1", "name": "protein1", "part": "head"},
                {"key": "protein2", "name": "protein2", "part": "head"},
                {"key": "protein3", "name": "protein3", "part": "tail"},
                {"key": "protein4", "name": "protein4", "part": "head"},
                {"key": "protein5", "name": "protein5", "part": "head"},
            ]

        return items[:5]

    def _normalize_protein_key(self, protein_name_or_key: str) -> str:
        value = str(protein_name_or_key or "").strip()

        if not value:
            return ""

        try:
            return self.config.normalize_protein_key(value)
        except Exception:
            pass

        upper_value = value.upper()

        if upper_value.startswith("HEL-"):
            number = upper_value.replace("HEL-", "").strip()

            if number.isdigit():
                return f"protein{number}"

        # 最后一层兼容：根据当前配置名称反查
        try:
            for item in self.config.get_protein_items():
                key = str(item.get("key", "") or "").strip()
                name = str(item.get("name", "") or "").strip()

                if value == key or value == name:
                    return key
        except Exception:
            pass

        return value.lower()

    def _get_protein_intensity_min(self, protein_key: str) -> float:
        try:
            return self.config.get_protein_intensity_min(protein_key)
        except Exception:
            return 26.0

    def _get_protein_rate_min(self, protein_key: str) -> float:
        try:
            return self.config.get_protein_rate_min(protein_key)
        except Exception:
            return 82.88

    def _get_merge_suffixes(self) -> list:
        suffixes = []

        try:
            rule = self.config.get_image_rule()
            merge_suffix = getattr(rule, "merge_suffix", "")
            if merge_suffix:
                suffixes.append(str(merge_suffix))
        except Exception:
            pass

        try:
            merge_suffix = self.config.get("ImageRule", "merge_suffix", "")
            if merge_suffix:
                suffixes.append(str(merge_suffix))
        except Exception:
            pass

        # 兜底兼容
        suffixes.extend(["_Merge", "_merge", "Merge", "merge"])

        result = []
        for item in suffixes:
            item = str(item or "").strip()
            if item and item.lower() not in [x.lower() for x in result]:
                result.append(item)

        return result

    def _find_merge_image(self, image_folder: str):
        """
        查找 raw_images/proteinX 目录中的 Merge 原图。

        注意：
        - 不再从 cp_output 中取 G_colocalized / G_objects / R_objects 叠加图。
        - 没有 Merge 图时直接返回 None，让 PDF 图片槽位保持为空。
        """
        if not image_folder:
            return None

        folder = Path(image_folder)

        if not folder.exists() or not folder.is_dir():
            return None

        image_exts = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
        suffixes = self._get_merge_suffixes()

        candidates = []

        try:
            files = [p for p in folder.iterdir() if p.is_file()]
        except Exception:
            files = []

        for file_path in files:
            if file_path.suffix.lower() not in image_exts:
                continue

            stem = file_path.stem
            stem_lower = stem.lower()

            matched = False

            for suffix in suffixes:
                suffix_lower = str(suffix).lower()

                if stem_lower.endswith(suffix_lower):
                    matched = True
                    break

            # 兜底：如果文件名里明确包含 merge，也可以识别。
            if not matched and "merge" in stem_lower:
                matched = True

            if matched:
                candidates.append(file_path)

        candidates.sort(key=lambda p: p.name.lower())

        return candidates[0] if candidates else None

    # 兼容旧调用名：如果其他位置还调用 _find_representative_image，则也只返回 Merge 图。
    def _find_representative_image(self, image_folder: str):
        return self._find_merge_image(image_folder)

    # ------------------------------------------------------------------
    # 绘图辅助
    # ------------------------------------------------------------------

    def _draw_label_value(self, c: canvas.Canvas, x, y, label, value="", font_size=11):
        c.setFont(self.font_name, font_size)
        c.setFillColor(colors.black)
        c.drawString(x, y, label)

        value_x = x + c.stringWidth(label, self.font_name, font_size) + 1.0 * mm

        if value not in [None, ""]:
            c.drawString(value_x, y, str(value))

    def _draw_checkbox(
        self,
        c: canvas.Canvas,
        x,
        y,
        checked: bool,
        size=3.6 * mm,
        box_line_width: float = 0.6,
        tick_line_width: float = 0.8,
    ):
        c.setLineWidth(box_line_width)
        c.rect(x, y - size * 0.75, size, size, stroke=1, fill=0)

        if checked:
            c.setLineWidth(tick_line_width)
            c.line(
                x + size * 0.18,
                y - size * 0.45,
                x + size * 0.42,
                y - size * 0.70,
            )
            c.line(
                x + size * 0.42,
                y - size * 0.70,
                x + size * 0.82,
                y - size * 0.20,
            )

        c.setLineWidth(1)

    # ------------------------------------------------------------------
    # 图片处理
    # ------------------------------------------------------------------

    def _prepare_image_for_report(self, image_path, output_name: str):
        if not image_path:
            return None

        image_path = Path(image_path)

        if not image_path.exists():
            return None

        try:
            with PILImage.open(str(image_path)) as img:
                img.load()

                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                elif img.mode == "L":
                    img = img.convert("RGB")
                else:
                    img = img.copy()

                output_path = self.temp_image_dir / output_name
                img.save(str(output_path), format="PNG")

                return output_path
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 格式化
    # ------------------------------------------------------------------

    def _fmt_with_arrow(self, value, ref_min, nd=0, unit=""):
        if value is None:
            return ""

        try:
            value_float = float(value)
        except Exception:
            return ""

        arrow = ""

        try:
            if ref_min is not None and ref_min != "":
                arrow = "↑" if value_float >= float(ref_min) else "↓"
        except Exception:
            arrow = ""

        if nd == 0:
            value_text = str(int(round(value_float)))
        else:
            value_text = f"{value_float:.{nd}f}"

        return f"{value_text}{unit}{arrow}"

    def _fmt_ref(self, value):
        if value in [None, ""]:
            return ""

        try:
            return f"≥{float(value):.2f}".rstrip("0").rstrip(".")
        except Exception:
            return f"≥{value}"

    def _to_float(self, value):
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _safe_filename(name: str):
        invalid_chars = '\\/:*?"<>|'
        safe = str(name)

        for ch in invalid_chars:
            safe = safe.replace(ch, "_")

        return safe
