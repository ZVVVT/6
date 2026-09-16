"""Seed-aware ROI exactness contract for region-indexed reconstruction geometry.

F60-F2 moved ``cv2.distanceTransformWithLabels`` inside ``_path_geometry_region``
from the full frame to the bounding box of the parent region unioned with every
in-bounds path seed (halo 0).  These tests pin the contract that the ROI call is
bit-exact against the frozen full-frame reference: the ROI source, the
``DIST_LABEL_PIXEL`` label -> global seed coordinate mapping, the region
distance/alignment values, and the returned full-frame seed mask.

A parent-bbox-only ROI is also pinned as UNSAFE so the seed-aware union cannot be
relaxed back to the cheaper, wrong spatial domain.
"""
import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np


C18B_DIR = Path(__file__).resolve().parents[1] / "tools" / "analysis_v2" / "c18b_score015"
sys.path.insert(0, str(C18B_DIR))
try:
    GEOMETRY = importlib.import_module("graph_constrained_instance_separation")
finally:
    sys.path.remove(str(C18B_DIR))


def full_frame_reference(path, shape):
    """Frozen pre-F60-F2 implementation, retained only as a test oracle."""
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
    return seed, distance, nearest, ys, xs, tangent_x, tangent_y


def reference_region(path, shape, region_y, region_x):
    """Frozen full-frame region geometry plus the nearest global seed coordinates."""
    seed, distance, nearest, ys, xs, tangent_x, tangent_y = full_frame_reference(path, shape)
    lut_x = np.zeros(len(xs) + 1, np.float32)
    lut_y = np.zeros(len(xs) + 1, np.float32)
    lut_tx = np.zeros(len(xs) + 1, np.float32)
    lut_ty = np.zeros(len(xs) + 1, np.float32)
    lut_x[1:], lut_y[1:] = xs, ys
    lut_tx[1:], lut_ty[1:] = tangent_x[ys, xs], tangent_y[ys, xs]
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
    return (distance_region, align, seed, lut_x[nearest_region], lut_y[nearest_region])


def call_with_roi_capture(path, shape, region_y, region_x):
    """Run production geometry while capturing the exact ROI source sent to OpenCV."""
    original = GEOMETRY.cv2.distanceTransformWithLabels
    captured = {}

    def wrapper(src, *args, **kwargs):
        captured["src"] = np.array(src, copy=True)
        captured["args"] = (args, kwargs)
        return original(src, *args, **kwargs)

    with mock.patch.object(GEOMETRY.cv2, "distanceTransformWithLabels",
                           side_effect=wrapper):
        result = GEOMETRY._path_geometry_region(path, shape, region_y, region_x)
    return result, captured


def region_indices(mask):
    ys, xs = np.nonzero(mask)
    return ys, xs


def parent_bbox_only_geometry(path, shape, region_y, region_x):
    """The rejected naive variant: ROI limited to the parent bounding box alone."""
    seed = np.zeros(shape, np.uint8)
    for x, y in path:
        if 0 <= x < shape[1] and 0 <= y < shape[0]:
            seed[y, x] = 1
    y0, y1 = int(region_y.min()), int(region_y.max()) + 1
    x0, x1 = int(region_x.min()), int(region_x.max()) + 1
    crop_seed = seed[y0:y1, x0:x1]
    distance, nearest = GEOMETRY.cv2.distanceTransformWithLabels(
        (1 - crop_seed).astype(np.uint8), GEOMETRY.cv2.DIST_L2, 5,
        labelType=GEOMETRY.cv2.DIST_LABEL_PIXEL)
    local_y, local_x = region_y - y0, region_x - x0
    return distance[local_y, local_x], int(np.count_nonzero(crop_seed))


