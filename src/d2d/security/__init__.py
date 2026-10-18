"""Configuration-change anomaly detection: unexpected or unauthorized changes (OT security view).

The household inventory (``household.toml``) is the approved configuration baseline, like an asset
inventory / CMDB for a control system. Every config or topology change event is scored by:

* baseline rules - firmware downgrade, a network or circuit that is not in the inventory, a value
  outside the range this device has ever reported;
* context - off-hours, a lone change (no other device changed with it), a rare (device, property);
* an unsupervised Isolation Forest over the same numeric features.

``evaluate_security`` measures the detector by injecting simulated attacks (red-team events) into
a real event stream; the simulator and its ground truth are not touched. See
docs/security-analytics.md.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.ensemble import IsolationForest

from d2d.config import Household

SCORED_KINDS = frozenset({"config", "topology"})
OFF_HOURS = range(0, 5)  # local hour, 00:00-04:59
LONE_WINDOW = timedelta(minutes=30)
CAMPAIGN_WINDOW = timedelta(hours=3)
RANGE_TOLERANCE = 0.25  # a value may move 25% beyond its approved/observed range
CONTINUOUS_LOAD = 0.8  # NEC 80% rule for continuous loads
ALERT_THRESHOLD = 0.5

# Weights add up independent evidence; the score is clipped to [0, 1].
WEIGHTS = {
    "firmware_downgrade": 0.6,
    "unknown_network": 0.6,
    "unknown_circuit": 0.6,
    "out_of_range": 0.5,
    "undersized_circuit": 0.4,
    "off_hours": 0.15,
    "lone_change": 0.1,
    "rare_property": 0.1,
    "campaign": -0.3,
}
ISOLATION_WEIGHT = 0.25

ATTACKS = (
    "firmware_downgrade",
    "rogue_network",
    "setpoint_manipulation",
    "safety_limit_raised",
    "unscheduled_circuit_move",
)


@dataclass(frozen=True)
class Baseline:
    networks: frozenset[str]
    circuits: dict[str, float]  # circuit id -> continuous capacity in watts
    loads: dict[str, float]  # device id -> rated watts
    approved: dict[tuple[str, str], float]  # (device, property) -> approved numeric value


def baseline(household: Household, voltage: float = 120.0) -> Baseline:
    """The approved configuration: inventory networks, circuits, loads and numeric settings."""
    approved: dict[tuple[str, str], float] = {}
    for d in household.devices.values():
        if d.setpoint is not None:
            approved[(d.id, "setpoint")] = float(d.setpoint)
        for key, value in d.settings.items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                approved[(d.id, f"settings.{key}")] = float(value)
    return Baseline(
        networks=frozenset(household.upstreams),
        circuits={
            c.id: c.rating_amps * voltage * CONTINUOUS_LOAD for c in household.circuits.values()
        },
        loads={d.id: d.rated_w for d in household.devices.values()},
        approved=approved,
    )


@dataclass
class ScoredEvent:
    event_id: str
    device_id: str
    property: str
    old_value: str | None
    new_value: str | None
    event_ts: datetime
    score: float
    alert: bool
    reasons: list[str] = field(default_factory=list)


def read_events(data_dir: Path) -> list[dict[str, Any]]:
    path = data_dir / "events" / "events.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run `d2d run` first")
    rows: list[dict[str, Any]] = pq.read_table(path).to_pylist()
    return rows


def _semver(value: str | None) -> tuple[int, ...] | None:
    if not value:
        return None
    try:
        return tuple(int(p) for p in value.split("."))
    except ValueError:
        return None


def _number(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


def _history(events: list[dict[str, Any]]) -> dict[tuple[str, str], list[tuple[str, float]]]:
    """Numeric values each (device, property) has reported, with the event that reported them."""
    out: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for e in events:
        for v in (e["old_value"], e["new_value"]):
            n = _number(v)
            if n is not None:
                out.setdefault((e["device_id"], e["property"]), []).append((e["event_id"], n))
    return out


def _rules(
    e: dict[str, Any],
    base: Baseline,
    history: dict[tuple[str, str], list[tuple[str, float]]],
    others: list[dict[str, Any]],
    prop_counts: Counter[tuple[str, str]],
    tz: ZoneInfo,
) -> list[str]:
    reasons: list[str] = []
    device, prop, old, new = e["device_id"], e["property"], e["old_value"], e["new_value"]
    if prop == "firmware_version":
        old_v, new_v = _semver(old), _semver(new)
        if old_v and new_v and new_v < old_v:
            reasons.append("firmware_downgrade")
    if prop == "network_id" and new and new not in base.networks:
        reasons.append("unknown_network")
    if prop == "circuit_id" and new:
        if new not in base.circuits:
            reasons.append("unknown_circuit")
        elif base.loads.get(device, 0.0) > base.circuits[new] and base.circuits[
            new
        ] < base.circuits.get(old or "", 0.0):
            reasons.append("undersized_circuit")
    n = _number(new)
    if n is not None:
        seen = [v for eid, v in history.get((device, prop), []) if eid != e["event_id"]]
        if (device, prop) in base.approved:
            seen.append(base.approved[(device, prop)])
        if seen:
            lo, hi = min(seen), max(seen)
            margin = max(abs(hi), abs(lo)) * RANGE_TOLERANCE
            if n > hi + margin or n < lo - margin:
                reasons.append("out_of_range")
    ts = e["event_ts"]
    campaign = any(
        o["device_id"] != device
        and o["property"] == prop
        and o["new_value"] == new
        and abs(o["event_ts"] - ts) <= CAMPAIGN_WINDOW
        for o in others
    )
    if campaign:
        reasons.append("campaign")
    elif ts.astimezone(tz).hour in OFF_HOURS:
        reasons.append("off_hours")
    if not campaign and not any(
        o["device_id"] != device and abs(o["event_ts"] - ts) <= LONE_WINDOW for o in others
    ):
        reasons.append("lone_change")
    if prop_counts[(device, prop)] <= 1:
        reasons.append("rare_property")
    return reasons


def _features(reasons: list[str], e: dict[str, Any], tz: ZoneInfo) -> list[float]:
    local = e["event_ts"].astimezone(tz)
    hour = local.hour + local.minute / 60
    return [
        math.sin(2 * math.pi * hour / 24),
        math.cos(2 * math.pi * hour / 24),
        *(1.0 if r in reasons else 0.0 for r in WEIGHTS),
        1.0 if e["change_kind"] == "topology" else 0.0,
    ]


def score_events(
    events: list[dict[str, Any]], household: Household, seed: int = 0, voltage: float = 120.0
) -> list[ScoredEvent]:
    """Score config/topology changes, highest risk first. Other change kinds are not scored."""
    tz = ZoneInfo(household.timezone)
    base = baseline(household, voltage)
    scored_in = [e for e in events if e["change_kind"] in SCORED_KINDS]
    if not scored_in:
        return []
    history = _history(events)
    prop_counts = Counter((e["device_id"], e["property"]) for e in scored_in)
    reasons = [_rules(e, base, history, scored_in, prop_counts, tz) for e in scored_in]

    iso = np.zeros(len(scored_in))
    if len(scored_in) >= 8:
        x = np.array([_features(r, e, tz) for r, e in zip(reasons, scored_in, strict=True)])
        forest = IsolationForest(n_estimators=200, contamination="auto", random_state=seed)
        raw = -forest.fit(x).score_samples(x)  # higher = more isolated
        span = float(raw.max() - raw.min())
        if span > 0:
            iso = (raw - raw.min()) / span

    out = []
    for e, r, i in zip(scored_in, reasons, iso, strict=True):
        evidence = sum(WEIGHTS[k] for k in r) + ISOLATION_WEIGHT * float(i)
        score = min(1.0, max(0.0, evidence))
        out.append(
            ScoredEvent(
                event_id=e["event_id"],
                device_id=e["device_id"],
                property=e["property"],
                old_value=e["old_value"],
                new_value=e["new_value"],
                event_ts=e["event_ts"],
                score=round(score, 3),
                alert=score >= ALERT_THRESHOLD,
                reasons=r + (["isolated"] if i >= 0.8 else []),
            )
        )
    return sorted(out, key=lambda s: (-s.score, s.event_ts, s.event_id))


def write_alerts(scored: list[ScoredEvent], data_dir: Path) -> Path:
    """All scored changes with their score, reasons and alert flag (security/alerts.parquet)."""
    path = data_dir / "security" / "alerts.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "event_id": [s.event_id for s in scored],
            "device_id": [s.device_id for s in scored],
            "property": [s.property for s in scored],
            "old_value": [s.old_value for s in scored],
            "new_value": [s.new_value for s in scored],
            "event_ts": pa.array([s.event_ts for s in scored], pa.timestamp("us", tz="UTC")),
            "score": pa.array([s.score for s in scored], pa.float64()),
            "alert": [s.alert for s in scored],
            "reasons": pa.array([s.reasons for s in scored], pa.list_(pa.string())),
        }
    )
    pq.write_table(table, path)
    return path


# --- red-team injection + evaluation ----------------------------------------------------------


def attack_event(
    kind: str, events: list[dict[str, Any]], household: Household, rng: random.Random
) -> dict[str, Any]:
    """One simulated attack at a random time in the stream's period, on a random active device."""
    first = min(e["event_ts"] for e in events)
    last = max(e["event_ts"] for e in events)
    ts = first + timedelta(seconds=rng.uniform(0, (last - first).total_seconds()))
    active = sorted(d.id for d in household.devices.values() if d.active)
    device = rng.choice(active)
    if kind == "firmware_downgrade":
        prop, old = "firmware_version", household.devices[device].firmware_version
        new, change_kind = f"0.{rng.randint(1, 9)}.{rng.randint(0, 9)}", "config"
    elif kind == "rogue_network":
        prop, old = "network_id", household.devices[device].network
        new, change_kind = f"ap_{rng.randrange(16**4):04x}", "topology"
    elif kind == "setpoint_manipulation":
        device = rng.choice([d for d in active if household.devices[d].setpoint is not None])
        setpoint = household.devices[device].setpoint or 0.0
        prop, old = "setpoint", str(int(setpoint))
        new, change_kind = str(int(setpoint + rng.uniform(25, 40))), "config"
    elif kind == "safety_limit_raised":
        candidates = [
            (d, k, v)
            for d in active
            for k, v in sorted(household.devices[d].settings.items())
            if isinstance(v, int | float) and not isinstance(v, bool) and v > 0
        ]
        device, key, value = rng.choice(candidates)
        prop, old = f"settings.{key}", str(value)
        new, change_kind = str(int(float(value) * rng.uniform(1.4, 1.8))), "config"
    elif kind == "unscheduled_circuit_move":
        prop, old = "circuit_id", household.devices[device].circuit
        new, change_kind = rng.choice(sorted(c for c in household.circuits if c != old)), "topology"
    else:
        raise ValueError(f"unknown attack {kind!r}")
    eid = hashlib.sha256(f"attack|{kind}|{device}|{ts.isoformat()}".encode()).hexdigest()[:16]
    return {
        "event_id": eid,
        "device_id": device,
        "property": prop,
        "old_value": old,
        "new_value": new,
        "change_kind": change_kind,
        "semantic_tag": f"attack_{kind}",
        "event_ts": ts,
        "snapshot_run_id": "redteam",
    }


