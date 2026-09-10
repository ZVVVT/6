import threading

from core.analysis_v2.task_supervisor import (
    TaskSupervisor, TaskSupervisorState,
)
from core.analysis_v2.task_process_context import TaskProcessCancelled


class Process:
    pid = 101

    def __init__(self, finished=False):
        self.finished = finished

    def poll(self):
        return 0 if self.finished else None


def test_initial_state_is_running():
    assert TaskSupervisor().state == TaskSupervisorState.RUNNING


def test_first_failure_contains_error_identity_and_wins():
    supervisor = TaskSupervisor()
    first = supervisor.record_failure("c18b", "001", ValueError("primary"))
    second = supervisor.record_failure("measurement", "002", RuntimeError("secondary"))
    assert second is first
    assert (first.stage, first.field, first.exception_type, first.message) == (
        "c18b", "001", "ValueError", "primary",
    )


def test_failure_triggered_cancel_finishes_failed():
    supervisor = TaskSupervisor()
    supervisor.record_failure("head", "001", RuntimeError("primary"))
    supervisor.request_cancel("failure", failure_triggered=True)
    assert supervisor.finalize() == TaskSupervisorState.FAILED


def test_secondary_cancel_exception_does_not_replace_root_failure():
    supervisor = TaskSupervisor()
    first = supervisor.record_failure("c18b", "001", RuntimeError("primary"))
    supervisor.request_cancel("failure", failure_triggered=True)
    assert supervisor.record_failure("measurement", "001", TaskProcessCancelled("terminated")) is first


def test_user_cancel_without_failure_finishes_cancelled():
    supervisor = TaskSupervisor()
    supervisor.request_cancel("user")
    assert supervisor.finalize() == TaskSupervisorState.CANCELLED
    assert not supervisor.cancellation_is_failure_triggered
    assert supervisor.process_context._job_diagnostics["finish_requested"]


def test_failure_finalize_ends_future_spawn_lifetime():
    supervisor = TaskSupervisor()
    supervisor.record_failure("head", "001", RuntimeError("primary"))
    assert supervisor.finalize() == TaskSupervisorState.FAILED
    assert supervisor.process_context._job_diagnostics["finish_requested"]


def test_completion_waits_for_worker_and_process_cleanup():
    supervisor = TaskSupervisor()
    supervisor.register_worker("worker")
    assert not supervisor.mark_completed()
    supervisor.mark_worker_done("worker")
    process = Process(finished=True)
    supervisor.register_process(process)
    assert not supervisor.mark_completed()
    supervisor.unregister_process(process)
    assert supervisor.mark_completed()


def test_register_and_unregister_delegates_to_context():
    supervisor = TaskSupervisor()
    process = Process(finished=True)
    assert supervisor.register_process(process) is process
    supervisor.unregister_process(process)
    assert not supervisor.process_context.has_active_processes()


def test_register_worker_is_rejected_after_cancel():
    supervisor = TaskSupervisor()
    supervisor.request_cancel("user")
    assert not supervisor.register_worker("late")


def test_register_process_after_cancel_uses_context_immediate_termination(monkeypatch):
    supervisor = TaskSupervisor()
    stopped = []
    monkeypatch.setattr(supervisor.process_context, "_terminate", lambda process: stopped.append(process))
    supervisor.request_cancel("user")
    process = Process()
    supervisor.register_process(process)
    assert stopped == [process]


def test_concurrent_failure_recording_has_one_root_failure():
    supervisor = TaskSupervisor()
    barrier = threading.Barrier(8)
    failures = []

    def record(index):
        barrier.wait()
        failures.append(supervisor.record_failure("stage{}".format(index), None,
                                                  RuntimeError("error{}".format(index))))

    threads = [threading.Thread(target=record, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len({id(failure) for failure in failures}) == 1
    assert supervisor.state == TaskSupervisorState.FAILED


def test_deadline_configuration_is_data_only():
    supervisor = TaskSupervisor(graceful_deadline_seconds=2, force_deadline_seconds=3)
    assert supervisor.shutdown_deadlines == {"graceful_seconds": 2.0, "force_seconds": 3.0}
