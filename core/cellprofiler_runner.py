import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal


def ps_quote(path_or_text) -> str:
    """
    PowerShell 单引号安全转义。
    """
    text = str(path_or_text)
    return "'" + text.replace("'", "''") + "'"


class SourceCellProfilerRunner:
    """
    源码环境方式运行 CellProfiler / MvImageID。

    等效于用户原来的 PowerShell 命令：

    Set-Location "F:\\MvImageID"
    . "F:\\MvImageID\\.venv\\Scripts\\Activate.ps1"
    python -u -m MvImageID -c -r -p "CPP.cppipe" -i "input" -o "output" --plugins-directory "plugins"
    """

    def __init__(
        self,
        powershell_exe: str,
        source_project_dir: str,
        venv_activate: str,
        module_name: str,
        plugins_directory: str,
        log_file: str,
    ):
        self.powershell_exe = powershell_exe or "powershell.exe"
        self.source_project_dir = Path(source_project_dir).resolve()
        self.venv_activate = Path(venv_activate).resolve()
        self.module_name = module_name or "MvImageID"
        self.plugins_directory = Path(plugins_directory).resolve() if plugins_directory else None
        self.log_file = Path(log_file).resolve() if log_file else None

    def create_ps1_file(self, pipeline_file: str, input_dir: str, output_dir: str) -> Path:
        pipeline_file = Path(pipeline_file).resolve()
        input_dir = Path(input_dir).resolve()
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        log_file = self.log_file
        if log_file is None:
            log_file = output_dir / "run.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)

        ps1_path = output_dir / "run_mvimageid.ps1"

        plugins_part = ""
        if self.plugins_directory:
            plugins_part = f' --plugins-directory "$PLUGINS_PATH"'

        ps1_content = f"""
$ErrorActionPreference = "Stop"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -Namespace Win -Name K -PassThru -MemberDefinition '[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern System.IntPtr GetStdHandle(int nStdHandle);[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetConsoleMode(System.IntPtr h, out int m);[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern bool SetConsoleMode(System.IntPtr h, int m);' | Out-Null
$h = [Win.K]::GetStdHandle(-10)
$m = 0
if ($h -ne [IntPtr]::Zero -and [Win.K]::GetConsoleMode($h, [ref]$m)) {{
    $m = ($m -bor 0x80 -bor 0x10) -band (-bnot 0x40)
    [void][Win.K]::SetConsoleMode($h, $m)
}}

Set-Location {ps_quote(self.source_project_dir)}

$VENV_PATH     = {ps_quote(self.venv_activate)}
$PIPELINE_PATH = {ps_quote(pipeline_file)}
$INPUT_PATH    = {ps_quote(input_dir)}
$OUTPUT_PATH   = {ps_quote(output_dir)}
$PLUGINS_PATH  = {ps_quote(self.plugins_directory if self.plugins_directory else "")}
$LOG_FILE      = {ps_quote(log_file)}
$MODULE_NAME   = {ps_quote(self.module_name)}

Write-Host "=========================================="
Write-Host "MvImageID / CellProfiler 源码环境后台分析"
Write-Host "=========================================="
Write-Host "Project Dir : $PWD"
Write-Host "Venv        : $VENV_PATH"
Write-Host "Module      : $MODULE_NAME"
Write-Host "Pipeline    : $PIPELINE_PATH"
Write-Host "Input       : $INPUT_PATH"
Write-Host "Output      : $OUTPUT_PATH"
Write-Host "Plugins     : $PLUGINS_PATH"
Write-Host "Log File    : $LOG_FILE"
Write-Host "=========================================="

. $VENV_PATH

python -u -m $MODULE_NAME -c -r -p "$PIPELINE_PATH" -i "$INPUT_PATH" -o "$OUTPUT_PATH"{plugins_part} 2>&1 | Tee-Object -FilePath $LOG_FILE -Append

exit $LASTEXITCODE
"""

        ps1_path.write_text(ps1_content.strip() + "\n", encoding="utf-8")
        return ps1_path

    def build_command(self, ps1_path: Path) -> list:
        return [
            self.powershell_exe,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ps1_path),
        ]


class CellProfilerWorker(QThread):
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

    def run(self):
        start_time = time.time()

        try:
            runner = SourceCellProfilerRunner(
                powershell_exe=self.powershell_exe,
                source_project_dir=self.source_project_dir,
                venv_activate=self.venv_activate,
                module_name=self.module_name,
                plugins_directory=self.plugins_directory,
                log_file=self.log_file,
            )

            ps1_path = runner.create_ps1_file(
                pipeline_file=self.pipeline_file,
                input_dir=self.input_dir,
                output_dir=self.output_dir,
            )

            cmd = runner.build_command(ps1_path)

            self.log_signal.emit("开始以源码环境方式运行 CellProfiler / MvImageID...")
            self.log_signal.emit(f"PowerShell 脚本：{ps1_path}")
            self.log_signal.emit("执行命令：")
            self.log_signal.emit(" ".join([f'"{x}"' if " " in x else x for x in cmd]))
            self.log_signal.emit("")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )

            logs = []

            if process.stdout:
                for line in process.stdout:
                    line = line.rstrip()
                    if line:
                        logs.append(line)
                        self.log_signal.emit(line)

            process.wait()

            elapsed = time.time() - start_time
            success = process.returncode == 0

            if success:
                self.log_signal.emit(f"分析完成，用时 {elapsed:.2f} 秒。")
            else:
                self.log_signal.emit(f"分析失败，返回码：{process.returncode}")
                self.log_signal.emit("可以复制上面的 PowerShell 脚本路径，手动运行排查。")

            self.finished_signal.emit(success, elapsed, "\n".join(logs))

        except Exception as e:
            elapsed = time.time() - start_time
            message = f"运行源码环境时发生异常：{e}"
            self.log_signal.emit(message)
            self.finished_signal.emit(False, elapsed, message)