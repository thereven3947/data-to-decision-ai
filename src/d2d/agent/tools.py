"""Read-only agent tools. Each returns compact JSON under the caller's scope."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from d2d.agent.search import HashingEmbedder, StorySearch
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
        "List change stories (newest first) with the total count, counts per category and the "
        "period the data covers. Filters are optional; time filters use the home's local dates.",
        {
            "status": {**_STR, "enum": ["new", "needs_review", "published", "closed"]},
            "category": {**_STR, "description": "Category id, e.g. load_shift"},
            "device_id": {**_STR, "description": "Device id, e.g. washer"},
            "last_days": {
                "type": "integer",
                "description": "Only stories in the last N days, counted back from as_of "
                "(the end of the data), e.g. 7 for 'the past week'",
            },
            "since": {**_STR, "description": "Only stories on or after this date, YYYY-MM-DD"},
            "until": {**_STR, "description": "Only stories on or before this date, YYYY-MM-DD"},
            "limit": {
                "type": "integer",
                "description": "Max stories to list (default 10, max 25). Counts always cover "
                "all matching stories.",
            },
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
    _fn(
        "search_stories",
        "Find stories and category playbooks by meaning, for questions that name no category, "
        "device or date (e.g. 'anything risky with hot water?'). Returns the closest matches, "
        "each with a 0-1 relevance score; low scores mean a weak match.",
        {
            "query": {**_STR, "description": "What to look for, in plain words"},
            "limit": {"type": "integer", "description": "Max stories (default 5, max 10)"},
        },
        ["query"],
    ),
]
TOOL_NAMES = {t["function"]["name"] for t in TOOL_SPECS}
_LEXICAL = StorySearch(HashingEmbedder())


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


def _date(value: Any, field: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ToolError(f"{field} must be a date like 2026-10-01") from exc


def _list_stories(args: dict[str, Any], onto: Ontology, scope: Scope, tz: str) -> dict[str, Any]:
    zone = ZoneInfo(tz)
    limit = max(1, min(int(args.get("limit") or 10), 25))
    visible = onto.list_stories(scope, limit=100_000)
    if not visible:
        return {"total_matching": 0, "stories": [], "data_covers": None}

    def local(s: dict[str, Any], key: str) -> date:
        ts: datetime = s[key]
        return ts.astimezone(zone).date()

    first = min(local(s, "window_start") for s in visible)
    last = max(local(s, "window_end") for s in visible)
    as_of = max(s["window_end"] for s in visible)
    since, until = _date(args.get("since"), "since"), _date(args.get("until"), "until")
    if args.get("last_days") not in (None, ""):
        days = max(1, int(args["last_days"]))
        since = max(since or date.min, last - timedelta(days=days - 1))
    matching = [
        s
        for s in visible
        if (not args.get("status") or s["status"] == args["status"])
        and (not args.get("category") or s["category"] == args["category"])
        and (not args.get("device_id") or args["device_id"] in s["device_ids"])
        and (since is None or local(s, "window_end") >= since)
        and (until is None or local(s, "window_start") <= until)
    ]
    covered_days = (last - first).days + 1
    asked_days = int(args["last_days"]) if args.get("last_days") not in (None, "") else None
    exceeds = (asked_days is not None and asked_days > covered_days) or (
        since is not None and since < first
    )
    note = (
        f"The requested period is longer than the data: the simulated household only has data "
        f"from {first.isoformat()} to {last.isoformat()} ({covered_days} days). Say this first, "
        f"then report the {len(matching)} matching stories."
        if exceeds
        else None
    )
    return {
        **({"coverage_note": note} if note else {}),
        "data_covers": {
            "from": first.isoformat(),
            "to": last.isoformat(),
            "days": covered_days,
            "note": "simulated household data; this is the whole period available",
        },
        "as_of": _ts(as_of, tz),
        "filter": {
            k: v
            for k, v in {
                "since": since.isoformat() if since else None,
                "until": until.isoformat() if until else None,
                "status": args.get("status"),
                "category": args.get("category"),
                "device_id": args.get("device_id"),
            }.items()
            if v
        },
        "total_matching": len(matching),
        "by_category": dict(Counter(s["category"] or "unlabeled" for s in matching)),
        "returned": min(limit, len(matching)),
        "stories": [_story_brief(s, tz) for s in matching[:limit]],
    }


def run_tool(name: str, args: dict[str, Any], onto: Ontology, scope: Scope) -> dict[str, Any]:
    tz = onto.household.timezone
    if name == "list_stories":
        return _list_stories(args, onto, scope, tz)
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
    if name == "search_stories":
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("query is required")
        limit = max(1, min(int(args.get("limit") or 5), 10))
        method, stories, playbooks = (onto.search or _LEXICAL).search(query, onto, scope, limit)
        return {
            "query": query,
            "method": method,
            "stories": [
                {**_story_brief(s, tz), "summary": s["summary"], "score": round(score, 3)}
                for score, s in stories
            ],
            "playbooks": [
                {"category": c.id, "name": c.name, "score": round(score, 3)}
                for score, c in playbooks
            ],
        }
    raise ToolError(f"unknown tool {name!r}")
