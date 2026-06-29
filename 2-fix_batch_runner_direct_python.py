# -*- coding: utf-8 -*-
"""
修复批量蛋白分析 V1 的 MvImageID 启动方式。

问题：原批量窗口自己生成 run_mvimageid_batch.ps1，并使用：
    $ErrorActionPreference = "Stop"
    python ... 2>&1 | Tee-Object ...
在 Windows PowerShell 下，Python/CellProfiler 输出到 stderr 的内容容易被包装成 NativeCommandError，
导致只看到 "python : ... FullyQualifiedErrorId : NativeCommandError"，真实报错被遮住，且可能提前终止。

修复：批量分析改为直接调用 MvImageID 虚拟环境里的 python.exe，不再经过 PowerShell 包装；
同时把完整运行日志写入每个 proteinX 输出目录下的 run_mvimageid_batch.log。
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "app" / "batch_analysis_dialog.py"


def backup_file(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak_{ts}")
    shutil.copy2(path, backup)
    return backup


NEW_RUN_MVIMAGEID = r'''    def run_mvimageid(self, protein_key: str, protein_name: str, cp_input_dir: Path, cp_output_dir: Path):
        """直接调用 MvImageID 虚拟环境 python.exe，避免 PowerShell NativeCommandError 遮挡真实错误。"""
        source_project_dir = self.config.get_source_project_dir().resolve()
        venv_activate = self.config.get_venv_activate().resolve()
        module_name = self.config.get_module_name()
        pipeline_file = self.config.get_pipeline_by_protein(protein_key).resolve()
        plugins_directory = self.config.get_plugins_directory().resolve()

        if not source_project_dir.exists():
            raise FileNotFoundError(f"MvImageID 源码目录不存在：{source_project_dir}")
        if not venv_activate.exists():
            raise FileNotFoundError(f"虚拟环境激活脚本不存在：{venv_activate}")
        if not pipeline_file.exists():
            raise FileNotFoundError(f"Pipeline 文件不存在：{pipeline_file}")
        if not plugins_directory.exists():
            raise FileNotFoundError(f"插件目录不存在：{plugins_directory}")

        # Activate.ps1 所在目录通常就是 Scripts，里面有 python.exe。
        scripts_dir = venv_activate.parent
        python_exe = scripts_dir / "python.exe"
        if not python_exe.exists():
            python_exe = scripts_dir / "python"
        if not python_exe.exists():
            raise FileNotFoundError(f"虚拟环境 Python 不存在：{scripts_dir / 'python.exe'}")

        local_log_file = cp_output_dir / "run_mvimageid_batch.log"
        command_file = cp_output_dir / "run_mvimageid_batch_command.txt"

        command = [
            str(python_exe),
            "-u",
            "-m",
            module_name,
            "-c",
            "-r",
            "-p",
            str(pipeline_file),
            "-i",
            str(cp_input_dir),
            "-o",
            str(cp_output_dir),
            "--plugins-directory",
            str(plugins_directory),
        ]

        command_file.write_text("\n".join(command), encoding="utf-8")

        self.log_signal.emit(f"{protein_name} Pipeline：{pipeline_file}")
        self.log_signal.emit(f"{protein_name} Python：{python_exe}")
        self.log_signal.emit(f"{protein_name} 开始运行 MvImageID / CellProfiler ...")
        self.log_signal.emit(f"{protein_name} 运行日志：{local_log_file}")

        start_time = time.time()

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PATH"] = str(scripts_dir) + os.pathsep + env.get("PATH", "")

        last_lines = []
        with local_log_file.open("w", encoding="utf-8", errors="replace") as log_fp:
            log_fp.write("COMMAND:\n")
            log_fp.write(" ".join(command) + "\n\n")
            log_fp.write("CWD:\n")
            log_fp.write(str(source_project_dir) + "\n\n")
            log_fp.write("OUTPUT:\n")
            log_fp.flush()

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(source_project_dir),
                env=env,
            )

            assert process.stdout is not None
            for line in process.stdout:
                line = line.rstrip()
                if not line:
                    continue
                log_fp.write(line + "\n")
                log_fp.flush()
                last_lines.append(line)
                if len(last_lines) > 60:
                    last_lines.pop(0)
                self.log_signal.emit(line)

            return_code = process.wait()

        elapsed = time.time() - start_time

        if return_code != 0:
            tail = "\n".join(last_lines[-40:])
            raise RuntimeError(
                f"MvImageID / CellProfiler 运行失败，退出码：{return_code}，用时：{elapsed:.2f} 秒。\n"
                f"完整日志：{local_log_file}\n"
                f"最后日志：\n{tail}"
            )

        self.log_signal.emit(f"{protein_name} MvImageID / CellProfiler 运行完成，用时：{elapsed:.2f} 秒。")

'''


def main():
    if not TARGET.exists():
        raise FileNotFoundError(f"找不到文件：{TARGET}")

    text = TARGET.read_text(encoding="utf-8")
    backup = backup_file(TARGET)

    if "import os\n" not in text:
        text = text.replace("import subprocess\n", "import subprocess\nimport os\n")

    start = text.find("    def run_mvimageid(self, protein_key: str, protein_name: str, cp_input_dir: Path, cp_output_dir: Path):")
    if start < 0:
        raise RuntimeError("未找到 run_mvimageid 方法，无法自动修复。")

    marker = "    @staticmethod\n    def ps_quote"
    end = text.find(marker, start)
    if end < 0:
        raise RuntimeError("未找到 ps_quote 标记，无法自动定位 run_mvimageid 方法结束位置。")

    text = text[:start] + NEW_RUN_MVIMAGEID + text[end:]
    TARGET.write_text(text, encoding="utf-8")

    print(f"已备份：{backup}")
    print(f"已修复：{TARGET}")
    print("说明：批量分析现在直接调用 F:\\MvImageID\\.venv\\Scripts\\python.exe，不再生成/执行 PowerShell 脚本。")
    print("每个蛋白输出目录会生成 run_mvimageid_batch.log 和 run_mvimageid_batch_command.txt，方便定位真实错误。")


if __name__ == "__main__":
    main()
