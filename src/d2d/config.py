"""Typed loading of the TOML files under ``config/``."""

from __future__ import annotations

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
