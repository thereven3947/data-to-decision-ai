"""ONNX Runtime classifier over a model package; same predictions API as ``d2d.ml.train``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from d2d.hitl.training_set import Example
from d2d.ml.train import Prediction
from d2d.serving.export import INPUT, OUTPUT, vectorize

MANIFEST = "manifest.json"
MODEL = "model.onnx"
CARD = "MODEL_CARD.md"


def decide(proba: np.ndarray, classes: list[str], thresholds: dict[str, float]) -> list[Prediction]:
    out = []
    for row in proba:
        i = int(row.argmax())
        c, p = classes[i], float(row[i])
        out.append(Prediction(c, round(p, 4), p < thresholds.get(c, 1.01)))
    return out


class OnnxClassifier:
    def __init__(self, package_dir: Path, threads: int = 1) -> None:
        import onnxruntime as ort

        self.path = package_dir
        self.manifest: dict[str, Any] = json.loads((package_dir / MANIFEST).read_text("utf-8"))
        opts = ort.SessionOptions()
        # Small models: one intra-op thread is faster than the default thread pool.
        opts.intra_op_num_threads = threads
        self.session = ort.InferenceSession(
            str(package_dir / MODEL), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.vocabulary: list[str] = self.manifest["vocabulary"]
        self.classes: list[str] = self.manifest["classes"]
        self.thresholds: dict[str, float] = self.manifest["thresholds"]

    @property
    def name(self) -> str:
        return str(self.manifest["name"])

    @property
    def model_version(self) -> str:
        return str(self.manifest["version"])

    def vectorize(self, examples: list[Example]) -> np.ndarray:
        return vectorize(examples, self.vocabulary)

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        out: np.ndarray = self.session.run([OUTPUT], {INPUT: x.astype(np.float32, copy=False)})[0]
        return out

    def predict(self, examples: list[Example]) -> list[Prediction]:
        if not examples:
            return []
        return decide(self.predict_proba(self.vectorize(examples)), self.classes, self.thresholds)
