"""Semantic rule engine for stage 2 (``config/semantics.toml``)."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class Semantics:
    rules: tuple[Rule, ...]
    windows: dict[str, tuple[str, str]]
    clock_skew_threshold_s: int = 300
    durations: tuple[DurationRule, ...] = ()

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
    for d in durations:
        if d.change_kind not in CHANGE_KINDS:
            raise ConfigError(f"duration {d.tag!r}: unknown change_kind {d.change_kind!r}")
    return Semantics(
        rules=rules,
        windows=windows,
        clock_skew_threshold_s=int(dq.get("clock_skew_threshold_s", 300)),
        durations=durations,
    )
