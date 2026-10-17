"""Review queue, labels and cases in Postgres (``hitl.*``). Every write is one transaction."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, Engine, func, select, update
from sqlalchemy.dialects.postgresql import insert

from d2d.config import Taxonomy
from d2d.db import case_story, change_case, change_story, label, review_item


class ReviewError(ValueError):
    """Raised for an unknown story/case or an invalid transition."""


def _require_story(conn: Connection, story_id: str) -> None:
    found = conn.execute(
        select(change_story.c.story_id).where(change_story.c.story_id == story_id)
    ).first()
    if found is None:
        raise ReviewError(f"unknown story {story_id!r}")


def queue_for_review(
    conn: Connection,
    story_id: str,
    reason: str,
    suggested_category: str | None = None,
    suggested_confidence: float | None = None,
) -> None:
    values = {
        "story_id": story_id,
        "reason": reason,
        "suggested_category": suggested_category,
        "suggested_confidence": suggested_confidence,
    }
    stmt = insert(review_item).values(values)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["story_id"],
            set_={
                **{k: stmt.excluded[k] for k in values if k != "story_id"},
                "queued_at": func.now(),
                "resolved_at": None,
                "resolved_by": None,
            },
        )
    )
    conn.execute(
        update(change_story)
        .where(change_story.c.story_id == story_id)
        .values(status="needs_review")
    )


def record_label(
    conn: Connection,
    story_id: str,
    category: str,
    source: str,
    *,
    reviewer: str | None = None,
    note: str | None = None,
    confidence: float | None = None,
    model_version: str | None = None,
    status: str = "published",
) -> int:
    """Append a label, make it the story's current label and resolve its review item."""
    _require_story(conn, story_id)
    label_id: int = conn.execute(
        insert(label)
        .values(
            story_id=story_id,
            category=category,
            source=source,
            confidence=confidence,
            reviewer=reviewer,
            note=note,
            model_version=model_version,
        )
        .returning(label.c.label_id)
    ).scalar_one()
    conn.execute(
        update(change_story)
        .where(change_story.c.story_id == story_id)
        .values(
            category=category,
            confidence=confidence,
            label_source=source,
            model_version=model_version,
            status=status,
        )
    )
    if source != "model":
        conn.execute(
            update(review_item)
            .where(review_item.c.story_id == story_id)
            .where(review_item.c.resolved_at.is_(None))
            .values(resolved_at=func.now(), resolved_by=reviewer or source)
        )
    return label_id


def human_label(
    engine: Engine,
    taxonomy: Taxonomy,
    story_id: str,
    category: str,
    reviewer: str,
    note: str | None = None,
) -> int:
    taxonomy.require(category)
    with engine.begin() as conn:
        return record_label(conn, story_id, category, "human", reviewer=reviewer, note=note)


def open_queue(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        select(
            review_item.c.story_id,
            review_item.c.reason,
            review_item.c.suggested_category,
            review_item.c.suggested_confidence,
            review_item.c.queued_at,
            change_story.c.window_start,
            change_story.c.device_ids,
            change_story.c.event_count,
            change_story.c.category,
        )
        .join(change_story, change_story.c.story_id == review_item.c.story_id)
        .where(review_item.c.resolved_at.is_(None))
        .order_by(review_item.c.queued_at, change_story.c.window_start)
    )
    return [dict(r._mapping) for r in rows]


def simulate_reviews(engine: Engine, labels: dict[str, str], taxonomy: Taxonomy) -> int:
    """Resolve every open review item with the simulated reviewer's label (demo / bootstrap)."""
    done = 0
    with engine.begin() as conn:
        for item in open_queue(conn):
            category = labels.get(item["story_id"])
            if category is None:
                continue
            taxonomy.require(category)
            record_label(conn, item["story_id"], category, "sim_reviewer", reviewer="sim_reviewer")
            done += 1
    return done


def create_case(
    engine: Engine, title: str, created_by: str, story_ids: list[str], note: str | None = None
) -> int:
    with engine.begin() as conn:
        for sid in story_ids:
            _require_story(conn, sid)
        case_id: int = conn.execute(
            insert(change_case)
            .values(title=title, created_by=created_by, note=note)
            .returning(change_case.c.case_id)
        ).scalar_one()
        if story_ids:
            conn.execute(
                insert(case_story), [{"case_id": case_id, "story_id": s} for s in story_ids]
            )
    return case_id


def add_to_case(engine: Engine, case_id: int, story_id: str) -> None:
    with engine.begin() as conn:
        _require_open_case(conn, case_id)
        _require_story(conn, story_id)
        conn.execute(
            insert(case_story).values(case_id=case_id, story_id=story_id).on_conflict_do_nothing()
        )


def close_case(engine: Engine, case_id: int, note: str | None = None) -> None:
    with engine.begin() as conn:
        _require_open_case(conn, case_id)
        values: dict[str, Any] = {"status": "closed", "closed_at": func.now()}
        if note:
            values["note"] = note
        conn.execute(update(change_case).where(change_case.c.case_id == case_id).values(values))


def _require_open_case(conn: Connection, case_id: int) -> None:
    status = conn.execute(
        select(change_case.c.status).where(change_case.c.case_id == case_id)
    ).scalar_one_or_none()
    if status is None:
        raise ReviewError(f"unknown case {case_id}")
    if status != "open":
        raise ReviewError(f"case {case_id} is {status}")