def build_region(shape, kind):
    region = np.zeros(shape, np.bool_)
    if kind == "block":
        region[shape[0] // 2 - 2:shape[0] // 2 + 2,
               shape[1] // 2 - 2:shape[1] // 2 + 2] = True
    elif kind == "corner":
        region[0:4, 0:4] = True
    elif kind == "far_block":
        region[3:8, 3:8] = True
    elif kind == "thin_column":
        region[2:20, 8:9] = True
    elif kind == "donut":
        region[7:18, 7:18] = True
        region[10:15, 10:15] = False
    elif kind == "row_line":
        region[12:13, 3:22] = True
    elif kind == "two_blobs":
        region[4:12, 4:12] = True
        region[16:24, 20:28] = True
    else:
        raise AssertionError("unknown region kind {}".format(kind))
    return region


def duplicate_last_write_path():
    return np.asarray([[9, 11], [17, 5], [9, 11], [3, 17], [9, 11]], np.int32)


CASES = {
    "single_seed": ((21, 25),
                    lambda: np.asarray([[11, 10], [12, 11], [13, 12]], np.int32),
                    "block"),
    "same_row_equidistant": ((19, 31),
                             lambda: np.asarray([[9, 9], [8, 9], [10, 9],
                                                 [21, 9], [22, 9], [20, 9]], np.int32),
                             "block"),
    "same_column": ((27, 19),
                    lambda: np.asarray([[9, 5], [9, 6], [9, 7],
                                        [9, 21], [9, 22], [9, 20]], np.int32),
                    "block"),
    "diagonal": ((25, 25),
                 lambda: np.asarray([[5, 5], [6, 6], [19, 19], [18, 18]], np.int32),
                 "block"),
    "three_seed_tie": ((23, 23),
                       lambda: np.asarray([[11, 5], [5, 11], [17, 17],
                                           [4, 10], [11, 4], [18, 18]], np.int32),
                       "block"),
    "border_seed": ((17, 21),
                    lambda: np.asarray([[10, 0], [0, 10], [20, 10], [10, 16]], np.int32),
                    "block"),
    "corner_seed": ((17, 21),
                    lambda: np.asarray([[0, 0], [0, 20], [16, 0], [16, 20], [8, 8]],
                                       np.int32),
                    "block"),
    "parent_touches_boundary": ((17, 21),
                                lambda: np.asarray([[6, 6], [13, 14]], np.int32),
                                "corner"),
    "seed_outside_parent_bbox": ((34, 34),
                                 lambda: np.asarray([[9, 10], [9, 11], [9, 12]], np.int32),
                                 "far_block"),
    "multiple_outside_parent_seeds": ((40, 40),
                                      lambda: np.asarray([[12, 12], [12, 13],
                                                          [31, 33], [32, 34], [30, 32]],
                                                         np.int32),
                                      "thin_column"),
    "hole": ((25, 25),
             lambda: np.asarray([[11, 11], [12, 12], [6, 18], [19, 6]], np.int32),
             "donut"),
    "thin_parent": ((25, 25),
                    lambda: np.asarray([[12, 3], [12, 4], [12, 21], [12, 22]], np.int32),
                    "row_line"),
    "disconnected_parent": ((25, 31),
                            lambda: np.asarray([[6, 6], [24, 24], [6, 7], [23, 24]],
                                               np.int32),
                            "two_blobs"),
    "duplicate_seed": ((23, 23), duplicate_last_write_path, "block"),
    "adjacent_seeds": ((21, 21),
                       lambda: np.asarray([[10, 10], [10, 11], [11, 10], [11, 11]],
                                          np.int32),
                       "block"),
}


class C18BReconstructionDistanceRoiTests(unittest.TestCase):
    def assert_region_exact(self, path, shape, region, label=""):
        """Bit-exact distance/align/seed plus exact nearest global seed coordinates."""
        region_y, region_x = region_indices(region)
        expected = reference_region(path, shape, region_y, region_x)
        actual, captured = call_with_roi_capture(path, shape, region_y, region_x)
        self.assertEqual(np.dtype(np.float32), actual[0].dtype)
        self.assertEqual(np.dtype(np.float32), actual[1].dtype)
        np.testing.assert_array_equal(expected[0].view(np.int32),
                                      actual[0].view(np.int32))
        np.testing.assert_array_equal(expected[1].view(np.int32),
                                      actual[1].view(np.int32))
        np.testing.assert_array_equal(expected[2], actual[2])
        self.assertEqual(np.dtype(np.uint8), actual[2].dtype)
        self.assertEqual(tuple(shape), actual[2].shape)
        self.assertEqual(tuple(shape), expected[2].shape)
        # The ROI is exactly bbox(parent region UNION in-bounds path seeds), halo 0.
        seed_y, seed_x = np.nonzero(expected[2])
        ys = np.concatenate([seed_y, region_y])
        xs = np.concatenate([seed_x, region_x])
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        self.assertEqual((y1 - y0, x1 - x0), captured["src"].shape, label)
        np.testing.assert_array_equal(
            (1 - expected[2])[y0:y1, x0:x1], captured["src"],
            "ROI source must equal the frozen (1 - seed) slice")
        # Distance transform parameters are frozen.
        args, kwargs = captured["args"]
        self.assertEqual((GEOMETRY.cv2.DIST_L2, 5), tuple(args))
        self.assertEqual(GEOMETRY.cv2.DIST_LABEL_PIXEL, kwargs["labelType"])
        # DIST_LABEL_PIXEL labels the ROI seeds in row-major order, so a regional
        # label maps to the same global seed coordinate as the frozen full frame.
        roi_distance, roi_labels = GEOMETRY.cv2.distanceTransformWithLabels(
            captured["src"], GEOMETRY.cv2.DIST_L2, 5,
            labelType=GEOMETRY.cv2.DIST_LABEL_PIXEL)
        local_y, local_x = region_y - y0, region_x - x0
        np.testing.assert_array_equal(
            expected[0].view(np.int32), roi_distance[local_y, local_x].view(np.int32),
            "regional distance labels disagree with the frozen full frame")
        if len(seed_y):
            # DIST_LABEL_PIXEL numbers the zero pixels of the ROI in row-major
            # order, which is the same relative order as the frozen full-frame
            # LUT, so label k still selects the k-th row-major global seed.
            local_seed_y, local_seed_x = np.nonzero(captured["src"] == 0)
            self.assertEqual(len(seed_y), len(local_seed_y))
            np.testing.assert_array_equal(seed_y, local_seed_y + y0)
            np.testing.assert_array_equal(seed_x, local_seed_x + x0)
            np.testing.assert_array_equal(roi_labels[local_seed_y, local_seed_x],
                                          np.arange(1, len(local_seed_y) + 1))
            roi_nearest = np.minimum(roi_labels[local_y, local_x].astype(np.intp),
                                     len(seed_y)) - 1
            np.testing.assert_array_equal(
                expected[3].astype(np.float32),
                (local_seed_x + x0)[roi_nearest].astype(np.float32),
                "nearest global seed x differs from the frozen full-frame mapping")
            np.testing.assert_array_equal(
                expected[4].astype(np.float32),
                (local_seed_y + y0)[roi_nearest].astype(np.float32),
                "nearest global seed y differs from the frozen full-frame mapping")
        return expected, actual

    def test_every_documented_scenario_is_bit_exact(self):
        for label in sorted(CASES):
            shape, path_factory, region_kind = CASES[label]
            with self.subTest(case=label):
                self.assert_region_exact(path_factory(), shape,
                                         build_region(shape, region_kind), label)

    def test_empty_and_fully_out_of_bounds_paths_keep_frozen_degenerate_result(self):
        shape = (23, 29)
        region = build_region(shape, "block")
        cases = (("empty_path", np.zeros((0, 2), np.int32)),
                 ("outside_path", np.asarray([[-4, 3], [40, 3], [3, 40]], np.int32)))
        for label, path in cases:
            with self.subTest(case=label):
                expected, actual = self.assert_region_exact(path, shape, region, label)
                # No legal seed: frozen full-frame sentinel distance and zero alignment.
                self.assertTrue(np.all(actual[0] > 1e4))
                self.assertEqual(np.dtype(np.float32), actual[0].dtype)
                np.testing.assert_array_equal(expected[0], actual[0])
                np.testing.assert_array_equal(np.zeros_like(actual[1]), actual[1])
                self.assertFalse(actual[2].any())

    def test_partial_out_of_bounds_path_keeps_only_legal_seeds(self):
        shape = (19, 23)
        path = np.asarray([[4, 4], [3, 4], [23, 4], [4, -1], [5, 5], [6, 5]], np.int32)
        expected, actual = self.assert_region_exact(path, shape,
                                                    build_region(shape, "block"),
                                                    "partial_out_of_bounds")
        self.assertEqual(4, int(expected[2].sum()))
        self.assertEqual(4, int(actual[2].sum()))

    def test_identical_seed_coordinates_keep_last_write_tangent(self):
        shape = (23, 23)
        path = duplicate_last_write_path()
        self.assert_region_exact(path, shape, build_region(shape, "block"),
                                 "duplicate_seed")
        self.assertEqual(5, len(path))
        self.assertEqual(3, len(np.unique(path, axis=0)))

    def test_parent_bbox_only_roi_is_unsafe(self):
        # (a) No seed inside the parent bounding box: the naive crop has no zero pixel.
        shape = (34, 34)
        path = np.asarray([[9, 10], [9, 11], [9, 12]], np.int32)
        region = build_region(shape, "far_block")
        region_y, region_x = region_indices(region)
        expected = reference_region(path, shape, region_y, region_x)
        naive_distance, naive_seed_count = parent_bbox_only_geometry(
            path, shape, region_y, region_x)
        self.assertEqual(0, naive_seed_count)
        self.assertFalse(np.array_equal(naive_distance.view(np.int32),
                                        expected[0].view(np.int32)))
        self.assert_region_exact(path, shape, region, "seed_outside_parent_bbox")

        # (b) An in-bbox seed exists, yet an outside seed is genuinely nearest for some
        # parent pixel, so a parent-bbox-only ROI returns a wrong distance and label.
        shape = (24, 24)
        path = np.asarray([[6, 6], [10, 8], [11, 8]], np.int32)
        region = np.zeros(shape, np.bool_)
        region[5:9, 5:9] = True
        region_y, region_x = region_indices(region)
        expected = reference_region(path, shape, region_y, region_x)
        naive_distance, naive_seed_count = parent_bbox_only_geometry(
            path, shape, region_y, region_x)
        self.assertEqual(1, naive_seed_count)
        self.assertFalse(np.array_equal(naive_distance.view(np.int32),
                                        expected[0].view(np.int32)))
        # Parent pixel (row 8, col 8) is 2.0 from the outside seed (row 8, col 10)
        # but 2.8 from the only in-bbox seed (row 6, col 6).
        self.assertAlmostEqual(2.0, float(expected[0][15]), places=4)
        self.assertAlmostEqual(2.8, float(naive_distance[15]), places=4)
        self.assert_region_exact(path, shape, region, "outside_seed_is_nearest")

    def test_roi_spatial_domain_is_smaller_than_the_full_frame(self):
        shape = (256, 256)
        path = np.asarray([[60, 60], [61, 61], [95, 96], [96, 97]], np.int32)
        region = np.zeros(shape, np.bool_)
        region[60:66, 60:66] = True
        region_y, region_x = region_indices(region)
        _, captured = call_with_roi_capture(path, shape, region_y, region_x)
        roi_pixels = captured["src"].shape[0] * captured["src"].shape[1]
        self.assertGreater(roi_pixels, 0)
        self.assertLess(roi_pixels, shape[0] * shape[1] // 10)

    def test_randomized_paths_and_regions_match_the_frozen_reference(self):
        rng = np.random.RandomState(6025)
        for trial in range(120):
            shape = (rng.randint(6, 34), rng.randint(6, 34))
            count = rng.randint(1, 9)
            path = np.column_stack((rng.randint(-3, shape[1] + 3, count),
                                    rng.randint(-3, shape[0] + 3, count))).astype(np.int32)
            if trial % 4 == 0:  # duplicate coordinates exercise the last-write contract
                path[-1] = path[0]
            region = np.zeros(shape, np.bool_)
            region[rng.randint(0, shape[0] - 2):rng.randint(2, shape[0]),
                   rng.randint(0, shape[1] - 2):rng.randint(2, shape[1])] = True
            if not region.any():
                region[0, 0] = True
            with self.subTest(trial=trial):
                self.assert_region_exact(path, shape, region, "trial_{}".format(trial))


if __name__ == "__main__":
    unittest.main()
