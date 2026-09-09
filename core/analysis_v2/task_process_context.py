"""Per-task cancellation and process ownership; global shutdown stays registered."""

import threading
import time
import os
import subprocess

from core.analysis_process_registry import analysis_process_registry
from .windows_process_tree import descendant_pids, terminate_owned_tree
from .windows_job_object import WindowsJobObject, WindowsJobObjectError
from .task_process_spawn import SuspendedPopen, AtomicProcessSpawnError


class TaskProcessCancelled(Exception):
    """Cancellation control flow, never a measurement failure."""


class TaskProcessContext:
    def __init__(self):
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._processes = {}
        self._tree_diagnostics = {}
        self._job = WindowsJobObject()
        self._job_diagnostics = {"created": self._job.available,
                                 "creation_winerror": self._job.creation_error,
                                 "assignments": [], "termination": None}
        self._job_owned_pids = set()
        self._job_terminated = False

    def check_cancelled(self):
        if self.cancel_event.is_set():
            raise TaskProcessCancelled("Analysis V2 task cancelled")

    def register(self, process):
        analysis_process_registry.register(process)
        with self._lock:
            assignment = self._assign_job_locked(process)
            if assignment.get("assigned"):
                self._job_owned_pids.add(process.pid)
            self._processes[process.pid] = process
            cancelled = self.cancel_event.is_set()
        # Covers cancellation between the pre-Popen check and registration.  The
        # process remains owned until wait()/unregister() has reaped it.
        if cancelled:
            self._terminate_job_once()
            self._terminate(process)
        return process

    def spawn_atomic(self, args, **popen_kwargs):
        """Create, Job-own, register, and resume one task process atomically.

        Windows uses a suspended CPython-3.8 ``Popen``.  The context lock makes
        cancellation state, Job assignment, registration and resume one ordered
        protocol.  Non-Windows keeps the explicitly non-atomic legacy path.
        """
        if os.name != "nt" or not self._job.available:
            return self.register(subprocess.Popen(args, **popen_kwargs))
        with self._lock:
            if self.cancel_event.is_set():
                raise TaskProcessCancelled("Analysis V2 task cancelled")
            process = SuspendedPopen(args, **popen_kwargs)
            assignment = self._assign_job_locked(process)
            if not assignment.get("assigned"):
                self._abort_atomic_locked(process)
                raise AtomicProcessSpawnError(
                    "AssignProcessToJobObject failed; suspended process was reaped: {}"
                    .format(assignment))
            self._job_owned_pids.add(process.pid)
            self._processes[process.pid] = process
            analysis_process_registry.register(process)
            self._before_atomic_resume_locked(process)
            if self.cancel_event.is_set():
                self._abort_atomic_locked(process)
                raise TaskProcessCancelled("Analysis V2 task cancelled")
            try:
                process.resume()
            except Exception:
                self._abort_atomic_locked(process)
                raise
        return process

    def _before_atomic_resume_locked(self, process):
        """A deliberately empty deterministic test seam; lock remains held."""

    def _set_cancelled_locked(self):
        self.cancel_event.set()

    def _abort_atomic_locked(self, process):
        """Reap a never-resumed process and remove its direct ownership entry."""
        try:
            process.abort_before_resume()
        finally:
            self._processes.pop(process.pid, None)
            self._job_owned_pids.discard(process.pid)
            analysis_process_registry.unregister(process)
            self._close_job_if_reaped_locked()

    def _assign_job_locked(self, process):
        """Assign while the registration lock holds ownership state coherent."""
        pid = getattr(process, "pid", None)
        result = {"pid": pid, "assigned": False, "reason": "no-popen-handle"}
        handle = getattr(process, "_handle", None)
        if self._job.available and isinstance(handle, int) and handle:
            result = self._job.assign_process(handle)
            result["pid"] = pid
        elif self._job.available:
            result = {"pid": pid, "assigned": False, "reason": "no-popen-handle"}
        self._job_diagnostics["assignments"].append(result)
        return result

    def unregister(self, process):
        # Keep ownership if cleanup failed: shutdown must report the leak.
        if process.poll() is not None:
            try:
                # poll() established terminal state; wait(0) performs the
                # direct Popen reap path without allocating another deadline.
                process.wait(timeout=0)
            except (AttributeError, OSError, RuntimeError, TypeError):
                pass
            try:
                descendants = descendant_pids(process.pid)
            except Exception:
                descendants = []
            if descendants or self._job_active_locked():
                return
            with self._lock:
                self._processes.pop(process.pid, None)
                self._job_owned_pids.discard(process.pid)
                self._tree_diagnostics.pop(process.pid, None)
            analysis_process_registry.unregister(process)
            self._close_job_if_reaped_locked()

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

    def _job_active_locked(self):
        if not self._job.available:
            return False
        try:
            return self._job.active_process_count() > 0
        except WindowsJobObjectError as error:
            self._job_diagnostics["active_query_winerror"] = error.winerror
            return True

    def _close_job_if_reaped_locked(self):
        if not self._processes and self._job.available and not self._job_active_locked():
            self._job_diagnostics["active_after_reap"] = 0
            self._job_diagnostics["closed"] = self._job.close()

    def _terminate_job_once(self):
        with self._lock:
            if not self._job.available or self._job_terminated:
                return False
            self._job_terminated = True
            result = self._job.terminate()
            try:
                result["active_processes_at_cancel_return"] = self._job.active_process_count()
            except WindowsJobObjectError as error:
                result["active_at_cancel_return_query_winerror"] = error.winerror
            self._job_diagnostics["termination"] = result
            return bool(result.get("requested"))

    def cancel(self, deadline=None):
        # Cancellation becomes visible under the same lock used by atomic spawn:
        # a process is either resumed before cancellation takes ownership, or it
        # observes the sticky event while still suspended and is never resumed.
        with self._lock:
            self._set_cancelled_locked()
        job_terminated = self._terminate_job_once()
        with self._lock:
            processes = list(self._processes.values())
        for process in processes:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                # Job termination is the primary ownership operation.  Keep the
                # established tree/direct cleanup as bounded convergence fallback:
                # TerminateJobObject returning does not mean every member has
                # already exited at this instant.
                self._terminate(process, timeout=min(1.0, remaining), deadline=deadline)
            else:
                self._terminate(process)

    def _is_job_owned(self, process):
        with self._lock:
            return getattr(process, "pid", None) in self._job_owned_pids

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
