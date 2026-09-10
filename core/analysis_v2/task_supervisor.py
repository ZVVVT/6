"""Minimal task-level ownership and outcome coordination for Analysis V2."""

import threading
from dataclasses import dataclass

from .task_process_context import TaskProcessContext


class TaskSupervisorState:
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class TaskFailure:
    """The first non-cancellation error observed for a task."""

    stage: str
    field: object
    exception_type: str
    message: str
    details: object


class TaskSupervisor:
    """Own task state while delegating process mechanics to ``TaskProcessContext``.

    This class deliberately does not introduce a new process termination policy.
    ``TaskProcessContext`` remains the one implementation that registers with the
    application-wide shutdown registry and reclaims the task's process trees.
    """

    def __init__(self, process_context=None, graceful_deadline_seconds=5.0,
                 force_deadline_seconds=5.0):
        self.process_context = process_context or TaskProcessContext()
        self.cancel_event = self.process_context.cancel_event
        self._lock = threading.RLock()
        self._state = TaskSupervisorState.RUNNING
        self._root_failure = None
        self._cancel_reason = None
        self._failure_triggered_cancel = False
        self._workers = set()
        self.configure_shutdown_deadlines(
            graceful_deadline_seconds, force_deadline_seconds,
        )

    @property
    def state(self):
        with self._lock:
            return self._state

    @property
    def root_failure(self):
        with self._lock:
            return self._root_failure

    @property
    def cancel_reason(self):
        with self._lock:
            return self._cancel_reason

    @property
    def cancellation_is_failure_triggered(self):
        with self._lock:
            return self._failure_triggered_cancel

    @property
    def shutdown_deadlines(self):
        with self._lock:
            return {
                "graceful_seconds": self._graceful_deadline_seconds,
                "force_seconds": self._force_deadline_seconds,
            }

    def configure_shutdown_deadlines(self, graceful_seconds, force_seconds):
        graceful = float(graceful_seconds)
        force = float(force_seconds)
        if graceful < 0 or force < 0:
            raise ValueError("shutdown deadlines must be nonnegative")
        with self._lock:
            self._graceful_deadline_seconds = graceful
            self._force_deadline_seconds = force

    def register_process(self, process):
        """Register through the existing task process context.

        A process registered after cancellation remains registered long enough for
        the context's existing race-safe immediate termination path to reclaim it.
        """
        return self.process_context.register(process)

    def unregister_process(self, process):
        self.process_context.unregister(process)

    def register_worker(self, worker):
        with self._lock:
            if self._state != TaskSupervisorState.RUNNING:
                return False
            self._workers.add(worker)
            return True

    def mark_worker_done(self, worker):
        with self._lock:
            self._workers.discard(worker)

    def record_failure(self, stage, field, exception, details=None):
        """Persist the first real error; cancellation side effects never replace it."""
        failure = TaskFailure(
            stage=str(stage), field=field,
            exception_type=type(exception).__name__, message=str(exception),
            details=details,
        )
        with self._lock:
            if self._root_failure is None:
                self._root_failure = failure
                self._state = TaskSupervisorState.FAILED
                return failure
            return self._root_failure

    def request_cancel(self, reason, failure_triggered=False, deadline=None):
        """Set sticky cancellation and notify currently registered resources."""
        with self._lock:
            if self._cancel_reason is None:
                self._cancel_reason = str(reason)
            self._failure_triggered_cancel = (
                self._failure_triggered_cancel or bool(failure_triggered)
            )
            if self._root_failure is None and self._state == TaskSupervisorState.RUNNING:
                self._state = TaskSupervisorState.CANCELLING
        self.process_context.cancel(deadline=deadline)

    def mark_completed(self):
        with self._lock:
            if self._root_failure is not None:
                self._state = TaskSupervisorState.FAILED
                self.process_context.finish()
                return False
            if self._workers or self.process_context.has_active_processes():
                return False
            if self.cancel_event.is_set():
                self._state = TaskSupervisorState.CANCELLED
            else:
                self._state = TaskSupervisorState.COMPLETED
            self._finish_context_if_reaped_locked()
            return True

    def finalize(self):
        """Set the terminal state after registered workers have finished."""
        with self._lock:
            if self._root_failure is not None:
                self._state = TaskSupervisorState.FAILED
                # A failure is terminal even while cancellation cleanup is
                # still reaping children; prevent any later spawn immediately.
                self.process_context.finish()
            else:
                if self._workers or self.process_context.has_active_processes():
                    return self._state
                if self.cancel_event.is_set():
                    self._state = TaskSupervisorState.CANCELLED
                else:
                    self._state = TaskSupervisorState.COMPLETED
            self._finish_context_if_reaped_locked()
            return self._state

    def _finish_context_if_reaped_locked(self):
        """The supervisor alone closes its Context's future-spawn lifetime."""
        if not self._workers and not self.process_context.has_active_processes():
            self.process_context.finish()
