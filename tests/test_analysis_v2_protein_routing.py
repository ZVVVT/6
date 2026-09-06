from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_WINDOW = PROJECT_ROOT / "app" / "analysis_window.py"
COMPLETION_SERVICE = PROJECT_ROOT / "core" / "analysis_v2" / "result_completion_service.py"


def _source():
    return ANALYSIS_WINDOW.read_text(encoding="utf-8")


def test_protein3_tail_is_the_only_protein3_tail_workflow_entry():
    run_source = _source()[_source().index("def run_analysis"):]
    condition = 'if protein_key == "protein3" and protein_part == "tail":'
    tail_route = run_source.index(condition)
    head_route = run_source.index('if protein_part == "head":', tail_route)
    tail_block = run_source[tail_route:head_route]

    assert 'workflow="protein3_tail"' in tail_block
    assert "return" in tail_block
    assert 'if protein_key == "protein3":' not in run_source


def test_only_formal_head_proteins_use_head_workflow():
    run_source = _source()[_source().index("def run_analysis"):]
    head_route = run_source.index('if protein_part == "head":')
    head_block = run_source[head_route:run_source.index("def imported_images_match_current_protein")]

    assert "run_analysis_v2(" in head_block
    assert "self._start_head_analysis_v2," in head_block
    assert 'workflow="protein3_tail"' not in head_block
    assert "return" in head_block


def test_formal_mapping_is_checked_before_any_analysis_route():
    run_source = _source()[_source().index("def run_analysis"):]
    validation = run_source.index("FORMAL_PROTEIN_PARTS.get(protein_key)")
    tail_route = run_source.index(
        'if protein_key == "protein3" and protein_part == "tail":'
    )
    head_route = run_source.index('if protein_part == "head":')

    assert validation < tail_route < head_route
    assert "protein_part != expected_part" in run_source[validation:tail_route]
    assert "self.set_running_state(False)" in run_source[validation:tail_route]


def test_formal_mapping_covers_supported_and_fail_fast_combinations():
    from core.analysis_v2.batch_input_adapter import FORMAL_PROTEIN_PARTS

    expected = {
        "protein1": "head",
        "protein2": "head",
        "protein3": "tail",
        "protein4": "head",
        "protein5": "head",
    }
    assert {key: value[1] for key, value in FORMAL_PROTEIN_PARTS.items()} == expected

    rejected = [
        ("protein1", "tail"),
        ("protein2", "tail"),
        ("protein3", "head"),
        ("protein4", "tail"),
        ("protein5", "tail"),
        ("protein1", "body"),
        ("unknown", "head"),
    ]
    for protein_key, protein_part in rejected:
        formal = FORMAL_PROTEIN_PARTS.get(protein_key)
        expected_part = formal[1] if formal else ""
        assert not expected_part or protein_part != expected_part


def test_run_analysis_has_no_legacy_execution_entry():
    source = _source()
    run_source = source[
        source.index("def run_analysis"):
        source.index("def imported_images_match_current_protein")
    ]

    assert "SingleProteinAnalysisWorker(" not in run_source
    assert "ProteinAnalysisService" not in run_source
    assert "get_pipeline_by_protein" not in run_source
    assert "pipeline_head.cppipe" not in run_source
    assert "pipeline_tail.cppipe" not in run_source


def test_page_display_and_execution_use_same_tail_condition():
    source = _source()
    condition = 'protein_key == "protein3" and protein_part == "tail"'
    display_source = source[
        source.index("def on_protein_changed"):
        source.index("def get_analyzed_protein_name_set")
    ]
    run_source = source[source.index("def run_analysis"):]

    assert "if {}:".format(condition) in display_source
    assert "if {}:".format(condition) in run_source


def test_head_and_tail_workflows_keep_workers_and_database_parts():
    source = _source()
    service_source = COMPLETION_SERVICE.read_text(encoding="utf-8")
    head_callback = source[
        source.index("def _on_head_calibration_completed"):
        source.index("def _start_tail_path_worker")
    ]
    assert 'context.get("workflow") == "protein3_tail"' in head_callback
    assert "HeadMeasurementWorker(" in head_callback
    assert 'part == "head"' in service_source
    assert 'part == "tail"' in service_source
    assert "replace_protein_analysis_with_fields" in service_source
    assert source.count("publish_measured_completion(") == 2
