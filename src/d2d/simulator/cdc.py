"""Stage 1 CDC stream: second-precision property changes of event-native devices (M5).

Same shape as a change event: ``device_id, property, old_value, new_value, event_ts, source``
(Home Assistant ``state_changed`` carries old and new state). Momentary signals (doorbell press,
motion) are pulses: ``idle -> pressed -> idle``. This module generates the *routine* baseline;
planted M5 scenarios come later.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from d2d.config import Household

CDC_KINDS = frozenset({"light_switch", "garage_door", "ring_doorbell", "ring_camera"})
INITIAL = {"door": "closed", "power": "off", "motion": "clear", "person": "clear", "button": "idle"}


def _at(day: datetime, hhmm: str, jitter_min: float, rng: random.Random) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    base = day.replace(hour=h, minute=m, second=0)
    return base + timedelta(seconds=round(rng.uniform(-jitter_min, jitter_min) * 60))


Planned = tuple[datetime, str, str, str]  # (ts, property, new_value, source)


def _pulse(prop: str, on: str, off: str, ts: datetime, hold_s: int, src: str) -> list[Planned]:
    return [(ts, prop, on, src), (ts + timedelta(seconds=hold_s), prop, off, src)]


def _routine(
    kind: str, day: datetime, schedule: dict[str, str] | None, rng: random.Random
) -> list[Planned]:
    out: list[Planned] = []
    if kind == "light_switch":
        on, off, src = (
            (schedule["start"], schedule["end"], "automation")
            if schedule
            else ("19:00", "22:30", "manual")
        )
        jitter = 10 if schedule else 30
        out += [(_at(day, on, jitter, rng), "power", "on", src)]
        out += [(_at(day, off, jitter, rng), "power", "off", src)]
    elif kind == "garage_door":
        for hhmm, jitter in (("08:00", 15), ("17:45", 20)):
            opened = _at(day, hhmm, jitter, rng)
            out += _pulse("door", "open", "closed", opened, rng.randint(120, 300), "remote")
    elif kind == "ring_doorbell":
        for _ in range(rng.randint(0, 2)):
            ts = _at(day, "13:30", 210, rng)
            out += _pulse("motion", "detected", "clear", ts - timedelta(seconds=8), 30, "device")
            out += _pulse("button", "pressed", "idle", ts, 1, "device")
            out += _pulse("call", "answered", "idle", ts + timedelta(seconds=40), 30, "app")
    elif kind == "ring_camera":
        for _ in range(rng.randint(3, 6)):
            ts = _at(day, "13:00", 240, rng)
            out += _pulse("motion", "detected", "clear", ts, 30, "device")
            if rng.random() < 0.5:
                out += _pulse(
                    "person", "detected", "clear", ts + timedelta(seconds=5), 20, "device"
                )
    return out


def _planted_open(household: Household, start: datetime) -> tuple[datetime, datetime] | None:
    """Scenario garage_left_open_overnight: 2nd local evening 22:15 -> next day 06:30."""
    if "garage_door" not in household.devices:
        return None
    tz = ZoneInfo(household.timezone)
    day = start.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    opened = (day + timedelta(days=1)).replace(hour=22, minute=15)
    closed = (day + timedelta(days=2)).replace(hour=6, minute=30)
    return opened.astimezone(start.tzinfo), closed.astimezone(start.tzinfo)


def cdc_ground_truth(
    household: Household, *, start: datetime, days: int, garage_minutes: int = 30
) -> list[dict[str, Any]]:
    planted = _planted_open(household, start)
    if planted is None or planted[1] >= start + timedelta(days=days):
        return []
    ts = (planted[0] + timedelta(minutes=garage_minutes)).isoformat()
    return [
        {
            "scenario_id": "garage_left_open_overnight",
            "category": "access_left_open",
            "events": [{"device_id": "garage_door", "property": "door.duration", "ts": ts}],
            "start": ts,
            "end": ts,
        }
    ]


def generate_cdc(
    household: Household, *, start: datetime, days: int, seed: int
) -> list[dict[str, Any]]:
    """Routine CDC rows in UTC, sorted by time; own RNG so snapshots are unaffected."""
    rng = random.Random(f"cdc-{seed}")
    tz = ZoneInfo(household.timezone)
    end = start + timedelta(days=days)
    first_day = start.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    planned: list[tuple[datetime, str, str, str, str]] = []
    for d in range(days + 1):
        day = first_day + timedelta(days=d)
        for spec in sorted(household.devices.values(), key=lambda s: s.id):
            if spec.kind in CDC_KINDS and spec.active:
                for ts, prop, new, src in _routine(spec.kind, day, spec.schedule, rng):
                    planned.append((ts.astimezone(start.tzinfo), spec.id, prop, new, src))
    overnight = _planted_open(household, start)
    if overnight is not None:
        planned.append((overnight[0], "garage_door", "door", "open", "app"))
        planned.append((overnight[1], "garage_door", "door", "closed", "button"))

    state: dict[tuple[str, str], str] = {}
    rows: list[dict[str, Any]] = []
    for ts, device_id, prop, new, src in sorted(planned):
        old = state.get((device_id, prop), INITIAL.get(prop, "idle"))
        state[(device_id, prop)] = new
        if old == new or not start <= ts < end:
            continue
        rows.append(
            {
                "device_id": device_id,
                "property": prop,
                "old_value": old,
                "new_value": new,
                "event_ts": ts,
                "source": src,
            }
        )
    return rows
