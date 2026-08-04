import io
import os
import sys
from pathlib import Path

import pytest

from core.config_manager import ConfigManager, get_application_root
from tools.analysis_v2.tail_joint_oneclick_v2 import run_command


def test_config_relative_paths_use_application_root_outside_project(monkeypatch):
    original_cwd = Path.cwd()
    try:
        monkeypatch.chdir(Path("F:/"))
        config = ConfigManager()
        app_root = get_application_root()

        assert config.get_workspace_root() == app_root / "workspace" / "cases"
        assert config.get_database_path() == app_root / "data" / "analysis.db"
        assert config.get_report_dir() == app_root / "reports"
    finally:
        os.chdir(str(original_cwd))


def test_config_absolute_path_is_unchanged():
    config = ConfigManager()
    absolute_path = Path("F:/absolute/path/value")

    assert config.resolve_config_path(absolute_path) == absolute_path


def test_frozen_application_root_is_executable_directory(monkeypatch):
    executable = Path("F:/release/SpermProteinAnalyzer.exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))

    assert get_application_root() == executable.parent


def test_streaming_command_decodes_utf8_and_replaces_invalid_bytes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PYTHONIOENCODING", "gbk")
    log_handle = io.StringIO()
    code = (
        "import sys; "
        "sys.stdout.buffer.write('中文\\n'.encode('utf-8') + b'\\x9a\\n'); "
        "sys.stdout.buffer.flush()"
    )

    run_command([sys.executable, "-c", code], tmp_path, log_handle, "encoding")

    output = log_handle.getvalue()
    assert "中文" in output
    assert "\ufffd" in output


def test_streaming_command_reports_nonzero_return_code(tmp_path):
    with pytest.raises(RuntimeError, match="return_code=7"):
        run_command(
            [sys.executable, "-c", "raise SystemExit(7)"],
            tmp_path,
            io.StringIO(),
            "nonzero",
        )
