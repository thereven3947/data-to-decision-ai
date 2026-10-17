"""Stage 6: labeled training set with provenance, versioned by a content hash.

An *example* is a story with the facts the classifier may use (events, device kinds, link reasons);
a labeled example adds ``category``, ``source``, ``reviewer`` and ``labeled_at``.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import distinct_on

from d2d.config import Household
from d2d.db import change_event, change_story, change_story_event, device, label
from d2d.hitl.sim_reviewer import SIM_REVIEWER, label_stories

Example = dict[str, Any]
EVENT_FIELDS = ("device_id", "property", "change_kind", "semantic_tag", "old_value", "new_value")


def _example(story_id: str, link_reasons: list[str], events: list[dict[str, Any]]) -> Example:
    events = sorted(events, key=lambda e: (e["event_ts"], e["device_id"], e["property"]))
    return {
        "story_id": story_id,
        "link_reasons": sorted(link_reasons),
        "events": [
            {**{k: e[k] for k in EVENT_FIELDS}, "device_kind": e["device_kind"]} for e in events
        ],
    }


def examples_from_run(data_dir: Path, household: Household) -> dict[str, Example]:
    """Every story of the latest ``d2d run`` as an unlabeled example."""
    kinds = {d.id: d.kind for d in household.devices.values()}
    events = {
        e["event_id"]: {**e, "device_kind": kinds.get(e["device_id"], "unknown")}
        for e in pq.read_table(data_dir / "events" / "events.parquet").to_pylist()
    }
    return {
        s["story_id"]: _example(
            s["story_id"], s["link_reasons"], [events[i] for i in s["event_ids"]]
        )
        for s in pq.read_table(data_dir / "stories" / "stories.parquet").to_pylist()
    }


def examples_from_db(engine: Engine, story_ids: list[str]) -> dict[str, Example]:
    if not story_ids:
        return {}
    with engine.connect() as conn:
        reasons = dict(
            conn.execute(
                select(change_story.c.story_id, change_story.c.link_reasons).where(
                    change_story.c.story_id.in_(story_ids)
                )
            ).all()
        )
        rows = conn.execute(
            select(
                change_story_event.c.story_id,
                *[change_event.c[k] for k in EVENT_FIELDS],
                change_event.c.event_ts,
                device.c.kind.label("device_kind"),
            )
            .join(change_event, change_event.c.event_id == change_story_event.c.event_id)
            .join(device, device.c.device_id == change_event.c.device_id)
            .where(change_story_event.c.story_id.in_(story_ids))
        ).mappings()
        by_story: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_story.setdefault(r["story_id"], []).append(dict(r))
    return {sid: _example(sid, list(reasons[sid]), evs) for sid, evs in by_story.items()}


def reviewed_labels(engine: Engine) -> dict[str, dict[str, Any]]:
    """Latest non-model label per story (human or simulated reviewer)."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                label.c.story_id,
                label.c.category,
                label.c.source,
                label.c.reviewer,
                label.c.created_at,
            )
            .where(label.c.source.in_(("human", SIM_REVIEWER)))
            .order_by(label.c.story_id, label.c.created_at.desc(), label.c.label_id.desc())
            .ext(distinct_on(label.c.story_id))
        ).mappings()
        return {r["story_id"]: dict(r) for r in rows}


@dataclass(frozen=True)
class TrainingSetManifest:
    version: str
    examples: int
    by_category: dict[str, int]
    by_source: dict[str, int]
    created_at: str


def build_training_set(
    data_dir: Path,
    household: Household,
    engine: Engine | None = None,
    include_ground_truth: bool = True,
) -> list[Example]:
    """Reviewed labels from Postgres, plus ground-truth labels for the remaining run stories."""
    rows: dict[str, Example] = {}
    if engine is not None:
        reviewed = reviewed_labels(engine)
        for sid, ex in examples_from_db(engine, list(reviewed)).items():
            r = reviewed[sid]
            rows[sid] = {
                **ex,
                "category": r["category"],
                "source": r["source"],
                "reviewer": r["reviewer"],
                "labeled_at": r["created_at"].astimezone(UTC).isoformat(),
            }
    if include_ground_truth:
        run = examples_from_run(data_dir, household)
        for sid, category in label_stories(data_dir).items():
            if sid not in rows and sid in run:
                rows[sid] = {
                    **run[sid],
                    "category": category,
                    "source": SIM_REVIEWER,
                    "reviewer": "ground_truth",
                    "labeled_at": None,
                }
    return [rows[k] for k in sorted(rows)]


def training_set_version(rows: list[Example]) -> str:
    stable = [{k: v for k, v in r.items() if k != "labeled_at"} for r in rows]
    return hashlib.sha1(json.dumps(stable, sort_keys=True).encode()).hexdigest()[:12]


def write_training_set(rows: list[Example], out_dir: Path) -> TrainingSetManifest:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "training_set.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    manifest = TrainingSetManifest(
        version=training_set_version(rows),
        examples=len(rows),
        by_category=dict(sorted(Counter(r["category"] for r in rows).items())),
        by_source=dict(sorted(Counter(r["source"] for r in rows).items())),
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest.__dict__, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def read_training_set(path: Path) -> list[Example]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
