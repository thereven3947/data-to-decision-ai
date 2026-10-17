"""Owner-only insights (M11-01 KPI dashboard): pipeline, review, Copilot, feedback, alerts."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import select

from d2d.api.repo import Ontology
from d2d.api.scope import Scope
from d2d.db import chat_message, eval_item, feedback, notification, review_item


def _pct(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]


def insights(onto: Ontology, owner: Scope, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    week = now - timedelta(days=7)
    with onto.engine.connect() as conn:
        fb = [
            dict(r)
            for r in conn.execute(
                select(feedback).order_by(feedback.c.feedback_id.desc())
            ).mappings()
        ]
        msgs = [
            dict(r)
            for r in conn.execute(
                select(
                    chat_message.c.message_id,
                    chat_message.c.skill,
                    chat_message.c.llm_model_tag,
                    chat_message.c.checker_result,
                    chat_message.c.latency_ms,
                    chat_message.c.created_at,
                )
            ).mappings()
        ]
        reviews = [dict(r) for r in conn.execute(select(review_item)).mappings()]
        notes = [
            dict(r)
            for r in conn.execute(
                select(notification).order_by(notification.c.created_at.desc())
            ).mappings()
        ]
        evals = [
            dict(r)
            for r in conn.execute(select(eval_item.c.set_name, eval_item.c.exported_at)).mappings()
        ]

    msg_ratings = Counter(f["rating"] for f in fb if f["target_type"] == "message")
    grounded = sum(1 for m in msgs if (m["checker_result"] or {}).get("ok"))
    resolved = [r for r in reviews if r["resolved_at"] is not None]
    turnaround = [(r["resolved_at"] - r["queued_at"]).total_seconds() / 3600 for r in resolved]
    comments = [
        {
            "feedback_id": f["feedback_id"],
            "target_type": f["target_type"],
            "target_id": f["target_id"],
            "rating": f["rating"],
            "reason": f["reason"],
            "comment": f["comment"],
            "page": f["page"],
            "username": f["username"],
            "user_role": f["user_role"],
            "routed_to": f["routed_to"],
            "created_at": f["created_at"],
        }
        for f in fb
        if f["comment"] or f["rating"] == "down"
    ][:25]
    return {
        "generated_at": now,
        "pipeline": onto.kpis(owner),
        "review": {
            "open_by_reason": dict(
                Counter(r["reason"] for r in reviews if r["resolved_at"] is None)
            ),
            "resolved_last_7d": sum(1 for r in resolved if r["resolved_at"] >= week),
            "median_hours_to_review": round(median(turnaround), 2) if turnaround else None,
        },
        "copilot": {
            "answers": len(msgs),
            "answers_last_7d": sum(1 for m in msgs if m["created_at"] >= week),
            "grounded_rate": _pct(grounded, len(msgs)),
            "fallback_answers": sum(1 for m in msgs if "fallback" in (m["llm_model_tag"] or "")),
            "latency_ms_median": int(median([m["latency_ms"] for m in msgs])) if msgs else None,
            "latency_ms_p95": _p95([m["latency_ms"] for m in msgs]),
            "thumbs_up": msg_ratings["up"],
            "thumbs_down": msg_ratings["down"],
            "helpful_rate": _pct(msg_ratings["up"], msg_ratings["up"] + msg_ratings["down"]),
            "by_skill": dict(Counter(m["skill"] for m in msgs)),
        },
        "feedback": {
            "total": len(fb),
            "unrouted": sum(1 for f in fb if f["routed_at"] is None),
            "by_target": dict(Counter(f["target_type"] for f in fb)),
            "by_reason": dict(Counter(f["reason"] or "none" for f in fb if f["rating"] == "down")),
            "by_route": dict(Counter(f["routed_to"] or "unrouted" for f in fb)),
            "recent": comments,
        },
        "eval_sets": {
            name: {
                "total": n,
                "pending_export": sum(
                    1 for e in evals if e["set_name"] == name and e["exported_at"] is None
                ),
            }
            for name, n in Counter(e["set_name"] for e in evals).items()
        },
        "notifications": {
            "by_status": dict(Counter(n["status"] for n in notes)),
            "by_severity": dict(Counter(n["severity"] for n in notes)),
            "recent": [
                {
                    k: n[k]
                    for k in (
                        "story_id",
                        "severity",
                        "channel",
                        "status",
                        "title",
                        "created_at",
                        "sent_at",
                    )
                }
                for n in notes[:10]
            ],
        },
    }
