from pathlib import Path

import pandas as pd

from core.result_parser import ResultParser


IMAGE_COLUMNS = [
    "ImageNumber",
    "Count_R_objects",
    "Count_G_colocalized",
    "Math_ColocalizationRate",
]


def write_image_csv(output_dir: Path, rows) -> None:
    pd.DataFrame(rows, columns=IMAGE_COLUMNS).to_csv(
        output_dir / "Image.csv", index=False
    )


def write_object_csv(output_dir: Path, rows=None, columns=None) -> None:
    pd.DataFrame(
        rows or [],
        columns=columns or ["ImageNumber", "Math_MeanIntensity255"],
    ).to_csv(output_dir / "G_colocalized.csv", index=False)


def parse_head(output_dir: Path) -> dict:
    return ResultParser(str(output_dir), protein_part="head").parse_image_summary()


def test_normal_head_result_is_unchanged(tmp_path):
    write_image_csv(tmp_path, [[1, 2, 1, 0.5]])
    write_object_csv(tmp_path, [[1, 80]])

    result = parse_head(tmp_path)

    assert result["success"] is True
    assert result["total"]["sperm_count"] == 2
    assert result["total"]["positive_count"] == 1
    assert result["total"]["expression_rate"] == 50.0
    assert result["total"]["mean255_sum"] == 80.0


def test_header_only_object_csv_is_valid_for_zero_head_colocalization(tmp_path):
    write_image_csv(tmp_path, [[1, 3, 0, 0]])
    write_object_csv(tmp_path)

    result = parse_head(tmp_path)

    assert result["success"] is True
    assert result["message"] == "解析成功。"
    assert result["total"]["positive_count"] == 0
    assert result["total"]["expression_rate"] == 0
    assert result["total"]["mean255_sum"] == 0
    assert result["total"]["mean_intensity"] == 0


def test_zero_head_objects_is_not_treated_as_valid_zero_result(tmp_path):
    write_image_csv(tmp_path, [[1, 0, 0, 0]])
    write_object_csv(tmp_path)

    result = parse_head(tmp_path)

    assert result["success"] is False
    assert "Count_R_objects 不大于 0" in result["message"]


def test_missing_object_csv_still_fails(tmp_path):
    write_image_csv(tmp_path, [[1, 3, 0, 0]])

    result = parse_head(tmp_path)

    assert result["success"] is False
    assert "未找到对象级 CSV" in result["message"]


def test_broken_object_csv_header_still_fails(tmp_path):
    write_image_csv(tmp_path, [[1, 3, 0, 0]])
    write_object_csv(tmp_path, columns=["ImageNumber", "BrokenColumn"])

    result = parse_head(tmp_path)

    assert result["success"] is False
    assert "缺少 Math_MeanIntensity255" in result["message"]


def test_empty_object_csv_does_not_hide_invalid_head_count(tmp_path):
    write_image_csv(tmp_path, [[1, "broken", 0, 0]])
    write_object_csv(tmp_path)

    result = parse_head(tmp_path)

    assert result["success"] is False
    assert "Count_R_objects 无法取得有效数值" in result["message"]


def test_empty_object_csv_is_rejected_when_image_claims_positive_objects(tmp_path):
    write_image_csv(tmp_path, [[1, 3, 1, 1 / 3]])
    write_object_csv(tmp_path)

    result = parse_head(tmp_path)

    assert result["success"] is False
    assert "对象级 CSV 为空" in result["message"]


def test_batch_shared_parser_accepts_zero_head_colocalization(tmp_path):
    write_image_csv(tmp_path, [[1, 3, 0, 0]])
    write_object_csv(tmp_path)

    # batch_analysis_dialog 的 BatchAnalysisWorker 调用
    # ProteinAnalysisService；该服务的 parse_result 直接使用 ResultParser。
    result = ResultParser(
        str(tmp_path), protein_part="head"
    ).parse_image_summary(protein_part="head")

    assert result["success"] is True
    assert result["total"]["positive_count"] == 0
    assert result["total"]["expression_rate"] == 0
