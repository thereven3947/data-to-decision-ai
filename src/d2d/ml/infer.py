"""Classify new stories: confident -> published (model label); otherwise -> review queue."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import Engine, select

from d2d.config import Taxonomy
from d2d.db import change_story
from d2d.hitl.review import queue_for_review, record_label
from d2d.hitl.training_set import examples_from_db
from d2d.ml.train import Classifier


@dataclass(frozen=True)
class ClassifySummary:
    classified: int
    published: int
    audit: int
    abstained: int
    model_version: str


def in_audit_sample(story_id: str, rate: float) -> bool:
    return int(hashlib.sha1(story_id.encode()).hexdigest()[:8], 16) % 10_000 < rate * 10_000


def classify_new(engine: Engine, classifier: Classifier, taxonomy: Taxonomy) -> ClassifySummary:
    """Label every story still in status ``new``. Stories a human touched are never relabeled."""
    with engine.connect() as conn:
        ids = list(
            conn.execute(
                select(change_story.c.story_id).where(change_story.c.status == "new")
            ).scalars()
        )
    examples = examples_from_db(engine, ids)
    ordered = [examples[i] for i in ids if i in examples]
    predictions = classifier.predict(ordered)
    version = classifier.meta.model_version
    published = audit = abstained = 0
    with engine.begin() as conn:
        for ex, p in zip(ordered, predictions, strict=True):
            sid = ex["story_id"]
            if p.abstain or p.category not in taxonomy.categories:
                queue_for_review(conn, sid, "abstain", p.category, p.confidence)
                abstained += 1
                continue
            audited = in_audit_sample(sid, taxonomy.audit_rate)
            record_label(
                conn,
                sid,
                p.category,
                "model",
                confidence=p.confidence,
                model_version=version,
                status="needs_review" if audited else "published",
            )
            if audited:
                queue_for_review(conn, sid, "audit", p.category, p.confidence)
                audit += 1
            else:
                published += 1
    return ClassifySummary(len(ordered), published, audit, abstained, version)
