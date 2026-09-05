import os
import subprocess
import sys
import unittest
from pathlib import Path


class TailEditorStandaloneImportTests(unittest.TestCase):
    def test_adapter_import_from_non_project_directory(self):
        project_root = Path(__file__).resolve().parents[1]
        editor_path = (
            project_root / "tools" / "analysis_v2" / "tail_legacy"
            / "tail_result_editor_v2_3_draft_mvp.py"
        )
        code = """
import runpy
import sys
from pathlib import Path

editor_path = Path(sys.argv[1])
project_root = editor_path.parents[3]
assert Path.cwd() != project_root
assert str(project_root) not in sys.path
assert 'tools' not in sys.modules
runpy.run_path(str(editor_path), run_name='standalone_import_check')
from tools.analysis_v2.c18b_tail_editor_adapter import ordered_centerline
assert callable(ordered_centerline)
assert Path(sys.modules[ordered_centerline.__module__].__file__).resolve() == (
    project_root / 'tools' / 'analysis_v2' / 'c18b_tail_editor_adapter.py'
)
print('adapter import OK')
"""
        environment = os.environ.copy()
        environment["MPLBACKEND"] = "Agg"
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, str(editor_path)],
            cwd=str(project_root / "tests"),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("adapter import OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
