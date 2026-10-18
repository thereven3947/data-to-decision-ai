"""Analytics warehouse export: a star schema of Parquet files for BigQuery / Looker (or DuckDB).

Dimensions come from config, facts from the pipeline output (and, when a database is given, from
Postgres: labels, review status, agent answers and feedback). Free text that a person typed
(questions, answers, comments, usernames) is never exported; agent answers carry only quality
metrics. See docs/analytics-warehouse.md.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import Engine, select

from d2d.config import Household, Taxonomy
from d2d.db import change_story, chat_message, feedback

TS = pa.timestamp("us", tz="UTC")

SCHEMAS: dict[str, pa.Schema] = {
    "dim_device": pa.schema(
        [
            ("device_id", pa.string()),
            ("kind", pa.string()),
            ("room", pa.string()),
            ("circuit_id", pa.string()),
            ("network_id", pa.string()),
            ("rated_w", pa.float64()),
            ("active", pa.bool_()),
            ("tags", pa.string()),
        ]
    ),
    "dim_circuit": pa.schema(
        [("circuit_id", pa.string()), ("panel_id", pa.string()), ("rating_amps", pa.int64())]
    ),
    "dim_category": pa.schema(
        [
            ("category_id", pa.string()),
            ("name", pa.string()),
            ("grid_analogue", pa.string()),
            ("energy", pa.bool_()),
        ]
    ),
    "fact_change_event": pa.schema(
        [
            ("event_id", pa.string()),
            ("story_id", pa.string()),
            ("device_id", pa.string()),
            ("property", pa.string()),
            ("old_value", pa.string()),
            ("new_value", pa.string()),
            ("change_kind", pa.string()),
            ("semantic_tag", pa.string()),
            ("event_ts", TS),
            ("event_date", pa.date32()),
            ("event_hour", pa.int64()),
        ]
    ),
    "fact_story": pa.schema(
        [
            ("story_id", pa.string()),
            ("window_start", TS),
            ("window_end", TS),
            ("story_date", pa.date32()),
            ("duration_min", pa.float64()),
            ("event_count", pa.int64()),
            ("device_count", pa.int64()),
            ("devices", pa.string()),
            ("link_reasons", pa.string()),
            ("status", pa.string()),
            ("category_id", pa.string()),
            ("confidence", pa.float64()),
            ("label_source", pa.string()),
            ("model_version", pa.string()),
        ]
    ),
    "fact_security_alert": pa.schema(
        [
            ("event_id", pa.string()),
            ("device_id", pa.string()),
            ("property", pa.string()),
            ("event_ts", TS),
            ("event_date", pa.date32()),
            ("score", pa.float64()),
            ("alert", pa.bool_()),
            ("reasons", pa.string()),
        ]
    ),
    "fact_agent_answer": pa.schema(
        [
            ("message_id", pa.int64()),
            ("created_at", TS),
            ("answer_date", pa.date32()),
            ("user_role", pa.string()),
            ("skill", pa.string()),
            ("llm_model_tag", pa.string()),
            ("grounded", pa.bool_()),
            ("latency_ms", pa.int64()),
            ("tokens_in", pa.int64()),
            ("tokens_out", pa.int64()),
            ("tool_calls", pa.int64()),
            ("cited_stories", pa.int64()),
        ]
    ),
    "fact_feedback": pa.schema(
        [
            ("feedback_id", pa.int64()),
            ("created_at", TS),
            ("feedback_date", pa.date32()),
            ("target_type", pa.string()),
            ("rating", pa.string()),
            ("reason", pa.string()),
            ("user_role", pa.string()),
            ("routed_to", pa.string()),
        ]
    ),
}


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = pq.read_table(path).to_pylist()
    return rows


def _local(tz: ZoneInfo) -> Callable[[datetime], datetime]:
    return lambda ts: ts.astimezone(tz)


def _devices(household: Household) -> list[dict[str, Any]]:
    return [
        {
            "device_id": d.id,
            "kind": d.kind,
            "room": d.room,
            "circuit_id": d.circuit,
            "network_id": d.network,
            "rated_w": float(d.rated_w),
            "active": d.active,
            "tags": ",".join(d.tags),
        }
        for d in sorted(household.devices.values(), key=lambda d: d.id)
    ]


def _story_labels(engine: Engine) -> dict[str, dict[str, Any]]:
    cols = ("story_id", "status", "category", "confidence", "label_source", "model_version")
    with engine.connect() as conn:
        rows = conn.execute(select(*[change_story.c[c] for c in cols])).mappings()
        return {r["story_id"]: dict(r) for r in rows}


def _agent_answers(engine: Engine, local: Callable[[datetime], datetime]) -> list[dict[str, Any]]:
    cols = (
        "message_id",
        "created_at",
        "user_role",
        "skill",
        "llm_model_tag",
        "checker_result",
        "latency_ms",
        "tokens_in",
        "tokens_out",
        "tool_calls",
        "cited_ids",
    )
    with engine.connect() as conn:
        rows = list(conn.execute(select(*[chat_message.c[c] for c in cols])).mappings())
    return [
        {
            "message_id": r["message_id"],
            "created_at": r["created_at"],
            "answer_date": local(r["created_at"]).date(),
            "user_role": r["user_role"],
            "skill": r["skill"],
            "llm_model_tag": r["llm_model_tag"],
            "grounded": bool((r["checker_result"] or {}).get("ok")),
            "latency_ms": r["latency_ms"],
            "tokens_in": r["tokens_in"],
            "tokens_out": r["tokens_out"],
            "tool_calls": len(r["tool_calls"] or []),
            "cited_stories": len(r["cited_ids"] or []),
        }
        for r in rows
    ]


def _feedback(engine: Engine, local: Callable[[datetime], datetime]) -> list[dict[str, Any]]:
    cols = (
        "feedback_id",
        "created_at",
        "target_type",
        "rating",
        "reason",
        "user_role",
        "routed_to",
    )
    with engine.connect() as conn:
        rows = list(conn.execute(select(*[feedback.c[c] for c in cols])).mappings())
    return [{**dict(r), "feedback_date": local(r["created_at"]).date()} for r in rows]


def build_tables(
    data_dir: Path, household: Household, taxonomy: Taxonomy, engine: Engine | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Rows for every warehouse table. Database-only facts are empty without an engine."""
    local = _local(ZoneInfo(household.timezone))
    stories = _read(data_dir / "stories" / "stories.parquet")
    events = _read(data_dir / "events" / "events.parquet")
    alerts = _read(data_dir / "security" / "alerts.parquet")
    labels = _story_labels(engine) if engine is not None else {}
    story_of = {eid: s["story_id"] for s in stories for eid in s["event_ids"]}

    fact_story = []
    for s in stories:
        lab = labels.get(s["story_id"], {})
        fact_story.append(
            {
                "story_id": s["story_id"],
                "window_start": s["window_start"],
                "window_end": s["window_end"],
                "story_date": local(s["window_start"]).date(),
                "duration_min": (s["window_end"] - s["window_start"]).total_seconds() / 60,
                "event_count": s["event_count"],
                "device_count": len(s["device_ids"]),
                "devices": ",".join(s["device_ids"]),
                "link_reasons": ",".join(s["link_reasons"]),
                "status": lab.get("status", "new"),
                "category_id": lab.get("category"),
                "confidence": lab.get("confidence"),
                "label_source": lab.get("label_source"),
                "model_version": lab.get("model_version"),
            }
        )

    return {
        "dim_device": _devices(household),
        "dim_circuit": [
            {"circuit_id": c.id, "panel_id": c.panel, "rating_amps": c.rating_amps}
            for c in sorted(household.circuits.values(), key=lambda c: c.id)
        ],
        "dim_category": [
            {
                "category_id": c.id,
                "name": c.name,
                "grid_analogue": c.grid_analogue,
                "energy": c.energy,
            }
            for c in taxonomy.categories.values()
        ],
        "fact_change_event": [
            {
                **{k: e[k] for k in SCHEMAS["fact_change_event"].names if k in e},
                "story_id": story_of.get(e["event_id"]),
                "event_date": local(e["event_ts"]).date(),
                "event_hour": local(e["event_ts"]).hour,
            }
            for e in events
        ],
        "fact_story": fact_story,
        "fact_security_alert": [
            {
                **{k: a[k] for k in ("event_id", "device_id", "property", "event_ts", "score")},
                "alert": a["alert"],
                "event_date": local(a["event_ts"]).date(),
                "reasons": ",".join(a["reasons"]),
            }
            for a in alerts
        ],
        "fact_agent_answer": _agent_answers(engine, local) if engine is not None else [],
        "fact_feedback": _feedback(engine, local) if engine is not None else [],
    }


def export_warehouse(
    data_dir: Path,
    out_dir: Path,
    household: Household,
    taxonomy: Taxonomy,
    engine: Engine | None = None,
) -> dict[str, int]:
    """Write one Parquet file per table plus manifest.json; return row counts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for name, rows in build_tables(data_dir, household, taxonomy, engine).items():
        schema = SCHEMAS[name]
        table = pa.Table.from_pylist([{k: r.get(k) for k in schema.names} for r in rows], schema)
        pq.write_table(table, out_dir / f"{name}.parquet")
        counts[name] = table.num_rows
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "home_id": household.id,
        "source": "postgres+files" if engine is not None else "files",
        "tables": counts,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return counts
