"""Simulated reviewer: labels a story with the majority ground-truth category of its events."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

SIM_REVIEWER = "sim_reviewer"


def ground_truth_categories(data_dir: Path) -> dict[tuple[str, str, str], str]:
    path = data_dir / "ground_truth" / "scenarios.jsonl"
    truth: dict[tuple[str, str, str], str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = json.loads(line)
        for e in s["events"]:
            truth[(e["device_id"], e["property"], e["ts"])] = s["category"]
    return truth


def label_stories(data_dir: Path) -> dict[str, str]:
    """story_id -> category for every story with at least one ground-truth event."""
    truth = ground_truth_categories(data_dir)
    events = pq.read_table(data_dir / "events" / "events.parquet").to_pylist()
    key_of = {
        e["event_id"]: (e["device_id"], e["property"], e["event_ts"].isoformat()) for e in events
    }
    labels: dict[str, str] = {}
    for story in pq.read_table(data_dir / "stories" / "stories.parquet").to_pylist():
        votes = Counter(truth[k] for eid in story["event_ids"] if (k := key_of[eid]) in truth)
        if votes:
            best = max(votes.values())
            labels[story["story_id"]] = min(c for c, n in votes.items() if n == best)
    return labels
