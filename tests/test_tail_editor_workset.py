import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from tools.analysis_v2.tail_legacy.tail_result_editor_v2_3_draft_mvp import (
    TailResultEditor,
    TailWorksetObject,
    build_path_aware_region_labels,
    build_tail_workset_labels,
)


def make_record(head_id, fragment_id):
    return SimpleNamespace(
        head_id=int(head_id),
        current_status="trusted_auto",
        deleted=False,
        selected_points_xy=np.asarray(
            [[float(fragment_id - 1), 0.0], [float(fragment_id - 1), 1.0]],
            dtype=np.float32,
        ),
        selected_fragment_ids=[int(fragment_id)],
        selected_rank=1,
        selected_source="global_candidate",
        accepted_by_user=False,
        edit_note="",
    )


def make_89_editor():
    editor = object.__new__(TailResultEditor)
    editor.fragment_labels = np.arange(1, 90, dtype=np.uint16).reshape(1, 89)
    editor.records = [make_record(1000 + value, value) for value in range(1, 69)]
    editor.record_by_head = {record.head_id: record for record in editor.records}
    editor.unassigned_tail_candidates = [
        {"fragment_label_id": value} for value in range(69, 90)
    ]
    editor.workset_objects = TailResultEditor._build_initial_workset(editor)
    editor.unassigned_candidate_ids = list(range(69, 90))
    editor.display_fragment_labels = editor.fragment_labels.astype(np.uint32)
    editor.display_scale = 1.0
    editor.artists = {}
    editor.selected_unassigned_candidate_id = None
    editor.selected_index = None
    editor.selected_indices = set()
    editor.edit_history = []
    editor.mode = "idle"
    editor.message = ""
    editor._mark_result_cache_dirty = lambda: None
    editor._autosave_state = lambda: None
    editor.redraw = lambda: None
    return editor


def make_count_editor(accepted_count, associated_count):
    editor = object.__new__(TailResultEditor)
    editor.output_dir = Path("tail-output")
    editor.workset_objects = [
        TailWorksetObject(
            tail_object_id=value,
            fragment_label_id=value,
            accepted=True,
            head_label_id=value if value <= associated_count else None,
            association_status=(
                "associated" if value <= associated_count else "unresolved"
            ),
            source="auto",
            selected_points_xy=np.zeros((0, 2), dtype=np.float32),
            selected_fragment_ids=[value],
        )
        for value in range(1, accepted_count + 1)
    ]
    return editor


