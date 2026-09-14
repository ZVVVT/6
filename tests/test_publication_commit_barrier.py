"""M1E-0: deterministic races and the existing SQLite/publication boundary."""

import threading
from unittest.mock import Mock

import pytest

from core.analysis_v2 import result_completion_service as service
from core.analysis_v2.task_supervisor import TaskSupervisor, TaskPublicationRejected
from core.analysis_v2.task_process_context import TaskProcessCancelled
from core.analysis_v2.tail_result_publisher import TailResultPublication
from core.database import Database
from test_analysis_v2_result_completion_service import completion, publication
from test_tail_result_publisher import _create_valid_tail_output, _measurement_contract


def owner_ready():
    owner = TaskSupervisor()
    owner.expect_publication()
    assert owner.finalize() == "RUNNING"
    assert owner.process_context._finish_requested
    return owner


def start_thread(action):
    outcomes = []

    def run():
        try:
            outcomes.append(action())
        except BaseException as error:
            outcomes.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcomes


def joined(thread, outcomes):
    thread.join(5)
    assert not thread.is_alive(), "publication/cancel deadlock"
    assert len(outcomes) == 1
    return outcomes[0]


def setup_mock(tmp_path, monkeypatch):
    owner = owner_ready()
    value, summary = completion(tmp_path)
    staged = publication(summary)
    stage = Mock(return_value=staged)
    monkeypatch.setattr(service, "stage_head_measurement_output", stage)
    database = Mock()
    publish = lambda: service.publish_measured_completion(value, database, supervisor=owner)
    return owner, stage, staged, database, publish


def test_cancel_wins_before_begin(tmp_path, monkeypatch):
    owner, stage, staged, database, publish = setup_mock(tmp_path, monkeypatch)
    ready, release = threading.Event(), threading.Event()

    def blocked():
        ready.set()
        assert release.wait(5)
        return publish()

    thread, outcomes = start_thread(blocked)
    assert ready.wait(5)
    cancel, result = start_thread(lambda: owner.request_cancel("user"))
    try:
        assert joined(cancel, result) is True
    finally:
        release.set()
    assert isinstance(joined(thread, outcomes), TaskProcessCancelled)
    stage.assert_not_called()
    database.replace_protein_analysis_with_fields.assert_not_called()
    assert owner.finalize() == "CANCELLED"
    assert owner.root_failure is None


