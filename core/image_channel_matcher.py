# -*- coding: utf-8 -*-
"""
Unified image channel matching utilities for sperm protein analysis.

This module is intentionally independent from the UI. It provides one
canonical way to scan a folder, identify R/G/DIC/Merge channel images,
group them by field/view, and report whether each field is analyzable.

Strict matching rule:
- Only channel suffixes configured in config.ini are recognized.
- No hidden aliases such as green / fitc / red / pi are recognized.
- R and G are required for analysis.
- DIC and Merge are optional.

Example with default settings:
  G suffix     = _G
  R suffix     = _R
  DIC suffix   = _DIC
  Merge suffix = _Merge

Recognized:
  bb50-2_G.tif
  bb50-2_R.tif

Not recognized unless explicitly configured:
  bb50-2_green.tif
  bb50-2_fitc.tif
  bb50-2_pi.tif
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional, Tuple, Union


CHANNEL_R = "R"
CHANNEL_G = "G"
CHANNEL_DIC = "DIC"
CHANNEL_MERGE = "Merge"
CHANNELS = (CHANNEL_G, CHANNEL_R, CHANNEL_DIC, CHANNEL_MERGE)
REQUIRED_CHANNELS = (CHANNEL_G, CHANNEL_R)
OPTIONAL_CHANNELS = (CHANNEL_DIC, CHANNEL_MERGE)

DEFAULT_IMAGE_EXTENSIONS = (
    ".tif",
    ".tiff",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
)


@dataclass
class ImageRule:
    """Image naming rule loaded from config.ini or defaults."""

    r_suffix: str = "_R"
    g_suffix: str = "_G"
    dic_suffix: str = "_DIC"
    merge_suffix: str = "_Merge"
    image_ext: str = ".tif"

    @classmethod
    def from_dict(cls, rule: Optional[dict]) -> "ImageRule":
        rule = rule or {}
        return cls(
            r_suffix=str(rule.get("r_suffix") or "_R"),
            g_suffix=str(rule.get("g_suffix") or "_G"),
            dic_suffix=str(rule.get("dic_suffix") or "_DIC"),
            merge_suffix=str(rule.get("merge_suffix") or "_Merge"),
            image_ext=str(rule.get("image_ext") or ".tif"),
        )

    def suffix_map(self) -> Dict[str, str]:
        return {
            CHANNEL_R: self.r_suffix,
            CHANNEL_G: self.g_suffix,
            CHANNEL_DIC: self.dic_suffix,
            CHANNEL_MERGE: self.merge_suffix,
        }

    def allowed_extensions(self) -> Tuple[str, ...]:
        """
        Return accepted image extensions.

        The configured extension is included, but common microscopy/export
        formats are also accepted. Channel recognition is still strict: only
        configured channel suffixes are used.
        """
        values = {ext.lower() for ext in DEFAULT_IMAGE_EXTENSIONS}
        configured = (self.image_ext or "").strip().lower()
        if configured:
            if not configured.startswith("."):
                configured = "." + configured
            values.add(configured)
        return tuple(sorted(values))


@dataclass
class ChannelMatch:
    """One file matched to one channel."""

    channel: str
    field_id: str
    path: Path
    source: str = ""


@dataclass
class FieldImageSet:
    """All channel files belonging to one microscope field/view."""

    field_id: str
    files: Dict[str, Path] = field(default_factory=dict)
    duplicates: Dict[str, List[Path]] = field(default_factory=dict)

    def add(self, channel: str, path: Path):
        if channel in self.files:
            self.duplicates.setdefault(channel, [self.files[channel]]).append(path)
            # Keep the first file stable; duplicate details are reported.
            return
        self.files[channel] = path

    def get(self, channel: str) -> Optional[Path]:
        return self.files.get(channel)

    @property
    def has_g(self) -> bool:
        return CHANNEL_G in self.files

    @property
    def has_r(self) -> bool:
        return CHANNEL_R in self.files

    @property
    def has_dic(self) -> bool:
        return CHANNEL_DIC in self.files

    @property
    def has_merge(self) -> bool:
        return CHANNEL_MERGE in self.files

    @property
    def is_complete(self) -> bool:
        """Complete enough for analysis: G + R are required and no duplicates."""
        return self.has_g and self.has_r and not self.duplicates

    @property
    def missing_required(self) -> List[str]:
        return [channel for channel in REQUIRED_CHANNELS if channel not in self.files]

    @property
    def missing_optional(self) -> List[str]:
        return [channel for channel in OPTIONAL_CHANNELS if channel not in self.files]

    def status_text(self) -> str:
        if self.duplicates:
            duplicate_channels = ",".join(sorted(self.duplicates.keys()))
            return "重复通道：{}".format(duplicate_channels)
        if self.is_complete:
            return "可分析"
        missing = ",".join(self.missing_required)
        return "缺少：{}".format(missing)

    def to_row_dict(self) -> Dict[str, object]:
        return {
            "field_id": self.field_id,
            "G": str(self.files.get(CHANNEL_G, "")),
            "R": str(self.files.get(CHANNEL_R, "")),
            "DIC": str(self.files.get(CHANNEL_DIC, "")),
            "Merge": str(self.files.get(CHANNEL_MERGE, "")),
            "complete": self.is_complete,
            "status": self.status_text(),
        }


@dataclass
class FolderMatchResult:
    """Scan result for one source folder."""

    source_dir: Path
    fields: List[FieldImageSet]
    unmatched_files: List[Path]
    ignored_files: List[Path]

    @property
    def total_fields(self) -> int:
        return len(self.fields)

    @property
    def complete_fields(self) -> List[FieldImageSet]:
        return [item for item in self.fields if item.is_complete]

    @property
    def complete_count(self) -> int:
        return len(self.complete_fields)

    @property
    def analyzable(self) -> bool:
        return self.complete_count > 0

    def channel_count(self, channel: str) -> int:
        return sum(1 for item in self.fields if item.get(channel))

    def summary_text(self) -> str:
        if self.total_fields == 0:
            return "未找到可识别视野"
        return "共 {} 个视野，完整视野 {} 个".format(self.total_fields, self.complete_count)


class ImageChannelMatcher:
    """Canonical strict scanner/matcher for R/G/DIC/Merge channel images."""

    def __init__(self, image_rule: Optional[Union[ImageRule, dict]] = None, recursive: bool = False):
        if isinstance(image_rule, ImageRule):
            self.rule = image_rule
        else:
            self.rule = ImageRule.from_dict(image_rule)
        self.recursive = recursive

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def scan_folder(self, source_dir: Union[str, Path]) -> FolderMatchResult:
        source_path = Path(source_dir)
        fields_by_id: Dict[str, FieldImageSet] = {}
        unmatched_files: List[Path] = []
        ignored_files: List[Path] = []

        for path in self.iter_image_files(source_path):
            match = self.match_file(path)
            if match is None:
                unmatched_files.append(path)
                continue
            fields_by_id.setdefault(match.field_id, FieldImageSet(match.field_id)).add(
                match.channel, match.path
            )

        if source_path.exists():
            for path in self.iter_all_files(source_path):
                if path.suffix.lower() not in self.rule.allowed_extensions():
                    ignored_files.append(path)

        fields = sorted(fields_by_id.values(), key=lambda item: natural_key(item.field_id))
        return FolderMatchResult(source_path, fields, unmatched_files, ignored_files)

    def match_file(self, path: Union[str, Path]) -> Optional[ChannelMatch]:
        path = Path(path)
        stem = path.stem

        matched = self._match_by_config_suffix(stem)
        if matched is None:
            return None

        channel, field_id, source = matched
        return ChannelMatch(channel=channel, field_id=field_id, path=path, source=source)

    def iter_image_files(self, source_dir: Path) -> Iterable[Path]:
        if not source_dir.exists() or not source_dir.is_dir():
            return []
        iterator = source_dir.rglob("*") if self.recursive else source_dir.glob("*")
        allowed = self.rule.allowed_extensions()
        return sorted(
            (path for path in iterator if path.is_file() and path.suffix.lower() in allowed),
            key=lambda p: natural_key(p.name),
        )

    def iter_all_files(self, source_dir: Path) -> Iterable[Path]:
        if not source_dir.exists() or not source_dir.is_dir():
            return []
        iterator = source_dir.rglob("*") if self.recursive else source_dir.glob("*")
        return sorted((path for path in iterator if path.is_file()), key=lambda p: natural_key(p.name))

    # ------------------------------------------------------------------
    # Matching internals
    # ------------------------------------------------------------------
    def _match_by_config_suffix(self, stem: str) -> Optional[Tuple[str, str, str]]:
        """
        Strictly match only suffixes configured in ImageRule.

        Case-insensitive exact suffix match is allowed, but hidden aliases and
        token guessing are not allowed. For example, with suffix _G:
        - bb50-2_G      -> matched as G
        - bb50-2_g      -> matched as G
        - bb50-2_green  -> not matched
        """
        suffix_items = sorted(
            self.rule.suffix_map().items(), key=lambda item: len(item[1] or ""), reverse=True
        )

        stem_lower = stem.lower()
        for channel, suffix in suffix_items:
            suffix = (suffix or "").strip()
            if not suffix:
                continue

            if stem_lower.endswith(suffix.lower()):
                field_id = stem[: -len(suffix)].rstrip(" _-.")
                return channel, field_id or stem, "suffix:{}".format(suffix)

        return None


def clean_field_id(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"[\s_\-\.]+$", "", value)
    value = re.sub(r"^[\s_\-\.]+", "", value)
    return value


def natural_key(value: str) -> List[object]:
    """Natural sort key: field2 before field10."""
    text = str(value)
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]
