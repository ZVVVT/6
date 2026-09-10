"""P2B1 shared task-input preparation contract tests."""

import json
from pathlib import Path

from PIL import Image

from core.analysis_v2.c18b_execution import C18BExecution, _field_fitc_path
from core.analysis_v2.input_manifest_checkpoint import write_and_verify_input_manifest_checkpoint
from core.analysis_v2.segmentation_service import prepare_common_task_input
from core.analysis_v2.task_paths import AnalysisTaskPaths


def _source_fields(tmp_path):
    source = tmp_path / "source.tif"
    Image.new("L", (4, 3), color=7).save(str(source), format="TIFF")
    return [{
        "field_id": "001",
        "tritc_path": str(source),
        "fitc_path": str(source),
        "merge_path": str(source),
    }]


def test_common_preparation_materializes_single_contract_for_head_and_c18b(tmp_path):
    paths = AnalysisTaskPaths.for_smoke(tmp_path, run_id="prepared")
    fields = _source_fields(tmp_path)

    prepared = prepare_common_task_input(
        paths, fields, case_no="case", protein_key="protein3",
    )

    assert [path.name for path in sorted(paths.input_dir.iterdir())] == [
        "001_FITC.tif", "001_Merge.tif", "001_TRITC.tif",
    ]
    worker_payload = json.loads(prepared["worker_input_path"].read_text(encoding="utf-8"))
    assert worker_payload == prepared["worker_input"]
    assert worker_payload["schema_version"] == "analysis_v2_direct_cellpose_input_v1"
    assert worker_payload["fields"] == [{
        "field_id": "001",
        "tritc_path": str((paths.input_dir / "001_TRITC.tif").resolve()),
        "fitc_path": str((paths.input_dir / "001_FITC.tif").resolve()),
        "merge_path": str((paths.input_dir / "001_Merge.tif").resolve()),
        "labels_output_path": str((paths.segmentation_head_dir / "001_HeadInitialLabels.tif").resolve()),
        "overlay_output_path": str((paths.segmentation_head_dir / "001_HeadInitialOverlay.png").resolve()),
        "objects_output_path": str((paths.segmentation_head_dir / "001_HeadInitialObjects.json").resolve()),
    }]
    assert {item["role"] for item in json.loads(paths.manifest_path.read_text(encoding="utf-8"))["files"]} == {
        "tritc_input", "fitc_input", "merge_input",
    }

    execution = C18BExecution.__new__(C18BExecution)
    execution.task_root = paths.task_root
    assert execution._discover_fields() == ["001"]
    assert _field_fitc_path(paths.task_root, "001") == (paths.input_dir / "001_FITC.tif").resolve()

    checkpoint = write_and_verify_input_manifest_checkpoint(
        paths.task_root, fields[0], "protein3",
    )
    assert checkpoint["checkpoint_manifest"]["stage"] == "input_manifest"
    reference = write_and_verify_input_manifest_checkpoint(
        tmp_path / "checkpoint_reference", fields[0], "protein3",
    )
    assert checkpoint["input_fingerprint"] == reference["input_fingerprint"]


def test_common_preparation_is_not_repeated_when_head_consumes_payload(tmp_path, monkeypatch):
    from core.analysis_v2 import segmentation_service

    paths = AnalysisTaskPaths.for_smoke(tmp_path, run_id="head_consumes_prepared")
    fields = _source_fields(tmp_path)
    prepared = prepare_common_task_input(paths, fields, case_no="case", protein_key="protein1")
    worker_input_bytes = prepared["worker_input_path"].read_bytes()
    original_copy = segmentation_service._copy_fields
    calls = []

    def counted_copy(*args, **kwargs):
        calls.append(True)
        return original_copy(*args, **kwargs)

    class Runner:
        def __init__(self, **kwargs):
            pass

        def run(self, input_json_path, logs_dir, worker_result_path, **kwargs):
            assert Path(input_json_path) == prepared["worker_input_path"]
            raise RuntimeError("stop after direct Cellpose wiring assertion")

    monkeypatch.setattr(segmentation_service, "_copy_fields", counted_copy)
    monkeypatch.setattr(segmentation_service, "DirectCellposeRunner", Runner)
    try:
        segmentation_service.run_head_segmentation(
            paths, fields, tmp_path, Path(__file__), Path(__file__),
            case_no="case", protein_key="protein1", prepared_input=prepared,
        )
    except RuntimeError as error:
        assert str(error) == "stop after direct Cellpose wiring assertion"
    else:
        raise AssertionError("Direct Cellpose runner should have been called")
    assert calls == []
    assert prepared["worker_input_path"].read_bytes() == worker_input_bytes


def test_direct_head_call_keeps_legacy_prepare_compatibility(tmp_path, monkeypatch):
    from core.analysis_v2 import segmentation_service

    paths = AnalysisTaskPaths.for_smoke(tmp_path, run_id="legacy_head_prepare")
    fields = _source_fields(tmp_path)
    original_copy = segmentation_service._copy_fields
    calls = []

    def counted_copy(*args, **kwargs):
        calls.append(True)
        return original_copy(*args, **kwargs)

    class Runner:
        def __init__(self, **kwargs):
            pass

        def run(self, input_json_path, logs_dir, worker_result_path, **kwargs):
            assert Path(input_json_path) == paths.task_root / "worker_input.json"
            raise RuntimeError("legacy direct Cellpose wiring reached")

    monkeypatch.setattr(segmentation_service, "_copy_fields", counted_copy)
    monkeypatch.setattr(segmentation_service, "DirectCellposeRunner", Runner)
    try:
        segmentation_service.run_head_segmentation(
            paths, fields, tmp_path, Path(__file__), Path(__file__),
            case_no="case", protein_key="protein1",
        )
    except RuntimeError as error:
        assert str(error) == "legacy direct Cellpose wiring reached"
    else:
        raise AssertionError("Direct Cellpose runner should have been called")
    assert calls == [True]
    assert (paths.task_root / "worker_input.json").is_file()
