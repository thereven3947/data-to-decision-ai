"""Deterministic evals that score pipeline output against scenario ground truth."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

Key = tuple[str, str, str]


@dataclass(frozen=True)
class ChangelogEval:
    expected: int
    found: int
    matched: int
    dq_expected: int
    dq_matched: int
    missed: list[Key] = field(default_factory=list)
    unexpected: list[Key] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.matched / self.expected if self.expected else 1.0

    @property
    def precision(self) -> float:
        return self.matched / self.found if self.found else 1.0

    @property
    def dq_recall(self) -> float:
        return self.dq_matched / self.dq_expected if self.dq_expected else 1.0


def linking_truth(data_dir: Path) -> dict[Key, str]:
    truth: dict[Key, str] = {}
    for line in (data_dir / "ground_truth" / "scenarios.jsonl").read_text("utf-8").splitlines():
        s = json.loads(line)
        for e in s["events"]:
            truth[(e["device_id"], e["property"], e["ts"])] = s["scenario_id"]
    return truth


def evaluate_linking(data_dir: Path) -> dict[str, float]:
    """Pairwise precision/recall/F1 and Adjusted Rand Index over ground-truth events."""
    events = pq.read_table(data_dir / "events" / "events.parquet").to_pylist()
    stories = pq.read_table(data_dir / "stories" / "stories.parquet").to_pylist()
    return linking_metrics(linking_truth(data_dir), events, stories)


def linking_metrics(
    truth: dict[Key, str], events: list[dict[str, Any]], stories: list[dict[str, Any]]
) -> dict[str, float]:
    key_of = {
        e["event_id"]: (e["device_id"], e["property"], e["event_ts"].isoformat()) for e in events
    }
    pred: dict[Key, str] = {}
    for story in stories:
        for eid in story["event_ids"]:
            pred[key_of[eid]] = story["story_id"]
    keys = sorted(truth.keys() & pred.keys())
    tp = fp = fn = 0
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            same_t, same_p = truth[a] == truth[b], pred[a] == pred[b]
            tp += same_t and same_p
            fp += same_p and not same_t
            fn += same_t and not same_p
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "events": float(len(keys)),
        "stories": float(len({pred[k] for k in keys})),
        "scenarios": float(len({truth[k] for k in keys})),
        "pair_precision": precision,
        "pair_recall": recall,
        "pair_f1": f1,
        "ari": _ari([truth[k] for k in keys], [pred[k] for k in keys]),
    }


def _ari(a: list[str], b: list[str]) -> float:
    def c2(n: int) -> float:
        return n * (n - 1) / 2

    n = len(a)
    if n < 2:
        return 1.0
    sum_ij = sum(c2(v) for v in Counter(zip(a, b, strict=True)).values())
    sum_a = sum(c2(v) for v in Counter(a).values())
    sum_b = sum(c2(v) for v in Counter(b).values())
    expected = sum_a * sum_b / c2(n)
    max_index = (sum_a + sum_b) / 2
    return 1.0 if max_index == expected else (sum_ij - expected) / (max_index - expected)


def evaluate_changelog(data_dir: Path) -> ChangelogEval:
    gt_path = data_dir / "ground_truth" / "scenarios.jsonl"
    expected: set[Key] = set()
    for line in gt_path.read_text(encoding="utf-8").splitlines():
        for e in json.loads(line)["events"]:
            expected.add((e["device_id"], e["property"], e["ts"]))
    table = pq.read_table(data_dir / "events" / "events.parquet")
    found: set[Key] = {
        (r["device_id"], r["property"], r["event_ts"].isoformat()) for r in table.to_pylist()
    }
    dq = {k for k in expected if k[1].startswith("dq.")}
    return ChangelogEval(
        expected=len(expected),
        found=len(found),
        matched=len(expected & found),
        dq_expected=len(dq),
        dq_matched=len(dq & found),
        missed=sorted(expected - found),
        unexpected=sorted(found - expected),
    )
