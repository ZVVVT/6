"""Safely publish Analysis V2 head measurement output.

The measurement task writes into its private run directory.  This module
installs that complete result into the legacy ``cp_output/<protein>`` path
without exposing a partially copied directory.  The previous output is kept
as a rollback backup until the caller confirms that the database update has
also succeeded.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from core.result_parser import ResultParser


class HeadResultPublishError(RuntimeError):
    """Raised when a head result cannot be staged, validated, or restored."""


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
        raise HeadResultPublishError(
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
        raise HeadResultPublishError(
            "结果目录为空：{}".format(root)
        )

    return manifest


def validate_head_result_directory(
    output_dir: Path,
    expected_field_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Validate a published directory with the existing head ResultParser."""
    directory = Path(output_dir).resolve()

    parser = ResultParser(
        str(directory),
        protein_part="head",
    )
    summary = parser.parse_image_summary(
        protein_part="head"
    )

    if not summary.get("success"):
        raise HeadResultPublishError(
            "头部结果验证失败：{}".format(
                summary.get("message", "未知解析错误")
            )
        )

    rows = list(summary.get("rows") or [])
    total = dict(summary.get("total") or {})
    field_count = int(total.get("field_count", 0) or 0)

    if field_count <= 0 or len(rows) <= 0:
        raise HeadResultPublishError(
            "头部结果不包含有效视野。"
        )

    if field_count != len(rows):
        raise HeadResultPublishError(
            "头部结果视野数不一致：汇总={}，明细={}。".format(
                field_count,
                len(rows),
            )
        )

    if expected_field_count is not None:
        expected = int(expected_field_count)
        if expected > 0 and field_count != expected:
            raise HeadResultPublishError(
                "头部结果视野数错误：期望 {}，实际 {}。".format(
                    expected,
                    field_count,
                )
            )

    image_csv = Path(str(summary.get("image_csv", "") or ""))
    object_csv = Path(str(summary.get("object_csv", "") or ""))

    if not image_csv.is_file():
        raise HeadResultPublishError(
            "头部结果缺少 Image.csv。"
        )
    if not object_csv.is_file():
        raise HeadResultPublishError(
            "头部结果缺少对象级 CSV。"
        )

    return summary


@dataclass
class HeadResultPublication:
    """A staged output replacement awaiting database confirmation."""

    source_dir: Path
    target_dir: Path
    backup_dir: Path
    had_previous_output: bool
    summary: Dict[str, Any]
    _closed: bool = False

    def commit(self) -> str:
        """Keep the new result and remove the old backup.

        Returns a non-fatal cleanup warning.  A backup cleanup failure does
        not invalidate the already published files or database transaction.
        """
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
        """Remove the new result and restore the previous output."""
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
            raise HeadResultPublishError(
                "；".join(errors)
            )


def stage_head_measurement_output(
    source_dir: Path,
    target_dir: Path,
    expected_field_count: Optional[int] = None,
) -> HeadResultPublication:
    """Install a complete result and retain the previous output for rollback."""
    source = Path(source_dir).resolve()
    target = Path(target_dir).resolve()

    if source == target:
        raise HeadResultPublishError(
            "测量输出目录与正式输出目录相同。"
        )
    if source in target.parents or target in source.parents:
        raise HeadResultPublishError(
            "测量输出目录与正式输出目录不能互相嵌套。"
        )

    source_summary = validate_head_result_directory(
        source,
        expected_field_count=expected_field_count,
    )
    source_manifest = _directory_manifest(source)

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    token = uuid.uuid4().hex
    staging = target.parent / (
        ".{}.analysis_v2_staging_{}".format(
            target.name,
            token,
        )
    )
    backup = target.parent / (
        ".{}.analysis_v2_backup_{}".format(
            target.name,
            token,
        )
    )

    had_previous_output = target.exists()
    old_output_moved = False
    new_output_installed = False

    try:
        shutil.copytree(source, staging)

        staging_manifest = _directory_manifest(staging)
        if staging_manifest != source_manifest:
            raise HeadResultPublishError(
                "暂存目录与测量输出的文件校验不一致。"
            )

        validate_head_result_directory(
            staging,
            expected_field_count=expected_field_count,
        )

        if had_previous_output:
            target.replace(backup)
            old_output_moved = True

        staging.replace(target)
        new_output_installed = True

        target_manifest = _directory_manifest(target)
        if target_manifest != source_manifest:
            raise HeadResultPublishError(
                "正式输出目录与测量输出的文件校验不一致。"
            )

        published_summary = validate_head_result_directory(
            target,
            expected_field_count=expected_field_count,
        )

        return HeadResultPublication(
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

        raise HeadResultPublishError(detail) from exception

    finally:
        if staging.exists():
            try:
                _remove_path(staging)
            except BaseException:
                pass
