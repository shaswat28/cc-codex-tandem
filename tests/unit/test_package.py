"""Smoke tests so the scaffold is green before any feature lands."""

import json
from pathlib import Path

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
    for skill in (REPO_ROOT / "skills").iterdir():
        text = (skill / "SKILL.md").read_text()
        assert text.startswith("---\n"), skill.name
        front_matter = text.split("---\n")[1]
        assert "name:" in front_matter, skill.name
        assert "description:" in front_matter, skill.name
