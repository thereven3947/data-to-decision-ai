.PHONY: install lint type test check demo

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

type:
	uv run mypy

test:
	uv run pytest

check: lint type test
	uv run python scripts/check_docs.py
	uv run d2d simulate --data-dir data/check
	uv run d2d run --data-dir data/check
	uv run d2d eval changelog --data-dir data/check

demo:
	uv run d2d simulate
	uv run d2d run
	uv run d2d eval changelog
