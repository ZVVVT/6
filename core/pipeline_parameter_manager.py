# -*- coding: utf-8 -*-
"""
pipeline_parameter_manager.py

管道参数管理器。

设计原则：
1. pipeline_params.ini 只保存算法/管道参数；
2. pipelines/templates/*.cppipe 保存母版管道；
3. pipelines/*.cppipe 是软件实际运行的管道；
4. 用户在系统设置中修改参数后，点击“生成管道”，才会根据模板生成实际运行管道；
5. 蛋白分析、批量分析、质控测试运行时仍直接读取 config.ini 中配置的 .cppipe，不做动态生成；
6. 每次覆盖实际管道前，自动备份当前 pipelines/*.cppipe，避免误操作不可恢复。
"""

from __future__ import annotations

import configparser
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class PipelineTarget:
    key: str
    title: str
    template_name: str
    output_name: str


@dataclass
class ReplacementRecord:
    section: str
    module_num: int
    setting_name: str
    param_key: str
    old_value: str
    new_value: str
    measurement_name: str = ""


class PipelineParameterManager:
    """管理 pipeline_params.ini，并根据模板生成 .cppipe。"""

    PARAM_FILE_NAME = "pipeline_params.ini"

    TARGETS = {
        "Head": PipelineTarget(
            key="Head",
            title="头部蛋白管道",
            template_name="pipeline_head_template.cppipe",
            output_name="pipeline_head.cppipe",
        ),
        "Tail": PipelineTarget(
            key="Tail",
            title="尾部蛋白管道",
            template_name="pipeline_tail_template.cppipe",
            output_name="pipeline_tail.cppipe",
        ),
        "QC": PipelineTarget(
            key="QC",
            title="质控微球管道",
            template_name="pipeline_qc_template.cppipe",
            output_name="pipeline_qc.cppipe",
        ),
    }

    DEFAULT_PARAMS = {
        "Head": {
            # 默认值来自本次上传的 pipeline_head.cppipe
            "red_diameter": "35",
            "red_flow_threshold": "0.4",
            "red_cellprob_threshold": "0",
            "red_min_size": "30",
            "red_formfactor_min": "0.4",
            "red_equivalent_diameter_max": "75",
            "green_expand_pixels": "1",
            "green_intensity_min": "10",
        },
        "Tail": {
            # 默认值来自本次上传的 pipeline_tail.cppipe
            "green_diameter": "50",
            "green_flow_threshold": "0.4",
            "green_distance_threshold": "0",
            "green_min_size": "10",
            "green_eccentricity_min": "0.8",
            "green_intensity_min": "5",
            "red_diameter": "35",
            "red_flow_threshold": "0.4",
            "red_cellprob_threshold": "0",
            "red_min_size": "30",
            "red_formfactor_min": "0.2",
            "red_equivalent_diameter_max": "75",
            "red_search_radius": "30",
            "colocalized_child_count_min": "1",
        },
        "QC": {
            "bead_diameter": "65",
            "bead_flow_threshold": "0.5",
            "bead_cellprob_threshold": "0",
            "bead_min_size": "10",
            "bead_formfactor_min": "0.7",
        },
    }

    PARAM_VERSION = "20260707_uploaded_head_tail_cppipe_v1"

    # 用于把旧 pipeline_params.ini 自动迁移到本次上传管道的默认值。
    # 如果用户参数刚好还是旧默认值，则更新为新默认；如果用户已改成其他值，则保留。
    PREVIOUS_DEFAULT_PARAMS = {
        "Head": {
            "red_diameter": "65",
            "red_flow_threshold": "0.5",
            "red_cellprob_threshold": "0",
            "red_min_size": "10",
            "red_formfactor_min": "0.2",
            "red_equivalent_diameter_max": "70",
            "green_expand_pixels": "1",
            "green_intensity_min": "5",
        },
        "Tail": {
            "green_diameter": "10",
            "green_flow_threshold": "2",
            "green_distance_threshold": "-2",
            "green_min_size": "10",
            "green_eccentricity_min": "0.8",
            "red_diameter": "65",
            "red_flow_threshold": "0.5",
            "red_cellprob_threshold": "0",
            "red_min_size": "10",
            "red_formfactor_min": "0.2",
            "red_equivalent_diameter_max": "70",
            "red_search_radius": "50",
            "colocalized_child_count_min": "1",
        },
    }

    # 每个参数精确定位：section -> [(module_num, setting_name, param_key, measurement_name), ...]
    # measurement_name 可选，用于 FilterObjects 中同一个模块存在多组“最小值/最大值”的情况。
    # 例如红色对象过滤模块同时包含 AreaShape_FormFactor 和 AreaShape_EquivalentDiameter，
    # 如果只按“最大值”替换，可能会误改 FormFactor 的最大值。
    PARAM_RULES: Dict[str, List[Tuple[int, str, str, str]]] = {
        "Head": [
            # pipeline_head.cppipe：红色头部识别 RunCellpose，module_num:7
            (7, "预期物体直径", "red_diameter", ""),
            (7, "流阈值", "red_flow_threshold", ""),
            (7, "细胞概率阈值", "red_cellprob_threshold", ""),
            (7, "最小尺寸", "red_min_size", ""),
            # pipeline_head.cppipe：红色头部过滤 FilterObjects，module_num:9
            (9, "最小值", "red_formfactor_min", "AreaShape_FormFactor"),
            (9, "最大值", "red_equivalent_diameter_max", "AreaShape_EquivalentDiameter"),
            # pipeline_head.cppipe：绿色匹配区域 ExpandOrShrinkObjects，module_num:11
            (11, "扩张或收缩的像素数", "green_expand_pixels", ""),
            # pipeline_head.cppipe：共定位绿色强度过滤 FilterObjects，module_num:15
            (15, "最小值", "green_intensity_min", "Math_G_objects_MeanIntensity"),
        ],
        "Tail": [
            # pipeline_tail.cppipe：绿色尾部识别 RunOmnipose，module_num:7
            (7, "Expected object diameter", "green_diameter", ""),
            (7, "Flow threshold", "green_flow_threshold", ""),
            (7, "Distance field threshold", "green_distance_threshold", ""),
            (7, "Minimum size", "green_min_size", ""),
            # pipeline_tail.cppipe：绿色尾部过滤 FilterObjects，module_num:11
            (11, "最小值", "green_eccentricity_min", "AreaShape_Eccentricity"),
            (11, "最小值", "green_intensity_min", "Math_G_objects_Run_MeanIntensity"),
            # pipeline_tail.cppipe：红色头部识别 RunCellpose，module_num:15
            (15, "预期物体直径", "red_diameter", ""),
            (15, "流阈值", "red_flow_threshold", ""),
            (15, "细胞概率阈值", "red_cellprob_threshold", ""),
            (15, "最小尺寸", "red_min_size", ""),
            # pipeline_tail.cppipe：红色头部过滤 FilterObjects，module_num:17
            (17, "最小值", "red_formfactor_min", "AreaShape_FormFactor"),
            (17, "最大值", "red_equivalent_diameter_max", "AreaShape_EquivalentDiameter"),
            # pipeline_tail.cppipe：红色头部搜索区 ExpandOrShrinkObjects，module_num:19
            (19, "扩张或收缩的像素数", "red_search_radius", ""),
            # pipeline_tail.cppipe：共定位红色精子过滤 FilterObjects，module_num:21
            (21, "最小值", "colocalized_child_count_min", "Children_G_objects_Count"),
        ],
        "QC": [
            (6, "预期物体直径", "bead_diameter", ""),
            (6, "流阈值", "bead_flow_threshold", ""),
            (6, "细胞概率阈值", "bead_cellprob_threshold", ""),
            (6, "最小尺寸", "bead_min_size", ""),
            (8, "最小值", "bead_formfactor_min", "AreaShape_FormFactor"),
        ],
    }

    REQUIRED_SECTIONS = ["Head", "Tail"]
    OPTIONAL_SECTIONS = ["QC"]

    def __init__(self, project_root: Optional[Path] = None, param_file: Optional[Path] = None):
        self.project_root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
        self.pipelines_dir = self.project_root / "pipelines"
        self.templates_dir = self.pipelines_dir / "templates"
        self.backups_dir = self.pipelines_dir / "backups"
        self.param_file = Path(param_file) if param_file else self.project_root / self.PARAM_FILE_NAME
        self.config = configparser.ConfigParser()
        self.load()

    # ------------------------------------------------------------------
    # 参数文件
    # ------------------------------------------------------------------

    def load(self) -> None:
        self.config = configparser.ConfigParser()
        self.config.read(self.param_file, encoding="utf-8")

    def save(self) -> None:
        self.param_file.parent.mkdir(parents=True, exist_ok=True)
        with self.param_file.open("w", encoding="utf-8") as f:
            self.config.write(f)

    def ensure_default_params(self) -> None:
        changed = False

        if not self.config.has_section("Meta"):
            self.config.add_section("Meta")
            changed = True

        current_version = self.config.get("Meta", "pipeline_param_version", fallback="")
        need_migrate = current_version != self.PARAM_VERSION

        for section, values in self.DEFAULT_PARAMS.items():
            if not self.config.has_section(section):
                self.config.add_section(section)
                changed = True
            for key, value in values.items():
                if not self.config.has_option(section, key):
                    self.config.set(section, key, str(value))
                    changed = True
                    continue

                # 首次升级到本次上传的新头部/尾部管道时，自动把旧默认值换成新默认值。
                # 用户改过的非旧默认值会保留，避免覆盖现场调参结果。
                if need_migrate:
                    old_defaults = self.PREVIOUS_DEFAULT_PARAMS.get(section, {})
                    old_default = old_defaults.get(key)
                    current_value = self.config.get(section, key)
                    if old_default is not None and self.values_equal(current_value, old_default):
                        self.config.set(section, key, str(value))
                        changed = True

        if need_migrate:
            self.config.set("Meta", "pipeline_param_version", self.PARAM_VERSION)
            changed = True

        if changed:
            self.save()

    def reset_defaults(self) -> None:
        self.config = configparser.ConfigParser()
        self.config.add_section("Meta")
        self.config.set("Meta", "pipeline_param_version", self.PARAM_VERSION)
        for section, values in self.DEFAULT_PARAMS.items():
            self.config.add_section(section)
            for key, value in values.items():
                self.config.set(section, key, str(value))
        self.save()

    def get_params(self, section: str) -> Dict[str, str]:
        self.ensure_default_params()
        values = dict(self.DEFAULT_PARAMS.get(section, {}))
        if self.config.has_section(section):
            for key, _ in self.config.items(section):
                values[key] = self.config.get(section, key)
        return values

    def set_params(self, section: str, values: Dict[str, object]) -> None:
        if not self.config.has_section(section):
            self.config.add_section(section)

        defaults = self.DEFAULT_PARAMS.get(section, {})
        for key in defaults.keys():
            if key in values:
                self.config.set(section, key, self.format_value(values[key]))

        self.save()

    @staticmethod
    def format_value(value: object) -> str:
        try:
            number = float(value)
            if number.is_integer():
                return str(int(number))
            text = ("%.6f" % number).rstrip("0").rstrip(".")
            return text or "0"
        except Exception:
            return str(value)

    # ------------------------------------------------------------------
    # 模板与生成
    # ------------------------------------------------------------------

    def get_active_sections(self) -> List[str]:
        """返回当前项目需要处理的管道类型。

        头部/尾部是本软件蛋白分析的必需管道；质控管道是可选项。
        这样项目里只有 pipeline_head.cppipe 和 pipeline_tail.cppipe 时，
        管道参数保存、检查、生成不会被缺失的 pipeline_qc.cppipe 阻断。
        """
        sections = list(self.REQUIRED_SECTIONS)
        for section in self.OPTIONAL_SECTIONS:
            target = self.TARGETS.get(section)
            if not target:
                continue
            template_path = self.templates_dir / target.template_name
            output_path = self.pipelines_dir / target.output_name
            if template_path.exists() or output_path.exists():
                sections.append(section)
        return sections

    def ensure_templates(self) -> List[str]:
        """确保 templates 目录存在。首次使用时从当前 pipelines/*.cppipe 复制母版。

        头部和尾部管道必须存在；质控管道可选。
        如果更换了新的头部/尾部管道，建议同时把新管道复制到
        pipelines/templates 目录作为母版，否则“保存并应用参数”仍会基于旧母版生成。
        """
        self.pipelines_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)

        messages: List[str] = []

        for section, target in self.TARGETS.items():
            template_path = self.templates_dir / target.template_name
            output_path = self.pipelines_dir / target.output_name
            required = section in self.REQUIRED_SECTIONS

            if template_path.exists():
                continue

            if not output_path.exists():
                if required:
                    messages.append(f"× 缺少 {target.title}，无法创建模板：{output_path}")
                else:
                    messages.append(f"○ 未配置{target.title}，已跳过：{output_path}")
                continue

            shutil.copy2(output_path, template_path)
            messages.append(f"√ 已创建模板：{template_path}")

        return messages

    def generate_all_pipelines(self) -> List[str]:
        """根据当前参数生成所有实际运行管道。

        写入前会先完整渲染并校验三条管道；只要其中任何一个参数定位失败，
        就不会覆盖现有 pipelines/*.cppipe。
        """
        messages: List[str] = []
        records: List[ReplacementRecord] = []

        messages.extend(self.ensure_templates())
        self.ensure_default_params()

        rendered: Dict[str, str] = {}

        # 先全部渲染，确保无错误后再覆盖，避免部分成功、部分失败。
        active_sections = self.get_active_sections()

        for section in active_sections:
            text, section_records = self.render_pipeline(section)
            rendered[section] = text
            records.extend(section_records)

        backup_dir = self.backup_existing_pipelines()
        if backup_dir:
            messages.append(f"√ 已备份当前管道：{backup_dir}")

        for section in active_sections:
            target = self.TARGETS[section]
            output_path = self.pipelines_dir / target.output_name
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered[section], encoding="utf-8")
            messages.append(f"√ 已生成{target.title}：{output_path}")

        report_path = self.write_generate_report(records, backup_dir)
        messages.append(f"√ 已生成参数写入报告：{report_path}")

        return messages

    def generate_pipeline(self, section: str) -> str:
        """生成单条管道。保留这个方法，兼容后续可能单独生成的调用。"""
        messages = self.ensure_templates()
        self.ensure_default_params()

        text, records = self.render_pipeline(section)

        target = self.TARGETS[section]
        output_path = self.pipelines_dir / target.output_name

        backup_dir = self.backup_existing_pipelines([section])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")

        self.write_generate_report(records, backup_dir)

        prefix = "；".join(messages) + "；" if messages else ""
        return f"{prefix}√ 已生成{target.title}：{output_path}"

    def render_pipeline(self, section: str) -> Tuple[str, List[ReplacementRecord]]:
        if section not in self.TARGETS:
            raise ValueError(f"未知管道类型：{section}")

        target = self.TARGETS[section]
        template_path = self.templates_dir / target.template_name

        if not template_path.exists():
            raise FileNotFoundError(f"缺少模板管道：{template_path}")

        text = template_path.read_text(encoding="utf-8", errors="replace")
        params = self.get_params(section)
        records: List[ReplacementRecord] = []

        for module_num, setting_name, param_key, measurement_name in self.PARAM_RULES.get(section, []):
            if param_key not in params:
                continue

            new_text, old_value = self.replace_module_setting(
                text=text,
                module_num=module_num,
                setting_name=setting_name,
                value=params[param_key],
                measurement_name=measurement_name,
            )
            text = new_text

            records.append(
                ReplacementRecord(
                    section=section,
                    module_num=module_num,
                    setting_name=setting_name,
                    param_key=param_key,
                    old_value=old_value,
                    new_value=self.format_value(params[param_key]),
                    measurement_name=measurement_name,
                )
            )

        return text, records

    def backup_existing_pipelines(self, sections: Optional[List[str]] = None) -> Optional[Path]:
        sections = sections or self.get_active_sections()

        existing = []
        for section in sections:
            target = self.TARGETS.get(section)
            if not target:
                continue
            output_path = self.pipelines_dir / target.output_name
            if output_path.exists():
                existing.append(output_path)

        if not existing:
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self.backups_dir / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)

        for path in existing:
            shutil.copy2(path, backup_dir / path.name)

        return backup_dir

    def write_generate_report(
        self,
        records: List[ReplacementRecord],
        backup_dir: Optional[Path],
    ) -> Path:
        report_path = self.pipelines_dir / "last_generate_report.txt"

        lines: List[str] = []
        lines.append("管道参数生成报告")
        lines.append("=" * 72)
        lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"项目目录：{self.project_root}")
        lines.append(f"参数文件：{self.param_file}")
        lines.append(f"模板目录：{self.templates_dir}")
        if backup_dir:
            lines.append(f"备份目录：{backup_dir}")
        else:
            lines.append("备份目录：无旧管道可备份")
        lines.append("")

        section_titles = {
            "Head": "头部蛋白管道",
            "Tail": "尾部蛋白管道",
            "QC": "质控微球管道",
        }

        for section in self.get_active_sections():
            lines.append(section_titles.get(section, section))
            lines.append("-" * 72)
            section_records = [r for r in records if r.section == section]
            if not section_records:
                lines.append("无参数写入记录。")
            else:
                for r in section_records:
                    measurement_text = f" | {r.measurement_name}" if r.measurement_name else ""
                    lines.append(
                        f"module_num:{r.module_num}{measurement_text} | {r.setting_name} | "
                        f"{r.param_key} | {r.old_value} -> {r.new_value}"
                    )
            lines.append("")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        return report_path

    @classmethod
    def replace_module_setting(
        cls,
        text: str,
        module_num: int,
        setting_name: str,
        value: object,
        measurement_name: str = "",
    ) -> Tuple[str, str]:
        start, end = cls.find_module_block(text, module_num)
        if start < 0:
            raise ValueError(f"未找到 module_num:{module_num}")

        block = text[start:end]
        if measurement_name:
            new_block, count, old_value = cls.replace_setting_line_after_measurement(
                block=block,
                measurement_name=measurement_name,
                setting_name=setting_name,
                value=cls.format_value(value),
            )
        else:
            new_block, count, old_value = cls.replace_setting_line(
                block=block,
                setting_name=setting_name,
                value=cls.format_value(value),
            )

        if count <= 0:
            if measurement_name:
                raise ValueError(
                    f"module_num:{module_num} 中未找到测量值 {measurement_name} 的参数：{setting_name}"
                )
            raise ValueError(f"module_num:{module_num} 中未找到参数：{setting_name}")

        return text[:start] + new_block + text[end:], old_value

    @staticmethod
    def find_module_block(text: str, module_num: int) -> Tuple[int, int]:
        pattern = re.compile(r"^[^\n\r:]+:\[module_num:%s\|" % re.escape(str(module_num)), re.M)
        match = pattern.search(text)
        if not match:
            return -1, -1

        start = match.start()
        next_pattern = re.compile(r"^[^\n\r:]+:\[module_num:\d+\|", re.M)
        next_match = next_pattern.search(text, match.end())
        end = next_match.start() if next_match else len(text)
        return start, end

    @staticmethod
    def replace_setting_line(block: str, setting_name: str, value: str) -> Tuple[str, int, str]:
        # .cppipe 参数行通常是：四个空格 + 参数名:值
        # 这里保留原缩进，只替换冒号后的内容。
        pattern = re.compile(r"^(\s*%s\s*:)\s*(.*?)\s*$" % re.escape(setting_name), re.M)

        old_value_holder = {"value": ""}

        def repl(match):
            old_value_holder["value"] = str(match.group(2) or "").strip()
            return f"{match.group(1)}{value}"

        new_block, count = pattern.subn(repl, block, count=1)
        return new_block, count, old_value_holder["value"]

    @classmethod
    def replace_setting_line_after_measurement(
        cls,
        block: str,
        measurement_name: str,
        setting_name: str,
        value: str,
    ) -> Tuple[str, int, str]:
        """替换指定测量值下面的参数行。

        FilterObjects 模块中常见结构：
            选择用于筛选的测量值:AreaShape_FormFactor
            最小值:0.2
            最大值:1.0
            选择用于筛选的测量值:AreaShape_EquivalentDiameter
            最小值:0.0
            最大值:70

        因此红色等效直径上限必须定位到 AreaShape_EquivalentDiameter 后面的“最大值”，
        不能直接替换模块中的第一个“最大值”。
        """
        lines = block.splitlines(keepends=True)
        measurement_index = -1
        for index, line in enumerate(lines):
            if measurement_name and measurement_name in line:
                measurement_index = index
                break

        if measurement_index < 0:
            return block, 0, ""

        end_index = len(lines)
        for index in range(measurement_index + 1, len(lines)):
            if cls.is_measurement_selector_line(lines[index]):
                end_index = index
                break

        pattern = re.compile(r"^(\s*%s\s*:)\s*(.*?)(\r?\n?)$" % re.escape(setting_name))
        for index in range(measurement_index + 1, end_index):
            match = pattern.match(lines[index])
            if not match:
                continue
            old_value = str(match.group(2) or "").strip()
            lines[index] = f"{match.group(1)}{value}{match.group(3)}"
            return "".join(lines), 1, old_value

        return block, 0, ""

    @staticmethod
    def is_measurement_selector_line(line: str) -> bool:
        text = str(line or "").strip()
        return (
            text.startswith("选择用于筛选的测量值:")
            or text.startswith("Measurement:")
            or text.startswith("Select the measurement")
        )



    # ------------------------------------------------------------------
    # 模板校验
    # ------------------------------------------------------------------

    def validate_template_rules(self) -> List[str]:
        """校验 templates 中的母版管道是否还能匹配当前参数规则。

        用途：
        1. 更换 .cppipe 后，先检查 module_num 和参数名有没有变化；
        2. 避免点击“生成管道”时才发现参数定位失败；
        3. 给用户一个可读的检查结果。
        """
        messages: List[str] = []
        self.ensure_default_params()
        messages.extend(self.ensure_templates())

        all_ok = True

        for section in self.get_active_sections():
            target = self.TARGETS[section]
            template_path = self.templates_dir / target.template_name

            messages.append("")
            messages.append(f"{target.title}：{template_path}")

            if not template_path.exists():
                all_ok = False
                messages.append(f"× 模板不存在：{template_path}")
                continue

            text = template_path.read_text(encoding="utf-8", errors="replace")
            params = self.get_params(section)

            for module_num, setting_name, param_key, measurement_name in self.PARAM_RULES.get(section, []):
                try:
                    start, end = self.find_module_block(text, module_num)
                    if start < 0:
                        raise ValueError(f"未找到 module_num:{module_num}")

                    block = text[start:end]
                    old_value = self.find_setting_value(block, setting_name, measurement_name)
                    if old_value is None:
                        if measurement_name:
                            raise ValueError(f"module_num:{module_num} 中未找到测量值 {measurement_name} 的参数：{setting_name}")
                        raise ValueError(f"module_num:{module_num} 中未找到参数：{setting_name}")

                    current_param = self.format_value(params.get(param_key, ""))
                    measurement_text = f" | {measurement_name}" if measurement_name else ""
                    messages.append(
                        f"√ module_num:{module_num}{measurement_text} | {setting_name} | "
                        f"当前模板值:{old_value} | 参数文件值:{current_param}"
                    )
                except Exception as e:
                    all_ok = False
                    messages.append(f"× {target.title} | {param_key} | {e}")

        messages.insert(0, "√ 管道模板参数检查通过。" if all_ok else "× 管道模板参数检查未通过。")
        return messages

    @classmethod
    def find_setting_value(cls, block: str, setting_name: str, measurement_name: str = "") -> Optional[str]:
        if measurement_name:
            return cls.find_setting_value_after_measurement(block, measurement_name, setting_name)

        pattern = re.compile(r"^\s*%s\s*:\s*(.*?)\s*$" % re.escape(setting_name), re.M)
        match = pattern.search(block)
        if not match:
            return None
        return str(match.group(1) or "").strip()

    @classmethod
    def find_setting_value_after_measurement(
        cls,
        block: str,
        measurement_name: str,
        setting_name: str,
    ) -> Optional[str]:
        lines = block.splitlines(keepends=False)
        measurement_index = -1
        for index, line in enumerate(lines):
            if measurement_name and measurement_name in line:
                measurement_index = index
                break

        if measurement_index < 0:
            return None

        end_index = len(lines)
        for index in range(measurement_index + 1, len(lines)):
            if cls.is_measurement_selector_line(lines[index]):
                end_index = index
                break

        pattern = re.compile(r"^\s*%s\s*:\s*(.*?)\s*$" % re.escape(setting_name))
        for index in range(measurement_index + 1, end_index):
            match = pattern.match(lines[index])
            if match:
                return str(match.group(1) or "").strip()
        return None



    # ------------------------------------------------------------------
    # 生效状态检查
    # ------------------------------------------------------------------

    def check_generated_pipeline_status(self) -> List[str]:
        """检查 pipeline_params.ini 中的参数是否已经写入实际运行管道。

        这个检查针对 pipelines\\pipeline_*.cppipe，而不是 templates。
        用途：
        1. 用户修改并保存参数后，确认是否已经点击“生成管道”；
        2. 避免参数文件改了，但实际运行管道仍然是旧参数；
        3. 交付前确认当前 .cppipe 与参数文件一致。
        """
        messages: List[str] = []
        self.ensure_default_params()

        all_ok = True

        for section in self.get_active_sections():
            target = self.TARGETS[section]
            output_path = self.pipelines_dir / target.output_name

            messages.append("")
            messages.append(f"{target.title}：{output_path}")

            if not output_path.exists() or not output_path.is_file():
                all_ok = False
                messages.append(f"× 实际运行管道不存在：{output_path}")
                continue

            text = output_path.read_text(encoding="utf-8", errors="replace")
            params = self.get_params(section)

            for module_num, setting_name, param_key, measurement_name in self.PARAM_RULES.get(section, []):
                expected_value = self.format_value(params.get(param_key, ""))
                try:
                    start, end = self.find_module_block(text, module_num)
                    if start < 0:
                        raise ValueError(f"未找到 module_num:{module_num}")

                    block = text[start:end]
                    actual_value = self.find_setting_value(block, setting_name, measurement_name)
                    if actual_value is None:
                        if measurement_name:
                            raise ValueError(f"module_num:{module_num} 中未找到测量值 {measurement_name} 的参数：{setting_name}")
                        raise ValueError(f"module_num:{module_num} 中未找到参数：{setting_name}")

                    measurement_text = f" | {measurement_name}" if measurement_name else ""
                    if self.values_equal(actual_value, expected_value):
                        messages.append(
                            f"√ module_num:{module_num}{measurement_text} | {setting_name} | "
                            f"{param_key} | 已生效:{actual_value}"
                        )
                    else:
                        all_ok = False
                        messages.append(
                            f"× module_num:{module_num}{measurement_text} | {setting_name} | "
                            f"{param_key} | 管道当前值:{actual_value} | 参数文件值:{expected_value}"
                        )
                except Exception as e:
                    all_ok = False
                    messages.append(f"× {target.title} | {param_key} | {e}")

        messages.insert(0, "√ 实际运行管道与 pipeline_params.ini 一致。" if all_ok else "× 实际运行管道与 pipeline_params.ini 不一致。")
        return messages

    @staticmethod
    def values_equal(a: object, b: object) -> bool:
        """比较 .cppipe 中的值和参数文件值。

        优先按数字比较，避免 65 和 65.0 被判断为不同。
        如果不能转换为数字，再按字符串比较。
        """
        a_text = str(a or "").strip()
        b_text = str(b or "").strip()

        try:
            return abs(float(a_text) - float(b_text)) < 1e-9
        except Exception:
            return a_text == b_text


    # ------------------------------------------------------------------
    # 备份恢复
    # ------------------------------------------------------------------

    def list_backup_dirs(self) -> List[Path]:
        """返回所有管道备份目录，按时间倒序排列。"""
        if not self.backups_dir.exists():
            return []

        dirs = [p for p in self.backups_dir.iterdir() if p.is_dir()]
        dirs.sort(key=lambda p: p.name, reverse=True)
        return dirs

    def get_latest_backup_dir(self) -> Optional[Path]:
        """返回最近一次备份目录。"""
        dirs = self.list_backup_dirs()
        return dirs[0] if dirs else None

    def restore_latest_backup(self) -> List[str]:
        """恢复最近一次管道备份。

        恢复前会先把当前 pipelines/*.cppipe 再备份一次，避免误恢复后无法回退。
        """
        latest = self.get_latest_backup_dir()
        if not latest:
            raise FileNotFoundError("没有找到任何管道备份。")

        return self.restore_backup(latest)

    def restore_backup(self, backup_dir: Path) -> List[str]:
        """从指定备份目录恢复管道。"""
        backup_dir = Path(backup_dir)

        if not backup_dir.exists() or not backup_dir.is_dir():
            raise FileNotFoundError(f"备份目录不存在：{backup_dir}")

        messages: List[str] = []

        # 恢复前先备份当前管道
        current_backup = self.backup_existing_pipelines()
        if current_backup:
            messages.append(f"√ 恢复前已备份当前管道：{current_backup}")

        restored_count = 0

        for section in self.get_active_sections():
            target = self.TARGETS[section]
            backup_file = backup_dir / target.output_name
            output_file = self.pipelines_dir / target.output_name

            if not backup_file.exists():
                messages.append(f"× 备份中缺少{target.title}：{backup_file}")
                continue

            output_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_file, output_file)
            restored_count += 1
            messages.append(f"√ 已恢复{target.title}：{output_file}")

        if restored_count <= 0:
            raise RuntimeError(f"备份目录中没有可恢复的管道文件：{backup_dir}")

        report_path = self.pipelines_dir / "last_restore_report.txt"
        lines = [
            "管道备份恢复报告",
            "=" * 72,
            f"恢复时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"项目目录：{self.project_root}",
            f"恢复来源：{backup_dir}",
            f"恢复前备份：{current_backup if current_backup else '无'}",
            "",
        ]
        lines.extend(messages)
        report_path.write_text("\n".join(lines), encoding="utf-8")
        messages.append(f"√ 已生成恢复报告：{report_path}")

        return messages


    # ------------------------------------------------------------------
    # 便捷路径
    # ------------------------------------------------------------------

    def get_param_file(self) -> Path:
        return self.param_file

    def get_templates_dir(self) -> Path:
        return self.templates_dir

    def get_pipelines_dir(self) -> Path:
        return self.pipelines_dir

    def get_backups_dir(self) -> Path:
        return self.backups_dir
