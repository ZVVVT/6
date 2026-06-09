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
    报告生成器。

    当前版本业务逻辑：
    1. 固定生成 HEL-1 到 HEL-5 五个分子标志物项目。
    2. 报告中的表格和图片均按 HEL-1 到 HEL-5 固定槽位显示。
    3. 某个项目没有分析结果时，显示“未检测”。
    4. 某个项目有结果时，数据和图片只出现在对应项目位置。
    5. 参考值从 config.ini 读取。
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
            r"C:\Windows\Fonts\simsun.ttc",
            r"C:\Windows\Fonts\simsun.ttf",
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\msyh.ttf",
            r"C:\Windows\Fonts\simhei.ttf",
        ]

        for font_path in font_candidates:
            path = Path(font_path)

            if not path.exists():
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

        # LOGO
        logo_file = self._prepare_image_for_report(self.logo_path, "logo_safe.png")
        if logo_file:
            try:
                c.drawImage(
                    str(logo_file),
                    left,
                    y - 7 * mm,
                    width=18 * mm,
                    height=18 * mm,
                    preserveAspectRatio=True,
                    anchor="c",
                    mask="auto",
                )
            except Exception:
                pass

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
        marker_slots = self._build_marker_slots(analysis_rows)

        if marker_slots:
            y -= gap_big
            c.setFont(self.font_name, 13)
            c.setFillColor(blue)
            c.drawString(left, y, "精子质量分子评价")

            y -= gap_line
            c.setFillColor(black)
            c.setFont(self.font_name, 12)

            headers = ["分子标志物", "荧光强度", "参考值范围", "标定率", "参考值范围"]
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
            # 注意：表格只显示已检测项目；但荧光图区域固定显示 HEL-1 到 HEL-5 五个槽位。
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
                    name = slot.get("name", f"HEL-{i + 1}")
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

        # 页脚
        c.setFillColor(colors.grey)
        c.setFont(self.font_name, 8)
        c.drawString(
            left,
            16 * mm,
            "备注：本报告由软件根据 MvImageID / CellProfiler 后台分析结果自动生成。",
        )
        c.drawRightString(
            right,
            16 * mm,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        c.showPage()
        c.save()

    # ------------------------------------------------------------------
    # 数据组装
    # ------------------------------------------------------------------

    def _build_meta(self, case_data: dict) -> dict:
        def bool_value(key):
            value = case_data.get(key, 0)
            return value in [1, "1", True, "true", "True", "是"]

        test_date = str(case_data.get("test_date", "") or "")
        collect_time = str(case_data.get("collect_time", "") or "")
        receive_time = str(case_data.get("receive_time", "") or "")

        if collect_time:
            sample_time = f"{test_date} {collect_time}".strip()
        else:
            sample_time = test_date

        if receive_time:
            test_time = f"{test_date} {receive_time}".strip()
        else:
            test_time = test_date

        return {
            "姓名": case_data.get("patient_name", ""),
            "样本号": case_data.get("sample_no", ""),
            "病历号": case_data.get("case_no", ""),
            "年龄": case_data.get("age", ""),
            "性别": case_data.get("sex", "男"),
            "节欲天数": case_data.get("abstinence_days", ""),
            "取样方式": case_data.get("collect_method", ""),
            "取样时间": sample_time,
            "检测时间": test_time,
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
        构建报告中需要显示的分子标志物结果。

        当前逻辑：
        1. 只显示已经分析、有数据库结果的项目。
        2. 没有分析的 HEL 项目不显示。
        3. 显示顺序仍然按照 config.ini 中 ProteinOrder 的顺序。
        4. 例如只分析 HEL-2，则报告只显示 HEL-2。
        """
        if not analysis_rows:
            return []

        analysis_map = {}

        for row in analysis_rows:
            protein_name = str(row.get("protein_name", "") or "").strip()
            protein_key = self._normalize_protein_key(protein_name)

            if protein_key:
                analysis_map[protein_key] = row

        protein_items = self._get_report_protein_items()

        slots = []

        for item in protein_items:
            key = item.get("key", "")
            name = item.get("name", key)

            row = analysis_map.get(key)

            if not row:
                continue

            intensity_min = self._get_protein_intensity_min(key)
            rate_min = self._get_protein_rate_min(key)

            intensity = self._to_float(row.get("mean_intensity", 0))
            rate = self._to_float(row.get("expression_rate", 0))
            image_path = self._find_representative_image(row.get("output_folder", ""))

            slots.append({
                "key": key,
                "name": name,
                "has_result": True,
                "intensity": intensity,
                "rate": rate,
                "intensity_min": intensity_min,
                "rate_min": rate_min,
                "image_path": image_path,
            })

        return slots[:5]

    def _build_marker_image_slots(self, analysis_rows: list) -> list:
        """
        固定构建 HEL-1 到 HEL-5 五个荧光图槽位。

        业务逻辑：
        1. 图片区域永远显示 HEL-1 到 HEL-5 的标题。
        2. 有结果的项目，在对应槽位显示图片。
        3. 没有结果的项目，只显示标题，下面为空。
        4. 只分析 HEL-2 时，图片只出现在 HEL-2 下方。
        """
        analysis_map = {}

        for row in analysis_rows:
            protein_name = str(row.get("protein_name", "") or "").strip()
            protein_key = self._normalize_protein_key(protein_name)

            if protein_key:
                analysis_map[protein_key] = row

        protein_items = self._get_report_protein_items()

        slots = []

        for item in protein_items:
            key = item.get("key", "")
            name = item.get("name", key)

            row = analysis_map.get(key)

            image_path = None
            if row:
                image_path = self._find_representative_image(row.get("output_folder", ""))

            slots.append({
                "key": key,
                "name": name,
                "image_path": image_path,
            })

        while len(slots) < 5:
            index = len(slots) + 1
            slots.append({
                "key": f"protein{index}",
                "name": f"HEL-{index}",
                "image_path": None,
            })

        return slots[:5]

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
                {"key": "protein1", "name": "HEL-1", "part": "head"},
                {"key": "protein2", "name": "HEL-2", "part": "head"},
                {"key": "protein3", "name": "HEL-3", "part": "tail"},
                {"key": "protein4", "name": "HEL-4", "part": "head"},
                {"key": "protein5", "name": "HEL-5", "part": "head"},
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

    def _find_representative_image(self, output_folder: str):
        if not output_folder:
            return None

        folder = Path(output_folder)

        if not folder.exists():
            return None

        candidates = list(folder.glob("*G_colocalized*Overlay*.png"))

        if not candidates:
            candidates = list(folder.glob("*G_objects*Overlay*.png"))

        if not candidates:
            candidates = list(folder.glob("*R_objects*Overlay*.png"))

        if not candidates:
            candidates = list(folder.glob("*Overlay*.png"))

        if not candidates:
            candidates = list(folder.glob("*.png"))

        candidates.sort()

        return candidates[0] if candidates else None

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