"""Shadow and canary rollout between two package versions, with automatic rollback.

Runs simulated, labeled traffic through the same ``ModelServer`` the v2 API uses:

1. Gate: the candidate package must validate (files, parity, golden eval gate).
2. Shadow: stable answers; the candidate scores the same requests in the background.
3. Canary: each step sends a share of requests (stable hash of the request id) to the candidate.
4. Promote, or roll back as soon as a guardrail fails (error rate, p95 latency, precision on
   labeled traffic, shadow agreement). Each decision is appended to the registry state history.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np

from d2d.hitl.training_set import Example
from d2d.serving.package import validate
from d2d.serving.server import ModelServer, RolloutState, bucket
from d2d.serving.settings import RolloutPolicy


@dataclass
class Step:
    phase: str
    weight: float
    requests: int
    candidate_requests: int
    candidate_errors: int
    error_rate: float
    p95_ms: float
    precision: float | None
    coverage: float | None
    agreement: float | None
    ok: bool
    reason: str


def _body(row: Example, request_id: str) -> dict[str, Any]:
    story = {"events": row["events"], "link_reasons": row["link_reasons"]}
    return {
        "id": request_id,
        "inputs": [
            {"name": "story", "datatype": "BYTES", "shape": [1], "data": [json.dumps(story)]}
        ],
    }


def _precision(preds: list[tuple[str, bool]], labels: list[str]) -> tuple[float | None, float]:
    covered = [(c, y) for (c, abstain), y in zip(preds, labels, strict=True) if not abstain]
    coverage = len(covered) / len(labels) if labels else 0.0
    if not covered:
        return None, coverage
    return sum(c == y for c, y in covered) / len(covered), coverage


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def run_rollout(
    server: ModelServer,
    name: str,
    candidate: str,
    traffic: list[Example],
    golden: list[Example],
    policy: RolloutPolicy,
    skip_shadow: bool = False,
) -> dict[str, Any]:
    registry = server.registry
    state = registry.state(name)
    steps: list[Step] = []

    def finish(outcome: str, reason: str, new: RolloutState) -> dict[str, Any]:
        new.history.append(
            {
                "at": _now(),
                "candidate": candidate,
                "previous_stable": state.stable,
                "outcome": outcome,
                "reason": reason,
                "steps": [asdict(s) for s in steps],
            }
        )
        registry.save_state(new)
        return {"outcome": outcome, "reason": reason, "stable": new.stable, "steps": steps}

    errors = validate(registry.package_dir(name, candidate), golden, server.gates)
    steps.append(
        Step("gate", 0.0, 0, 0, 0, 0.0, 0.0, None, None, None, not errors, "; ".join(errors))
    )
    if errors:
        return finish("rejected", "package validation failed", RolloutState(**asdict(state)))
    if state.stable is None:
        return finish(
            "initial",
            "no stable version yet",
            RolloutState(name, stable=candidate, history=state.history),
        )
    if state.stable == candidate:
        return finish("noop", "candidate is already stable", RolloutState(**asdict(state)))

    cursor = 0

    def next_rows(n: int) -> list[Example]:
        nonlocal cursor
        rows = [traffic[(cursor + i) % len(traffic)] for i in range(n)]
        cursor += n
        return rows

    def stats_mark() -> tuple[int, int, int]:
        st = server.stats.get((name, candidate))
        return (st.requests, st.errors, len(st.latencies_ms)) if st else (0, 0, 0)

    def stats_since(mark: tuple[int, int, int]) -> tuple[int, int, float]:
        st = server.stats.get((name, candidate))
        if st is None:
            return 0, 0, 0.0
        lat = list(st.latencies_ms)[mark[2] :]
        p95 = float(np.percentile(lat, 95)) if lat else 0.0
        return st.requests - mark[0], st.errors - mark[1], p95

    def check(step: Step) -> str:
        if step.error_rate > policy.max_error_rate:
            return f"error rate {step.error_rate:.3f} > {policy.max_error_rate}"
        if step.p95_ms > policy.max_p95_ms:
            return f"p95 {step.p95_ms:.1f} ms > {policy.max_p95_ms} ms"
        if step.precision is not None and step.precision < policy.min_precision:
            return f"precision {step.precision:.3f} < {policy.min_precision}"
        if step.agreement is not None and step.agreement < policy.min_agreement:
            return f"shadow agreement {step.agreement:.3f} < {policy.min_agreement}"
        return ""

    rollback = RolloutState(name, stable=state.stable, history=state.history)
    if not skip_shadow:
        registry.save_state(
            RolloutState(name, state.stable, candidate, "shadow", 0.0, state.history)
        )
        server.shadow.pop((name, candidate), None)
        mark = stats_mark()
        rows = next_rows(policy.shadow_requests)
        for i, row in enumerate(rows):
            server.infer(name, None, _body(row, f"shadow-{cursor}-{i}"))
        n, errs, p95 = stats_since(mark)
        tally = server.shadow.get((name, candidate), {"rows": 0, "agree": 0, "errors": 0})
        cand = server.model(name, candidate).predict(rows)
        precision, coverage = _precision(
            [(p.category, p.abstain) for p in cand], [r["category"] for r in rows]
        )
        step = Step(
            "shadow",
            0.0,
            len(rows),
            n,
            errs,
            errs / n if n else 0.0,
            p95,
            precision,
            coverage,
            tally["agree"] / tally["rows"] if tally["rows"] else 0.0,
            True,
            "",
        )
        step.reason = check(step)
        step.ok = not step.reason
        steps.append(step)
        if not step.ok:
            return finish("rolled_back", f"shadow: {step.reason}", rollback)

    for weight in policy.canary_steps:
        registry.save_state(
            RolloutState(name, state.stable, candidate, "canary", weight, state.history)
        )
        mark = stats_mark()
        rows = next_rows(policy.requests_per_step)
        routed: list[tuple[str, bool]] = []
        labels: list[str] = []
        for i, row in enumerate(rows):
            rid = f"canary-{weight}-{cursor}-{i}"
            try:
                resp = server.infer(name, None, _body(row, rid))
            except Exception:
                continue  # counted in the candidate's error stats when it was routed there
            if resp["model_version"] == candidate:
                out = {o["name"]: o["data"] for o in resp["outputs"]}
                routed.append((out["label"][0], out["abstain"][0]))
                labels.append(row["category"])
        n, errs, p95 = stats_since(mark)
        expected = sum(bucket(f"canary-{weight}-{cursor}-{i}") < weight for i in range(len(rows)))
        precision, coverage = _precision(routed, labels)
        step = Step(
            "canary",
            weight,
            len(rows),
            n,
            errs,
            errs / n if n else 0.0,
            p95,
            precision,
            coverage,
            None,
            True,
            "",
        )
        if n != expected:
            step.reason = f"routed {n} requests to the candidate, expected {expected}"
        else:
            step.reason = check(step)
        step.ok = not step.reason
        steps.append(step)
        if not step.ok:
            return finish("rolled_back", f"canary {weight:.0%}: {step.reason}", rollback)

    return finish(
        "promoted",
        "all guardrails passed",
        RolloutState(name, stable=candidate, history=state.history),
    )
