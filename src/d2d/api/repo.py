"""Read model shared by the API routes and the agent tools. Every method applies a Scope."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq
from sqlalchemy import Engine, select

from d2d.api.scope import Scope
from d2d.config import Household, Taxonomy
from d2d.db import (
    case_story,
    change_case,
    change_event,
    change_story,
    change_story_event,
    label,
    review_item,
)
from d2d.enrichment import EnrichmentConfig, display_name, enrich
from d2d.pipeline.events import event_id

STORY_COLUMNS = (
    "story_id",
    "home_id",
    "window_start",
    "window_end",
    "device_ids",
    "event_count",
    "link_reasons",
    "linking_config_sha",
    "status",
    "category",
    "confidence",
    "label_source",
    "model_version",
)
EVENT_COLUMNS = (
    "event_id",
    "device_id",
    "property",
    "old_value",
    "new_value",
    "change_kind",
    "semantic_tag",
    "event_ts",
    "snapshot_run_id",
)

_LATEST_SQL = """
SELECT device_id, snapshot_ts, state, power_w, energy_kwh_total, firmware_version, mode,
       setpoint, network_id, rssi_dbm, circuit_id, error_code
FROM read_parquet(?, union_by_name = true)
QUALIFY row_number() OVER (PARTITION BY device_id ORDER BY snapshot_ts DESC) = 1
"""


@dataclass
class Ontology:
    engine: Engine
    household: Household
    taxonomy: Taxonomy
    enrichment: EnrichmentConfig
    data_dir: Path

    # --- stories ---------------------------------------------------------------------------
    def _stories(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            stories = [
                dict(r)
                for r in conn.execute(
                    select(*[change_story.c[c] for c in STORY_COLUMNS]).order_by(
                        change_story.c.window_start.desc(), change_story.c.story_id
                    )
                ).mappings()
            ]
            rows = conn.execute(
                select(change_story_event.c.story_id, *[change_event.c[c] for c in EVENT_COLUMNS])
                .join(change_event, change_event.c.event_id == change_story_event.c.event_id)
                .order_by(change_event.c.event_ts, change_event.c.device_id)
            ).mappings()
            events: dict[str, list[dict[str, Any]]] = {}
            for r in rows:
                events.setdefault(r["story_id"], []).append({k: r[k] for k in EVENT_COLUMNS})
        for s in stories:
            s["events"] = events.get(s["story_id"], [])
        return stories

    def _view(self, s: dict[str, Any], scope: Scope, detail: bool) -> dict[str, Any]:
        e = enrich(s["category"], s["events"], self.household, self.enrichment)
        cat = self.taxonomy.categories.get(s["category"] or "")
        view = {
            **{k: s[k] for k in STORY_COLUMNS},
            "title": e["title"],
            "summary": e["summary"],
            "category_name": cat.name if cat else None,
            "devices": [self.device_ref(d, scope) for d in s["device_ids"]],
        }
        if detail:
            view["events"] = s["events"]
            view["enrichment"] = e
            view["playbook"] = (
                {
                    "category": cat.id,
                    "name": cat.name,
                    "definition": cat.definition,
                    "plain": cat.plain,
                    "grid_analogue": cat.grid_analogue,
                    "checks": list(cat.checks),
                    "actions": list(cat.actions),
                }
                if cat
                else None
            )
        return view

    def list_stories(
        self,
        scope: Scope,
        status: str | None = None,
        category: str | None = None,
        device_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        out = []
        for s in self._stories():
            if not scope.story_visible(s):
                continue
            if status and s["status"] != status:
                continue
            if category and s["category"] != category:
                continue
            if device_id and device_id not in s["device_ids"]:
                continue
            out.append(self._view(s, scope, detail=False))
            if len(out) >= limit:
                break
        return out

    def get_story(self, scope: Scope, story_id: str) -> dict[str, Any] | None:
        for s in self._stories():
            if s["story_id"] == story_id and scope.story_visible(s):
                view = self._view(s, scope, detail=True)
                view["labels"] = self._labels(story_id) if scope.policy.can_label else []
                view["cases"] = self._cases_for(story_id)
                view["review"] = self._review(story_id)
                return view
        return None

    def visible_event_ids(self, scope: Scope) -> set[str]:
        return {
            e["event_id"] for s in self._stories() if scope.story_visible(s) for e in s["events"]
        }

    def _labels(self, story_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    select(label).where(label.c.story_id == story_id).order_by(label.c.label_id)
                ).mappings()
            ]

    def _review(self, story_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            r = (
                conn.execute(select(review_item).where(review_item.c.story_id == story_id))
                .mappings()
                .first()
            )
            return dict(r) if r else None

    def _cases_for(self, story_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    select(change_case.c.case_id, change_case.c.title, change_case.c.status)
                    .join(case_story, case_story.c.case_id == change_case.c.case_id)
                    .where(case_story.c.story_id == story_id)
                ).mappings()
            ]

    # --- review queue and cases ---------------------------------------------------------
    def review_queue(self, scope: Scope) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            items = {
                r["story_id"]: dict(r)
                for r in conn.execute(
                    select(review_item).where(review_item.c.resolved_at.is_(None))
                ).mappings()
            }
        out = []
        for s in self._stories():
            if s["story_id"] in items and scope.story_visible(s):
                out.append({**self._view(s, scope, detail=False), "review": items[s["story_id"]]})
        return sorted(out, key=lambda x: (x["review"]["queued_at"], x["window_start"]))

    def list_cases(self, scope: Scope) -> list[dict[str, Any]]:
        visible = {s["story_id"] for s in self._stories() if scope.story_visible(s)}
        with self.engine.connect() as conn:
            cases = [
                dict(r)
                for r in conn.execute(
                    select(change_case).order_by(change_case.c.created_at.desc())
                ).mappings()
            ]
            links = conn.execute(select(case_story.c.case_id, case_story.c.story_id)).all()
        by_case: dict[int, list[str]] = {}
        for case_id, story_id in links:
            by_case.setdefault(case_id, []).append(story_id)
        out = []
        for c in cases:
            stories = by_case.get(c["case_id"], [])
            if stories and not all(s in visible for s in stories):
                continue
            out.append({**c, "story_ids": sorted(stories)})
        return out

    # --- events and changelog -----------------------------------------------------------
    def _event_filter(self, scope: Scope) -> set[str] | None:
        return self.visible_event_ids(scope) if scope.event_level else None

    def list_events(
        self,
        scope: Scope,
        device_id: str | None = None,
        change_kind: str | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        allowed = self._event_filter(scope)
        q = select(*[change_event.c[c] for c in EVENT_COLUMNS]).order_by(
            change_event.c.event_ts.desc(), change_event.c.device_id
        )
        if device_id:
            q = q.where(change_event.c.device_id == device_id)
        if change_kind:
            q = q.where(change_event.c.change_kind == change_kind)
        if since:
            q = q.where(change_event.c.event_ts >= since)
        with self.engine.connect() as conn:
            story_of: dict[str, str] = {
                eid: sid
                for eid, sid in conn.execute(
                    select(change_story_event.c.event_id, change_story_event.c.story_id)
                )
            }
            rows = [dict(r) for r in conn.execute(q).mappings()]
        out = []
        for r in rows:
            if not scope.device_visible(r["device_id"]):
                continue
            if allowed is not None and r["event_id"] not in allowed:
                continue
            out.append({**r, "story_id": story_of.get(r["event_id"])})
            if len(out) >= limit:
                break
        return out

    def changelog(
        self, scope: Scope, device_id: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        path = self.data_dir / "changelog" / "changelog.parquet"
        if not path.is_file():
            return []
        allowed = self._event_filter(scope)
        out = []
        rows = pq.read_table(path).to_pylist()
        rows.sort(key=lambda r: (r["snapshot_ts_after"], r["device_id"]), reverse=True)
        for r in rows:
            if not scope.device_visible(r["device_id"]):
                continue
            if device_id and r["device_id"] != device_id:
                continue
            eid = event_id(r["device_id"], r["property"], r["snapshot_ts_after"].isoformat())
            if allowed is not None and eid not in allowed:
                continue
            out.append({**r, "event_id": eid})
            if len(out) >= limit:
                break
        return out

    # --- devices ------------------------------------------------------------------------
    def device_ref(self, device_id: str, scope: Scope) -> dict[str, Any]:
        spec = self.household.devices.get(device_id)
        ref = {
            "device_id": device_id,
            "name": display_name(device_id),
            "kind": spec.kind if spec else "unknown",
            "room": spec.room if spec else None,
        }
        return scope.redact_device(ref)

    def _latest_snapshots(self) -> dict[str, dict[str, Any]]:
        snaps = self.data_dir / "snapshots"
        if not snaps.is_dir() or not any(snaps.glob("*/*.parquet")):
            return {}
        with duckdb.connect() as con:
            con.execute("SET TimeZone = 'UTC'")
            table = con.execute(_LATEST_SQL, [(snaps / "*" / "*.parquet").as_posix()])
            rows: list[dict[str, Any]] = table.to_arrow_table().to_pylist()
        return {r["device_id"]: r for r in rows}

    def list_devices(self, scope: Scope) -> list[dict[str, Any]]:
        latest = self._latest_snapshots()
        events = self.list_events(scope, limit=100_000)
        stories = self.list_stories(scope, limit=100_000)
        ev_count = Counter(e["device_id"] for e in events)
        st_count = Counter(d for s in stories for d in s["device_ids"])
        out = []
        for d in sorted(self.household.devices.values(), key=lambda d: (d.room, d.id)):
            if not scope.device_visible(d.id):
                continue
            snap = latest.get(d.id, {})
            out.append(
                scope.redact_device(
                    {
                        "device_id": d.id,
                        "name": display_name(d.id),
                        "kind": d.kind,
                        "room": d.room,
                        "circuit_id": snap.get("circuit_id") or d.circuit,
                        "network_id": snap.get("network_id") or d.network,
                        "rated_w": d.rated_w,
                        "active": d.active,
                        "tags": list(d.tags),
                        "latest": {k: v for k, v in snap.items() if k != "device_id"} or None,
                        "event_count": ev_count[d.id],
                        "story_count": st_count[d.id],
                    }
                )
            )
        return out

    def device_history(
        self, scope: Scope, device_id: str, since: datetime | None = None
    ) -> dict[str, Any] | None:
        if not scope.device_visible(device_id) or device_id not in self.household.devices:
            return None
        device = next(d for d in self.list_devices(scope) if d["device_id"] == device_id)
        stories = self.list_stories(scope, device_id=device_id)
        if since:
            stories = [s for s in stories if s["window_end"] >= since]
        return {
            "device": device,
            "events": self.list_events(scope, device_id=device_id, since=since),
            "stories": stories,
        }

    # --- KPIs ---------------------------------------------------------------------------
    def kpis(self, scope: Scope) -> dict[str, Any]:
        stories = [s for s in self._stories() if scope.story_visible(s)]
        by_status = Counter(s["status"] for s in stories)
        by_source = Counter(s["label_source"] or "unlabeled" for s in stories)
        by_category = Counter(s["category"] or "unlabeled" for s in stories)
        labeled = sum(n for k, n in by_source.items() if k != "unlabeled")
        with self.engine.connect() as conn:
            open_reviews = conn.execute(
                select(review_item.c.story_id, review_item.c.reason).where(
                    review_item.c.resolved_at.is_(None)
                )
            ).all()
            # Agreement of model labels with later human / simulated reviews (audit sample).
            rows = conn.execute(
                select(label.c.story_id, label.c.source, label.c.category).order_by(
                    label.c.label_id
                )
            ).all()
        visible = {s["story_id"] for s in stories}
        model_label: dict[str, str] = {}
        agree = total = 0
        for sid, source, category in rows:
            if sid not in visible:
                continue
            if source == "model":
                model_label[sid] = category
            elif sid in model_label:
                total += 1
                agree += model_label.pop(sid) == category
        return {
            "stories": len(stories),
            "events": sum(s["event_count"] for s in stories),
            "by_status": dict(by_status),
            "by_label_source": dict(by_source),
            "by_category": dict(sorted(by_category.items())),
            "auto_label_coverage": round(by_source.get("model", 0) / labeled, 3)
            if labeled
            else None,
            "open_reviews": sum(1 for sid, _ in open_reviews if sid in visible),
            "abstained_open": sum(
                1 for sid, r in open_reviews if sid in visible and r == "abstain"
            ),
            "audit_agreement": round(agree / total, 3) if total else None,
            "audited": total,
        }
