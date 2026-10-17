"""Read-only agent tools. Each returns compact JSON under the caller's scope."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from d2d.api.repo import Ontology
from d2d.api.scope import Scope

_STR = {"type": "string"}


def _fn(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


TOOL_SPECS = [
    _fn(
        "list_stories",
        "List change stories (newest first). Filters are optional.",
        {
            "status": {**_STR, "enum": ["new", "needs_review", "published", "closed"]},
            "category": {**_STR, "description": "Category id, e.g. load_shift"},
            "device_id": {**_STR, "description": "Device id, e.g. washer"},
            "limit": {"type": "integer", "description": "Max stories (default 10)"},
        },
        [],
    ),
    _fn(
        "get_story",
        "One story with its linked events, enrichment, label and category playbook.",
        {"story_id": {**_STR, "description": "16-character story id"}},
        ["story_id"],
    ),
    _fn(
        "get_device_history",
        "Recent change events and stories for one device.",
        {"device_id": _STR},
        ["device_id"],
    ),
    _fn(
        "list_devices",
        "All devices with room, kind and latest state.",
        {},
        [],
    ),
    _fn(
        "get_category_playbook",
        "Definition, plain-language meaning, checks and recommended actions for a category.",
        {"category": _STR},
        ["category"],
    ),
    _fn("get_kpis", "Story counts by status, label source and category; review backlog.", {}, []),
]
TOOL_NAMES = {t["function"]["name"] for t in TOOL_SPECS}


_TZ: dict[str, ZoneInfo] = {}


def _ts(value: Any, tz: str = "UTC") -> Any:
    """Local home time, e.g. 'Thu Oct 01 2026 03:00 PDT', so the LLM never converts time zones."""
    if not isinstance(value, datetime):
        return value
    zone = _TZ.setdefault(tz, ZoneInfo(tz))
    return value.astimezone(zone).strftime("%a %b %d %Y %H:%M %Z")


def _story_brief(s: dict[str, Any], tz: str) -> dict[str, Any]:
    return {
        "story_id": s["story_id"],
        "title": s["title"],
        "category": s["category"],
        "confidence": s["confidence"],
        "label_source": s["label_source"],
        "status": s["status"],
        "window_start": _ts(s["window_start"], tz),
        "window_end": _ts(s["window_end"], tz),
        "device_ids": s["device_ids"],
    }


def _event_brief(e: dict[str, Any], tz: str) -> dict[str, Any]:
    return {
        "device_id": e["device_id"],
        "property": e["property"],
        "old": e["old_value"],
        "new": e["new_value"],
        "tag": e["semantic_tag"],
        "ts": _ts(e["event_ts"], tz),
    }


class ToolError(ValueError):
    pass


def run_tool(name: str, args: dict[str, Any], onto: Ontology, scope: Scope) -> dict[str, Any]:
    tz = onto.household.timezone
    if name == "list_stories":
        limit = max(1, min(int(args.get("limit") or 10), 25))
        stories = onto.list_stories(
            scope,
            status=args.get("status") or None,
            category=args.get("category") or None,
            device_id=args.get("device_id") or None,
            limit=limit,
        )
        return {"stories": [_story_brief(s, tz) for s in stories]}
    if name == "get_story":
        story = onto.get_story(scope, str(args.get("story_id", "")))
        if story is None:
            raise ToolError(f"story {args.get('story_id')!r} not found or not visible")
        return {
            **_story_brief(story, tz),
            "summary": story["summary"],
            "facts": story["enrichment"]["facts"],
            "link_reasons": story["link_reasons"],
            "events": [_event_brief(e, tz) for e in story["events"]][:30],
            "playbook": story["playbook"],
            "review": {"reason": story["review"]["reason"]} if story["review"] else None,
        }
    if name == "get_device_history":
        hist = onto.device_history(scope, str(args.get("device_id", "")))
        if hist is None:
            raise ToolError(f"device {args.get('device_id')!r} not found or not visible")
        latest = hist["device"].get("latest") or {}
        return {
            "device": {
                **{k: v for k, v in hist["device"].items() if k != "latest"},
                "latest": {k: _ts(v, tz) for k, v in latest.items()},
            },
            "events": [_event_brief(e, tz) for e in hist["events"]][:30],
            "stories": [_story_brief(s, tz) for s in hist["stories"]][:10],
        }
    if name == "list_devices":
        return {
            "devices": [
                {
                    k: d.get(k)
                    for k in ("device_id", "kind", "room", "active", "story_count")
                    if k in d
                }
                | {"state": (d.get("latest") or {}).get("state")}
                for d in onto.list_devices(scope)
            ]
        }
    if name == "get_category_playbook":
        cat = onto.taxonomy.categories.get(str(args.get("category", "")))
        if cat is None or not scope.category_visible(cat.id):
            raise ToolError(f"unknown category {args.get('category')!r}")
        return {
            "category": cat.id,
            "name": cat.name,
            "definition": cat.definition,
            "plain": cat.plain,
            "checks": list(cat.checks),
            "actions": list(cat.actions),
        }
    if name == "get_kpis":
        return onto.kpis(scope)
    raise ToolError(f"unknown tool {name!r}")
