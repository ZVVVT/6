"""Regression coverage for reopening legacy and Analysis V2 protein3 results."""

import os

import pandas as pd
import pytest

from core.result_parser import ResultParser


def write_tail_result(output_dir, legacy=False, both_columns=False):
    pd.DataFrame([{
        "ImageNumber": 1,
        "Count_R_objects": 300,
        "Count_G_objects": 228,
        "Count_R_colocalized": 187,
        "Math_ColocalizationRate": 187 / 300,
    }]).to_csv(output_dir / "Image.csv", index=False)
    objects = pd.DataFrame({
        "ImageNumber": [1] * 228,
        "ObjectNumber": list(range(1, 229)),
        "AreaShape_Area": [10] * 228,
    })
    if legacy or both_columns:
        objects["Math_IntegratedIntensity255"] = 1000
    if not legacy or both_columns:
        objects["Math_MeanIntensity255"] = 60
    objects.to_csv(output_dir / "G_objects.csv", index=False)


@pytest.mark.parametrize("part", ["", "tail", "尾部"])
@pytest.mark.parametrize("legacy,both_columns", [(True, False), (True, True), (False, False)])
def test_tail_parser_reopen(tmp_path, part, legacy, both_columns):
    write_tail_result(tmp_path, legacy, both_columns)
    result = ResultParser(str(tmp_path), protein_part=part).parse_image_summary()

    assert result["success"], result["message"]
    assert result["calculation_mode"] == ("legacy" if legacy else "head_equivalent")
    assert result["warnings"] == []
    assert result["rows"][0]["g_objects_count"] == 228
    assert result["rows"][0]["positive_count"] == 187
    assert result["total"]["area_sum"] == 2280
    assert result["total"]["sperm_count"] == 300
    assert result["total"]["positive_count"] == 187
    expected = 100 * 187 / 300 if legacy else 228 * 60 / 300
    assert result["total"]["mean_intensity_raw"] == pytest.approx(expected, abs=0.0001)


def test_explicit_calculation_mode_still_takes_priority(tmp_path):
    write_tail_result(tmp_path)
    result = ResultParser(
        str(tmp_path), protein_part="tail", calculation_mode="legacy"
    ).parse_image_summary()
    assert not result["success"]
    assert "Math_IntegratedIntensity255" in result["message"]


def test_v2_object_row_count_mismatch_is_still_reported(tmp_path):
    write_tail_result(tmp_path)
    path = tmp_path / "G_objects.csv"
    pd.read_csv(path).iloc[:-1].to_csv(path, index=False)
    result = ResultParser(str(tmp_path), protein_part="tail").parse_image_summary()
    assert result["success"]
    assert len(result["warnings"]) == 1
    assert "对象行数=227" in result["warnings"][0]


@pytest.mark.parametrize("legacy", [True, False])
def test_result_viewer_reopens_protein3_tail(tmp_path, legacy):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from app.result_viewer import ResultViewer
    from core.config_manager import ConfigManager

    application = QApplication.instance() or QApplication([])
    write_tail_result(tmp_path, legacy=legacy)
    viewer = ResultViewer(config=ConfigManager(str(tmp_path / "config.ini")))
    try:
        viewer.set_result_context(protein_key="protein3", protein_part="tail")
        viewer.set_output_dir(str(tmp_path))
        viewer.refresh_results()
        assert viewer.summary_data["success"], viewer.summary_data["message"]
        assert viewer.summary_data["calculation_mode"] == (
            "legacy" if legacy else "head_equivalent"
        )
        assert viewer.summary_data["warnings"] == []
        assert viewer.summary_rows[0]["g_objects_count"] == 228
        assert viewer.summary_total["positive_count"] == 187
        assert viewer.summary_table.rowCount() == 2
        assert viewer.summary_table.item(0, 2).text() == "187"
        assert viewer.summary_total["mean_intensity_raw"] == pytest.approx(
            100 * 187 / 300 if legacy else 45.6, abs=0.0001
        )
    finally:
        viewer.close()
        viewer.deleteLater()
        application.processEvents()
