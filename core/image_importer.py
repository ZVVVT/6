import shutil
from pathlib import Path
from typing import Dict, List


class ImageImporter:
    """
    按文件名后缀识别通道图像：
    xxx_R.tif
    xxx_G.tif
    xxx_DIC.tif
    xxx_Merge.tif

    其中 xxx 会作为视野编号/视野基础名。
    """

    def __init__(self, image_rule: dict):
        self.r_suffix = image_rule.get("r_suffix", "_R")
        self.g_suffix = image_rule.get("g_suffix", "_G")
        self.dic_suffix = image_rule.get("dic_suffix", "_DIC")
        self.merge_suffix = image_rule.get("merge_suffix", "_Merge")
        self.image_ext = image_rule.get("image_ext", ".tif").lower()

        self.support_exts = {
            ".tif",
            ".tiff",
            ".png",
            ".jpg",
            ".jpeg",
            ".bmp",
        }

    def scan_folder(self, folder_path: str) -> List[dict]:
        folder = Path(folder_path)

        if not folder.exists():
            raise FileNotFoundError(f"图片文件夹不存在：{folder}")

        if not folder.is_dir():
            raise NotADirectoryError(f"不是有效文件夹：{folder}")

        image_files = [
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in self.support_exts
        ]

        groups: Dict[str, dict] = {}

        for image_path in image_files:
            channel_info = self._parse_channel(image_path)

            if channel_info is None:
                continue

            field_key, channel = channel_info

            if field_key not in groups:
                groups[field_key] = {
                    "field_no": field_key,
                    "R": "",
                    "G": "",
                    "DIC": "",
                    "Merge": "",
                    "status": "未完整",
                }

            groups[field_key][channel] = str(image_path)

        results = list(groups.values())

        for item in results:
            required_ok = bool(item["R"]) and bool(item["G"])
            item["status"] = "完整" if required_ok else "缺少R或G"

        results.sort(key=lambda x: x["field_no"])
        return results

    def copy_to_workspace(
        self,
        source_folder: str,
        target_folder: str,
        protein_name: str,
    ) -> List[dict]:
        scan_results = self.scan_folder(source_folder)

        target_root = Path(target_folder)
        target_root.mkdir(parents=True, exist_ok=True)

        copied_results = []

        for item in scan_results:
            copied_item = {
                "field_no": item["field_no"],
                "R": "",
                "G": "",
                "DIC": "",
                "Merge": "",
                "status": item["status"],
            }

            for channel in ["R", "G", "DIC", "Merge"]:
                source_path = item.get(channel, "")

                if not source_path:
                    continue

                source = Path(source_path)
                new_name = f"{protein_name}_{item['field_no']}_{channel}{source.suffix}"
                target = target_root / new_name

                shutil.copy2(source, target)
                copied_item[channel] = str(target)

            copied_results.append(copied_item)

        return copied_results

    def _parse_channel(self, image_path: Path):
        stem = image_path.stem

        suffix_map = {
            self.r_suffix: "R",
            self.g_suffix: "G",
            self.dic_suffix: "DIC",
            self.merge_suffix: "Merge",
        }

        for suffix, channel in suffix_map.items():
            if stem.endswith(suffix):
                field_key = stem[:-len(suffix)]
                field_key = field_key.strip("_- ")

                if not field_key:
                    field_key = stem

                return field_key, channel

        return None