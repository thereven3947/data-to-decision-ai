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

demo:
	uv run d2d simulate
