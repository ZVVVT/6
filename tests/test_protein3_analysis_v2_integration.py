from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

import numpy as np

from core.analysis_v2.label_image_io import atomic_save_label_image, read_label_image
from core.analysis_v2.manifest_store import ManifestStore
from core.analysis_v2.tail_calibration_service import (
    complete_tail_calibration,
    publish_tail_final_labels,
)
from core.analysis_v2.task_paths import AnalysisTaskPaths
from core.analysis_v2.task_state import TaskStateStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_WINDOW = PROJECT_ROOT / "app" / "analysis_window.py"
TAIL_WORKER = PROJECT_ROOT / "app" / "analysis_v2" / "tail_analysis_workers.py"
C18B_EXECUTION = PROJECT_ROOT / "core" / "analysis_v2" / "c18b_execution.py"
TAIL_EDITOR = (
    PROJECT_ROOT
    / "tools"
    / "analysis_v2"
    / "tail_legacy"
    / "tail_result_editor_v2_3_draft_mvp.py"
)
TAIL_CONTROLLER = (
    PROJECT_ROOT / "app" / "analysis_v2" / "c18b_tail_calibration_window.py"
)


class Protein3AnalysisV2IntegrationTests(unittest.TestCase):
    def test_protein3_tail_routes_without_legacy_single_worker(self):
        source = ANALYSIS_WINDOW.read_text(encoding="utf-8")
        run_source = source[source.index("def run_analysis"):]
        route_condition = (
            'if protein_key == "protein3" and protein_part == "tail":'
        )
        protein3_route = run_source.index(route_condition)
        route_end = run_source.index("def imported_images_match_current_protein")
        route_block = run_source[protein3_route:route_end]
        self.assertIn('workflow="protein3_tail"', route_block)
        self.assertIn("return", route_block)
        self.assertNotIn("SingleProteinAnalysisWorker(", run_source[:route_end])
        self.assertNotIn('if protein_key == "protein3":', run_source)

    def test_head_route_and_batch_file_remain_separate(self):
        source = ANALYSIS_WINDOW.read_text(encoding="utf-8")
        run_source = source[source.index("def run_analysis"):]
        self.assertIn('if protein_part == "head":', run_source)
        self.assertIn("run_analysis_v2(", run_source)
        self.assertIn("self._start_head_analysis_v2,", run_source)
        self.assertNotIn("batch_analysis", run_source)

    def test_head_calibration_routes_protein3_to_tail_worker(self):
        source = ANALYSIS_WINDOW.read_text(encoding="utf-8")
        callback = source[
            source.index("def _on_head_calibration_completed"):
            source.index("def _start_tail_path_worker")
        ]
        deferred_start = source[
            source.index("def _maybe_start_tail_path_after_field_prepare"):
            source.index("def _on_head_calibration_completed")
        ]
        worker_start = source[
            source.index("def _start_tail_path_worker"):
            source.index("def _on_tail_path_finished")
        ]
        self.assertIn('context.get("workflow") == "protein3_tail"', callback)
        self.assertIn("self._tail_head_calibration_finished = True", callback)
        self.assertIn("self._tail_path_start_pending = True", callback)
        self.assertIn("self._start_next_tail_field_prepare()", callback)
        self.assertIn("self._maybe_start_tail_path_after_field_prepare()", callback)
        self.assertLess(
            callback.index('context.get("workflow") == "protein3_tail"'),
            callback.index("HeadMeasurementWorker("),
        )
        self.assertIn("no_prepare_work_left", deferred_start)
        self.assertIn('not getattr(self, "tail_field_prepare_workers", {})', deferred_start)
        self.assertIn("self._start_tail_path_worker(project_root, task_root)", deferred_start)
        self.assertIn("worker = TailPathWorker(", worker_start)
        self.assertIn("project_root=project_root", worker_start)
        self.assertIn("task_root=task_root", worker_start)
        self.assertIn("worker.start()", worker_start)

    def test_c18b_field_prepare_is_bounded_and_has_serial_fallback(self):
        source = ANALYSIS_WINDOW.read_text(encoding="utf-8")
        scheduler = source[
            source.index("def _start_next_tail_field_prepare"):
            source.index("def _on_tail_field_prepare_finished")
        ]
        self.assertIn("min(\n                2,", scheduler)
        self.assertIn("len(workers) < max_workers", scheduler)
        self.assertIn("self.tail_field_prepare_max_workers = 1", scheduler)
        self.assertIn("self.tail_field_prepare_queue.insert(0, field_id)", scheduler)

    def test_tail_worker_uses_only_c18b_backend_and_editor_adapter(self):
        source = TAIL_WORKER.read_text(encoding="utf-8") + C18B_EXECUTION.read_text(encoding="utf-8")
        self.assertIn('"c18b_score015_adapter.py"', source)
        self.assertIn('"c18b_tail_editor_adapter.py"', source)
        self.assertIn('"tail_result_editor_v2_3_draft_mvp.py"', source)
        self.assertIn('"workflow": "c18b_tail_editor"', source)
        self.assertIn('"tail_backend": "C18B"', source)
        self.assertIn("仅支持 protein3 C18B 尾部流程", source)
        self.assertIn("str(self.python_executable)", source)
        self.assertNotIn("import cv2", source)
        self.assertNotIn("TailPathService", source)

    def test_production_worker_passes_unassigned_candidates_to_editor(self):
        worker_source = TAIL_WORKER.read_text(encoding="utf-8") + C18B_EXECUTION.read_text(encoding="utf-8")
        controller_source = TAIL_CONTROLLER.read_text(encoding="utf-8")
        self.assertIn(
            '"unassigned_candidates": str(',
            worker_source,
        )
        self.assertIn(
            '(output_dir / "unassigned_tail_candidates.json").resolve()',
            worker_source,
        )
        self.assertIn(
            '"--unassigned-candidates", self.payload["unassigned_candidates"]',
            controller_source,
        )

    def test_calibration_and_measurement_workers_do_not_publish_or_write_database(self):
        formal_source = "\n".join((
            TAIL_WORKER.read_text(encoding="utf-8"),
            TAIL_CONTROLLER.read_text(encoding="utf-8"),
        ))
        self.assertNotIn("tail_result_publisher", formal_source)
        self.assertNotIn("stage_tail_measurement_output", formal_source)
        self.assertNotIn("save_protein_result", formal_source)

    def test_tail_result_rejects_non_c18b_workflow_without_legacy_fallback(self):
        source = ANALYSIS_WINDOW.read_text(encoding="utf-8")
        callback = source[
            source.index("def _on_tail_path_finished"):
            source.index("def _on_tail_path_thread_finished")
        ]
        self.assertIn('payload.get("workflow") != "c18b_tail_editor"', callback)
        self.assertIn('payload.get("tail_backend") != "C18B"', callback)
        self.assertIn("不支持的旧尾部 workflow", callback)
        self.assertIn("C18BTailCalibrationController(", callback)
        self.assertNotIn("from app.analysis_v2.tail_calibration_window", source)

    def test_legacy_tail_stages_are_not_in_formal_call_chain(self):
        formal_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                ANALYSIS_WINDOW,
                TAIL_WORKER,
                TAIL_EDITOR,
            )
        )
        for retired_script in (
            "tail_path_worker.py",
            "tail_graph_stage2_1_head_entry_match_v1_1_baseline.py",
            "tail_graph_stage2_2_beam_path_v1_2_fullfix.py",
            "tail_graph_stage2_3_global_unique_v1_1.py",
        ):
            self.assertNotIn(retired_script, formal_sources)

    def test_formal_code_has_no_test_path_dependencies(self):
        paths = [
            ANALYSIS_WINDOW,
            TAIL_WORKER,
            PROJECT_ROOT / "app" / "analysis_v2" / "c18b_tail_calibration_window.py",
            PROJECT_ROOT / "core" / "analysis_v2" / "tail_calibration_service.py",
            TAIL_EDITOR,
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths).lower()
        self.assertNotIn("experiments", text)
        self.assertNotIn("analysis_v2_tail_runs", text)
        self.assertNotIn("f:\\v1", text)

    def test_editor_state_alone_cannot_publish(self):
        with tempfile.TemporaryDirectory(dir=str(PROJECT_ROOT / "workspace")) as temp:
            task_root, payload = self._make_task(Path(temp))
            output_dir = Path(payload["output_dir"])
            (output_dir / "editor_state_v2_2.json").write_text(
                "{}", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                FileNotFoundError, "请在尾部编辑器中点击保存结果"
            ):
                publish_tail_final_labels(payload)
            self.assertFalse((output_dir / "001_TailFinalLabels.tif").exists())
            self.assertEqual(
                TaskStateStore.from_task_paths(
                    AnalysisTaskPaths._build(PROJECT_ROOT, task_root, "test_run")
                ).load()["status"],
                "tail_calibration_required",
            )

    def test_saved_editor_output_publishes_and_completes(self):
        with tempfile.TemporaryDirectory(dir=str(PROJECT_ROOT / "workspace")) as temp:
            task_root, payload = self._make_task(Path(temp))
            source = (
                Path(payload["output_dir"])
                / "edited_tail_regions_head_id_uint16.tif"
            )
            labels = np.zeros((8, 8), dtype=np.uint16)
            labels[1:3, 1:3] = 7
            atomic_save_label_image(source, labels)
            Path(payload["output_dir"], "edited_tail_region_conflicts.json").write_text(
                json.dumps({"conflicts": []}), encoding="utf-8"
            )
            head_labels = np.zeros((8, 8), dtype=np.uint16)
            head_labels[4:6, 4:6] = 7
            atomic_save_label_image(
                task_root / "calibration" / "head" / "001_HeadFinalLabels.tif",
                head_labels,
            )
            published = publish_tail_final_labels(payload)
            final_path = Path(published["tail_final_labels"])
            self.assertEqual(
                sorted(int(value) for value in np.unique(read_label_image(final_path)) if value),
                [1],
            )
            result = complete_tail_calibration(task_root, [published])
            self.assertEqual(result["state"]["status"], "tail_calibrated")
            roles = [item["role"] for item in result["manifest"]["files"]]
            self.assertIn("tail_final_labels", roles)

    def test_page_unlocks_on_tail_success_and_abort(self):
        source = ANALYSIS_WINDOW.read_text(encoding="utf-8")
        success = source[
            source.index("def _on_tail_calibration_completed"):
            source.index("def _on_tail_calibration_aborted")
        ]
        aborted = source[
            source.index("def _on_tail_calibration_aborted"):
            source.index("def _on_head_calibration_closed")
        ]
        self.assertIn("TailMeasurementWorker(", success)
        self.assertIn("_finish_analysis_v2_ui()", aborted)

    def test_legacy_tail_pipeline_is_retained(self):
        self.assertTrue((PROJECT_ROOT / "pipelines" / "pipeline_tail.cppipe").is_file())

    @staticmethod
    def _make_task(root: Path):
        task_root = root / "task"
        paths = AnalysisTaskPaths._build(PROJECT_ROOT, task_root, "test_run")
        paths.create_directories()
        TaskStateStore.from_task_paths(paths).initialize(
            case_no="case1", protein_key="protein3"
        )
        TaskStateStore.from_task_paths(paths).update(
            "tail_calibration_required",
            "tail_calibration",
            "waiting",
        )
        ManifestStore.from_task_paths(paths).initialize(
            case_no="case1", protein_key="protein3"
        )
        output_dir = paths.calibration_tail_dir / "001"
        output_dir.mkdir(parents=True, exist_ok=True)
        return task_root, {
            "task_root": str(task_root),
            "field_id": "001",
            "output_dir": str(output_dir),
        }


if __name__ == "__main__":
    unittest.main()
