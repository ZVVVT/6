from pathlib import Path
from datetime import datetime


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
            elif file_path.name.lower().endswith(".ps1"):
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

    def get_summary(self) -> dict:
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