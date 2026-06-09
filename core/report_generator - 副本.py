import sys
import hashlib
from pathlib import Path
from datetime import datetime


# ---------------------------------------------------------
# Python 3.8 兼容补丁
# 某些新版 reportlab 会调用 hashlib.md5(..., usedforsecurity=False)
# 但 Python 3.8 不支持 usedforsecurity 参数，会导致：
# 'usedforsecurity' is an invalid keyword argument for openssl_md5()
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


from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont


class ReportGenerator:
    def __init__(self, database, report_dir: str = "reports", logo_path: str = ""):
        self.database = database
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)

        self.logo_path = Path(logo_path) if logo_path else None

        self.font_name = self._register_chinese_font()
        self.styles = self._build_styles()

    def _register_chinese_font(self):
        """
        优先使用 Windows 常见中文字体。
        如果本机没有 TTF 字体，则使用 ReportLab 内置 STSong-Light。
        """
        font_candidates = [
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\simsun.ttc",
            r"C:\Windows\Fonts\simsun.ttf",
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\msyh.ttf",
        ]

        for font_path in font_candidates:
            path = Path(font_path)
            if path.exists():
                try:
                    pdfmetrics.registerFont(TTFont("ChineseFont", str(path)))
                    return "ChineseFont"
                except Exception:
                    pass

        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light"

    def _build_styles(self):
        styles = getSampleStyleSheet()

        styles.add(ParagraphStyle(
            name="ChineseTitle",
            parent=styles["Title"],
            fontName=self.font_name,
            fontSize=20,
            alignment=TA_CENTER,
            leading=26,
            spaceAfter=12,
        ))

        styles.add(ParagraphStyle(
            name="ChineseHeading",
            parent=styles["Heading2"],
            fontName=self.font_name,
            fontSize=14,
            leading=20,
            spaceBefore=10,
            spaceAfter=8,
        ))

        styles.add(ParagraphStyle(
            name="ChineseNormal",
            parent=styles["Normal"],
            fontName=self.font_name,
            fontSize=10,
            leading=16,
            alignment=TA_LEFT,
        ))

        styles.add(ParagraphStyle(
            name="ChineseSmall",
            parent=styles["Normal"],
            fontName=self.font_name,
            fontSize=8,
            leading=12,
            alignment=TA_LEFT,
            textColor=colors.grey,
        ))

        return styles

    def generate_case_report(self, case_id: int) -> str:
        case_data = self.database.get_case(case_id)
        if not case_data:
            raise ValueError(f"未找到病例：case_id={case_id}")

        analysis_rows = self.database.get_protein_analysis_by_case(case_id)

        case_no = str(case_data.get("case_no", f"case_{case_id}"))
        safe_case_no = self._safe_filename(case_no)

        output_path = self.report_dir / f"{safe_case_no}_精子蛋白分析报告.pdf"

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            rightMargin=18 * mm,
            leftMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
            title="人精子蛋白质量分析报告",
        )

        story = []

        story.extend(self._build_title())
        story.extend(self._build_case_info(case_data))
        story.extend(self._build_analysis_summary(analysis_rows))
        story.extend(self._build_analysis_detail(analysis_rows))
        story.extend(self._build_images_section(analysis_rows))
        story.extend(self._build_note())

        doc.build(story)

        self.database.update_case_report_path(case_id, str(output_path))

        return str(output_path)

    def _build_title(self):
        story = []

        if self.logo_path and self.logo_path.exists():
            try:
                logo = Image(str(self.logo_path), width=28 * mm, height=28 * mm)
                logo.hAlign = "CENTER"
                story.append(logo)
                story.append(Spacer(1, 4 * mm))
            except Exception:
                pass

        story.append(Paragraph("人精子蛋白质量分析报告", self.styles["ChineseTitle"]))
        story.append(Paragraph(
            f"报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            self.styles["ChineseSmall"],
        ))
        story.append(Spacer(1, 8 * mm))

        return story

    def _build_case_info(self, case_data):
        story = []
        story.append(Paragraph("一、病例信息", self.styles["ChineseHeading"]))

        data = [
            ["病例编号", case_data.get("case_no", ""), "姓名", case_data.get("patient_name", "")],
            ["年龄", case_data.get("age", ""), "样本编号", case_data.get("sample_no", "")],
            ["检测日期", case_data.get("test_date", ""), "备注", case_data.get("remark", "")],
        ]

        table = Table(data, colWidths=[28 * mm, 52 * mm, 28 * mm, 70 * mm])
        table.setStyle(self._basic_table_style())
        story.append(table)
        story.append(Spacer(1, 8 * mm))

        return story

    def _build_analysis_summary(self, analysis_rows):
        story = []
        story.append(Paragraph("二、蛋白分析汇总", self.styles["ChineseHeading"]))

        if not analysis_rows:
            story.append(Paragraph("当前病例暂无蛋白分析结果。", self.styles["ChineseNormal"]))
            story.append(Spacer(1, 8 * mm))
            return story

        data = [[
            "蛋白名称",
            "表达部位",
            "视野数",
            "精子总数",
            "阳性/共定位数",
            "标定率(%)",
            "荧光强度",
            "状态",
        ]]

        for row in analysis_rows:
            data.append([
                row.get("protein_name", ""),
                row.get("protein_part", ""),
                row.get("total_fields", 0),
                row.get("total_sperm_count", 0),
                row.get("positive_count", 0),
                self._fmt(row.get("expression_rate", 0)),
                self._fmt(row.get("mean_intensity", 0)),
                row.get("status", ""),
            ])

        table = Table(
            data,
            colWidths=[
                24 * mm,
                24 * mm,
                18 * mm,
                24 * mm,
                28 * mm,
                24 * mm,
                24 * mm,
                18 * mm,
            ],
            repeatRows=1,
        )
        table.setStyle(self._header_table_style())
        story.append(table)
        story.append(Spacer(1, 8 * mm))

        return story

    def _build_analysis_detail(self, analysis_rows):
        story = []
        story.append(Paragraph("三、视野明细", self.styles["ChineseHeading"]))

        if not analysis_rows:
            story.append(Paragraph("暂无视野明细。", self.styles["ChineseNormal"]))
            story.append(Spacer(1, 8 * mm))
            return story

        for analysis in analysis_rows:
            analysis_id = analysis.get("id")
            field_rows = self.database.get_field_results(analysis_id)

            story.append(Paragraph(
                f"蛋白：{analysis.get('protein_name', '')}",
                self.styles["ChineseNormal"],
            ))

            if not field_rows:
                story.append(Paragraph("暂无视野明细。", self.styles["ChineseSmall"]))
                story.append(Spacer(1, 4 * mm))
                continue

            data = [[
                "视野",
                "精子总数",
                "阳性/共定位数",
                "标定率(%)",
                "荧光强度",
            ]]

            for row in field_rows:
                data.append([
                    row.get("field_no", ""),
                    row.get("sperm_count", 0),
                    row.get("positive_count", 0),
                    self._fmt(row.get("expression_rate", 0)),
                    self._fmt(row.get("mean_intensity", 0)),
                ])

            table = Table(
                data,
                colWidths=[34 * mm, 34 * mm, 38 * mm, 34 * mm, 34 * mm],
            )
            table.setStyle(self._header_table_style())
            story.append(table)
            story.append(Spacer(1, 6 * mm))

        return story

    def _build_images_section(self, analysis_rows):
        story = []
        story.append(Paragraph("四、代表性图像", self.styles["ChineseHeading"]))

        image_paths = []

        for analysis in analysis_rows:
            output_folder = analysis.get("output_folder", "")
            if not output_folder:
                continue

            folder = Path(output_folder)
            if not folder.exists():
                continue

            # 优先选共定位图，其次选 overlay 图，其次任意 png
            candidates = list(folder.glob("*G_colocalized*Overlay*.png"))
            if not candidates:
                candidates = list(folder.glob("*Overlay*.png"))
            if not candidates:
                candidates = list(folder.glob("*.png"))

            if candidates:
                image_paths.append((analysis.get("protein_name", ""), candidates[0]))

        if not image_paths:
            story.append(Paragraph("未找到可插入报告的结果图像。", self.styles["ChineseNormal"]))
            story.append(Spacer(1, 8 * mm))
            return story

        for protein_name, image_path in image_paths[:5]:
            story.append(Paragraph(
                f"蛋白：{protein_name}　图像：{image_path.name}",
                self.styles["ChineseSmall"],
            ))

            try:
                img = Image(str(image_path), width=145 * mm, height=145 * mm)
                img.hAlign = "CENTER"
                story.append(img)
                story.append(Spacer(1, 6 * mm))
            except Exception:
                story.append(Paragraph(f"图像插入失败：{image_path}", self.styles["ChineseSmall"]))

        return story

    def _build_note(self):
        story = []
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph("五、备注", self.styles["ChineseHeading"]))
        story.append(Paragraph(
            "本报告由软件根据 CellProfiler/MvImageID 后台分析结果自动生成。"
            "当前版本结果基于自动识别结果，尚未包含人工校正流程。",
            self.styles["ChineseNormal"],
        ))
        return story

    def _basic_table_style(self):
        return TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), self.font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
            ("BACKGROUND", (2, 0), (2, -1), colors.whitesmoke),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ])

    def _header_table_style(self):
        return TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), self.font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ])

    @staticmethod
    def _fmt(value):
        try:
            return f"{float(value):.2f}"
        except Exception:
            return str(value)

    @staticmethod
    def _safe_filename(name: str):
        invalid_chars = '\\/:*?"<>|'
        safe = str(name)

        for ch in invalid_chars:
            safe = safe.replace(ch, "_")

        return safe