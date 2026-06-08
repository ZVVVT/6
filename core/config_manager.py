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

    def get_pipeline_by_protein(self, protein_name: str) -> Path:
        protein_part = self.get_protein_part(protein_name)

        if protein_part == "tail":
            pipeline = self.get("CellProfiler", "tail_pipeline", "")
        elif protein_part == "pna":
            pipeline = self.get("CellProfiler", "pna_pipeline", "")
        else:
            pipeline = self.get("CellProfiler", "head_pipeline", "")

        return Path(pipeline)

    def get_powershell_exe(self) -> str:
        return self.get("CellProfiler", "powershell_exe", "powershell.exe")

    def get_source_project_dir(self) -> Path:
        return Path(self.get("CellProfiler", "source_project_dir", r"F:\MvImageID"))

    def get_venv_activate(self) -> Path:
        return Path(self.get("CellProfiler", "venv_activate", r"F:\MvImageID\.venv\Scripts\Activate.ps1"))

    def get_module_name(self) -> str:
        return self.get("CellProfiler", "module_name", "MvImageID")

    def get_plugins_directory(self) -> Path:
        return Path(self.get("CellProfiler", "plugins_directory", ""))

    def get_log_file(self) -> Path:
        return Path(self.get("CellProfiler", "log_file", r"F:\MvImageID\run.log"))

    def get_report_dir(self) -> Path:
        report_dir = self.get("Workspace", "report_dir", "reports")
        return Path(report_dir)