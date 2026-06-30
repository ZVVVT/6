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

    # ------------------------------------------------------------------
    # 软件品牌信息
    # ------------------------------------------------------------------
    def get_app_name(self) -> str:
        """
        软件窗口标题和左侧品牌区使用的名称。

        优先读取新版 [AppInfo]，兼容旧版 [Software]。
        """
        name = self.get("AppInfo", "app_name", "").strip()
        if name:
            return name
        name = self.get("Software", "name", "").strip()
        return name or "人精子蛋白质量分析软件"

    def get_app_logo_path(self) -> Path:
        """
        软件窗口图标和左侧品牌区使用的 LOGO。

        注意：报告 LOGO 仍然走 [Report] logo_path，避免把之前取消报告 LOGO 的逻辑又牵回来。
        """
        logo_path = self.get("AppInfo", "logo_path", "").strip()
        if not logo_path:
            logo_path = self.get("Software", "logo_path", "").strip()
        if not logo_path:
            logo_path = r"assets\logo.png"
        return Path(logo_path)

    def get_app_font_path(self) -> Path:
        """软件界面字体文件路径。

        为空时表示使用系统默认字体。
        只有配置了字体文件，且文件存在并能被 Qt 加载时，软件才会使用该字体。
        """
        font_path = self.get("AppInfo", "font_path", "").strip()
        return Path(font_path) if font_path else Path("")

    def get_app_font_size(self) -> int:
        """软件界面字号，限制在 8~18，避免用户误填导致界面异常。"""
        value = self.get("AppInfo", "font_size", "10").strip()
        try:
            size = int(float(value))
        except Exception:
            size = 10
        return max(8, min(18, size))

    # ------------------------------------------------------------------
    # 工作目录 / 图片规则 / 蛋白配置
    # ------------------------------------------------------------------
    def get_workspace_root(self) -> Path:
        return Path(self.get("Workspace", "root_dir", "workspace/cases"))

    def get_database_path(self) -> Path:
        return Path(self.get("Workspace", "database", "data/analysis.db"))

    def get_report_dir(self) -> Path:
        return Path(self.get("Workspace", "report_dir", "reports"))

    def get_image_rule(self) -> dict:
        return {
            "r_suffix": self.get("ImageRule", "r_suffix", "_R"),
            "g_suffix": self.get("ImageRule", "g_suffix", "_G"),
            "dic_suffix": self.get("ImageRule", "dic_suffix", "_DIC"),
            "merge_suffix": self.get("ImageRule", "merge_suffix", "_Merge"),
            "image_ext": self.get("ImageRule", "image_ext", ".tif"),
        }

    def get_protein_keys(self) -> list:
        keys_text = self.get("ProteinOrder", "keys", "")
        if keys_text:
            keys = [item.strip() for item in keys_text.split(",") if item.strip()]
        else:
            keys = ["protein1", "protein2", "protein3", "protein4", "protein5"]

        valid_keys = []
        for key in keys:
            if self.get("Protein", key, ""):
                valid_keys.append(key)
        return valid_keys or ["protein1", "protein2", "protein3", "protein4", "protein5"]

    def normalize_protein_key(self, protein_name_or_key: str) -> str:
        value = str(protein_name_or_key or "").strip()
        if not value:
            return ""

        # 直接传入内部编号 protein1~protein5 时，直接返回。
        if self.get("Protein", value, ""):
            return value

        # 兼容历史显示名称和当前标准蛋白编号，避免恢复默认或历史数据导致结果无法对应。
        alias_map = {
            "HEL-1": "protein1",
            "HEL-2": "protein2",
            "HEL-3": "protein3",
            "HEL-4": "protein4",
            "HEL-5": "protein5",
            "Q9BYW3": "protein1",
            "P10323": "protein2",
            "Q96P56": "protein3",
            "Q8IYV9": "protein4",
            "W5XKT8": "protein5",
        }
        if value in alias_map:
            return alias_map[value]

        value_upper = value.upper()
        for name, key in alias_map.items():
            if value_upper == name.upper():
                return key

        for key in self.get_protein_keys():
            if value == self.get_protein_display_name(key):
                return key

        return value.lower()

    def get_protein_display_name(self, protein_key: str) -> str:
        protein_key = str(protein_key or "").strip()
        return self.get("ProteinNames", protein_key, protein_key)

    def get_protein_part(self, protein_name_or_key: str) -> str:
        protein_key = self.normalize_protein_key(protein_name_or_key)
        return self.get("Protein", protein_key, "")

    def get_pipeline_by_protein(self, protein_name_or_key: str) -> Path:
        protein_key = self.normalize_protein_key(protein_name_or_key)
        custom_pipeline = self.get("ProteinPipelines", protein_key, "").strip()
        if custom_pipeline:
            return Path(custom_pipeline)

        protein_part = self.get_protein_part(protein_key)
        if protein_part == "tail":
            pipeline = self.get("CellProfiler", "tail_pipeline", "")
        else:
            pipeline = self.get("CellProfiler", "head_pipeline", "")
        return Path(pipeline)

    def get_protein_intensity_min(self, protein_name_or_key: str) -> float:
        protein_key = self.normalize_protein_key(protein_name_or_key)
        return self.get_float("ProteinReferenceIntensityMin", protein_key, 26.0)

    def get_protein_rate_min(self, protein_name_or_key: str) -> float:
        protein_key = self.normalize_protein_key(protein_name_or_key)
        return self.get_float("ProteinReferenceRateMin", protein_key, 82.88)

    def get_protein_items(self) -> list:
        items = []
        for key in self.get_protein_keys():
            custom_pipeline = self.get("ProteinPipelines", key, "").strip()
            items.append(
                {
                    "key": key,
                    "name": self.get_protein_display_name(key),
                    "part": self.get_protein_part(key),
                    "pipeline": str(self.get_pipeline_by_protein(key)),
                    "custom_pipeline": custom_pipeline,
                    "intensity_min": self.get_protein_intensity_min(key),
                    "rate_min": self.get_protein_rate_min(key),
                }
            )
        return items

    # ------------------------------------------------------------------
    # MvImageID / CellProfiler 配置
    # ------------------------------------------------------------------
    def get_powershell_exe(self) -> str:
        return self.get("CellProfiler", "powershell_exe", "powershell.exe")

    def get_source_project_dir(self) -> Path:
        return Path(self.get("CellProfiler", "source_project_dir", r"F:\MvImageID"))

    def get_venv_activate(self) -> Path:
        return Path(
            self.get("CellProfiler", "venv_activate", r"F:\MvImageID\.venv\Scripts\Activate.ps1")
        )

    def get_module_name(self) -> str:
        return self.get("CellProfiler", "module_name", "MvImageID")

    def get_plugins_directory(self) -> Path:
        return Path(self.get("CellProfiler", "plugins_directory", ""))

    def get_log_file(self) -> Path:
        return Path(self.get("CellProfiler", "log_file", r"F:\MvImageID\run.log"))

    def get_logo_path(self) -> Path:
        """报告 LOGO 路径。为兼容旧代码保留，不用于软件窗口图标。"""
        return Path(self.get("Report", "logo_path", ""))

    # ------------------------------------------------------------------
    # 默认配置
    # ------------------------------------------------------------------
    def ensure_default_config(self):
        defaults = {
            "Software": {
                "name": "人精子蛋白质量分析软件",
                "version": "1.0.0",
                "company": "公司名称",
            },
            "AppInfo": {
                "app_name": "人精子蛋白质量分析软件",
                "logo_path": r"assets\logo.png",
                # 为空表示使用系统默认字体；需要自定义字体时再填写字体文件路径。
                "font_path": "",
                "font_size": "10",
            },
            "CellProfiler": {
                "run_mode": "source",
                "powershell_exe": "powershell.exe",
                "source_project_dir": r"F:\MvImageID",
                "venv_activate": r"F:\MvImageID\.venv\Scripts\Activate.ps1",
                "module_name": "MvImageID",
                "head_pipeline": r"F:\MvImageID\pipelines4_DLM\DLM\CPP.cppipe",
                "tail_pipeline": r"F:\MvImageID\pipelines4_DLM\DLM\CPP.cppipe",
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
            },
            "ProteinOrder": {
                "keys": "protein1,protein2,protein3,protein4,protein5",
            },
            "ProteinNames": {
                "protein1": "Q9BYW3",
                "protein2": "P10323",
                "protein3": "Q96P56",
                "protein4": "Q8IYV9",
                "protein5": "W5XKT8",
            },
            "ProteinPipelines": {
                "protein1": "",
                "protein2": "",
                "protein3": "",
                "protein4": "",
                "protein5": "",
            },
            "ProteinReferenceIntensityMin": {
                "protein1": "26.0",
                "protein2": "26.0",
                "protein3": "26.0",
                "protein4": "26.0",
                "protein5": "26.0",
            },
            "ProteinReferenceRateMin": {
                "protein1": "82.88",
                "protein2": "82.88",
                "protein3": "82.88",
                "protein4": "82.88",
                "protein5": "82.88",
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
