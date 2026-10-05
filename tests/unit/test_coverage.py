"""Per-module floors cannot be hidden by a healthy aggregate or rounded totals."""

import json
from pathlib import Path

import pytest
from scripts.check_coverage import main, percentage, violations


def report(root: Path, *, lines: int = 90, branches: int = 90) -> dict[str, object]:
    source = root / "src/cc_tandem"
    source.mkdir(parents=True)
    for name in ("lanes.py", "other.py", "__init__.py"):
        (source / name).touch()
    return {
        "meta": {"branch_coverage": True},
        "files": {
            f"src/cc_tandem/{name}": {
                "summary": {
                    "covered_lines": lines if name == "lanes.py" else 100,
                    "num_statements": 100,
                    "covered_branches": branches if name == "lanes.py" else 100,
                    "num_branches": 100,
                }
            }
            for name in ("lanes.py", "other.py")
        }
        | {
            "src/cc_tandem/__init__.py": {
                "summary": {
                    "covered_lines": 0,
                    "num_statements": 0,
                    "covered_branches": 0,
                    "num_branches": 0,
                }
            }
        },
    }


SETTINGS = {"minimum": 85, "overrides": {"src/cc_tandem/lanes.py": 90}}


@pytest.mark.parametrize(
    ("lines", "branches", "label"), [(89, 100, "statement"), (100, 89, "branch")]
)
def test_low_module_fails_despite_high_total(
    tmp_path: Path, lines: int, branches: int, label: str
) -> None:
    data = report(tmp_path, lines=lines, branches=branches)
    data["totals"] = {"percent_covered": 99}
    failures = violations(data, SETTINGS, tmp_path)
    assert failures == [f"src/cc_tandem/lanes.py: {label} coverage 89.00% < 90%"]


def test_default_floor_and_boundary(tmp_path: Path) -> None:
    data = report(tmp_path, lines=85, branches=85)
    assert violations(data, {"minimum": 85}, tmp_path) == []
    assert len(violations(data, SETTINGS, tmp_path)) == 2
    assert violations(data, {"minimum": 86}, tmp_path)


def test_missing_file_and_branch_data_fail_closed(tmp_path: Path) -> None:
    data = report(tmp_path)
    data["files"] = {}
    assert len(violations(data, SETTINGS, tmp_path)) == 3
    data["meta"] = {"branch_coverage": False}
    with pytest.raises(ValueError, match="Branch coverage"):
        violations(data, SETTINGS, tmp_path)


@pytest.mark.parametrize("minimum", [-1, 101, True, "85", float("nan")])
def test_invalid_floor(tmp_path: Path, minimum: object) -> None:
    with pytest.raises(ValueError, match="floors"):
        violations(report(tmp_path), {"minimum": minimum}, tmp_path)


def test_unknown_override(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown modules"):
        violations(report(tmp_path), {"minimum": 85, "overrides": {"typo.py": 90}}, tmp_path)


def test_no_modules(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No production"):
        violations({"meta": {"branch_coverage": True}, "files": {}}, {"minimum": 85}, tmp_path)


@pytest.mark.parametrize(("covered", "total"), [(-1, 10), (11, 10), (True, 10), (1, "10")])
def test_invalid_counts(covered: object, total: object) -> None:
    with pytest.raises(ValueError, match="Invalid coverage counts"):
        percentage({"hit": covered, "count": total}, "hit", "count")


def test_rounding_does_not_hide_a_failure(tmp_path: Path) -> None:
    data = report(tmp_path)
    files = data["files"]
    assert isinstance(files, dict)
    files["src/cc_tandem/lanes.py"] = {
        "summary": {
            "covered_lines": 84999,
            "num_statements": 100000,
            "covered_branches": 0,
            "num_branches": 0,
        }
    }
    assert violations(data, {"minimum": 85}, tmp_path) == [
        "src/cc_tandem/lanes.py: statement coverage 85.00% < 85%"
    ]


def test_malformed_object() -> None:
    with pytest.raises(ValueError, match="object"):
        violations([], SETTINGS, Path())


@pytest.mark.parametrize("kind", ["pass", "low", "missing", "malformed", "statement_only"])
def test_cli_reports_failure_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    config = tmp_path / "pyproject.toml"
    config.write_text("[tool.tandem.coverage]\nminimum = 85\n")
    data = report(tmp_path, lines=10 if kind == "low" else 90)
    path = tmp_path / "coverage.json"
    if kind == "statement_only":
        data["meta"] = {"branch_coverage": False}
    if kind != "missing":
        path.write_text("invalid" if kind == "malformed" else json.dumps(data))
    assert main(["--report", str(path), "--config", str(config)]) == (0 if kind == "pass" else 1)
    output = capsys.readouterr()
    if kind == "low":
        assert "src/cc_tandem/lanes.py" in output.err and "10.00% < 85%" in output.err
    elif kind == "pass":
        assert "passed" in output.out
    else:
        assert "Cannot check" in output.err
