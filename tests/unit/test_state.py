"""Check checkpoint integrity independently of jobs and git."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event, Thread

import pytest

from cc_tandem.effort import Effort
from cc_tandem.state import Phase, StateError, StateStore, TicketState, timestamp


def record(ticket: str = "T-01") -> TicketState:
    now = timestamp()
    return TicketState(ticket, 0, Effort.MEDIUM, now, now)


def test_empty_and_round_trip(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    assert store.read() == {}
    saved = store.put(replace(record(), phase=Phase.WAITING, logs=("one", "two")))
    assert store.read() == {"T-01": saved}
    assert saved.updated_at >= saved.created_at
    assert json.loads(store.path.read_text())["version"] == 1


def test_concurrent_writers_and_readers(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.put(record())
    stop = Event()
    failures: list[BaseException] = []

    def reader() -> None:
        try:
            while not stop.is_set():
                assert store.read()
                assert isinstance(json.loads(store.path.read_text())["tickets"], dict)
        except BaseException as exc:
            failures.append(exc)

    def writer(index: int) -> None:
        independent = StateStore(tmp_path)
        for attempt in range(10):
            independent.put(replace(record(f"T-{index + 2:02}"), attempts=attempt))

    thread = Thread(target=reader)
    thread.start()
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(writer, range(4)))
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert failures == []
    assert len(store.read()) == 5
    assert all(item.attempts == 9 for key, item in store.read().items() if key != "T-01")


@pytest.mark.parametrize(
    "raw",
    [
        "{broken",
        "[]",
        '{"version": 2}',
        '{"version": 1, "tickets": []}',
        '{"version": 1, "tickets": {"T-01": null}}',
        '{"version": 1, "tickets": {"T-01": {}}}',
    ],
)
def test_invalid_document(tmp_path: Path, raw: str) -> None:
    store = StateStore(tmp_path)
    store.path.parent.mkdir()
    store.path.write_text(raw)
    with pytest.raises(StateError):
        store.read()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("extra", "bad"),
        ("ticket", ""),
        ("lane", True),
        ("attempts", -1),
        ("effort", "xhigh"),
        ("phase", "unknown"),
        ("job_id", 123),
        ("logs", [1]),
        ("logs", "string"),
        ("created_at", "bad"),
        ("retry_at", "2026-10-05T00:00:00"),
    ],
)
def test_invalid_record(tmp_path: Path, field: str, value: object) -> None:
    store = StateStore(tmp_path)
    store.path.parent.mkdir()
    data = asdict(record())
    data[field] = value
    store.path.write_text(json.dumps({"version": 1, "tickets": {"T-01": data}}))
    with pytest.raises(StateError):
        store.read()


def test_mismatched_key(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.put(record())
    data = json.loads(store.path.read_text())
    data["tickets"]["T-02"] = data["tickets"].pop("T-01")
    store.path.write_text(json.dumps(data))
    with pytest.raises(StateError, match="key"):
        store.read()


def test_failed_replace_preserves_old_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path)
    before = store.put(record())

    def fail_replace(self: Path, target: Path) -> Path:
        raise OSError("disk unavailable")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        store.put(replace(before, attempts=1))
    assert store.read() == {"T-01": before}
    assert {path.name for path in store.path.parent.iterdir()} == {"state.json", "state.lock"}


@pytest.mark.parametrize("target", [".tandem", ".tandem/state.json", ".tandem/state.lock"])
def test_symlink_escape(tmp_path: Path, target: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    path = root / target
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside if target == ".tandem" else outside / "file")
    with pytest.raises(StateError, match="escapes"):
        StateStore(root).put(record())
    assert list(outside.iterdir()) == []


def test_logs(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    output = store.write_log("T-01-1.json", "evidence")
    assert Path(output).read_text() == "evidence"
    with pytest.raises(StateError, match="component"):
        store.write_log("../escape", "bad")
    with pytest.raises(StateError, match="component"):
        store.write_log("", "bad")
