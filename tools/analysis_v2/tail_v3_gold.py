"""Tail V3 Phase 0 Gold manifest and strict correctness comparator.

This module is a read-only consumer of Analysis V2 run directories.  It is not
imported by the formal analysis workflow and never rewrites run artifacts.
"""

import argparse
import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import numpy as np
import tifffile


TIER1_RUNS = (
    ("ZBFY023-C-1", "CASE20260908102941", "20260908_103450_acad3c"),
    ("ZBFY020-C-1", "CASE20260908103925", "20260908_103952_a64637"),
    ("ZBFY016-C-1", "CASE20260908104300", "20260908_104320_f4c8fc"),
    ("ZBFY022-C-1", "CASE20260908104656", "20260908_104716_af7f2c"),
)

EXPECTED = {
    "ZBFY023-C-1": (98, 77, 65, 12, Decimal("61.2171"), 61, Decimal("66.33")),
    "ZBFY020-C-1": (112, 63, 54, 9, Decimal("23.0846"), 23, Decimal("48.21")),
    "ZBFY016-C-1": (96, 74, 68, 6, Decimal("19.4131"), 19, Decimal("70.83")),
    "ZBFY022-C-1": (140, 104, 92, 12, Decimal("47.8856"), 48, Decimal("65.71")),
}

IMAGE_COLUMNS = (
    "ImageNumber",
    "Count_G_objects",
    "Count_R_objects",
    "Count_R_colocalized",
    "Math_ColocalizationRate",
)
OBJECT_COLUMNS = (
    "ObjectNumber",
    "AreaShape_Area",
    "Math_MeanIntensity255",
)

# These keys describe where or when a JSON was produced.  Every other key,
# including unknown future keys, remains part of the strict business contract.
NON_BUSINESS_JSON_KEYS = frozenset((
    "region_label_path",
    "head_id_label_path",
    "positive_head_label_path",
    "generated_at",
    "created_at",
    "updated_at",
    "timestamp",
    "attempt_path",
    "attempt_dir",
    "log_path",
))


class GoldMismatch(AssertionError):
    pass


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value):
    if isinstance(value, dict):
        return {
            key: _canonical_json(item)
            for key, item in sorted(value.items())
            if key not in NON_BUSINESS_JSON_KEYS
        }
    if isinstance(value, list):
        return [_canonical_json(item) for item in value]
    return value


def _semantic_sha256(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_json(path):
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _read_csv(path, columns):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in columns if column not in (reader.fieldnames or [])]
        if missing:
            raise GoldMismatch("{} 缺少字段：{}".format(path, ", ".join(missing)))
        return [tuple(row[column].strip() for column in columns) for row in reader]


def _decimal(value):
    return Decimal(str(value).strip())


def _run_paths(run_root, field_id):
    root = Path(run_root).resolve()
    tail_dir = root / "calibration" / "tail" / field_id
    c18b_dir = (
        root / "segmentation" / "c18b_score015" / field_id
        / (field_id + "_FITC")
    )
    output_dir = root / "measurement" / "tail" / "candidate_output"
    return {
        "input_fitc": root / "input" / (field_id + "_FITC.tif"),
        "input_tritc": root / "input" / (field_id + "_TRITC.tif"),
        "input_merge": root / "input" / (field_id + "_Merge.tif"),
        "head_final_labels": root / "calibration" / "head" / (field_id + "_HeadFinalLabels.tif"),
        "c18b_final_tail_instances": c18b_dir / "06_final_tail_instances.tif",
        "extreme_fragment_filtered_labels": c18b_dir / "07_extreme_fragment_filtered_labels.tif",
        "tail_final_labels": tail_dir / (field_id + "_TailFinalLabels.tif"),
        "tail_positive_head_labels": tail_dir / (field_id + "_TailPositiveHeadLabels.tif"),
        "tail_final_head_id_labels": tail_dir / (field_id + "_TailFinalHeadIdLabels.tif"),
        "tail_final_objects": tail_dir / (field_id + "_TailFinalObjects.json"),
        "image_csv": output_dir / "Image.csv",
        "g_objects_csv": output_dir / "G_objects.csv",
        "measurement_result": root / "measurement" / "tail" / "tail_measurement_result.json",
    }


def _tiff_record(path):
    image = tifffile.imread(str(path))
    return {
        "sha256": _sha256(path),
        "shape": list(image.shape),
        "dtype": str(image.dtype),
    }


def _assert_equal(actual, expected, label):
    if actual != expected:
        raise GoldMismatch("{} 不一致：actual={!r}, expected={!r}".format(
            label, actual, expected
        ))


