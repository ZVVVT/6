import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import QThread, Signal


LogCallback = Optional[Callable[[str], None]]
CancelCallback = Optional[Callable[[], bool]]


@dataclass
class MvImageIDRunResult:
    """MvImageID 单次运行结果。"""

    success: bool
    elapsed_seconds: float
    return_code: int
    command: List[str] = field(default_factory=list)
    command_file: Optional[Path] = None
    log_file: Optional[Path] = None
    output_text: str = ""
    error_message: str = ""


class MvImageIDRunner:
    """
    统一 MvImageID 执行器。

    设计目标：
    - 直接调用 MvImageID 虚拟环境中的 python.exe；
    - 单蛋白分析、批量分析后续都应复用此执行器；
    - 每次运行都在输出目录生成命令文件和日志文件，方便排查。
    """

    def __init__(
        self,
        source_project_dir: str,
        python_exe: str = "",
        module_name: str = "MvImageID",
        plugins_directory: str = "",
        log_file: str = "",
        **legacy_kwargs,
    ):
        self.source_project_dir = Path(str(source_project_dir or "")).expanduser().resolve()
        self.python_exe = (
            Path(str(python_exe or "")).expanduser().resolve()
            if str(python_exe or "").strip()
            else None
        )
        self.module_name = str(module_name or "MvImageID").strip() or "MvImageID"
        self.plugins_directory = (
            Path(str(plugins_directory or "")).expanduser().resolve()
            if str(plugins_directory or "").strip()
            else None
        )
        # log_file 仅作历史兼容；标准日志始终写入当前输出目录。
        self.config_log_file = (
            Path(str(log_file or "")).expanduser().resolve()
            if str(log_file or "").strip()
            else None
        )

    # ------------------------------------------------------------------
    # 路径检查与命令构建
    # ------------------------------------------------------------------
    def get_python_executable(self) -> Path:
        """定位 MvImageID Python解释器。"""
        candidates: List[Path] = []

        if self.python_exe is not None:
            candidates.append(self.python_exe)

        candidates.extend([
            self.source_project_dir / ".venv" / "Scripts" / "python.exe",
            self.source_project_dir / ".venv" / "Scripts" / "python",
        ])

        seen = set()
        for candidate in candidates:
            key = str(candidate).lower()
            if key in seen:
                continue
            seen.add(key)
            if candidate.exists() and candidate.is_file():
                return candidate.resolve()

        expected = self.python_exe or (self.source_project_dir / ".venv" / "Scripts" / "python.exe")
        raise FileNotFoundError(f"MvImageID Python解释器不存在：{expected}")

    def validate_paths(self, pipeline_file: Path, input_dir: Path, output_dir: Path) -> None:
        if not self.source_project_dir.exists():
            raise FileNotFoundError(f"MvImageID 源码目录不存在：{self.source_project_dir}")
        # 提前确认 Python解释器存在，避免运行时才发现环境错误。
        self.get_python_executable()
        if not pipeline_file.exists():
            raise FileNotFoundError(f"Pipeline 文件不存在：{pipeline_file}")
        if not input_dir.exists():
            raise FileNotFoundError(f"输入目录不存在：{input_dir}")
        if self.plugins_directory and not self.plugins_directory.exists():
            raise FileNotFoundError(f"插件目录不存在：{self.plugins_directory}")
        output_dir.mkdir(parents=True, exist_ok=True)

    def build_command(self, pipeline_file: Path, input_dir: Path, output_dir: Path) -> List[str]:
        python_exe = self.get_python_executable()
        command = [
            str(python_exe),
            "-u",
            "-m",
            self.module_name,
            "-c",
            "-r",
            "-p",
            str(pipeline_file),
            "-i",
            str(input_dir),
            "-o",
            str(output_dir),
        ]
        if self.plugins_directory:
            command.extend(["--plugins-directory", str(self.plugins_directory)])
        return command

    def _resolve_log_file(self, output_dir: Path, log_file: str = "") -> Path:
        """
        每次分析都把标准运行日志写到当前蛋白输出目录。

        旧的全局日志路径只作为历史配置保留，不再覆盖本次输出目录日志。
        """
        return output_dir / "run_mvimageid.log"

    def _write_command_file(self, output_dir: Path, command: Sequence[str], log_file: Path) -> Path:
        command_file = output_dir / "run_mvimageid_command.txt"
        lines = [
            "COMMAND:",
            " ".join(command),
            "",
            "ARGS:",
            *[str(item) for item in command],
            "",
            "CWD:",
            str(self.source_project_dir),
            "",
            "LOG:",
            str(log_file),
        ]
        command_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return command_file

    def _get_subprocess_window_options(self) -> dict:
        """
        Windows 下隐藏外部 MvImageID Python 子进程窗口。

        打包成 GUI 程序后，如果直接 Popen python.exe，Windows 会弹出一个黑色控制台窗口。
        这里通过 CREATE_NO_WINDOW + STARTUPINFO 双保险隐藏该窗口，同时仍然保留 stdout/stderr 管道，
        让软件界面里的运行日志继续正常显示。
        """
        options = {}
        if os.name != "nt":
            return options

        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if create_no_window:
            options["creationflags"] = create_no_window

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        options["startupinfo"] = startupinfo
        return options

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------
    def run(
        self,
        pipeline_file: str,
        input_dir: str,
        output_dir: str,
        log_callback: LogCallback = None,
        cancel_callback: CancelCallback = None,
        log_file: str = "",
    ) -> MvImageIDRunResult:
        start_time = time.time()
        pipeline_path = Path(str(pipeline_file or "")).expanduser().resolve()
        input_path = Path(str(input_dir or "")).expanduser().resolve()
        output_path = Path(str(output_dir or "")).expanduser().resolve()
        output_path.mkdir(parents=True, exist_ok=True)

        local_log_file = self._resolve_log_file(output_path, log_file)
        local_log_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            self.validate_paths(pipeline_path, input_path, output_path)
            command = self.build_command(pipeline_path, input_path, output_path)
            command_file = self._write_command_file(output_path, command, local_log_file)

            if log_callback:
                log_callback("开始运行 MvImageID 分析...")
                log_callback(f"Pipeline：{pipeline_path}")
                log_callback(f"输入目录：{input_path}")
                log_callback(f"输出目录：{output_path}")
                log_callback(f"Python：{command[0]}")
                log_callback(f"运行日志：{local_log_file}")
                log_callback(f"命令文件：{command_file}")

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            scripts_dir = Path(command[0]).parent
            env["PATH"] = str(scripts_dir) + os.pathsep + env.get("PATH", "")

            output_lines: List[str] = []
            last_lines: List[str] = []

            with local_log_file.open("w", encoding="utf-8", errors="replace") as log_fp:
                log_fp.write("MvImageID analysis log\n")
                log_fp.write("=" * 60 + "\n")
                log_fp.write(f"Command : {' '.join(command)}\n")
                log_fp.write(f"CWD     : {self.source_project_dir}\n")
                log_fp.write(f"Pipeline: {pipeline_path}\n")
                log_fp.write(f"Input   : {input_path}\n")
                log_fp.write(f"Output  : {output_path}\n")
                log_fp.write(f"Plugins : {self.plugins_directory or ''}\n")
                log_fp.write("=" * 60 + "\n\n")
                log_fp.flush()

                process = analysis_process_registry.register(subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(self.source_project_dir),
                    env=env,
                    **self._get_subprocess_window_options(),
                ))

                assert process.stdout is not None
                try:
                    for line in process.stdout:
                        if cancel_callback and cancel_callback():
                            # 第一阶段只做温和终止；批量“取消后续分析”仍由上层控制。
                            # 如确实进入这里，终止当前进程，避免界面无限等待。
                            try:
                                process.terminate()
                            except Exception:
                                pass

                        line = line.rstrip()
                        if not line:
                            continue
                        output_lines.append(line)
                        last_lines.append(line)
                        if len(last_lines) > 100:
                            last_lines.pop(0)
                        log_fp.write(line + "\n")
                        log_fp.flush()
                        if log_callback:
                            log_callback(line)

                    return_code = process.wait()
                except BaseException:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    raise
                finally:
                    analysis_process_registry.unregister(process)
                elapsed = time.time() - start_time
                log_fp.write("\n" + "=" * 60 + "\n")
                log_fp.write(f"ExitCode: {return_code}\n")
                log_fp.write(f"Elapsed : {elapsed:.2f}s\n")
                log_fp.flush()

            success = return_code == 0
            error_message = ""
            if not success:
                tail = "\n".join(last_lines[-50:])
                error_message = (
                    f"MvImageID 运行失败，退出码：{return_code}，用时：{elapsed:.2f} 秒。\n"
                    f"完整日志：{local_log_file}\n"
                    f"最后日志：\n{tail}"
                )
                if log_callback:
                    log_callback(error_message)
            elif log_callback:
                log_callback(f"MvImageID 分析完成，用时 {elapsed:.2f} 秒。")

            return MvImageIDRunResult(
                success=success,
                elapsed_seconds=elapsed,
                return_code=return_code,
                command=command,
                command_file=command_file,
                log_file=local_log_file,
                output_text="\n".join(output_lines),
                error_message=error_message,
            )

        except Exception as e:
            elapsed = time.time() - start_time
            message = f"MvImageID 执行异常：{e}"
            try:
                with local_log_file.open("a", encoding="utf-8", errors="replace") as log_fp:
                    log_fp.write("\n" + message + "\n")
            except Exception:
                pass
            if log_callback:
                log_callback(message)
            return MvImageIDRunResult(
                success=False,
                elapsed_seconds=elapsed,
                return_code=-1,
                command=[],
                command_file=None,
                log_file=local_log_file,
                output_text="",
                error_message=message,
            )


class MvImageIDWorker(QThread):
    """后台线程封装，供界面层调用。"""

    log_signal = Signal(str)
    finished_signal = Signal(bool, float, str)

    def __init__(
        self,
        source_project_dir: str,
        python_exe: str = "",
        module_name: str = "MvImageID",
        pipeline_file: str = "",
        input_dir: str = "",
        output_dir: str = "",
        plugins_directory: str = "",
        log_file: str = "",
        parent=None,
        **legacy_kwargs,
    ):
        super().__init__(parent)
        self.source_project_dir = source_project_dir
        self.python_exe = python_exe
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
            python_exe=self.python_exe,
            module_name=self.module_name,
            plugins_directory=self.plugins_directory,
            log_file=self.log_file,
        )
        result = runner.run(
            pipeline_file=self.pipeline_file,
            input_dir=self.input_dir,
            output_dir=self.output_dir,
            log_callback=self.log_signal.emit,
            cancel_callback=lambda: self._cancel_requested,
            log_file=self.log_file,
        )
        message = result.output_text if result.success else (result.error_message or result.output_text)
        self.finished_signal.emit(result.success, result.elapsed_seconds, message)
from core.analysis_process_registry import analysis_process_registry
