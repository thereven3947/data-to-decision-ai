"""Create GitHub milestones and issues from planning/backlog.toml (idempotent).

Usage:
    python scripts/seed_github_backlog.py --repo <owner>/<repo> --dry-run
    GITHUB_TOKEN=... python scripts/seed_github_backlog.py --repo <owner>/<repo>

Issues are matched by their "[ID]" title prefix, so re-running only creates what is missing.
Issues with status = "done" are created closed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"


def _call(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> Any:
    req = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read() or b"null")


def _list(url: str, token: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = _call("GET", f"{url}{'&' if '?' in url else '?'}per_page=100&page={page}", token)
        items += batch
        if len(batch) < 100:
            return items
        page += 1


def issue_body(issue: dict[str, Any]) -> str:
    lines = [f"Size: **{issue.get('size', '?')}**"]
    if issue.get("depends_on"):
        lines.append("Depends on: " + ", ".join(issue["depends_on"]))
    if issue.get("acceptance"):
        lines += ["", "### Acceptance criteria", *[f"- [ ] {a}" for a in issue["acceptance"]]]
    lines += ["", "Source: `planning/backlog.toml`. Docs: `docs/feature-roadmap.md`."]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    backlog = tomllib.loads((ROOT / "planning" / "backlog.toml").read_text(encoding="utf-8"))
    token = os.environ.get("GITHUB_TOKEN", "")
    if not args.dry_run and not token:
        print("GITHUB_TOKEN is not set (use --dry-run to preview)")
        return 2
    base = f"{API}/repos/{args.repo}"

    existing_ms: dict[str, int] = {}
    existing_issues: set[str] = set()
    if not args.dry_run:
        existing_ms = {
            m["title"]: m["number"] for m in _list(f"{base}/milestones?state=all", token)
        }
        for i in _list(f"{base}/issues?state=all", token):
            if i["title"].startswith("[") and "]" in i["title"]:
                existing_issues.add(i["title"][1 : i["title"].index("]")])

    ms_number: dict[str, int] = {}
    for m in backlog["milestone"]:
        if m["title"] in existing_ms:
            ms_number[m["id"]] = existing_ms[m["title"]]
            continue
        print(f"create milestone  {m['title']}")
        if not args.dry_run:
            ms_number[m["id"]] = _call("POST", f"{base}/milestones", token, {"title": m["title"]})[
                "number"
            ]

    for issue in backlog["issue"]:
        if issue["id"] in existing_issues:
            continue
        done = issue.get("status") == "done"
        print(f"create issue      [{issue['id']}] {issue['title']}{'  (closed)' if done else ''}")
        if args.dry_run:
            continue
        created = _call(
            "POST",
            f"{base}/issues",
            token,
            {
                "title": f"[{issue['id']}] {issue['title']}",
                "body": issue_body(issue),
                "milestone": ms_number[issue["milestone"]],
                "labels": [f"size:{issue.get('size', '?')}", *issue.get("tags", [])],
            },
        )
        if done:
            _call("PATCH", f"{base}/issues/{created['number']}", token, {"state": "closed"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
