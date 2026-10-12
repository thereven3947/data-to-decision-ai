"""Fail if a relative Markdown link points at a missing file, or the backlog TOML is invalid."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def main() -> int:
    errors: list[str] = []
    for md in ROOT.glob("**/*.md"):
        if any(part in {".venv", "node_modules", ".git"} for part in md.parts):
            continue
        for target in LINK.findall(md.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("mailto:"):
                continue
            if not (md.parent / target).exists():
                errors.append(f"{md.relative_to(ROOT)}: broken link -> {target}")
    backlog = tomllib.loads((ROOT / "planning" / "backlog.toml").read_text(encoding="utf-8"))
    ids = [i["id"] for i in backlog.get("issue", [])]
    if len(ids) != len(set(ids)):
        errors.append("planning/backlog.toml: duplicate issue ids")
    milestones = {m["id"] for m in backlog.get("milestone", [])}
    for issue in backlog.get("issue", []):
        if issue["milestone"] not in milestones:
            errors.append(f"backlog: {issue['id']} has unknown milestone {issue['milestone']}")
        for dep in issue.get("depends_on", []):
            if dep not in ids:
                errors.append(f"backlog: {issue['id']} depends on unknown issue {dep}")
    for e in errors:
        print(e)
    print("docs ok" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
