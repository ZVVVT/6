"""Task-wide persistence must remain safe across independent store instances."""

import json
import threading

from core.analysis_v2.manifest_store import ManifestStore
from core.analysis_v2.stage_logger import StageLogger
from core.analysis_v2.task_state import TaskStateStore


def _join(threads):
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()


def _state_stores(root):
    path = root / "state.json"
    first = TaskStateStore(path, "concurrent-task")
    second = TaskStateStore(path, "concurrent-task")
    first.initialize(case_no="CASE", protein_key="P3")
    return first, second


def _manifest_stores(root):
    path = root / "manifest.json"
    first = ManifestStore(path, root, "concurrent-task")
    second = ManifestStore(path, root, "concurrent-task")
    first.initialize(case_no="CASE", protein_key="P3")
    return first, second


def _pause_after_first_load(monkeypatch, store):
    entered = threading.Event()
    release = threading.Event()
    original_load = store.load

    def paused_load():
        data = original_load()
        entered.set()
        assert release.wait(10)
        return data

    monkeypatch.setattr(store, "load", paused_load)
    return entered, release


def test_state_updates_from_independent_stores_do_not_lose_history(
        tmp_path, monkeypatch):
    first, second = _state_stores(tmp_path)
    entered, release = _pause_after_first_load(monkeypatch, first)
    second_attempted = threading.Event()
    second_finished = threading.Event()

    thread_a = threading.Thread(
        target=lambda: first.update("head_segmenting", "head", "first"),
    )

    def update_second():
        second_attempted.set()
        second.update("head_segmented", "head", "second")
        second_finished.set()

    thread_b = threading.Thread(target=update_second)
    thread_a.start()
    assert entered.wait(10)
    thread_b.start()
    assert second_attempted.wait(10)
    assert not second_finished.wait(0.1)
    release.set()
    _join([thread_a, thread_b])

    data = first.load()
    assert [item["message"] for item in data["history"]] == [
        "任务状态已创建", "first", "second",
    ]


def test_manifest_different_records_from_independent_stores_are_preserved(
        tmp_path, monkeypatch):
    first, second = _manifest_stores(tmp_path)
    first_file = tmp_path / "first.txt"
    second_file = tmp_path / "second.txt"
    first_file.write_text("first", encoding="utf-8")
    second_file.write_text("second", encoding="utf-8")
    entered, release = _pause_after_first_load(monkeypatch, first)
    second_attempted = threading.Event()
    second_finished = threading.Event()

    thread_a = threading.Thread(
        target=lambda: first.add_file(first_file, "first", "stage"),
    )

    def add_second():
        second_attempted.set()
        second.add_file(second_file, "second", "stage")
        second_finished.set()

    thread_b = threading.Thread(target=add_second)
    thread_a.start()
    assert entered.wait(10)
    thread_b.start()
    assert second_attempted.wait(10)
    assert not second_finished.wait(0.1)
    release.set()
    _join([thread_a, thread_b])

    data = first.load()
    assert {item["record_id"] for item in data["files"]} == {
        "first::first.txt", "second::second.txt",
    }


def test_manifest_same_record_keeps_one_complete_last_record(
        tmp_path, monkeypatch):
    first, second = _manifest_stores(tmp_path)
    source = tmp_path / "same.txt"
    source.write_text("same", encoding="utf-8")
    entered, release = _pause_after_first_load(monkeypatch, first)
    second_attempted = threading.Event()

    thread_a = threading.Thread(
        target=lambda: first.add_file(
            source, "result", "stage", metadata={"writer": "first"},
        ),
    )

    def add_second():
        second_attempted.set()
        second.add_file(
            source, "result", "stage", metadata={"writer": "second"},
        )

    thread_b = threading.Thread(target=add_second)
    thread_a.start()
    assert entered.wait(10)
    thread_b.start()
    assert second_attempted.wait(10)
    release.set()
    _join([thread_a, thread_b])

    data = first.load()
    assert len(data["files"]) == 1
    assert data["files"][0]["record_id"] == "result::same.txt"
    assert data["files"][0]["metadata"] == {"writer": "second"}


