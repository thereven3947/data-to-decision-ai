"""Mutable per-device state and the baseline operating model."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from d2d.config import DeviceSpec, Household, SettingValue

OFFLINE = "offline"
JITTER_DB = 3


def parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def in_window(t: time, start: str, end: str) -> bool:
    """True if ``t`` is in [start, end), wrapping past midnight when end <= start."""
    s, e = parse_hhmm(start), parse_hhmm(end)
    if s < e:
        return s <= t < e
    return t >= s or t < e


@dataclass
class DeviceState:
    spec: DeviceSpec
    present: bool
    room: str
    circuit: str
    network: str
    firmware_version: str
    schedule: dict[str, str] | None
    mode: str | None
    setpoint: float | None
    settings: dict[str, SettingValue]
    rssi_base: int
    energy_kwh_total: float = 0.0
    clock_skew_s: int = 0
    duplicate: bool = False
    forced_state: str | None = None
    error_code: str | None = None

    @classmethod
    def from_spec(cls, spec: DeviceSpec) -> DeviceState:
        return cls(
            spec=spec,
            present=spec.active,
            room=spec.room,
            circuit=spec.circuit,
            network=spec.network,
            firmware_version=spec.firmware_version,
            schedule=dict(spec.schedule) if spec.schedule else None,
            mode=spec.mode,
            setpoint=spec.setpoint,
            settings=dict(spec.settings),
            rssi_base=spec.base_rssi_dbm,
        )

    @property
    def id(self) -> str:
        return self.spec.id


def operate(
    state: DeviceState,
    ts: datetime,
    tz: ZoneInfo,
    cadence_minutes: int,
    rng: random.Random,
) -> tuple[str, float, int | None]:
    """Advance one cadence step; returns (state, power_w, rssi_dbm) and accumulates energy."""
    spec = state.spec
    if state.forced_state is not None:
        status = state.forced_state
        power = 0.0 if status == OFFLINE else spec.standby_w
    elif spec.always_on:
        status = "running"
        power = spec.rated_w * rng.uniform(0.6, 1.0)
    elif state.schedule and in_window(
        ts.astimezone(tz).time(), state.schedule["start"], state.schedule["end"]
    ):
        status = "running"
        power = spec.rated_w * rng.uniform(0.9, 1.1)
    else:
        status = "idle"
        power = spec.standby_w
    state.energy_kwh_total += power * cadence_minutes / 60 / 1000
    jitter = max(-JITTER_DB, min(JITTER_DB, round(rng.gauss(0, 1.5))))
    rssi = None if status == OFFLINE else state.rssi_base + jitter
    return status, round(power, 3), rssi


def snapshot_row(
    state: DeviceState,
    household: Household,
    run_id: str,
    ts: datetime,
    status: str,
    power_w: float,
    rssi_dbm: int | None,
) -> dict[str, Any]:
    return {
        "snapshot_run_id": run_id,
        "snapshot_ts": ts,
        "reported_ts": ts + timedelta(seconds=state.clock_skew_s),
        "device_id": state.id,
        "kind": state.spec.kind,
        "room": state.room,
        "circuit_id": state.circuit,
        "upstream_ids": household.upstream_ids(state.circuit, state.network),
        "firmware_version": state.firmware_version,
        "schedule": json.dumps(state.schedule, sort_keys=True) if state.schedule else None,
        "mode": state.mode,
        "setpoint": state.setpoint,
        "settings": json.dumps(state.settings, sort_keys=True) if state.settings else None,
        "power_w": power_w,
        "energy_kwh_total": round(state.energy_kwh_total, 6),
        "state": status,
        "error_code": state.error_code,
        "rssi_dbm": rssi_dbm,
        "network_id": state.network,
        "source": "simulator",
    }
