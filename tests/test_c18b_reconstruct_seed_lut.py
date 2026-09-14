"""Exact seed-label mapping contract for region-indexed reconstruction."""
import importlib
import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np


C18B_DIR = Path(__file__).resolve().parents[1] / "tools" / "analysis_v2" / "c18b_score015"
sys.path.insert(0, str(C18B_DIR))
try:
    GEOMETRY = importlib.import_module("graph_constrained_instance_separation")
finally:
    sys.path.remove(str(C18B_DIR))


def old_mapping(path, shape):
    """Frozen pre-P3A-2 implementation, retained only as a test oracle."""
    seed = np.zeros(shape, np.uint8)
    tangent_x = np.zeros(shape, np.float32)
    tangent_y = np.zeros(shape, np.float32)
    for k, (x, y) in enumerate(path):
        if not (0 <= x < shape[1] and 0 <= y < shape[0]):
            continue
        a = path[max(0, k - 6)].astype(float)
        b = path[min(len(path) - 1, k + 6)].astype(float)
        v = b - a
        norm = max(float(np.linalg.norm(v)), 1.)
        seed[y, x] = 1
        tangent_x[y, x], tangent_y[y, x] = v / norm
    distance, nearest = GEOMETRY.cv2.distanceTransformWithLabels(
        1 - seed, GEOMETRY.cv2.DIST_L2, 5,
        labelType=GEOMETRY.cv2.DIST_LABEL_PIXEL)
    ys, xs = np.where(seed)
    order = np.lexsort((xs, ys))
    ys, xs = ys[order], xs[order]
    return seed, tangent_x, tangent_y, distance, nearest, ys, xs


def old_region(path, shape, region_y, region_x):
    seed, tx, ty, distance, nearest, ys, xs = old_mapping(path, shape)
    lut_x = np.zeros(len(xs) + 1, np.float32)
    lut_y = np.zeros(len(xs) + 1, np.float32)
    lut_tx = np.zeros(len(xs) + 1, np.float32)
    lut_ty = np.zeros(len(xs) + 1, np.float32)
    lut_x[1:], lut_y[1:] = xs, ys
    lut_tx[1:], lut_ty[1:] = tx[ys, xs], ty[ys, xs]
    nearest_region = nearest[region_y, region_x]
    np.minimum(nearest_region, len(xs), out=nearest_region)
    distance_region = distance[region_y, region_x]
    x = region_x.astype(np.float32, copy=False)
    y = region_y.astype(np.float32, copy=False)
    vx = x - lut_x[nearest_region]
    vy = y - lut_y[nearest_region]
    align = (np.abs(vx * lut_tx[nearest_region] + vy * lut_ty[nearest_region]) /
             np.maximum(distance_region, 1.))
    np.clip(align, 0., 1., out=align)
    return distance_region, align, seed


def paths():
    shape = (23, 31)
    boundary_duplicates = np.asarray([
        [30, 22], [0, 0], [30, 0], [0, 22], [12, 9], [30, 22],
        [7, 18], [12, 9], [-1, 3], [31, 5], [0, 0], [14, 3],
    ], np.int32)
    rng = np.random.RandomState(6024)
    random_path = np.column_stack((rng.randint(0, shape[1], 90),
                                   rng.randint(0, shape[0], 90))).astype(np.int32)
    return shape, (boundary_duplicates, random_path)


def test_old_contract_row_major_unique_last_write_and_label_mapping():
    shape, cases = paths()
    for path in cases:
        seed, tx, ty, _, labels, ys, xs = old_mapping(path, shape)
        raw_y, raw_x = np.where(seed)
        np.testing.assert_array_equal(ys, raw_y)
        np.testing.assert_array_equal(xs, raw_x)
        assert len(ys) == len(set((int(x), int(y)) for x, y in path
                                  if 0 <= x < shape[1] and 0 <= y < shape[0]))
        assert np.all(seed[ys, xs] == 1)
        np.testing.assert_array_equal(labels[ys, xs], np.arange(1, len(xs) + 1))
        for k, (x, y) in enumerate(path):
            if not (0 <= x < shape[1] and 0 <= y < shape[0]):
                continue
            last = max(i for i, pair in enumerate(path) if np.array_equal(pair, [x, y]))
            a = path[max(0, last - 6)].astype(float)
            b = path[min(len(path) - 1, last + 6)].astype(float)
            v = b - a
            expected = (v / max(float(np.linalg.norm(v)), 1.)).astype(np.float32)
            np.testing.assert_array_equal([tx[y, x], ty[y, x]], expected)
        assert np.all(labels >= 1)
        assert np.all(labels <= len(xs))


def test_new_region_matches_old_reference_exact():
    shape, cases = paths()
    region_y, region_x = np.indices(shape)
    for path in cases:
        expected = old_region(path, shape, region_y.ravel(), region_x.ravel())
        actual = GEOMETRY._path_geometry_region(path, shape, region_y.ravel(), region_x.ravel())
        for old, new in zip(expected, actual):
            np.testing.assert_array_equal(old, new)


