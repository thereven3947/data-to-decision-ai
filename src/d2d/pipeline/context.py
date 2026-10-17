"""Context rules over the CDC stream (M5-03): duration, policy zone and sequence.

All thresholds, windows and zones live in ``semantics.toml``. Each rule emits at most one change
per device per episode, at the moment the condition becomes true:

- ``[[duration]]``: a state held at least ``min_minutes`` (optionally only counting time inside a
  zone), e.g. garage left open, light on > 4 h while away.
- ``[[zone_rule]]``: a CDC change inside a policy zone, e.g. motion while ``away_mode``, garage
  opened during ``quiet_hours``.
- ``[[sequence]]``: trigger + required companion signal + missing follow-up, e.g. doorbell ring +
  person detected + no answer within 2 minutes = missed visitor.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from d2d.pipeline.cdc import CDC_RUN_ID
from d2d.pipeline.changelog import Change
from d2d.pipeline.semantics import DurationRule, Semantics, SequenceRule, Zone
from d2d.simulator.devices import parse_hhmm

Interval = tuple[datetime, datetime]


def zone_intervals(
    zone: Zone,
    rows: list[dict[str, Any]],
    windows: dict[str, tuple[str, str]],
    tz: str,
    lo: datetime,
    hi: datetime,
) -> list[Interval]:
    """Episodes of ``zone`` between ``lo`` and ``hi`` (UTC)."""
    if zone.property is not None:
        out: list[Interval] = []
        opened: datetime | None = None
        for r in rows:
            if r["property"] != zone.property:
                continue
            if r["new_value"] == zone.on_value and opened is None:
                opened = r["event_ts"]
            elif r["new_value"] != zone.on_value and opened is not None:
                out.append((opened, r["event_ts"]))
                opened = None
        if opened is not None:
            out.append((opened, hi))
        return out
    assert zone.window is not None
    start, end = (parse_hhmm(t) for t in windows[zone.window])
    zi = ZoneInfo(tz)
    day = lo.astimezone(zi).date() - timedelta(days=1)
    out = []
    while True:
        a = datetime.combine(day, start, zi)
        b_day = day + timedelta(days=1) if end <= start else day
        b = datetime.combine(b_day, end, zi)
        if a > hi:
            return out
        if b >= lo:
            out.append((a.astimezone(lo.tzinfo), b.astimezone(lo.tzinfo)))
        day += timedelta(days=1)


def _containing(intervals: list[Interval], ts: datetime) -> Interval | None:
    return next((iv for iv in intervals if iv[0] <= ts < iv[1]), None)


def _bounds(rows: list[dict[str, Any]], until: datetime | None) -> tuple[datetime, datetime]:
    stamps = [r["event_ts"] for r in rows]
    lo = min(stamps)
    return lo, max([*stamps, until] if until else stamps)


def _change(
    dev: str, prop: str, old: str | None, new: str, kind: str, tag: str, t0: datetime, t1: datetime
) -> Change:
    return Change(
        device_id=dev,
        property=prop,
        old_value=old,
        new_value=new,
        change_kind=kind,
        semantic_tag=tag,
        snapshot_ts_before=t0,
        snapshot_ts_after=t1,
        snapshot_run_id=CDC_RUN_ID,
    )


def duration_changes(
    rows: list[dict[str, Any]],
    rules: list[DurationRule],
    device_kinds: dict[str, str],
    until: datetime | None = None,
    semantics: Semantics | None = None,
    tz: str = "UTC",
) -> list[Change]:
    """``rows`` are CDC rows sorted by event_ts."""
    changes: list[Change] = []
    for rule in rules:
        limit = timedelta(minutes=rule.min_minutes)
        started: dict[str, datetime] = {}
        held: list[tuple[str, datetime, datetime]] = []
        for r in rows:
            dev = r["device_id"]
            if r["property"] != rule.property:
                continue
            if rule.kinds and device_kinds.get(dev) not in rule.kinds:
                continue
            if r["new_value"] == rule.value:
                started[dev] = r["event_ts"]
            elif dev in started:
                held.append((dev, started.pop(dev), r["event_ts"]))
        if until is not None:
            held += [(dev, t0, until) for dev, t0 in started.items()]
        zones: list[Interval] | None = None
        if rule.zone is not None and semantics is not None and rows:
            lo, hi = _bounds(rows, until)
            zone = semantics.zones[rule.zone]
            zones = zone_intervals(zone, rows, semantics.windows, tz, lo, hi)
        for dev, t0, t1 in held:
            spans = [(t0, t1)]
            if zones is not None:
                spans = [(max(t0, a), min(t1, b)) for a, b in zones if max(t0, a) < min(t1, b)]
            for a, b in spans:
                if b - a >= limit:
                    changes.append(
                        _change(
                            dev,
                            f"{rule.property}.duration",
                            rule.value,
                            f"{rule.value}>{rule.min_minutes}m",
                            rule.change_kind,
                            rule.tag,
                            a,
                            a + limit,
                        )
                    )
    return changes


def zone_changes(
    rows: list[dict[str, Any]],
    semantics: Semantics,
    device_kinds: dict[str, str],
    until: datetime | None = None,
    tz: str = "UTC",
) -> list[Change]:
    if not rows or not semantics.zone_rules:
        return []
    lo, hi = _bounds(rows, until)
    cache: dict[str, list[Interval]] = {}
    changes: list[Change] = []
    for rule in semantics.zone_rules:
        zone = semantics.zones[rule.zone]
        if rule.zone not in cache:
            cache[rule.zone] = zone_intervals(zone, rows, semantics.windows, tz, lo, hi)
        fired: set[tuple[str, Interval]] = set()
        for r in rows:
            dev = r["device_id"]
            if r["property"] != rule.property or r["new_value"] != rule.value:
                continue
            if rule.kinds and device_kinds.get(dev) not in rule.kinds:
                continue
            iv = _containing(cache[rule.zone], r["event_ts"])
            if iv is None or (dev, iv) in fired:
                continue
            fired.add((dev, iv))
            changes.append(
                _change(
                    dev,
                    r["property"],
                    r["old_value"],
                    r["new_value"],
                    rule.change_kind,
                    rule.tag,
                    r["event_ts"],
                    r["event_ts"],
                )
            )
    return changes


def sequence_changes(
    rows: list[dict[str, Any]], rules: list[SequenceRule], device_kinds: dict[str, str]
) -> list[Change]:
    changes: list[Change] = []
    for rule in rules:
        near = timedelta(seconds=rule.require_within_s)
        wait = timedelta(seconds=rule.absent_within_s)
        for r in rows:
            dev, ts = r["device_id"], r["event_ts"]
            if r["property"] != rule.trigger_property or r["new_value"] != rule.trigger_value:
                continue
            if rule.trigger_kinds and device_kinds.get(dev) not in rule.trigger_kinds:
                continue
            required = any(
                o["property"] == rule.require_property
                and o["new_value"] == rule.require_value
                and (
                    not rule.require_kinds or device_kinds.get(o["device_id"]) in rule.require_kinds
                )
                and abs(o["event_ts"] - ts) <= near
                for o in rows
            )
            answered = any(
                o["device_id"] == dev
                and o["property"] == rule.absent_property
                and o["new_value"] == rule.absent_value
                and ts < o["event_ts"] <= ts + wait
                for o in rows
            )
            if required and not answered:
                changes.append(
                    _change(
                        dev,
                        rule.emit_property,
                        r["new_value"],
                        rule.emit_value,
                        rule.change_kind,
                        rule.tag,
                        ts,
                        ts + wait,
                    )
                )
    return changes


def context_changes(
    rows: list[dict[str, Any]],
    semantics: Semantics,
    device_kinds: dict[str, str],
    until: datetime | None,
    tz: str,
) -> list[Change]:
    return (
        duration_changes(rows, list(semantics.durations), device_kinds, until, semantics, tz)
        + zone_changes(rows, semantics, device_kinds, until, tz)
        + sequence_changes(rows, list(semantics.sequences), device_kinds)
    )
