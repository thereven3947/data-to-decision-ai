"""Export the static demo data for the GitHub Pages build (no server, read-only, toy data).

    uv run python scripts/export_demo.py --data-dir data --out web/public/demo
    uv run python scripts/export_demo.py --data-dir data --record-copilot   # needs local Ollama

Writes one JSON file per role with every GET response the UI needs, captured through the real API
(login off, LLM off). ``--record-copilot`` asks the real Copilot the suggested questions and saves
the answers to ``web/demo/copilot.json`` (committed), which the demo replays.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from d2d.agent.provider import OllamaProvider, load_llm
from d2d.api.main import build_context, create_app
from d2d.api.scope import ROLES
from d2d.db import database_url

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = [
    "Brief me on what happened this week",
    "Why did the dishwasher start running at 2 a.m.?",
    "What should I do about the washer?",
    "What's been going on with the garage door?",
    "Which devices had data problems?",
]


def _client(data_dir: Path, provider: Any = None) -> TestClient:
    engine = create_engine(database_url())
    ctx = build_context(engine, ROOT / "config", data_dir, ROOT / "agent" / "skills", provider)
    return TestClient(create_app(ctx, web_dist=Path("/nonexistent"), allowed_hosts=["testserver"]))


def snapshot_role(client: TestClient, role: str) -> dict[str, Any]:
    h = {"X-D2D-Role": role}

    def get(path: str) -> Any:
        r = client.get(f"/api{path}", headers=h)
        r.raise_for_status()
        return r.json()

    pages: dict[str, Any] = {}
    for path in (
        "/meta",
        "/stories",
        "/events",
        "/changelog",
        "/devices",
        "/review",
        "/cases",
        "/kpis",
    ):
        pages[path] = get(path)
    for s in pages["/stories"]:
        pages[f"/stories/{s['story_id']}"] = get(f"/stories/{s['story_id']}")
    for d in pages["/devices"]:
        pages[f"/devices/{d['device_id']}"] = get(f"/devices/{d['device_id']}")
    if role == "owner":
        pages["/insights"] = get("/insights")
    return pages


def record_copilot(data_dir: Path) -> None:
    provider = OllamaProvider(load_llm(ROOT / "config" / "llm.toml"))
    client = _client(data_dir, provider)
    answers = {}
    for q in QUESTIONS:
        r = client.post("/api/chat", json={"question": q}, headers={"X-D2D-Role": "owner"})
        r.raise_for_status()
        body = r.json()
        keep = (
            "answer",
            "skill",
            "llm_model_tag",
            "tool_calls",
            "cited_ids",
            "checker",
            "latency_ms",
            "tokens_in",
            "tokens_out",
            "citations",
        )
        answers[q] = {k: body[k] for k in keep}
        print(f"recorded: {q} ({body['latency_ms']} ms, grounded={body['checker']['ok']})")
    out = ROOT / "web" / "demo" / "copilot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(answers, indent=2, sort_keys=True) + "\n", "utf-8")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", type=Path, default=ROOT / "data")
    p.add_argument("--out", type=Path, default=ROOT / "web" / "public" / "demo")
    p.add_argument("--record-copilot", action="store_true")
    args = p.parse_args()
    os.environ["D2D_LLM"] = "off"
    if args.record_copilot:
        record_copilot(args.data_dir)
        return
    client = _client(args.data_dir)
    args.out.mkdir(parents=True, exist_ok=True)
    for role in ROLES:
        pages = snapshot_role(client, role)
        (args.out / f"{role}.json").write_text(json.dumps(pages, default=str) + "\n", "utf-8")
        print(f"{role}: {len(pages)} responses")
    recorded = ROOT / "web" / "demo" / "copilot.json"
    if recorded.is_file():
        shutil.copy(recorded, args.out / "copilot.json")


if __name__ == "__main__":
    main()
