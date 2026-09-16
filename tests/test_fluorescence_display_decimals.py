"""F5: 荧光强度精度持久化与 display_decimals 显示合同。

历史问题：ResultParser 同时输出旧兼容整数 ``mean_intensity`` 与精确
``mean_intensity_raw``；旧链只把整数写入 DB 并交给显示层，导致
``display_decimals`` 无论设置成几，最终只能看到 ``61`` / ``61.0`` / ``61.00``。

本文件覆盖：
1. ``_preferred_intensity`` 优先 raw、缺失时回落旧字段；
2. Publisher 汇总与逐视野写入 DB 的精度；
3. ResultViewer 现场解析时同样使用 raw；
4. display_decimals 0~4 的固定小数位合同。
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.result_viewer import ResultViewer
from core.analysis_v2.result_completion_service import (
    _preferred_intensity,
    publish_measured_completion,
)
from core.analysis_v2.task_supervisor import TaskSupervisor
from core.config_manager import ConfigManager
from core.result_adjustment_service import ResultAdjustmentService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StubAdjustmentConfig:
    """ResultAdjustmentService 需要的最小配置面。"""

    def __init__(self, decimals=1):
        self.decimals = decimals

    def is_result_adjustment_enabled(self):
        return True

    def get_fluorescence_result_factor(self):
        return 1.0

    def get_expression_rate_result_factor(self):
        return 1.0

    def is_sync_positive_count_with_expression_rate(self):
        return False

    def is_use_case_tail_rate_for_head_intensity(self):
        return False

    def get_result_display_decimals(self):
        return self.decimals

    def get_default_tail_rate_ratio(self):
        return 1.0


def adjustment_service(decimals):
    return ResultAdjustmentService(None, StubAdjustmentConfig(decimals))


def formatted_intensity(value, decimals):
    service = adjustment_service(decimals)
    adjusted = service.adjust_result(
        case_id=None,
        protein_key="protein3",
        protein_part="tail",
        raw_mean_intensity=value,
        raw_expression_rate=66.33,
        raw_total_sperm_count=98,
        raw_positive_count=65,
    )
    return service.format_intensity(adjusted.get("adjusted_mean_intensity"))


def viewer_harness(decimals=2):
    return SimpleNamespace(
        adjustment_service=adjustment_service(decimals),
        case_id=None,
        protein_key="protein3",
        protein_part="tail",
    )


def publication_owner():
    owner = TaskSupervisor()
    owner.expect_publication()
    return owner


def completion_payload(tmp_path, part="tail", with_raw=True):
    total = {
        "field_count": 1,
        "sperm_count": 98,
        "positive_count": 65,
        "mean_intensity": 61,
        "expression_rate": 66.33,
    }
    row = {
        "image_number": "1",
        "sperm_count": 98,
        "positive_count": 65,
        "mean_intensity": 61,
        "expression_rate": 66.33,
    }
    if with_raw:
        total["mean_intensity_raw"] = 61.2171
        row["mean_intensity_raw"] = 61.2171
    summary = {
        "success": True,
        "calculation_mode": "head_equivalent",
        "total": total,
        "rows": [row],
        "image_csv": "Image.csv",
    }
    payload = {
        "status": "measured",
        "part": part,
        "protein_key": "protein3" if part == "tail" else "protein1",
        "protein_name": "Q96P56" if part == "tail" else "Q9BYW3",
        "task_root": str(tmp_path / "task"),
        "source_dir": tmp_path / "candidate",
        "target_dir": tmp_path / "formal",
        "expected_field_count": 1,
        "measurement_contract": {"result_parser": summary},
        "context": {
            "case_id": 7,
            "case_no": "CASE-001",
            "protein_key": "protein3" if part == "tail" else "protein1",
            "protein_name": "Q96P56" if part == "tail" else "Q9BYW3",
            "raw_image_folder": "raw-images",
        },
    }
    return payload, summary


def publish_with_summary(tmp_path, part, summary):
    payload, _summary = completion_payload(tmp_path, part=part)
    published = Mock()
    published.summary = summary
    published.commit.return_value = ""
    database = Mock()
    database.replace_protein_analysis_with_fields.return_value = 5
    target = "core.analysis_v2.result_completion_service.stage_{}_measurement_output".format(part)
    with patch(target, return_value=published):
        publish_measured_completion(payload, database, supervisor=publication_owner())
    return database.replace_protein_analysis_with_fields.call_args.kwargs


# ----------------------------------------------------------------------
# 1. preferred intensity 语义
# ----------------------------------------------------------------------


def test_preferred_intensity_uses_raw_value():
    assert _preferred_intensity(
        {"mean_intensity": 61, "mean_intensity_raw": 61.2171}
    ) == pytest.approx(61.2171)


def test_preferred_intensity_falls_back_to_legacy_integer():
    assert _preferred_intensity({"mean_intensity": 61}) == pytest.approx(61.0)
    assert _preferred_intensity({"mean_intensity_raw": None, "mean_intensity": 61}) == pytest.approx(61.0)
    assert _preferred_intensity({}) == 0
    assert _preferred_intensity({}, default=3) == 3


@pytest.mark.parametrize("value", ["abc", object(), float("nan"), float("inf"), float("-inf")])
def test_preferred_intensity_uses_default_for_invalid_raw(value):
    assert _preferred_intensity({"mean_intensity_raw": value, "mean_intensity": 61}) == 0


def test_preferred_intensity_uses_default_for_invalid_legacy_value():
    assert _preferred_intensity({"mean_intensity": "abc"}) == 0


# ----------------------------------------------------------------------
# 2. display_decimals 合同
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "decimals,expected",
    [(0, "61"), (1, "61.2"), (2, "61.22"), (3, "61.217"), (4, "61.2171")],
)
def test_display_decimals_contract(decimals, expected):
    assert formatted_intensity(61.2171, decimals) == expected


def test_fixed_decimals_keep_trailing_zero():
    assert formatted_intensity(61.2, 2) == "61.20"


@pytest.mark.parametrize(
    "value,decimals,expected",
    [
        (0, 2, "0.00"),
        (61.0, 1, "61.0"),
        (0.4567, 2, "0.46"),
        (0.4567, 4, "0.4567"),
        (123.4567, 3, "123.457"),
    ],
)
def test_display_decimals_boundaries(value, decimals, expected):
    assert formatted_intensity(value, decimals) == expected


def test_regression_integerized_input_collapses_decimals():
    # 旧链行为：只有 int(round(raw)) 进入格式化层
    assert formatted_intensity(61, 2) == "61.00"
    # 修复后：raw 进入同一条格式化链
    assert formatted_intensity(61.2171, 2) == "61.22"


def test_display_decimals_come_from_temp_config(tmp_path):
    source = (PROJECT_ROOT / "config.ini").read_text(encoding="utf-8-sig")
    temp_config_path = tmp_path / "config.ini"
    temp_config_path.write_text(
        source.replace("display_decimals = 1", "display_decimals = 2"),
        encoding="utf-8",
    )
    config = ConfigManager(str(temp_config_path))

    assert config.get_result_display_decimals() == 2
    service = ResultAdjustmentService(None, config)
    adjusted = service.adjust_result(
        case_id=None,
        protein_key="protein3",
        protein_part="tail",
        raw_mean_intensity=61.2171,
        raw_expression_rate=66.33,
        raw_total_sperm_count=98,
        raw_positive_count=65,
    )
    assert service.format_intensity(adjusted.get("adjusted_mean_intensity")) == "61.22"


# ----------------------------------------------------------------------
# 3. Publisher → DB 精度
# ----------------------------------------------------------------------


@pytest.mark.parametrize("part", ["head", "tail"])
def test_publisher_persists_raw_precision(tmp_path, part):
    _payload, summary = completion_payload(tmp_path, part=part)

    kwargs = publish_with_summary(tmp_path, part, summary)

    assert kwargs["mean_intensity"] == pytest.approx(61.2171)
    assert kwargs["field_results"][0]["mean_intensity"] == pytest.approx(61.2171)
    assert kwargs["total_sperm_count"] == 98
    assert kwargs["positive_count"] == 65
    assert kwargs["expression_rate"] == pytest.approx(66.33)


@pytest.mark.parametrize("part", ["head", "tail"])
def test_publisher_keeps_legacy_fallback_without_raw(tmp_path, part):
    _payload, summary = completion_payload(tmp_path, part=part, with_raw=False)

    kwargs = publish_with_summary(tmp_path, part, summary)

    assert kwargs["mean_intensity"] == pytest.approx(61.0)
    assert kwargs["field_results"][0]["mean_intensity"] == pytest.approx(61.0)


# ----------------------------------------------------------------------
# 4. ResultViewer 现场解析
# ----------------------------------------------------------------------


def test_result_viewer_prefers_raw_when_parsing_output():
    viewer = viewer_harness(decimals=2)
    item = {
        "mean_intensity": 61,
        "mean_intensity_raw": 61.2171,
        "expression_rate": 66.33,
        "positive_count": 65,
        "sperm_count": 98,
    }

    adjusted = ResultViewer._adjust_item(viewer, item)

    assert viewer.adjustment_service.format_intensity(
        adjusted.get("adjusted_mean_intensity")
    ) == "61.22"


def test_result_viewer_keeps_legacy_row_working():
    viewer = viewer_harness(decimals=2)
    item = {
        "mean_intensity": 61.0,
        "expression_rate": 66.33,
        "positive_count": 65,
        "sperm_count": 98,
    }

    adjusted = ResultViewer._adjust_item(viewer, item)

    assert viewer.adjustment_service.format_intensity(
        adjusted.get("adjusted_mean_intensity")
    ) == "61.00"


def test_result_viewer_without_adjustment_service_uses_raw():
    viewer = SimpleNamespace(adjustment_service=None)

    adjusted = ResultViewer._adjust_item(
        viewer, {"mean_intensity": 61, "mean_intensity_raw": 61.2171}
    )

    assert adjusted["adjusted_mean_intensity"] == pytest.approx(61.2171)


def test_page_and_report_share_display_decimals():
    service = adjustment_service(2)
    viewer = SimpleNamespace(
        adjustment_service=service,
        case_id=None,
        protein_key="protein3",
        protein_part="tail",
    )
    # 修复后 DB 行的 mean_intensity 已经是 raw 精度（报告链输入）。
    db_row_intensity = 61.2171

    report_adjusted = service.adjust_result(
        case_id=None,
        protein_key="protein3",
        protein_part="tail",
        raw_mean_intensity=db_row_intensity,
        raw_expression_rate=66.33,
        raw_total_sperm_count=98,
        raw_positive_count=65,
    )
    report_text = service.format_intensity(report_adjusted.get("adjusted_mean_intensity"))

    page_adjusted = ResultViewer._adjust_item(
        viewer,
        {
            "mean_intensity": 61,
            "mean_intensity_raw": 61.2171,
            "expression_rate": 66.33,
            "positive_count": 65,
            "sperm_count": 98,
        },
    )
    page_text = service.format_intensity(page_adjusted.get("adjusted_mean_intensity"))

    assert report_text == "61.22"
    assert page_text == report_text
