"""Ownership reservations use process liveness independently of companion status."""

import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock

import pytest

from cc_tandem.worktree import JobOwner, OwnershipLock, WorktreeError, worker_alive


@pytest.fixture
def ownership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OwnershipLock:
    checkout = tmp_path / "T-01"
    checkout.mkdir()
    monkeypatch.setattr("cc_tandem.worktree.worker_alive", Mock(return_value=False))
    return OwnershipLock(tmp_path / "owners" / "T-01.json", checkout)


def publish(lock: OwnershipLock, *, pid: int = 424242, job: str = "old-job") -> JobOwner:
    owner = JobOwner(job, pid, "2026-01-01T00:00:00+00:00", "old-token")
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    lock.path.write_text(json.dumps(asdict(owner)))
    return owner


def test_second_job_fails_immediately_with_owner_id(ownership: OwnershipLock) -> None:
    with ownership.acquire() as owner:
        ownership.set_job(owner, "job-123")
        metadata = json.loads(ownership.path.read_text())
        assert metadata["job_id"] == "job-123"
        assert metadata["pid"] == os.getpid()
        assert metadata["started_at"]
        with pytest.raises(WorktreeError, match="job-123"), ownership.acquire():
            pytest.fail("An owned worktree cannot be shared")
    assert not ownership.path.exists()


def test_stale_pid_is_reclaimed(
    ownership: OwnershipLock, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    publish(ownership)
    monkeypatch.setattr("cc_tandem.worktree.os.kill", Mock(side_effect=ProcessLookupError))
    with ownership.acquire("new-job"):
        assert json.loads(ownership.path.read_text())["job_id"] == "new-job"
    assert "Reclaimed stale ownership" in caplog.text


def test_worker_survives_dead_coordinator_and_can_be_resumed(
    ownership: OwnershipLock, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = publish(ownership)
    monkeypatch.setattr("cc_tandem.worktree.os.kill", Mock(side_effect=ProcessLookupError))
    alive = Mock(return_value=True)
    monkeypatch.setattr("cc_tandem.worktree.worker_alive", alive)
    with pytest.raises(WorktreeError, match="old-job"), ownership.acquire("new-job"):
        pytest.fail("The live worker must retain ownership")
    with ownership.acquire("old-job") as owner:
        assert owner.started_at == old.started_at
        assert owner.pid == os.getpid()
        assert owner.token != old.token
    assert ownership.path.exists()
    alive.return_value = False
    with ownership.acquire("next-job"):
        pass
    assert not ownership.path.exists()


def test_error_keeps_live_worker_lock(
    ownership: OwnershipLock, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(RuntimeError, match="lost status"), ownership.acquire() as owner:
        ownership.set_job(owner, "worker-job")
        monkeypatch.setattr("cc_tandem.worktree.worker_alive", Mock(return_value=True))
        raise RuntimeError("lost status")
    assert json.loads(ownership.path.read_text())["job_id"] == "worker-job"


def test_unsignalable_owner_is_live(
    ownership: OwnershipLock, monkeypatch: pytest.MonkeyPatch
) -> None:
    publish(ownership)
    monkeypatch.setattr("cc_tandem.worktree.os.kill", Mock(side_effect=PermissionError))
    with pytest.raises(WorktreeError, match="old-job"), ownership.acquire():
        pytest.fail("Permission denied must not imply a dead owner")


@pytest.mark.parametrize("data", ["{", "[]", '{"job_id": "job", "pid": 0}'])
def test_invalid_metadata_is_preserved(ownership: OwnershipLock, data: str) -> None:
    ownership.path.parent.mkdir()
    ownership.path.write_text(data)
    with pytest.raises(WorktreeError, match="ownership lock"), ownership.acquire():
        pytest.fail("Corrupt ownership must not be stolen")
    assert ownership.path.read_text() == data


def test_missing_checkout_cannot_be_owned(ownership: OwnershipLock) -> None:
    ownership.worktree.rmdir()
    with pytest.raises(WorktreeError, match="missing worktree"), ownership.acquire():
        pytest.fail("A missing checkout cannot be reserved")


def test_replaced_owner_is_never_released(ownership: OwnershipLock) -> None:
    with ownership.acquire() as owner:
        publish(ownership, pid=os.getpid(), job="replacement")
        with pytest.raises(WorktreeError, match="ownership changed"):
            ownership.set_job(owner, "wrong")
    assert json.loads(ownership.path.read_text())["job_id"] == "replacement"


def test_missing_metadata_during_submission(ownership: OwnershipLock) -> None:
    with ownership.acquire() as owner:
        ownership.path.unlink()
        with pytest.raises(WorktreeError, match="ownership changed"):
            ownership.set_job(owner, "wrong")


@pytest.mark.parametrize("returncode", [0, 1, 2])
def test_worker_check_uses_exact_job_argument(
    monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    run = Mock(return_value=subprocess.CompletedProcess([], returncode, "", "error"))
    monkeypatch.setattr("cc_tandem.worktree.subprocess.run", run)
    if returncode == 2:
        with pytest.raises(WorktreeError, match="Cannot inspect worker"):
            worker_alive("job.1")
    else:
        assert worker_alive("job.1") == (returncode == 0)
    assert run.call_args.args[0] == [
        "pgrep",
        "-f",
        r"task-worker.*--job-id[ =]job\.1([[:space:]]|$)",
    ]
