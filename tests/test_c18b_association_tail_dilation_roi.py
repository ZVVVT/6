"""Tail dilation ROI preserves the full-frame overlap contract exactly.

The OLD oracle of this suite is pinned to the adapter revision *before* the
ROI dilation optimisation (``25e40f5``).  See ``_legacy_adapter_source``.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import types
import unittest

import cv2
import numpy as np


ADAPTER = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "analysis_v2"
    / "c18b_tail_editor_adapter.py"
)
SPEC = importlib.util.spec_from_file_location("c18b_tail_dilation_roi_test", ADAPTER)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)

ADAPTER_REPO_PATH = "tools/analysis_v2/c18b_tail_editor_adapter.py"
OLD_ADAPTER_COMMIT = "25e40f5"
OLD_ADAPTER_BLOB = "e400399c41d54b085d6ef49de69f23bdde16f1ee"


def _source_digest(source):
    """SHA256 of adapter source text with normalised line endings."""
    return hashlib.sha256(source.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _legacy_adapter_source():
    """Return the pinned pre-A2 adapter source used as the OLD oracle.

    The oracle must stay on a fixed revision: an oracle read from ``HEAD``
    turns the comparison into NEW-vs-NEW as soon as the optimisation lands,
    which both hides regressions and can produce stale false failures.  Any
    git failure (missing executable, unreachable commit, missing blob, blob
    that no longer matches ``commit:path``) is a hard failure - never a skip
    and never a fallback to workspace or ``HEAD`` source.
    """
    root = str(ADAPTER.parents[2])
    revision = "{}:{}".format(OLD_ADAPTER_COMMIT, ADAPTER_REPO_PATH)
    try:
        actual_blob = subprocess.check_output(
            ["git", "rev-parse", revision],
            cwd=root, encoding="utf-8", stderr=subprocess.STDOUT,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise AssertionError(
            "legacy association oracle unavailable: git rev-parse {} failed: {}"
            .format(revision, error))
    if actual_blob != OLD_ADAPTER_BLOB:
        raise AssertionError(
            "legacy association oracle blob mismatch for {}: expected {} got {}"
            .format(revision, OLD_ADAPTER_BLOB, actual_blob))
    try:
        source = subprocess.check_output(
            ["git", "cat-file", "blob", OLD_ADAPTER_BLOB],
            cwd=root, encoding="utf-8", stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise AssertionError(
            "legacy association oracle unavailable: git cat-file blob {} failed: {}"
            .format(OLD_ADAPTER_BLOB, error))
    if _source_digest(source) == _source_digest(ADAPTER.read_text(encoding="utf-8")):
        raise AssertionError(
            "legacy oracle unexpectedly matches current adapter: {} == {}"
            .format(revision, ADAPTER))
    return source


def _legacy_module(name):
    """Execute the pinned pre-A2 adapter source as a standalone module."""
    module = types.ModuleType(name)
    exec(compile(_legacy_adapter_source(), str(ADAPTER), "exec"), module.__dict__)
    return module


def _old_result(instances, heads, tail_id, radius=20):
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1)
    )
    dilated = cv2.dilate(
        (instances == tail_id).astype(np.uint8), kernel, iterations=1
    ) > 0
    overlapping = heads[dilated]
    overlapping = overlapping[overlapping > 0].astype(np.int64, copy=False)
    if not overlapping.size:
        return dilated, [], []
    ids, counts = np.unique(overlapping, return_counts=True)
    return dilated, ids.tolist(), counts.tolist()


def _roi_result(instances, heads, tail_id, bbox, radius=20):
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1)
    )
    y0, y1, x0, x1 = bbox
    ry0 = max(0, y0 - radius)
    ry1 = min(instances.shape[0], y1 + radius)
    rx0 = max(0, x0 - radius)
    rx1 = min(instances.shape[1], x1 + radius)
    local = (instances[ry0:ry1, rx0:rx1] == tail_id).astype(np.uint8)
    local_dilated = cv2.dilate(local, kernel, iterations=1) > 0
    restored = np.zeros(instances.shape, dtype=bool)
    restored[ry0:ry1, rx0:rx1] = local_dilated
    overlapping = heads[ry0:ry1, rx0:rx1][local_dilated]
    overlapping = overlapping[overlapping > 0].astype(np.int64, copy=False)
    if not overlapping.size:
        return restored, [], []
    ids, counts = np.unique(overlapping, return_counts=True)
    return restored, ids.tolist(), counts.tolist()


class TailDilationRoiTest(unittest.TestCase):
    def test_single_pixel_center_edges_offsets_and_corners(self):
        pixels = [
        (60, 60),
        (1, 60), (19, 60), (20, 60), (21, 60),
        (118, 60), (100, 60), (99, 60), (98, 60),
        (60, 1), (60, 19), (60, 20), (60, 21),
        (60, 118), (60, 100), (60, 99), (60, 98),
        (0, 0), (0, 119), (119, 0), (119, 119),
        ]
        for pixel in pixels:
            with self.subTest(pixel=pixel):
                instances = np.zeros((120, 120), dtype=np.uint16)
                heads = np.zeros_like(instances)
                instances[pixel] = 7
                heads[max(0, pixel[0] - 20), pixel[1]] = 13
                heads[min(119, pixel[0] + 21), pixel[1]] = 29
                bboxes = adapter._raw_label_bboxes(instances, [7])
                old = _old_result(instances, heads, 7)
                new = _roi_result(instances, heads, 7, bboxes[7])
                self.assertTrue(np.array_equal(old[0], new[0]))
                self.assertEqual(old[1:], new[1:])

    def test_single_short_narrow_and_large_objects(self):
        fixtures = [
        (slice(35, 36), slice(40, 41)),
        (slice(35, 37), slice(40, 55)),
        (slice(10, 85), slice(15, 100)),
        ]
        for tail_pixels in fixtures:
            with self.subTest(tail_pixels=tail_pixels):
                instances = np.zeros((100, 120), dtype=np.uint16)
                heads = np.zeros_like(instances)
                instances[tail_pixels] = 101
                heads[0:100:7, 0:120:9] = 9
                heads[2:100:11, 3:120:13] = 3
                bbox = adapter._raw_label_bboxes(instances, [101])[101]
                old = _old_result(instances, heads, 101)
                new = _roi_result(instances, heads, 101, bbox)
                self.assertTrue(np.array_equal(old[0], new[0]))
                self.assertEqual(old[1:], new[1:])

    def test_multiple_tail_ids_heads_radius_boundary_and_order(self):
        instances = np.zeros((140, 160), dtype=np.uint16)
        instances[10:14, 10:30] = 40
        instances[65:100, 70:72] = 2
        instances[125:140, 145:160] = 91
        heads = np.zeros_like(instances)
        heads[10, 30] = 30
        heads[10, 49] = 10
        heads[10, 50] = 50
        heads[65, 50] = 70
        heads[120:125, 140:145] = 20

        tail_ids = [2, 40, 91]
        bboxes = adapter._raw_label_bboxes(instances, tail_ids)
        self.assertEqual(
            bboxes,
            {2: (65, 100, 70, 72), 40: (10, 14, 10, 30),
             91: (125, 140, 145, 160)},
        )
        for tail_id in tail_ids:
            old = _old_result(instances, heads, tail_id)
            new = _roi_result(instances, heads, tail_id, bboxes[tail_id])
            self.assertTrue(np.array_equal(old[0], new[0]))
            self.assertEqual(old[1], new[1])
            self.assertEqual(old[2], new[2])

    def test_empty_label_index_preserves_empty_instance_path(self):
        labels = np.zeros((3, 4), dtype=np.uint16)
        self.assertEqual(adapter._raw_label_bboxes(labels, []), {})

    @unittest.skipUnless(
        os.environ.get("C18B_A2_REAL_GATE") == "1",
        "explicit three-field geometry and association gate",
    )
    def test_real_fields_geometry_candidates_and_assignment_exact(self):
        root = ADAPTER.parents[2]
        old = _legacy_module("old_c18b_tail_dilation_adapter")
        fields = (
            ("023", "CASE20260908102941", "20260908_103450_acad3c", 7546),
            ("016", "CASE20260908104300", "20260908_104320_f4c8fc", 7104),
            ("022", "CASE20260908104656", "20260908_104716_af7f2c", 14560),
        )
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41))
        for field, case, run, expected_pairs in fields:
            run_root = (
                root / "workspace" / "cases" / case / "analysis_v2"
                / "protein3" / "runs" / run
            )
            field_id = "ZBFY{}-C-1".format(field)
            instances = cv2.imread(
                str(run_root / "segmentation" / "c18b_score015" / field_id
                    / (field_id + "_FITC")
                    / "07_extreme_fragment_filtered_labels.tif"),
                cv2.IMREAD_UNCHANGED,
            )
            heads = cv2.imread(
                str(run_root / "calibration" / "head"
                    / (field_id + "_HeadFinalLabels.tif")),
                cv2.IMREAD_UNCHANGED,
            )
            self.assertIsNotNone(instances)
            self.assertIsNotNone(heads)
            tail_ids = adapter.positive_ids(instances)
            head_ids = adapter.positive_ids(heads)
            bboxes = adapter._raw_label_bboxes(instances, tail_ids)
            dilation_mismatches = 0
            overlap_pair_mismatches = 0
            maximum_count_difference = 0
            for tail_id in tail_ids:
                full_mask = (instances == tail_id).astype(np.uint8)
                old_dilated = cv2.dilate(full_mask, kernel, iterations=1) > 0
                y0, y1, x0, x1 = bboxes[tail_id]
                ry0, ry1 = max(0, y0 - 20), min(instances.shape[0], y1 + 20)
                rx0, rx1 = max(0, x0 - 20), min(instances.shape[1], x1 + 20)
                local = full_mask[ry0:ry1, rx0:rx1]
                local_dilated = cv2.dilate(local, kernel, iterations=1) > 0
                restored = np.zeros(instances.shape, dtype=bool)
                restored[ry0:ry1, rx0:rx1] = local_dilated
                dilation_mismatches += int(not np.array_equal(old_dilated, restored))
                old_values = heads[old_dilated]
                new_values = heads[ry0:ry1, rx0:rx1][local_dilated]
                old_values = old_values[old_values > 0]
                new_values = new_values[new_values > 0]
                old_unique, old_counts = np.unique(old_values, return_counts=True)
                new_unique, new_counts = np.unique(new_values, return_counts=True)
                old_map = dict(zip(old_unique.tolist(), old_counts.tolist()))
                new_map = dict(zip(new_unique.tolist(), new_counts.tolist()))
                for head_id in head_ids:
                    difference = abs(int(old_map.get(head_id, 0))
                                     - int(new_map.get(head_id, 0)))
                    overlap_pair_mismatches += int(difference != 0)
                    maximum_count_difference = max(maximum_count_difference, difference)

            def measured(module):
                captured = []
                original_assignment = module.maximum_weight_assignment

                def capture(scores):
                    import sys
                    rows = sys._getframe(1).f_locals["proposals_by_instance"]
                    captured.extend(row.copy() for tail_id in sorted(rows)
                                    for row in rows[tail_id])
                    return original_assignment(scores)

                module.maximum_weight_assignment = capture
                try:
                    matched, unmatched = module.match_instances(instances, heads, 20, 80.0)
                finally:
                    module.maximum_weight_assignment = original_assignment
                return captured, matched, unmatched

            old_result = measured(old)
            new_result = measured(adapter)
            self.assertEqual(len(tail_ids) * len(head_ids), expected_pairs)
            self.assertEqual(dilation_mismatches, 0)
            self.assertEqual(overlap_pair_mismatches, 0)
            self.assertEqual(maximum_count_difference, 0)
            self.assertEqual(old_result, new_result)
            print(
                "A2_EXACT {} tails={} pairs={} dilation_mismatch={} "
                "overlap_pair_mismatch={} max_count_diff={} candidates={} "
                "assignment=exact".format(
                    field, len(tail_ids), expected_pairs, dilation_mismatches,
                    overlap_pair_mismatches, maximum_count_difference,
                    len(old_result[0]),
                ),
                flush=True,
            )

    @unittest.skipUnless(
        os.environ.get("C18B_A2_ADAPTER_AB") == "1",
        "explicit Option 1 adapter A/B gate",
    )
    def test_option1_adapter_ab(self):
        root = ADAPTER.parents[2]
        old = _legacy_module("old_c18b_tail_dilation_ab")
        order = ("OLD", "NEW", "NEW", "OLD", "OLD", "NEW")
        fields = (
            ("023", "CASE20260908102941", "20260908_103450_acad3c"),
            ("022", "CASE20260908104656", "20260908_104716_af7f2c"),
        )
        for field, case, run in fields:
            run_root = (
                root / "workspace" / "cases" / case / "analysis_v2"
                / "protein3" / "runs" / run
            )
            field_id = "ZBFY{}-C-1".format(field)
            inputs = (
                run_root / "segmentation" / "c18b_score015" / field_id
                / (field_id + "_FITC") / "07_extreme_fragment_filtered_labels.tif",
                run_root / "calibration" / "head" / (field_id + "_HeadFinalLabels.tif"),
                run_root / "input" / (field_id + "_FITC.tif"),
                run_root / "input" / (field_id + "_Merge.tif"),
                run_root / "segmentation" / "c18b_runner_contract" / field_id
                / "02_probability_uint16.tif",
            )
            self.assertTrue(all(path.is_file() for path in inputs))
            times = {"OLD": [], "NEW": []}
            reference = None
            with tempfile.TemporaryDirectory(prefix="c18b_a2_adapter_ab_") as temp_dir:
                for index, version in enumerate(order):
                    module = old if version == "OLD" else adapter
                    output_dir = Path(temp_dir) / "{}_{}_{}".format(field, index, version)
                    started = time.perf_counter()
                    module.run_adapter(*inputs, output_dir)
                    elapsed = time.perf_counter() - started
                    payload = {}
                    for name in ("fragments.tif", "probability.tif"):
                        payload[name] = cv2.imread(
                            str(output_dir / name), cv2.IMREAD_UNCHANGED
                        )
                    for name in (
                        "entries.json", "paths.json", "global_results.json",
                        "unassigned_tail_candidates.json",
                    ):
                        payload[name] = json.loads(
                            (output_dir / name).read_text(encoding="utf-8")
                        )
                    if reference is None:
                        reference = payload
                    else:
                        for name in ("fragments.tif", "probability.tif"):
                            self.assertTrue(np.array_equal(reference[name], payload[name]))
                        for name in (
                            "entries.json", "paths.json", "global_results.json",
                            "unassigned_tail_candidates.json",
                        ):
                            self.assertEqual(reference[name], payload[name])
                    times[version].append(elapsed)
                    print(
                        "A2_ADAPTER_AB {} {} run{} {:.3f}s business=exact".format(
                            field, version, len(times[version]), elapsed
                        ),
                        flush=True,
                    )
            old_median = float(np.median(times["OLD"]))
            new_median = float(np.median(times["NEW"]))
            saved = old_median - new_median
            print(
                "A2_ADAPTER_AB_RESULT {} OLD={} median={:.3f} range={:.3f} "
                "NEW={} median={:.3f} range={:.3f} saved={:.3f} "
                "improvement={:.1f}%".format(
                    field, [round(value, 3) for value in times["OLD"]], old_median,
                    max(times["OLD"]) - min(times["OLD"]),
                    [round(value, 3) for value in times["NEW"]], new_median,
                    max(times["NEW"]) - min(times["NEW"]), saved,
                    100.0 * saved / old_median,
                ),
                flush=True,
            )


if __name__ == "__main__":
    unittest.main()
