"""Safely publish validated Analysis V2 tail measurement output.

The measurement workflow writes into a private ``candidate_output`` directory.
This module installs that complete result into ``cp_output/protein3`` without
exposing a partially copied directory.  The previous output remains available
for rollback until the caller confirms that the database transaction succeeded.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from core.result_parser import ResultParser


class TailResultPublishError(RuntimeError):
    """Raised when a tail result cannot be staged, validated, or restored."""


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _directory_manifest(directory: Path) -> Dict[str, Dict[str, Any]]:
    root = Path(directory).resolve()
    if not root.is_dir():
        raise TailResultPublishError(
            "结果目录不存在：{}".format(root)
        )

    manifest: Dict[str, Dict[str, Any]] = {}
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: str(item.relative_to(root)).casefold(),
    ):
        relative = path.relative_to(root).as_posix()
        manifest[relative] = {
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }

    if not manifest:
        raise TailResultPublishError(
            "结果目录为空：{}".format(root)
        )

    return manifest


def _count_overlay_files(directory: Path) -> Dict[str, int]:
    counts = {
        "g_objects": 0,
        "r_objects": 0,
        "colocalized": 0,
    }
    for path in directory.rglob("*.png"):
        name = path.name.lower()
        if name.endswith("_g_g_objects_origoverlay.png"):
            counts["g_objects"] += 1
        elif name.endswith("_r_r_objects_origoverlay.png"):
            counts["r_objects"] += 1
        elif name.endswith("_g_g_colocalized_origoverlay.png"):
            counts["colocalized"] += 1
    return counts


def validate_tail_result_directory(
    output_dir: Path,
    expected_field_count: Optional[int] = None,
    measurement_contract: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate a formal tail directory with the head-equivalent contract."""
    directory = Path(output_dir).resolve()

    parser = ResultParser(
        str(directory),
        protein_part="tail",
        calculation_mode="head_equivalent",
    )
    summary = parser.parse_image_summary(
        protein_part="tail"
    )

    if not summary.get("success"):
        raise TailResultPublishError(
            "尾部结果验证失败：{}".format(
                summary.get("message", "未知解析错误")
            )
        )
    if summary.get("calculation_mode") != "head_equivalent":
        raise TailResultPublishError(
            "尾部正式结果未使用 head_equivalent 公式。"
        )

    warnings = list(summary.get("warnings") or [])
    if measurement_contract:
        _validate_measurement_contract(summary, measurement_contract)
        warnings = [
            warning for warning in warnings
            if not _is_legacy_tail_count_warning(warning)
        ]
        summary["warnings"] = warnings
    if warnings:
        raise TailResultPublishError(
            "尾部结果存在一致性警告：{}".format(
                "；".join(str(item) for item in warnings)
            )
        )

    rows = list(summary.get("rows") or [])
    total = dict(summary.get("total") or {})
    field_count = int(total.get("field_count", 0) or 0)

    if field_count <= 0 or not rows:
        raise TailResultPublishError(
            "尾部结果不包含有效视野。"
        )
    if field_count != len(rows):
        raise TailResultPublishError(
            "尾部结果视野数不一致：汇总={}，明细={}。".format(
                field_count,
                len(rows),
            )
        )

    if expected_field_count is not None:
        expected = int(expected_field_count)
        if expected > 0 and field_count != expected:
            raise TailResultPublishError(
                "尾部结果视野数错误：期望 {}，实际 {}。".format(
                    expected,
                    field_count,
                )
            )

    image_csv = Path(str(summary.get("image_csv", "") or ""))
    object_csv = Path(str(summary.get("object_csv", "") or ""))
    if not image_csv.is_file():
        raise TailResultPublishError("尾部结果缺少 Image.csv。")
    if not object_csv.is_file():
        raise TailResultPublishError("尾部结果缺少 G_objects.csv。")

    total_sperm = int(total.get("sperm_count", 0) or 0)
    total_positive = int(total.get("positive_count", 0) or 0)
    # Count_G_objects matches tail_object_count after contract validation.
    total_tail = sum(int(row.get("g_objects_count", 0) or 0) for row in rows)
    if total_sperm <= 0:
        raise TailResultPublishError("尾部结果精子总数无效。")
    if total_tail <= 0:
        raise TailResultPublishError("尾部结果有效尾部数无效。")
    if total_positive > total_sperm:
        raise TailResultPublishError(
            "尾部有效数量大于精子总数。"
        )

    overlay_counts = _count_overlay_files(directory)
    expected_overlays = field_count
    for role, count in overlay_counts.items():
        if count != expected_overlays:
            raise TailResultPublishError(
                "尾部叠加图数量错误：{} 期望 {}，实际 {}。".format(
                    role,
                    expected_overlays,
                    count,
                )
            )

    return summary


