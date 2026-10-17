"""Feedback routing (M11-01): every new feedback row goes to the layer that can fix it.

Routing follows docs/evaluation-and-calibration.md#feedback-routing:

- story ``wrong_category`` -> ``labels``: the story is queued for review again (stage 5)
- story ``wrong_grouping`` -> ``linking``: eval item, these events should not be one story
- story ``unclear_description`` -> ``enrichment``: eval item for the template
- ``wrong_data`` / ``missing_data`` -> ``data``: eval item; nightly calibration reports it
- Copilot answer thumbs-down -> ``conversations``: eval item for skill regression tests
- page comments and other thumbs-down -> ``ux`` (UX inbox); thumbs-up -> ``kpi`` (counted)

Eval items live in ``feedback.eval_item`` until the nightly calibration exports them into
``evals/feedback/*.jsonl`` through a human-reviewed pull request.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import Connection, Engine, func, select, update
from sqlalchemy.dialects.postgresql import insert

from d2d.db import change_event, change_story, change_story_event, chat_message, eval_item, feedback
from d2d.hitl.review import queue_for_review

DATA_REASONS = {"wrong_data", "missing_data"}


def _story(conn: Connection, story_id: str) -> dict[str, Any] | None:
    row = (
        conn.execute(select(change_story).where(change_story.c.story_id == story_id))
        .mappings()
        .first()
    )
    if row is None:
        return None
    events = conn.execute(
        select(
            change_event.c.event_id,
            change_event.c.device_id,
            change_event.c.property,
            change_event.c.old_value,
            change_event.c.new_value,
            change_event.c.semantic_tag,
            change_event.c.event_ts,
        )
        .join(change_story_event, change_story_event.c.event_id == change_event.c.event_id)
        .where(change_story_event.c.story_id == story_id)
        .order_by(change_event.c.event_ts)
    ).mappings()
    return {
        "story_id": story_id,
        "category": row["category"],
        "label_source": row["label_source"],
        "model_version": row["model_version"],
        "linking_config_sha": row["linking_config_sha"],
        "events": [{**dict(e), "event_ts": e["event_ts"].isoformat()} for e in events],
    }


def _message(conn: Connection, message_id: str) -> dict[str, Any] | None:
    if not message_id.isdigit():
        return None
    row = (
        conn.execute(select(chat_message).where(chat_message.c.message_id == int(message_id)))
        .mappings()
        .first()
    )
    if row is None:
        return None
    return {
        "message_id": row["message_id"],
        "question": row["question"],
        "answer": row["answer"],
        "skill": row["skill"],
        "skill_sha": row["skill_sha"],
        "llm_model_tag": row["llm_model_tag"],
        "checker": row["checker_result"],
        "cited_ids": list(row["cited_ids"]),
    }


def route_of(fb: dict[str, Any]) -> str:
    if fb["rating"] == "up":
        return "kpi"
    reason = fb.get("reason")
    if fb["target_type"] == "page":
        return "ux"
    if reason in DATA_REASONS:
        return "data"
    if fb["target_type"] == "story":
        return {
            "wrong_category": "labels",
            "wrong_grouping": "linking",
            "unclear_description": "enrichment",
        }.get(reason or "", "ux")
    return "conversations"


def route_feedback(engine: Engine) -> dict[str, int]:
    """Route all unrouted feedback (idempotent). Returns counts per route."""
    counts: Counter[str] = Counter()
    with engine.begin() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                select(feedback)
                .where(feedback.c.routed_at.is_(None))
                .order_by(feedback.c.feedback_id)
            ).mappings()
        ]
        for fb in rows:
            route = route_of(fb)
            context: dict[str, Any] | None = None
            if fb["target_type"] == "story":
                context = _story(conn, fb["target_id"])
            elif fb["target_type"] == "message":
                context = _message(conn, fb["target_id"])
            base = {
                "feedback_id": fb["feedback_id"],
                "reason": fb["reason"],
                "comment": fb["comment"],
                "page": fb["page"],
                "user_role": fb["user_role"],
                "created_at": fb["created_at"].isoformat(),
                "target": {"type": fb["target_type"], "id": fb["target_id"]},
            }
            if route == "labels" and context is not None:
                queue_for_review(conn, fb["target_id"], "feedback", context["category"], None)
            if route != "kpi":
                set_name = route if route != "labels" else "labels"
                conn.execute(
                    insert(eval_item).values(
                        set_name=set_name,
                        feedback_id=fb["feedback_id"],
                        payload={**base, "context": context},
                    )
                )
            conn.execute(
                update(feedback)
                .where(feedback.c.feedback_id == fb["feedback_id"])
                .values(routed_to=route, routed_at=func.now())
            )
            counts[route] += 1
    return dict(counts)
