"""Scenario injectors: planted, labeled changes that give every stage a ground truth.

Each scenario is a list of actions at step offsets. An action mutates device state and returns
the ``(device_id, property)`` keys it changed; the engine records them with the snapshot
timestamp at which the change becomes visible.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from d2d.simulator.devices import OFFLINE, DeviceState

Key = tuple[str, str]
Devices = dict[str, DeviceState]
Action = Callable[[Devices], list[Key]]


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    start_hours: float
    actions: tuple[tuple[int, Action], ...]


def _present(devices: Devices, device_id: str) -> DeviceState | None:
    d = devices.get(device_id)
    return d if d is not None and d.present else None


def _set_schedule(device_id: str, start: str, end: str) -> Action:
    def act(devices: Devices) -> list[Key]:
        d = _present(devices, device_id)
        if d is None or d.schedule is None:
            return []
        keys: list[Key] = []
        for name, value in (("start", start), ("end", end)):
            if d.schedule.get(name) != value:
                d.schedule[name] = value
                keys.append((device_id, f"schedule.{name}"))
        return keys

    return act


def _set_schedule_start(device_id: str, start: str) -> Action:
    def act(devices: Devices) -> list[Key]:
        d = _present(devices, device_id)
        if d is None or not d.schedule:
            return []
        d.schedule["start"] = start
        return [(device_id, "schedule.start")]

    return act


def _set_attr(device_id: str, attr: str, value: object, prop: str | None = None) -> Action:
    def act(devices: Devices) -> list[Key]:
        d = _present(devices, device_id)
        if d is None or getattr(d, attr) == value:
            return []
        setattr(d, attr, value)
        return [(device_id, prop or attr)]

    return act


def _set_setting(device_id: str, name: str, value: bool | int | float | str) -> Action:
    def act(devices: Devices) -> list[Key]:
        d = _present(devices, device_id)
        if d is None or d.settings.get(name) == value:
            return []
        d.settings[name] = value
        return [(device_id, f"settings.{name}")]

    return act


def _set_presence(device_id: str, present: bool) -> Action:
    def act(devices: Devices) -> list[Key]:
        d = devices[device_id]
        if d.present == present:
            return []
        d.present = present
        return [(device_id, "presence")]

    return act


def _fault(device_id: str, forced_state: str | None, error_code: str | None) -> Action:
    def act(devices: Devices) -> list[Key]:
        d = _present(devices, device_id)
        if d is None:
            return []
        keys: list[Key] = [(device_id, "state")]
        if d.error_code != error_code:
            keys.append((device_id, "error_code"))
        d.forced_state = forced_state
        d.error_code = error_code
        return keys

    return act


def _firmware_batch(network: str, version: str) -> Action:
    def act(devices: Devices) -> list[Key]:
        pending = sorted(
            d.id
            for d in devices.values()
            if d.present and d.network == network and d.firmware_version != version
        )
        if not pending:
            return []
        devices[pending[0]].firmware_version = version
        return [(pending[0], "firmware_version")]

    return act


def _router_swap(old: str, new: str, rssi_gain_db: int) -> Action:
    def act(devices: Devices) -> list[Key]:
        keys: list[Key] = []
        for d in sorted(devices.values(), key=lambda x: x.id):
            if d.present and d.network == old:
                d.network = new
                d.rssi_base += rssi_gain_db
                keys += [(d.id, "network_id")]
                if d.forced_state != OFFLINE:
                    keys.append((d.id, "rssi_dbm"))
        return keys

    return act


def _clock_skew(device_id: str, seconds: int) -> Action:
    def act(devices: Devices) -> list[Key]:
        d = _present(devices, device_id)
        if d is None:
            return []
        d.clock_skew_s = seconds
        return [(device_id, "dq.clock_skew")]

    return act


def _duplicate(device_id: str, on: bool) -> Action:
    def act(devices: Devices) -> list[Key]:
        d = _present(devices, device_id)
        if d is None:
            return []
        d.duplicate = on
        return [(device_id, "dq.duplicate_device")] if on else []

    return act


def _meter_reset(device_id: str) -> Action:
    def act(devices: Devices) -> list[Key]:
        d = _present(devices, device_id)
        if d is None or d.energy_kwh_total <= 0:
            return []
        d.energy_kwh_total = 0.0
        return [(device_id, "dq.negative_energy_delta")]

    return act


def build_scenarios(steps_per_hour: int) -> list[Scenario]:
    """Default scenario plan for a 3-day simulation (times are hours from the start)."""
    h = steps_per_hour

    def seq(*actions: tuple[int, Action]) -> tuple[tuple[int, Action], ...]:
        return tuple(actions)

    return [
        Scenario(
            "firmware_rollout",
            "firmware_rollout",
            10,
            seq(*((i * h, _firmware_batch("hub_zigbee", "1.3.0")) for i in range(3))),
        ),
        Scenario(
            "load_shift_to_off_peak",
            "load_shift",
            14,
            seq(
                (0, _set_schedule("dishwasher", "02:00", "03:30")),
                (1, _set_schedule("washer", "01:00", "02:00")),
            ),
        ),
        Scenario(
            "ev_charger_moved_circuit",
            "circuit_reassignment",
            20,
            seq((0, _set_attr("ev_charger", "circuit", "circuit_utility", "circuit_id"))),
        ),
        Scenario(
            "dishwasher_replaced",
            "device_replacement",
            26,
            seq((0, _set_presence("dishwasher", False)), (1, _set_presence("dishwasher_2", True))),
        ),
        Scenario(
            "router_swap",
            "network_change",
            30,
            seq((0, _router_swap("router_mesh_1", "router_mesh_3", 20))),
        ),
        Scenario(
            "heat_wave_dr",
            "weather_dr_response",
            36,
            seq(
                (0, _set_attr("thermostat", "setpoint", 25.0)),
                (0, _set_attr("thermostat", "mode", "eco")),
                (1, _set_attr("water_heater", "setpoint", 50.0)),
                (1, _set_schedule("ev_charger", "23:00", "03:00")),
                (6 * h, _set_attr("thermostat", "setpoint", 22.0)),
                (6 * h, _set_attr("thermostat", "mode", "cool")),
                (6 * h + 1, _set_attr("water_heater", "setpoint", 55.0)),
            ),
        ),
        Scenario(
            "child_lock_disabled",
            "safety_setting_change",
            44,
            seq(
                (0, _set_setting("washer", "child_lock", False)),
                (1, _set_setting("water_heater", "max_temp_c", 70)),
            ),
        ),
        Scenario(
            "washer_bearing_degradation",
            "fault_degradation",
            50,
            seq(
                (0, _fault("washer", "error", "E21")),
                (1, _fault("washer", None, None)),
                (8 * h, _fault("washer", "error", "E21")),
                (8 * h + 1, _fault("washer", None, None)),
                (14 * h, _fault("washer", OFFLINE, None)),
                (14 * h + 1, _fault("washer", None, None)),
            ),
        ),
        Scenario("clock_drift", "data_error", 52, seq((0, _clock_skew("thermostat", 420)))),
        Scenario(
            "duplicate_device",
            "data_error",
            56,
            seq(
                (0, _duplicate("smart_plug_lamp", True)), (2, _duplicate("smart_plug_lamp", False))
            ),
        ),
        Scenario(
            "new_smart_plug",
            "device_added_removed",
            60,
            seq((0, _set_presence("smart_plug_office", True))),
        ),
        Scenario("meter_reset", "data_error", 66, seq((0, _meter_reset("smart_plug_tv")))),
        # M5-04: the porch light automation is moved earlier (snapshot config, not CDC).
        Scenario(
            "lighting_automation_changed",
            "lighting_change",
            49,
            seq((2, _set_schedule_start("light_porch", "17:45"))),
        ),
    ]
