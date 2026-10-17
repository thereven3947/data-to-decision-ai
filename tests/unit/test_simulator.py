from __future__ import annotations

from collections import Counter
from datetime import time

from d2d.config import Household, PipelineConfig
from d2d.simulator import SimulationResult, simulate
from d2d.simulator.devices import in_window


def _run(household: Household, pipeline: PipelineConfig, **kw: object) -> SimulationResult:
    args: dict[str, object] = {
        "start": pipeline.start,
        "days": pipeline.days,
        "cadence_minutes": pipeline.cadence_minutes,
        "seed": pipeline.seed,
    }
    args.update(kw)
    return simulate(household, **args)  # type: ignore[arg-type]


def test_in_window_wraps_midnight() -> None:
    assert in_window(time(23, 0), "22:00", "06:00")
    assert in_window(time(5, 59), "22:00", "06:00")
    assert not in_window(time(6, 0), "22:00", "06:00")
    assert in_window(time(18, 15), "18:00", "19:30")


def test_simulation_is_deterministic(household: Household, pipeline: PipelineConfig) -> None:
    a = _run(household, pipeline, days=1)
    b = _run(household, pipeline, days=1)
    assert a.rows == b.rows
    assert a.ground_truth == b.ground_truth


def test_baseline_without_scenarios_has_no_config_drift(
    household: Household, pipeline: PipelineConfig
) -> None:
    result = _run(household, pipeline, scenario_ids=set())
    assert result.ground_truth == []
    per_device = {(r["device_id"], r["firmware_version"], r["circuit_id"]) for r in result.rows}
    assert len(per_device) == sum(1 for d in household.devices.values() if d.active)
    # Energy counters never decrease without a scenario.
    last: dict[str, float] = {}
    for r in result.rows:
        assert r["energy_kwh_total"] >= last.get(r["device_id"], 0.0)
        last[r["device_id"]] = r["energy_kwh_total"]
    # Scheduled appliances actually run.
    assert any(r["device_id"] == "dishwasher" and r["state"] == "running" for r in result.rows)


def test_all_default_scenarios_are_injected(household: Household, pipeline: PipelineConfig) -> None:
    result = _run(household, pipeline)
    ids = {s["scenario_id"] for s in result.ground_truth}
    assert ids == {
        "lighting_automation_changed",
        "firmware_rollout",
        "load_shift_to_off_peak",
        "ev_charger_moved_circuit",
        "dishwasher_replaced",
        "router_swap",
        "heat_wave_dr",
        "child_lock_disabled",
        "washer_bearing_degradation",
        "clock_drift",
        "duplicate_device",
        "new_smart_plug",
        "meter_reset",
    }
    for s in result.ground_truth:
        assert s["events"], s["scenario_id"]
        assert s["start"] <= s["end"]


def test_scenario_effects_are_visible_in_snapshots(
    household: Household, pipeline: PipelineConfig
) -> None:
    result = _run(household, pipeline)
    rows = result.rows
    final_run = rows[-1]["snapshot_run_id"]
    final = {r["device_id"]: r for r in rows if r["snapshot_run_id"] == final_run}

    assert "dishwasher" not in final and "dishwasher_2" in final
    assert "smart_plug_office" in final
    assert final["ev_charger"]["circuit_id"] == "circuit_utility"
    assert final["thermostat"]["firmware_version"] == "1.3.0"
    assert final["washer"]["network_id"] == "router_mesh_3"
    assert '"child_lock": false' in final["washer"]["settings"]

    dupes = Counter((r["snapshot_run_id"], r["device_id"]) for r in rows)
    assert {dev for (_, dev), n in dupes.items() if n > 1} == {"smart_plug_lamp"}
    assert any(r["device_id"] == "washer" and r["error_code"] == "E21" for r in rows)
    skewed = [r for r in rows if r["reported_ts"] != r["snapshot_ts"]]
    assert skewed and {r["device_id"] for r in skewed} == {"thermostat"}
