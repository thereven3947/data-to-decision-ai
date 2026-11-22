"""``config/serving.toml``: package gates, simulated data, latency targets, rollout, drift."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from d2d.config import load_toml


@dataclass(frozen=True)
class PackageGates:
    name: str
    min_precision: float
    min_coverage: float
    max_parity_diff: float


@dataclass(frozen=True)
class DataPlan:
    train_seeds: tuple[int, ...]
    traffic_seeds: tuple[int, ...]
    days: int


@dataclass(frozen=True)
class Targets:
    inproc_p95_ms: float
    http_p50_ms: float
    http_p95_ms: float
    http_p99_ms: float
    http_batch_rows_per_s: float
    max_error_rate: float


@dataclass(frozen=True)
class RolloutPolicy:
    canary_steps: tuple[float, ...]
    requests_per_step: int
    shadow_requests: int
    max_error_rate: float
    max_p95_ms: float
    min_precision: float
    min_agreement: float


@dataclass(frozen=True)
class DriftPolicy:
    psi_alert: float
    ks_pvalue: float
    ece_increase: float
    min_window: int


@dataclass(frozen=True)
class ServingConfig:
    package: PackageGates
    data: DataPlan
    targets: Targets
    rollout: RolloutPolicy
    drift: DriftPolicy


def load_serving(path: Path) -> ServingConfig:
    raw = load_toml(path)
    p, d, t, r, dr = raw["package"], raw["data"], raw["targets"], raw["rollout"], raw["drift"]
    return ServingConfig(
        package=PackageGates(
            str(p["name"]),
            float(p["min_precision"]),
            float(p["min_coverage"]),
            float(p["max_parity_diff"]),
        ),
        data=DataPlan(
            tuple(int(s) for s in d["train_seeds"]),
            tuple(int(s) for s in d["traffic_seeds"]),
            int(d["days"]),
        ),
        targets=Targets(**{k: float(v) for k, v in t.items()}),
        rollout=RolloutPolicy(
            tuple(float(s) for s in r["canary_steps"]),
            int(r["requests_per_step"]),
            int(r["shadow_requests"]),
            float(r["max_error_rate"]),
            float(r["max_p95_ms"]),
            float(r["min_precision"]),
            float(r["min_agreement"]),
        ),
        drift=DriftPolicy(
            float(dr["psi_alert"]),
            float(dr["ks_pvalue"]),
            float(dr["ece_increase"]),
            int(dr["min_window"]),
        ),
    )
