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
            "red_diameter": "65",
            "red_flow_threshold": "0.5",
            "red_cellprob_threshold": "0",
            "red_min_size": "10",
            "red_formfactor_min": "0.2",
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
            "red_search_radius": "50",
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

    # 每个参数精确定位：section -> [(module_num, setting_name, param_key), ...]
    # 注意：同一个管道里可能有多个“最小值”，所以必须通过 module_num + 参数名精确定位。
    PARAM_RULES: Dict[str, List[Tuple[int, str, str]]] = {
        "Head": [
            (7, "预期物体直径", "red_diameter"),
            (7, "流阈值", "red_flow_threshold"),
            (7, "细胞概率阈值", "red_cellprob_threshold"),
            (7, "最小尺寸", "red_min_size"),
            (9, "最小值", "red_formfactor_min"),
            (11, "扩张或收缩的像素数", "green_expand_pixels"),
            (14, "最小值", "green_intensity_min"),
        ],
        "Tail": [
            (7, "Expected object diameter", "green_diameter"),
            (7, "Flow threshold", "green_flow_threshold"),
            (7, "Distance field threshold", "green_distance_threshold"),
            (7, "Minimum size", "green_min_size"),
            (9, "最小值", "green_eccentricity_min"),
            (11, "预期物体直径", "red_diameter"),
            (11, "流阈值", "red_flow_threshold"),
            (11, "细胞概率阈值", "red_cellprob_threshold"),
            (11, "最小尺寸", "red_min_size"),
            (13, "最小值", "red_formfactor_min"),
            (15, "扩张或收缩的像素数", "red_search_radius"),
            (17, "最小值", "colocalized_child_count_min"),
        ],
        "QC": [
            (6, "预期物体直径", "bead_diameter"),
            (6, "流阈值", "bead_flow_threshold"),
            (6, "细胞概率阈值", "bead_cellprob_threshold"),
            (6, "最小尺寸", "bead_min_size"),
            (8, "最小值", "bead_formfactor_min"),
        ],
    }

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
        for section, values in self.DEFAULT_PARAMS.items():
            if not self.config.has_section(section):
                self.config.add_section(section)
                changed = True
            for key, value in values.items():
                if not self.config.has_option(section, key):
                    self.config.set(section, key, str(value))
                    changed = True
        if changed:
            self.save()

    def reset_defaults(self) -> None:
        self.config = configparser.ConfigParser()
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

    def ensure_templates(self) -> List[str]:
        """确保 templates 目录存在。首次使用时从当前 pipelines/*.cppipe 复制母版。"""
        self.pipelines_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)

        messages: List[str] = []

        for target in self.TARGETS.values():
            template_path = self.templates_dir / target.template_name
            output_path = self.pipelines_dir / target.output_name

            if template_path.exists():
                continue

            if not output_path.exists():
                messages.append(f"× 缺少 {target.title}，无法创建模板：{output_path}")
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
        for section in ["Head", "Tail", "QC"]:
            text, section_records = self.render_pipeline(section)
            rendered[section] = text
            records.extend(section_records)

        backup_dir = self.backup_existing_pipelines()
        if backup_dir:
            messages.append(f"√ 已备份当前管道：{backup_dir}")

        for section in ["Head", "Tail", "QC"]:
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

        for module_num, setting_name, param_key in self.PARAM_RULES.get(section, []):
            if param_key not in params:
                continue

            new_text, old_value = self.replace_module_setting(
                text=text,
                module_num=module_num,
                setting_name=setting_name,
                value=params[param_key],
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
                )
            )

        return text, records

    def backup_existing_pipelines(self, sections: Optional[List[str]] = None) -> Optional[Path]:
        sections = sections or ["Head", "Tail", "QC"]

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

        for section in ["Head", "Tail", "QC"]:
            lines.append(section_titles.get(section, section))
            lines.append("-" * 72)
            section_records = [r for r in records if r.section == section]
            if not section_records:
                lines.append("无参数写入记录。")
            else:
                for r in section_records:
                    lines.append(
                        f"module_num:{r.module_num} | {r.setting_name} | "
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
    ) -> Tuple[str, str]:
        start, end = cls.find_module_block(text, module_num)
        if start < 0:
            raise ValueError(f"未找到 module_num:{module_num}")

        block = text[start:end]
        new_block, count, old_value = cls.replace_setting_line(
            block=block,
            setting_name=setting_name,
            value=cls.format_value(value),
        )

        if count <= 0:
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

        for section in ["Head", "Tail", "QC"]:
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

            for module_num, setting_name, param_key in self.PARAM_RULES.get(section, []):
                try:
                    start, end = self.find_module_block(text, module_num)
                    if start < 0:
                        raise ValueError(f"未找到 module_num:{module_num}")

                    block = text[start:end]
                    old_value = self.find_setting_value(block, setting_name)
                    if old_value is None:
                        raise ValueError(f"module_num:{module_num} 中未找到参数：{setting_name}")

                    current_param = self.format_value(params.get(param_key, ""))
                    messages.append(
                        f"√ module_num:{module_num} | {setting_name} | "
                        f"当前模板值:{old_value} | 参数文件值:{current_param}"
                    )
                except Exception as e:
                    all_ok = False
                    messages.append(f"× {target.title} | {param_key} | {e}")

        messages.insert(0, "√ 管道模板参数检查通过。" if all_ok else "× 管道模板参数检查未通过。")
        return messages

    @staticmethod
    def find_setting_value(block: str, setting_name: str) -> Optional[str]:
        pattern = re.compile(r"^\s*%s\s*:\s*(.*?)\s*$" % re.escape(setting_name), re.M)
        match = pattern.search(block)
        if not match:
            return None
        return str(match.group(1) or "").strip()



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

        for section in ["Head", "Tail", "QC"]:
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

            for module_num, setting_name, param_key in self.PARAM_RULES.get(section, []):
                expected_value = self.format_value(params.get(param_key, ""))
                try:
                    start, end = self.find_module_block(text, module_num)
                    if start < 0:
                        raise ValueError(f"未找到 module_num:{module_num}")

                    block = text[start:end]
                    actual_value = self.find_setting_value(block, setting_name)
                    if actual_value is None:
                        raise ValueError(f"module_num:{module_num} 中未找到参数：{setting_name}")

                    if self.values_equal(actual_value, expected_value):
                        messages.append(
                            f"√ module_num:{module_num} | {setting_name} | "
                            f"{param_key} | 已生效:{actual_value}"
                        )
                    else:
                        all_ok = False
                        messages.append(
                            f"× module_num:{module_num} | {setting_name} | "
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

        for section in ["Head", "Tail", "QC"]:
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
