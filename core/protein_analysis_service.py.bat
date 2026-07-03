# -*- coding: utf-8 -*-
"""
统一单蛋白分析服务。

职责：
1. 根据病例 + protein_key + 源图片文件夹，完成一次标准单蛋白分析。
2. 统一 raw_images / cp_input / cp_output 的目录清理与准备。
3. 统一使用 ImageChannelMatcher 识别 R/G/DIC/Merge 与视野编号。
4. raw_images 保留原始文件名。
5. cp_input 也保留原始文件名，只复制参与分析的 G/R 图。
6. 统一调用 MvImageIDRunner。
5. 统一解析 Image.csv / colocalized CSV。

说明：
- 本服务不直接写数据库，返回的 result dict 与当前界面保存逻辑兼容。
- 批量分析和单蛋白分析都应该调用本服务。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core.config_manager import ConfigManager
from core.image_channel_matcher import ImageChannelMatcher, FieldImageSet, FolderMatchResult
from core.mvimageid_runner import MvImageIDRunner, MvImageIDRunResult
from core.result_parser import ResultParser

LogCallback = Optional[Callable[[str], None]]
CancelCallback = Optional[Callable[[], bool]]


class ProteinAnalysisService:
    """统一执行“一个病例的一个蛋白”的完整分析流程。"""

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or ConfigManager()
        self.config.ensure_default_config()
        self.matcher = ImageChannelMatcher(self.config.get_image_rule())

    # ------------------------------------------------------------------
    # 对外主入口
    # ------------------------------------------------------------------
    def run_one_protein(
        self,
        case_data: dict,
        protein_key: str,
        source_folder: str,
        protein_name: str = "",
        overwrite: bool = True,
        reuse_existing_raw: bool = False,
        log_callback: LogCallback = None,
        cancel_callback: CancelCallback = None,
    ) -> dict:
        """
        执行单个蛋白分析。

        返回 dict，字段兼容当前 batch_analysis_dialog / analysis_window 保存逻辑。
        """
        case_no = str((case_data or {}).get("case_no", "") or "").strip()
        case_id = (case_data or {}).get("id")
        if not case_no:
            raise RuntimeError("当前病例编号为空。")

        protein_key = str(protein_key or "").strip()
        if not protein_key:
            raise RuntimeError("蛋白内部编号为空。")

        protein_name = str(protein_name or "").strip() or self.config.get_protein_display_name(protein_key)
        protein_part = self.config.get_protein_part(protein_key)

        source_folder_path: Optional[Path] = None
        if not reuse_existing_raw:
            source_folder_path = Path(str(source_folder or "")).resolve()
            if not source_folder_path.exists():
                raise FileNotFoundError(f"源图片文件夹不存在：{source_folder_path}")
            if not source_folder_path.is_dir():
                raise NotADirectoryError(f"源图片路径不是文件夹：{source_folder_path}")

        workspace_root = Path(self.config.get_workspace_root())
        raw_folder = (workspace_root / case_no / "raw_images" / protein_key).resolve()
        cp_input_dir = (workspace_root / case_no / "cp_input" / protein_key).resolve()
        cp_output_dir = (workspace_root / case_no / "cp_output" / protein_key).resolve()

        if source_folder_path is not None:
            self._log(log_callback, f"{protein_name} 源图片目录：{source_folder_path}")
        else:
            self._log(log_callback, f"{protein_name} 使用已有原始导入目录，不重新复制原始图片。")
        self._log(log_callback, f"{protein_name} 原始导入目录：{raw_folder}")
        self._log(log_callback, f"{protein_name} 分析输入目录：{cp_input_dir}")
        self._log(log_callback, f"{protein_name} 分析输出目录：{cp_output_dir}")

        if not overwrite:
            self._assert_folder_not_existing(raw_folder, "原始导入目录")
            self._assert_folder_not_existing(cp_input_dir, "分析输入目录")
            self._assert_folder_not_existing(cp_output_dir, "分析输出目录")

        if reuse_existing_raw:
            imported_images = self.load_images_from_raw_folder(
                raw_folder=raw_folder,
                protein_key=protein_key,
                protein_name=protein_name,
                log_callback=log_callback,
            )
        else:
            imported_images = self.import_images_to_raw_folder(
                source_folder=source_folder_path,
                raw_folder=raw_folder,
                protein_key=protein_key,
                protein_name=protein_name,
                log_callback=log_callback,
            )

        complete_items = [item for item in imported_images if item.get("status") == "完整"]
        if not complete_items:
            raise RuntimeError("没有完整的 R/G 视野，无法运行分析。")

        copied_count = self.prepare_input_folder(
            complete_items=complete_items,
            cp_input_dir=cp_input_dir,
            protein_name=protein_name,
            log_callback=log_callback,
        )
        if copied_count <= 0:
            raise RuntimeError("没有复制任何 R/G 图像到分析输入目录。")

        self.prepare_output_folder(cp_output_dir, protein_name, log_callback)

        runner_result = self.run_mvimageid(
            protein_key=protein_key,
            protein_name=protein_name,
            cp_input_dir=cp_input_dir,
            cp_output_dir=cp_output_dir,
            log_callback=log_callback,
            cancel_callback=cancel_callback,
        )

        parsed_result = self.parse_result(cp_output_dir)
        total = parsed_result.get("total", {})
        rows = parsed_result.get("rows", [])
        image_csv = parsed_result.get("image_csv", "")

        return {
            "case_id": case_id,
            "case_no": case_no,
            "protein_key": protein_key,
            "protein_name": protein_name,
            "protein_part": protein_part,
            "image_folder": str(raw_folder),
            "input_folder": str(cp_input_dir),
            "output_folder": str(cp_output_dir),
            "total": total,
            "rows": rows,
            "image_csv": image_csv,
            "imported_images": imported_images,
            "complete_count": len(complete_items),
            "copied_input_count": copied_count,
            "runner_success": runner_result.success,
            "runner_elapsed_seconds": runner_result.elapsed_seconds,
            "runner_return_code": runner_result.return_code,
            "runner_log_file": str(runner_result.log_file or ""),
            "runner_command_file": str(runner_result.command_file or ""),
        }

    # ------------------------------------------------------------------
    # 图片识别与导入
    # ------------------------------------------------------------------
    def import_images_to_raw_folder(
        self,
        source_folder: Path,
        raw_folder: Path,
        protein_key: str,
        protein_name: str,
        log_callback: LogCallback = None,
    ) -> List[dict]:
        """
        清空 raw_images/proteinX 并重新导入原始图片。

        统一调用 ImageChannelMatcher，
        保证批量预检查、单蛋白导入、分析输入准备使用同一套通道规则。
        """
        match_result = self.matcher.scan_folder(source_folder)
        if match_result.total_fields <= 0:
            raise RuntimeError(f"未在源图片文件夹中识别到 R/G/DIC/Merge 图像：{source_folder}")

        if raw_folder.exists():
            shutil.rmtree(raw_folder)
        raw_folder.mkdir(parents=True, exist_ok=True)

        copied_items: List[dict] = []
        for field_set in match_result.fields:
            copied_item = self._copy_field_set_to_raw_folder(
                field_set=field_set,
                raw_folder=raw_folder,
                protein_key=protein_key,
            )
            copied_items.append(copied_item)

        complete_count = len([item for item in copied_items if item.get("status") == "完整"])
        self._log(
            log_callback,
            f"{protein_name} 导入完成：共 {len(copied_items)} 个视野，完整视野 {complete_count} 个。",
        )

        if match_result.unmatched_files:
            self._log(log_callback, f"{protein_name} 未识别图片：{len(match_result.unmatched_files)} 张。")
        if any(item.get("status") != "完整" for item in copied_items):
            bad = [f"{item.get('field_no')}({item.get('status')})" for item in copied_items if item.get("status") != "完整"]
            self._log(log_callback, f"{protein_name} 不完整/异常视野：" + "，".join(bad))

        return copied_items

    def load_images_from_raw_folder(
        self,
        raw_folder: Path,
        protein_key: str,
        protein_name: str,
        log_callback: LogCallback = None,
    ) -> List[dict]:
        """
        读取已经导入到 raw_images/proteinX 的图片，不清空、不复制原始图。

        这里同样统一使用 ImageChannelMatcher，避免历史加载和新导入规则不一致。
        """
        if not raw_folder.exists() or not raw_folder.is_dir():
            raise FileNotFoundError(f"原始导入目录不存在：{raw_folder}")

        match_result = self.matcher.scan_folder(raw_folder)
        imported_images = self._match_result_to_rows(match_result, protein_key=protein_key)

        complete_count = len([item for item in imported_images if item.get("status") == "完整"])
        self._log(
            log_callback,
            f"{protein_name} 已读取原始导入图片：共 {len(imported_images)} 个视野，完整视野 {complete_count} 个。",
        )
        return imported_images

    def _copy_field_set_to_raw_folder(
        self,
        field_set: FieldImageSet,
        raw_folder: Path,
        protein_key: str,
    ) -> dict:

        field_no = self._normalize_field_no(field_set.field_id, protein_key)
        copied_item = self._empty_row(field_no)
        copied_item["status"] = self._field_status_for_ui(field_set)

        for channel in ["G", "R", "DIC", "Merge"]:
            source_path = field_set.get(channel)
            if not source_path:
                continue
            source = Path(source_path)
            target = raw_folder / source.name
            shutil.copy2(source, target)
            copied_item[channel] = str(target)

        # 重复通道也复制到 raw_images，便于追溯；但不会作为可分析视野进入 cp_input。
        for duplicate_list in field_set.duplicates.values():
            for duplicate_path in duplicate_list:
                duplicate_source = Path(duplicate_path)
                duplicate_target = raw_folder / duplicate_source.name
                if not duplicate_target.exists():
                    shutil.copy2(duplicate_source, duplicate_target)

        return copied_item

    def _match_result_to_rows(self, match_result: FolderMatchResult, protein_key: str) -> List[dict]:
        rows = []
        for field_set in match_result.fields:
            field_no = self._normalize_field_no(field_set.field_id, protein_key)
            row = self._empty_row(field_no)
            row["status"] = self._field_status_for_ui(field_set)
            for channel in ["G", "R", "DIC", "Merge"]:
                path = field_set.get(channel)
                row[channel] = str(path) if path else ""
            rows.append(row)
        rows.sort(key=lambda item: self._natural_key(str(item.get("field_no", ""))))
        return rows

    @staticmethod
    def _empty_row(field_no: str) -> dict:
        return {
            "field_no": field_no,
            "R": "",
            "G": "",
            "DIC": "",
            "Merge": "",
            "status": "未完整",
        }

    @staticmethod
    def _field_status_for_ui(field_set: FieldImageSet) -> str:
        if field_set.is_complete:
            return "完整"
        return field_set.status_text()

    @staticmethod
    def _normalize_field_no(field_id: str, protein_key: str) -> str:
        field_no = str(field_id or "").strip()
        prefix = f"{protein_key}_"
        if field_no.startswith(prefix):
            field_no = field_no[len(prefix):]
        field_no = field_no.strip("_- ")
        return field_no or str(field_id or "")

    @staticmethod
    def _natural_key(value: str) -> List[object]:
        import re

        return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(value))]

    # ------------------------------------------------------------------
    # 分析输入 / 输出
    # ------------------------------------------------------------------
    def prepare_input_folder(
        self,
        complete_items: List[dict],
        cp_input_dir: Path,
        protein_name: str,
        log_callback: LogCallback = None,
    ) -> int:
        """清空 cp_input/proteinX，并复制本次分析实际需要的 R/G 图。

        设计原则：
        - raw_images 保留原始文件名，用于原始数据追溯。
        - cp_input 也保留原始文件名，只筛选出本次真正参与分析的 G/R 图。
        - 不再额外添加 proteinX_ 前缀，因为 proteinX 已经体现在目录层级中。

        这样 cp_output 中的叠加图文件名会自然对应用户原始图片名，
        例如 bb50-2_G.tif → bb50-2_G_G_objects_OrigOverlay.png。
        """
        if cp_input_dir.exists():
            shutil.rmtree(cp_input_dir)
        cp_input_dir.mkdir(parents=True, exist_ok=True)

        copied_count = 0
        used_target_names = set()

        for item in complete_items:
            for channel in ["G", "R"]:
                source_path = item.get(channel, "")
                if not source_path:
                    continue

                source = Path(str(source_path)).resolve()
                if not source.exists():
                    raise FileNotFoundError(f"输入图像不存在：{source}")

                target_name = source.name

                # 正常情况下，同一 raw_images/proteinX 目录下不会存在完全同名文件。
                # 这里加保护，是为了避免极端情况下不同来源复制到 cp_input 时发生覆盖。
                if target_name in used_target_names or (cp_input_dir / target_name).exists():
                    raise RuntimeError(
                        f"分析输入文件名重复，无法安全复制：{target_name}。"
                        "请检查原始图片是否存在同名文件。"
                    )

                target = cp_input_dir / target_name
                shutil.copy2(source, target)
                used_target_names.add(target_name)
                copied_count += 1

        self._log(log_callback, f"{protein_name} 已准备分析输入图像：{copied_count} 张。")
        return copied_count

    def prepare_output_folder(self, cp_output_dir: Path, protein_name: str, log_callback: LogCallback = None) -> None:
        """清空 cp_output/proteinX，避免旧输出混入本次结果。"""
        if cp_output_dir.exists():
            shutil.rmtree(cp_output_dir)
            self._log(log_callback, f"{protein_name} 已清空旧输出目录：{cp_output_dir}")
        cp_output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # MvImageID / 结果解析
    # ------------------------------------------------------------------
    def run_mvimageid(
        self,
        protein_key: str,
        protein_name: str,
        cp_input_dir: Path,
        cp_output_dir: Path,
        log_callback: LogCallback = None,
        cancel_callback: CancelCallback = None,
    ) -> MvImageIDRunResult:
        """统一调用 MvImageIDRunner。"""
        pipeline_file = Path(self.config.get_pipeline_by_protein(protein_key)).resolve()
        self._log(log_callback, f"{protein_name} Pipeline：{pipeline_file}")
        self._log(log_callback, f"{protein_name} 开始运行 MvImageID ...")

        runner = MvImageIDRunner(
            source_project_dir=str(self.config.get_source_project_dir()),
            venv_activate=str(self.config.get_venv_activate()),
            module_name=self.config.get_module_name(),
            plugins_directory=str(self.config.get_plugins_directory()),
            log_file="",
        )

        result = runner.run(
            pipeline_file=str(pipeline_file),
            input_dir=str(cp_input_dir),
            output_dir=str(cp_output_dir),
            log_callback=log_callback,
            cancel_callback=cancel_callback,
            log_file="",
        )

        if not result.success:
            raise RuntimeError(result.error_message or "MvImageID 运行失败。")

        self._log(log_callback, f"{protein_name} MvImageID 运行完成，用时：{result.elapsed_seconds:.2f} 秒。")
        return result

    def parse_result(self, cp_output_dir: Path) -> dict:
        parser = ResultParser(str(cp_output_dir))
        summary_result = parser.parse_image_summary()
        if not summary_result.get("success"):
            raise RuntimeError(summary_result.get("message", "解析分析结果失败。"))
        return summary_result

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    @staticmethod
    def _log(log_callback: LogCallback, message: str) -> None:
        if log_callback:
            log_callback(str(message))

    @staticmethod
    def _assert_folder_not_existing(folder: Path, name: str) -> None:
        if folder.exists() and any(folder.iterdir()):
            raise RuntimeError(f"{name} 已存在且非空：{folder}")
