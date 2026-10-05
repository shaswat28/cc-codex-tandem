"""Enforce separate statement and branch floors on every production module."""

import argparse
import json
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import cast


def table(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Expected a coverage object")
    return cast(dict[str, object], value)


def percentage(summary: dict[str, object], covered: str, total: str) -> float:
    hit, count = summary[covered], summary[total]
    if type(hit) is not int or type(count) is not int or not 0 <= hit <= count:
        raise ValueError(f"Invalid coverage counts: {covered}, {total}")
    return 100.0 * hit / count if count else 100.0


def floor(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 100:
        raise ValueError("Coverage floors must be between 0 and 100")
    return float(value)


def violations(report: object, settings: object, root: Path) -> list[str]:
    """Fail closed on missing modules or statement-only coverage data."""
    data, config = table(report), table(settings)
    if table(data["meta"]).get("branch_coverage") is not True:
        raise ValueError("Branch coverage data is required")
    minimum = floor(config["minimum"])
    overrides = table(config.get("overrides", {}))
    thresholds = {name: floor(value) for name, value in overrides.items()}
    files = table(data["files"])
    # Resolve against the project, not the caller's current directory.
    measured = {(root / name).resolve(): value for name, value in files.items()}
    modules = sorted((root / "src" / "cc_tandem").rglob("*.py"))
    if not modules:
        raise ValueError("No production modules found")
    failures: list[str] = []
    for module in modules:
        name = module.relative_to(root).as_posix()
        if module.resolve() not in measured:
            failures.append(f"{name}: missing coverage data")
            continue
        summary = table(table(measured[module.resolve()])["summary"])
        threshold = thresholds.get(name, minimum)
        for label, covered, total in (
            ("statement", "covered_lines", "num_statements"),
            ("branch", "covered_branches", "num_branches"),
        ):
            actual = percentage(summary, covered, total)
            if actual < threshold:
                failures.append(f"{name}: {label} coverage {actual:.2f}% < {threshold:g}%")
    unknown = thresholds.keys() - {module.relative_to(root).as_posix() for module in modules}
    if unknown:
        raise ValueError(f"Coverage overrides name unknown modules: {', '.join(sorted(unknown))}")
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("coverage.json"))
    parser.add_argument("--config", type=Path, default=Path("pyproject.toml"))
    args = parser.parse_args(argv)
    try:
        with args.config.open("rb") as stream:
            config = tomllib.load(stream)
        report: object = json.loads(args.report.read_text(encoding="utf-8"))
        failures = violations(
            report, config["tool"]["tandem"]["coverage"], args.config.resolve().parent
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Cannot check module coverage: {exc}", file=sys.stderr)
        return 1
    if failures:
        print("Module coverage failed:\n" + "\n".join(failures), file=sys.stderr)
        return 1
    print("Statement and branch coverage floors passed for every production module.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
