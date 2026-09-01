import os
import sys
from pathlib import Path

from core.config_manager import ConfigManager, get_application_root


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
