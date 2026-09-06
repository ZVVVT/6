"""Phase 4-B Step 2 coverage for the read-only Batch input adapter."""

import ast
from pathlib import Path

import pytest

from core.analysis_v2.batch_input_adapter import (
    AnalysisV2BatchInputError,
    FORMAL_PROTEIN_PARTS,
    build_batch_task_request,
)
from core.analysis_v2.task_runner import AnalysisV2TaskRequest


class StubConfig:
    def __init__(self, workspace_root, parts=None):
        self.app_root = Path(workspace_root).parent
        self._workspace_root = Path(workspace_root)
        self._parts = parts or {
            key: part for key, (_name, part) in FORMAL_PROTEIN_PARTS.items()
        }

    def get_protein_part(self, key):
        return self._parts.get(key, "")

    def get_image_rule(self):
        return {
            "g_suffix": "_G", "r_suffix": "_R",
            "dic_suffix": "_DIC", "merge_suffix": "_Merge",
            "image_ext": ".tif",
        }

    def get_workspace_root(self):
        return self._workspace_root


def write_field(folder, field_id="field1", channels=("G", "R", "Merge"), extension=".tif"):
    folder.mkdir(parents=True, exist_ok=True)
    for channel in channels:
        (folder / "{}_{}{}".format(field_id, channel, extension)).write_bytes(b"image")


def build(tmp_path, protein_key="protein1", folder_name="Q9BYW3", channels=("G", "R", "Merge"),
          config=None, case_data=None, **kwargs):
    folder = tmp_path / folder_name
    write_field(folder, channels=channels)
    config = config or StubConfig(tmp_path / "cases")
    request = build_batch_task_request(
        case_data or {"case_no": "CASE001", "id": 17},
        protein_key,
        folder,
        config,
        **kwargs
    )
    return request, folder


@pytest.mark.parametrize(
    "protein_key,accession,part",
    [
        ("protein1", "Q9BYW3", "head"),
        ("protein2", "P10323", "head"),
        ("protein3", "Q96P56", "tail"),
        ("protein4", "Q8IYV9", "head"),
        ("protein5", "W5XKT8", "head"),
    ],
)
def test_five_formal_protein_mappings(tmp_path, protein_key, accession, part):
    request, _folder = build(tmp_path, protein_key, accession)
    assert isinstance(request, AnalysisV2TaskRequest)
    assert request.protein_key == protein_key
    assert request.protein_part == part
    assert FORMAL_PROTEIN_PARTS[protein_key] == (accession, part)


def test_existing_batch_folder_alias_matching_feeds_adapter(tmp_path):
    from app.batch_analysis_dialog import BatchAnalysisDialog, FolderAliasStore

    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "[BatchFolderAliases]\nprotein1 = custom-q9-folder\n",
        encoding="utf-8",
    )
    batch_matching = type("BatchMatchingHarness", (), {})()
    batch_matching.alias_store = FolderAliasStore(config_path)
    batch_matching.get_protein_items = lambda: [
        {"key": "protein1", "name": "Q9BYW3"},
    ]
    batch_matching.normalize_text = FolderAliasStore.normalize_text
    alias_map = BatchAnalysisDialog.build_folder_alias_map(batch_matching)
    matched_keys = BatchAnalysisDialog.match_folder_to_keys(
        batch_matching, "custom-q9-folder", alias_map,
    )
    assert matched_keys == ["protein1"]

    request, folder = build(tmp_path, matched_keys[0], "custom-q9-folder")
    assert Path(request.raw_image_folder) == folder.resolve()


def test_one_field_builds_formal_g_r_merge_keys(tmp_path):
    request, folder = build(tmp_path, "protein3", "Q96P56")
    assert request.matched_fields == [{
        "field_id": "field1",
        "tritc_path": str((folder / "field1_R.tif").resolve()),
        "fitc_path": str((folder / "field1_G.tif").resolve()),
        "merge_path": str((folder / "field1_Merge.tif").resolve()),
    }]


def test_head_merge_is_optional(tmp_path):
    request, _folder = build(tmp_path, channels=("G", "R"))
    assert request.matched_fields[0]["merge_path"] == ""


def test_multiple_fields_use_stable_natural_order(tmp_path):
    folder = tmp_path / "Q9BYW3"
    write_field(folder, "field10", ("G", "R"))
    write_field(folder, "field2", ("G", "R"))
    request = build_batch_task_request(
        {"case_no": "CASE001", "id": 17}, "protein1", folder,
        StubConfig(tmp_path / "cases"),
    )
    assert [row["field_id"] for row in request.matched_fields] == ["field2", "field10"]


