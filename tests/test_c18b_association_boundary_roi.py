"""Association erosion boundaries remain exact when evaluated in 1px ROIs."""

import importlib.util
import os
from pathlib import Path

import cv2
import numpy as np
import pytest


ADAPTER = (
    Path(__file__).resolve().parents[1]
    / "tools" / "analysis_v2" / "c18b_tail_editor_adapter.py"
)
SPEC = importlib.util.spec_from_file_location("c18b_boundary_roi_test", ADAPTER)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)
KERNEL = np.ones((3, 3), dtype=np.uint8)


def _old(labels, label_id):
    mask = (labels == label_id).astype(np.uint8)
    eroded = cv2.erode(mask, KERNEL, iterations=1)
    boundary = np.argwhere((mask > 0) & (eroded == 0))
    count = int(np.count_nonzero(mask))
    return np.argwhere(mask > 0), boundary, count, _sample(boundary)


def _new(labels, label_id):
    bbox = adapter._raw_label_bboxes(
        labels, adapter.positive_ids(labels)
    )[label_id]
    boundary, count = adapter._boundary_from_label_roi(
        labels, label_id, bbox, KERNEL
    )
    y0, y1, x0, x1 = bbox
    pixels = np.argwhere(labels[y0:y1, x0:x1] == label_id)
    pixels[:, 0] += y0
    pixels[:, 1] += x0
    return pixels, boundary, count, _sample(boundary)


def _sample(boundary):
    if len(boundary) > 2048:
        indices = np.linspace(0, len(boundary) - 1, 2048).astype(np.int64)
        boundary = boundary[indices]
    return boundary.astype(np.float32)


def _assert_exact(labels, label_id):
    old = _old(labels, label_id)
    new = _new(labels, label_id)
    for old_array, new_array in zip((old[0], old[1], old[3]),
                                    (new[0], new[1], new[3])):
        assert old_array.dtype == new_array.dtype
        assert np.array_equal(old_array, new_array)
    assert old[2] == new[2]


@pytest.mark.parametrize("pixel", [
    (0, 0), (0, 8), (8, 0), (8, 8),
    (0, 4), (8, 4), (4, 0), (4, 8),
    (1, 4), (7, 4), (4, 1), (4, 7), (4, 4),
])
def test_single_pixel_edges_corners_and_one_pixel_offsets(pixel):
    labels = np.zeros((9, 9), dtype=np.uint16)
    labels[pixel] = 7
    _assert_exact(labels, 7)


@pytest.mark.parametrize("region", [
    (slice(0, 1), slice(1, 8)),
    (slice(8, 9), slice(1, 8)),
    (slice(1, 8), slice(0, 1)),
    (slice(1, 8), slice(8, 9)),
    (slice(2, 3), slice(2, 8)),
    (slice(2, 8), slice(2, 3)),
])
def test_one_pixel_wide_and_narrow_objects(region):
    labels = np.zeros((9, 9), dtype=np.uint16)
    labels[region] = 11
    _assert_exact(labels, 11)


def test_hole_adjacent_labels_and_large_sampling_preserve_order():
    labels = np.zeros((80, 90), dtype=np.uint16)
    labels[2:70, 3:80] = 3
    labels[20:45, 25:55] = 0
    labels[10:60, 80:84] = 9
    labels[25:55, 79] = 9
    labels[70:80, 0:12] = 17
    for label_id in (3, 9, 17):
        _assert_exact(labels, label_id)


def test_contract_constants_and_raw_id_order():
    assert adapter.EROSION_BOUNDARY_HALO_PX == 1
    labels = np.array([[9, 0, 2], [0, 5, 0]], dtype=np.uint16)
    assert adapter.positive_ids(labels) == [2, 5, 9]


@pytest.mark.skipif(
    os.environ.get("C18B_A2B_REAL_GATE") != "1",
    reason="explicit three-field boundary geometry gate",
)
@pytest.mark.parametrize("field,case,run", [
    ("023", "CASE20260908102941", "20260908_103450_acad3c"),
    ("016", "CASE20260908104300", "20260908_104320_f4c8fc"),
    ("022", "CASE20260908104656", "20260908_104716_af7f2c"),
])
def test_real_field_tail_head_boundary_count_and_sampling_exact(field, case, run):
    root = ADAPTER.parents[2]
    run_root = (root / "workspace" / "cases" / case / "analysis_v2"
                / "protein3" / "runs" / run)
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
    assert instances is not None and heads is not None
    tail_mismatch = head_mismatch = sampling_mismatch = 0
    for labels, kind in ((instances, "tail"), (heads, "head")):
        for label_id in adapter.positive_ids(labels):
            old = _old(labels, label_id)
            new = _new(labels, label_id)
            mismatch = int(
                not np.array_equal(old[1], new[1]) or old[2] != new[2]
            )
            if kind == "tail":
                tail_mismatch += mismatch
                sampling_mismatch += int(not np.array_equal(old[3], new[3]))
            else:
                head_mismatch += mismatch
    assert tail_mismatch == head_mismatch == sampling_mismatch == 0
    print("A2B_GEOMETRY {} tail_mismatch={} head_mismatch={} "
          "sampling_mismatch={}".format(
              field, tail_mismatch, head_mismatch, sampling_mismatch
          ), flush=True)