def test_publication_wins_and_duplicate_is_rejected(tmp_path, monkeypatch):
    owner, stage, staged, database, publish = setup_mock(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()

    def install(**kwargs):
        assert owner.publication_started
        entered.set()
        assert release.wait(5)
        return staged

    stage.side_effect = install
    thread, outcomes = start_thread(publish)
    assert entered.wait(5)
    try:
        cancel, result = start_thread(lambda: owner.request_cancel("late"))
        assert joined(cancel, result) is False
        assert not owner.cancel_event.is_set()
        assert owner.cancel_reason is None
        with pytest.raises(TaskPublicationRejected):
            publish()
        assert owner.root_failure is None
        assert owner.finalize() == "RUNNING"
    finally:
        release.set()
    assert not isinstance(joined(thread, outcomes), BaseException)
    assert owner.finalize() == "COMPLETED"
    assert owner.request_cancel("shutdown") is False
    with pytest.raises(TaskPublicationRejected):
        publish()
    with pytest.raises(RuntimeError):
        owner.mark_publication_completed()
    stage.assert_called_once()
    database.replace_protein_analysis_with_fields.assert_called_once()
    staged.commit.assert_called_once()
    assert owner.state == "COMPLETED"


@pytest.mark.parametrize("iteration", range(24))
def test_simultaneous_has_exactly_one_legal_winner(tmp_path, monkeypatch, iteration):
    owner, stage, staged, database, publish = setup_mock(tmp_path, monkeypatch)
    barrier = threading.Barrier(2, timeout=5)

    def compete(action):
        barrier.wait()
        return action()

    publisher, published = start_thread(lambda: compete(publish))
    canceller, cancelled = start_thread(lambda: compete(lambda: owner.request_cancel("user")))
    result, accepted = joined(publisher, published), joined(canceller, cancelled)
    if accepted is True:
        assert isinstance(result, TaskProcessCancelled)
        assert owner.finalize() == "CANCELLED"
        stage.assert_not_called()
        database.replace_protein_analysis_with_fields.assert_not_called()
    else:
        assert accepted is False
        assert not isinstance(result, BaseException)
        assert owner.finalize() == "COMPLETED"
        assert not owner.cancel_event.is_set()
        stage.assert_called_once()
        database.replace_protein_analysis_with_fields.assert_called_once()
        staged.commit.assert_called_once()


def test_cancel_accepted_before_event_is_set_still_blocks_begin(tmp_path, monkeypatch):
    owner, stage, staged, database, publish = setup_mock(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = owner.process_context.cancel

    def delayed_cancel(**kwargs):
        entered.set()
        assert release.wait(5)
        original(**kwargs)

    monkeypatch.setattr(owner.process_context, "cancel", delayed_cancel)
    thread, outcomes = start_thread(lambda: owner.request_cancel("user"))
    assert entered.wait(5)
    try:
        assert not owner.cancel_event.is_set()
        with pytest.raises(TaskProcessCancelled):
            publish()
        assert owner.finalize() == "CANCELLED"
    finally:
        release.set()
    assert joined(thread, outcomes) is True
    stage.assert_not_called()


def test_begin_rejects_missing_obligation_failure_and_terminal():
    owner = TaskSupervisor()
    with pytest.raises(TaskPublicationRejected):
        owner.begin_publication()
    with pytest.raises(RuntimeError):
        owner.mark_publication_completed()
    owner.expect_publication()
    with pytest.raises(RuntimeError):
        owner.expect_publication()
    root = owner.record_failure("measurement", None, ValueError("root"))
    owner.request_cancel("cleanup", failure_triggered=True)
    with pytest.raises(TaskPublicationRejected):
        owner.begin_publication()
    assert owner.root_failure is root
    assert owner.finalize() == "FAILED"
    other = TaskSupervisor()
    assert other.finalize() == "COMPLETED"
    with pytest.raises(TaskPublicationRejected):
        other.begin_publication()


def test_failure_cleanup_during_rollback_never_kills_context(tmp_path, monkeypatch):
    owner, stage, staged, database, publish = setup_mock(tmp_path, monkeypatch)
    cause = ValueError("DB business failure")
    cleanup = OSError("rollback failure")
    database.replace_protein_analysis_with_fields.side_effect = cause
    cancel_context = Mock(wraps=owner.process_context.cancel)
    monkeypatch.setattr(owner.process_context, "cancel", cancel_context)

    def rollback():
        assert owner.root_failure.message == str(cause)
        assert owner.request_cancel("late user") is False
        assert owner.request_cancel("failure cleanup", failure_triggered=True) is False
        raise cleanup

    staged.rollback.side_effect = rollback
    with pytest.raises(service.AnalysisV2CompletionPublishError) as caught:
        publish()
    assert caught.value.cause is cause
    assert caught.value.rollback_error is cleanup
    assert owner.root_failure.message == str(cause)
    assert owner.root_failure.details["rollback_error"] is cleanup
    assert owner.finalize() == "FAILED"
    assert not owner.publication_completed
    assert not owner.cancel_event.is_set()
    cancel_context.assert_not_called()


@pytest.fixture
def real_tail(tmp_path):
    database = Database(str(tmp_path / "results.db"))
    with database.connect() as conn:
        case_id = conn.execute("INSERT INTO cases (case_no) VALUES ('barrier')").lastrowid
        conn.commit()
    value, unused = completion(tmp_path, "tail")
    value["context"]["case_id"] = case_id
    value["expected_field_count"] = 1
    value["measurement_contract"] = _measurement_contract(2, 2, 0)
    value["source_dir"].mkdir()
    _create_valid_tail_output(value["source_dir"], field_count=1)
    service.publish_measured_completion(value, database, supervisor=owner_ready())
    old_rows = database.get_protein_analysis_by_case(case_id)
    old_files = {p.name: p.read_bytes() for p in value["target_dir"].iterdir()}
    _create_valid_tail_output(value["source_dir"], field_count=1, tail_count=3)
    value["measurement_contract"] = _measurement_contract(3, 2, 1)
    return database, value, old_rows, old_files


def test_real_sqlite_failure_before_commit_restores_existing_files(real_tail, monkeypatch):
    database, value, old_rows, old_files = real_tail
    owner = owner_ready()
    with database.connect() as conn:
        conn.execute("CREATE TRIGGER reject_fields BEFORE INSERT ON field_results "
                     "BEGIN SELECT RAISE(ABORT, 'injected DB failure'); END")
        conn.commit()
    original = database.replace_protein_analysis_with_fields

    def fail(**kwargs):
        assert owner.request_cancel("late") is False
        return original(**kwargs)

    monkeypatch.setattr(database, "replace_protein_analysis_with_fields", fail)
    with pytest.raises(service.AnalysisV2CompletionPublishError) as error:
        service.publish_measured_completion(value, database, supervisor=owner)
    assert error.value.stage == "database"
    assert "injected DB failure" in owner.root_failure.message
    assert owner.finalize() == "FAILED"
    assert database.get_protein_analysis_by_case(value["context"]["case_id"]) == old_rows
    assert {p.name: p.read_bytes() for p in value["target_dir"].iterdir()} == old_files
    assert not owner.cancel_event.is_set()


def test_post_db_commit_failure_records_closed_rollback_noop(real_tail, monkeypatch):
    database, value, old_rows, old_files = real_tail
    owner = owner_ready()
    cause = OSError("post-DB-commit publication finalization failure")
    original_commit, original_rollback = TailResultPublication.commit, TailResultPublication.rollback
    observations = []

    def fail_after_close(publication):
        original_commit(publication)
        assert publication._closed
        assert owner.request_cancel("late") is False
        raise cause

    def rollback(publication):
        observations.append(publication._closed)
        return original_rollback(publication)

    monkeypatch.setattr(TailResultPublication, "commit", fail_after_close)
    monkeypatch.setattr(TailResultPublication, "rollback", rollback)
    with pytest.raises(service.AnalysisV2CompletionPublishError) as caught:
        service.publish_measured_completion(value, database, supervisor=owner)
    assert caught.value.cause is cause
    assert caught.value.stage == str(cause)
    assert observations == [True]  # The existing rollback is a no-op.
    assert owner.finalize() == "FAILED"
    assert not owner.publication_completed
    assert owner.root_failure.message == str(cause)
    assert owner.request_cancel("shutdown") is False
    assert database.get_protein_analysis_by_case(value["context"]["case_id"]) != old_rows
    assert (value["target_dir"] / "G_objects.csv").read_bytes() != old_files["G_objects.csv"]
    assert (value["target_dir"] / "G_objects.csv").read_bytes() == (value["source_dir"] / "G_objects.csv").read_bytes()
    with pytest.raises(TaskPublicationRejected):
        service.publish_measured_completion(value, database, supervisor=owner)
    assert observations == [True]
