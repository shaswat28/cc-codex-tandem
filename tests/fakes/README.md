# Offline companion

All tests default `CC_TANDEM_COMPANION` to `fake_companion.mjs`. Tests that need
real process execution request the `fake_companion` fixture, which also changes
into an isolated workspace. Node is required; no installation, login, network
access or subprocess launched by the fake is involved.

`fake_companion.configure(...)` writes JSON configuration and resets recorded
attempts and jobs. Pass a scenario name or an object:

```python
fake_companion.configure({
    "scenario": "succeed",
    "files": {"src/example.txt": "replacement content\n"},
})
```

Scenarios: `succeed`, `fail`, `usage_limit`, `hang`, `exit_nonzero`,
`invalid_json`, `no_change`. Only `succeed` applies file edits. Paths are relative
to the working directory (or `--cwd`); absolute paths, traversal and symlink
escapes are rejected before any file is edited.

For retry tests, use `{"attempts": ["usage_limit", "fail", "succeed"]}`.
Each `task` consumes one entry; the final entry repeats when exhausted. Entries
may also be configuration objects, allowing different edits on each attempt.
Status queries do not consume attempts. `status` can be supplied as an object
to override aggregate status, including under-reported running jobs.

Outside pytest, set `CC_TANDEM_FAKE_STATE` to a test-owned state file and supply
configuration through either `CC_TANDEM_FAKE_SCENARIO_FILE` (a JSON file) or
`CC_TANDEM_FAKE_SCENARIO` (inline JSON). The file takes precedence. The default
scenario is `succeed`.

`task` accepts the companion's task flags, including `--write`, `--background`,
`--model`, `--effort`, resume flags and `--cwd`. Background tasks return a queued
job response; the scenario resolves immediately to its stored terminal status.
A background `hang` remains running until `cancel`; it needs no worker process.
Foreground `hang` stays alive until terminated, so tests must bound it and clean
up the process. `status --all --json`, per-job `status`, `result` and `cancel`
read recorded jobs. This double does not implement the companion's review,
authentication or server lifecycle.

The Python classifier requires ticket and gate evidence from the caller. A zero
exit from `invalid_json` or `no_change` alone is `stalled`; a parsed ticket status
of `DONE` without a failing gate establishes completion. `status_all()` rejects
invalid JSON.
