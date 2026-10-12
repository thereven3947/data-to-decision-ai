# Copilot instructions (D2D)

- Read the issue in `planning/backlog.toml` and only the docs it links to. Do not redesign.
- Python 3.12, `uv`. Code in `src/d2d/`, tests in `tests/`. Config in `config/*.toml` (no hard-coded topology).
- Deterministic first: pipeline/ML/enrichment must be testable without an LLM. Seeded randomness only.
- The LLM never assigns labels (see `docs/agent-and-skills.md`).
- Before finishing: `uv run ruff format . ; uv run ruff check . ; uv run mypy ; uv run pytest ; uv run python scripts/check_docs.py`.
- Keep sessions small: one issue per session, short progress updates, stop and report after two failed attempts.
