from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd


class ResultParser:
    IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
    TABLE_EXTS = {".csv", ".xlsx", ".xls"}
    LOG_EXTS = {".log", ".txt"}
    PDF_EXTS = {".pdf"}

    def __init__(self, output_dir: str, protein_part: str = ""):
        self.output_dir = Path(output_dir)
        self.protein_part = self._normalize_part(protein_part)

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

    def find_object_csv(self, protein_part: str = "") -> Optional[Path]:
        """定位本次荧光强度计算所需的对象级 CSV。

        新版 Pipeline 的口径：
        - 头部：G_colocalized.csv，使用 Math_MeanIntensity255 汇总。
        - 尾部：G_objects.csv，使用 Math_IntegratedIntensity255 / AreaShape_Area 汇总。

        为了兼容旧结果和用户调试过程中的文件名，保留 colocalized 兜底匹配。
        """
        if not self.output_dir.exists():
            return None

        part = self._normalize_part(protein_part or self.protein_part)
        csv_files = list(self.output_dir.rglob("*.csv"))

        if part == "tail":
            priority_groups = [
                ["g_objects.csv"],
                ["g_objects"],
                ["r_colocalized.csv"],
                ["r_colocalized"],
                ["g_colocalized.csv"],
                ["g_colocalized"],
                ["colocalized"],
            ]
        elif part == "head":
            priority_groups = [
                ["g_colocalized.csv"],
                ["g_colocalized"],
                ["r_colocalized.csv"],
                ["r_colocalized"],
                ["colocalized"],
                ["g_objects.csv"],
                ["g_objects"],
            ]
        else:
            priority_groups = [
                ["g_colocalized.csv"],
                ["r_colocalized.csv"],
                ["g_objects.csv"],
                ["g_colocalized"],
                ["r_colocalized"],
                ["g_objects"],
                ["colocalized"],
            ]

        for patterns in priority_groups:
            for file_path in csv_files:
                name = file_path.name.lower()
                for pattern in patterns:
                    if pattern.endswith(".csv"):
                        if name == pattern:
                            return file_path
                    elif pattern in name:
                        return file_path

        return None

    def parse_image_summary(self, protein_part: str = "") -> dict:
        image_csv = self.find_image_csv()
        requested_part = self._normalize_part(protein_part or self.protein_part)

        empty_result = {
            "success": False,
            "message": "未找到 Image.csv。",
            "image_csv": "",
            "object_csv": "",
            "calculation_mode": requested_part,
            "warnings": [],
            "rows": [],
            "total": {},
        }

        if image_csv is None:
            return empty_result

        try:
            image_df = pd.read_csv(image_csv)
        except Exception as e:
            empty_result["message"] = f"读取 Image.csv 失败：{e}"
            empty_result["image_csv"] = str(image_csv)
            return empty_result

        if image_df.empty:
            empty_result["message"] = "Image.csv 为空。"
            empty_result["image_csv"] = str(image_csv)
            return empty_result

        object_csv = self.find_object_csv(requested_part)
        object_df = None
        if object_csv is not None:
            try:
                object_df = pd.read_csv(object_csv)
            except Exception as e:
                empty_result["message"] = f"读取对象 CSV 失败：{e}"
                empty_result["image_csv"] = str(image_csv)
                empty_result["object_csv"] = str(object_csv)
                return empty_result

        part = self._infer_part(requested_part, image_df, object_df, object_csv)
        warnings = []

        ok, message = self._validate_required_columns(image_df, object_df, object_csv, part)
        if not ok:
            empty_result["message"] = message
            empty_result["image_csv"] = str(image_csv)
            empty_result["object_csv"] = str(object_csv or "")
            empty_result["calculation_mode"] = part
            return empty_result

        if "Count_R_colocalized" not in image_df.columns and "Count_G_colocalized" in image_df.columns:
            warnings.append("未找到 Count_R_colocalized，已兼容使用 Count_G_colocalized 作为共定位数量。")

        object_stats = self._build_object_stats(object_df)
        rows = []

        for _, row in image_df.iterrows():
            image_number = self._get_value(row, "ImageNumber", default="")
            image_key = self._image_key(image_number)
            stats = object_stats.get(image_key, object_stats.get("__all__", self._empty_object_stats()))

            # 分母：红色头部精子数，即 Count_R_objects。
            r_count = self._get_float_any(row, [
                "Count_R_objects",
                "Count_R_Objects",
                "Count_R",
            ])

            # 共定位数量：新版尾部为 Count_R_colocalized；头部历史/当前测试输出为 Count_G_colocalized。
            colocalized_count = self._get_float_any(row, [
                "Count_R_colocalized",
                "Count_G_colocalized",
                "Count_colocalized",
                "Count_Colocalized",
            ])

            g_objects_count = self._get_float_any(row, [
                "Count_G_objects",
                "Count_G_Objects",
                "Count_G",
            ])

            r_objects_run = self._get_float_any(row, [
                "Count_R_objects_Run",
                "Count_R_Objects_Run",
            ])

            g_objects_run = self._get_float_any(row, [
                "Count_G_objects_Run",
                "Count_G_Objects_Run",
            ])

            cp_rate = self._get_float(row, "Math_ColocalizationRate")
            rate_fraction = self._to_fraction(cp_rate)

            if cp_rate is not None:
                expression_rate = rate_fraction * 100
            elif r_count and colocalized_count is not None:
                expression_rate = colocalized_count / r_count * 100
                rate_fraction = colocalized_count / r_count
            else:
                expression_rate = 0
                rate_fraction = 0

            if part == "tail":
                # 尾部荧光强度：∑Math_IntegratedIntensity255 / ∑AreaShape_Area * Math_ColocalizationRate
                if stats["area_sum"] > 0:
                    mean_intensity = stats["integrated255_sum"] / stats["area_sum"] * rate_fraction
                else:
                    mean_intensity = 0
            else:
                # 头部荧光强度：∑Math_MeanIntensity255 / Count_R_objects
                if r_count and r_count > 0:
                    mean_intensity = stats["mean255_sum"] / r_count
                else:
                    mean_intensity = 0

            rows.append({
                "image_number": self._normalize_image_number(image_number),
                "sperm_count": int(round(r_count or 0)),
                "positive_count": int(round(colocalized_count or 0)),
                "expression_rate": round(expression_rate or 0, 2),
                "mean_intensity": int(round(mean_intensity or 0)),
                # 以下字段用于核查/总计，不直接影响旧 UI。
                "rate_fraction": round(rate_fraction or 0, 6),
                "mean255_sum": round(stats["mean255_sum"], 2),
                "integrated255_sum": round(stats["integrated255_sum"], 2),
                "area_sum": round(stats["area_sum"], 2),
                "total_green_intensity": round(stats["integrated255_sum"], 2),
                "g_objects_count": int(round(g_objects_count or 0)),
                "r_objects_run": int(round(r_objects_run or 0)),
                "g_objects_run": int(round(g_objects_run or 0)),
            })

        total = self._build_total(rows, part)

        return {
            "success": True,
            "message": "解析成功。",
            "image_csv": str(image_csv),
            "object_csv": str(object_csv or ""),
            "calculation_mode": part,
            "warnings": warnings,
            "rows": rows,
            "total": total,
        }

    def parse_object_summary(self) -> dict:
        object_csv = self.find_object_csv()

        result = {
            "success": False,
            "message": "未找到对象 CSV。",
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
    def _normalize_part(value: str) -> str:
        text = str(value or "").strip().lower()
        if text in {"tail", "尾部"}:
            return "tail"
        if text in {"head", "头部"}:
            return "head"
        return ""

    def _infer_part(self, requested_part: str, image_df, object_df, object_csv: Optional[Path]) -> str:
        if requested_part in {"head", "tail"}:
            return requested_part

        image_cols = set(image_df.columns)
        object_cols = set(object_df.columns) if object_df is not None else set()
        object_name = object_csv.name.lower() if object_csv is not None else ""

        if "Math_MeanIntensity255" in object_cols:
            return "head"
        if "g_objects" in object_name:
            return "tail"
        if "Count_R_colocalized" in image_cols and "Math_IntegratedIntensity255" in object_cols:
            return "tail"
        return "head"

    @staticmethod
    def _validate_required_columns(image_df, object_df, object_csv: Optional[Path], part: str):
        missing = []

        if "Count_R_objects" not in image_df.columns:
            missing.append("Image.csv 缺少 Count_R_objects，无法确定精子数量。")

        if "Math_ColocalizationRate" not in image_df.columns:
            missing.append("Image.csv 缺少 Math_ColocalizationRate，无法确定标定率。")

        if "Count_R_colocalized" not in image_df.columns and "Count_G_colocalized" not in image_df.columns:
            missing.append("Image.csv 缺少 Count_R_colocalized / Count_G_colocalized，无法确定共定位数量。")

        if object_csv is None or object_df is None:
            missing.append("未找到对象级 CSV，无法按新版公式计算荧光强度。")
        elif object_df.empty:
            missing.append(f"对象级 CSV 为空：{object_csv}")
        else:
            object_cols = set(object_df.columns)
            if part == "tail":
                if "Math_IntegratedIntensity255" not in object_cols:
                    missing.append("尾部对象 CSV 缺少 Math_IntegratedIntensity255，无法计算尾部荧光强度。")
                if "AreaShape_Area" not in object_cols:
                    missing.append("尾部对象 CSV 缺少 AreaShape_Area，无法计算尾部荧光强度。")
            else:
                if "Math_MeanIntensity255" not in object_cols:
                    missing.append("头部对象 CSV 缺少 Math_MeanIntensity255，无法计算头部荧光强度。")

        if missing:
            return False, "；".join(missing)
        return True, ""

    @staticmethod
    def _empty_object_stats() -> dict:
        return {
            "mean255_sum": 0.0,
            "integrated255_sum": 0.0,
            "area_sum": 0.0,
            "object_count": 0,
        }

    def _build_object_stats(self, object_df) -> dict:
        stats_by_image = {}
        if object_df is None or object_df.empty:
            return stats_by_image

        if "ImageNumber" in object_df.columns:
            grouped = object_df.groupby("ImageNumber", dropna=False)
            for image_number, group in grouped:
                stats_by_image[self._image_key(image_number)] = self._stats_from_object_df(group)
        else:
            stats_by_image["__all__"] = self._stats_from_object_df(object_df)

        return stats_by_image

    @classmethod
    def _stats_from_object_df(cls, df) -> dict:
        stats = cls._empty_object_stats()
        stats["object_count"] = int(len(df))
        stats["mean255_sum"] = cls._sum_numeric(df, "Math_MeanIntensity255")
        stats["integrated255_sum"] = cls._sum_numeric(df, "Math_IntegratedIntensity255")
        stats["area_sum"] = cls._sum_numeric(df, "AreaShape_Area")
        return stats

    @staticmethod
    def _sum_numeric(df, column_name: str) -> float:
        if df is None or column_name not in df.columns:
            return 0.0
        try:
            values = pd.to_numeric(df[column_name], errors="coerce").fillna(0)
            return float(values.sum())
        except Exception:
            return 0.0

    @staticmethod
    def _to_fraction(value) -> float:
        if value is None:
            return 0.0
        try:
            v = float(value)
        except Exception:
            return 0.0
        if pd.isna(v):
            return 0.0
        # 兼容历史数据：如果 Pipeline 已输出百分数，则先转回 0~1，用于尾部公式。
        if v > 1:
            return v / 100.0
        return v

    @staticmethod
    def _image_key(value) -> str:
        try:
            if pd.isna(value):
                return ""
        except Exception:
            pass
        try:
            num = float(value)
            if num.is_integer():
                return str(int(num))
            return str(num)
        except Exception:
            return str(value).strip()

    @classmethod
    def _normalize_image_number(cls, value):
        key = cls._image_key(value)
        if key.isdigit():
            return int(key)
        return key

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

    @classmethod
    def _get_float_any(cls, row, column_names: list):
        for column_name in column_names:
            value = cls._get_float(row, column_name)
            if value is not None:
                return value
        return None

    @staticmethod
    def _build_total(rows: list, part: str) -> dict:
        total_sperm_count = sum(item.get("sperm_count", 0) for item in rows)
        total_positive_count = sum(item.get("positive_count", 0) for item in rows)
        total_mean255_sum = sum(item.get("mean255_sum", 0) for item in rows)
        total_integrated255_sum = sum(item.get("integrated255_sum", 0) for item in rows)
        total_area_sum = sum(item.get("area_sum", 0) for item in rows)

        if total_sperm_count > 0:
            total_rate_fraction = total_positive_count / total_sperm_count
        else:
            total_rate_fraction = 0

        if part == "tail":
            if total_area_sum > 0:
                total_mean_intensity = total_integrated255_sum / total_area_sum * total_rate_fraction
            else:
                total_mean_intensity = 0
        else:
            if total_sperm_count > 0:
                total_mean_intensity = total_mean255_sum / total_sperm_count
            elif rows:
                total_mean_intensity = sum(item.get("mean_intensity", 0) for item in rows) / len(rows)
            else:
                total_mean_intensity = 0

        return {
            "field_count": len(rows),
            "sperm_count": int(total_sperm_count),
            "positive_count": int(total_positive_count),
            "expression_rate": round(total_rate_fraction * 100, 2),
            "mean_intensity": int(round(total_mean_intensity or 0)),
            "rate_fraction": round(total_rate_fraction, 6),
            "mean255_sum": round(total_mean255_sum, 2),
            "integrated255_sum": round(total_integrated255_sum, 2),
            "area_sum": round(total_area_sum, 2),
            "total_green_intensity": round(total_integrated255_sum, 2),
        }
