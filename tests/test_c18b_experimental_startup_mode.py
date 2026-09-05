import inspect
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
C18B_ROOT = PROJECT_ROOT / "tools" / "analysis_v2" / "c18b_score015"
sys.path.insert(0, str(C18B_ROOT))

import candidate_scoring
from main import parse_startup_args


class Edge:
    def __init__(self, a, b, a_start=False, b_start=True):
        self.a = a
        self.b = b
        self.a_start = a_start
        self.b_start = b_start


class C18BExperimentalStartupModeTests(unittest.TestCase):
    def test_startup_defaults_to_graph_preserving(self):
        mode, qt_args = parse_startup_args([])
        self.assertEqual(mode, "graph_preserving")
        self.assertEqual(qt_args, [])

    def test_ordered_is_an_explicit_rollback_mode(self):
        mode, qt_args = parse_startup_args(
            ["--c18b-candidate-path-mode", "ordered"]
        )
        self.assertEqual(mode, "ordered")
        self.assertEqual(qt_args, [])

    def test_legacy_experimental_flag_remains_compatible(self):
        mode, qt_args = parse_startup_args(
            ["--experimental-c18b-graph-preserving"]
        )
        self.assertEqual(mode, "graph_preserving")
        self.assertEqual(qt_args, [])

    def test_candidate_path_defaults_to_ordered(self):
        self.assertEqual(
            inspect.signature(candidate_scoring.candidate_path)
            .parameters["mode"].default,
            "ordered",
        )

    def test_graph_preserving_keeps_unbranched_reconstruction(self):
        paths = [
            [(0, 0), (1, 0)],
            [(2, 0), (3, 0)],
            [(2, 1), (2, 3)],
        ]
        links = [Edge(0, 1), Edge(1, 2, False, True)]
        ordered = candidate_scoring.candidate_path(
            [0, 1, 2], paths, links, mode="ordered"
        )
        experimental = candidate_scoring.candidate_path(
            [0, 1, 2], paths, links, mode="graph_preserving"
        )
        self.assertEqual(ordered, experimental)

    def test_flag_is_not_connected_to_batch(self):
        batch_source = (PROJECT_ROOT / "app" / "batch_analysis_dialog.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("candidate_path_mode", batch_source)
        self.assertNotIn("experimental-c18b-graph-preserving", batch_source)

    def test_production_chain_carries_explicit_mode(self):
        expected = {
            "main.py": "--c18b-candidate-path-mode",
            "app/main_window.py": "c18b_candidate_path_mode",
            "app/analysis_window.py": "candidate_path_mode=self.c18b_candidate_path_mode",
            "app/analysis_v2/tail_analysis_workers.py": '"--candidate-path-mode"',
            "tools/analysis_v2/c18b_score015_adapter.py": "candidate_path_mode=args.candidate_path_mode",
            "tools/analysis_v2/c18b_score015/run_pipeline.py": "candidate_path_mode=candidate_path_mode",
            "tools/analysis_v2/c18b_score015/candidate_validation.py": "candidate_path(",
        }
        for relative, marker in expected.items():
            source = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(marker, source, relative)

        main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("--experimental-c18b-graph-preserving", main_source)

        worker_source = (
            PROJECT_ROOT / "app" / "analysis_v2" / "tail_analysis_workers.py"
        ).read_text(encoding="utf-8")
        self.assertGreaterEqual(worker_source.count('"--candidate-path-mode"'), 2)
        self.assertNotIn("C18B EXPERIMENT:", worker_source)


if __name__ == "__main__":
    unittest.main()
