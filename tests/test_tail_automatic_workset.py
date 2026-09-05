"""Initial Workset equivalence without importing or constructing the UI."""

import ast
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import MethodType, SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple
import unittest

import numpy as np
from PIL import Image

from core.analysis_v2.opencv_compat import cv2
from core.analysis_v2.tail_calibration_service import (
    build_initial_c18b_tail_workset,
    save_initial_c18b_tail_workset,
)


ROOT = Path(__file__).resolve().parents[1]
EDITOR_SOURCE = ROOT / "tools/analysis_v2/tail_legacy/tail_result_editor_v2_3_draft_mvp.py"


def editor_reference(adapter_dir, output_dir):
    # Execute the actual, unchanged business methods as an independent oracle.
    # No Editor import, constructor, object.__new__, figure, or UI save button.
    tree = ast.parse(EDITOR_SOURCE.read_text(encoding="utf-8-sig"))
    names = {"EditorRecord", "TailWorksetObject", "ensure_points",
             "fragment_ids_near_path", "build_tail_workset_labels"}
    methods = {"_build_records", "_build_initial_workset", "_has_result",
               "_accepted_workset_objects", "save_tail_workset"}
    nodes = [node for node in tree.body if getattr(node, "name", "") in names]
    editor_class = next(node for node in tree.body
                        if getattr(node, "name", "") == "TailResultEditor")
    nodes.extend(node for node in editor_class.body
                 if getattr(node, "name", "") in methods)
    namespace = dict(globals())
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(EDITOR_SOURCE), "exec"),
         namespace)
    with Image.open(adapter_dir / "fragments.tif") as image:
        fragments = np.asarray(image).copy()
    context = SimpleNamespace(
        fragment_labels=fragments, manual_fragment_radius_px=2,
        manual_region_support=None, output_dir=output_dir,
        ACCEPTED_STATUSES={"trusted_auto", "user_accepted"},
        unassigned_tail_candidates=[],
    )
    for name in methods:
        setattr(context, name, MethodType(namespace[name], context))
    payloads = [json.loads((adapter_dir / name).read_text(encoding="utf-8"))
                for name in ("entries.json", "paths.json", "global_results.json")]
    context.records = context._build_records(*payloads)
    context.workset_objects = context._build_initial_workset()
    output_dir.mkdir()
    return context.save_tail_workset()


class TailAutomaticWorksetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.adapter = self.root / "adapter"
        self.adapter.mkdir()
        self.head_path = self.root / "heads.tif"

    def fixture(self, fragment_ids, associated):
        fragments = np.zeros((4, len(fragment_ids) + 1), dtype=np.uint16)
        heads = np.zeros_like(fragments)
        entries, paths, global_results = [], [], []
        for index, fragment_id in enumerate(fragment_ids):
            fragments[1:3, index] = fragment_id
            head_id = 101 + index
            heads[0, index] = head_id
            entries.append({"head_id": head_id, "center_x": index, "center_y": 0,
                            "status": "auto_confirmed" if fragment_id in associated
                            else "manual_required"})
            if fragment_id in associated:
                candidate = {"rank": 1, "points_xy": [[index, 1], [index, 2]],
                             "selected_fragment_ids": [fragment_id],
                             "source": "c18b_instance_centerline"}
                paths.append({"head_id": head_id, "candidates": [candidate]})
                global_results.append({"head_id": head_id,
                                       "status": "auto_confirmed_unique",
                                       "selected_rank": 1, "selected_candidate": candidate})
        Image.fromarray(fragments).save(self.adapter / "fragments.tif")
        Image.fromarray(heads).save(self.head_path)
        for name, results in (("entries.json", entries), ("paths.json", paths),
                              ("global_results.json", global_results)):
            self.write(name, {"version": 1, "results": results})
        self.write("unassigned_tail_candidates.json", {"candidates": []})
        return fragments

    def write(self, name, payload):
        (self.adapter / name).write_text(json.dumps(payload), encoding="utf-8")

    def compare(self, expected_associated):
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in list(self.adapter.iterdir()) + [self.head_path]}
        paths = save_initial_c18b_tail_workset(
            self.adapter, self.head_path, self.root / "automatic")
        reference_paths = editor_reference(self.adapter, self.root / "reference")
        with Image.open(paths[0]) as image:
            labels = np.asarray(image).copy()
        with Image.open(reference_paths[0]) as image:
            reference_labels = np.asarray(image).copy()
        np.testing.assert_array_equal(labels, reference_labels)
        self.assertEqual(labels.dtype, reference_labels.dtype)
        self.assertEqual(labels.dtype, np.uint16)
        payload = json.loads(paths[1].read_text(encoding="utf-8"))
        reference = json.loads(reference_paths[1].read_text(encoding="utf-8"))
        payload.pop("saved_at_unix")
        reference.pop("saved_at_unix")
        self.assertEqual(payload, reference)  # all fields, metadata and object order
        with Image.open(self.adapter / "fragments.tif") as image:
            fragments = np.asarray(image).copy()
        ids = sorted(int(value) for value in np.unique(fragments) if value > 0)
        rows = payload["objects"]
        accepted = sum(row["accepted"] for row in rows)
        associated = sum(row["association_status"] == "associated" for row in rows)
        unresolved = sum(row["association_status"] == "unresolved" for row in rows)
        self.assertEqual(accepted, len(ids))
        self.assertEqual(payload["accepted_count"], accepted)
        self.assertEqual(associated + unresolved, accepted)
        self.assertEqual(associated, expected_associated)
        self.assertEqual([row["fragment_label_id"] for row in rows], ids)
        for index, row in enumerate(rows, 1):
            self.assertEqual(row["source"], "auto")
            self.assertEqual(row["workset_label_id"], index)
            np.testing.assert_array_equal(labels == index, fragments == row["fragment_label_id"])
            if row["association_status"] == "unresolved":
                self.assertIsNone(row["head_label_id"])
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})

    def test_all_associated(self):
        self.fixture([1, 2, 3], {1, 2, 3})
        self.compare(3)

    def test_mixed(self):
        self.fixture([1, 2, 3], {2})
        self.compare(1)

    def test_all_unresolved(self):
        self.fixture([1, 2, 3], set())
        self.compare(0)

    def test_sparse_ids(self):
        self.fixture([20, 1, 5], {20, 5})
        self.compare(2)

    def test_skipped_match_without_path_global_or_updated_entry(self):
        self.fixture([1, 3, 8, 20], {1})
        self.write("manifest.json", {"matching": {"skipped_matches": [
            {"c18b_instance_id": 8, "head_id": 103,
             "reason": "ordered_centerline_has_fewer_than_two_points"}]}})
        self.compare(1)

    def test_missing_unassigned_file_does_not_drop_fragments(self):
        self.fixture([1, 5, 20], {1})
        (self.adapter / "unassigned_tail_candidates.json").unlink()
        self.compare(1)

    def test_short_centerline_is_unresolved(self):
        self.fixture([1, 5], {1, 5})
        path = self.adapter / "global_results.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["results"][1]["selected_candidate"]["points_xy"] = [[1, 1]]
        self.write(path.name, payload)
        self.compare(1)

    def test_untrusted_entry_is_unresolved(self):
        self.fixture([1, 5], {1, 5})
        path = self.adapter / "entries.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["results"][1]["status"] = "manual_required"
        self.write(path.name, payload)
        self.compare(1)

    def test_untrusted_global_is_unresolved(self):
        self.fixture([1, 5], {1, 5})
        path = self.adapter / "global_results.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["results"][1]["status"] = "manual_required"
        self.write(path.name, payload)
        self.compare(1)

    def test_path_cannot_restore_filtered_fragment(self):
        fragments = self.fixture([1, 5, 20], {1, 5, 20})
        fragments[fragments == 5] = 0
        Image.fromarray(fragments).save(self.adapter / "fragments.tif")
        self.compare(2)

    def test_nonexistent_head_is_unresolved(self):
        self.fixture([1], {1})
        Image.fromarray(np.zeros((4, 2), dtype=np.uint16)).save(self.head_path)
        _, payload = build_initial_c18b_tail_workset(self.adapter, self.head_path)
        self.assertTrue(payload["objects"][0]["accepted"])
        self.assertIsNone(payload["objects"][0]["head_label_id"])

    def test_no_nearest_fragment_fallback(self):
        self.fixture([1], {1})
        self.write("paths.json", {"results": []})
        _, payload = build_initial_c18b_tail_workset(self.adapter, self.head_path)
        self.assertEqual(payload["objects"][0]["association_status"], "unresolved")

    def test_relative_paths_rejected(self):
        with self.assertRaises(ValueError):
            build_initial_c18b_tail_workset(Path("adapter"), self.head_path)
        with self.assertRaises(ValueError):
            save_initial_c18b_tail_workset(self.adapter, self.head_path, Path("output"))

    def test_backend_runs_with_ui_imports_blocked(self):
        self.fixture([1, 5, 20], {1})
        script = '''
import importlib.abc
import sys
from pathlib import Path
class BlockUI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("PySide6", "PyQt", "matplotlib")) or "tail_result_editor" in fullname:
            raise AssertionError("UI import: " + fullname)
sys.meta_path.insert(0, BlockUI())
sys.path.insert(0, sys.argv[1])
from core.analysis_v2.tail_calibration_service import save_initial_c18b_tail_workset
save_initial_c18b_tail_workset(*map(Path, sys.argv[2:]))
'''
        result = subprocess.run(
            [sys.executable, "-c", script, str(ROOT), str(self.adapter),
             str(self.head_path), str(self.root / "subprocess")],
            cwd=str(self.root), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_adapter_read_only_equivalence(self):
        directory = ROOT / (
            "workspace/tail_e2e_not_for_publication/CASE20260904163259/"
            "20260904_163345_be6989/ZBFY023-C-1/calibration/tail/ZBFY023-C-1"
        )
        manifest = directory / "manifest.json"
        if not manifest.is_file():
            self.skipTest("Local real Adapter fixture unavailable")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        head_path = Path(payload["sources"]["head_labels"]["path"])
        if not head_path.is_file():
            self.skipTest("Local real Head labels unavailable")
        self.adapter = directory
        self.head_path = head_path
        self.compare(68)


if __name__ == "__main__":
    unittest.main()