def test_stage_loggers_append_complete_events_and_text_lines(tmp_path):
    loggers = [StageLogger(tmp_path / "logs", "concurrent-task") for _ in range(8)]
    event_start = threading.Barrier(8)

    def write_events(thread_id):
        event_start.wait()
        for sequence in range(50):
            loggers[thread_id].event(
                "concurrent_event", "stage", "running",
                extra={"thread_id": thread_id, "sequence": sequence},
            )

    event_threads = [
        threading.Thread(target=write_events, args=(thread_id,))
        for thread_id in range(8)
    ]
    for thread in event_threads:
        thread.start()
    _join(event_threads)

    event_lines = (tmp_path / "logs" / "events.jsonl").read_text(
        encoding="utf-8",
    ).splitlines()
    assert len(event_lines) == 400
    events = [json.loads(line) for line in event_lines]
    assert {
        (item["extra"]["thread_id"], item["extra"]["sequence"])
        for item in events
    } == {(thread_id, sequence) for thread_id in range(8) for sequence in range(50)}

    line_start = threading.Barrier(8)
    expected_lines = set()
    expected_guard = threading.Lock()

    def write_lines(thread_id):
        line_start.wait()
        for sequence in range(25):
            message = "thread={} sequence={} {}".format(
                thread_id, sequence, "x" * 1000,
            )
            with expected_guard:
                expected_lines.add(message)
            loggers[thread_id].info("stage", message)

    line_threads = [
        threading.Thread(target=write_lines, args=(thread_id,))
        for thread_id in range(8)
    ]
    for thread in line_threads:
        thread.start()
    _join(line_threads)

    text_lines = (tmp_path / "logs" / "task.log").read_text(
        encoding="utf-8",
    ).splitlines()
    assert len(text_lines) == 200
    assert {line.rsplit(" | ", 1)[-1] for line in text_lines} == expected_lines


def test_mark_failed_and_update_keep_both_history_entries(tmp_path, monkeypatch):
    first, second = _state_stores(tmp_path)
    entered, release = _pause_after_first_load(monkeypatch, first)
    thread_a = threading.Thread(
        target=lambda: first.update("head_segmenting", "head", "update"),
    )
    thread_b = threading.Thread(
        target=lambda: second.mark_failed("tail", RuntimeError("boom")),
    )
    thread_a.start()
    assert entered.wait(10)
    thread_b.start()
    release.set()
    _join([thread_a, thread_b])

    data = first.load()
    assert [item["message"] for item in data["history"]] == [
        "任务状态已创建", "update", "任务执行失败：boom",
    ]
    assert data["status"] == "failed"


def test_initialize_race_preserves_existing_file_semantics(tmp_path):
    path = tmp_path / "state.json"
    first = TaskStateStore(path, "concurrent-task")
    second = TaskStateStore(path, "concurrent-task")
    start = threading.Barrier(2)
    outcomes = []
    guard = threading.Lock()

    def initialize(store):
        start.wait()
        try:
            result = store.initialize(case_no="CASE", protein_key="P3")
            outcome = ("created", result)
        except FileExistsError:
            outcome = ("exists", None)
        with guard:
            outcomes.append(outcome)

    threads = [threading.Thread(target=initialize, args=(store,)) for store in (first, second)]
    for thread in threads:
        thread.start()
    _join(threads)

    assert [outcome[0] for outcome in outcomes].count("created") == 1
    assert [outcome[0] for outcome in outcomes].count("exists") == 1
    data = first.load()
    assert data["task_id"] == "concurrent-task"
    assert data["history"][0]["status"] == "created"


def test_manifest_initialize_race_preserves_existing_file_semantics(tmp_path):
    path = tmp_path / "manifest.json"
    first = ManifestStore(path, tmp_path, "concurrent-task")
    second = ManifestStore(path, tmp_path, "concurrent-task")
    start = threading.Barrier(2)
    outcomes = []
    guard = threading.Lock()

    def initialize(store):
        start.wait()
        try:
            result = store.initialize(case_no="CASE", protein_key="P3")
            outcome = ("created", result)
        except FileExistsError:
            outcome = ("exists", None)
        with guard:
            outcomes.append(outcome)

    threads = [threading.Thread(target=initialize, args=(store,)) for store in (first, second)]
    for thread in threads:
        thread.start()
    _join(threads)

    assert [outcome[0] for outcome in outcomes].count("created") == 1
    assert [outcome[0] for outcome in outcomes].count("exists") == 1
    data = first.load()
    assert data["task_id"] == "concurrent-task"
    assert data["files"] == []
