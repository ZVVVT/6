"""Strict bbox pruning preserves the distance-based association contract."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import types

import cv2
import numpy as np
try:
    import pytest
except ImportError:
    class _Mark:
        @staticmethod
        def parametrize(*args, **kwargs):
            return lambda function: function

        @staticmethod
        def skipif(*args, **kwargs):
            return lambda function: function

    class _PytestStub:
        mark = _Mark()

    pytest = _PytestStub()


ADAPTER = Path(__file__).resolve().parents[1] / "tools" / "analysis_v2" / "c18b_tail_editor_adapter.py"
spec = importlib.util.spec_from_file_location("c18b_tail_bbox_test", ADAPTER)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def _run(monkeypatch, tail_yx, head_yx, threshold, overlap=False):
    shape = (220, 220)
    instances = np.zeros(shape, dtype=np.uint16)
    heads = np.zeros(shape, dtype=np.uint16)
    instances[tail_yx] = 1
    heads[head_yx] = 1
    calls = []
    original = adapter.nearest_distances

    def counted(query, target, *args, **kwargs):
        calls.append((query.copy(), target.copy()))
        return original(query, target, *args, **kwargs)

    monkeypatch.setattr(adapter, "nearest_distances", counted)
    try:
        result = adapter.match_instances(instances, heads, 0, threshold)
    finally:
        monkeypatch.setattr(adapter, "nearest_distances", original)
    return result, calls


@pytest.mark.parametrize("offset,expected_calls,matched", [
    ((0, 1), 1, True), ((0, 80), 1, True), ((0, 81), 0, False),
    ((48, 64), 1, True), ((49, 64), 0, False),
])
def test_strict_pixel_center_distance(monkeypatch, offset, expected_calls, matched):
    result, calls = _run(monkeypatch, (20, 20),
                         (20 + offset[0], 20 + offset[1]), 80.0)
    assert len(calls) == expected_calls
    assert bool(result[0]) is matched
    if matched:
        assert result[0][0]["matching_distance_px"] == float(np.hypot(*offset))
        assert calls[0][0].dtype == np.float32
        assert calls[0][1].dtype == np.int64


@pytest.mark.parametrize("threshold,expected_calls", [
    (float("nan"), 1), (float("inf"), 1), (-1.0, 1),
    (80.0, 0), (100.0, 1),
])
def test_threshold_fast_path_only_when_safe(monkeypatch, threshold, expected_calls):
    result, calls = _run(monkeypatch, (20, 20), (20, 101), threshold)
    assert len(calls) == expected_calls
    assert bool(result[0]) == bool(threshold == 100.0 or np.isposinf(threshold))


def test_overlap_always_uses_distance(monkeypatch):
    result, calls = _run(monkeypatch, (20, 20), (20, 20), -1.0)
    assert len(calls) == 1
    assert result[0][0]["matching_method"] == "dilated_overlap_20px"


def test_unconvertible_threshold_preserves_overlap_old_path(monkeypatch):
    result, calls = _run(monkeypatch, (20, 20), (20, 20), "invalid")
    assert len(calls) == 1
    assert result[0][0]["matching_method"] == "dilated_overlap_20px"


def test_overlap_bypasses_even_far_bbox(monkeypatch):
    original_dilate = adapter.cv2.dilate

    def all_overlapping(image, kernel, iterations=1):
        return np.ones_like(image)

    monkeypatch.setattr(adapter.cv2, "dilate", all_overlapping)
    result, calls = _run(monkeypatch, (20, 20), (20, 101), 80.0)
    assert len(calls) == 1
    assert result[0][0]["matching_method"] == "dilated_overlap_20px"
    monkeypatch.setattr(adapter.cv2, "dilate", original_dilate)


def test_empty_query_keeps_existing_numpy_error(monkeypatch):
    monkeypatch.setattr(adapter, "positive_ids", lambda labels: [1])
    with pytest.raises(ValueError, match="zero-size array to reduction operation minimum"):
        adapter.match_instances(np.zeros((3, 3), dtype=np.uint16),
                                np.array([[1, 0, 0], [0, 0, 0], [0, 0, 0]], dtype=np.uint16),
                                0, 80.0)


def test_empty_target_keeps_existing_distance_error(monkeypatch):
    original_erode = adapter.cv2.erode
    count = [0]

    def empty_head_boundary(image, kernel, iterations=1):
        count[0] += 1
        return image if count[0] == 1 else original_erode(image, kernel, iterations=iterations)

    monkeypatch.setattr(adapter.cv2, "erode", empty_head_boundary)
    instances = np.array([[0, 1], [0, 0]], dtype=np.uint16)
    heads = np.array([[1, 0], [0, 0]], dtype=np.uint16)
    with pytest.raises(ValueError, match="最近距离计算缺少目标像素"):
        adapter.match_instances(instances, heads, 0, 80.0)


def test_bbox_uses_final_endpoint_array(monkeypatch):
    class XImgProc:
        @staticmethod
        def thinning(image):
            skeleton = np.zeros_like(image)
            skeleton[20, 20:102] = 255
            return skeleton

    monkeypatch.setattr(adapter.cv2, "ximgproc", XImgProc(), raising=False)
    result, calls = _run(monkeypatch, (20, 20), (20, 101), 80.0)
    assert len(calls) == 1
    assert len(calls[0][0]) > 1
    assert result[0]


def test_bbox_point_arrays_use_signed_integer_arithmetic():
    tail = np.array([[20, 20]], dtype=np.float32)
    head = np.array([[69, 84]], dtype=np.int64)
    assert adapter._point_bbox(tail) == (20, 20, 20, 20)
    assert adapter._point_bbox(head) == (69, 69, 84, 84)


@pytest.mark.skipif(os.environ.get("C18B_A1_REAL_GATE") != "1",
                    reason="explicit four-field runtime gate")
@pytest.mark.parametrize("field,case,run,expected_all,expected_pruned,expected_candidates", [
    ("023", "CASE20260908102941", "20260908_103450_acad3c", 7546, 7296, 192),
    ("020", "CASE20260908103925", "20260908_103952_a64637", 7056, 6831, 188),
    ("016", "CASE20260908104300", "20260908_104320_f4c8fc", 7104, 6822, 242),
    ("022", "CASE20260908104656", "20260908_104716_af7f2c", 14560, 14035, 435),
])
def test_real_field_old_new_exact(field, case, run, expected_all,
                                  expected_pruned, expected_candidates):
    root = ADAPTER.parents[2]
    run_root = root / "workspace" / "cases" / case / "analysis_v2" / "protein3" / "runs" / run
    field_id = "ZBFY{}-C-1".format(field)
    instances_path = (run_root / "segmentation" / "c18b_score015" / field_id /
                      (field_id + "_FITC") / "07_extreme_fragment_filtered_labels.tif")
    head_path = run_root / "calibration" / "head" / (field_id + "_HeadFinalLabels.tif")
    instances = cv2.imread(str(instances_path), cv2.IMREAD_UNCHANGED)
    heads = cv2.imread(str(head_path), cv2.IMREAD_UNCHANGED)
    assert instances is not None and heads is not None
    source = subprocess.check_output(
        ["git", "show", "HEAD:tools/analysis_v2/c18b_tail_editor_adapter.py"],
        cwd=str(root), encoding="utf-8")
    old = types.ModuleType("old_c18b_tail_adapter")
    exec(compile(source, str(ADAPTER), "exec"), old.__dict__)

    def measure(module):
        distances = []
        proposals = []
        original_distance = module.nearest_distances
        original_assignment = module.maximum_weight_assignment

        def counted(query, target, *args, **kwargs):
            result = original_distance(query, target, *args, **kwargs)
            distances.append((module._point_bbox(query) if len(query) else None,
                              module._point_bbox(target) if len(target) else None,
                              float(np.min(result))))
            return result

        def captured(scores):
            import sys
            rows = sys._getframe(1).f_locals["proposals_by_instance"]
            proposals.extend(row.copy() for iid in sorted(rows) for row in rows[iid])
            return original_assignment(scores)

        if not hasattr(module, "_point_bbox"):
            module._point_bbox = adapter._point_bbox
        module.nearest_distances = counted
        module.maximum_weight_assignment = captured
        started = time.perf_counter()
        try:
            matched, unmatched = module.match_instances(instances, heads, 20, 80.0)
        finally:
            module.nearest_distances = original_distance
            module.maximum_weight_assignment = original_assignment
        return time.perf_counter() - started, distances, proposals, matched, unmatched

    old_time, old_distances, old_proposals, old_matched, old_unmatched = measure(old)
    new_time, new_distances, new_proposals, new_matched, new_unmatched = measure(adapter)
    prunable = []
    for tail_bbox, head_bbox, distance in old_distances:
        dy = max(0, head_bbox[0] - tail_bbox[1], tail_bbox[0] - head_bbox[1])
        dx = max(0, head_bbox[2] - tail_bbox[3], tail_bbox[2] - head_bbox[3])
        if dx * dx + dy * dy > 80.0 * 80.0:
            prunable.append(distance)
    assert len(old_distances) == expected_all
    assert len(prunable) == expected_pruned
    assert all(distance > 80.0 for distance in prunable)
    assert len(old_distances) - len(new_distances) == expected_pruned
    assert len(old_proposals) == len(new_proposals) == expected_candidates
    assert old_proposals == new_proposals
    assert old_matched == new_matched
    assert old_unmatched == new_unmatched
    print("A1 {} all={} pruned={} candidates={} old={:.3f}s new={:.3f}s".format(
        field, len(old_distances), len(old_distances)-len(new_distances),
        len(old_proposals), old_time, new_time))


def _direct_ab():
    root = ADAPTER.parents[2]
    source = subprocess.check_output(
        ["git", "show", "HEAD:tools/analysis_v2/c18b_tail_editor_adapter.py"],
        cwd=str(root), encoding="utf-8")
    old = types.ModuleType("old_c18b_tail_ab")
    exec(compile(source, str(ADAPTER), "exec"), old.__dict__)
    order = ("OLD", "NEW", "NEW", "OLD", "OLD", "NEW")
    for field, case, run in (
        ("023", "CASE20260908102941", "20260908_103450_acad3c"),
        ("022", "CASE20260908104656", "20260908_104716_af7f2c"),
    ):
        run_root = root / "workspace" / "cases" / case / "analysis_v2" / "protein3" / "runs" / run
        field_id = "ZBFY{}-C-1".format(field)
        instances = cv2.imread(str(run_root / "segmentation" / "c18b_score015" /
                                   field_id / (field_id + "_FITC") /
                                   "07_extreme_fragment_filtered_labels.tif"), cv2.IMREAD_UNCHANGED)
        heads = cv2.imread(str(run_root / "calibration" / "head" /
                               (field_id + "_HeadFinalLabels.tif")), cv2.IMREAD_UNCHANGED)
        assert instances is not None and heads is not None
        times = {"OLD": [], "NEW": []}
        reference = None
        with tempfile.TemporaryDirectory(prefix="c18b_a1_ab_") as temp_dir:
            for index, version in enumerate(order):
                module = old if version == "OLD" else adapter
                started = time.perf_counter()
                matched, unmatched = module.match_instances(instances, heads, 20, 80.0)
                elapsed = time.perf_counter() - started
                payload = {"matched": matched, "unmatched": unmatched}
                if reference is None:
                    reference = payload
                assert payload == reference
                output = Path(temp_dir) / "{}_{}_{}.json".format(field, index, version)
                output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                times[version].append(elapsed)
                print("AB {} {} run{} {:.3f}s exact".format(field, version,
                                                            len(times[version]), elapsed), flush=True)
        old_median = float(np.median(times["OLD"]))
        new_median = float(np.median(times["NEW"]))
        print("AB_RESULT {} OLD={} median={:.3f} range={:.3f} NEW={} median={:.3f} range={:.3f} saved={:.3f} improvement={:.1f}%".format(
            field, [round(x, 3) for x in times["OLD"]], old_median,
            max(times["OLD"]) - min(times["OLD"]),
            [round(x, 3) for x in times["NEW"]], new_median,
            max(times["NEW"]) - min(times["NEW"]),
            old_median - new_median, 100.0 * (old_median - new_median) / old_median), flush=True)


def _adapter_exact():
    root = ADAPTER.parents[2]
    source = subprocess.check_output(
        ["git", "show", "HEAD:tools/analysis_v2/c18b_tail_editor_adapter.py"],
        cwd=str(root), encoding="utf-8")
    old = types.ModuleType("old_c18b_tail_adapter_exact")
    exec(compile(source, str(ADAPTER), "exec"), old.__dict__)
    for field, case, run in (
        ("023", "CASE20260908102941", "20260908_103450_acad3c"),
        ("020", "CASE20260908103925", "20260908_103952_a64637"),
        ("016", "CASE20260908104300", "20260908_104320_f4c8fc"),
        ("022", "CASE20260908104656", "20260908_104716_af7f2c"),
    ):
        run_root = root / "workspace" / "cases" / case / "analysis_v2" / "protein3" / "runs" / run
        field_id = "ZBFY{}-C-1".format(field)
        inputs = (
            run_root / "segmentation" / "c18b_score015" / field_id /
            (field_id + "_FITC") / "07_extreme_fragment_filtered_labels.tif",
            run_root / "calibration" / "head" / (field_id + "_HeadFinalLabels.tif"),
            run_root / "input" / (field_id + "_FITC.tif"),
            run_root / "input" / (field_id + "_Merge.tif"),
            run_root / "segmentation" / "c18b_runner_contract" / field_id /
            "02_probability_uint16.tif",
        )
        assert all(path.is_file() for path in inputs)
        with tempfile.TemporaryDirectory(prefix="c18b_a1_adapter_") as temp_dir:
            old_dir = Path(temp_dir) / "old"
            new_dir = Path(temp_dir) / "new"
            old_manifest = old.run_adapter(*inputs, old_dir)
            new_manifest = adapter.run_adapter(*inputs, new_dir)
            for name in ("fragments.tif", "probability.tif"):
                assert np.array_equal(cv2.imread(str(old_dir / name), cv2.IMREAD_UNCHANGED),
                                      cv2.imread(str(new_dir / name), cv2.IMREAD_UNCHANGED))
            for name in ("entries.json", "paths.json", "global_results.json",
                         "unassigned_tail_candidates.json"):
                assert json.loads((old_dir / name).read_text(encoding="utf-8")) == json.loads(
                    (new_dir / name).read_text(encoding="utf-8"))
            assert old_manifest["matching"] == new_manifest["matching"]
            assert old_manifest["validation"] == new_manifest["validation"]
        print("ADAPTER_EXACT {} PASS".format(field), flush=True)


if __name__ == "__main__":
    if os.environ.get("C18B_A1_AB") == "1":
        _direct_ab()
    elif os.environ.get("C18B_A1_ADAPTER_EXACT") == "1":
        _adapter_exact()
    else:
        for row in (
            ("023", "CASE20260908102941", "20260908_103450_acad3c", 7546, 7296, 192),
            ("020", "CASE20260908103925", "20260908_103952_a64637", 7056, 6831, 188),
            ("016", "CASE20260908104300", "20260908_104320_f4c8fc", 7104, 6822, 242),
            ("022", "CASE20260908104656", "20260908_104716_af7f2c", 14560, 14035, 435),
        ):
            test_real_field_old_new_exact(*row)