def _is_legacy_tail_count_warning(warning: Any) -> bool:
    text = str(warning)
    return (
        "Count_G_objects=" in text
        and "Count_R_colocalized=" in text
        and "不一致" in text
    )


def _contract_count(value: Any, name: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise TailResultPublishError(
            "尾部测量 contract 的 {} 不是有效计数：{}。".format(name, value)
        )
    count = int(round(number))
    if count < 0 or abs(number - count) > 1e-6:
        raise TailResultPublishError(
            "尾部测量 contract 的 {} 不是非负整数：{}。".format(name, value)
        )
    return count


def _validate_measurement_contract(
    summary: Dict[str, Any],
    measurement_contract: Dict[str, Any],
) -> None:
    """Match published CSV counts to the formal per-field measurement contract."""
    contract_fields = list(measurement_contract.get("fields") or [])
    if not contract_fields:
        raise TailResultPublishError("尾部测量 contract 缺少视野计数。")

    rows_by_image = {
        int(row.get("image_number", 0) or 0): row
        for row in list(summary.get("rows") or [])
    }
    if len(rows_by_image) != len(contract_fields):
        raise TailResultPublishError(
            "尾部测量 contract 视野数与发布结果不一致。"
        )

    for field in contract_fields:
        image_number = _contract_count(field.get("image_number"), "image_number")
        row = rows_by_image.get(image_number)
        if row is None:
            raise TailResultPublishError(
                "尾部测量 contract 的视野 {} 在发布结果中不存在。".format(
                    image_number
                )
            )

        tail_count = _contract_count(
            field.get("tail_object_count"), "tail_object_count"
        )
        associated_count = _contract_count(
            field.get("associated_object_count"), "associated_object_count"
        )
        if associated_count > tail_count:
            raise TailResultPublishError(
                "视野 {} 的 associated_object_count={} 大于 "
                "tail_object_count={}。".format(
                    image_number, associated_count, tail_count
                )
            )

        unresolved_value = field.get("unresolved_object_count")
        if unresolved_value is not None:
            unresolved_count = _contract_count(
                unresolved_value, "unresolved_object_count"
            )
            if unresolved_count != tail_count - associated_count:
                raise TailResultPublishError(
                    "视野 {} 的 unresolved_object_count={} 与 "
                    "tail_object_count-associated_object_count={} 不一致。".format(
                        image_number,
                        unresolved_count,
                        tail_count - associated_count,
                    )
                )

        g_count = _contract_count(row.get("g_objects_count"), "Count_G_objects")
        colocalized_count = _contract_count(
            row.get("positive_count"), "Count_R_colocalized"
        )
        if g_count != tail_count:
            raise TailResultPublishError(
                "视野 {} 的 Count_G_objects={} 与 tail_object_count={} 不一致。".format(
                    image_number, g_count, tail_count
                )
            )
        if colocalized_count != associated_count:
            raise TailResultPublishError(
                "视野 {} 的 Count_R_colocalized={} 与 "
                "associated_object_count={} 不一致。".format(
                    image_number, colocalized_count, associated_count
                )
            )


@dataclass
class TailResultPublication:
    """A staged output replacement awaiting database confirmation."""

    source_dir: Path
    target_dir: Path
    backup_dir: Path
    had_previous_output: bool
    summary: Dict[str, Any]
    _closed: bool = False

    def commit(self) -> str:
        """Keep the new result and remove the old output backup."""
        if self._closed:
            return ""

        self._closed = True
        if not self.backup_dir.exists():
            return ""

        try:
            _remove_path(self.backup_dir)
            return ""
        except BaseException as exception:
            return (
                "旧结果备份未能自动清理，可稍后手动删除：{}；错误：{}"
                .format(self.backup_dir, exception)
            )

    def rollback(self) -> None:
        """Remove the new result and restore the previous formal output."""
        if self._closed:
            return

        errors = []

        try:
            if self.target_dir.exists():
                _remove_path(self.target_dir)
        except BaseException as exception:
            errors.append(
                "删除新结果失败：{}".format(exception)
            )

        if self.had_previous_output:
            try:
                if not self.backup_dir.exists():
                    raise FileNotFoundError(
                        "备份目录不存在：{}".format(
                            self.backup_dir
                        )
                    )
                self.backup_dir.replace(self.target_dir)
            except BaseException as exception:
                errors.append(
                    "恢复旧结果失败：{}".format(exception)
                )
        else:
            try:
                if self.backup_dir.exists():
                    _remove_path(self.backup_dir)
            except BaseException as exception:
                errors.append(
                    "清理空备份失败：{}".format(exception)
                )

        self._closed = True
        if errors:
            raise TailResultPublishError("；".join(errors))


def stage_tail_measurement_output(
    source_dir: Path,
    target_dir: Path,
    expected_field_count: Optional[int] = None,
    measurement_contract: Optional[Dict[str, Any]] = None,
) -> TailResultPublication:
    """Install a complete validated tail result and retain rollback backup."""
    source = Path(source_dir).resolve()
    target = Path(target_dir).resolve()

    if source == target:
        raise TailResultPublishError(
            "候选输出目录与正式输出目录相同。"
        )
    if source in target.parents or target in source.parents:
        raise TailResultPublishError(
            "候选输出目录与正式输出目录不能互相嵌套。"
        )

    source_summary = validate_tail_result_directory(
        source,
        expected_field_count=expected_field_count,
        measurement_contract=measurement_contract,
    )
    source_manifest = _directory_manifest(source)

    target.parent.mkdir(parents=True, exist_ok=True)

    token = uuid.uuid4().hex
    staging = target.parent / (
        ".{}.analysis_v2_tail_staging_{}".format(
            target.name,
            token,
        )
    )
    backup = target.parent / (
        ".{}.analysis_v2_tail_backup_{}".format(
            target.name,
            token,
        )
    )

    had_previous_output = target.exists()
    old_output_moved = False
    new_output_installed = False

    try:
        shutil.copytree(source, staging)

        if _directory_manifest(staging) != source_manifest:
            raise TailResultPublishError(
                "暂存目录与候选输出的文件校验不一致。"
            )

        validate_tail_result_directory(
            staging,
            expected_field_count=expected_field_count,
            measurement_contract=measurement_contract,
        )

        if had_previous_output:
            target.replace(backup)
            old_output_moved = True

        staging.replace(target)
        new_output_installed = True

        if _directory_manifest(target) != source_manifest:
            raise TailResultPublishError(
                "正式输出目录与候选输出的文件校验不一致。"
            )

        published_summary = validate_tail_result_directory(
            target,
            expected_field_count=expected_field_count,
            measurement_contract=measurement_contract,
        )

        return TailResultPublication(
            source_dir=source,
            target_dir=target,
            backup_dir=backup,
            had_previous_output=had_previous_output,
            summary=published_summary or source_summary,
        )

    except BaseException as exception:
        rollback_errors = []

        try:
            if new_output_installed and target.exists():
                _remove_path(target)
        except BaseException as rollback_exception:
            rollback_errors.append(
                "删除新结果失败：{}".format(
                    rollback_exception
                )
            )

        try:
            if old_output_moved and backup.exists():
                backup.replace(target)
        except BaseException as rollback_exception:
            rollback_errors.append(
                "恢复旧结果失败：{}".format(
                    rollback_exception
                )
            )

        try:
            if staging.exists():
                _remove_path(staging)
        except BaseException as rollback_exception:
            rollback_errors.append(
                "清理暂存目录失败：{}".format(
                    rollback_exception
                )
            )

        detail = str(exception)
        if rollback_errors:
            detail += "；自动回滚异常：" + " | ".join(
                rollback_errors
            )

        raise TailResultPublishError(detail) from exception

    finally:
        if staging.exists():
            try:
                _remove_path(staging)
            except BaseException:
                pass
