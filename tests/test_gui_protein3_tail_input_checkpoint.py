"""Regression coverage for the GUI protein3-tail checkpoint boundary."""

from pathlib import Path

from PIL import Image

from app.analysis_v2 import head_analysis_workers as workers
from core.analysis_v2.tail_core_checkpoint import _input_manifest


class _Config:
    def get_source_project_dir(self):
        return Path(".")

    def get_python_exe(self):
        return Path("python")


def test_gui_protein3_tail_commits_input_before_head_segmentation(
        tmp_path, monkeypatch):
    """The concrete TailCore reader must work before the head worker starts."""
    channels = {}
    for role in ("FITC", "TRITC", "Merge"):
        path = tmp_path / "ZBFY023-C-1_{}.tif".format(role)
        Image.new("L", (4, 3), color=1).save(str(path))
        channels[role] = path

    observed = []

    def fake_segmentation(**kwargs):
        task_root = kwargs["paths"].task_root
        manifest = _input_manifest(task_root, "ZBFY023-C-1")
        observed.append(manifest)
        return {"fields": []}

    monkeypatch.setattr(workers, "run_head_segmentation", fake_segmentation)
    worker = workers.HeadSegmentationWorker(
        project_root=tmp_path,
        case_data={"case_no": "case"},
        protein_key="protein3",
        paired_fields=[{
            "field_id": "ZBFY023-C-1",
            "fitc_path": str(channels["FITC"]),
            "tritc_path": str(channels["TRITC"]),
            "merge_path": str(channels["Merge"]),
        }],
        config=_Config(),
        write_input_manifest_checkpoint=True,
    )

    worker.run()

    assert len(observed) == 1
    assert observed[0]["field_id"] == "ZBFY023-C-1"
    assert observed[0]["protein_key"] == "protein3"
