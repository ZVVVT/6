import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog

from app.settings_window import SettingsWindow


class ProteinConfigStub:
    def __init__(self, items):
        self._items = items

    def get_protein_items(self):
        return self._items


def _app():
    return QApplication.instance() or QApplication([])


def _window_with_rows(rows):
    _app()
    window = SettingsWindow()
    window.protein_table.setRowCount(0)
    for row in rows:
        window.add_protein_row(**row)
    return window


def test_load_keeps_saved_custom_pipeline_without_automatic_override():
    _app()
    window = SettingsWindow()
    try:
        custom_pipeline = r"D:\custom\saved_pipeline.cppipe"
        window.config = ProteinConfigStub(
            [
                {
                    "key": "protein1",
                    "name": "P1",
                    "part": "head",
                    "custom_pipeline": custom_pipeline,
                    "intensity_min": 26.0,
                    "rate_min": 82.88,
                }
            ]
        )

        window.load_protein_table()

        assert window.get_table_text(0, 3) == custom_pipeline
        assert window.get_part_combo_from_row(0).currentText() == "head"
    finally:
        window.close()


def test_part_switches_use_standard_pipeline_and_keep_rows_isolated():
    window = _window_with_rows(
        [
            {"key": "protein1", "name": "P1", "part": "head", "pipeline": "one.cppipe"},
            {"key": "protein2", "name": "P2", "part": "tail", "pipeline": "two.cppipe"},
        ]
    )
    try:
        first_combo = window.get_part_combo_from_row(0)
        second_combo = window.get_part_combo_from_row(1)

        first_combo.setCurrentText("tail")
        assert window.get_table_text(0, 3) == r"pipelines\pipeline_tail.cppipe"
        assert window.get_table_text(1, 3) == "two.cppipe"

        second_combo.setCurrentText("head")
        assert window.get_table_text(1, 3) == r"pipelines\pipeline_head.cppipe"
        assert window.get_table_text(0, 3) == r"pipelines\pipeline_tail.cppipe"
    finally:
        window.close()


def test_manual_selection_keeps_part_then_next_switch_restores_standard_pipeline(monkeypatch):
    window = _window_with_rows(
        [{"key": "protein1", "name": "P1", "part": "head", "pipeline": "saved.cppipe"}]
    )
    try:
        combo = window.get_part_combo_from_row(0)
        button = window.get_button_from_row(0)
        selected = r"D:\custom\manual.cppipe"
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: (selected, ""))

        window.select_protein_pipeline_for_button(button)

        assert window.get_table_text(0, 3) == selected
        assert combo.currentText() == "head"

        combo.setCurrentText("tail")
        assert window.get_table_text(0, 3) == r"pipelines\pipeline_tail.cppipe"
        assert combo.currentText() == "tail"
    finally:
        window.close()
