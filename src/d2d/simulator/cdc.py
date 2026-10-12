"""Stage 1 CDC stream: second-precision state changes of event-native devices (M5).

Mirrors Home Assistant ``state_changed`` events. This module generates the *routine* baseline
(lights at dusk, garage on commutes, visitors, yard motion); planted M5 scenarios come later.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from d2d.config import Household

CDC_KINDS = frozenset({"light_switch", "garage_door", "ring_doorbell", "ring_camera"})


def _at(day: datetime, hhmm: str, jitter_min: float, rng: random.Random) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    base = day.replace(hour=h, minute=m, second=0)
    return base + timedelta(seconds=round(rng.uniform(-jitter_min, jitter_min) * 60))


def _event(
    device_id: str, event: str, ts: datetime, source: str, value: str | None = None
) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "event": event,
        "value": value,
        "source": source,
        "event_ts": ts,
    }


def _routine(
    kind: str, device_id: str, day: datetime, schedule: dict[str, str] | None, rng: random.Random
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if kind == "light_switch":
        if schedule:  # automation-driven light
            out.append(_event(device_id, "on", _at(day, schedule["start"], 10, rng), "automation"))
            out.append(_event(device_id, "off", _at(day, schedule["end"], 10, rng), "automation"))
        else:
            out.append(_event(device_id, "on", _at(day, "19:00", 30, rng), "manual"))
            out.append(_event(device_id, "off", _at(day, "22:30", 30, rng), "manual"))
    elif kind == "garage_door":
        for hhmm, jitter in (("08:00", 15), ("17:45", 20)):
            opened = _at(day, hhmm, jitter, rng)
            out.append(_event(device_id, "open", opened, "remote"))
            closed = opened + timedelta(seconds=rng.randint(120, 300))
            out.append(_event(device_id, "close", closed, "remote"))
    elif kind == "ring_doorbell":
        for _ in range(rng.randint(0, 2)):
            ts = _at(day, "13:30", 210, rng)
            out.append(_event(device_id, "motion", ts - timedelta(seconds=8), "device"))
            out.append(_event(device_id, "ring", ts, "device"))
            out.append(
                _event(device_id, "answered", ts + timedelta(seconds=rng.randint(30, 90)), "app")
            )
    elif kind == "ring_camera":
        for _ in range(rng.randint(3, 6)):
            ts = _at(day, "13:00", 240, rng)
            out.append(_event(device_id, "motion", ts, "device"))
            if rng.random() < 0.5:
                out.append(
                    _event(device_id, "person_detected", ts + timedelta(seconds=5), "device")
                )
    return out


def generate_cdc(
    household: Household, *, start: datetime, days: int, seed: int
) -> list[dict[str, Any]]:
    """Routine CDC events in UTC, sorted by time; uses its own RNG so snapshots are unaffected."""
    rng = random.Random(f"cdc-{seed}")
    tz = ZoneInfo(household.timezone)
    end = start + timedelta(days=days)
    first_day = start.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    events: list[dict[str, Any]] = []
    for d in range(days + 1):
        day = first_day + timedelta(days=d)
        for spec in sorted(household.devices.values(), key=lambda s: s.id):
            if spec.kind in CDC_KINDS and spec.active:
                events += _routine(spec.kind, spec.id, day, spec.schedule, rng)
    utc = [{**e, "event_ts": e["event_ts"].astimezone(start.tzinfo)} for e in events]
    return sorted(
        (e for e in utc if start <= e["event_ts"] < end),
        key=lambda e: (e["event_ts"], e["device_id"], e["event"]),
    )
