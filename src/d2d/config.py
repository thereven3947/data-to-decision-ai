"""Typed loading of the TOML files under ``config/``."""

from __future__ import annotations

import hashlib
import os
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SettingValue = bool | int | float | str


class ConfigError(ValueError):
    """Raised when a config file is missing or inconsistent."""


def config_dir(override: Path | None = None) -> Path:
    if override is not None:
        return override
    env = os.environ.get("D2D_CONFIG_DIR")
    if env:
        return Path(env)
    return Path.cwd() / "config"


def load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    with path.open("rb") as fh:
        return tomllib.load(fh)


@dataclass(frozen=True)
class Upstream:
    id: str
    kind: str


@dataclass(frozen=True)
class Circuit:
    id: str
    panel: str
    rating_amps: int


@dataclass(frozen=True)
class DeviceSpec:
    id: str
    kind: str
    room: str
    circuit: str
    network: str
    rated_w: float
    standby_w: float = 1.0
    always_on: bool = False
    firmware_version: str = "1.0.0"
    schedule: dict[str, str] | None = None
    mode: str | None = None
    setpoint: float | None = None
    settings: dict[str, SettingValue] = field(default_factory=dict)
    base_rssi_dbm: int = -55
    active: bool = True
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Household:
    id: str
    timezone: str
    upstreams: dict[str, Upstream]
    circuits: dict[str, Circuit]
    devices: dict[str, DeviceSpec]

    def upstream_ids(self, circuit_id: str, network_id: str) -> list[str]:
        return sorted({self.circuits[circuit_id].panel, network_id})


def load_household(path: Path) -> Household:
    raw = load_toml(path)
    upstreams = {u["id"]: Upstream(id=u["id"], kind=u["kind"]) for u in raw.get("upstream", [])}
    circuits = {
        c["id"]: Circuit(id=c["id"], panel=c["panel"], rating_amps=int(c["rating_amps"]))
        for c in raw.get("circuit", [])
    }
    devices: dict[str, DeviceSpec] = {}
    for d in raw.get("device", []):
        data = dict(d)
        tags = tuple(data.pop("tags", ()))
        try:
            spec = DeviceSpec(tags=tags, **data)
        except TypeError as exc:
            raise ConfigError(f"device {d.get('id')!r}: {exc}") from exc
        if spec.id in devices:
            raise ConfigError(f"duplicate device id {spec.id!r}")
        if spec.circuit not in circuits:
            raise ConfigError(f"device {spec.id!r}: unknown circuit {spec.circuit!r}")
        if spec.network not in upstreams:
            raise ConfigError(f"device {spec.id!r}: unknown network {spec.network!r}")
        devices[spec.id] = spec
    for c in circuits.values():
        if c.panel not in upstreams:
            raise ConfigError(f"circuit {c.id!r}: unknown panel {c.panel!r}")
    home = raw["home"]
    return Household(
        id=home["id"],
        timezone=home["timezone"],
        upstreams=upstreams,
        circuits=circuits,
        devices=devices,
    )


@dataclass(frozen=True)
class PipelineConfig:
    cadence_minutes: int
    seed: int
    days: int
    start: datetime


def parse_utc(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def load_pipeline(path: Path) -> PipelineConfig:
    raw = load_toml(path)
    cadence = int(raw["pipeline"]["cadence_minutes"])
    if cadence <= 0 or 60 % cadence != 0:
        raise ConfigError("cadence_minutes must be a positive divisor of 60")
    sim = raw.get("simulator", {})
    return PipelineConfig(
        cadence_minutes=cadence,
        seed=int(sim.get("seed", 7)),
        days=int(sim.get("days", 3)),
        start=parse_utc(str(sim.get("start", "2026-10-01T00:00:00Z"))),
    )


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    grid_analogue: str
    definition: str
    plain: str
    energy: bool
    checks: tuple[str, ...]
    actions: tuple[str, ...]


@dataclass(frozen=True)
class Taxonomy:
    categories: dict[str, Category]
    audit_rate: float
    sha: str

    def require(self, category_id: str) -> Category:
        if category_id not in self.categories:
            raise ConfigError(f"unknown category {category_id!r}")
        return self.categories[category_id]


def load_categories(path: Path) -> Taxonomy:
    raw = load_toml(path)
    categories: dict[str, Category] = {}
    for c in raw.get("category", []):
        if c["id"] in categories:
            raise ConfigError(f"duplicate category id {c['id']!r}")
        categories[c["id"]] = Category(
            id=c["id"],
            name=c["name"],
            grid_analogue=c["grid_analogue"],
            definition=c["definition"],
            plain=c["plain"],
            energy=bool(c.get("energy", False)),
            checks=tuple(c.get("checks", ())),
            actions=tuple(c.get("actions", ())),
        )
    audit_rate = float(raw.get("review", {}).get("audit_rate", 0.0))
    if not 0.0 <= audit_rate <= 1.0:
        raise ConfigError("review.audit_rate must be between 0 and 1")
    sha = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    return Taxonomy(categories=categories, audit_rate=audit_rate, sha=sha)