def inspect_run(run_root, field_id):
    """Validate one completed run and return its minimal frozen snapshot."""
    paths = _run_paths(run_root, field_id)
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise GoldMismatch("缺少 Gold 文件：{}".format("; ".join(missing)))

    tiff_keys = tuple(key for key in paths if key not in (
        "tail_final_objects", "image_csv", "g_objects_csv", "measurement_result"
    ))
    arrays = {key: tifffile.imread(str(paths[key])) for key in tiff_keys}
    final_labels = arrays["tail_final_labels"]
    positive_labels = arrays["tail_positive_head_labels"]
    head_id_labels = arrays["tail_final_head_id_labels"]
    for key in ("tail_positive_head_labels", "tail_final_head_id_labels"):
        _assert_equal(arrays[key].shape, final_labels.shape, key + " shape")

    objects_raw = _read_json(paths["tail_final_objects"])
    objects_semantic = _canonical_json(objects_raw)
    objects = list(objects_raw.get("objects") or [])
    tail_count = int(objects_raw.get("tail_object_count", -1))
    associated = int(objects_raw.get("associated_object_count", -1))
    unresolved = int(objects_raw.get("unresolved_object_count", -1))
    _assert_equal(len(objects), tail_count, field_id + " TailFinalObjects rows")
    _assert_equal(associated + unresolved, tail_count, field_id + " association totals")
    _assert_equal(
        [int(row.get("tail_object_id", -1)) for row in objects],
        list(range(1, tail_count + 1)),
        field_id + " tail_object_id",
    )
    _assert_equal(
        sorted(int(value) for value in np.unique(final_labels) if int(value) > 0),
        list(range(1, tail_count + 1)),
        field_id + " TailFinalLabels IDs",
    )
    associated_ids = sorted(
        int(row["tail_object_id"])
        for row in objects
        if row.get("association_status") == "associated"
    )
    _assert_equal(len(associated_ids), associated, field_id + " associated rows")
    _assert_equal(
        sorted(int(value) for value in np.unique(positive_labels) if int(value) > 0),
        associated_ids,
        field_id + " TailPositiveHeadLabels IDs",
    )

    image_rows = _read_csv(paths["image_csv"], IMAGE_COLUMNS)
    _assert_equal(len(image_rows), 1, field_id + " Image.csv rows")
    object_rows = _read_csv(paths["g_objects_csv"], OBJECT_COLUMNS)
    _assert_equal(len(object_rows), tail_count, field_id + " G_objects.csv rows")
    object_by_id = {}
    for row in object_rows:
        object_id = int(_decimal(row[0]))
        if object_id in object_by_id:
            raise GoldMismatch("{} G_objects.csv ObjectNumber 重复：{}".format(
                field_id, object_id
            ))
        object_by_id[object_id] = row
    _assert_equal(sorted(object_by_id), list(range(1, tail_count + 1)),
                  field_id + " G_objects.csv ObjectNumber")
    label_areas = np.bincount(final_labels.reshape(-1), minlength=tail_count + 1)
    for row in objects:
        object_id = int(row["tail_object_id"])
        pixel_count = int(label_areas[object_id])
        _assert_equal(pixel_count, int(row["pixel_count"]),
                      "{} object {} JSON pixel_count".format(field_id, object_id))
        _assert_equal(Decimal(pixel_count), _decimal(object_by_id[object_id][1]),
                      "{} object {} CSV area".format(field_id, object_id))

    result = _read_json(paths["measurement_result"])
    total = dict((result.get("result_parser") or {}).get("total") or {})
    head_count = int(total.get("sperm_count", -1))
    _assert_equal(
        len([value for value in np.unique(arrays["head_final_labels"]) if int(value) > 0]),
        head_count,
        field_id + " head_count",
    )
    values = {
        "head_count": head_count,
        "tail_count": tail_count,
        "associated": associated,
        "unresolved": unresolved,
        "fluorescence_raw": str(total.get("mean_intensity_raw")),
        "fluorescence_display": int(total.get("mean_intensity", -1)),
        "expression": str(total.get("expression_rate")),
    }
    if field_id in EXPECTED:
        expected = EXPECTED[field_id]
        actual = (
            head_count, tail_count, associated, unresolved,
            _decimal(values["fluorescence_raw"]),
            values["fluorescence_display"],
            _decimal(values["expression"]),
        )
        _assert_equal(actual, expected, field_id + " formal expected values")

    artifacts = {}
    for key, path in paths.items():
        record = {"sha256": _sha256(path)}
        if key in tiff_keys:
            record.update({"shape": list(arrays[key].shape), "dtype": str(arrays[key].dtype)})
        artifacts[key] = record
    artifacts["tail_final_objects"]["business_semantic_sha256"] = _semantic_sha256(
        objects_semantic
    )
    artifacts["image_csv"]["selected_semantic_sha256"] = _semantic_sha256(image_rows)
    artifacts["g_objects_csv"]["selected_semantic_sha256"] = _semantic_sha256(object_rows)
    return {
        "field_id": field_id,
        "run_root": str(Path(run_root).resolve()),
        "values": values,
        "artifacts": artifacts,
    }


