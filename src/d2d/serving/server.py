"""Model registry and server: package loading, rollout routing, v2 inference, per-version stats.

``<registry>/state.json`` holds, per model name, the stable version, an optional candidate, the
rollout mode (``none``, ``shadow`` or ``canary``), the canary weight and the rollout history.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from d2d.hitl.training_set import Example
from d2d.serving.export import INPUT
from d2d.serving.runtime import OnnxClassifier, decide
from d2d.serving.settings import PackageGates

STATE = "state.json"
MAX_BATCH = 1024
MODES = ("none", "shadow", "canary")


class InferError(ValueError):
    """Bad request (400)."""


class NotReady(RuntimeError):
    """Unknown model or version, or a package that fails validation (404/503)."""


@dataclass
class RolloutState:
    name: str
    stable: str | None = None
    candidate: str | None = None
    mode: str = "none"
    weight: float = 0.0
    history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class VersionStats:
    requests: int = 0
    rows: int = 0
    errors: int = 0
    latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=10_000))

    def summary(self) -> dict[str, Any]:
        lat = np.array(self.latencies_ms) if self.latencies_ms else np.zeros(1)
        return {
            "requests": self.requests,
            "rows": self.rows,
            "errors": self.errors,
            "error_rate": self.errors / self.requests if self.requests else 0.0,
            "p50_ms": float(np.percentile(lat, 50)),
            "p95_ms": float(np.percentile(lat, 95)),
            "p99_ms": float(np.percentile(lat, 99)),
        }


class Registry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: tuple[int, dict[str, RolloutState]] | None = None

    def package_dir(self, name: str, version: str) -> Path:
        return self.root / "packages" / name / version

    def names(self) -> list[str]:
        base = self.root / "packages"
        return sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []

    def versions(self, name: str) -> list[str]:
        base = self.root / "packages" / name
        return sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []

    def states(self) -> dict[str, RolloutState]:
        path = self.root / STATE
        if not path.is_file():
            return {}
        mtime = path.stat().st_mtime_ns
        if self._cache is None or self._cache[0] != mtime:
            raw = json.loads(path.read_text("utf-8"))
            self._cache = (mtime, {k: RolloutState(**v) for k, v in raw.items()})
        return dict(self._cache[1])

    def state(self, name: str) -> RolloutState:
        return self.states().get(name) or RolloutState(name)

    def save_state(self, state: RolloutState) -> None:
        states = self.states()
        states[state.name] = state
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / (STATE + ".tmp")
        tmp.write_text(
            json.dumps({k: asdict(v) for k, v in sorted(states.items())}, indent=2) + "\n", "utf-8"
        )
        tmp.replace(self.root / STATE)


def bucket(request_id: str) -> float:
    """Stable value in [0, 1) per request id, so a retried request takes the same route."""
    return int(hashlib.sha1(request_id.encode()).hexdigest()[:8], 16) / 0x1_0000_0000


class ModelServer:
    def __init__(self, registry: Registry, gates: PackageGates) -> None:
        self.registry = registry
        self.gates = gates
        self._models: dict[tuple[str, str], OnnxClassifier] = {}
        self._lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self.stats: dict[tuple[str, str], VersionStats] = {}
        self.shadow: dict[tuple[str, str], dict[str, int]] = {}
        # Demo only (rollout fault injection): share of calls that fail per version.
        self.faults: dict[tuple[str, str], float] = {}
        self._rng = random.Random(0)

    def model(self, name: str, version: str) -> OnnxClassifier:
        from d2d.serving.package import validate

        key = (name, version)
        with self._lock:
            if key not in self._models:
                pkg = self.registry.package_dir(name, version)
                if not pkg.is_dir():
                    raise NotReady(f"model {name} version {version} not found")
                errors = validate(pkg, None, self.gates)
                if errors:
                    raise NotReady(f"{name}/{version} failed validation: {'; '.join(errors)}")
                self._models[key] = OnnxClassifier(pkg)
            return self._models[key]

    def stable(self, name: str) -> str:
        state = self.registry.state(name)
        if state.stable is None:
            raise NotReady(f"model {name} has no stable version; run `d2d model rollout`")
        return state.stable

    def route(self, name: str, request_id: str) -> tuple[str, str | None]:
        """(version that answers, version that only shadows)."""
        s = self.registry.state(name)
        if s.stable is None:
            raise NotReady(f"model {name} has no stable version; run `d2d model rollout`")
        if s.candidate and s.mode == "canary" and bucket(request_id) < s.weight:
            return s.candidate, None
        if s.candidate and s.mode == "shadow":
            return s.stable, s.candidate
        return s.stable, None

    def _run(self, key: tuple[str, str], x: np.ndarray) -> np.ndarray:
        t0 = time.perf_counter()
        failed = False
        try:
            if self._rng.random() < self.faults.get(key, 0.0):
                raise RuntimeError("injected fault")
            proba = self.model(*key).predict_proba(x)
        except Exception:
            failed = True
            raise
        finally:
            with self._stats_lock:
                st = self.stats.setdefault(key, VersionStats())
                st.requests += 1
                st.rows += len(x)
                st.errors += failed
                st.latencies_ms.append((time.perf_counter() - t0) * 1000)
        return proba

    def infer(self, name: str, version: str | None, body: dict[str, Any]) -> dict[str, Any]:
        """Open Inference Protocol v2 request -> response (KServe / Triton compatible)."""
        request_id = str(body.get("id") or f"{time.time_ns()}-{self._rng.random()}")
        shadow = None
        if version is None:
            version, shadow = self.route(name, request_id)
        clf = self.model(name, version)
        x = decode_inputs(body, clf)
        proba = self._run((name, version), x)
        if shadow is not None:
            self._shadow(name, version, shadow, x, proba)
        preds = decide(proba, clf.classes, clf.thresholds)
        outputs = [
            _tensor("probabilities", "FP32", list(proba.shape), proba.ravel().tolist()),
            _tensor("label", "BYTES", [len(preds)], [p.category for p in preds]),
            _tensor("confidence", "FP32", [len(preds)], [p.confidence for p in preds]),
            _tensor("abstain", "BOOL", [len(preds)], [p.abstain for p in preds]),
        ]
        wanted = {o.get("name") for o in body.get("outputs") or []}
        if wanted:
            unknown = wanted - {o["name"] for o in outputs}
            if unknown:
                raise InferError(f"unknown outputs {sorted(unknown)}")
            outputs = [o for o in outputs if o["name"] in wanted]
        return {"model_name": name, "model_version": version, "id": request_id, "outputs": outputs}

    def _shadow(
        self, name: str, stable: str, candidate: str, x: np.ndarray, proba: np.ndarray
    ) -> None:
        tally = self.shadow.setdefault((name, candidate), {"rows": 0, "agree": 0, "errors": 0})
        try:
            other = self._run((name, candidate), x)
        except Exception:
            tally["errors"] += 1
            return
        a = decide(proba, *self._labels(name, stable))
        b = decide(other, *self._labels(name, candidate))
        tally["rows"] += len(a)
        tally["agree"] += sum(
            (p.category, p.abstain) == (q.category, q.abstain) for p, q in zip(a, b, strict=True)
        )

    def _labels(self, name: str, version: str) -> tuple[list[str], dict[str, float]]:
        m = self.model(name, version)
        return m.classes, m.thresholds

    def metadata(self, name: str, version: str | None) -> dict[str, Any]:
        version = version or self.stable(name)
        m = self.model(name, version).manifest
        return {
            "name": name,
            "versions": self.registry.versions(name),
            "platform": "onnxruntime_onnx",
            "inputs": m["inputs"] + [{"name": "story", "datatype": "BYTES", "shape": [-1]}],
            "outputs": m["outputs"]
            + [
                {"name": "label", "datatype": "BYTES", "shape": [-1]},
                {"name": "confidence", "datatype": "FP32", "shape": [-1]},
                {"name": "abstain", "datatype": "BOOL", "shape": [-1]},
            ],
            "parameters": {
                "version": version,
                "framework": m["framework"],
                "classes": m["classes"],
            },
        }


def _tensor(name: str, datatype: str, shape: list[int], data: list[Any]) -> dict[str, Any]:
    return {"name": name, "datatype": datatype, "shape": shape, "data": data}


def decode_inputs(body: dict[str, Any], clf: OnnxClassifier) -> np.ndarray:
    """``features`` (FP32 [n, k], row-major) or ``story`` (BYTES [n], JSON story objects)."""
    inputs = body.get("inputs")
    if not isinstance(inputs, list) or len(inputs) != 1 or not isinstance(inputs[0], dict):
        raise InferError("expected exactly one input: 'features' (FP32) or 'story' (BYTES)")
    t = inputs[0]
    name, dtype, shape, data = t.get("name"), t.get("datatype"), t.get("shape"), t.get("data")
    if not isinstance(shape, list) or not all(isinstance(s, int) and s >= 0 for s in shape):
        raise InferError("shape must be a list of non-negative integers")
    if not isinstance(data, list):
        raise InferError("data must be a list")
    if not shape or shape[0] == 0 or shape[0] > MAX_BATCH:
        raise InferError(f"batch size must be 1..{MAX_BATCH}")
    if name == INPUT and dtype == "FP32":
        k = len(clf.vocabulary)
        if shape != [shape[0], k]:
            raise InferError(f"'features' shape must be [n, {k}]")
        try:
            x = np.asarray(data, dtype=np.float32).reshape(shape)
        except (TypeError, ValueError) as exc:
            raise InferError(f"'features' data does not match shape {shape}: {exc}") from exc
        if not np.isfinite(x).all():
            raise InferError("'features' must be finite")
        return x
    if name == "story" and dtype == "BYTES":
        if len(shape) != 1 or len(data) != shape[0]:
            raise InferError("'story' shape must be [n] with n JSON strings")
        stories: list[Example] = []
        for item in data:
            try:
                s = json.loads(item) if isinstance(item, str) else item
                if not isinstance(s, dict):
                    raise TypeError("not an object")
                stories.append(
                    {"events": list(s["events"]), "link_reasons": list(s.get("link_reasons", []))}
                )
                clf.vectorize(stories[-1:])
            except (TypeError, KeyError, ValueError, AttributeError) as exc:
                raise InferError(f"bad story: {exc}") from exc
        return clf.vectorize(stories)
    raise InferError(f"unsupported input {name!r} ({dtype}); use 'features' FP32 or 'story' BYTES")
