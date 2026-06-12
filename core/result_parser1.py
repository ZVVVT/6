from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd


class ResultParser:
    IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
    TABLE_EXTS = {".csv", ".xlsx", ".xls"}
    LOG_EXTS = {".log", ".txt"}
    PDF_EXTS = {".pdf"}

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)

    def scan_files(self) -> list:
        if not self.output_dir.exists():
            return []

        files = []

        for file_path in self.output_dir.rglob("*"):
            if not file_path.is_file():
                continue

            suffix = file_path.suffix.lower()

            if suffix in self.IMAGE_EXTS:
                file_type = "图片"
            elif suffix in self.TABLE_EXTS:
                file_type = "表格"
            elif suffix in self.LOG_EXTS:
                file_type = "日志"
            elif suffix in self.PDF_EXTS:
                file_type = "PDF"
            elif suffix == ".ps1":
                file_type = "脚本"
            else:
                file_type = "其他"

            stat = file_path.stat()

            files.append({
                "name": file_path.name,
                "type": file_type,
                "suffix": suffix,
                "path": str(file_path),
                "size_kb": round(stat.st_size / 1024, 2),
                "modified_time": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })

        files.sort(key=lambda x: (x["type"], x["name"]))
        return files

    def get_file_summary(self) -> dict:
        files = self.scan_files()

        summary = {
            "total": len(files),
            "image": 0,
            "table": 0,
            "log": 0,
            "pdf": 0,
            "script": 0,
            "other": 0,
        }

        for item in files:
            file_type = item["type"]

            if file_type == "图片":
                summary["image"] += 1
            elif file_type == "表格":
                summary["table"] += 1
            elif file_type == "日志":
                summary["log"] += 1
            elif file_type == "PDF":
                summary["pdf"] += 1
            elif file_type == "脚本":
                summary["script"] += 1
            else:
                summary["other"] += 1

        return summary

    def find_image_csv(self) -> Optional[Path]:
        if not self.output_dir.exists():
            return None

        csv_files = list(self.output_dir.rglob("*.csv"))

        # 优先匹配 Image.csv
        for file_path in csv_files:
            if file_path.name.lower() == "image.csv":
                return file_path

        # 兼容 MyExpt_Image.csv 等名称
        for file_path in csv_files:
            if file_path.name.lower().endswith("_image.csv"):
                return file_path

        # 兜底：包含 image 的 csv
        for file_path in csv_files:
            if "image" in file_path.name.lower():
                return file_path

        return None

    def find_object_csv(self) -> Optional[Path]:
        if not self.output_dir.exists():
            return None

        csv_files = list(self.output_dir.rglob("*.csv"))

        for file_path in csv_files:
            name = file_path.name.lower()
            if "g_colocalized" in name:
                return file_path

        return None

    def parse_image_summary(self) -> dict:
        image_csv = self.find_image_csv()

        empty_result = {
            "success": False,
            "message": "未找到 Image.csv。",
            "image_csv": "",
            "rows": [],
            "total": {},
        }

        if image_csv is None:
            return empty_result

        try:
            df = pd.read_csv(image_csv)
        except Exception as e:
            empty_result["message"] = f"读取 Image.csv 失败：{e}"
            return empty_result

        if df.empty:
            empty_result["message"] = "Image.csv 为空。"
            empty_result["image_csv"] = str(image_csv)
            return empty_result

        rows = []

        for _, row in df.iterrows():
            image_number = self._get_value(row, "ImageNumber", default="")

            r_count = self._get_float(row, "Count_R_objects")
            g_colocalized_count = self._get_float(row, "Count_G_colocalized")
            g_objects_count = self._get_float(row, "Count_G_objects")
            r_objects_run = self._get_float(row, "Count_R_objects_Run")
            g_objects_run = self._get_float(row, "Count_G_objects_Run")

            total_intensity = self._get_float(row, "Intensity_TotalIntensity_G_Gray_G_colocalized")
            cp_rate = self._get_float(row, "Math_ColocalizationRate")
            cp_intensity = self._get_float(row, "Math_FluorescenceIntensity")

            if cp_rate is not None:
                expression_rate = cp_rate * 100 if cp_rate <= 1 else cp_rate
            elif r_count:
                expression_rate = g_colocalized_count / r_count * 100
            else:
                expression_rate = 0

            if cp_intensity is not None:
                mean_intensity = cp_intensity
            elif r_count:
                mean_intensity = total_intensity / r_count
            else:
                mean_intensity = 0

            rows.append({
                "image_number": int(image_number) if str(image_number).isdigit() else image_number,
                "sperm_count": int(r_count or 0),
                "positive_count": int(g_colocalized_count or 0),
                "expression_rate": round(expression_rate or 0, 2),
                "mean_intensity": round(mean_intensity or 0, 2),
                "total_green_intensity": round(total_intensity or 0, 2),
                "g_objects_count": int(g_objects_count or 0),
                "r_objects_run": int(r_objects_run or 0),
                "g_objects_run": int(g_objects_run or 0),
            })

        total_sperm_count = sum(item["sperm_count"] for item in rows)
        total_positive_count = sum(item["positive_count"] for item in rows)
        total_green_intensity = sum(item["total_green_intensity"] for item in rows)

        if total_sperm_count > 0:
            total_expression_rate = total_positive_count / total_sperm_count * 100
            total_mean_intensity = total_green_intensity / total_sperm_count
        else:
            total_expression_rate = 0
            total_mean_intensity = 0

        total = {
            "field_count": len(rows),
            "sperm_count": total_sperm_count,
            "positive_count": total_positive_count,
            "expression_rate": round(total_expression_rate, 2),
            "mean_intensity": round(total_mean_intensity, 2),
            "total_green_intensity": round(total_green_intensity, 2),
        }

        return {
            "success": True,
            "message": "解析成功。",
            "image_csv": str(image_csv),
            "rows": rows,
            "total": total,
        }

    def parse_object_summary(self) -> dict:
        object_csv = self.find_object_csv()

        result = {
            "success": False,
            "message": "未找到 G_colocalized.csv。",
            "object_csv": "",
            "object_count": 0,
            "columns": [],
        }

        if object_csv is None:
            return result

        try:
            df = pd.read_csv(object_csv)
        except Exception as e:
            result["message"] = f"读取对象 CSV 失败：{e}"
            return result

        result.update({
            "success": True,
            "message": "解析成功。",
            "object_csv": str(object_csv),
            "object_count": len(df),
            "columns": list(df.columns),
        })

        return result

    @staticmethod
    def _get_value(row, column_name: str, default=None):
        if column_name in row.index:
            return row[column_name]
        return default

    @staticmethod
    def _get_float(row, column_name: str):
        if column_name not in row.index:
            return None

        value = row[column_name]

        try:
            if pd.isna(value):
                return None
            return float(value)
        except Exception:
            return None