"""Semantic rule engine for stage 2 (``config/semantics.toml``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from d2d.config import ConfigError, load_toml
from d2d.simulator.devices import in_window, parse_hhmm

CHANGE_KINDS = frozenset({"config", "operational", "topology", "data_quality"})


@dataclass(frozen=True)
class Rule:
    property: str
    change_kind: str
    tag: str
    old_in: tuple[str, ...] | None = None
    new_in: tuple[str, ...] | None = None
    old_is_null: bool | None = None
    new_is_null: bool | None = None
    old_in_window: str | None = None
    new_in_window: str | None = None
    min_abs_delta: float | None = None


@dataclass(frozen=True)
class DurationRule:
    """CDC state held at least ``min_minutes`` (e.g. garage door left open)."""

    property: str
    value: str
    min_minutes: int
    change_kind: str
    tag: str
    kinds: tuple[str, ...] = ()
    zone: str | None = None  # only count time inside this zone (e.g. away_mode)


@dataclass(frozen=True)
class Zone:
    """A policy zone: a daily local-time window, or on/off episodes of a CDC property."""

    name: str
    window: str | None = None
    property: str | None = None
    on_value: str = "on"


@dataclass(frozen=True)
class ZoneRule:
    """A CDC change (``property`` -> ``value``) inside a zone; once per device per episode."""

    property: str
    value: str
    zone: str
    change_kind: str
    tag: str
    kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class SequenceRule:
    """Trigger, a required companion signal nearby, and a missing follow-up -> one change."""

    tag: str
    change_kind: str
    emit_property: str
    emit_value: str
    trigger_property: str
    trigger_value: str
    trigger_kinds: tuple[str, ...]
    require_property: str
    require_value: str
    require_kinds: tuple[str, ...]
    require_within_s: int
    absent_property: str
    absent_value: str
    absent_within_s: int


@dataclass(frozen=True)
class ReconcileRule:
    """Snapshot field that must agree with a CDC property (M5-06 ``dq.cdc_gap``)."""

    kinds: tuple[str, ...]
    cdc_property: str
    snapshot_field: str
    mapping: dict[str, str]
    initial: str


@dataclass(frozen=True)
class Semantics:
    rules: tuple[Rule, ...]
    windows: dict[str, tuple[str, str]]
    clock_skew_threshold_s: int = 300
    durations: tuple[DurationRule, ...] = ()
    zones: dict[str, Zone] = field(default_factory=dict)
    zone_rules: tuple[ZoneRule, ...] = ()
    sequences: tuple[SequenceRule, ...] = ()
    reconciles: tuple[ReconcileRule, ...] = ()

    def classify(self, prop: str, old: str | None, new: str | None) -> tuple[str, str] | None:
        """Return ``(change_kind, semantic_tag)`` of the first matching rule, else None."""
        for rule in self.rules:
            if fnmatchcase(prop, rule.property) and self._matches(rule, old, new):
                return rule.change_kind, rule.tag
        return None

    def _matches(self, rule: Rule, old: str | None, new: str | None) -> bool:
        if rule.old_in is not None and old not in rule.old_in:
            return False
        if rule.new_in is not None and new not in rule.new_in:
            return False
        if rule.old_is_null is not None and (old is None) != rule.old_is_null:
            return False
        if rule.new_is_null is not None and (new is None) != rule.new_is_null:
            return False
        if rule.old_in_window and not self._in_window(old, rule.old_in_window):
            return False
        if rule.new_in_window and not self._in_window(new, rule.new_in_window):
            return False
        if rule.min_abs_delta is not None:
            if old is None or new is None:
                return False
            try:
                delta = abs(float(new) - float(old))
            except ValueError:
                return False
            if delta < rule.min_abs_delta:
                return False
        return True

    def _in_window(self, value: str | None, window: str) -> bool:
        if value is None:
            return False
        start, end = self.windows[window]
        try:
            return in_window(parse_hhmm(value), start, end)
        except ValueError:
            return False


def _rule(raw: dict[str, Any], windows: dict[str, tuple[str, str]]) -> Rule:
    data = dict(raw)
    for key in ("old_in", "new_in"):
        if key in data:
            data[key] = tuple(str(v) for v in data[key])
    try:
        rule = Rule(**data)
    except TypeError as exc:
        raise ConfigError(f"semantics rule {raw}: {exc}") from exc
    if rule.change_kind not in CHANGE_KINDS:
        raise ConfigError(f"rule {rule.tag!r}: unknown change_kind {rule.change_kind!r}")
    for w in (rule.old_in_window, rule.new_in_window):
        if w is not None and w not in windows:
            raise ConfigError(f"rule {rule.tag!r}: unknown window {w!r}")
    return rule


def load_semantics(path: Path) -> Semantics:
    raw = load_toml(path)
    windows = {name: (str(v[0]), str(v[1])) for name, v in raw.get("windows", {}).items()}
    rules = tuple(_rule(r, windows) for r in raw.get("rule", []))
    dq = raw.get("data_quality", {})
    durations = tuple(
        DurationRule(**{**d, "kinds": tuple(d.get("kinds", ()))}) for d in raw.get("duration", [])
    )
    zones = {z["name"]: Zone(**z) for z in raw.get("zone", [])}
    for z in zones.values():
        if (z.window is None) == (z.property is None):
            raise ConfigError(f"zone {z.name!r}: set exactly one of window or property")
        if z.window is not None and z.window not in windows:
            raise ConfigError(f"zone {z.name!r}: unknown window {z.window!r}")
    zone_rules = tuple(
        ZoneRule(**{**r, "kinds": tuple(r.get("kinds", ()))}) for r in raw.get("zone_rule", [])
    )
    sequences = tuple(_sequence(r) for r in raw.get("sequence", []))
    reconciles = tuple(
        ReconcileRule(
            kinds=tuple(r["kinds"]),
            cdc_property=str(r["cdc_property"]),
            snapshot_field=str(r["snapshot_field"]),
            mapping={str(k): str(v) for k, v in r["map"].items()},
            initial=str(r["initial"]),
        )
        for r in raw.get("reconcile", [])
    )
    tagged: list[tuple[str, str, str | None]] = [
        *((d.tag, d.change_kind, d.zone) for d in durations),
        *((z.tag, z.change_kind, z.zone) for z in zone_rules),
        *((q.tag, q.change_kind, None) for q in sequences),
    ]
    for tag, kind, zone_name in tagged:
        if kind not in CHANGE_KINDS:
            raise ConfigError(f"{tag!r}: unknown change_kind {kind!r}")
        if zone_name is not None and zone_name not in zones:
            raise ConfigError(f"{tag!r}: unknown zone {zone_name!r}")
    return Semantics(
        rules=rules,
        windows=windows,
        clock_skew_threshold_s=int(dq.get("clock_skew_threshold_s", 300)),
        durations=durations,
        zones=zones,
        zone_rules=zone_rules,
        sequences=sequences,
        reconciles=reconciles,
    )


def _sequence(raw: dict[str, Any]) -> SequenceRule:
    try:
        trig, req, absent, emit = raw["trigger"], raw["require"], raw["absent"], raw["emit"]
        return SequenceRule(
            tag=str(raw["tag"]),
            change_kind=str(raw["change_kind"]),
            emit_property=str(emit["property"]),
            emit_value=str(emit["value"]),
            trigger_property=str(trig["property"]),
            trigger_value=str(trig["value"]),
            trigger_kinds=tuple(trig.get("kinds", ())),
            require_property=str(req["property"]),
            require_value=str(req["value"]),
            require_kinds=tuple(req.get("kinds", ())),
            require_within_s=int(req["within_s"]),
            absent_property=str(absent["property"]),
            absent_value=str(absent["value"]),
            absent_within_s=int(absent["within_s"]),
        )
    except KeyError as exc:
        raise ConfigError(f"sequence {raw.get('tag')!r}: missing {exc}") from exc
