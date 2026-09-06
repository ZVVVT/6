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


def test_protein3_head_and_all_other_head_proteins_use_head_workflow():
    run_source = _source()[_source().index("def run_analysis"):]
    head_route = run_source.index('if protein_part == "head":')
    legacy_worker = run_source.index("SingleProteinAnalysisWorker(", head_route)
    head_block = run_source[head_route:legacy_worker]

    assert "run_analysis_v2(" in head_block
    assert "self._start_head_analysis_v2," in head_block
    assert 'workflow="protein3_tail"' not in head_block
    assert "return" in head_block


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
