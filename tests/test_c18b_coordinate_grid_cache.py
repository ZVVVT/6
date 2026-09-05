import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
C18B_DIR = PROJECT_ROOT / "tools" / "analysis_v2" / "c18b_score015"
sys.path.insert(0, str(C18B_DIR))
try:
    GEOMETRY = importlib.import_module("graph_constrained_instance_separation")
    IDENTITY = importlib.import_module("identity_graph_v3")
finally:
    sys.path.remove(str(C18B_DIR))


class C18BCoordinateGridCacheTests(unittest.TestCase):
    @staticmethod
    def _reference_path_geometry(path, shape, coordinate_grid):
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
        lut_x = np.zeros(len(xs) + 1, np.float32)
        lut_y = np.zeros(len(xs) + 1, np.float32)
        lut_tx = np.zeros(len(xs) + 1, np.float32)
        lut_ty = np.zeros(len(xs) + 1, np.float32)
        lut_x[1:], lut_y[1:] = xs, ys
        lut_tx[1:], lut_ty[1:] = tangent_x[ys, xs], tangent_y[ys, xs]
        safe = np.minimum(nearest, len(xs))
        vx = coordinate_grid[1] - lut_x[safe]
        vy = coordinate_grid[0] - lut_y[safe]
        align = (np.abs(vx * lut_tx[safe] + vy * lut_ty[safe]) /
                 np.maximum(distance, 1.))
        return distance.astype(np.float32), np.clip(align, 0., 1.), seed

    def assert_exact_array(self, expected, actual):
        self.assertEqual(expected.dtype, actual.dtype)
        self.assertEqual(expected.shape, actual.shape)
        expected_nan = np.isnan(expected) if expected.dtype.kind == "f" else None
        if expected_nan is not None:
            np.testing.assert_array_equal(expected_nan, np.isnan(actual))
            np.testing.assert_array_equal(expected[~expected_nan],
                                          actual[~expected_nan])
        else:
            np.testing.assert_array_equal(expected, actual)

    def test_cached_grid_matches_original_indices(self):
        shape = (7, 11)
        cached = np.indices(shape, dtype=np.float32)
        original_y = np.indices(shape, dtype=np.float32)[0]
        original_x = np.indices(shape, dtype=np.float32)[1]
        self.assertEqual(cached.dtype, np.float32)
        np.testing.assert_array_equal(cached[0], original_y)
        np.testing.assert_array_equal(cached[1], original_x)

    def test_path_geometry_cached_and_uncached_are_identical(self):
        shape = (15, 17)
        path = np.asarray([[2, 3], [3, 4], [4, 5], [5, 5]], dtype=np.int32)
        uncached = GEOMETRY._path_geometry(path, shape)
        cached = GEOMETRY._path_geometry(
            path, shape, np.indices(shape, dtype=np.float32)
        )
        for before, after in zip(uncached, cached):
            np.testing.assert_array_equal(before, after)

    def test_path_geometry_matches_reference_exactly(self):
        shape = (31, 43)
        path = np.asarray([
            [3, 4], [4, 5], [6, 7], [9, 8], [12, 8], [15, 10],
            [18, 13], [22, 15], [27, 17], [32, 20], [37, 24],
        ], dtype=np.int32)
        coordinate_grid = np.indices(shape, dtype=np.float32)
        expected = self._reference_path_geometry(path, shape, coordinate_grid)
        actual = GEOMETRY._path_geometry(path, shape, coordinate_grid)
        for expected_array, actual_array in zip(expected, actual):
            self.assert_exact_array(expected_array, actual_array)

    def test_wrong_shape_grid_is_rejected(self):
        path = np.asarray([[1, 1], [2, 2]], dtype=np.int32)
        with self.assertRaises(ValueError):
            GEOMETRY._path_geometry(
                path, (6, 8), np.indices((8, 6), dtype=np.float32)
            )

    def test_reconstruct_cache_lifetime_is_one_call(self):
        grown = np.ones((9, 10), dtype=np.uint16)
        fitc = np.full(grown.shape, 50, dtype=np.float32)
        groups = [[
            np.asarray([[1, 1], [2, 2], [3, 3]], dtype=np.int32),
            np.asarray([[7, 1], [7, 2], [7, 3]], dtype=np.int32),
        ]]
        membership = {"P1:F1": 1, "P1:F2": 2}
        original_indices = np.indices
        with mock.patch.object(
                IDENTITY.np, "indices", wraps=original_indices) as indices:
            first = IDENTITY.reconstruct(
                grown, fitc, groups, membership, 3.0, 0.8
            )
            second = IDENTITY.reconstruct(
                grown, fitc, groups, membership, 3.0, 0.8
            )
        self.assertEqual(indices.call_count, 2)
        np.testing.assert_array_equal(first, second)

    def test_reconstruct_final_labels_match_uncached_geometry(self):
        grown = np.ones((9, 10), dtype=np.uint16)
        fitc = np.full(grown.shape, 50, dtype=np.float32)
        groups = [[
            np.asarray([[1, 1], [2, 2], [3, 3]], dtype=np.int32),
            np.asarray([[7, 1], [7, 2], [7, 3]], dtype=np.int32),
        ]]
        membership = {"P1:F1": 1, "P1:F2": 2}
        cached = IDENTITY.reconstruct(
            grown, fitc, groups, membership, 3.0, 0.8
        )

        original_geometry = IDENTITY._path_geometry

        def uncached_geometry(path, shape, coordinate_grid):
            return original_geometry(path, shape)

        with mock.patch.object(
                IDENTITY, "_path_geometry", side_effect=uncached_geometry):
            uncached = IDENTITY.reconstruct(
                grown, fitc, groups, membership, 3.0, 0.8
            )
        np.testing.assert_array_equal(cached, uncached)


if __name__ == "__main__":
    unittest.main()
