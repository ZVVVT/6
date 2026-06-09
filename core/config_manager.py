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

    def get_float(self, section: str, key: str, default: float = 0.0) -> float:
        value = self.get(section, key, "")
        try:
            return float(value)
        except Exception:
            return default

    def get_section_dict(self, section: str) -> dict:
        if not self.config.has_section(section):
            return {}
        return dict(self.config.items(section))

    # -------------------------
    # Workspace
    # -------------------------

    def get_workspace_root(self) -> Path:
        return Path(self.get("Workspace", "root_dir", "workspace/cases"))

    def get_database_path(self) -> Path:
        return Path(self.get("Workspace", "database", "data/analysis.db"))

    def get_report_dir(self) -> Path:
        return Path(self.get("Workspace", "report_dir", "reports"))

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

    def get_protein_keys(self) -> list:
        keys_text = self.get("ProteinOrder", "keys", "")

        if keys_text:
            keys = [item.strip() for item in keys_text.split(",") if item.strip()]
        else:
            keys = ["protein1", "protein2", "protein3", "protein4", "protein5", "pna"]

        valid_keys = []
        for key in keys:
            if self.get("Protein", key, ""):
                valid_keys.append(key)

        return valid_keys or ["protein1", "protein2", "protein3", "protein4", "protein5", "pna"]

    def normalize_protein_key(self, protein_name_or_key: str) -> str:
        value = str(protein_name_or_key or "").strip()

        if not value:
            return ""

        if self.get("Protein", value, ""):
            return value

        for key in self.get_protein_keys():
            display_name = self.get_protein_display_name(key)
            if value == display_name:
                return key

        return value.lower()

    def get_protein_display_name(self, protein_key: str) -> str:
        protein_key = str(protein_key or "").strip()
        return self.get("ProteinNames", protein_key, protein_key)

    def get_protein_part(self, protein_name_or_key: str) -> str:
        protein_key = self.normalize_protein_key(protein_name_or_key)
        return self.get("Protein", protein_key, "")

    def get_protein_items(self) -> list:
        items = []

        for key in self.get_protein_keys():
            display_name = self.get_protein_display_name(key)
            part = self.get_protein_part(key)
            pipeline = self.get_pipeline_by_protein(key)
            custom_pipeline = self.get("ProteinPipelines", key, "").strip()
            intensity_min = self.get_protein_intensity_min(key)
            rate_min = self.get_protein_rate_min(key)

            items.append({
                "key": key,
                "name": display_name,
                "part": part,
                "pipeline": str(pipeline),
                "custom_pipeline": custom_pipeline,
                "intensity_min": intensity_min,
                "rate_min": rate_min,
            })

        return items

    def get_pipeline_by_protein(self, protein_name_or_key: str) -> Path:
        protein_key = self.normalize_protein_key(protein_name_or_key)

        custom_pipeline = self.get("ProteinPipelines", protein_key, "").strip()
        if custom_pipeline:
            return Path(custom_pipeline)

        protein_part = self.get_protein_part(protein_key)

        if protein_part == "tail":
            pipeline = self.get("CellProfiler", "tail_pipeline", "")
        elif protein_part == "pna":
            pipeline = self.get("CellProfiler", "pna_pipeline", "")
        else:
            pipeline = self.get("CellProfiler", "head_pipeline", "")

        return Path(pipeline)

    def get_next_protein_key(self, current_key: str) -> str:
        keys = self.get_protein_keys()

        if not keys:
            return ""

        current_key = self.normalize_protein_key(current_key)

        if current_key not in keys:
            return keys[0]

        current_index = keys.index(current_key)
        next_index = (current_index + 1) % len(keys)

        return keys[next_index]

    # -------------------------
    # Protein reference
    # -------------------------

    def get_protein_intensity_min(self, protein_name_or_key: str) -> float:
        protein_key = self.normalize_protein_key(protein_name_or_key)
        return self.get_float("ProteinReferenceIntensityMin", protein_key, 26.0)

    def get_protein_rate_min(self, protein_name_or_key: str) -> float:
        protein_key = self.normalize_protein_key(protein_name_or_key)
        return self.get_float("ProteinReferenceRateMin", protein_key, 82.88)

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
            "ProteinOrder": {
                "keys": "protein1,protein2,protein3,protein4,protein5,pna",
            },
            "ProteinNames": {
                "protein1": "HEL-1",
                "protein2": "HEL-2",
                "protein3": "HEL-3",
                "protein4": "HEL-4",
                "protein5": "HEL-5",
                "pna": "PNA",
            },
            "ProteinPipelines": {
                "protein1": "",
                "protein2": "",
                "protein3": "",
                "protein4": "",
                "protein5": "",
                "pna": "",
            },
            "ProteinReferenceIntensityMin": {
                "protein1": "26.0",
                "protein2": "26.0",
                "protein3": "26.0",
                "protein4": "26.0",
                "protein5": "26.0",
                "pna": "0",
            },
            "ProteinReferenceRateMin": {
                "protein1": "82.88",
                "protein2": "82.88",
                "protein3": "82.88",
                "protein4": "82.88",
                "protein5": "82.88",
                "pna": "0",
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