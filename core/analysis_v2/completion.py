"""Measurement completion data, independent of Qt, publication and database IO."""

from copy import deepcopy
from pathlib import Path


def build_completion_result(part, payload, context, task_root, elapsed):
    """Snapshot worker output and caller metadata; do not publish candidate files.

    summary_result refers to candidate CSV paths. After publication the caller
    must keep using publication.summary for database rows (the existing rule).
    """
    if part not in ("head", "tail"):
        raise ValueError("Unsupported measurement part: {}".format(part))
    payload = deepcopy(dict(payload or {}))
    context = deepcopy(dict(context or {}))
    measurement = dict(payload.get("measurement_result") or {})
    contract = dict(measurement.get("validation") or {})
    if part == "head":
        summary = dict(measurement.get("parsed_result") or
                       (contract.get("result_parser") or {}).get("image_summary") or {})
        source_key = "measurement_output_dir"
    else:
        summary = dict(contract.get("result_parser") or {})
        source_key = "candidate_output_dir"
        if not summary.get("success"):
            raise ValueError(summary.get("message") or "尾部结果解析失败。")
        if summary.get("calculation_mode") != "head_equivalent":
            raise ValueError("尾部测量未使用 head_equivalent 公式。")
    source = str(payload.get(source_key) or "").strip()
    target = str(context.get("target_output_dir") or "").strip()
    if not source or not target:
        raise ValueError("测量结果缺少 {} 或 target_output_dir。".format(source_key))
    total = dict(summary.get("total") or {})
    completion = {
        "status": "measured", "part": part,
        "task_root": str(task_root or payload.get("task_root") or ""),
        "protein_key": context.get("protein_key"),
        "protein_name": context.get("protein_name"),
        "context": context, "measurement_payload": payload,
        "measurement_result": measurement, "measurement_contract": contract,
        "source_dir": Path(source).resolve(), "target_dir": Path(target).resolve(),
        "expected_field_count": int(context.get("field_count", 0) or 0),
        "elapsed_seconds": float(elapsed or 0.0),
        "summary_result": summary, "total": total,
        "mean_intensity": total.get("mean_intensity", 0),
        "mean_intensity_raw": total.get("mean_intensity_raw", total.get("mean_intensity", 0)),
        "expression_rate": total.get("expression_rate", 0),
    }
    if part == "tail":
        count = contract.get("tail_object_count", contract.get(
            "expected_object_count", total.get("positive_count", 0)))
        associated = total.get("positive_count", 0)
        completion.update({
            "tail_object_count": count,
            "associated_object_count": associated,
            "unresolved_object_count": count - associated,
        })
    return completion
