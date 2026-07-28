"""Adapt protein-page image rows for Analysis V2 head segmentation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Sequence


COMPLETE_STATUSES = {
    "\u5b8c\u6574",
    "\u53ef\u5206\u6790",
}


def build_head_segmentation_fields(
    image_items: Sequence[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Build head fields with required R/G and optional Merge."""
    items = [
        dict(item or {})
        for item in image_items
    ]

    if not items:
        raise ValueError(
            "Analysis V2 \u6ca1\u6709\u6536\u5230\u4efb\u4f55\u5934\u90e8\u89c6\u91ce\u3002"
        )

    fields: List[Dict[str, str]] = []
    errors: List[str] = []
    seen_field_ids = set()

    for index, item in enumerate(items, start=1):
        field_id = str(
            item.get("field_no", "") or ""
        ).strip()

        if not field_id:
            errors.append(
                "\u7b2c {} \u884c\u89c6\u91ce\u7f16\u53f7\u4e3a\u7a7a".format(
                    index
                )
            )
            continue

        normalized_id = field_id.casefold()

        if normalized_id in seen_field_ids:
            errors.append(
                "\u89c6\u91ce\u7f16\u53f7\u91cd\u590d\uff1a{}".format(
                    field_id
                )
            )
            continue

        seen_field_ids.add(normalized_id)

        status = str(
            item.get("status", "") or ""
        ).strip()

        if status and status not in COMPLETE_STATUSES:
            errors.append(
                "{} \u4e0d\u662f\u53ef\u5206\u6790\u89c6\u91ce\uff1a{}".format(
                    field_id,
                    status,
                )
            )
            continue

        channel_paths: Dict[str, str] = {}
        field_has_error = False

        for source_channel, target_key in (
            ("R", "tritc_path"),
            ("G", "fitc_path"),
        ):
            value = str(
                item.get(source_channel, "") or ""
            ).strip()

            if not value:
                errors.append(
                    "{} \u7f3a\u5c11\u5fc5\u9700\u901a\u9053\uff1a{}".format(
                        field_id,
                        source_channel,
                    )
                )
                field_has_error = True
                continue

            source_path = Path(value).resolve()

            if not source_path.is_file():
                errors.append(
                    "{} {} \u6587\u4ef6\u4e0d\u5b58\u5728\uff1a{}".format(
                        field_id,
                        source_channel,
                        source_path,
                    )
                )
                field_has_error = True
                continue

            channel_paths[target_key] = str(source_path)

        merge_value = str(
            item.get("Merge", "") or ""
        ).strip()

        if merge_value:
            merge_path = Path(merge_value).resolve()

            if not merge_path.is_file():
                errors.append(
                    "{} Merge \u6587\u4ef6\u4e0d\u5b58\u5728\uff1a{}".format(
                        field_id,
                        merge_path,
                    )
                )
                field_has_error = True
            else:
                channel_paths["merge_path"] = str(
                    merge_path
                )
        else:
            channel_paths["merge_path"] = ""

        if field_has_error:
            continue

        fields.append({
            "field_id": field_id,
            "tritc_path": channel_paths["tritc_path"],
            "fitc_path": channel_paths["fitc_path"],
            "merge_path": channel_paths["merge_path"],
        })

    if errors:
        raise RuntimeError(
            "Analysis V2 \u5934\u90e8\u8f93\u5165\u68c0\u67e5\u5931\u8d25\uff1a\n- "
            + "\n- ".join(errors)
        )

    if not fields:
        raise RuntimeError(
            "Analysis V2 \u6ca1\u6709\u53ef\u7528\u7684 R/G \u5934\u90e8\u89c6\u91ce\u3002"
        )

    return fields
