"""Run one formal Tail Tier1 field and compare it with the frozen Gold run."""

import argparse
import json
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from core.analysis_v2.task_runner import AnalysisV2TaskRequest, AnalysisV2TaskRunner
from core.config_manager import ConfigManager
from core.analysis_v2 import tail_measurement_service
from core.analysis_v2.lightweight_tail_measurement import LightweightGenerationError
from core.database import Database
from core.analysis_v2.result_completion_service import publish_measured_completion
from tools.analysis_v2 import tail_v3_gold


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("field", choices=("023", "020", "016", "022"))
    parser.add_argument("--run-root")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--backend-mvimageid", action="store_true")
    parser.add_argument("--inject-eligible", action="store_true")
    args = parser.parse_args()
    field_id, case_no, run_id = next(
        row for row in tail_v3_gold.TIER1_RUNS if row[0].startswith("ZBFY" + args.field)
    )
    root = Path(__file__).resolve().parents[2]
    old = root / "workspace" / "cases" / case_no / "analysis_v2" / "protein3" / "runs" / run_id
    source = old / "input"
    request = AnalysisV2TaskRequest(
        case_no=case_no, protein_key="protein3", protein_part="tail",
        matched_fields=[{
            "field_no": field_id,
            "G": str(source / (field_id + "_FITC.tif")),
            "R": str(source / (field_id + "_TRITC.tif")),
            "Merge": str(source / (field_id + "_Merge.tif")),
        }],
        raw_image_folder="M1E-1 formal Tier1",
        candidate_path_mode="graph_preserving",
    )
    publication_record = None
    if args.publish:
        if args.run_root:
            raise RuntimeError("Publication requires a live task runner")
        temp = Path(tempfile.mkdtemp(prefix="m1e1_publication_"))
        database = Database(str(temp / "publication.db"))
        connection = database.connect()
        try:
            case_id = connection.execute(
                "INSERT INTO cases (case_no) VALUES (?)", ("M1E1-" + args.field,)
            ).lastrowid
            connection.commit()
        finally:
            connection.close()
    if args.run_root:
        new = Path(args.run_root).resolve()
        candidates = list((new / "measurement" / "tail" / "attempts").glob("*/lightweight/Image.csv"))
        if len(candidates) != 1:
            raise RuntimeError("Expected exactly one winning lightweight candidate")
        candidate = candidates[0].parent
    else:
        if args.backend_mvimageid:
            ConfigManager.get_tail_measurement_backend = lambda self: "mvimageid"
        if args.inject_eligible:
            def fail_after_partial(records, output, *args):
                output.mkdir()
                (output / "partial.txt").write_text("injected", encoding="utf-8")
                raise LightweightGenerationError("injected array failure")

            tail_measurement_service.generate_tail_measurement = fail_after_partial
        runner = AnalysisV2TaskRunner(ConfigManager())
        wall_started = time.perf_counter()
        completion = runner.run(request)
        new = Path(completion["task_root"])
        candidate = Path(completion["source_dir"])
        if args.publish:
            completion["context"]["case_id"] = case_id
            completion["target_dir"] = str(temp / "formal")
            publication_started = time.perf_counter()
            published = publish_measured_completion(
                completion, database, supervisor=runner.supervisor,
            )
            publication_finished = time.perf_counter()
            assert published.output_dir.is_dir()
            assert len(database.get_protein_analysis_by_case(case_id)) == 1
            publication_record = {
                "publisher_db": "PASS", "field": field_id,
                "test_output": str(temp),
                "publisher_db_seconds": round(publication_finished - publication_started, 4),
                "full_wall_seconds": round(publication_finished - wall_started, 4),
                "sperm_count": published.summary["total"]["sperm_count"],
                "positive_count": published.summary["total"]["positive_count"],
            }
    original_paths = tail_v3_gold._run_paths

    def winning_paths(run_root, requested_field):
        paths = original_paths(run_root, requested_field)
        if Path(run_root).resolve() == new.resolve():
            paths["image_csv"] = candidate / "Image.csv"
            paths["g_objects_csv"] = candidate / "G_objects.csv"
        return paths

    tail_v3_gold._run_paths = winning_paths
    comparison = tail_v3_gold.compare_runs(old, new, field_id)
    old_candidate = old / "measurement" / "tail" / "candidate_output"
    csv_exact = all((candidate / name).read_bytes() == (old_candidate / name).read_bytes()
                    for name in ("Image.csv", "G_objects.csv"))
    overlays = ("G_G_objects", "R_R_objects", "G_G_colocalized")
    overlay_exact = all(np.array_equal(
        cv2.imread(str(candidate / (field_id + "_" + kind + "_OrigOverlay.png")),
                   cv2.IMREAD_UNCHANGED),
        cv2.imread(str(old_candidate / (field_id + "_" + kind + "_OrigOverlay.png")),
                   cv2.IMREAD_UNCHANGED),
    ) for kind in overlays)
    print(json.dumps({"field": field_id, "run_root": str(new),
                      "candidate_output_dir": str(candidate),
                      "comparison": comparison, "csv_byte_exact": csv_exact,
                      "overlay_pixel_exact": overlay_exact}, ensure_ascii=False), flush=True)
    if not comparison.get("equal") or not csv_exact or not overlay_exact:
        raise SystemExit(1)
    if publication_record is not None:
        print(json.dumps(publication_record, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
