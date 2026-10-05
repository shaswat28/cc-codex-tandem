.PHONY: check lint types test cov install-hooks

check: lint types cov

lint:
	uv run ruff check .
	uv run ruff format --check .

types:
	uv run mypy

test:
	uv run pytest -m "not live" --cov --cov-report=term-missing --cov-report=json

cov: test
	uv run python -m scripts.check_coverage

install-hooks:
	git config core.hooksPath .githooks
	@echo "git hooks enabled from .githooks/"