def compare_runs(baseline_run, candidate_run, field_id):
    """Strictly compare all Phase 0 business outputs for one field."""
    baseline_paths = _run_paths(baseline_run, field_id)
    candidate_paths = _run_paths(candidate_run, field_id)
    baseline = inspect_run(baseline_run, field_id)
    candidate = inspect_run(candidate_run, field_id)
    _assert_equal(candidate["values"], baseline["values"], field_id + " final values")

    array_keys = (
        "head_final_labels",
        "c18b_final_tail_instances",
        "extreme_fragment_filtered_labels",
        "tail_final_labels",
        "tail_positive_head_labels",
        "tail_final_head_id_labels",
    )
    for key in array_keys:
        left = tifffile.imread(str(baseline_paths[key]))
        right = tifffile.imread(str(candidate_paths[key]))
        _assert_equal(right.shape, left.shape, field_id + " " + key + " shape")
        _assert_equal(str(right.dtype), str(left.dtype), field_id + " " + key + " dtype")
        if not np.array_equal(right, left):
            raise GoldMismatch("{} {} 像素不完全一致".format(field_id, key))

    for key in ("input_fitc", "input_tritc", "input_merge"):
        _assert_equal(_sha256(candidate_paths[key]), _sha256(baseline_paths[key]),
                      field_id + " " + key + " SHA256")
    _assert_equal(
        _canonical_json(_read_json(candidate_paths["tail_final_objects"])),
        _canonical_json(_read_json(baseline_paths["tail_final_objects"])),
        field_id + " TailFinalObjects business semantics",
    )
    _assert_equal(
        _read_csv(candidate_paths["image_csv"], IMAGE_COLUMNS),
        _read_csv(baseline_paths["image_csv"], IMAGE_COLUMNS),
        field_id + " Image.csv selected fields",
    )
    baseline_rows = _read_csv(baseline_paths["g_objects_csv"], OBJECT_COLUMNS)
    candidate_rows = _read_csv(candidate_paths["g_objects_csv"], OBJECT_COLUMNS)
    baseline_by_id = {int(_decimal(row[0])): row for row in baseline_rows}
    candidate_by_id = {int(_decimal(row[0])): row for row in candidate_rows}
    _assert_equal(candidate_by_id, baseline_by_id,
                  field_id + " G_objects.csv field + ObjectNumber")
    return {"field_id": field_id, "equal": True}


def freeze_tier1(project_root, output_path):
    root = Path(project_root).resolve()
    fields = []
    for field_id, case_no, run_id in TIER1_RUNS:
        run_root = root / "workspace" / "cases" / case_no / "analysis_v2" / "protein3" / "runs" / run_id
        snapshot = inspect_run(run_root, field_id)
        snapshot["case_no"] = case_no
        snapshot["run_id"] = run_id
        snapshot["run_root"] = str(run_root.relative_to(root)).replace("\\", "/")
        fields.append(snapshot)
    manifest = {
        "schema_version": 1,
        "benchmark": "Tail V3 Phase 0 Tier1 Gold",
        "code_commit": _git_head(root),
        "comparison": {
            "arrays": "shape + dtype + numpy.array_equal",
            "json": "all keys except explicit non-business key allowlist",
            "ignored_json_keys": sorted(NON_BUSINESS_JSON_KEYS),
            "csv": "selected columns, field + ObjectNumber strict alignment",
            "float_policy": "exact decimal value; no tolerance",
        },
        "field_count": len(fields),
        "object_count": sum(item["values"]["tail_count"] for item in fields),
        "fields": fields,
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _git_head(root):
    head = root / ".git" / "HEAD"
    value = head.read_text(encoding="ascii").strip()
    if value.startswith("ref: "):
        value = (root / ".git" / value[5:]).read_text(encoding="ascii").strip()
    return value


def verify_manifest(project_root, manifest_path):
    root = Path(project_root).resolve()
    manifest = _read_json(manifest_path)
    for frozen in manifest.get("fields", []):
        current = inspect_run(root / frozen["run_root"], frozen["field_id"])
        _assert_equal(current["values"], frozen["values"], frozen["field_id"] + " values")
        _assert_equal(current["artifacts"], frozen["artifacts"], frozen["field_id"] + " artifacts")
    _assert_equal(sum(item["values"]["tail_count"] for item in manifest["fields"]),
                  int(manifest.get("object_count", -1)), "manifest object_count")
    return {"field_count": len(manifest["fields"]), "object_count": manifest["object_count"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[2]))
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--output", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", required=True)
    compare = subparsers.add_parser("compare")
    compare.add_argument("--baseline-run", required=True)
    compare.add_argument("--candidate-run", required=True)
    compare.add_argument("--field-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "freeze":
        result = freeze_tier1(args.project_root, args.output)
    elif args.command == "verify":
        result = verify_manifest(args.project_root, args.manifest)
    else:
        result = compare_runs(args.baseline_run, args.candidate_run, args.field_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