@dataclass
class SecurityEval:
    trials: int
    injected: int
    precision_at_k: float
    recall: float
    false_alerts_per_trial: float
    clean_false_alerts: int
    recall_by_attack: dict[str, float]


def evaluate_security(
    events: list[dict[str, Any]],
    household: Household,
    trials: int = 25,
    seed: int = 7,
    voltage: float = 120.0,
) -> SecurityEval:
    """Inject one attack of each type per trial; score precision@k, recall and false alerts."""
    clean = score_events(events, household, seed=seed, voltage=voltage)
    hits_at_k = caught = false_alerts = 0
    by_attack: Counter[str] = Counter()
    for t in range(trials):
        rng = random.Random(seed * 1000 + t)
        injected = [attack_event(k, events, household, rng) for k in ATTACKS]
        attacks = {e["event_id"]: e["semantic_tag"].removeprefix("attack_") for e in injected}
        ranked = score_events(events + injected, household, seed=seed, voltage=voltage)
        hits_at_k += len({s.event_id for s in ranked[: len(injected)]} & attacks.keys())
        for s in ranked:
            if s.event_id in attacks and s.alert:
                caught += 1
                by_attack[attacks[s.event_id]] += 1
            elif s.event_id not in attacks and s.alert:
                false_alerts += 1
    total = trials * len(ATTACKS)
    return SecurityEval(
        trials=trials,
        injected=total,
        precision_at_k=hits_at_k / total,
        recall=caught / total,
        false_alerts_per_trial=false_alerts / trials,
        clean_false_alerts=sum(1 for s in clean if s.alert),
        recall_by_attack={k: by_attack[k] / trials for k in ATTACKS},
    )
