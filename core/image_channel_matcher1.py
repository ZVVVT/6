# -*- coding: utf-8 -*-
"""
Unified image channel matching utilities for sperm protein analysis.

This module is intentionally independent from the UI.  It provides one
canonical way to scan a folder, identify R/G/DIC/Merge channel images,
group them by field/view, and report whether each field is analyzable.

Planned integration points:
- single protein import in app/analysis_window.py
- batch pre-check in app/batch_analysis_dialog.py
- ProteinAnalysisService raw image import / cp_input preparation
- historical result/input loading where channel grouping is needed

Design rules:
- R and G are required for analysis.
- DIC and Merge are optional.
- Configured suffixes such as _R, _G, _DIC, _Merge are preferred.
- A small alias set is supported for robustness, but exact configured
  suffixes should remain the primary standard for hospital delivery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


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
        formats are also accepted to make batch pre-check and manual import
        behavior consistent.
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
        """Complete enough for analysis: G + R are required."""
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
            return f"重复通道：{duplicate_channels}"
        if self.is_complete:
            return "可分析"
        missing = ",".join(self.missing_required)
        return f"缺少：{missing}"

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
        return f"共 {self.total_fields} 个视野，完整视野 {self.complete_count} 个"


class ImageChannelMatcher:
    """Canonical scanner/matcher for R/G/DIC/Merge channel images."""

    def __init__(self, image_rule: Optional[ImageRule | dict] = None, *, recursive: bool = False):
        if isinstance(image_rule, ImageRule):
            self.rule = image_rule
        else:
            self.rule = ImageRule.from_dict(image_rule)
        self.recursive = recursive

        # Aliases are only a fallback. Config suffixes remain first priority.
        self.alias_tokens: Dict[str, Tuple[str, ...]] = {
            CHANNEL_G: ("g", "green", "fitc", "荧光g", "绿色"),
            CHANNEL_R: ("r", "red", "pi", "荧光r", "红色"),
            CHANNEL_DIC: ("dic", "brightfield", "bf", "phase", "相差", "明场"),
            CHANNEL_MERGE: ("merge", "merged", "overlay", "合并"),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def scan_folder(self, source_dir: str | Path) -> FolderMatchResult:
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

    def match_file(self, path: str | Path) -> Optional[ChannelMatch]:
        path = Path(path)
        stem = path.stem

        by_suffix = self._match_by_config_suffix(stem)
        if by_suffix is not None:
            channel, field_id, source = by_suffix
            return ChannelMatch(channel=channel, field_id=field_id, path=path, source=source)

        by_alias = self._match_by_alias_token(stem)
        if by_alias is not None:
            channel, field_id, source = by_alias
            return ChannelMatch(channel=channel, field_id=field_id, path=path, source=source)

        return None

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
        # Sort by suffix length to avoid _R accidentally matching _Merge-like names.
        suffix_items = sorted(
            self.rule.suffix_map().items(), key=lambda item: len(item[1] or ""), reverse=True
        )
        stem_norm = normalize_name(stem)

        for channel, suffix in suffix_items:
            suffix = (suffix or "").strip()
            if not suffix:
                continue

            # Exact end match keeps field_id from the original stem.
            if stem.lower().endswith(suffix.lower()):
                field_id = stem[: -len(suffix)].rstrip(" _-.")
                return channel, field_id or stem, f"suffix:{suffix}"

            # Normalized end match supports HEL1-G, HEL1 G, etc.
            suffix_norm = normalize_name(suffix)
            if suffix_norm and stem_norm.endswith(suffix_norm):
                field_id_norm = stem_norm[: -len(suffix_norm)].strip("_-.")
                field_id = extract_field_id_by_normalized_suffix(stem, suffix)
                return channel, field_id or field_id_norm or stem, f"suffix-normalized:{suffix}"

        return None

    def _match_by_alias_token(self, stem: str) -> Optional[Tuple[str, str, str]]:
        tokens = split_tokens(stem)
        if not tokens:
            return None

        lower_tokens = [token.lower() for token in tokens]
        candidates: List[Tuple[str, int, str]] = []

        for channel, aliases in self.alias_tokens.items():
            for alias in aliases:
                alias_lower = alias.lower()
                for index, token in enumerate(lower_tokens):
                    if token == alias_lower:
                        candidates.append((channel, index, alias))

        # Ambiguous aliases should not be guessed.
        unique_channels = {item[0] for item in candidates}
        if len(unique_channels) != 1:
            return None

        channel, index, alias = candidates[0]
        field_tokens = tokens[:index] + tokens[index + 1 :]
        field_id = clean_field_id("_".join(field_tokens)) or stem
        return channel, field_id, f"alias:{alias}"


def normalize_name(value: str) -> str:
    """Normalize names for permissive comparison."""
    value = str(value or "").strip().lower()
    return re.sub(r"[\s_\-\.]+", "", value)


def split_tokens(value: str) -> List[str]:
    value = str(value or "").strip()
    raw = re.split(r"[\s_\-\.()\[\]{}]+", value)
    return [item for item in raw if item]


def clean_field_id(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"[\s_\-\.]+$", "", value)
    value = re.sub(r"^[\s_\-\.]+", "", value)
    return value


def extract_field_id_by_normalized_suffix(stem: str, suffix: str) -> str:
    """
    Best effort field_id extraction for normalized suffix matches.

    If the exact suffix was not found due to delimiter/case differences, use
    token logic to remove the final channel-like token.
    """
    tokens = split_tokens(stem)
    suffix_token = normalize_name(suffix)
    if tokens and normalize_name(tokens[-1]) == suffix_token:
        return clean_field_id("_".join(tokens[:-1]))
    return stem


def natural_key(value: str) -> List[object]:
    """Natural sort key: field2 before field10."""
    text = str(value)
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]
