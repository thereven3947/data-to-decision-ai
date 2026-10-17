"""Regenerate the frozen golden stories (evals/golden/stories.jsonl) from the default config.

Golden stories are the regression set for the classifier and enrichment gates. Only rerun this on
purpose (new scenarios or a linking change) and review the diff in the PR.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from typer.testing import CliRunner

from d2d.cli import app
from d2d.config import load_household
from d2d.hitl.training_set import build_training_set

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        runner = CliRunner()
        for cmd in ("simulate", "run"):
            result = runner.invoke(app, [cmd, "--data-dir", tmp, "--config", str(ROOT / "config")])
            if result.exit_code:
                print(result.output)
                return 1
        rows = build_training_set(data, load_household(ROOT / "config" / "household.toml"))
    out = ROOT / "evals" / "golden" / "stories.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps({**r, "labeled_at": None}, sort_keys=True) + "\n")
    print(f"{len(rows)} golden stories -> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
