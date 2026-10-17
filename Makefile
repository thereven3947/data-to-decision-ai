.PHONY: install lint type test check demo serve web

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
	uv run d2d eval linking --data-dir data/check
	uv run d2d review export --no-db --data-dir data/check
	uv run d2d train --training-set data/check/labels/training_set.jsonl --models-dir data/check/models
	uv run d2d eval classifier --models-dir data/check/models
	cd web && npm ci --no-audit --no-fund && npm test && npm run lint && npm run build

demo:
	uv run d2d simulate
	uv run d2d run
	uv run d2d eval changelog
	uv run d2d eval linking

serve:
	uv run d2d serve --data-dir data

web:
	cd web && npm run dev
