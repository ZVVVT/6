import unittest
from types import SimpleNamespace

import numpy as np

from tools.analysis_v2.c18b_tail_editor_adapter import (
    build_unassigned_tail_candidates,
)
from tools.analysis_v2.tail_legacy.tail_result_editor_v2_3_draft_mvp import (
    EditorRecord,
    TailResultEditor,
    build_path_aware_region_labels,
)


VISIBLE_MISSED_IDS = {5, 8, 38, 86, 96, 97, 110, 112, 117}
UNASSIGNED_IDS = [
    5, 8, 9, 29, 37, 38, 53, 55, 61, 76, 78,
    80, 84, 86, 87, 96, 97, 107, 110, 112, 117,
]


class TailEditorUnassignedCandidateTests(unittest.TestCase):
    def setUp(self):
        self.associated_ids = list(range(201, 269))
        self.all_fragment_ids = self.associated_ids + UNASSIGNED_IDS
        self.fragments = np.asarray(
            self.all_fragment_ids, dtype=np.uint16
        ).reshape(1, 89)
        unmatched = []
        for index, fragment_id in enumerate(UNASSIGNED_IDS):
            row = {
                "c18b_instance_id": fragment_id,
                "reason": (
                    "no_head_within_maximum_distance"
                    if index < 9
                    else "all_candidate_heads_assigned_to_better_tail"
                ),
            }
            if index >= 9:
                row["best_candidate_head_id"] = index
                row["matching_distance_px"] = float(index)
            unmatched.append(row)
        self.payload = build_unassigned_tail_candidates(unmatched)

    def test_89_fragments_are_conserved_as_68_associated_plus_21_candidates(self):
        all_ids = set(self.all_fragment_ids)
        candidate_ids = {
            int(item["fragment_label_id"])
            for item in self.payload["candidates"]
        }
        associated_ids = all_ids - candidate_ids

        self.assertEqual(len(all_ids), 89)
        self.assertEqual(len(associated_ids), 68)
        self.assertEqual(len(candidate_ids), 21)
        self.assertEqual(associated_ids | candidate_ids, all_ids)
        self.assertTrue(VISIBLE_MISSED_IDS.issubset(candidate_ids))

    def test_candidates_load_separately_without_changing_associated_records(self):
        editor = object.__new__(TailResultEditor)
        editor.fragment_labels = self.fragments
        editor.records = [SimpleNamespace(head_id=value) for value in self.associated_ids]
        original_head_ids = [record.head_id for record in editor.records]

        loaded = TailResultEditor._load_unassigned_candidates(editor, self.payload)

        self.assertEqual(len(loaded), 21)
        self.assertEqual([record.head_id for record in editor.records], original_head_ids)
        self.assertTrue(all("association_failure_reason" in item for item in loaded))

    def test_candidates_do_not_automatically_enter_final_region_labels(self):
        accepted_record = SimpleNamespace(
            head_id=201,
            selected_fragment_ids=[self.associated_ids[0]],
            selected_points_xy=np.asarray([[0, 0], [0, 0]], dtype=np.float32),
            selected_source="c18b_instance_centerline",
        )

        labels, conflicts = build_path_aware_region_labels(
            self.fragments,
            [accepted_record],
        )

        self.assertEqual(int(labels[0, 0]), 201)
        candidate_positions = np.arange(68, 89)
        self.assertTrue(np.all(labels[0, candidate_positions] == 0))
        self.assertEqual(conflicts, [])

    def _make_interaction_editor(self, occupied=False):
        editor = object.__new__(TailResultEditor)
        editor.fragment_labels = np.zeros((12, 24), dtype=np.uint16)
        editor.fragment_labels[4:7, 2:9] = 5
        editor.head_labels = np.zeros_like(editor.fragment_labels)
        editor.head_labels[4:7, 15:18] = 1
        editor.probability = np.ones(editor.fragment_labels.shape, dtype=np.float32)
        points = (
            np.asarray([[15, 5], [10, 5]], dtype=np.float32)
            if occupied
            else np.zeros((0, 2), dtype=np.float32)
        )
        record = EditorRecord(
            head_id=1, center_x=16.0, center_y=5.0,
            entry_status="auto_confirmed", initial_status="trusted_auto",
            current_status="trusted_auto" if occupied else "manual_required",
            candidates=[], selected_rank=1 if occupied else None,
            selected_points_xy=points,
            selected_fragment_ids=[99] if occupied else [],
            suggested_points_xy=np.zeros((0, 2), dtype=np.float32),
            selected_source="global_candidate" if occupied else "none",
            accepted_by_user=False, deleted=False, review_reasons=[],
            original_global_status="auto_confirmed_unique" if occupied else "",
        )
        editor.records = [record]
        editor.record_by_head = {1: record}
        editor.unassigned_tail_candidates = [
            {"fragment_label_id": 5,
             "association_failure_reason": "no_head_within_maximum_distance"}
        ]
        editor.unassigned_candidate_ids = [5]
        editor.selected_unassigned_candidate_id = None
        editor.selected_index = None
        editor.selected_indices = set()
        editor.edit_history = []
        editor.mode = "idle"
        editor.display_scale = 1.0
        editor.display_fragment_labels = editor.fragment_labels.astype(np.uint32)
        editor.display_height, editor.display_width = editor.fragment_labels.shape
        editor.artists = {}
        editor._refresh_unassigned_display()
        editor._candidate_path_for_head = lambda *_args: np.asarray(
            [[8, 5], [12, 5], [15, 5]], dtype=np.float32
        )
        editor._mark_result_cache_dirty = lambda: None
        editor._ensure_result_cache = lambda: None
        editor._autosave_state = lambda: None
        editor.redraw = lambda: None
        editor.message = ""
        return editor

    def test_candidate_hit_test_and_selection(self):
        editor = self._make_interaction_editor()
        self.assertEqual(editor._hit_test_unassigned_candidate((5, 5)), 5)
        self.assertEqual(editor._hit_test_unassigned_candidate((9, 5)), 5)
        self.assertEqual(editor._select_at_point((5, 5)), "candidate")
        self.assertEqual(editor.selected_unassigned_candidate_id, 5)

    def test_candidate_and_free_head_becomes_formal_entry_and_updates_counts(self):
        editor = self._make_interaction_editor()
        initial_associated = 68
        initial_candidates = 21
        editor.selected_unassigned_candidate_id = 5
        self.assertTrue(editor._associate_selected_candidate(1))
        self.assertTrue(editor._has_result(editor.records[0]))
        self.assertEqual(editor.records[0].selected_fragment_ids, [5])
        self.assertEqual(editor.records[0].selected_source,
                         "manual_unassigned_candidate")
        self.assertEqual(editor.unassigned_tail_candidates, [])
        self.assertEqual(initial_associated + 1, 69)
        self.assertEqual(initial_candidates - 1, 20)
        output = editor._output_record(editor.records[0])
        self.assertEqual(output["head_id"], 1)
        self.assertEqual(output["selected_fragment_ids"], [5])

    def test_occupied_head_never_silently_replaced(self):
        editor = self._make_interaction_editor(occupied=True)
        old_path = editor.records[0].selected_points_xy.copy()
        editor.selected_unassigned_candidate_id = 5
        editor._confirm_replace_existing_tail = lambda _head_id: False
        self.assertFalse(editor._associate_selected_candidate(1))
        np.testing.assert_array_equal(editor.records[0].selected_points_xy, old_path)
        self.assertEqual(len(editor.unassigned_tail_candidates), 1)
        self.assertEqual(len(editor.edit_history), 0)

    def test_association_undo_restores_candidate_and_previous_head_state(self):
        editor = self._make_interaction_editor(occupied=True)
        old_path = editor.records[0].selected_points_xy.copy()
        editor.selected_unassigned_candidate_id = 5
        editor._confirm_replace_existing_tail = lambda _head_id: True
        self.assertTrue(editor._associate_selected_candidate(1))
        self.assertEqual(len(editor.unassigned_tail_candidates), 0)
        editor.undo_action()
        np.testing.assert_array_equal(editor.records[0].selected_points_xy, old_path)
        self.assertEqual(editor.records[0].selected_fragment_ids, [99])
        self.assertEqual(
            [item["fragment_label_id"] for item in editor.unassigned_tail_candidates],
            [5],
        )

    def test_original_associated_result_is_unchanged_without_confirmation(self):
        editor = self._make_interaction_editor(occupied=True)
        before = editor._output_record(editor.records[0])
        after = editor._output_record(editor.records[0])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
