"""Per-task cancellation and process ownership; global shutdown stays registered."""

import threading
import time

from core.analysis_process_registry import analysis_process_registry


class TaskProcessCancelled(Exception):
    """Cancellation control flow, never a measurement failure."""


class TaskProcessContext:
    def __init__(self):
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._processes = {}

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
            with self._lock:
                self._processes.pop(process.pid, None)
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
            if alive:
                result.append({"pid": getattr(process, "pid", None),
                               "role": "direct-child"})
        return result

    @staticmethod
    def _terminate(process, timeout=1.0):
        try:
            analysis_process_registry._terminate_tree(process.pid, process, timeout=timeout)
        except Exception:
            # A tree operation is best-effort.  The direct child is still task
            # owned and must receive its own termination request below.
            pass
        try:
            if process.poll() is None:
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
                self._terminate(process, timeout=min(1.0, remaining))
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
                elif time.monotonic() < deadline:
                    self._terminate(process, timeout=min(1.0, deadline - time.monotonic()))
            if time.monotonic() >= deadline:
                with self._lock:
                    return not self._processes
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))
