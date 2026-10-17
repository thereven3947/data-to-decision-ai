"""Train a calibrated, precision-first classifier with per-class thresholds and abstain.

Training examples are augmented with random event subsets of each labeled story (seeded), so the
model learns from partial stories (linking splits) and reports lower confidence for them.
Thresholds come from out-of-fold probabilities: the lowest threshold per class whose precision
reaches ``target_precision``; predictions below it abstain and go to the review queue.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline, make_pipeline

from d2d.hitl.training_set import Example
from d2d.ml.features import FEATURE_VERSION, features

MODEL_FILE = "classifier.joblib"
META_FILE = "classifier.json"


@dataclass(frozen=True)
class TrainParams:
    target_precision: float = 0.9
    min_threshold: float = 0.6
    augment: int = 8
    seed: int = 7
    folds: int = 3


@dataclass(frozen=True)
class ModelMeta:
    model_version: str
    training_set_version: str
    feature_version: str
    sklearn_version: str
    classes: list[str]
    thresholds: dict[str, float]
    oof_precision: dict[str, float]
    examples: int
    augmented_examples: int
    params: dict[str, Any]


@dataclass(frozen=True)
class Prediction:
    category: str
    confidence: float
    abstain: bool


def augment(rows: list[Example], n: int, seed: int) -> list[Example]:
    rng = random.Random(seed)
    out: list[Example] = []
    for r in rows:
        out.append(r)
        events = r["events"]
        for _ in range(n):
            k = rng.randint(1, len(events))
            out.append({**r, "events": rng.sample(events, k)})
    return out


def _model(seed: int, folds: int) -> Pipeline:
    base = LogisticRegression(C=1.0, max_iter=5000, random_state=seed)
    return make_pipeline(
        DictVectorizer(sparse=True),
        CalibratedClassifierCV(base, method="sigmoid", cv=folds),
    )


def _thresholds(
    proba: np.ndarray, y: list[str], classes: list[str], params: TrainParams
) -> tuple[dict[str, float], dict[str, float]]:
    pred_idx = proba.argmax(axis=1)
    top = proba.max(axis=1)
    y_arr = np.array(y)
    thresholds: dict[str, float] = {}
    precision: dict[str, float] = {}
    for i, c in enumerate(classes):
        mask = pred_idx == i
        chosen, chosen_p = 1.01, 0.0
        for t in sorted({params.min_threshold, *top[mask].tolist()}):
            if t < params.min_threshold:
                continue
            sel = mask & (top >= t)
            if not sel.any():
                break
            p = float((y_arr[sel] == c).mean())
            if p >= params.target_precision:
                chosen, chosen_p = float(t), p
                break
        thresholds[c] = round(chosen, 6)
        precision[c] = round(chosen_p, 4)
    return thresholds, precision


def model_version(training_set_version: str, params: TrainParams) -> str:
    key = json.dumps(
        [training_set_version, FEATURE_VERSION, sklearn.__version__, asdict(params)],
        sort_keys=True,
    )
    return "clf-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def train(
    rows: list[Example], training_set_version: str, params: TrainParams | None = None
) -> tuple[Pipeline, ModelMeta]:
    params = params or TrainParams()
    if len({r["category"] for r in rows}) < 2:
        raise ValueError("training set needs at least two categories")
    data = augment(rows, params.augment, params.seed)
    x = [features(r) for r in data]
    y = [r["category"] for r in data]
    classes = sorted(set(y))
    folds = StratifiedKFold(n_splits=params.folds, shuffle=True, random_state=params.seed)
    oof = cross_val_predict(
        _model(params.seed, params.folds), x, y, cv=folds, method="predict_proba"
    )
    thresholds, precision = _thresholds(oof, y, classes, params)
    model = _model(params.seed, params.folds).fit(x, y)
    meta = ModelMeta(
        model_version=model_version(training_set_version, params),
        training_set_version=training_set_version,
        feature_version=FEATURE_VERSION,
        sklearn_version=sklearn.__version__,
        classes=classes,
        thresholds=thresholds,
        oof_precision=precision,
        examples=len(rows),
        augmented_examples=len(data),
        params=asdict(params),
    )
    return model, meta


def save(model: Pipeline, meta: ModelMeta, models_dir: Path) -> None:
    models_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, models_dir / MODEL_FILE)
    (models_dir / META_FILE).write_text(json.dumps(asdict(meta), indent=2) + "\n", "utf-8")


def load_meta(models_dir: Path) -> ModelMeta | None:
    path = models_dir / META_FILE
    if not path.is_file():
        return None
    return ModelMeta(**json.loads(path.read_text("utf-8")))


class Classifier:
    def __init__(self, models_dir: Path) -> None:
        meta = load_meta(models_dir)
        if meta is None:
            raise FileNotFoundError(f"no model in {models_dir}; run `d2d train` first")
        self.meta = meta
        self.model: Pipeline = joblib.load(models_dir / MODEL_FILE)

    def predict(self, examples: list[Example]) -> list[Prediction]:
        if not examples:
            return []
        proba = self.model.predict_proba([features(e) for e in examples])
        classes = list(self.model.classes_)
        out = []
        for row in proba:
            i = int(row.argmax())
            c, p = str(classes[i]), float(row[i])
            out.append(Prediction(c, round(p, 4), p < self.meta.thresholds.get(c, 1.01)))
        return out