class TailEditorWorksetTests(unittest.TestCase):
    def test_save_message_uses_228_accepted_not_187_associated(self):
        editor = make_count_editor(228, 187)
        self.assertEqual(
            editor._save_success_message(),
            "已保存228条尾部结果到：tail-output",
        )

    def test_save_message_all_associated_still_uses_68(self):
        editor = make_count_editor(68, 68)
        self.assertEqual(
            editor._save_success_message(),
            "已保存68条尾部结果到：tail-output",
        )

    def test_save_message_updates_to_227_after_one_deleted(self):
        editor = make_count_editor(228, 187)
        editor.workset_objects[-1].accepted = False
        self.assertEqual(
            editor._save_success_message(),
            "已保存227条尾部结果到：tail-output",
        )

    def test_save_message_does_not_exclude_unresolved_tails(self):
        editor = make_count_editor(228, 187)
        unresolved_count = sum(
            item.association_status == "unresolved"
            for item in editor.workset_objects
        )
        self.assertEqual(unresolved_count, 41)
        self.assertIn("已保存228条尾部结果", editor._save_success_message())

    def test_all_fragments_enter_independent_accepted_workset(self):
        editor = make_89_editor()
        self.assertEqual(len(editor.workset_objects), 89)
        self.assertEqual(
            [item.tail_object_id for item in editor.workset_objects],
            list(range(1, 90)),
        )
        self.assertTrue(all(item.accepted for item in editor.workset_objects))
        associated = [
            item for item in editor.workset_objects
            if item.association_status == "associated"
        ]
        unresolved = [
            item for item in editor.workset_objects
            if item.association_status == "unresolved"
        ]
        self.assertEqual((len(associated), len(unresolved)), (68, 21))
        self.assertTrue(all(item.head_label_id is None for item in unresolved))
        self.assertNotEqual(associated[0].tail_object_id, associated[0].head_label_id)

    def test_unresolved_delete_and_undo_preserve_association_metadata(self):
        editor = make_89_editor()
        editor.selected_unassigned_candidate_id = 69
        editor.delete_selected()
        item = editor._workset_object_for_fragment(69)
        self.assertFalse(item.accepted)
        self.assertEqual(len(editor._accepted_workset_objects()), 88)
        editor.undo_action()
        item = editor._workset_object_for_fragment(69)
        self.assertTrue(item.accepted)
        self.assertEqual(item.association_status, "unresolved")
        self.assertIsNone(item.head_label_id)
        self.assertEqual(len(editor._accepted_workset_objects()), 89)

    def test_associated_delete_and_undo_preserve_association_metadata(self):
        editor = make_89_editor()
        editor.selected_index = 0
        editor.selected_indices = {0}
        editor.delete_selected()
        item = editor._workset_object_for_fragment(1)
        self.assertFalse(item.accepted)
        self.assertEqual(len(editor._accepted_workset_objects()), 88)
        editor.undo_action()
        item = editor._workset_object_for_fragment(1)
        self.assertTrue(item.accepted)
        self.assertEqual(item.association_status, "associated")
        self.assertEqual(item.head_label_id, 1001)

    def test_manual_tail_can_be_unresolved(self):
        editor = make_89_editor()
        item = editor.add_manual_workset_tail(
            np.asarray([[1, 1], [2, 2]], dtype=np.float32),
            [],
        )
        self.assertEqual(item.tail_object_id, 90)
        self.assertTrue(item.accepted)
        self.assertEqual(item.source, "manual")
        self.assertEqual(item.association_status, "unresolved")
        self.assertIsNone(item.head_label_id)

    def test_workset_labels_include_unresolved_exclude_deleted_and_are_continuous(self):
        fragments = np.asarray([[10, 20, 30]], dtype=np.uint16)
        objects = [
            TailWorksetObject(7, 10, True, 501, "associated", "auto",
                              np.zeros((0, 2), dtype=np.float32), [10]),
            TailWorksetObject(8, 20, True, None, "unresolved", "auto",
                              np.zeros((0, 2), dtype=np.float32), [20]),
            TailWorksetObject(9, 30, False, None, "unresolved", "auto",
                              np.zeros((0, 2), dtype=np.float32), [30]),
        ]
        labels, mapping = build_tail_workset_labels(fragments, objects)
        np.testing.assert_array_equal(labels, np.asarray([[1, 2, 0]], dtype=np.uint16))
        self.assertEqual([row["workset_label_id"] for row in mapping], [1, 2])
        self.assertEqual([row["tail_object_id"] for row in mapping], [7, 8])

    def test_workset_save_marks_experimental_contract(self):
        editor = make_89_editor()
        editor.manual_fragment_radius_px = 2
        editor.manual_region_support = np.ones(editor.fragment_labels.shape, dtype=bool)
        with tempfile.TemporaryDirectory() as directory:
            editor.output_dir = Path(directory)
            labels_path, json_path = editor.save_tail_workset()
            labels = np.asarray(Image.open(labels_path))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(set(np.unique(labels).tolist()), set(range(1, 90)))
        self.assertTrue(payload["not_for_measurement"])
        self.assertTrue(payload["not_for_publication"])
        self.assertEqual(payload["accepted_count"], 89)

    def test_formal_region_contract_still_uses_head_id(self):
        fragments = np.asarray([[1, 0]], dtype=np.uint16)
        record = make_record(501, 1)
        labels, conflicts = build_path_aware_region_labels(fragments, [record])
        self.assertEqual(int(labels[0, 0]), 501)
        self.assertEqual(conflicts, [])


if __name__ == "__main__":
    unittest.main()
