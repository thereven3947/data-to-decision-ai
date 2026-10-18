"""Stage 8: enrichment - deterministic facts and impact per category, rendered with templates."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, StrictUndefined

from d2d.config import ConfigError, Household, load_toml


@dataclass(frozen=True)
class Tariff:
    currency: str
    peak_usd_per_kwh: float
    off_peak_usd_per_kwh: float
    voltage: int


@dataclass(frozen=True)
class EnrichmentConfig:
    tariff: Tariff
    peak: tuple[str, str]
    off_peak: tuple[str, str]
    templates: dict[str, dict[str, str]]
    default: dict[str, str]
    sha: str


def load_enrichment(path: Path, semantics_path: Path) -> EnrichmentConfig:
    raw = load_toml(path)
    windows = load_toml(semantics_path).get("windows", {})
    try:
        peak, off_peak = windows["peak"], windows["off_peak"]
    except KeyError as exc:
        raise ConfigError("semantics.toml [windows] needs peak and off_peak") from exc
    t = raw["tariff"]
    return EnrichmentConfig(
        tariff=Tariff(
            currency=str(t.get("currency", "USD")),
            peak_usd_per_kwh=float(t["peak_usd_per_kwh"]),
            off_peak_usd_per_kwh=float(t["off_peak_usd_per_kwh"]),
            voltage=int(t.get("voltage", 120)),
        ),
        peak=(peak[0], peak[1]),
        off_peak=(off_peak[0], off_peak[1]),
        templates=raw.get("template", {}),
        default=raw["default"],
        sha=hashlib.sha1(path.read_bytes()).hexdigest()[:12],
    )


def display_name(device_id: str) -> str:
    return device_id.replace("_", " ").capitalize()


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_window(hhmm: str, window: tuple[str, str]) -> bool:
    t, a, b = _minutes(hhmm), _minutes(window[0]), _minutes(window[1])
    return a <= t < b if a <= b else t >= a or t < b


def window_hours(start: str, end: str) -> float:
    return ((_minutes(end) - _minutes(start)) % (24 * 60)) / 60


def _num(value: str | None) -> float | None:
    try:
        return float(value or "")
    except ValueError:
        return None


def _fmt(x: float) -> float | int:
    r = round(x, 2)
    return int(r) if r == int(r) else r


Events = list[dict[str, Any]]


def _by_device(events: Events) -> dict[str, dict[str, list[dict[str, Any]]]]:
    out: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for e in sorted(events, key=lambda e: e["event_ts"]):
        out[e["device_id"]][e["property"]].append(e)
    return out


def load_shift_facts(events: Events, household: Household, cfg: EnrichmentConfig) -> dict[str, Any]:
    shifts, kwh, usd = [], 0.0, 0.0
    price_gap = cfg.tariff.peak_usd_per_kwh - cfg.tariff.off_peak_usd_per_kwh
    for dev, props in sorted(_by_device(events).items()):
        if "schedule.start" not in props or "schedule.end" not in props:
            continue
        s, e = props["schedule.start"][0], props["schedule.end"][0]
        old_start, new_start = s["old_value"], s["new_value"]
        old_end, new_end = e["old_value"], e["new_value"]
        spec = household.devices.get(dev)
        hours = window_hours(new_start, new_end)
        dev_kwh = (spec.rated_w if spec else 0.0) * hours / 1000
        moved_off_peak = in_window(old_start, cfg.peak) and not in_window(new_start, cfg.peak)
        shifts.append(
            {
                "device": display_name(dev),
                "old_window": f"{old_start}-{old_end}",
                "new_window": f"{new_start}-{new_end}",
                "kwh": _fmt(dev_kwh),
                "moved_off_peak": moved_off_peak,
            }
        )
        if moved_off_peak:
            kwh += dev_kwh
            usd += dev_kwh * price_gap
    return {"shifts": shifts, "kwh_shifted": _fmt(kwh), "usd_per_day": _fmt(usd)}


def circuit_facts(events: Events, household: Household, cfg: EnrichmentConfig) -> dict[str, Any]:
    moves = []
    for e in events:
        if e["property"] != "circuit_id":
            continue
        spec = household.devices.get(e["device_id"])
        circuit = household.circuits.get(e["new_value"] or "")
        capacity = circuit.rating_amps * cfg.tariff.voltage if circuit else 0
        rated = spec.rated_w if spec else 0.0
        moves.append(
            {
                "device": display_name(e["device_id"]),
                "from_circuit": e["old_value"],
                "to_circuit": e["new_value"],
                "rated_w": _fmt(rated),
                "capacity_w": capacity,
                "load_pct": _fmt(100 * rated / capacity) if capacity else None,
            }
        )
    return {"moves": moves}


def _presence(events: Events, value: str) -> list[str]:
    return sorted(
        display_name(e["device_id"])
        for e in events
        if e["property"] == "presence" and e["new_value"] == value
    )


def network_facts(events: Events) -> dict[str, Any]:
    moves = [e for e in events if e["property"] == "network_id"]
    gains = [
        n - o
        for e in events
        if e["property"] == "rssi_dbm"
        and (o := _num(e["old_value"])) is not None
        and (n := _num(e["new_value"])) is not None
    ]
    return {
        "devices_moved": len({e["device_id"] for e in moves}),
        "from_network": ", ".join(sorted({str(e["old_value"]) for e in moves})) or "-",
        "to_network": ", ".join(sorted({str(e["new_value"]) for e in moves})) or "-",
        "mean_rssi_gain_db": _fmt(sum(gains) / len(gains)) if gains else 0,
    }


def setpoint_facts(events: Events) -> dict[str, Any]:
    setpoints, schedule_devices = [], set()
    for dev, props in sorted(_by_device(events).items()):
        changes = props.get("setpoint", [])
        if changes:
            first, last = changes[0], changes[-1]
            setpoints.append(
                {
                    "device": display_name(dev),
                    "from": first["old_value"],
                    "to": first["new_value"],
                    "restored": len(changes) > 1 and last["new_value"] == first["old_value"],
                }
            )
        if any(p.startswith("schedule.") for p in props):
            schedule_devices.add(display_name(dev))
    return {"setpoints": setpoints, "schedule_devices": sorted(schedule_devices)}


def fault_facts(events: Events) -> dict[str, Any]:
    return {
        "error_episodes": sum(e["semantic_tag"] == "error_raised" for e in events),
        "offline_episodes": sum(e["semantic_tag"] == "device_offline" for e in events),
        "battery_low": any(e["semantic_tag"] == "battery_low" for e in events),
        "error_codes": sorted(
            {str(e["new_value"]) for e in events if e["semantic_tag"] == "error_raised"}
        ),
    }


DQ_ISSUES = {
    "cdc_gap": "event feed out of sync",
    "negative_energy_delta": "negative energy delta",
    "clock_skew": "clock skew",
    "duplicate_device": "duplicate device",
}


def _issue(prop: str) -> str:
    key = prop.removeprefix("dq.")
    return DQ_ISSUES.get(key, key.replace("_", " "))


def enrichment_facts(
    category: str | None, events: Events, household: Household, cfg: EnrichmentConfig
) -> dict[str, Any]:
    versions = [e for e in events if e["property"] == "firmware_version"]
    match category:
        case "load_shift":
            return load_shift_facts(events, household, cfg)
        case "circuit_reassignment":
            return circuit_facts(events, household, cfg)
        case "network_change":
            return network_facts(events)
        case "weather_dr_response":
            return setpoint_facts(events)
        case "fault_degradation":
            return fault_facts(events)
        case "firmware_rollout":
            return {
                "from_version": ", ".join(sorted({str(e["old_value"]) for e in versions})),
                "to_version": ", ".join(sorted({str(e["new_value"]) for e in versions})),
            }
        case "device_replacement" | "device_added_removed":
            return {"added": _presence(events, "present"), "removed": _presence(events, "absent")}
        case "safety_setting_change":
            return {
                "settings": [
                    {
                        "device": display_name(e["device_id"]),
                        "setting": e["property"].removeprefix("settings."),
                        "from": e["old_value"],
                        "to": e["new_value"],
                    }
                    for e in events
                    if e["property"].startswith("settings.")
                ]
            }
        case "data_error":
            dq = [e for e in events if e["property"].startswith("dq.")]
            return {
                "issues": sorted({_issue(e["property"]) for e in dq}),
                "details": [
                    {
                        "device": display_name(e["device_id"]),
                        "issue": _issue(e["property"]),
                        "value": e["new_value"],
                    }
                    for e in dq
                ],
            }
        case "access_left_open":
            held = [e for e in events if e["property"].endswith(".duration")]
            return {
                "state": held[0]["new_value"] if held else "open",
                "quiet_hours": any(
                    e["semantic_tag"] == "garage_opened_quiet_hours" for e in events
                ),
            }
        case "security_event":
            return {"signals": sorted({e["property"] for e in events})}
        case "visitor_activity":
            return {
                "package": any(e["semantic_tag"] == "package_delivered" for e in events),
                "missed": any(e["semantic_tag"] == "missed_visitor" for e in events),
            }
        case "lighting_change":
            return {
                "left_on": any(e["semantic_tag"] == "lights_left_on_away" for e in events),
                "schedule": [
                    {
                        "device": display_name(e["device_id"]),
                        "field": e["property"].removeprefix("schedule."),
                        "from": e["old_value"],
                        "to": e["new_value"],
                    }
                    for e in events
                    if e["property"].startswith("schedule.")
                ],
            }
    return {}


_ENV = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=False)


def _local(ts: datetime, tz: str) -> str:
    local = ts.astimezone(ZoneInfo(tz))
    return f"{local:%a %b} {local.day} {local:%H:%M}"


def enrich(
    category: str | None,
    events: Events,
    household: Household,
    cfg: EnrichmentConfig,
) -> dict[str, Any]:
    """Title, summary and facts for a story. Falls back to the default template on any gap."""
    devices = sorted({display_name(e["device_id"]) for e in events})
    ordered = sorted(events, key=lambda e: e["event_ts"])
    context: dict[str, Any] = {
        "devices": devices,
        "device_count": len(devices),
        "event_count": len(events),
        "start": _local(ordered[0]["event_ts"], household.timezone) if ordered else "-",
        "end": _local(ordered[-1]["event_ts"], household.timezone) if ordered else "-",
    }
    facts = enrichment_facts(category, events, household, cfg)
    template = cfg.templates.get(category or "", cfg.default)
    try:
        title = _ENV.from_string(template["title"]).render(**context, facts=facts)
        summary = _ENV.from_string(template["summary"]).render(**context, facts=facts)
    except Exception:
        title = _ENV.from_string(cfg.default["title"]).render(**context)
        summary = _ENV.from_string(cfg.default["summary"]).render(**context)
    return {
        "title": title.strip(),
        "summary": summary.strip(),
        "facts": facts,
        "enrichment_template_sha": cfg.sha,
    }
