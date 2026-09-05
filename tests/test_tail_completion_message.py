"""Exercise the actual UI message code without loading Qt or running analysis."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "app" / "analysis_window.py"


def _method(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


@pytest.mark.parametrize("tail_count, associated_count", [(77, 65), (65, 65), (12, 0)])
def test_completion_message_distinguishes_tail_and_association_counts(
    tail_count, associated_count
):
    method = _method("_on_tail_measurement_finished")
    success_block = next(node for node in method.body if isinstance(node, ast.Try))
    # Run the final display block, after publication and database saving.
    start = next(
        index for index, node in enumerate(success_block.body)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "total"
                for target in node.targets)
    )
    display_code = ast.Module(body=success_block.body[start:], type_ignores=[])
    message_box = Mock()
    window = Mock()
    window._worker_is_running.return_value = False
    namespace = {
        "self": window,
        "QMessageBox": message_box,
        "parsed_result": {"total": {
            "field_count": 2, "sperm_count": 100,
            "positive_count": associated_count,
            "mean_intensity_raw": 12.3456, "expression_rate": 65,
        }},
        "validation": {"tail_object_count": tail_count},
        "total_elapsed": 1.25,
        "target_dir": "output",
        "save_message": "保存成功",
    }
    exec(compile(display_code, str(SOURCE), "exec"), namespace)
    message_box.information.assert_called_once()
    _, title, message = message_box.information.call_args[0]
    assert title == "尾部分析完成"
    assert "识别尾部数：{}\n".format(tail_count) in message
    assert "关联尾部数：{}\n".format(associated_count) in message
    assert "未关联尾部数：{}\n".format(tail_count - associated_count) in message
    assert "有效尾部数" not in message
    assert "精子总数：100\n" in message
    assert "C 荧光强度：12.3456\n" in message
    assert "标定率：65%" in message


def test_save_message_names_associated_count_without_changing_database_value(tmp_path):
    method = _method("_save_tail_analysis_v2_to_database")
    namespace = {"Path": Path}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), "exec"), namespace)
    database = Mock()
    window = SimpleNamespace(database=database, format_rate_for_display=str)
    message = namespace[method.name](
        window,
        {"case_id": 1, "protein_name": "Q96P56"},
        tmp_path,
        {"success": True, "calculation_mode": "head_equivalent",
         "total": {"field_count": 2, "sperm_count": 100, "positive_count": 65,
                   "mean_intensity": 12, "expression_rate": 65}, "rows": []},
    )
    assert "关联尾部数 65，" in message
    assert "有效尾部数" not in message
    database.replace_protein_analysis_with_fields.assert_called_once()
    assert database.replace_protein_analysis_with_fields.call_args[1]["positive_count"] == 65