@pytest.mark.parametrize("present,missing", [("R", "G"), ("G", "R")])
def test_missing_global_channel_is_explicit(tmp_path, present, missing):
    folder = tmp_path / "Q9BYW3"
    write_field(folder, channels=(present,))
    with pytest.raises(AnalysisV2BatchInputError) as caught:
        build_batch_task_request(
            {"case_no": "CASE001"}, "protein1", folder,
            StubConfig(tmp_path / "cases"),
        )
    assert caught.value.channel == missing
    assert "protein1" in str(caught.value)
    assert str(folder.resolve()) in str(caught.value)


def test_protein3_requires_merge_for_every_field(tmp_path):
    folder = tmp_path / "Q96P56"
    write_field(folder, channels=("G", "R"))
    with pytest.raises(AnalysisV2BatchInputError) as caught:
        build_batch_task_request(
            {"case_no": "CASE001"}, "protein3", folder,
            StubConfig(tmp_path / "cases"),
        )
    assert caught.value.field == "field1"
    assert caught.value.channel == "Merge"


def test_g_r_field_mismatch_is_rejected(tmp_path):
    folder = tmp_path / "Q9BYW3"
    write_field(folder, "field1", ("G",))
    write_field(folder, "field2", ("R",))
    with pytest.raises(AnalysisV2BatchInputError, match="G/R 视野不匹配") as caught:
        build_batch_task_request(
            {"case_no": "CASE001"}, "protein1", folder,
            StubConfig(tmp_path / "cases"),
        )
    assert caught.value.field in ("field1", "field2")


def test_duplicate_field_channel_is_rejected(tmp_path):
    folder = tmp_path / "Q9BYW3"
    write_field(folder, channels=("G", "R"))
    (folder / "field1_G.png").write_bytes(b"duplicate")
    with pytest.raises(AnalysisV2BatchInputError, match="重复 field/通道") as caught:
        build_batch_task_request(
            {"case_no": "CASE001"}, "protein1", folder,
            StubConfig(tmp_path / "cases"),
        )
    assert caught.value.field == "field1"
    assert caught.value.channel == "G"


def test_invalid_protein_key_is_rejected(tmp_path):
    folder = tmp_path / "unknown"
    folder.mkdir()
    with pytest.raises(AnalysisV2BatchInputError, match="非法 protein_key"):
        build_batch_task_request(
            {"case_no": "CASE001"}, "Q9BYW3", folder,
            StubConfig(tmp_path / "cases"),
        )


def test_missing_protein_folder_is_rejected(tmp_path):
    folder = tmp_path / "missing"
    with pytest.raises(AnalysisV2BatchInputError, match="folder 不存在") as caught:
        build_batch_task_request(
            {"case_no": "CASE001"}, "protein1", folder,
            StubConfig(tmp_path / "cases"),
        )
    assert caught.value.folder == str(folder.resolve())


def test_configured_part_must_match_formal_mapping(tmp_path):
    folder = tmp_path / "Q96P56"
    write_field(folder)
    config = StubConfig(tmp_path / "cases", parts={"protein3": "head"})
    with pytest.raises(AnalysisV2BatchInputError, match="正式映射不一致"):
        build_batch_task_request(
            {"case_no": "CASE001"}, "protein3", folder, config,
        )


def test_case_identity_workspace_and_default_candidate_mode(tmp_path):
    request, _folder = build(
        tmp_path, case_data={"case_no": "CASE-2026", "id": 88},
    )
    assert request.case_no == "CASE-2026"
    assert request.case_id == 88
    assert request.workspace_root == (tmp_path / "cases").resolve()
    assert request.candidate_path_mode == "graph_preserving"


def test_explicit_workspace_root_is_used(tmp_path):
    request, _folder = build(tmp_path, workspace_root=tmp_path / "isolated-cases")
    assert request.workspace_root == (tmp_path / "isolated-cases").resolve()


def test_adapter_source_has_no_execution_publication_database_or_qt_dependencies():
    source_path = Path(__file__).parents[1] / "core" / "analysis_v2" / "batch_input_adapter.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not any(name.startswith(("PyQt", "PySide")) for name in imported)
    assert "AnalysisV2TaskRunner" not in source
    assert "publish_measured_completion" not in source
    assert "Database" not in source
    assert "run_one_protein" not in source
    assert "prepare_input_folder" not in source
    assert "pipeline_head.cppipe" not in source
    assert "pipeline_tail.cppipe" not in source
