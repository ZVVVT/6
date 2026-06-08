import subprocess
from pathlib import Path


class CellProfilerRunner:
    def __init__(self, cellprofiler_exe: str):
        self.cellprofiler_exe = Path(cellprofiler_exe)

    def run(self, pipeline_file: str, input_dir: str, output_dir: str):
        pipeline_file = Path(pipeline_file)
        input_dir = Path(input_dir)
        output_dir = Path(output_dir)

        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(self.cellprofiler_exe),
            "-c",
            "-r",
            "-p",
            str(pipeline_file),
            "-i",
            str(input_dir),
            "-o",
            str(output_dir),
        ]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )

        log = result.stdout + "\n" + result.stderr

        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "log": log,
            "cmd": " ".join(cmd),
        }