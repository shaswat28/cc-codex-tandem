"""Offline companion fixtures shared by unit and future lane integration tests."""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from cc_tandem.companion import JsonValue


@dataclass(frozen=True)
class FakeCompanion:
    script: Path
    scenario_file: Path
    state_file: Path

    def configure(self, scenario: JsonValue) -> None:
        """Set a scenario (or attempt sequence) and reset previously recorded jobs."""
        self.scenario_file.write_text(json.dumps(scenario), encoding="utf-8")
        self.state_file.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def offline_companion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeCompanion:
    """Default every test to the fake; discovery unit tests can override this."""
    # The fake has no task-worker process; process-list access can also be
    # sandboxed. Individual ownership tests explicitly simulate live workers.
    monkeypatch.setattr("cc_tandem.worktree.worker_alive", lambda _: False)
    monkeypatch.setattr("cc_tandem.lanes.worker_alive", lambda _: False)
    fake = FakeCompanion(
        Path(__file__).parent / "fakes" / "fake_companion.mjs",
        tmp_path / "scenario.json",
        tmp_path / "fake-state.json",
    )
    fake.configure("succeed")
    monkeypatch.setenv("CC_TANDEM_COMPANION", str(fake.script))
    monkeypatch.setenv("CC_TANDEM_FAKE_SCENARIO_FILE", str(fake.scenario_file))
    monkeypatch.setenv("CC_TANDEM_FAKE_STATE", str(fake.state_file))
    monkeypatch.delenv("CC_TANDEM_FAKE_SCENARIO", raising=False)
    monkeypatch.delenv("CC_TANDEM_DRY_RUN", raising=False)
    return fake


@pytest.fixture
def fake_companion(
    offline_companion: FakeCompanion, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> FakeCompanion:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    return offline_companion
