import hashlib
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.settings_window import SettingsWindow


EXPECTED_TABS = [
    "软件信息",
    "运行环境",
    "质控微球测试",
    "工作目录",
    "图片规则",
    "蛋白配置",
    "结果校正",
    "关于",
]


def _digest(path):
    target = Path(path)
    return hashlib.sha256(target.read_bytes()).hexdigest()


def test_pipeline_parameters_page_is_built_but_not_registered():
    app = QApplication.instance() or QApplication([])
    parameter_file = Path("pipeline_params.ini").resolve()
    before = _digest(parameter_file)
    window = SettingsWindow()
    try:
        names = [window.tabs.tabText(index) for index in range(window.tabs.count())]
        assert names == EXPECTED_TABS
        assert "管道参数" not in names
        assert window.pipeline_params_page is not None
        assert window.pipeline_param_tabs.count() == 3

        for index, expected_name in enumerate(EXPECTED_TABS):
            window.tabs.setCurrentIndex(index)
            app.processEvents()
            assert window.tabs.currentIndex() == index
            assert window.tabs.tabText(index) == expected_name

        window.tabs.setCurrentIndex(3)
        assert window.tabs.tabText(window.tabs.currentIndex()) == "工作目录"
        window.tabs.setCurrentIndex(999)
        assert window.tabs.currentIndex() == 3

        assert window.btn_save.isEnabled()
        assert window.btn_reload.isEnabled()
        assert window.btn_test.isEnabled()
        assert callable(window.save_current_settings)
        assert callable(window.load_config)
        assert callable(window.check_paths)
        assert callable(window.run_qc_beads_test)
    finally:
        window.close()

    assert _digest(parameter_file) == before
