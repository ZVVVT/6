"""
兼容旧导入路径的 MvImageID 后台执行入口。

历史界面代码仍从 core.cellprofiler_runner 导入 CellProfilerWorker。
为了降低第一步改动风险，暂时保留这个文件名和类名，内部统一转到
core.mvimageid_runner.MvImageIDRunner / MvImageIDWorker。
"""

from pathlib import Path
from typing import List

from PySide6.QtCore import QThread, Signal

from core.mvimageid_runner import MvImageIDRunner


def ps_quote(path_or_text) -> str:
    """保留旧函数名，避免外部调试代码导入时报错。"""
    text = str(path_or_text)
    return "'" + text.replace("'", "''") + "'"


class SourceCellProfilerRunner:
    """
    兼容旧类名。

    不再生成 PowerShell 脚本，实际使用 MvImageIDRunner 直接调用
    MvImageID 虚拟环境 python。后续第二/三步会把界面层导入名也逐步改成
    MvImageIDWorker。
    """

    def __init__(
        self,
        powershell_exe: str = "",
        source_project_dir: str = "",
        venv_activate: str = "",
        module_name: str = "MvImageID",
        plugins_directory: str = "",
        log_file: str = "",
    ):
        self.powershell_exe = powershell_exe or "powershell.exe"
        self.source_project_dir = Path(source_project_dir).resolve()
        self.venv_activate = Path(venv_activate).resolve()
        self.module_name = module_name or "MvImageID"
        self.plugins_directory = Path(plugins_directory).resolve() if plugins_directory else None
        self.log_file = Path(log_file).resolve() if log_file else None
        self._last_pipeline_file = None
        self._last_input_dir = None
        self._last_output_dir = None

    def create_ps1_file(self, pipeline_file: str, input_dir: str, output_dir: str) -> Path:
        """
        保留旧接口。

        新执行方式不再需要 ps1，这里只写一个说明文件，便于旧调试入口不报错。
        """
        self._last_pipeline_file = str(Path(pipeline_file).resolve())
        self._last_input_dir = str(Path(input_dir).resolve())
        self._last_output_dir = str(Path(output_dir).resolve())
        output_path = Path(output_dir).resolve()
        output_path.mkdir(parents=True, exist_ok=True)
        marker = output_path / "run_mvimageid_legacy_note.txt"
        marker.write_text(
            "当前版本已改为直接调用 MvImageID 虚拟环境 python，未生成 PowerShell 脚本。\n",
            encoding="utf-8",
        )
        return marker

    def build_command(self, ps1_path: Path) -> List[str]:
        """保留旧接口，仅返回说明文件路径。"""
        return [str(ps1_path)]

    def run(self, pipeline_file: str, input_dir: str, output_dir: str, log_callback=None):
        runner = MvImageIDRunner(
            source_project_dir=str(self.source_project_dir),
            venv_activate=str(self.venv_activate),
            module_name=self.module_name,
            plugins_directory=str(self.plugins_directory or ""),
            log_file=str(self.log_file or ""),
        )
        # 统一标准：日志固定写入当前蛋白输出目录 run_mvimageid.log，
        # 不再用旧配置 log_file 覆盖，避免日志被写到 F:\MvImageID\run.log。
        return runner.run(
            pipeline_file=pipeline_file,
            input_dir=input_dir,
            output_dir=output_dir,
            log_callback=log_callback,
            log_file="",
        )


class CellProfilerWorker(QThread):
    """
    兼容旧类名的后台线程。

    analysis_window.py 当前仍引用 CellProfilerWorker；第一步只替换执行层，
    不动界面文件，避免大范围改动。
    """

    log_signal = Signal(str)
    finished_signal = Signal(bool, float, str)

    def __init__(
        self,
        powershell_exe: str,
        source_project_dir: str,
        venv_activate: str,
        module_name: str,
        pipeline_file: str,
        input_dir: str,
        output_dir: str,
        plugins_directory: str = "",
        log_file: str = "",
    ):
        super().__init__()
        self.powershell_exe = powershell_exe
        self.source_project_dir = source_project_dir
        self.venv_activate = venv_activate
        self.module_name = module_name
        self.pipeline_file = pipeline_file
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.plugins_directory = plugins_directory
        self.log_file = log_file
        self._cancel_requested = False

    def request_cancel(self):
        self._cancel_requested = True

    def run(self):
        runner = MvImageIDRunner(
            source_project_dir=self.source_project_dir,
            venv_activate=self.venv_activate,
            module_name=self.module_name,
            plugins_directory=self.plugins_directory,
            log_file=self.log_file,
        )
        self.log_signal.emit("开始运行 MvImageID 分析...")
        # 统一标准：日志固定写入当前蛋白输出目录 run_mvimageid.log，
        # 不再用旧配置 log_file 覆盖，避免日志被写到 F:\MvImageID\run.log。
        result = runner.run(
            pipeline_file=self.pipeline_file,
            input_dir=self.input_dir,
            output_dir=self.output_dir,
            log_callback=self.log_signal.emit,
            cancel_callback=lambda: self._cancel_requested,
            log_file="",
        )
        message = result.output_text if result.success else (result.error_message or result.output_text)
        self.finished_signal.emit(result.success, result.elapsed_seconds, message)
