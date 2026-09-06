"""Phase 5-B Step 1: Batch readiness follows the formal Analysis V2 assets."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import batch_analysis_dialog as batch


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def readiness_harness(project_root):
    return SimpleNamespace(get_project_root=lambda: Path(project_root))


def create_assets(project_root, protein_key):
    assets = list(batch.ANALYSIS_V2_HEAD_ASSETS)
    if protein_key == "protein3":
        assets.extend(batch.ANALYSIS_V2_TAIL_ASSETS)
    for _label, relative_path in assets:
        path = Path(project_root) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


@pytest.mark.parametrize("protein_key", ["protein1", "protein2", "protein4", "protein5"])
def test_head_proteins_use_the_same_formal_v2_requirements(tmp_path, protein_key):
    create_assets(tmp_path, protein_key)

    result = batch.BatchAnalysisDialog.check_pipeline_for_protein(
        readiness_harness(tmp_path), protein_key,
    )

    assert result["ok"] is True
    assert "measure_head_from_labels.cppipe" in result["path"]
    assert "direct_cellpose_worker.py" in result["path"]
    assert "measure_tail_from_labels.cppipe" not in result["path"]


def test_protein3_requires_head_tail_and_c18b_assets(tmp_path):
    create_assets(tmp_path, "protein3")

    result = batch.BatchAnalysisDialog.check_pipeline_for_protein(
        readiness_harness(tmp_path), "protein3",
    )

    assert result["ok"] is True
    assert "measure_head_from_labels.cppipe" in result["path"]
    assert "measure_tail_from_labels.cppipe" in result["path"]
    assert ".venv-c18b\\python.exe" in result["path"]
    assert "frozen_parameters.json" in result["path"]
    assert "graph_constrained_instance_separation.py" in result["path"]


@pytest.mark.parametrize(
    "protein_key,missing_name",
    [
        ("protein1", "pipelines/analysis_v2/measure_head_from_labels.cppipe"),
        ("protein3", "pipelines/analysis_v2/measure_tail_from_labels.cppipe"),
        ("protein3", ".venv-c18b/python.exe"),
    ],
)
def test_missing_formal_asset_fails_with_protein_and_asset(
    tmp_path, protein_key, missing_name,
):
    create_assets(tmp_path, protein_key)
    (tmp_path / missing_name).unlink()

    result = batch.BatchAnalysisDialog.check_pipeline_for_protein(
        readiness_harness(tmp_path), protein_key,
    )

    assert result["ok"] is False
    assert protein_key in result["detail"]
    assert Path(missing_name).name in result["detail"]


@pytest.mark.parametrize(
    "protein_key", ["protein1", "protein2", "protein3", "protein4", "protein5"],
)
def test_missing_direct_cellpose_worker_fails_every_formal_route(tmp_path, protein_key):
    create_assets(tmp_path, protein_key)
    (tmp_path / "tools/analysis_v2/direct_cellpose_worker.py").unlink()

    result = batch.BatchAnalysisDialog.check_pipeline_for_protein(
        readiness_harness(tmp_path), protein_key,
    )

    assert result["ok"] is False
    assert "Direct Cellpose worker" in result["detail"]


def test_legacy_head_and_tail_pipelines_are_not_readiness_assets(tmp_path):
    create_assets(tmp_path, "protein3")
    assert not (tmp_path / "pipelines/pipeline_head.cppipe").exists()
    assert not (tmp_path / "pipelines/pipeline_tail.cppipe").exists()

    head = batch.BatchAnalysisDialog.check_pipeline_for_protein(
        readiness_harness(tmp_path), "protein1",
    )
    tail = batch.BatchAnalysisDialog.check_pipeline_for_protein(
        readiness_harness(tmp_path), "protein3",
    )

    assert head["ok"] is True
    assert tail["ok"] is True


def test_mvimageid_readiness_keeps_python_module_and_plugins_checks(tmp_path):
    source = tmp_path / "MvImageID"
    source.mkdir()
    python_exe = source / "python.exe"
    python_exe.touch()
    plugins = source / "plugins"
    plugins.mkdir()
    config = SimpleNamespace(
        get_source_project_dir=lambda: source,
        get_python_exe=lambda: python_exe,
        get_plugins_directory=lambda: plugins,
        get_module_name=lambda: "MvImageID",
    )
    harness = SimpleNamespace(config=config)

    assert batch.BatchAnalysisDialog.check_mvimageid_environment(harness)["ok"] is True

    python_exe.unlink()
    missing_python = batch.BatchAnalysisDialog.check_mvimageid_environment(harness)
    assert missing_python["ok"] is False
    assert "Python解释器" in missing_python["detail"]
    python_exe.touch()

    plugins.rmdir()
    missing_plugins = batch.BatchAnalysisDialog.check_mvimageid_environment(harness)
    assert missing_plugins["ok"] is False
    assert "插件目录" in missing_plugins["detail"]

    config.get_module_name = lambda: ""
    missing_module = batch.BatchAnalysisDialog.check_mvimageid_environment(harness)
    assert missing_module["ok"] is False
    assert "module_name" in missing_module["detail"]


def test_batch_dialog_does_not_call_legacy_pipeline_lookup():
    class Config:
        def get_protein_keys(self):
            return ["protein1", "protein2", "protein3", "protein4", "protein5"]

        def get_protein_display_name(self, key):
            return batch.PROTEIN_DISPLAY_FALLBACK[key]

        def get_pipeline_by_protein(self, _key):
            raise AssertionError("Batch must not call get_pipeline_by_protein")

    harness = SimpleNamespace(config=Config())

    items = batch.BatchAnalysisDialog.get_protein_items(harness)

    assert [item["key"] for item in items] == [
        "protein1", "protein2", "protein3", "protein4", "protein5",
    ]


def test_legacy_configuration_and_pipeline_files_remain_untouched():
    config_text = (PROJECT_ROOT / "config.ini").read_text(encoding="utf-8-sig")
    source = (PROJECT_ROOT / "app/batch_analysis_dialog.py").read_text(encoding="utf-8")

    assert "[ProteinPipelines]" in config_text
    assert (PROJECT_ROOT / "pipelines/pipeline_head.cppipe").is_file()
    assert (PROJECT_ROOT / "pipelines/pipeline_tail.cppipe").is_file()
    readiness_source = source[
        source.index("def check_pipeline_for_protein"):
        source.index("def check_mvimageid_environment")
    ]
    assert "get_pipeline_by_protein" not in readiness_source
    assert "pipeline_head.cppipe" not in readiness_source
    assert "pipeline_tail.cppipe" not in readiness_source


def test_alias_and_manual_folder_matching_contract_remains():
    source = (PROJECT_ROOT / "app/batch_analysis_dialog.py").read_text(encoding="utf-8")
    assert 'SECTION_NAME = "BatchFolderAliases"' in source
    assert "def match_folder_to_keys" in source
    assert "def on_folder_combo_changed" in source
    assert "def save_current_mapping" in source
