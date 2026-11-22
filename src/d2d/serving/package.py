"""Versioned model packages: ``<registry>/packages/<name>/<version>/``.

A package holds ``model.onnx``, ``manifest.json`` (input/output schema, vocabulary, classes,
calibration, abstain thresholds, training-data hash, golden eval, native/ONNX parity, file hashes)
and ``MODEL_CARD.md``. ``validate`` must pass before a package is served or rolled out.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from d2d import __version__
from d2d.hitl.training_set import Example
from d2d.ml.evaluate import evaluate_classifier
from d2d.ml.features import FEATURE_VERSION
from d2d.serving.export import INPUT, OPSET, OUTPUT, Exported, vectorize
from d2d.serving.runtime import CARD, MANIFEST, MODEL, OnnxClassifier, decide
from d2d.serving.settings import PackageGates

FORMAT_VERSION = 1
REQUIRED = (
    "format_version",
    "name",
    "version",
    "framework",
    "inputs",
    "outputs",
    "vocabulary",
    "classes",
    "thresholds",
    "calibration",
    "training",
    "eval",
    "parity",
    "files",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parity(exp: Exported, onnx: OnnxClassifier, rows: list[Example]) -> dict[str, Any]:
    """Native vs ONNX Runtime on the same stories: max |p diff| and identical decisions."""
    native = exp.native_proba(rows)
    ort = onnx.predict_proba(vectorize(rows, exp.vocabulary))
    a = decide(native, exp.classes, exp.thresholds)
    b = decide(ort, exp.classes, exp.thresholds)
    same = sum(
        (x.category, x.abstain) == (y.category, y.abstain) for x, y in zip(a, b, strict=True)
    )
    return {
        "rows": len(rows),
        "max_abs_diff": float(np.abs(native - ort).max()),
        "decisions_identical": same == len(rows),
        "decision_mismatches": len(rows) - same,
    }


def golden_eval(clf: OnnxClassifier, golden: list[Example]) -> dict[str, Any]:
    r = evaluate_classifier(clf, golden)
    return {
        "set": "evals/golden/stories.jsonl",
        "examples": r.examples,
        "coverage": round(r.coverage, 4),
        "min_precision": round(r.min_precision, 4),
        "precision_by_class": {k: round(v, 4) for k, v in r.precision_by_class.items()},
    }


def write_package(
    exp: Exported,
    registry: Path,
    name: str,
    training_set: Path,
    golden: list[Example],
    parity_rows: list[Example],
    sources: dict[str, Any],
) -> Path:
    out = registry / "packages" / name / exp.model_version
    out.mkdir(parents=True, exist_ok=True)
    (out / MODEL).write_bytes(exp.onnx)
    manifest: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "name": name,
        "version": exp.model_version,
        "framework": exp.framework,
        "framework_version": exp.framework_version,
        "runtime": "onnxruntime",
        "opset": OPSET,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "d2d_version": __version__,
        "inputs": [{"name": INPUT, "datatype": "FP32", "shape": [-1, len(exp.vocabulary)]}],
        "outputs": [{"name": OUTPUT, "datatype": "FP32", "shape": [-1, len(exp.classes)]}],
        "feature_version": FEATURE_VERSION,
        "vocabulary": exp.vocabulary,
        "classes": exp.classes,
        "thresholds": exp.thresholds,
        "calibration": exp.calibration,
        "training": {
            "training_set_version": exp.training_set_version,
            "data_sha256": sha256(training_set),
            "examples": exp.examples,
            "augmented_examples": exp.augmented_examples,
            "oof_precision": exp.oof_precision,
            "params": exp.params,
            "sources": sources,
        },
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    clf = OnnxClassifier(out)
    manifest["eval"] = {"golden": golden_eval(clf, golden)}
    manifest["parity"] = parity(exp, clf, parity_rows + golden)
    (out / CARD).write_text(model_card(manifest), "utf-8")
    manifest["files"] = {f: sha256(out / f) for f in (MODEL, CARD)}
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    return out


def validate(pkg: Path, golden: list[Example] | None, gates: PackageGates) -> list[str]:
    """Problems that block serving; an empty list means the package is valid."""
    if not (pkg / MANIFEST).is_file():
        return [f"{MANIFEST} missing"]
    try:
        m = json.loads((pkg / MANIFEST).read_text("utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{MANIFEST} is not JSON: {exc}"]
    errors = [f"manifest key missing: {k}" for k in REQUIRED if k not in m]
    if errors:
        return errors
    if m["format_version"] != FORMAT_VERSION:
        errors.append(f"format_version {m['format_version']} != {FORMAT_VERSION}")
    if pkg.name != m["version"] or pkg.parent.name != m["name"]:
        errors.append("directory does not match <name>/<version> in the manifest")
    for f in (MODEL, CARD):
        if not (pkg / f).is_file():
            errors.append(f"{f} missing")
        elif sha256(pkg / f) != m["files"].get(f):
            errors.append(f"{f} sha256 does not match the manifest")
    n_feat, n_cls = len(m["vocabulary"]), len(m["classes"])
    if m["inputs"] != [{"name": INPUT, "datatype": "FP32", "shape": [-1, n_feat]}]:
        errors.append("inputs do not match the vocabulary")
    if m["outputs"] != [{"name": OUTPUT, "datatype": "FP32", "shape": [-1, n_cls]}]:
        errors.append("outputs do not match the classes")
    if set(m["thresholds"]) != set(m["classes"]):
        errors.append("thresholds must cover exactly the classes")
    if not m["parity"].get("decisions_identical"):
        errors.append("native and ONNX decisions differ")
    if m["parity"].get("max_abs_diff", 1.0) > gates.max_parity_diff:
        errors.append(f"parity max_abs_diff {m['parity'].get('max_abs_diff')} too large")
    g = m["eval"].get("golden", {})
    if g.get("min_precision", 0.0) < gates.min_precision:
        errors.append(f"golden min precision {g.get('min_precision')} < {gates.min_precision}")
    if g.get("coverage", 0.0) < gates.min_coverage:
        errors.append(f"golden coverage {g.get('coverage')} < {gates.min_coverage}")
    if errors:
        return errors
    try:
        clf = OnnxClassifier(pkg)
    except Exception as exc:  # onnxruntime raises its own exception types
        return [f"ONNX Runtime cannot load {MODEL}: {exc}"]
    sig_in = [(i.name, i.shape[1]) for i in clf.session.get_inputs()]
    sig_out = [(o.name, o.shape[1]) for o in clf.session.get_outputs() if o.name == OUTPUT]
    if sig_in != [(INPUT, n_feat)] or sig_out != [(OUTPUT, n_cls)]:
        errors.append(f"ONNX signature {sig_in} -> {sig_out} does not match the manifest")
    elif golden is not None:
        live = golden_eval(clf, golden)
        if (live["coverage"], live["min_precision"]) != (g["coverage"], g["min_precision"]):
            errors.append(f"golden eval does not reproduce: {live} vs {g}")
    return errors


def model_card(m: dict[str, Any]) -> str:
    g, p, t = m["eval"]["golden"], m["parity"], m["training"]
    per_class = "\n".join(f"| {c} | {v:.3f} |" for c, v in g["precision_by_class"].items())
    return f"""# Model card: {m["name"]} {m["version"]}

