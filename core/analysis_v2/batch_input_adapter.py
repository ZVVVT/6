"""Adapt one already-matched Batch protein folder to Analysis V2 input."""

from pathlib import Path
from typing import Any, Mapping, Optional

from core.image_channel_matcher import ImageChannelMatcher

from .segmentation_service import _validate_field_id
from .task_runner import AnalysisV2TaskRequest


FORMAL_PROTEIN_PARTS = {
    "protein1": ("Q9BYW3", "head"),
    "protein2": ("P10323", "head"),
    "protein3": ("Q96P56", "tail"),
    "protein4": ("Q8IYV9", "head"),
    "protein5": ("W5XKT8", "head"),
}


class AnalysisV2BatchInputError(ValueError):
    """A non-UI error raised while adapting one Batch input folder."""

    def __init__(self, message, protein_key, folder, field=None, channel=None):
        details = ["protein_key={}".format(protein_key), "folder={}".format(folder)]
        if field is not None:
            details.append("field={}".format(field))
        if channel is not None:
            details.append("channel={}".format(channel))
        super().__init__("{} ({})".format(message, ", ".join(details)))
        self.protein_key = protein_key
        self.folder = str(folder)
        self.field = field
        self.channel = channel


def build_batch_task_request(
    case_data: Mapping[str, Any],
    protein_key: str,
    protein_folder: Any,
    config: Any,
    workspace_root: Optional[Any] = None,
    candidate_path_mode: str = "graph_preserving",
) -> AnalysisV2TaskRequest:
    """Build, but never execute, an Analysis V2 request for one Batch item.

    ``protein_folder`` is the final folder selected by the existing Batch alias
    and folder-matching UI. This adapter deliberately does not redefine those
    matching rules.
    """
    key = str(protein_key or "").strip()
    folder = Path(str(protein_folder or "")).resolve()
    formal = FORMAL_PROTEIN_PARTS.get(key)
    if formal is None:
        raise AnalysisV2BatchInputError(
            "非法 protein_key", key, folder,
        )

    _accession, expected_part = formal
    configured_part = str(config.get_protein_part(key) or "").strip().lower()
    if configured_part != expected_part:
        raise AnalysisV2BatchInputError(
            "配置 part 与正式映射不一致：配置={}，正式={}".format(
                configured_part or "<empty>", expected_part,
            ),
            key,
            folder,
        )

    if not folder.exists():
        raise AnalysisV2BatchInputError("protein folder 不存在", key, folder)
    if not folder.is_dir():
        raise AnalysisV2BatchInputError("protein folder 不是文件夹", key, folder)

    match_result = ImageChannelMatcher(config.get_image_rule()).scan_folder(folder)
    _validate_channel_totals(match_result, key, folder)
    matched_fields = _build_matched_fields(match_result, key, expected_part, folder)

    case = dict(case_data or {})
    case_no = str(case.get("case_no", "") or "").strip()
    if not case_no:
        raise AnalysisV2BatchInputError("case_no 为空", key, folder)

    root_value = workspace_root
    if root_value is None:
        root_value = config.get_workspace_root()
    root = Path(root_value)
    if not root.is_absolute():
        root = Path(config.app_root) / root
    root = root.resolve()

    if candidate_path_mode not in ("graph_preserving", "ordered"):
        raise AnalysisV2BatchInputError(
            "非法 candidate_path_mode：{}".format(candidate_path_mode), key, folder,
        )

    return AnalysisV2TaskRequest(
        case_no=case_no,
        case_id=case.get("id"),
        protein_key=key,
        protein_part=expected_part,
        matched_fields=matched_fields,
        workspace_root=root,
        raw_image_folder=str(folder),
        candidate_path_mode=candidate_path_mode,
    )


def _validate_channel_totals(match_result, protein_key, folder):
    if match_result.channel_count("G") <= 0:
        raise AnalysisV2BatchInputError(
            "未识别到必需通道 G", protein_key, folder, channel="G",
        )
    if match_result.channel_count("R") <= 0:
        raise AnalysisV2BatchInputError(
            "未识别到必需通道 R", protein_key, folder, channel="R",
        )


def _build_matched_fields(match_result, protein_key, protein_part, folder):
    fields = []
    seen_ids = set()
    for field_set in match_result.fields:
        field_id = str(field_set.field_id or "").strip()
        folded_id = field_id.casefold()
        if folded_id in seen_ids or field_set.duplicates:
            duplicate_channels = ",".join(sorted(field_set.duplicates)) or "field_id"
            raise AnalysisV2BatchInputError(
                "重复 field/通道：{}".format(duplicate_channels),
                protein_key,
                folder,
                field=field_id,
                channel=duplicate_channels,
            )
        seen_ids.add(folded_id)

        try:
            _validate_field_id(field_id)
        except ValueError as error:
            raise AnalysisV2BatchInputError(
                str(error), protein_key, folder, field=field_id,
            ) from error

        missing = [channel for channel in ("G", "R") if not field_set.get(channel)]
        if missing:
            raise AnalysisV2BatchInputError(
                "G/R 视野不匹配，缺少通道 {}".format(",".join(missing)),
                protein_key,
                folder,
                field=field_id,
                channel=",".join(missing),
            )

        merge = field_set.get("Merge")
        if protein_part == "tail" and not merge:
            raise AnalysisV2BatchInputError(
                "protein3 缺少必需通道 Merge",
                protein_key,
                folder,
                field=field_id,
                channel="Merge",
            )

        fields.append({
            "field_id": field_id,
            "tritc_path": str(Path(field_set.get("R")).resolve()),
            "fitc_path": str(Path(field_set.get("G")).resolve()),
            "merge_path": str(Path(merge).resolve()) if merge else "",
        })

    if not fields:
        raise AnalysisV2BatchInputError(
            "未识别到可用视野", protein_key, folder, channel="G,R",
        )
    return fields
