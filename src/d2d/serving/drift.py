"""Drift monitoring for a served package: inputs, predictions and calibration.

- Input drift: PSI of each feature's presence rate, plus the share of stories with features the
  model has never seen.
- Prediction drift: PSI of the predicted-label mix (abstain is its own bin) and a two-sample KS
  test on top-class confidence.
- Calibration drift: expected calibration error (ECE, 10 bins) on labeled traffic vs reference.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter
from typing import Any

import numpy as np

from d2d.hitl.training_set import Example
from d2d.ml.features import features
from d2d.serving.runtime import OnnxClassifier, decide
from d2d.serving.settings import DriftPolicy

EPS = 1e-4


def psi(expected: list[float], actual: list[float]) -> float:
    total = 0.0
    for e, a in zip(expected, actual, strict=True):
        e, a = max(e, EPS), max(a, EPS)
        total += (a - e) * math.log(a / e)
    return float(total)


def ece(confidence: np.ndarray, correct: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = 0.0
    for lo, hi in itertools.pairwise(edges):
        m = (confidence > lo) & (confidence <= hi)
        if m.any():
            total += m.mean() * abs(correct[m].mean() - confidence[m].mean())
    return float(total)


def profile(clf: OnnxClassifier, rows: list[Example]) -> dict[str, Any]:
    x = clf.vectorize(rows)
    proba = clf.predict_proba(x)
    preds = decide(proba, clf.classes, clf.thresholds)
    known = set(clf.vocabulary)
    mix = Counter("abstain" if p.abstain else p.category for p in preds)
    conf = proba.max(axis=1)
    correct = np.array(
        [clf.classes[int(i)] == r["category"] for i, r in zip(proba.argmax(1), rows, strict=True)]
    )
    return {
        "n": len(rows),
        "presence": x.mean(axis=0),
        "unseen_rate": float(np.mean([bool(set(features(r)) - known) for r in rows])),
        "mix": {k: mix.get(k, 0) / len(rows) for k in [*clf.classes, "abstain"]},
        "confidence": conf,
        "ece": ece(conf, correct.astype(float)),
        "accuracy": float(correct.mean()),
    }


def drift_report(
    clf: OnnxClassifier, reference: list[Example], window: list[Example], policy: DriftPolicy
) -> dict[str, Any]:
    from scipy.stats import ks_2samp

    ref, cur = profile(clf, reference), profile(clf, window)
    feature_psi = {
        name: psi([p, 1 - p], [q, 1 - q])
        for name, p, q in zip(clf.vocabulary, ref["presence"], cur["presence"], strict=True)
    }
    top = sorted(feature_psi.items(), key=lambda kv: -kv[1])[:5]
    keys = list(ref["mix"])
    pred_psi = psi([ref["mix"][k] for k in keys], [cur["mix"][k] for k in keys])
    ks = ks_2samp(ref["confidence"], cur["confidence"])
    alerts = []
    if cur["n"] < policy.min_window:
        alerts.append(f"window too small ({cur['n']} < {policy.min_window}); no drift decision")
    else:
        if top and top[0][1] > policy.psi_alert:
            alerts.append(f"input drift: PSI {top[0][1]:.2f} on {top[0][0]}")
        if cur["unseen_rate"] > ref["unseen_rate"] + 0.05:
            alerts.append(f"input drift: {cur['unseen_rate']:.0%} of stories have unseen features")
        if pred_psi > policy.psi_alert:
            alerts.append(f"prediction drift: label-mix PSI {pred_psi:.2f}")
        if ks.pvalue < policy.ks_pvalue:
            alerts.append(f"prediction drift: confidence KS p={ks.pvalue:.1e}")
        if cur["ece"] > ref["ece"] + policy.ece_increase:
            alerts.append(f"calibration drift: ECE {ref['ece']:.3f} -> {cur['ece']:.3f}")
    return {
        "model": f"{clf.name}/{clf.model_version}",
        "reference_n": ref["n"],
        "window_n": cur["n"],
        "top_feature_psi": [{"feature": k, "psi": round(v, 4)} for k, v in top],
        "unseen_rate": {"reference": ref["unseen_rate"], "window": cur["unseen_rate"]},
        "prediction_psi": round(pred_psi, 4),
        "confidence_ks": {"statistic": round(float(ks.statistic), 4), "pvalue": float(ks.pvalue)},
        "ece": {"reference": round(ref["ece"], 4), "window": round(cur["ece"], 4)},
        "accuracy": {"reference": round(ref["accuracy"], 4), "window": round(cur["accuracy"], 4)},
        "alerts": alerts,
        "alert": any(not a.startswith("window too small") for a in alerts),
    }
