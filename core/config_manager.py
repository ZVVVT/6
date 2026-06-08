import configparser
from pathlib import Path


class ConfigManager:
    def __init__(self, config_path: str = "config.ini"):
        self.config_path = Path(config_path)
        self.config = configparser.ConfigParser()
        self.load()

    def load(self):
        self.config.read(self.config_path, encoding="utf-8")

    def save(self):
        with self.config_path.open("w", encoding="utf-8") as f:
            self.config.write(f)

    def get(self, section: str, key: str, default: str = "") -> str:
        try:
            return self.config.get(section, key)
        except Exception:
            return default

    def set(self, section: str, key: str, value: str):
        if not self.config.has_section(section):
            self.config.add_section(section)

        self.config.set(section, key, str(value))

    def get_section_dict(self, section: str) -> dict:
        if not self.config.has_section(section):
            return {}

        return dict(self.config.items(section))

    # -------------------------
    # Workspace
    # -------------------------

    def get_workspace_root(self) -> Path:
        root_dir = self.get("Workspace", "root_dir", "workspace/cases")
        return Path(root_dir)

    def get_database_path(self) -> Path:
        database = self.get("Workspace", "database", "data/analysis.db")
        return Path(database)

    def get_report_dir(self) -> Path:
        report_dir = self.get("Workspace", "report_dir", "reports")
        return Path(report_dir)

    # -------------------------
    # Image rule
    # -------------------------

    def get_image_rule(self) -> dict:
        return {
            "r_suffix": self.get("ImageRule", "r_suffix", "_R"),
            "g_suffix": self.get("ImageRule", "g_suffix", "_G"),
            "dic_suffix": self.get("ImageRule", "dic_suffix", "_DIC"),
            "merge_suffix": self.get("ImageRule", "merge_suffix", "_Merge"),
            "image_ext": self.get("ImageRule", "image_ext", ".tif"),
        }

    # -------------------------
    # Protein
    # -------------------------

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

    # -------------------------
    # MvImageID / CellProfiler source mode
    # -------------------------

    def get_powershell_exe(self) -> str:
        return self.get("CellProfiler", "powershell_exe", "powershell.exe")

    def get_source_project_dir(self) -> Path:
        return Path(self.get("CellProfiler", "source_project_dir", r"F:\MvImageID"))

    def get_venv_activate(self) -> Path:
        return Path(
            self.get(
                "CellProfiler",
                "venv_activate",
                r"F:\MvImageID\.venv\Scripts\Activate.ps1",
            )
        )

    def get_module_name(self) -> str:
        return self.get("CellProfiler", "module_name", "MvImageID")

    def get_plugins_directory(self) -> Path:
        return Path(self.get("CellProfiler", "plugins_directory", ""))

    def get_log_file(self) -> Path:
        return Path(self.get("CellProfiler", "log_file", r"F:\MvImageID\run.log"))

    # -------------------------
    # Report
    # -------------------------

    def get_logo_path(self) -> Path:
        return Path(self.get("Report", "logo_path", ""))

    # -------------------------
    # Default config
    # -------------------------

    def ensure_default_config(self):
        defaults = {
            "Software": {
                "name": "人精子蛋白质量分析软件",
                "version": "1.0.0",
                "company": "公司名称",
            },
            "CellProfiler": {
                "run_mode": "source",
                "powershell_exe": "powershell.exe",
                "source_project_dir": r"F:\MvImageID",
                "venv_activate": r"F:\MvImageID\.venv\Scripts\Activate.ps1",
                "module_name": "MvImageID",
                "head_pipeline": r"F:\MvImageID\pipelines4_DLM\DLM\CPP.cppipe",
                "tail_pipeline": r"F:\MvImageID\pipelines4_DLM\DLM\CPP.cppipe",
                "pna_pipeline": r"F:\MvImageID\pipelines4_DLM\DLM\CPP.cppipe",
                "plugins_directory": r"F:\MvImageID\C-plugins\active_plugins",
                "log_file": r"F:\MvImageID\run.log",
            },
            "Workspace": {
                "root_dir": r"workspace\cases",
                "database": r"data\analysis.db",
                "report_dir": "reports",
            },
            "ImageRule": {
                "r_suffix": "_R",
                "g_suffix": "_G",
                "dic_suffix": "_DIC",
                "merge_suffix": "_Merge",
                "image_ext": ".tif",
            },
            "Protein": {
                "protein1": "head",
                "protein2": "head",
                "protein3": "tail",
                "protein4": "head",
                "protein5": "head",
                "pna": "pna",
            },
            "Report": {
                "logo_path": r"assets\logo.png",
            },
        }

        changed = False

        for section, values in defaults.items():
            if not self.config.has_section(section):
                self.config.add_section(section)
                changed = True

            for key, value in values.items():
                if not self.config.has_option(section, key):
                    self.config.set(section, key, value)
                    changed = True

        if changed:
            self.save()