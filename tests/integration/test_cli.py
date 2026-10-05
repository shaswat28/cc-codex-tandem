"""Exercise process detachment with a bounded offline runner double."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.integration
def test_detached_child_outlives_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".tandem.toml").write_text('[[queue]]\nticket="T-01"\n')
    hook = tmp_path / "hook"
    hook.mkdir()
    # Inject only the runner, leaving CLI argument handling, exec, inherited locks,
    # logs and detached session creation real. No git or companion is invoked.
    (hook / "sitecustomize.py").write_text("""
import json
import os
import time
from pathlib import Path
from cc_tandem.lanes import Runner

def init(self, repo, config, **kwargs):
    self.repo = repo

def run(self):
    root = self.repo
    (root / "started.json").write_text(json.dumps({"pid":os.getpid(), "session":os.getsid(0)}))
    print("worker started", flush=True)
    deadline = time.monotonic() + 10
    while not (root / "release").exists() and time.monotonic() < deadline:
        time.sleep(.02)
    (root / "finished").touch()
    return {}

Runner.__init__ = init
Runner.run = run
""")
    source = Path(__file__).resolve().parents[2] / "src"
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(hook), str(source)]))
    command = [sys.executable, "-m", "cc_tandem.cli", "run", "--repo", str(repo)]
    parent = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = parent.communicate(timeout=5)
        assert parent.returncode == 0, stderr
        assert "Logs:" in stdout
        deadline = time.monotonic() + 5
        while not (repo / "started.json").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        child = json.loads((repo / "started.json").read_text())
        assert child["pid"] != parent.pid
        assert child["session"] == child["pid"]
        os.kill(child["pid"], 0)
        # Parent has exited but its inherited launch guard still excludes a second run.
        second = subprocess.run(command, capture_output=True, text=True, timeout=5)
        assert second.returncode == 1
        assert "already active" in second.stderr
        log = Path(stdout.strip().split("Logs: ")[1])
        assert "worker started" in log.read_text()
    finally:
        (repo / "release").touch()
        deadline = time.monotonic() + 5
        while not (repo / "finished").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
    assert (repo / "finished").exists()
