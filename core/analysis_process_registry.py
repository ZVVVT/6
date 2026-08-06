"""Track and terminate only child process trees started by the active analysis."""

import os
import subprocess
import threading


class AnalysisProcessRegistry:
    """Thread-safe registry of root PIDs owned by the current application analysis."""

    def __init__(self):
        self._lock = threading.Lock()
        self._processes = {}

    def register(self, process):
        pid_value = getattr(process, "pid", 0)
        if not pid_value and hasattr(process, "processId"):
            pid_value = process.processId()
        pid = int(pid_value or 0)
        if pid > 0:
            with self._lock:
                self._processes[pid] = process
        return process

    def unregister(self, process):
        pid_value = getattr(process, "pid", 0)
        if not pid_value and hasattr(process, "processId"):
            pid_value = process.processId()
        pid = int(pid_value or 0)
        with self._lock:
            if pid > 0:
                self._processes.pop(pid, None)
            else:
                for registered_pid, registered_process in list(self._processes.items()):
                    if registered_process is process:
                        self._processes.pop(registered_pid, None)

    def root_pids(self):
        with self._lock:
            return sorted(self._processes)

    @staticmethod
    def _terminate_tree(pid, process=None):
        if pid <= 0:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        if process is not None:
            try:
                process.terminate()
            except BaseException:
                pass

    def terminate_all(self):
        with self._lock:
            items = list(self._processes.items())
        for pid, process in items:
            self._terminate_tree(pid, process)

    def clear_finished(self):
        with self._lock:
            for pid, process in list(self._processes.items()):
                try:
                    finished = process.poll() is not None
                except (AttributeError, RuntimeError):
                    finished = True
                if finished:
                    self._processes.pop(pid, None)


analysis_process_registry = AnalysisProcessRegistry()