def test_new_lut_coordinates_and_tangents_match_old_full_arrays():
    shape, cases = paths()
    region_y, region_x = np.indices(shape)
    original_unique = np.unique
    for path in cases:
        seed, tx, ty, _, labels, ys, xs = old_mapping(path, shape)
        captured = []

        def capture_unique(*args, **kwargs):
            result = original_unique(*args, **kwargs)
            captured.append(result)
            return result

        with mock.patch.object(GEOMETRY.np, "unique", side_effect=capture_unique) as unique:
            GEOMETRY._path_geometry_region(path, shape,
                                           region_y.ravel(), region_x.ravel())
        assert unique.call_count == 1
        linear_reversed = unique.call_args[0][0]
        unique_linear, reverse_first = captured[0]
        expected_linear = ys * shape[1] + xs
        np.testing.assert_array_equal(unique_linear, expected_linear)
        valid_reversed = [int(y) * shape[1] + int(x) for x, y in path[::-1]
                          if 0 <= x < shape[1] and 0 <= y < shape[0]]
        np.testing.assert_array_equal(linear_reversed, valid_reversed)
        last_write = len(linear_reversed) - 1 - reverse_first
        valid_path = [(k, int(x), int(y)) for k, (x, y) in enumerate(path)
                      if 0 <= x < shape[1] and 0 <= y < shape[0]]
        selected = [valid_path[int(k)] for k in last_write]
        np.testing.assert_array_equal([(x, y) for _, x, y in selected],
                                      np.column_stack((xs, ys)))
        for k, x, y in selected:
            a = path[max(0, k - 6)].astype(float)
            b = path[min(len(path) - 1, k + 6)].astype(float)
            v = b - a
            compact = (v / max(float(np.linalg.norm(v)), 1.)).astype(np.float32)
            np.testing.assert_array_equal(compact, [tx[y, x], ty[y, x]])
        np.testing.assert_array_equal(labels[ys, xs],
                                      np.arange(1, len(xs) + 1))
        np.testing.assert_array_equal(seed[ys, xs], np.ones(len(xs), np.uint8))


def formal_probe(field, compare_mapping=False):
    """Run one independent C18B field and compare both label files to Gold."""
    cases = {
        "023": ("CASE20260908102941", "20260908_103450_acad3c"),
        "022": ("CASE20260908104656", "20260908_104716_af7f2c"),
    }
    case, run = cases[field]
    root = Path(__file__).resolve().parents[1]
    field_id = "ZBFY{}-C-1".format(field)
    old = (root / "workspace" / "cases" / case / "analysis_v2" / "protein3" /
           "runs" / run / "segmentation" / "c18b_score015" / field_id /
           (field_id + "_FITC"))
    input_path = (root / "workspace" / "cases" / case / "analysis_v2" /
                  "protein3" / "runs" / run / "input" / (field_id + "_FITC.tif"))
    sys.path.insert(0, str(C18B_DIR))
    try:
        pipeline = importlib.import_module("run_pipeline")
        identity = importlib.import_module("identity_graph_v3")
        filter_module = importlib.import_module("extreme_fragment_filter")
    finally:
        sys.path.remove(str(C18B_DIR))
    original = identity._path_geometry_region
    compared = [0]

    def checked_region(path, shape, region_y, region_x):
        actual = original(path, shape, region_y, region_x)
        expected = old_region(path, shape, region_y, region_x)
        for old_array, new_array in zip(expected, actual):
            np.testing.assert_array_equal(old_array, new_array)
        compared[0] += 1
        return actual

    cfg = json.loads((C18B_DIR / "config" / "frozen_parameters.json").read_text(
        encoding="utf-8"))
    output_root = Path(tempfile.mkdtemp(prefix="p3a2_{}_".format(field),
                                        dir=str(root / "workspace")))
    if compare_mapping:
        identity._path_geometry_region = checked_region
    try:
        output, stats = pipeline.run_one(input_path, output_root, cfg,
                                         candidate_path_mode="graph_preserving")
    finally:
        identity._path_geometry_region = original
    filter_module.apply_extreme_fragment_filter(output)
    hashes = {}
    for name in ("06_final_tail_instances.tif", "07_extreme_fragment_filtered_labels.tif"):
        def sha(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()
        actual_hash = sha(output / name)
        expected_hash = sha(old / name)
        assert actual_hash == expected_hash, "{} mismatch: {}".format(field_id, name)
        hashes[name[:2]] = actual_hash
    timing = json.loads((output / "timing.json").read_text(encoding="utf-8"))
    print(json.dumps({"field": field, "output": str(output), "stats": stats,
                      "mapping_calls": compared[0], "hashes": hashes,
                      "timing": timing["stages_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal", choices=("023", "022"))
    parser.add_argument("--compare-mapping", action="store_true")
    args = parser.parse_args()
    if args.formal:
        formal_probe(args.formal, args.compare_mapping)
    else:
        test_old_contract_row_major_unique_last_write_and_label_mapping()
        test_new_region_matches_old_reference_exact()
        test_new_lut_coordinates_and_tangents_match_old_full_arrays()
        print("3 targeted tests PASS")
