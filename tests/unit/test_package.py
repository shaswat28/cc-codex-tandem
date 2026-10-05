"""Package metadata, skill front matter and real plugin validation."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

import cc_tandem

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_version_is_exposed() -> None:
    assert cc_tandem.__version__ == "0.1.0"


def test_plugin_manifest_matches_marketplace() -> None:
    plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
    marketplace = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    listed = {entry["name"]: entry for entry in marketplace["plugins"]}
    assert plugin["name"] in listed
    assert listed[plugin["name"]]["version"] == plugin["version"]


def test_every_skill_has_front_matter() -> None:
    skills = sorted((REPO_ROOT / "skills").iterdir())
    assert {skill.name for skill in skills} == {"ticket", "tandem", "verify-codex"}
    for skill in skills:
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        lines = text.splitlines()
        assert lines[0] == "---", skill.name
        end = lines.index("---", 1)
        front_matter = yaml.safe_load("\n".join(lines[1:end]))
        assert isinstance(front_matter, dict), skill.name
        assert front_matter.get("name") == skill.name, skill.name
        description = front_matter.get("description")
        assert isinstance(description, str) and description.strip(), skill.name
        assert "\n".join(lines[end + 1 :]).strip(), skill.name


@pytest.mark.parametrize("target", [".", ".claude-plugin/plugin.json"])
def test_plugin_validates_with_claude_cli(target: str) -> None:
    """Skipped without the CLI so the suite runs anywhere; CI installs it."""
    executable = shutil.which("claude")
    if executable is None:
        pytest.skip("claude CLI not installed; CI runs this check")
    result = subprocess.run(
        [executable, "plugin", "validate", target],
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
