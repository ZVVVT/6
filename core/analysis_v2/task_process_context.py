"""Per-task cancellation and process ownership; global shutdown stays registered."""

import threading
import time

from core.analysis_process_registry import analysis_process_registry
from .windows_process_tree import descendant_pids, terminate_owned_tree


class TaskProcessCancelled(Exception):
    """Cancellation control flow, never a measurement failure."""


class TaskProcessContext:
    def __init__(self):
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._processes = {}
        self._tree_diagnostics = {}

    def check_cancelled(self):
        if self.cancel_event.is_set():
            raise TaskProcessCancelled("Analysis V2 task cancelled")

    def register(self, process):
        analysis_process_registry.register(process)
        with self._lock:
            self._processes[process.pid] = process
            cancelled = self.cancel_event.is_set()
        # Covers cancellation between the pre-Popen check and registration.  The
        # process remains owned until wait()/unregister() has reaped it.
        if cancelled:
            self._terminate(process)
        return process

    def unregister(self, process):
        # Keep ownership if cleanup failed: shutdown must report the leak.
        if process.poll() is not None:
            try:
                descendants = descendant_pids(process.pid)
            except Exception:
                descendants = []
            if descendants:
                return
            with self._lock:
                self._processes.pop(process.pid, None)
                self._tree_diagnostics.pop(process.pid, None)
            analysis_process_registry.unregister(process)

    def has_active_processes(self):
        with self._lock:
            return bool(self._processes)

    def live_processes(self):
        """Return diagnostic data without releasing ownership."""
        with self._lock:
            processes = list(self._processes.values())
        result = []
        for process in processes:
            try:
                alive = process.poll() is None
            except (AttributeError, RuntimeError):
                alive = False
            pid = getattr(process, "pid", None)
            descendants = []
            try:
                descendants = [child_pid for child_pid, _depth in descendant_pids(pid)]
            except Exception as error:
                descendants = ["enumeration-error: {}".format(error)]
            if alive or descendants:
                result.append({"pid": pid, "role": "direct-child" if alive else "exited-root",
                               "descendants": descendants,
                               "tree_cleanup": self._tree_diagnostics.get(pid)})
        return result

    def _terminate(self, process, timeout=1.0, deadline=None):
        pid = getattr(process, "pid", None)
        alive = process.poll() is None
        if alive:
            try:
                taskkill = analysis_process_registry._terminate_tree(pid, process, timeout=timeout)
            except Exception as error:
                # A tree operation is best-effort.  The direct child is still task
                # owned and must receive its own termination request below.
                taskkill = {"strategy": "taskkill", "error": repr(error)}
        else:
            taskkill = {"strategy": "taskkill", "status": "root-already-exited"}
        try:
            native = terminate_owned_tree(pid, deadline=deadline, terminate_root=alive)
        except Exception as error:
            native = {"root_pid": pid, "strategy": "toolhelp-native", "error": repr(error)}
        with self._lock:
            self._tree_diagnostics[pid] = {"taskkill": taskkill, "native": native}
        try:
            if alive and process.poll() is None:
                # On Windows taskkill can be denied even for a process whose
                # Popen handle is owned by this task.  Do not treat that as a
                # successful cancellation: terminate the owned direct child.
                # This deliberately does not claim to solve descendant trees.
                process.terminate()
        except (AttributeError, OSError, RuntimeError):
            # Ownership is retained; wait() will retry and report a timeout.
            pass

    def cancel(self, deadline=None):
        self.cancel_event.set()
        with self._lock:
            processes = list(self._processes.values())
        for process in processes:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._terminate(process, timeout=min(1.0, remaining), deadline=deadline)
            else:
                self._terminate(process)

    def wait(self, deadline):
        while True:
            with self._lock:
                processes = list(self._processes.values())
            if not processes:
                return True
            for process in processes:
                if process.poll() is not None:
                    self.unregister(process)
                if time.monotonic() < deadline:
                    self._terminate(
                        process, timeout=min(1.0, deadline - time.monotonic()),
                        deadline=deadline,
                    )
            if time.monotonic() >= deadline:
                with self._lock:
                    return not self._processes
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))
