"""Deterministic evals that score pipeline output against scenario ground truth."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

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
