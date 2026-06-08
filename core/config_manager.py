import configparser
from pathlib import Path


class ConfigManager:
    def __init__(self, config_path: str = "config.ini"):
        self.config_path = Path(config_path)
        self.config = configparser.ConfigParser()
        self.config.read(self.config_path, encoding="utf-8")

    def get(self, section: str, key: str, default: str = "") -> str:
        try:
            return self.config.get(section, key)
        except Exception:
            return default

    def get_workspace_root(self) -> Path:
        root_dir = self.get("Workspace", "root_dir", "workspace/cases")
        return Path(root_dir)

    def get_image_rule(self) -> dict:
        return {
            "r_suffix": self.get("ImageRule", "r_suffix", "_R"),
            "g_suffix": self.get("ImageRule", "g_suffix", "_G"),
            "dic_suffix": self.get("ImageRule", "dic_suffix", "_DIC"),
            "merge_suffix": self.get("ImageRule", "merge_suffix", "_Merge"),
            "image_ext": self.get("ImageRule", "image_ext", ".tif"),
        }

    def get_protein_part(self, protein_name: str) -> str:
        return self.get("Protein", protein_name.lower(), "")