"""Automatic Workset uses the formal contract without constructing the Editor."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest

from core.analysis_v2.label_image_io import read_label_image
from core.analysis_v2.tail_calibration_service import (
    build_automatic_tail_final_contract,
    build_tail_final_contract,
    save_initial_c18b_tail_workset,
)
import test_tail_automatic_workset as workset_tests

ROOT = workset_tests.ROOT
editor_reference = workset_tests.editor_reference


@pytest.fixture
def adapter_case():
    case = workset_tests.TailAutomaticWorksetTests()
    case.setUp()
    try:
        yield case
    finally:
        case.doCleanups()


def payload(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def business(path):
    return {key: value for key, value in payload(path).items()
            if key not in {"region_label_path", "head_id_label_path",
                           "positive_head_label_path"}}


def compare_contracts(result, reference):
    for key in ("tail_final_labels", "tail_positive_head_labels",
                "tail_final_head_id_labels"):
        np.testing.assert_array_equal(
            read_label_image(Path(result[key]), require_objects=False),
            read_label_image(Path(reference[key]), require_objects=False))
    assert business(result["tail_final_objects"]) == business(reference["tail_final_objects"])


def assert_contract(result, counts, workset_dir, head_path):
    data = payload(result["tail_final_objects"])
    keys = ("tail_object_count", "associated_object_count", "unresolved_object_count")
    assert tuple(result[key] for key in keys) == counts
    assert tuple(data[key] for key in keys) == counts
    assert counts[0] == counts[1] + counts[2]
    labels = read_label_image(Path(result["tail_final_labels"]))
    positive = read_label_image(Path(result["tail_positive_head_labels"]), require_objects=False)
    heads = read_label_image(head_path)
    workset = read_label_image(workset_dir / "TailWorksetLabels.tif")
    rows = sorted((row for row in payload(workset_dir / "tail_workset.json")["objects"]
                   if row["accepted"]), key=lambda row: row["tail_object_id"])
    assert list(np.unique(labels)) == list(range(counts[0] + 1))
    assert [row["tail_object_id"] for row in data["objects"]] == list(range(1, counts[0] + 1))
    associated_ids = [row["tail_object_id"] for row in data["objects"]
                      if row["association_status"] == "associated"]
    assert list(np.unique(positive)) == [0] + associated_ids
    for object_id, (row, final) in enumerate(zip(rows, data["objects"]), 1):
        np.testing.assert_array_equal(labels == object_id, workset == row["workset_label_id"])
        assert final["head_label_id"] == row["head_label_id"]
        assert final["association_status"] == row["association_status"]
        if final["association_status"] == "associated":
            np.testing.assert_array_equal(positive == object_id, heads == row["head_label_id"])
        else:
            assert final["head_label_id"] is None
            assert not np.any(positive == object_id)


@pytest.mark.parametrize("total,associated", [(68, 68), (89, 68), (12, 0)])
def test_counts_and_editor_business_equivalence(adapter_case, total, associated):
    case = adapter_case
    case.fixture(list(range(1, total + 1)), set(range(1, associated + 1)))
    output = case.root / "automatic"
    save_initial_c18b_tail_workset(case.adapter, case.head_path, output)
    result = build_automatic_tail_final_contract("field", output, case.head_path)
    editor_reference(case.adapter, case.root / "reference")
    reference = build_tail_final_contract("field", case.root / "reference", case.head_path)
    compare_contracts(result, reference)
    assert_contract(result, (total, associated, total - associated), output, case.head_path)


def test_sparse_deleted_and_skipped_match(adapter_case):
    case = adapter_case
    case.fixture([1, 5, 10, 20], {1, 5})
    case.write("manifest.json", {"matching": {"skipped_matches": [
        {"c18b_instance_id": 20, "reason": "ordered_centerline_has_fewer_than_two_points"}]}})
    output = case.root / "automatic"
    labels_path, json_path = save_initial_c18b_tail_workset(case.adapter, case.head_path, output)
    data = payload(json_path)
    # Preserve sparse original IDs in both the Workset and its metadata.
    labels = read_label_image(labels_path)
    sparse = np.zeros_like(labels)
    for row in data["objects"]:
        sparse[labels == row["workset_label_id"]] = row["fragment_label_id"]
        row["tail_object_id"] = row["workset_label_id"] = row["fragment_label_id"]
        row["accepted"] = row["fragment_label_id"] != 10
    data["objects"].reverse()  # builder must sort, rather than trust JSON order
    Image.fromarray(sparse).save(labels_path)
    json_path.write_text(json.dumps(data), encoding="utf-8")
    result = build_automatic_tail_final_contract("field", output, case.head_path)
    assert_contract(result, (3, 2, 1), output, case.head_path)
    final = payload(result["tail_final_objects"])
    assert [row["fragment_label_id"] for row in final["objects"]] == [1, 5, 20]
    assert not np.any(read_label_image(Path(result["tail_final_labels"]))[sparse == 10])


def test_nonexistent_associated_head_is_rejected(adapter_case):
    case = adapter_case
    case.fixture([1], {1})
    output = case.root / "automatic"
    _, path = save_initial_c18b_tail_workset(case.adapter, case.head_path, output)
    data = payload(path)
    data["objects"][0]["head_label_id"] = 999
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="HeadFinalLabels"):
        build_automatic_tail_final_contract("field", output, case.head_path)
    assert not list(output.glob("*TailFinal*"))


@pytest.mark.parametrize("missing", ["TailWorksetLabels.tif", "tail_workset.json"])
def test_missing_workset_does_not_fall_back_to_legacy(adapter_case, missing):
    case = adapter_case
    case.fixture([1], {1})
    output = case.root / "automatic"
    save_initial_c18b_tail_workset(case.adapter, case.head_path, output)
    shutil.copy2(output / "TailWorksetLabels.tif", output / "edited_tail_regions_head_id_uint16.tif")
    (output / missing).unlink()
    with pytest.raises(FileNotFoundError, match="Workset"):
        build_automatic_tail_final_contract("field", output, case.head_path)


def test_backend_blocks_ui_and_downstream_calls(adapter_case):
    case = adapter_case
    case.fixture([1, 5], {1})
    output = case.root / "automatic"
    save_initial_c18b_tail_workset(case.adapter, case.head_path, output)
    script = '''
import importlib.abc
import sys
from pathlib import Path
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname.startswith(("PyQt", "PySide", "matplotlib", "app."))
                or "tail_result_editor" in fullname):
            raise AssertionError("Forbidden import: " + fullname)
sys.meta_path.insert(0, Block())
sys.path.insert(0, sys.argv[1])
from core.analysis_v2.tail_calibration_service import build_automatic_tail_final_contract
def guard(frame, event, arg):
    if event == "call":
        module = frame.f_globals.get("__name__", "")
        if (any(word in module for word in ("measurement", "publisher", "database"))
                or frame.f_code.co_name in ("publish_tail_final_labels", "complete_tail_calibration")):
            raise AssertionError("Forbidden call: " + module + "." + frame.f_code.co_name)
sys.setprofile(guard)
build_automatic_tail_final_contract("field", Path(sys.argv[2]), Path(sys.argv[3]))
sys.setprofile(None)
'''
    result = subprocess.run([sys.executable, "-c", script, str(ROOT), str(output),
                             str(case.head_path)], cwd=str(case.root), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert {path.name for path in output.iterdir()} == {
        "TailWorksetLabels.tif", "tail_workset.json", "field_TailFinalLabels.tif",
        "field_TailPositiveHeadLabels.tif", "field_TailFinalHeadIdLabels.tif",
        "field_TailFinalObjects.json"}


def test_real_adapter_and_existing_formal_baseline(tmp_path):
    directory = ROOT / (
        "workspace/tail_e2e_not_for_publication/CASE20260904163259/"
        "20260904_163345_be6989/ZBFY023-C-1/calibration/tail/ZBFY023-C-1")
    if not (directory / "manifest.json").is_file():
        pytest.skip("Local real Adapter unavailable")
    head = Path(payload(directory / "manifest.json")["sources"]["head_labels"]["path"])
    if not head.is_file():
        pytest.skip("Local real Head labels unavailable")
    field = "ZBFY023-C-1"
    sources = list(directory.iterdir()) + [head]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sources if path.is_file()}
    output = tmp_path / "automatic"
    save_initial_c18b_tail_workset(directory, head, output)
    result = build_automatic_tail_final_contract(field, output, head)
    assert_contract(result, (89, 68, 21), output, head)
    editor_reference(directory, tmp_path / "reference")
    compare_contracts(result, build_tail_final_contract(field, tmp_path / "reference", head))
    baseline = {key: str(directory / (field + suffix)) for key, suffix in (
        ("tail_final_labels", "_TailFinalLabels.tif"),
        ("tail_positive_head_labels", "_TailPositiveHeadLabels.tif"),
        ("tail_final_head_id_labels", "_TailFinalHeadIdLabels.tif"),
        ("tail_final_objects", "_TailFinalObjects.json"))}
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}
    if not all(Path(path).is_file() for path in baseline.values()):
        pytest.skip("89/68/21 and Editor business-method equivalence passed; stored baseline unavailable")
    compare_contracts(result, baseline)
