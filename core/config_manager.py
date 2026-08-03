import configparser
import math
import sys
from pathlib import Path


def get_application_root() -> Path:
    """返回软件运行根目录。

    源码运行时：项目根目录。
    PyInstaller 打包后：exe 所在目录。

    这样可以避免打包后把 config.ini 保存到 _internal，导致界面提示已保存但实际不生效。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


class ConfigManager:
    def __init__(self, config_path: str = None):
        self.app_root = get_application_root()
        if config_path is None:
            self.config_path = self.app_root / "config.ini"
        else:
            path = Path(config_path)
            self.config_path = path if path.is_absolute() else self.app_root / path
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
            number = float(value)
        except Exception:
            return default
        return number if math.isfinite(number) else default

    def get_bool(self, section: str, key: str, default: bool = False) -> bool:
        value = self.get(section, key, "").strip().lower()
        if value in {"1", "true", "yes", "on", "是", "启用"}:
            return True
        if value in {"0", "false", "no", "off", "否", "禁用"}:
            return False
        return bool(default)

    def get_int(self, section: str, key: str, default: int = 0) -> int:
        value = self.get(section, key, "")
        try:
            return int(float(value))
        except Exception:
            return int(default)

    # ------------------------------------------------------------------
    # 最终结果校正
    # ------------------------------------------------------------------
    def is_result_adjustment_enabled(self) -> bool:
        return self.get_bool("ResultAdjustment", "enabled", True)

    def is_use_case_tail_rate_for_head_intensity(self) -> bool:
        return self.get_bool(
            "ResultAdjustment",
            "use_case_tail_rate_for_head_intensity",
            True,
        )

    def get_default_tail_rate_ratio(self) -> float:
        value = self.get_float(
            "ResultAdjustment",
            "default_tail_rate_ratio",
            1.0,
        )
        if not math.isfinite(value) or value < 0.0 or value > 1.0:
            return 1.0
        return value

    def get_fluorescence_result_factor(self) -> float:
        value = self.get_float(
            "ResultAdjustment",
            "fluorescence_result_factor",
            1.0,
        )
        if not math.isfinite(value) or value <= 0.0:
            return 1.0
        return value

    def get_expression_rate_result_factor(self) -> float:
        value = self.get_float(
            "ResultAdjustment",
            "expression_rate_result_factor",
            1.0,
        )
        if not math.isfinite(value) or value <= 0.0:
            return 1.0
        return value

    def is_sync_positive_count_with_expression_rate(self) -> bool:
        return self.get_bool(
            "ResultAdjustment",
            "sync_positive_count_with_expression_rate",
            False,
        )

    def get_result_display_decimals(self) -> int:
        value = self.get_int("ResultAdjustment", "display_decimals", 1)
        return max(0, min(6, value))

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

    def get_app_font_path(self) -> str:
        """软件界面字体文件路径。

        返回规则：
        - 空字符串：使用系统默认字体。
        - 非空字符串：尝试按该路径加载字体文件。

        注意：这里不能返回 Path("")，因为 Path("") 会显示成 "."，
        并在路径检查时被解析成项目根目录，导致误报。
        """
        font_path = self.get("AppInfo", "font_path", "").strip()
        if font_path in ("", ".", "系统默认字体"):
            return ""
        return font_path

    def get_app_font_size(self) -> int:
        """软件界面字号，限制在 8~18，避免用户误填导致界面异常。"""
        value = self.get("AppInfo", "font_size", "10").strip()
        try:
            size = int(float(value))
        except Exception:
            size = 10
        return max(8, min(18, size))

    # ------------------------------------------------------------------
    # MvImageID 配置读取
    # ------------------------------------------------------------------
    def get_mvimageid(self, key: str, default: str = "") -> str:
        """
        读取 MvImageID 运行环境配置。

        新版本统一使用 [MvImageID] 配置节。
        """
        value = self.get("MvImageID", key, "").strip()
        if value:
            return value
        return default

    # ------------------------------------------------------------------
    # 工作目录 / 图片规则 / 蛋白配置
    # ------------------------------------------------------------------
    def get_workspace_root(self) -> Path:
        return Path(self.get("Workspace", "root_dir", "workspace/cases"))

    def get_database_path(self) -> Path:
        return Path(self.get("Workspace", "database", "data/analysis.db"))

    def get_report_dir(self) -> Path:
        return Path(self.get("Workspace", "report_dir", "reports"))

    def get_qc_root_dir(self) -> Path:
        """质控微球测试根目录。

        默认使用 workspace/qc，质控每次运行会在其下创建 YYYYMMDD_01 目录。
        """
        return Path(self.get("QC", "root_dir", r"workspace\qc"))

    def get_qc_pipeline(self) -> Path:
        """质控微球荧光强度测试 Pipeline。"""
        return Path(self.get_mvimageid("qc_pipeline", r"pipelines\pipeline_qc.cppipe"))

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
            pipeline = self.get_mvimageid("tail_pipeline", "")
        else:
            pipeline = self.get_mvimageid("head_pipeline", "")
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
    # MvImageID 配置
    # ------------------------------------------------------------------
    def get_source_project_dir(self) -> Path:
        return Path(self.get_mvimageid("source_project_dir", r"F:\MvImageID"))

    def get_python_exe(self) -> Path:
        """MvImageID 虚拟环境 Python 解释器。

        新版本直接调用 python.exe。未配置时，默认使用源码目录下的
        .venv/Scripts/python.exe。
        """
        python_exe = self.get_mvimageid("python_exe", "").strip()
        if python_exe:
            return Path(python_exe)
        return self.get_source_project_dir() / ".venv" / "Scripts" / "python.exe"

    def get_module_name(self) -> str:
        return self.get_mvimageid("module_name", "MvImageID")

    def get_plugins_directory(self) -> Path:
        return Path(self.get_mvimageid("plugins_directory", ""))


    def get_logo_path(self) -> Path:
        """报告 LOGO 路径。为兼容旧代码保留，不用于软件窗口图标。"""
        return Path(self.get("Report", "logo_path", ""))

    # ------------------------------------------------------------------
    # 历史配置清理
    # ------------------------------------------------------------------
    def cleanup_legacy_config(self) -> bool:
        """清理旧版本遗留配置。

        当前正式运行配置统一放在 [MvImageID]。
        不再生成或保留 [CellProfiler]，也不再保留 PowerShell、Activate.ps1、
        全局日志文件这些旧执行方式相关字段。
        """
        changed = False

        if self.config.has_section("CellProfiler"):
            self.config.remove_section("CellProfiler")
            changed = True

        obsolete_mvimageid_options = (
            "powershell_exe",
            "venv_activate",
            "log_file",
        )
        if self.config.has_section("MvImageID"):
            for option in obsolete_mvimageid_options:
                if self.config.has_option("MvImageID", option):
                    self.config.remove_option("MvImageID", option)
                    changed = True

        # 旧界面可能把“系统默认字体”文字写入 font_path。
        # 新逻辑统一用空字符串表示系统默认字体。
        if self.config.has_section("AppInfo"):
            font_path = self.config.get("AppInfo", "font_path", fallback="").strip()
            if font_path in (".", "系统默认字体"):
                self.config.set("AppInfo", "font_path", "")
                changed = True

        return changed

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
            "MvImageID": {
                "run_mode": "source",
                "source_project_dir": r"F:\MvImageID",
                "python_exe": r"F:\MvImageID\.venv\Scripts\python.exe",
                "module_name": "MvImageID",
                "head_pipeline": r"pipelines\pipeline_head.cppipe",
                "tail_pipeline": r"pipelines\pipeline_tail.cppipe",
                "qc_pipeline": r"pipelines\pipeline_qc.cppipe",
                "plugins_directory": r"F:\MvImageID\C-plugins\active_plugins",
            },
            "Workspace": {
                "root_dir": r"workspace\cases",
                "database": r"data\analysis.db",
                "report_dir": "reports",
            },
            "QC": {
                "root_dir": r"workspace\qc",
                "output_dir": "",
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
            "ResultAdjustment": {
                "enabled": "true",
                "use_case_tail_rate_for_head_intensity": "true",
                "default_tail_rate_ratio": "1.0",
                "fluorescence_result_factor": "1.0",
                "expression_rate_result_factor": "1.0",
                "sync_positive_count_with_expression_rate": "false",
                "display_decimals": "1",
            },
            "Report": {
                "logo_path": r"assets\logo.png",
            },
        }

        changed = self.cleanup_legacy_config()
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
