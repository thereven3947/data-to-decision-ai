"""Simulation loop: baseline operation + scenario injection -> snapshot rows + ground truth."""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from d2d.config import Household
from d2d.simulator.devices import DeviceState, operate, snapshot_row
from d2d.simulator.scenarios import Scenario, build_scenarios


@dataclass(frozen=True)
class SimulationResult:
    rows: list[dict[str, Any]]
    ground_truth: list[dict[str, Any]]
    runs: int


def run_id_for(ts: datetime) -> str:
    """Deterministic, sortable run id so a seeded simulation is exactly replayable."""
    return f"run_{ts:%Y%m%dT%H%M%SZ}"


def simulate(
    household: Household,
    *,
    start: datetime,
    days: int,
    cadence_minutes: int,
    seed: int,
    scenario_ids: set[str] | None = None,
    scenarios: list[Scenario] | None = None,
) -> SimulationResult:
    rng = random.Random(seed)
    tz = ZoneInfo(household.timezone)
    steps_per_hour = 60 // cadence_minutes
    total_steps = days * 24 * steps_per_hour
    plan = scenarios if scenarios is not None else build_scenarios(steps_per_hour)
    if scenario_ids is not None:
        unknown = scenario_ids - {s.id for s in plan}
        if unknown:
            raise ValueError(f"unknown scenario(s): {sorted(unknown)}")
        plan = [s for s in plan if s.id in scenario_ids]

    actions_by_step: dict[int, list[tuple[Scenario, Any]]] = defaultdict(list)
    for scenario in plan:
        base = round(scenario.start_hours * steps_per_hour)
        for offset, action in scenario.actions:
            if base + offset < total_steps:
                actions_by_step[base + offset].append((scenario, action))

    devices = {d.id: DeviceState.from_spec(d) for d in household.devices.values()}
    rows: list[dict[str, Any]] = []
    truth: dict[str, dict[str, Any]] = {}

    for step in range(total_steps):
        ts = start + timedelta(minutes=step * cadence_minutes)
        run_id = run_id_for(ts)
        for scenario, action in actions_by_step.get(step, []):
            keys = action(devices)
            if not keys:
                continue
            entry = truth.setdefault(
                scenario.id,
                {"scenario_id": scenario.id, "category": scenario.category, "events": []},
            )
            entry["events"] += [
                {"device_id": dev, "property": prop, "ts": ts.isoformat()} for dev, prop in keys
            ]
        for state in sorted(devices.values(), key=lambda d: d.id):
            if not state.present:
                continue
            status, power, rssi = operate(state, ts, tz, cadence_minutes, rng)
            row = snapshot_row(state, household, run_id, ts, status, power, rssi)
            rows.append(row)
            if state.duplicate:
                rows.append(dict(row))

    ground_truth = []
    for entry in truth.values():
        stamps = [e["ts"] for e in entry["events"]]
        ground_truth.append({**entry, "start": min(stamps), "end": max(stamps)})
    return SimulationResult(rows=rows, ground_truth=ground_truth, runs=total_steps)
