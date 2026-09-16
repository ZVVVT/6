"""Association residual locality: bbox-scoped head records and centerline lookups.

The association stage resolved every head record and every ordered centerline
with full-frame ``np.nonzero``/``np.argwhere`` scans.  Both lookups are now
resolved inside the raw label bbox of the very same object.  These tests pin
that the bbox scoped values are byte-for-byte the full-frame values and that
the legacy two-argument ``ordered_centerline`` call keeps its old semantics.
"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = (
    PROJECT_ROOT / "tools" / "analysis_v2" / "c18b_tail_editor_adapter.py"
)
SPEC = importlib.util.spec_from_file_location(
    "c18b_centerline_locality_adapter", ADAPTER
)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)

DILATION_RADIUS = 20
MAXIMUM_DISTANCE = 80.0
REAL_FIELDS = (
    ("023", "CASE20260908102941", "20260908_103450_acad3c"),
    ("020", "CASE20260908103925", "20260908_103952_a64637"),
    ("016", "CASE20260908104300", "20260908_104320_f4c8fc"),
    ("022", "CASE20260908104656", "20260908_104716_af7f2c"),
)
ADAPTER_AB_FIELDS = (
    ("022", "CASE20260908104656", "20260908_104716_af7f2c"),
    ("023", "CASE20260908102941", "20260908_103450_acad3c"),
)


def _old_module(name):
    """Load the pre-G1 adapter (HEAD) for old-versus-new comparison."""
    source = subprocess.check_output(
        ["git", "show", "HEAD:tools/analysis_v2/c18b_tail_editor_adapter.py"],
        cwd=str(PROJECT_ROOT),
        encoding="utf-8",
    )
    module = types.ModuleType(name)
    exec(compile(source, str(ADAPTER), "exec"), module.__dict__)
    return module


def _old_head_records(head_labels):
    """Full-frame head record reference of the pre-G1 adapter."""
    records = []
    for head_id in adapter.positive_ids(head_labels):
        y, x = np.nonzero(head_labels == head_id)
        records.append({
            "head_id": int(head_id),
            "center_x": float(x.mean()),
            "center_y": float(y.mean()),
            "status": "manual_required",
        })
    return records


def _raw_bboxes(labels):
    return adapter._raw_label_bboxes(labels, adapter.positive_ids(labels))


def _isolate_cv2_ximgproc(test_case):
    """Drop a leaked ``cv2.ximgproc`` stub for the duration of one test.

    ``cv2`` is a process-wide module object, so a test that assigns a stand-in
    ``cv2.ximgproc`` leaks it into every later test of the session.  The
    locality contract has to be evaluated against the real runtime, so a leaked
    stub (whose module name is not ``cv2.ximgproc``) is removed here and put
    back afterwards.
    """
    leaked = getattr(cv2, "ximgproc", None)
    if leaked is None or getattr(leaked, "__name__", "") == "cv2.ximgproc":
        return
    test_case.addCleanup(setattr, cv2, "ximgproc", leaked)
    del cv2.ximgproc


def _geometry_fixtures():
    fixtures = []

    instances = np.zeros((90, 110), dtype=np.uint16)
    heads = np.zeros_like(instances)
    instances[40:60, 30:40] = 3
    heads[38:46, 24:30] = 11
    fixtures.append(("center_object", instances, heads))

    instances = np.zeros((90, 110), dtype=np.uint16)
    heads = np.zeros_like(instances)
    instances[0:6, 0:6] = 5
    heads[0:3, 7:11] = 2
    fixtures.append(("tail_and_head_touch_top_left_border", instances, heads))

    instances = np.zeros((90, 110), dtype=np.uint16)
    heads = np.zeros_like(instances)
    instances[89, 109] = 17
    heads[87:90, 104:109] = 19
    fixtures.append(("tail_and_head_touch_bottom_right_border", instances, heads))

    instances = np.zeros((90, 110), dtype=np.uint16)
    heads = np.zeros_like(instances)
    instances[45, 45] = 1
    heads[45, 40:43] = 4
    fixtures.append(("single_pixel_tail", instances, heads))

    instances = np.zeros((90, 110), dtype=np.uint16)
    heads = np.zeros_like(instances)
    instances[10:80, 55] = 6
    heads[8:12, 50:54] = 8
    fixtures.append(("thin_one_pixel_tail", instances, heads))

    instances = np.zeros((90, 110), dtype=np.uint16)
    heads = np.zeros_like(instances)
    instances[5:85, 5:105] = 9
    heads[45:50, 100:105] = 13
    fixtures.append(("large_tail", instances, heads))

    instances = np.zeros((90, 110), dtype=np.uint16)
    heads = np.zeros_like(instances)
    instances[10:80, 10:90] = 14
    instances[35:55, 40:60] = 0
    heads[20:26, 5:10] = 21
    heads[70:76, 92:98] = 23
    fixtures.append(("tail_with_hole_and_two_heads", instances, heads))

    instances = np.zeros((120, 150), dtype=np.uint16)
    heads = np.zeros_like(instances)
    instances[10:20, 10:25] = 31
    instances[60:65, 60:61] = 32
    instances[110:119, 140:150] = 33
    heads[8:12, 26:30] = 41
    heads[0:4, 0:4] = 42
    heads[115:120, 130:140] = 43
    fixtures.append(("multiple_tails_and_heads", instances, heads))

    fixtures.append((
        "empty_masks",
        np.zeros((12, 14), dtype=np.uint16),
        np.zeros((12, 14), dtype=np.uint16),
    ))
    return fixtures


class HeadRecordsLocalityTest(unittest.TestCase):
    def _assert_records_exact(self, head_labels):
        full_frame = _old_head_records(head_labels)
        bboxes = _raw_bboxes(head_labels)
        internal = adapter.head_records(head_labels)
        shared = adapter.head_records(head_labels, bboxes)
        self.assertEqual(full_frame, internal)
        self.assertEqual(full_frame, shared)
        for record in shared:
            self.assertEqual(
                sorted(record), ["center_x", "center_y", "head_id", "status"]
            )
            self.assertIsInstance(record["head_id"], int)
            self.assertIsInstance(record["center_x"], float)
            self.assertIsInstance(record["center_y"], float)
            self.assertEqual(record["status"], "manual_required")
        return shared

    def test_geometry_fixtures_match_full_frame_head_records(self):
        for name, _instances, heads in _geometry_fixtures():
            with self.subTest(fixture=name):
                self._assert_records_exact(heads)

    def test_head_ids_order_edges_and_holes_are_preserved(self):
        labels = np.zeros((40, 50), dtype=np.uint16)
        labels[0, 0] = 9
        labels[39, 49] = 2
        labels[10:30, 10:30] = 5
        labels[16:24, 16:24] = 0
        labels[5:8, 40:43] = 4
        records = self._assert_records_exact(labels)
        self.assertEqual([row["head_id"] for row in records], [2, 4, 5, 9])

    def test_shared_bbox_index_is_the_raw_label_bbox(self):
        labels = np.zeros((30, 40), dtype=np.uint16)
        labels[3:9, 7:19] = 6
        self.assertEqual(_raw_bboxes(labels), {6: (3, 9, 7, 19)})
        y, x = np.nonzero(labels == 6)
        self.assertEqual(
            _raw_bboxes(labels)[6],
            (int(y.min()), int(y.max()) + 1, int(x.min()), int(x.max()) + 1),
        )


class OrderedCenterlineLocalityTest(unittest.TestCase):
    def setUp(self):
        _isolate_cv2_ximgproc(self)
        self.old = _old_module("old_c18b_centerline_locality")

    def _assert_pair_exact(
        self, instances, heads, tail_id, head_id, instance_bboxes, head_bboxes
    ):
        instance_mask = instances == tail_id
        head_mask = heads == head_id
        instance_bbox = instance_bboxes[tail_id]
        head_bbox = head_bboxes[head_id]
        y, x = np.nonzero(instance_mask)
        self.assertEqual(
            instance_bbox,
            (int(y.min()), int(y.max()) + 1, int(x.min()), int(x.max()) + 1),
        )
        y, x = np.nonzero(head_mask)
        self.assertEqual(
            head_bbox,
            (int(y.min()), int(y.max()) + 1, int(x.min()), int(x.max()) + 1),
        )
        legacy = self.old.ordered_centerline(instance_mask, head_mask)
        two_argument = adapter.ordered_centerline(instance_mask, head_mask)
        localized = adapter.ordered_centerline(
            instance_mask,
            head_mask,
            instance_bbox=instance_bbox,
            head_bbox=head_bbox,
        )
        self.assertEqual(legacy, two_argument)
        self.assertEqual(legacy, localized)
        for point in localized:
            self.assertIsInstance(point[0], float)
            self.assertIsInstance(point[1], float)
        return localized

    def test_geometry_fixtures_are_exact_for_every_pair(self):
        non_empty = 0
        for name, instances, heads in _geometry_fixtures():
            with self.subTest(fixture=name):
                instance_bboxes = _raw_bboxes(instances)
                head_bboxes = _raw_bboxes(heads)
                for tail_id in adapter.positive_ids(instances):
                    for head_id in adapter.positive_ids(heads):
                        points = self._assert_pair_exact(
                            instances, heads, tail_id, head_id,
                            instance_bboxes, head_bboxes,
                        )
                        non_empty += int(len(points) >= 2)
        self.assertGreater(non_empty, 0)

    def test_empty_masks_keep_the_legacy_empty_result(self):
        empty = np.zeros((16, 18), dtype=np.uint16)
        self.assertEqual(adapter.ordered_centerline(empty > 0, empty > 0), [])
        self.assertEqual(
            self.old.ordered_centerline(empty > 0, empty > 0), []
        )
        instances = np.zeros((16, 18), dtype=np.uint16)
        heads = np.zeros_like(instances)
        instances[4:9, 4:9] = 3
        self.assertEqual(adapter.ordered_centerline(instances == 3, heads > 0), [])

    def test_skeleton_input_and_timings_are_unchanged(self):
        instances = np.zeros((70, 90), dtype=np.uint16)
        heads = np.zeros_like(instances)
        instances[20:50, 30:36] = 7
        heads[18:24, 24:30] = 9
        instance_bbox = adapter._raw_label_bboxes(instances, [7])[7]
        head_bbox = adapter._raw_label_bboxes(heads, [9])[9]
        captured = []
        original = adapter.skeletonize

        def capture(mask):
            captured.append(np.array(mask, copy=True))
            return original(mask)

        adapter.skeletonize = capture
        try:
            legacy_timings = {
                "skeletonize_seconds": 0.0, "ordered_centerline_seconds": 0.0,
            }
            legacy_points = adapter.ordered_centerline(
                instances == 7, heads == 9, timings=legacy_timings
            )
            local_timings = {
                "skeletonize_seconds": 0.0, "ordered_centerline_seconds": 0.0,
            }
            local_points = adapter.ordered_centerline(
                instances == 7,
                heads == 9,
                timings=local_timings,
                instance_bbox=instance_bbox,
                head_bbox=head_bbox,
            )
        finally:
            adapter.skeletonize = original
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0].shape, captured[1].shape)
        self.assertEqual(captured[0].dtype, captured[1].dtype)
        self.assertTrue(np.array_equal(captured[0], captured[1]))
        self.assertEqual(legacy_points, local_points)
        self.assertEqual(sorted(legacy_timings), sorted(local_timings))
        self.assertGreater(legacy_timings["skeletonize_seconds"], 0.0)
        self.assertGreater(local_timings["skeletonize_seconds"], 0.0)
        self.assertGreater(legacy_timings["ordered_centerline_seconds"], 0.0)
        self.assertGreater(local_timings["ordered_centerline_seconds"], 0.0)


class RealFieldExactnessTest(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("C18B_G1_REAL_GATE") == "1",
        "explicit real field locality gate",
    )
    def test_real_fields_head_records_centerlines_and_candidates_exact(self):
        _isolate_cv2_ximgproc(self)
        old = _old_module("old_c18b_centerline_real")
        for field, case, run in REAL_FIELDS:
            run_root = (
                PROJECT_ROOT / "workspace" / "cases" / case / "analysis_v2"
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
            instance_bboxes = adapter._raw_label_bboxes(instances, tail_ids)
            head_bboxes = adapter._raw_label_bboxes(heads, head_ids)

            self.assertEqual(_old_head_records(heads),
                             adapter.head_records(heads, head_bboxes))

            def measured(module):
                captured = []
                original_assignment = module.maximum_weight_assignment

                def capture(scores):
                    rows = sys._getframe(1).f_locals["proposals_by_instance"]
                    captured.extend(row.copy() for tail_id in sorted(rows)
                                    for row in rows[tail_id])
                    return original_assignment(scores)

                module.maximum_weight_assignment = capture
                try:
                    if module is adapter:
                        return captured, module.match_instances(
                            instances, heads, DILATION_RADIUS,
                            MAXIMUM_DISTANCE,
                            instance_raw_bboxes=instance_bboxes,
                            head_raw_bboxes=head_bboxes,
                        )
                    return captured, module.match_instances(
                        instances, heads, DILATION_RADIUS, MAXIMUM_DISTANCE
                    )
                finally:
                    module.maximum_weight_assignment = original_assignment

            old_candidates, old_matched = measured(old)
            new_candidates, new_matched = measured(adapter)
            self.assertEqual(old_candidates, new_candidates)
            self.assertEqual(old_matched, new_matched)

            path_mismatches = 0
            for match in new_matched[0]:
                instance_id = int(match["c18b_instance_id"])
                head_id = int(match["head_id"])
                legacy = old.ordered_centerline(
                    instances == instance_id, heads == head_id
                )
                localized = adapter.ordered_centerline(
                    instances == instance_id,
                    heads == head_id,
                    instance_bbox=instance_bboxes[instance_id],
                    head_bbox=head_bboxes[head_id],
                )
                path_mismatches += int(legacy != localized)
                self.assertEqual(legacy, localized)
            print(
                "G1_REAL_EXACT {} tails={} heads={} matched={} unmatched={} "
                "candidates={} path_mismatch={}".format(
                    field, len(tail_ids), len(head_ids), len(new_matched[0]),
                    len(new_matched[1]), len(new_candidates), path_mismatches,
                ),
                flush=True,
            )


class AdapterLocalityAbTest(unittest.TestCase):
    def _inputs_for(self, field, case, run):
        run_root = (
            PROJECT_ROOT / "workspace" / "cases" / case / "analysis_v2"
            / "protein3" / "runs" / run
        )
        field_id = "ZBFY{}-C-1".format(field)
        inputs = {
            "--instances": run_root / "segmentation" / "c18b_score015"
            / field_id / (field_id + "_FITC")
            / "07_extreme_fragment_filtered_labels.tif",
            "--head-labels": run_root / "calibration" / "head"
            / (field_id + "_HeadFinalLabels.tif"),
            "--fitc": run_root / "input" / (field_id + "_FITC.tif"),
            "--merge": run_root / "input" / (field_id + "_Merge.tif"),
            "--probability": run_root / "segmentation"
            / "c18b_runner_contract" / field_id / "02_probability_uint16.tif",
        }
        self.assertTrue(all(path.is_file() for path in inputs.values()))
        return inputs

    def _write_old_script(self, temp):
        old_script = temp / "old_c18b_tail_editor_adapter.py"
        old_script.write_text(
            subprocess.check_output(
                ["git", "show", "HEAD:tools/analysis_v2/c18b_tail_editor_adapter.py"],
                cwd=str(PROJECT_ROOT), encoding="utf-8",
            ),
            encoding="utf-8",
        )
        return old_script

    def _run_adapter(self, script, inputs, output_dir):
        command = [sys.executable, "-u", str(script)]
        for name, path in inputs.items():
            command.extend([name, str(path)])
        command.extend(["--output-dir", str(output_dir)])
        started = time.perf_counter()
        result = subprocess.run(
            command, cwd=str(PROJECT_ROOT), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True,
        )
        elapsed = time.perf_counter() - started
        self.assertEqual(result.returncode, 0, result.stderr)
        return elapsed

    def _assert_outputs_byte_exact(self, reference_dir, candidate_dir):
        for name in ("fragments.tif", "probability.tif"):
            self.assertTrue(np.array_equal(
                cv2.imread(str(reference_dir / name), cv2.IMREAD_UNCHANGED),
                cv2.imread(str(candidate_dir / name), cv2.IMREAD_UNCHANGED),
            ))
        for name in (
            "entries.json", "paths.json", "global_results.json",
            "unassigned_tail_candidates.json",
        ):
            self.assertEqual(
                (reference_dir / name).read_bytes(), (candidate_dir / name).read_bytes()
            )

    @staticmethod
    def _manifest_business(manifest):
        """Manifest payload without timings and run specific output paths."""
        payload = dict((key, value) for key, value in manifest.items()
                       if key != "timings")
        payload["outputs"] = dict(
            (key, Path(value).name) for key, value in payload["outputs"].items()
        )
        return payload

    @unittest.skipUnless(
        os.environ.get("C18B_G1_ADAPTER_AB") == "1",
        "explicit adapter exactness gate",
    )
    def test_adapter_outputs_and_manifest_exact_for_all_tier1_fields(self):
        with tempfile.TemporaryDirectory(prefix="c18b_g1_adapter_exact_") as temp_dir:
            temp = Path(temp_dir)
            old_script = self._write_old_script(temp)
            for field, case, run in REAL_FIELDS:
                with self.subTest(field=field):
                    inputs = self._inputs_for(field, case, run)
                    old_dir = temp / "{}_exact_OLD".format(field)
                    new_dir = temp / "{}_exact_NEW".format(field)
                    self._run_adapter(old_script, inputs, old_dir)
                    self._run_adapter(ADAPTER, inputs, new_dir)
                    self._assert_outputs_byte_exact(old_dir, new_dir)
                    old_manifest = json.loads(
                        (old_dir / "manifest.json").read_text(encoding="utf-8")
                    )
                    new_manifest = json.loads(
                        (new_dir / "manifest.json").read_text(encoding="utf-8")
                    )
                    self.assertEqual(
                        self._manifest_business(old_manifest),
                        self._manifest_business(new_manifest),
                    )
                    print(
                        "G1_ADAPTER_EXACT {} fragments/probability=array_equal "
                        "json=byte_exact manifest=exact".format(field),
                        flush=True,
                    )

    @unittest.skipUnless(
        os.environ.get("C18B_G1_ADAPTER_AB") == "1",
        "explicit adapter A/B timing gate",
    )
    def test_adapter_ab_022_primary_and_023_control(self):
        order = ("OLD", "NEW", "NEW", "OLD", "NEW", "OLD")
        with tempfile.TemporaryDirectory(prefix="c18b_g1_adapter_ab_") as temp_dir:
            temp = Path(temp_dir)
            old_script = self._write_old_script(temp)
            for field, case, run in ADAPTER_AB_FIELDS:
                inputs = self._inputs_for(field, case, run)
                times = {"OLD": [], "NEW": []}
                for index, version in enumerate(order):
                    script = old_script if version == "OLD" else ADAPTER
                    output_dir = temp / "{}_{}_{}".format(field, index, version)
                    elapsed = self._run_adapter(script, inputs, output_dir)
                    if index:
                        self._assert_outputs_byte_exact(
                            temp / "{}_0_OLD".format(field), output_dir
                        )
                    times[version].append(elapsed)
                    print(
                        "G1_ADAPTER_AB {} {} run{} {:.4f}s business=exact"
                        .format(field, version, len(times[version]), elapsed),
                        flush=True,
                    )
                old_median = float(np.median(times["OLD"]))
                new_median = float(np.median(times["NEW"]))
                saved = old_median - new_median
                variance = max(
                    max(times["OLD"]) - min(times["OLD"]),
                    max(times["NEW"]) - min(times["NEW"]),
                )
                print(
                    "G1_ADAPTER_AB_RESULT {} OLD={} median={:.4f} NEW={} "
                    "median={:.4f} saved={:.4f} improvement={:.1f}% "
                    "variance={:.4f}".format(
                        field, [round(value, 4) for value in times["OLD"]],
                        old_median,
                        [round(value, 4) for value in times["NEW"]], new_median,
                        saved, 100.0 * saved / old_median, variance,
                    ),
                    flush=True,
                )


if __name__ == "__main__":
    unittest.main()
