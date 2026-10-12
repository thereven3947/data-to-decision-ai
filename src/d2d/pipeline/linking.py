"""Stage 4: link change events into change stories (connected components of a link graph)."""

from __future__ import annotations

import hashlib
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from d2d.config import load_toml


@dataclass(frozen=True)
class LinkingConfig:
    device_window: timedelta
    topology_window: timedelta
    campaign_window: timedelta
    campaign_properties: frozenset[str]
    sha: str


def load_linking(path: Path) -> LinkingConfig:
    raw = load_toml(path)
    w = raw["windows"]
    return LinkingConfig(
        device_window=timedelta(minutes=int(w["device_minutes"])),
        topology_window=timedelta(minutes=int(w["topology_minutes"])),
        campaign_window=timedelta(hours=int(w["campaign_hours"])),
        campaign_properties=frozenset(raw.get("campaign", {}).get("properties", [])),
        sha=hashlib.sha1(path.read_bytes()).hexdigest()[:12],
    )


class TopologyIndex:
    """Circuit and network of each device at (or just before) a timestamp, from snapshots."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._ts: dict[str, list[datetime]] = defaultdict(list)
        self._topo: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for r in rows:  # rows are sorted by device_id, snapshot_ts
            self._ts[r["device_id"]].append(r["snapshot_ts"])
            self._topo[r["device_id"]].append((r["circuit_id"], r["network_id"]))

    def at(self, device_id: str, ts: datetime) -> tuple[str, str] | None:
        i = bisect_right(self._ts[device_id], ts) - 1
        return self._topo[device_id][i] if i >= 0 else None


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


def link_reason(
    a: dict[str, Any],
    b: dict[str, Any],
    topo_a: tuple[str, str] | None,
    topo_b: tuple[str, str] | None,
    cfg: LinkingConfig,
) -> str | None:
    dt = abs(a["event_ts"] - b["event_ts"])
    if a["device_id"] == b["device_id"] and dt <= cfg.device_window:
        return "device"
    if topo_a and topo_b and dt <= cfg.topology_window:
        if topo_a[0] == topo_b[0]:
            return "circuit"
        if topo_a[1] == topo_b[1]:
            return "network"
    if (
        a["property"] == b["property"]
        and a["property"] in cfg.campaign_properties
        and a["new_value"] == b["new_value"]
        and dt <= cfg.campaign_window
    ):
        return "campaign"
    return None


def build_stories(
    events: list[dict[str, Any]], topology: TopologyIndex, cfg: LinkingConfig, home_id: str
) -> list[dict[str, Any]]:
    events = sorted(events, key=lambda e: (e["event_ts"], e["event_id"]))
    topo = [topology.at(e["device_id"], e["event_ts"]) for e in events]
    uf = _UnionFind(len(events))
    reasons: dict[tuple[int, int], str] = {}
    widest = max(cfg.device_window, cfg.topology_window, cfg.campaign_window)
    for i, a in enumerate(events):
        for j in range(i + 1, len(events)):
            b = events[j]
            if b["event_ts"] - a["event_ts"] > widest:
                break
            reason = link_reason(a, b, topo[i], topo[j], cfg)
            if reason:
                uf.union(i, j)
                reasons[(i, j)] = reason

    members: dict[int, list[int]] = defaultdict(list)
    for i in range(len(events)):
        members[uf.find(i)].append(i)
    story_reasons: dict[int, set[str]] = defaultdict(set)
    for (i, _), reason in reasons.items():
        story_reasons[uf.find(i)].add(reason)

    stories = []
    for root, idx in members.items():
        group = [events[i] for i in idx]
        event_ids = sorted(e["event_id"] for e in group)
        stories.append(
            {
                "story_id": hashlib.sha1("|".join(event_ids).encode()).hexdigest()[:16],
                "home_id": home_id,
                "window_start": min(e["event_ts"] for e in group),
                "window_end": max(e["event_ts"] for e in group),
                "device_ids": sorted({e["device_id"] for e in group}),
                "event_ids": event_ids,
                "event_count": len(group),
                "link_reasons": sorted(story_reasons[root]),
                "linking_config_sha": cfg.sha,
            }
        )
    return sorted(stories, key=lambda s: (s["window_start"], s["story_id"]))
