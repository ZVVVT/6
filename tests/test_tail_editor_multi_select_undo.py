import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from tools.analysis_v2.tail_legacy.tail_result_editor_v2_3_draft_mvp import (
    TailResultEditor,
)


def make_record(head_id, offset=0.0):
    return SimpleNamespace(
        head_id=int(head_id),
        center_x=float(head_id),
        center_y=float(head_id),
        entry_status="auto_confirmed",
        initial_status="trusted_auto",
        current_status="trusted_auto",
        selected_rank=1,
        selected_points_xy=np.asarray(
            [[offset, 0.0], [offset + 5.0, 5.0]], dtype=np.float32
        ),
        selected_fragment_ids=[int(head_id)],
        selected_source="global_candidate",
        accepted_by_user=False,
        deleted=False,
        edit_note="",
    )


def make_editor(count=4):
    editor = object.__new__(TailResultEditor)
    editor.records = [make_record(index + 1, float(index * 10)) for index in range(count)]
    editor.record_by_head = {record.head_id: record for record in editor.records}
    editor.selected_index = None
    editor.selected_indices = set()
    editor.mode = "idle"
    editor.manual_target_index = None
    editor.manual_points = []
    editor.manual_segments = []
    editor.manual_preview = None
    editor.manual_preview_fragment_ids = []
    editor.manual_preview_region_mask = None
    editor.manual_conflict_fragment_ids = []
    editor.edit_history = []
    editor.message = ""
    editor.redraw = lambda: None
    editor._autosave_state = lambda: None
    editor._mark_result_cache_dirty = lambda: None
    return editor


class TailEditorMultiSelectUndoTests(unittest.TestCase):
    def test_single_delete_and_undo(self):
        editor = make_editor()
        editor.selected_index = 0
        editor.selected_indices = {0}
        editor.delete_selected()
        self.assertTrue(editor.records[0].deleted)
        self.assertEqual(editor.message, "已删除选中的 1 条尾部")
        editor.undo_action()
        self.assertFalse(editor.records[0].deleted)

    def test_ctrl_toggle_builds_three_item_selection(self):
        editor = make_editor()
        editor._toggle_result_selection(0)
        editor._toggle_result_selection(1)
        editor._toggle_result_selection(2)
        self.assertEqual(editor.selected_indices, {0, 1, 2})
        editor._toggle_result_selection(1)
        self.assertEqual(editor.selected_indices, {0, 2})

    def test_multi_delete_is_one_history_action(self):
        editor = make_editor()
        editor.selected_index = 2
        editor.selected_indices = {0, 1, 2}
        editor.delete_selected()
        self.assertTrue(all(editor.records[index].deleted for index in range(3)))
        self.assertEqual(len(editor.edit_history), 1)
        self.assertEqual(len(editor.edit_history[0]), 3)
        self.assertEqual(editor.selected_indices, set())
        editor.undo_action()
        self.assertTrue(all(not editor.records[index].deleted for index in range(3)))

    def test_two_deletes_undo_in_reverse_order(self):
        editor = make_editor()
        editor.selected_index = 0
        editor.selected_indices = {0}
        editor.delete_selected()
        editor.selected_index = 1
        editor.selected_indices = {1}
        editor.delete_selected()
        editor.undo_action()
        self.assertTrue(editor.records[0].deleted)
        self.assertFalse(editor.records[1].deleted)
        editor.undo_action()
        self.assertFalse(editor.records[0].deleted)

    def test_added_tail_undo_removes_only_added_result(self):
        editor = make_editor(2)
        editor.records[1].current_status = "manual_required"
        editor.records[1].selected_points_xy = np.zeros((0, 2), dtype=np.float32)
        editor.records[1].selected_fragment_ids = []
        editor._push_history(1)
        editor.records[1].current_status = "user_accepted"
        editor.records[1].selected_points_xy = np.asarray([[1, 1], [2, 2]], dtype=np.float32)
        editor.records[1].selected_fragment_ids = [9]
        editor.undo_action()
        self.assertEqual(editor.records[1].current_status, "manual_required")
        self.assertEqual(len(editor.records[1].selected_points_xy), 0)
        self.assertTrue(editor._has_result(editor.records[0]))

    def test_redraw_undo_restores_old_path(self):
        editor = make_editor(1)
        old_path = editor.records[0].selected_points_xy.copy()
        editor._push_history(0)
        editor.records[0].selected_points_xy = np.asarray([[30, 30], [40, 40]], dtype=np.float32)
        editor.undo_action()
        np.testing.assert_array_equal(editor.records[0].selected_points_xy, old_path)

    def test_drawing_undo_removes_one_point_without_touching_history(self):
        editor = make_editor(1)
        editor.mode = "drawing"
        editor.manual_target_index = 0
        editor.manual_points = [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
        editor.manual_segments = [
            np.asarray([[1, 1], [2, 2]], dtype=np.float32),
            np.asarray([[2, 2], [3, 3]], dtype=np.float32),
        ]
        editor.edit_history = [[(0, deepcopy(editor.records[0]))]]
        editor._refresh_live_preview = lambda: None
        editor.undo_action()
        self.assertEqual(editor.manual_points, [(1.0, 1.0), (2.0, 2.0)])
        self.assertEqual(len(editor.manual_segments), 1)
        self.assertEqual(len(editor.edit_history), 1)

    def test_blank_click_clears_selection(self):
        editor = make_editor()
        editor.selected_index = 1
        editor.selected_indices = {0, 1, 2}
        editor.fragment_labels = np.zeros((10, 10), dtype=np.uint16)
        editor.head_labels = np.zeros((10, 10), dtype=np.uint16)
        editor.display_result_owner_image = np.zeros((10, 10), dtype=np.uint16)
        editor.display_scale = 1.0
        editor.display_width = 10
        editor.display_height = 10
        editor.head_boundary_tree = None
        editor.head_boundary_ids = np.zeros((0,), dtype=np.int32)
        editor.path_tree = None
        editor.path_tree_head_ids = np.zeros((0,), dtype=np.int32)
        editor.head_tree = SimpleNamespace(query=lambda *_args, **_kwargs: (999.0, 0))
        editor._ensure_result_cache = lambda: None
        self.assertEqual(editor._select_at_point((5.0, 5.0)), "none")
        self.assertEqual(editor.selected_indices, set())

    def test_autosave_and_reopen_preserves_result_but_not_history(self):
        editor = make_editor(2)
        with tempfile.TemporaryDirectory() as directory:
            editor.output_dir = Path(directory)
            editor.state_path = editor.output_dir / "editor_state_v2_3_draft_mvp.json"
            editor._autosave_state = TailResultEditor._autosave_state.__get__(editor)
            editor.selected_index = 1
            editor.selected_indices = {1}
            editor.records[1].current_status = "deleted"
            editor.records[1].selected_points_xy = np.zeros((0, 2), dtype=np.float32)
            editor.records[1].selected_fragment_ids = []
            editor.records[1].deleted = True
            editor._autosave_state()

            reopened = make_editor(2)
            reopened.output_dir = editor.output_dir
            reopened.state_path = editor.state_path
            TailResultEditor._load_existing_state(reopened)
            self.assertTrue(reopened.records[1].deleted)
            self.assertEqual(reopened.selected_indices, {1})
            self.assertEqual(reopened.edit_history, [])

if __name__ == "__main__":
    unittest.main()
