"""Stage 1 CDC stream: second-precision property changes of event-native devices (M5).

Same shape as a change event: ``device_id, property, old_value, new_value, event_ts, source``
(Home Assistant ``state_changed`` carries old and new state). Momentary signals (doorbell press,
motion) are pulses: ``idle -> pressed -> idle``.

The stream is a routine baseline plus planted M5 scenarios with ground truth (see
``docs/feature-roadmap.md#m5--event-native-devices-and-notifications``). The household-level
``away_mode`` signal comes through the same stream from the pseudo-device ``home`` (like a Home
Assistant ``input_boolean``); it is context only and never becomes a change event itself.

``true_rows`` is what really happened; ``rows`` is what the CDC feed delivered. They differ only
where a scenario drops a row (``cdc_dropped_event``), which the snapshot reconciliation
(``dq.cdc_gap``, M5-06) must detect because snapshots mirror the true state.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from d2d.config import Household

CDC_KINDS = frozenset({"light_switch", "garage_door", "ring_doorbell", "ring_camera"})
HOME = "home"
INITIAL = {
    "door": "closed",
    "power": "off",
    "motion": "clear",
    "person": "clear",
    "button": "idle",
    "call": "idle",
    "package": "clear",
    "battery": "ok",
    "online": "online",
    "away_mode": "off",
}
# Snapshot ``state`` of CDC devices mirrors the true CDC state of one property (M5-06).
MIRRORED: dict[str, tuple[str, dict[str, str]]] = {
    "light_switch": ("power", {"on": "running", "off": "idle"}),
    "garage_door": ("door", {"open": "running", "closed": "idle"}),
}


def _at(day: datetime, hhmm: str, jitter_min: float, rng: random.Random) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    base = day.replace(hour=h, minute=m, second=0)
    return base + timedelta(seconds=round(rng.uniform(-jitter_min, jitter_min) * 60))


Planned = tuple[datetime, str, str, str]  # (ts, property, new_value, source)


def _pulse(prop: str, on: str, off: str, ts: datetime, hold_s: int, src: str) -> list[Planned]:
    return [(ts, prop, on, src), (ts + timedelta(seconds=hold_s), prop, off, src)]


def _routine(
    kind: str, day: datetime, schedule: dict[str, str] | None, rng: random.Random
) -> list[list[Planned]]:
    """Routine activity as groups (a visit, a motion burst); groups are kept or dropped whole."""
    out: list[list[Planned]] = []
    if kind == "light_switch":
        on, off, src = (
            (schedule["start"], schedule["end"], "automation")
            if schedule
            else ("19:00", "22:30", "manual")
        )
        jitter = 10 if schedule else 30
        out += [[(_at(day, on, jitter, rng), "power", "on", src)]]
        out += [[(_at(day, off, jitter, rng), "power", "off", src)]]
    elif kind == "garage_door":
        for hhmm, jitter in (("08:00", 15), ("17:45", 20)):
            opened = _at(day, hhmm, jitter, rng)
            out += [_pulse("door", "open", "closed", opened, rng.randint(120, 300), "remote")]
    elif kind == "ring_doorbell":
        for _ in range(rng.randint(0, 2)):
            ts = _at(day, "13:30", 210, rng)
            out += [
                _pulse("motion", "detected", "clear", ts - timedelta(seconds=8), 30, "device")
                + _pulse("button", "pressed", "idle", ts, 1, "device")
                + _pulse("call", "answered", "idle", ts + timedelta(seconds=40), 30, "app")
            ]
    elif kind == "ring_camera":
        for _ in range(rng.randint(3, 6)):
            ts = _at(day, "13:00", 240, rng)
            group = _pulse("motion", "detected", "clear", ts, 30, "device")
            if rng.random() < 0.5:
                group += _pulse(
                    "person", "detected", "clear", ts + timedelta(seconds=5), 20, "device"
                )
            out += [group]
    return out


@dataclass
class CdcPlan:
    true_rows: list[dict[str, Any]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    truth: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Scenario:
    scenario_id: str
    category: str
    planned: list[tuple[datetime, str, str, str, str]]  # ts, device, property, new, source
    events: list[tuple[str, str, datetime]]  # device, property, ts of expected change events
    quiet: list[tuple[frozenset[str], datetime, datetime]] = field(default_factory=list)


def _scenarios(household: Household, first_day: datetime) -> list[_Scenario]:
    """Planted M5 scenarios at fixed local times (day 0 = local date of the simulation start)."""

    def t(day: int, hhmm: str, second: int = 0) -> datetime:
        h, m = (int(x) for x in hhmm.split(":"))
        return (first_day + timedelta(days=day)).replace(hour=h, minute=m, second=second)

    s: list[_Scenario] = []
    devices = household.devices
    callers = frozenset({"ring_doorbell", "ring_camera"})
    if {"ring_doorbell", "ring_camera_front"} <= devices.keys():
        arrive = t(1, "11:20")
        s.append(
            _Scenario(
                "package_delivery",
                "visitor_activity",
                [
                    (
                        arrive - timedelta(seconds=10),
                        "ring_doorbell",
                        "motion",
                        "detected",
                        "device",
                    ),
                    (arrive + timedelta(seconds=20), "ring_doorbell", "motion", "clear", "device"),
                    (arrive, "ring_camera_front", "person", "detected", "device"),
                    (
                        arrive + timedelta(seconds=20),
                        "ring_camera_front",
                        "person",
                        "clear",
                        "device",
                    ),
                    (
                        arrive + timedelta(seconds=30),
                        "ring_doorbell",
                        "package",
                        "detected",
                        "device",
                    ),
                    (arrive + timedelta(hours=2), "ring_doorbell", "package", "clear", "app"),
                ],
                [("ring_doorbell", "package", arrive + timedelta(seconds=30))],
                [(callers, arrive - timedelta(minutes=30), arrive + timedelta(minutes=30))],
            )
        )
        ring = t(3, "10:20")
        s.append(
            _Scenario(
                "missed_visitor",
                "visitor_activity",
                [
                    (ring - timedelta(seconds=10), "ring_doorbell", "motion", "detected", "device"),
                    (ring + timedelta(seconds=20), "ring_doorbell", "motion", "clear", "device"),
                    (ring, "ring_doorbell", "button", "pressed", "device"),
                    (ring + timedelta(seconds=1), "ring_doorbell", "button", "idle", "device"),
                    (
                        ring + timedelta(seconds=10),
                        "ring_camera_front",
                        "person",
                        "detected",
                        "device",
                    ),
                    (
                        ring + timedelta(seconds=30),
                        "ring_camera_front",
                        "person",
                        "clear",
                        "device",
                    ),
                ],
                [("ring_doorbell", "visit", ring + timedelta(seconds=120))],
                [(callers, ring - timedelta(minutes=30), ring + timedelta(minutes=30))],
            )
        )
        battery = t(2, "07:30")
        s.append(
            _Scenario(
                "doorbell_battery_low",
                "fault_degradation",
                [(battery, "ring_doorbell", "battery", "low", "device")],
                [("ring_doorbell", "battery", battery)],
            )
        )
        off, on = t(3, "01:10"), t(3, "03:40")
        s.append(
            _Scenario(
                "camera_offline",
                "fault_degradation",
                [
                    (off, "ring_camera_front", "online", "offline", "device"),
                    (on, "ring_camera_front", "online", "online", "device"),
                ],
                [("ring_camera_front", "online", off), ("ring_camera_front", "online", on)],
            )
        )
    if "garage_door" in devices:
        opened, closed = t(1, "22:15"), t(2, "06:30")
        s.append(
            _Scenario(
                "garage_left_open_overnight",
                "access_left_open",
                [
                    (opened, "garage_door", "door", "open", "app"),
                    (closed, "garage_door", "door", "closed", "button"),
                ],
                [
                    ("garage_door", "door", opened),  # opened during quiet hours
                    ("garage_door", "door.duration", opened + timedelta(minutes=30)),
                ],
            )
        )
    away_on, away_off = t(2, "08:00"), t(2, "17:30")
    away = [
        (away_on, HOME, "away_mode", "on", "app"),
        (away_off, HOME, "away_mode", "off", "app"),
    ]
    if "light_living" in devices:
        s.append(
            _Scenario(
                "lights_left_on_away",
                "lighting_change",
                [
                    (t(2, "07:50"), "light_living", "power", "on", "manual"),
                    (t(2, "17:45"), "light_living", "power", "off", "manual"),
                    *away,
                ],
                [("light_living", "power.duration", away_on + timedelta(hours=4))],
                [(callers, away_on, away_off)],
            )
        )
        away = []
    if "ring_camera_front" in devices:
        seen = t(2, "15:10")
        s.append(
            _Scenario(
                "motion_while_away",
                "security_event",
                [
                    (seen, "ring_camera_front", "motion", "detected", "device"),
                    (
                        seen + timedelta(seconds=30),
                        "ring_camera_front",
                        "motion",
                        "clear",
                        "device",
                    ),
                    *away,
                ],
                [("ring_camera_front", "motion", seen)],
                [(callers, away_on, away_off)],
            )
        )
    return s


def _first_local_day(household: Household, start: datetime) -> datetime:
    tz = ZoneInfo(household.timezone)
    return start.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)


def plan_cdc(
    household: Household, *, start: datetime, days: int, seed: int, cadence_minutes: int = 15
) -> CdcPlan:
    """Routine + planted CDC rows (UTC, sorted) and the M5 ground truth. Own RNG: snapshot
    generation is unaffected."""
    rng = random.Random(f"cdc-{seed}")
    end = start + timedelta(days=days)
    first_day = _first_local_day(household, start)
    scenarios = [
        sc
        for sc in _scenarios(household, first_day)
        if all(start <= ts < end for _, _, ts in sc.events)
    ]
    quiet = [q for sc in scenarios for q in sc.quiet]
    kinds = {d.id: d.kind for d in household.devices.values()}

    planned: list[tuple[datetime, str, str, str, str, str]] = []
    for d in range(days + 1):
        day = first_day + timedelta(days=d)
        for spec in sorted(household.devices.values(), key=lambda s: s.id):
            if spec.kind in CDC_KINDS and spec.active:
                for group in _routine(spec.kind, day, spec.schedule, rng):
                    stamps = [ts.astimezone(start.tzinfo) for ts, *_ in group]
                    lo, hi = min(stamps), max(stamps)
                    # Planted scenarios get a quiet stage: no routine visitors / motion.
                    if any(spec.kind in k and lo <= b and hi >= a for k, a, b in quiet):
                        continue
                    for ts, (_, prop, new, src) in zip(stamps, group, strict=True):
                        planned.append((ts, spec.id, prop, new, src, f"routine:{d}"))
    for sc in scenarios:
        for ts, dev, prop, new, src in sc.planned:
            planned.append((ts.astimezone(start.tzinfo), dev, prop, new, src, sc.scenario_id))

    # M5-06: the feed drops the porch light's routine "off" on day 2; snapshots still see it.
    drop = ("light_porch", "power", "off", "routine:2") if "light_porch" in kinds else None

    plan = CdcPlan()
    state: dict[tuple[str, str], str] = {}
    dropped_at: datetime | None = None
    for ts, device_id, prop, new, src, origin in sorted(planned):
        old = state.get((device_id, prop), INITIAL.get(prop, "idle"))
        state[(device_id, prop)] = new
        if old == new or not start <= ts < end:
            continue
        row = {
            "device_id": device_id,
            "property": prop,
            "old_value": old,
            "new_value": new,
            "event_ts": ts,
            "source": src,
        }
        plan.true_rows.append(row)
        if drop is not None and (device_id, prop, new, origin) == drop and dropped_at is None:
            dropped_at = ts
            continue
        plan.rows.append(row)

    for sc in scenarios:
        events = [
            {"device_id": dev, "property": prop, "ts": ts.astimezone(start.tzinfo).isoformat()}
            for dev, prop, ts in sc.events
        ]
        iso = sorted(str(e["ts"]) for e in events)
        plan.truth.append(
            {
                "scenario_id": sc.scenario_id,
                "category": sc.category,
                "events": events,
                "start": iso[0],
                "end": iso[-1],
            }
        )
    if dropped_at is not None:
        step = timedelta(minutes=cadence_minutes)
        k = -(-(dropped_at - start) // step)  # first snapshot at or after the real change
        seen_at = (start + k * step).isoformat()
        plan.truth.append(
            {
                "scenario_id": "cdc_dropped_event",
                "category": "data_error",
                "events": [{"device_id": "light_porch", "property": "dq.cdc_gap", "ts": seen_at}],
                "start": seen_at,
                "end": seen_at,
            }
        )
    return plan


def generate_cdc(
    household: Household, *, start: datetime, days: int, seed: int
) -> list[dict[str, Any]]:
    """The delivered CDC feed (routine + planted, with the planted gap)."""
    return plan_cdc(household, start=start, days=days, seed=seed).rows


def cdc_ground_truth(
    household: Household, *, start: datetime, days: int, seed: int = 7, cadence_minutes: int = 15
) -> list[dict[str, Any]]:
    return plan_cdc(
        household, start=start, days=days, seed=seed, cadence_minutes=cadence_minutes
    ).truth


def mirror_cdc_state(
    snapshot_rows: list[dict[str, Any]], true_rows: list[dict[str, Any]], household: Household
) -> None:
    """Set the snapshot ``state`` of mirrored CDC devices to their true CDC state (in place)."""
    by_device: dict[str, list[tuple[datetime, str]]] = {}
    for r in true_rows:
        spec = household.devices.get(r["device_id"])
        if spec and spec.kind in MIRRORED and r["property"] == MIRRORED[spec.kind][0]:
            by_device.setdefault(r["device_id"], []).append((r["event_ts"], r["new_value"]))
    for row in snapshot_rows:
        spec = household.devices.get(row["device_id"])
        if spec is None or spec.kind not in MIRRORED or row.get("state") in ("error", "offline"):
            continue
        prop, mapping = MIRRORED[spec.kind]
        value = INITIAL.get(prop, "off")
        for ts, new in by_device.get(row["device_id"], []):
            if ts > row["snapshot_ts"]:
                break
            value = new
        row["state"] = mapping.get(value, row["state"])
