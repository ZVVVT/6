"""Filter confirmed C18B final instances before manual tail editing.

The production rule is intentionally limited to identity communities whose
maximum source-candidate path length is strictly less than 80 pixels.  Missing
or ambiguous metadata always keeps the corresponding final instance.
"""

import argparse
import csv
import json
import math
import os
import time
from pathlib import Path

import cv2
import numpy as np


THRESHOLD = 80.0
BASELINE_NAME = "06_final_tail_instances.tif"
FILTERED_NAME = "07_extreme_fragment_filtered_labels.tif"
AUDIT_NAME = "extreme_fragment_filter.csv"
REMOVE_REASON = "community_max_path_lt_80"
AUDIT_FIELDS = [
    "final_instance_id",
    "identity_community_id",
    "max_candidate_path_length",
    "removed",
    "reason",
]


def _read_rows(path):
    path = Path(path)
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _positive_ids(labels):
    return [int(value) for value in np.unique(labels) if int(value) > 0]


def _unique_rows(rows, key):
    grouped = {}
    for row in rows:
        try:
            value = int(str(row.get(key, "")).strip())
        except (TypeError, ValueError):
            continue
        grouped.setdefault(value, []).append(row)
    return {
        value: values[0]
        for value, values in grouped.items()
        if len(values) == 1
    }


def _finite_float(value):
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _write_audit(path, rows):
    temporary = Path(str(path) + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUDIT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(str(temporary), str(path))


def apply_extreme_fragment_filter(directory):
    """Write filtered labels and an audit without mutating baseline labels."""
    started = time.perf_counter()
    directory = Path(directory).resolve()
    baseline_path = directory / BASELINE_NAME
    filtered_path = directory / FILTERED_NAME
    audit_path = directory / AUDIT_NAME
    baseline = cv2.imread(str(baseline_path), cv2.IMREAD_UNCHANGED)
    if baseline is None or baseline.ndim != 2:
        raise ValueError("无法读取C18B baseline labels：{}".format(baseline_path))
    baseline_before = baseline.copy()
    baseline_ids = _positive_ids(baseline)

    communities = _unique_rows(
        _read_rows(directory / "shadow_communities.csv"),
        "dense_final_instance_id",
    )
    finals = _unique_rows(
        _read_rows(directory / "final_instance_diagnostics.csv"),
        "final_instance_id",
    )
    remove_ids = set()
    audit_rows = []
    for final_id in baseline_ids:
        community = communities.get(final_id)
        final = finals.get(final_id)
        identity_id = ""
        max_path_text = ""
        reliable = community is not None and final is not None
        if reliable:
            identity_id = str(
                community.get("identity_community_id", "")
            ).strip()
            max_path_text = str(
                community.get("max_candidate_path_length", "")
            ).strip()
            reliable = bool(identity_id)
            reliable = reliable and (
                str(final.get("identity_community_id", "")).strip()
                == identity_id
            )
        max_path = _finite_float(max_path_text) if reliable else None
        removed = max_path is not None and max_path < THRESHOLD
        if removed:
            remove_ids.add(final_id)
        audit_rows.append({
            "final_instance_id": final_id,
            "identity_community_id": identity_id,
            "max_candidate_path_length": max_path_text,
            "removed": "true" if removed else "false",
            "reason": REMOVE_REASON if removed else "",
        })

    filtered = baseline.copy()
    if remove_ids:
        filtered[np.isin(filtered, list(remove_ids))] = 0

    for final_id in set(baseline_ids) - remove_ids:
        if not np.array_equal(filtered == final_id, baseline == final_id):
            raise RuntimeError("保留实例像素发生变化：{}".format(final_id))
    if not np.array_equal(baseline, baseline_before):
        raise RuntimeError("C18B baseline labels在过滤过程中被修改")

    temporary_image = Path(str(filtered_path) + ".tmp.tif")
    if not cv2.imwrite(str(temporary_image), filtered):
        raise IOError("无法写入过滤后labels：{}".format(filtered_path))
    os.replace(str(temporary_image), str(filtered_path))
    _write_audit(audit_path, audit_rows)
    elapsed = time.perf_counter() - started
    return {
        "baseline_labels": str(baseline_path),
        "filtered_labels": str(filtered_path),
        "audit_csv": str(audit_path),
        "baseline_final_count": len(baseline_ids),
        "filtered_final_count": len(baseline_ids) - len(remove_ids),
        "removed_instance_count": len(remove_ids),
        "removed_ids": sorted(remove_ids),
        "surviving_masks_identical": True,
        "filter_seconds": elapsed,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = apply_extreme_fragment_filter(args.directory)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
