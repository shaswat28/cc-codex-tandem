"""Configuration defaults and actionable validation errors."""

import re
from pathlib import Path

import pytest

from cc_tandem.config import ConfigError, ConfigNotFoundError, _table, load
from cc_tandem.effort import Effort


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".tandem.toml"
    path.write_text(text)
    return path


def test_defaults(tmp_path: Path) -> None:
    config = load(write_config(tmp_path, '[[queue]]\nticket = "T-01"'), for_run=True)
    assert config.codex.model == "gpt-6.1-sol"
    assert config.codex.effort_default == Effort.MEDIUM
    assert config.lanes.count == 2
    assert config.lanes.isolation == "worktree"
    assert config.lanes.base == "main"
    assert config.prompt.read_first == ("AGENTS.md", "CLAUDE.md")
    assert config.queue[0].ticket == "T-01"
    assert config.queue[0].effort == Effort.MEDIUM


def test_overrides(tmp_path: Path) -> None:
    config = load(
        write_config(
            tmp_path,
            """
[codex]
model = "custom"
effort_default = "low"
[lanes]
count = 3
isolation = "none"
base = "develop"
[prompt]
read_first = ["GUIDE.md"]
[[queue]]
ticket = "T-01"
[[queue]]
ticket = "T-02"
effort = "high"
""",
        ),
        for_run=True,
    )
    assert config.codex.model == "custom"
    assert config.codex.effort_default == Effort.LOW
    assert config.lanes.count == 3
    assert config.lanes.isolation == "none"
    assert config.lanes.base == "develop"
    assert config.prompt.read_first == ("GUIDE.md",)
    assert [entry.effort for entry in config.queue] == [Effort.LOW, Effort.HIGH]


@pytest.mark.parametrize(
    "text, message",
    [
        ("[lanes]\ncount = 0", "lanes.count must be an integer >= 1"),
        ("[lanes]\ncount = -1", "lanes.count"),
        ("[lanes]\ncount = true", "lanes.count"),
        ("[lanes]\ncount = 1.5", "lanes.count"),
        ('[lanes]\nisolation = "shared"', "lanes.isolation must be worktree or none"),
        ('[[queue]]\nticket = "T-01"\neffort = "xhigh"', "costs far more quota for little gain"),
        ('[[queue]]\nticket = "T-01"\neffort = "invalid"', "queue\\[0\\].effort"),
        ('[codex]\neffort_default = "xhigh"', "costs far more quota for little gain"),
        ("unexpected = 1", "Unknown top-level configuration key: unexpected"),
        ("[broken", "Cannot read configuration"),
        ("codex = 1", "codex must be a table"),
        ("lanes = []", "lanes must be a table"),
        ('prompt = "text"', "prompt must be a table"),
        ("[codex]\nmodel = 2", "codex.model must be a non-empty string"),
        ('[lanes]\nbase = ""', "lanes.base must be a non-empty string"),
        ('[prompt]\nread_first = "AGENTS.md"', "prompt.read_first must be an array"),
        ("[prompt]\nread_first = [1]", "prompt.read_first entry must be a non-empty string"),
        ("queue = {}", "queue must be an array of tables"),
        ("queue = [1]", "queue\\[0\\] must be a table"),
        ('[[queue]]\neffort = "low"', "queue\\[0\\].ticket must be a non-empty string"),
        (
            '[[queue]]\nticket = "T-01"\neffort = 1',
            "queue\\[0\\].effort must be a non-empty string",
        ),
    ],
)
def test_invalid(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load(write_config(tmp_path, text))


@pytest.mark.parametrize("text", ["", "queue = []"])
def test_empty_queue(tmp_path: Path, text: str) -> None:
    path = write_config(tmp_path, text)
    assert load(path).queue == ()
    with pytest.raises(ConfigError, match="queue must be non-empty for run"):
        load(path, for_run=True)


def test_missing_file(tmp_path: Path) -> None:
    path = tmp_path / ".tandem.toml"
    with pytest.raises(ConfigNotFoundError) as error:
        load(path)
    assert str(path) in str(error.value)
    assert 'Create it with:\n[[queue]]\nticket = "T-01"\neffort = "medium"' in str(error.value)


def test_unreadable_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Cannot read configuration"):
        load(tmp_path)


def test_empty_read_first_and_minimum_count(tmp_path: Path) -> None:
    config = load(write_config(tmp_path, "[lanes]\ncount = 1\n[prompt]\nread_first = []"))
    assert config.lanes.count == 1
    assert config.prompt.read_first == ()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('[codex]\neffort_defualt = "high"\n', "Unknown key codex.effort_defualt"),
        ("[lanes]\ncont = 3\n", "Unknown key lanes.cont"),
        ('[prompt]\nread_frist = ["A.md"]\n', "Unknown key prompt.read_frist"),
        ('[[queue]]\nticket = "T-01"\neffrot = "low"\n', "Unknown key queue[0].effrot"),
    ],
)
def test_misspelled_nested_key_is_rejected(tmp_path: Path, body: str, expected: str) -> None:
    """A typo must fail loudly rather than silently fall back to the default."""
    path = tmp_path / ".tandem.toml"
    path.write_text(body)
    with pytest.raises(ConfigError, match=re.escape(expected)):
        load(path)


def test_table_without_an_allowed_set_accepts_any_key(tmp_path: Path) -> None:
    """The top-level table is checked separately, so _table skips its own check."""
    path = tmp_path / ".tandem.toml"
    path.write_text('[[queue]]\nticket = "T-01"\neffort = "low"\n')
    assert load(path).queue[0].ticket == "T-01"
    assert _table({"anything": 1}, "free") == {"anything": 1}
