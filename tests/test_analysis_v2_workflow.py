from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.analysis_v2.workflow import run_analysis_v2
from app.analysis_window import AnalysisWindow


@pytest.mark.parametrize("interactive", [True, False])
@pytest.mark.parametrize("workflow", ["head", "protein3_tail"])
def test_workflow_forwards_inputs_and_return_value(interactive, workflow):
    items = [{"field_id": "001"}]
    starter = Mock(return_value=object())

    result = run_analysis_v2(
        starter,
        complete_items=items,
        protein_key="protein3",
        protein_name="Q96P56",
        workflow=workflow,
        interactive=interactive,
    )

    starter.assert_called_once_with(
        complete_items=items,
        protein_key="protein3",
        protein_name="Q96P56",
        workflow=workflow,
        interactive=interactive,
    )
    assert starter.call_args.kwargs["complete_items"] is items
    assert result is starter.return_value


def test_workflow_preserves_startup_exception_and_defaults():
    error = RuntimeError("startup failed")
    starter = Mock(side_effect=error)
    with pytest.raises(RuntimeError) as caught:
        run_analysis_v2(
            starter, complete_items=[], protein_key="protein1", protein_name="P1"
        )
    assert caught.value is error
    starter.assert_called_once_with(
        complete_items=[], protein_key="protein1", protein_name="P1",
        workflow="head", interactive=True,
    )


@pytest.mark.parametrize(
    "protein_key, protein_part, workflow",
    [("protein1", "head", "head"), ("protein3", "head", "head"),
     ("protein3", "tail", "protein3_tail")],
)
def test_analysis_window_routes_through_workflow_in_original_order(
    tmp_path, protein_key, protein_part, workflow
):
    items = [{"status": "完整"}]
    formal_items = [{"field_id": "formal"}]
    events = []

    def prepare(**kwargs):
        assert kwargs == {"complete_items": items, "protein_name": "Protein"}
        events.append("prepare")
        return formal_items

    starter = Mock(side_effect=lambda **kwargs: events.append("start"))
    window = SimpleNamespace(
        current_case={"case_no": "test"},
        imported_images=items,
        current_raw_image_folder=str(tmp_path),
        is_analysis_active=lambda: False,
        get_current_protein_key=lambda: protein_key,
        get_current_protein_name=lambda: "Protein",
        config=SimpleNamespace(get_protein_part=lambda key: protein_part),
        imported_images_match_current_protein=lambda *args: True,
        get_existing_analysis_result_for_current_protein=lambda: None,
        append_log=Mock(),
        _prepare_protein3_formal_inputs=prepare,
        _start_head_analysis_v2=starter,
    )

    def enter_workflow(*args, **kwargs):
        events.append("workflow")
        return run_analysis_v2(*args, **kwargs)

    with patch("app.analysis_window.run_analysis_v2", side_effect=enter_workflow) as entry:
        AnalysisWindow.run_analysis(window)

    entry.assert_called_once()
    assert entry.call_args.kwargs["interactive"] is True
    starter.assert_called_once_with(
        complete_items=formal_items if workflow == "protein3_tail" else items,
        protein_key=protein_key,
        protein_name="Protein",
        workflow=workflow,
        interactive=True,
    )
    assert events == (
        ["prepare", "workflow", "start"] if workflow == "protein3_tail"
        else ["workflow", "start"]
    )
