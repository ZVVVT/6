from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_WINDOW = PROJECT_ROOT / "app" / "analysis_window.py"
COMPLETION_SERVICE = PROJECT_ROOT / "core" / "analysis_v2" / "result_completion_service.py"


class TailPublicationWiringTests(unittest.TestCase):
    def test_tail_callback_stages_files_before_atomic_database_replace(self):
        block = COMPLETION_SERVICE.read_text(encoding="utf-8")

        stage_index = block.index("stage_tail_measurement_output(")
        database_index = block.index("analysis_id = replace(")
        commit_index = block.index("publication.commit()")

        self.assertLess(stage_index, database_index)
        self.assertLess(database_index, commit_index)
        self.assertIn("publication.rollback()", block)
        self.assertIn('calculation_mode") != "head_equivalent"', block)
        self.assertIn("source_dir", block)
        self.assertIn("target_dir", block)

    def test_tail_database_save_is_explicitly_tail_and_atomic(self):
        block = COMPLETION_SERVICE.read_text(encoding="utf-8")

        self.assertIn(
            '"replace_protein_analysis_with_fields"',
            block,
        )
        self.assertIn("protein_part=part", block)
        self.assertIn('"head_equivalent"', block)
        self.assertNotIn("save_protein_analysis(", block)
        self.assertNotIn("save_field_result(", block)

    def test_analysis_window_has_no_legacy_tail_route(self):
        source = ANALYSIS_WINDOW.read_text(encoding="utf-8")
        run_start = source.index("def run_analysis")
        run_end = source.index("def imported_images_match_current_protein", run_start)
        run_source = source[run_start:run_end]

        self.assertIn("FORMAL_PROTEIN_PARTS.get(protein_key)", run_source)
        self.assertNotIn("SingleProteinAnalysisWorker(", run_source)


if __name__ == "__main__":
    unittest.main()
