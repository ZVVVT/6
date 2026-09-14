import csv
from pathlib import Path

import cv2
import numpy as np
import tifffile
import json
import os
import pytest

from core.analysis_v2.lightweight_tail_measurement import (
    _boundary, generate_tail_measurement, validate_standardized_tail_fields,
)
from core.analysis_v2.tail_measurement_service import validate_tail_measurement_output


def _field(root, name, tail, head, positive):
    root.mkdir()
    g = np.zeros((5, 6, 3), dtype=np.uint8)
    g[..., 1] = np.arange(30, dtype=np.uint8).reshape(5, 6)
    r = np.full_like(g, 31)
    paths = {}
    for key, value in (("g", g), ("r", r)):
        path = root / (name + "_" + key + ".tif")
        tifffile.imwrite(str(path), value)
        paths[key + "_path"] = str(path)
    for key, value in (("head_final_labels", head),
                       ("tail_final_labels", tail),
                       ("tail_positive_head_labels", positive)):
        path = root / (name + "_" + key + ".tif")
        tifffile.imwrite(str(path), value)
        paths[key + "_path"] = str(path)
    paths.update(field_id=name, tail_object_count=int(tail.max()),
                 expected_object_count=int(tail.max()),
                 head_object_count=len(np.unique(head[head != 0])),
                 associated_object_count=len(np.unique(positive[positive != 0])))
    return paths


def test_multifield_sort_csv_and_overlay_contract(tmp_path):
    a_tail = np.zeros((5, 6), dtype=np.uint16)
    a_tail[0, 0:2] = 1
    a_tail[2, 2:4] = 2
    a_head = np.zeros_like(a_tail)
    a_head[0, 0:2] = 1
    a_head[2, 2:4] = 2
    a_positive = np.where(a_tail == 1, a_tail, 0).astype(np.uint16)
    b_tail = np.zeros_like(a_tail)
    b_tail[1, 1:3] = 1
    b_head = np.zeros_like(a_tail)
    b_head[1, 1:3] = 1
    b_positive = b_tail.copy()
    b = _field(tmp_path / "b", "b", b_tail, b_head, b_positive)
    a = _field(tmp_path / "a", "a", a_tail, a_head, a_positive)
    records = [b, a]
    validate_standardized_tail_fields(records)
    output = tmp_path / "candidate"
    generate_tail_measurement(records, output)
    with (output / "Image.csv").open(newline="", encoding="utf-8") as handle:
        images = list(csv.DictReader(handle))
    with (output / "G_objects.csv").open(newline="", encoding="utf-8") as handle:
        objects = list(csv.DictReader(handle))
    assert [row["ImageNumber"] for row in images] == ["1", "2"]
    assert [(row["ImageNumber"], row["ObjectNumber"]) for row in objects] == [
        ("1", "1"), ("1", "2"), ("2", "1")]
    assert (output / "Image.csv").read_bytes().startswith(
        b"Count_G_objects,Count_R_colocalized,Count_R_objects,ImageNumber,Math_ColocalizationRate\r\n")
    assert len(list(output.glob("*_OrigOverlay.png"))) == 6
    overlay = cv2.imread(str(output / "a_G_G_objects_OrigOverlay.png"))
    assert np.array_equal(overlay[0, 0], [0, 0, 255])
    assert np.array_equal(overlay[4, 5], [0, 29, 0])
    assert validate_tail_measurement_output(output, records)["field_count"] == 2


def test_reflect_four_neighbor_boundary_covers_border_contact_and_hole():
    labels = np.zeros((7, 8), dtype=np.uint16)
    labels[:4, :4] = 1
    labels[1:3, 4:7] = 2
    labels[2, 2] = 0
    labels[6, 7] = 3
    expected = np.zeros_like(labels, dtype=bool)
    height, width = labels.shape
    for y in range(height):
        for x in range(width):
            values = [labels[y, x], labels[max(y - 1, 0), x],
                      labels[min(y + 1, height - 1), x],
                      labels[y, max(x - 1, 0)],
                      labels[y, min(x + 1, width - 1)]]
            expected[y, x] = max(values) != min(values)
    assert np.array_equal(_boundary(labels), expected)
    assert expected[2, 2] and expected[0, 3] and expected[6, 7]


@pytest.mark.parametrize("case_id,run_id", [
    ("CASE20260908102941", "20260910_171956_72a1f1"),
    ("CASE20260908103925", "20260910_172228_52c552"),
    ("CASE20260908104300", "20260910_172440_fe97b9"),
    ("CASE20260908104656", "20260910_172658_4e5240"),
])
def test_frozen_m1c_tier1_exact_if_available(tmp_path, case_id, run_id):
    root = Path(__file__).resolve().parents[1] / "workspace" / "cases" / case_id
    measurement = root / "analysis_v2" / "protein3" / "runs" / run_id / "measurement" / "tail"
    if not (measurement / "measurement_input.json").is_file():
        pytest.skip("Frozen M1C workspace fixture unavailable")
    records = json.loads((measurement / "measurement_input.json").read_text(encoding="utf-8"))["fields"]
    baseline = measurement / "candidate_output"
    output = tmp_path / "candidate"
    validate_standardized_tail_fields(records)
    generate_tail_measurement(records, output)
    for name in ("Image.csv", "G_objects.csv"):
        assert (output / name).read_bytes() == (baseline / name).read_bytes()
    for candidate in output.glob("*_OrigOverlay.png"):
        actual = cv2.imread(str(candidate), cv2.IMREAD_UNCHANGED)
        expected = cv2.imread(str(baseline / candidate.name), cv2.IMREAD_UNCHANGED)
        assert np.array_equal(actual, expected)
    assert validate_tail_measurement_output(output, records)["field_count"] == 1


def test_frozen_multifield_repeated_rss_if_available(tmp_path):
    fixtures = (
        ("CASE20260908102941", "20260910_171956_72a1f1"),
        ("CASE20260908103925", "20260910_172228_52c552"),
        ("CASE20260908104300", "20260910_172440_fe97b9"),
        ("CASE20260908104656", "20260910_172658_4e5240"),
    )
    root = Path(__file__).resolve().parents[1] / "workspace" / "cases"
    records = []
    for case_id, run_id in fixtures:
        path = root / case_id / "analysis_v2" / "protein3" / "runs" / run_id
        path = path / "measurement" / "tail" / "measurement_input.json"
        if not path.is_file():
            pytest.skip("Frozen M1C workspace fixture unavailable")
        records.extend(json.loads(path.read_text(encoding="utf-8"))["fields"])
    records.reverse()
    validate_standardized_tail_fields(records)
    rss = []
    for index in range(3):
        output = tmp_path / ("candidate_{}".format(index))
        generate_tail_measurement(records, output)
        result = validate_tail_measurement_output(output, records)
        assert result["field_count"] == 4
        assert result["expected_object_count"] == 318
        assert len(list(output.glob("*_OrigOverlay.png"))) == 12
        if os.name == "nt":
            import ctypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = (
                ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong)
            process = ctypes.windll.kernel32.GetCurrentProcess()
            assert ctypes.windll.psapi.GetProcessMemoryInfo(
                process, ctypes.byref(counters), counters.cb)
            rss.append(round(counters.WorkingSetSize / (1024 * 1024), 1))
    if rss:
        print("Repeated four-field RSS MiB: {}".format(rss))
