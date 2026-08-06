import os
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app.analysis_v2.head_calibration_window import HeadCalibrationWindow
from core.analysis_v2.head_calibration_model import HeadCalibrationModel
from core.analysis_v2.label_image_io import relabel_consecutive


def labels_with_heads():
    labels = np.zeros((40, 50), dtype=np.uint16)
    labels[2:7, 2:7] = 1
    labels[2:7, 12:17] = 2
    labels[2:7, 22:27] = 3
    labels[2:7, 32:37] = 4
    return labels


class FakeHeadCalibrationService:
    def __init__(self, _task_root):
        self.models = {
            "F1": HeadCalibrationModel("F1", labels_with_heads()),
            "F2": HeadCalibrationModel("F2", labels_with_heads()),
        }
        self.fields = [
            SimpleNamespace(field_id=field_id, model=model)
            for field_id, model in self.models.items()
        ]
        self.save_calls = []
        self.completed_fields = []

    def field_ids(self):
        return ["F1", "F2"]

    def available_channels(self, _field_id):
        return ["TRITC", "FITC", "Merge"]

    def default_channel(self, _field_id):
        return "Merge"

    def load_field(self, field_id):
        return next(field for field in self.fields if field.field_id == field_id)

    def image(self, _field_id, channel):
        value = {"TRITC": 30, "FITC": 60, "Merge": 90}[channel]
        return np.full((40, 50, 3), value, dtype=np.uint8)

    def select_object(self, field_id, x, y, toggle=False):
        return self.models[field_id].select_at(x, y, toggle=toggle)

    def delete_selected(self, field_id):
        command = self.models[field_id].delete_selected()
        if command is None:
            return 0
        self.save_field(field_id)
        return len(command.object_ids or (command.object_id,))

    def add_ellipse(self, field_id, start, end):
        command = self.models[field_id].add_ellipse(start, end)
        self.save_field(field_id)
        return command.object_id

    def undo(self, field_id):
        changed = self.models[field_id].undo() is not None
        if changed:
            self.save_field(field_id)
        return changed

    def redo(self, field_id):
        changed = self.models[field_id].redo() is not None
        if changed:
            self.save_field(field_id)
        return changed

    def save_field(self, field_id, completed=False):
        self.save_calls.append((field_id, bool(completed)))
        return {"field_id": field_id, "completed": bool(completed)}

    def complete_field(self, field_id):
        self.completed_fields.append(field_id)
        self.save_field(field_id, completed=True)
        return {"field_id": field_id, "object_count": self.models[field_id].object_count}

    def complete(self):
        for field_id in self.field_ids():
            if field_id not in self.completed_fields:
                self.complete_field(field_id)
        return {"fields": list(self.completed_fields), "state": {"status": "head_calibrated"}}

    def record_failure(self, _exception, _title):
        pass


class HeadCalibrationModelMultiSelectTests(unittest.TestCase):
    def test_single_and_ctrl_multi_selection(self):
        model = HeadCalibrationModel("F1", labels_with_heads())
        model.select_at(3, 3)
        self.assertEqual(model.selected_object_ids, {1})
        model.select_at(13, 3, toggle=True)
        model.select_at(23, 3, toggle=True)
        self.assertEqual(model.selected_object_ids, {1, 2, 3})
        model.select_at(13, 3, toggle=True)
        self.assertEqual(model.selected_object_ids, {1, 3})
        model.select_at(45, 35)
        self.assertEqual(model.selected_object_ids, set())

    def test_multi_delete_undo_redo_is_one_command(self):
        model = HeadCalibrationModel("F1", labels_with_heads())
        for x in (3, 13, 23):
            model.select_at(x, 3, toggle=True)
        command = model.delete_selected()
        self.assertEqual(command.object_ids, (1, 2, 3))
        self.assertEqual(len(model.undo_stack), 1)
        self.assertEqual(model.object_count, 1)
        model.undo()
        self.assertEqual(model.object_count, 4)
        model.redo()
        self.assertEqual(model.object_count, 1)

    def test_two_deletes_undo_step_by_step(self):
        model = HeadCalibrationModel("F1", labels_with_heads())
        model.select_at(3, 3)
        model.delete_selected()
        model.select_at(13, 3)
        model.delete_selected()
        model.undo()
        self.assertIn(2, model.object_ids)
        self.assertNotIn(1, model.object_ids)
        model.undo()
        self.assertIn(1, model.object_ids)

    def test_add_selects_only_new_object(self):
        model = HeadCalibrationModel("F1", labels_with_heads())
        model.select_at(3, 3)
        command = model.add_ellipse((39, 20), (48, 30))
        self.assertEqual(model.selected_object_ids, {command.object_id})

    def test_final_labels_remain_consecutive(self):
        model = HeadCalibrationModel("F1", labels_with_heads())
        model.select_at(13, 3)
        model.delete_selected()
        final_labels, mapping = relabel_consecutive(model.labels)
        self.assertEqual(np.unique(final_labels).tolist(), [0, 1, 2, 3])
        self.assertEqual(mapping, {1: 1, 3: 2, 4: 3})


class HeadCalibrationWindowMultiSelectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self, progressive=False):
        patcher = mock.patch(
            "app.analysis_v2.head_calibration_window.HeadCalibrationService",
            FakeHeadCalibrationService,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        window = HeadCalibrationWindow("unused", progressive_tail=progressive)
        self.addCleanup(window.close_for_shutdown)
        return window

    def test_a_window_multi_highlight_delete_and_keyboard_slot(self):
        window = self.make_window(progressive=False)
        self.assertTrue(window.controls.field_combo.isEnabled())
        for x in (3, 13, 23):
            window._select_at(x, 3, toggle=(x != 3))
        model = window.service.models["F1"]
        self.assertEqual(model.selected_object_ids, {1, 2, 3})
        self.assertGreater(window.canvas._selected_item.path().elementCount(), 0)
        self.assertGreater(window.canvas._selected_item.path().boundingRect().right(), 25.0)
        self.assertIn("已选中 3 个头部", window.statusBar().currentMessage())
        window._delete_selected()
        self.assertEqual(model.object_count, 1)
        self.assertIn("已删除选中的 3 个头部并自动保存", window.statusBar().currentMessage())
        window._undo()
        self.assertEqual(model.object_count, 4)
        window._redo()
        self.assertEqual(model.object_count, 1)

        window._undo()
        for x in (3, 13):
            window._select_at(x, 3, toggle=(x != 3))
        window.show()
        self.app.processEvents()
        QTest.keyClick(window, Qt.Key_Delete)
        self.app.processEvents()
        self.assertEqual(model.object_count, 2)
        window._delete_selected()
        self.assertEqual(window.statusBar().currentMessage(), "当前没有选中头部")

    def test_channel_keeps_selection_and_field_switch_clears_it(self):
        window = self.make_window(progressive=False)
        window._select_at(3, 3)
        window._select_at(13, 3, toggle=True)
        window._channel_changed("FITC")
        self.assertEqual(window.service.models["F1"].selected_object_ids, {1, 2})
        window._field_combo_changed("F2")
        self.assertEqual(window.service.models["F1"].selected_object_ids, set())
        self.assertEqual(window.service.models["F2"].selected_object_ids, set())
        self.assertIn(("F1", False), window.service.save_calls)

    def test_progressive_window_keeps_sequential_rules(self):
        window = self.make_window(progressive=True)
        self.assertFalse(window.controls.field_combo.isEnabled())
        self.assertFalse(window.controls.previous_button.isVisible())
        window._select_at(3, 3)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            window._complete_current_field_and_advance()
        self.assertEqual(window.service.completed_fields, ["F1"])
        self.assertEqual(window.current_field_id, "F2")
        self.assertEqual(window.service.models["F1"].selected_object_ids, set())
        self.assertFalse(window.controls.field_combo.isEnabled())
        self.assertEqual(window.controls.complete_button.text(), "完成最后视野和头部校准")


if __name__ == "__main__":
    unittest.main()
