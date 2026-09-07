import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox

from app.settings_window import SettingsWindow


FORMAL_PROTEINS = [
    ("protein1", "Q9BYW3", "head", r"pipelines\pipeline_head.cppipe"),
    ("protein2", "P10323", "head", r"pipelines\pipeline_head.cppipe"),
    ("protein3", "Q96P56", "tail", r"pipelines\pipeline_tail.cppipe"),
    ("protein4", "Q8IYV9", "head", r"pipelines\pipeline_head.cppipe"),
    ("protein5", "W5XKT8", "head", r"pipelines\pipeline_head.cppipe"),
]


class RecordingConfig:
    def __init__(self):
        self.values = {}

    def set(self, section, key, value):
        self.values.setdefault(section, {})[key] = value


def _app():
    return QApplication.instance() or QApplication([])


def _formal_window():
    _app()
    window = SettingsWindow()
    window.protein_table.setRowCount(0)
    for key, name, part, pipeline in FORMAL_PROTEINS:
        window.add_protein_row(
            key=key,
            name=name,
            part=part,
            pipeline=pipeline,
            intensity_min="26.0",
            rate_min="82.88",
        )
    return window


def test_formal_identity_fields_are_read_only_and_pipeline_ui_is_unavailable():
    window = _formal_window()
    try:
        for row in range(window.protein_table.rowCount()):
            for column in (0, 1):
                assert not window.protein_table.item(row, column).flags() & Qt.ItemIsEditable
            assert window.protein_table.cellWidget(row, 6) is None

        for row in (0, 1, 3, 4):
            assert not window.protein_table.item(row, 2).flags() & Qt.ItemIsEditable
            assert window.get_table_text(row, 2) == "head"
            assert window.get_part_combo_from_row(row) is None

        protein3_combo = window.get_part_combo_from_row(2)
        assert isinstance(protein3_combo, QComboBox)
        assert [protein3_combo.itemText(index) for index in range(protein3_combo.count())] == [
            "head", "tail"
        ]
        assert protein3_combo.currentText() == "tail"

        assert window.protein_table.isColumnHidden(3)
        assert window.protein_table.isColumnHidden(6)
    finally:
        window.close()


@pytest.mark.parametrize("value", ["0", "26.5", "1000000000"])
def test_non_negative_finite_intensity_can_be_saved(value):
    window = _formal_window()
    try:
        window.config = RecordingConfig()
        window.protein_table.item(0, 4).setText(value)
        ok, _ = window.save_protein_table_to_config()
        assert ok
        assert window.config.values["ProteinReferenceIntensityMin"]["protein1"] == value
    finally:
        window.close()


@pytest.mark.parametrize(
    ("value", "expected_message"),
    [
        ("-0.01", "不能小于 0"),
        ("nan", "有限数字"),
        ("inf", "有限数字"),
        ("-inf", "有限数字"),
    ],
)
def test_invalid_intensity_is_rejected(value, expected_message):
    window = _formal_window()
    try:
        window.config = RecordingConfig()
        window.protein_table.item(0, 4).setText(value)
        ok, message = window.save_protein_table_to_config()
        assert not ok
        assert expected_message in message
    finally:
        window.close()


@pytest.mark.parametrize("value", ["0", "100"])
def test_rate_range_boundaries_can_be_saved(value):
    window = _formal_window()
    try:
        window.config = RecordingConfig()
        window.protein_table.item(0, 5).setText(value)
        ok, _ = window.save_protein_table_to_config()
        assert ok
        assert window.config.values["ProteinReferenceRateMin"]["protein1"] == value
    finally:
        window.close()


@pytest.mark.parametrize(
    ("value", "expected_message"),
    [
        ("-0.01", "0~100"),
        ("100.01", "0~100"),
        ("nan", "有限数字"),
        ("inf", "有限数字"),
        ("-inf", "有限数字"),
    ],
)
def test_invalid_rate_is_rejected(value, expected_message):
    window = _formal_window()
    try:
        window.config = RecordingConfig()
        window.protein_table.item(0, 5).setText(value)
        ok, message = window.save_protein_table_to_config()
        assert not ok
        assert expected_message in message
    finally:
        window.close()


@pytest.mark.parametrize("part", ["head", "tail"])
def test_protein3_part_is_saved_without_touching_legacy_pipelines(part):
    window = _formal_window()
    try:
        window.config = RecordingConfig()
        window.get_part_combo_from_row(2).setCurrentText(part)
        ok, _ = window.save_protein_table_to_config()
        assert ok

        assert window.config.values["ProteinOrder"]["keys"] == ",".join(
            protein[0] for protein in FORMAL_PROTEINS
        )
        assert window.config.values["Protein"] == {
            key: part if key == "protein3" else formal_part
            for key, _, formal_part, _ in FORMAL_PROTEINS
        }
        assert window.config.values["ProteinNames"] == {
            key: name for key, name, _, _ in FORMAL_PROTEINS
        }
        assert "ProteinPipelines" not in window.config.values
    finally:
        window.close()


@pytest.mark.parametrize("row", [0, 1, 3, 4])
def test_non_protein3_tail_is_rejected_even_if_table_is_tampered(row):
    window = _formal_window()
    try:
        window.config = RecordingConfig()
        window.protein_table.item(row, 2).setText("tail")
        ok, message = window.save_protein_table_to_config()
        assert not ok
        assert "必须是 head" in message
    finally:
        window.close()