All training and evaluation data is simulated (seeded household simulator). No real-user data.

## Model

- Task: label a change story with one of {len(m["classes"])} categories, or abstain to the
  review queue when the calibrated confidence is below the class threshold.
- Framework: {m["framework"]} {m["framework_version"]}, exported to ONNX (opset {m["opset"]}),
  served with ONNX Runtime.
- Input `{INPUT}`: FP32 [batch, {len(m["vocabulary"])}] one-hot story features
  (feature version {m["feature_version"]}; vocabulary in `manifest.json`).
- Output `{OUTPUT}`: FP32 [batch, {len(m["classes"])}] calibrated class probabilities.
- Calibration: {json.dumps(m["calibration"])}.

## Training data

- Training set `{t["training_set_version"]}`, sha256 `{t["data_sha256"][:16]}...`:
  {t["examples"]} labeled stories ({t["augmented_examples"]} with random event-subset augmentation).
- Sources: {json.dumps(t["sources"])}.

## Evaluation (frozen golden set, {g["examples"]} stories)

Coverage (1 - abstain rate) {g["coverage"]:.3f}; minimum per-class precision
{g["min_precision"]:.3f}.

| class | precision |
|---|---|
{per_class}

## Native vs ONNX parity

{p["rows"]} stories: max absolute probability difference {p["max_abs_diff"]:.2e};
decisions identical: {p["decisions_identical"]}.

## Limitations

- The golden set is small ({g["examples"]} stories) and comes from the same simulator and scenario
  catalog as the training data, so it measures in-distribution quality only.
- Thresholds were chosen out of fold to reach the target precision on simulated labels; real homes
  would need recalibration.
"""
