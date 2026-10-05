"""Typed loading and validation of repository configuration."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from cc_tandem.effort import Effort

MINIMAL_EXAMPLE = '[[queue]]\nticket = "T-01"\neffort = "medium"'


class ConfigError(ValueError):
    """Configuration could not be read or validated."""


class ConfigNotFoundError(ConfigError):
    """The requested configuration file does not exist."""


@dataclass(frozen=True)
class CodexConfig:
    model: str = "gpt-6.1-sol"
    effort_default: Effort = Effort.MEDIUM


@dataclass(frozen=True)
class LanesConfig:
    count: int = 2
    isolation: Literal["worktree", "none"] = "worktree"
    base: str = "main"


@dataclass(frozen=True)
class PromptConfig:
    read_first: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md")


@dataclass(frozen=True)
class QueueEntry:
    ticket: str
    effort: Effort


@dataclass(frozen=True)
class Config:
    codex: CodexConfig
    lanes: LanesConfig
    prompt: PromptConfig
    queue: tuple[QueueEntry, ...]


def _table(value: object, name: str, allowed: set[str] | None = None) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a table")
    table = cast(dict[str, object], value)
    if allowed is not None:
        for key in table:
            if key not in allowed:
                raise ConfigError(f"Unknown key {name}.{key}")
    return table


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")
    return value


def _effort(value: object, name: str) -> Effort:
    text = _string(value, name)
    try:
        return Effort.parse(text)
    except ValueError as exc:
        raise ConfigError(f"{name}: {exc}") from exc


def load(path: Path = Path(".tandem.toml"), *, for_run: bool = False) -> Config:
    """Load configuration; require queued tickets when preparing a run.

    Queue entries without an effort inherit codex.effort_default.
    """
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except FileNotFoundError as exc:
        raise ConfigNotFoundError(
            f"Configuration file {path} is missing. Create it with:\n{MINIMAL_EXAMPLE}"
        ) from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Cannot read configuration {path}: {exc}") from exc

    for key in data:
        if key not in {"codex", "lanes", "prompt", "queue"}:
            raise ConfigError(f"Unknown top-level configuration key: {key}")
    codex = _table(data.get("codex", {}), "codex", {"model", "effort_default"})
    lanes = _table(data.get("lanes", {}), "lanes", {"count", "isolation", "base"})
    prompt = _table(data.get("prompt", {}), "prompt", {"read_first"})
    codex_config = CodexConfig(
        model=_string(codex.get("model", "gpt-6.1-sol"), "codex.model"),
        effort_default=_effort(codex.get("effort_default", "medium"), "codex.effort_default"),
    )
    count = lanes.get("count", 2)
    if type(count) is not int or count < 1:
        raise ConfigError("lanes.count must be an integer >= 1")
    isolation = lanes.get("isolation", "worktree")
    if isolation not in ("worktree", "none"):
        raise ConfigError("lanes.isolation must be worktree or none")
    lanes_config = LanesConfig(
        count=count,
        isolation=isolation,
        base=_string(lanes.get("base", "main"), "lanes.base"),
    )
    read_first = prompt.get("read_first", ["AGENTS.md", "CLAUDE.md"])
    if not isinstance(read_first, list):
        raise ConfigError("prompt.read_first must be an array of strings")
    prompt_config = PromptConfig(
        tuple(_string(item, "prompt.read_first entry") for item in read_first)
    )
    queue = data.get("queue", [])
    if not isinstance(queue, list):
        raise ConfigError("queue must be an array of tables")
    entries: list[QueueEntry] = []
    for index, value in enumerate(queue):
        name = f"queue[{index}]"
        entry = _table(value, name, {"ticket", "effort"})
        entries.append(
            QueueEntry(
                ticket=_string(entry.get("ticket"), f"{name}.ticket"),
                effort=_effort(entry.get("effort", codex_config.effort_default), f"{name}.effort"),
            )
        )
    if for_run and not entries:
        raise ConfigError("queue must be non-empty for run; add a [[queue]] ticket")
    return Config(codex_config, lanes_config, prompt_config, tuple(entries))
