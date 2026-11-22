"""Export story classifiers to ONNX: the calibrated scikit-learn model and a small PyTorch MLP.

Feature dicts are vectorized in Python with the package vocabulary (a one-hot lookup), so the ONNX
graph takes a dense float32 matrix ``features`` [batch, n_features] and returns calibrated
``probabilities`` [batch, n_classes]. Both runtimes are imported lazily (optional group).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from sklearn.feature_extraction import DictVectorizer
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline

from d2d.hitl.training_set import Example
from d2d.ml.features import FEATURE_VERSION, features
from d2d.ml.train import ModelMeta, TrainParams, _thresholds, augment

OPSET = 18
INPUT = "features"
OUTPUT = "probabilities"


@dataclass
class Exported:
    framework: str
    framework_version: str
    model_version: str
    onnx: bytes
    vocabulary: list[str]
    classes: list[str]
    thresholds: dict[str, float]
    oof_precision: dict[str, float]
    calibration: dict[str, Any]
    training_set_version: str
    examples: int
    augmented_examples: int
    params: dict[str, Any]
    native_proba: Callable[[list[Example]], np.ndarray] = field(repr=False)


def vectorize(rows: Sequence[Example], vocabulary: Sequence[str]) -> np.ndarray:
    index = {name: i for i, name in enumerate(vocabulary)}
    x = np.zeros((len(rows), len(vocabulary)), dtype=np.float32)
    for r, row in enumerate(rows):
        for name, value in features(row).items():
            if (i := index.get(name)) is not None:
                x[r, i] = value
    return x


def export_sklearn(model: Pipeline, meta: ModelMeta) -> Exported:
    """The deployed ``d2d train`` model: DictVectorizer + CalibratedClassifierCV (sigmoid)."""
    from skl2onnx import __version__ as skl2onnx_version
    from skl2onnx import to_onnx
    from skl2onnx.common.data_types import FloatTensorType

    dv, calibrated = model.steps[0][1], model.steps[-1][1]
    vocabulary = [str(n) for n in dv.feature_names_]
    onx = to_onnx(
        calibrated,
        initial_types=[(INPUT, FloatTensorType([None, len(vocabulary)]))],
        options={id(calibrated): {"zipmap": False}},
        target_opset=OPSET,
    )
    if [str(c) for c in calibrated.classes_] != meta.classes:
        raise ValueError("model classes differ from the model metadata")
    return Exported(
        framework="sklearn",
        framework_version=meta.sklearn_version,
        model_version=meta.model_version,
        onnx=onx.SerializeToString(),
        vocabulary=vocabulary,
        classes=meta.classes,
        thresholds=meta.thresholds,
        oof_precision=meta.oof_precision,
        calibration={"method": "sigmoid", "where": "in graph (CalibratedClassifierCV)"},
        training_set_version=meta.training_set_version,
        examples=meta.examples,
        augmented_examples=meta.augmented_examples,
        params={**meta.params, "converter": f"skl2onnx {skl2onnx_version}"},
        native_proba=lambda rows: np.asarray(model.predict_proba([features(r) for r in rows])),
    )


@dataclass(frozen=True)
class MLPParams:
    hidden: int = 64
    epochs: int = 300
    learning_rate: float = 0.01
    weight_decay: float = 1e-3
    augment: int = 8
    folds: int = 3
    seed: int = 7
    target_precision: float = 0.9
    min_threshold: float = 0.6


def mlp_version(training_set_version: str, params: MLPParams, torch_version: str) -> str:
    key = json.dumps(
        [training_set_version, FEATURE_VERSION, torch_version, asdict(params)], sort_keys=True
    )
    return "mlp-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def train_mlp(
    rows: list[Example], training_set_version: str, params: MLPParams | None = None
) -> Exported:
    """Train a one-hidden-layer MLP, temperature-scale it and pick thresholds out of fold.

    Folds are grouped by story, so augmented copies of a story never sit on both sides.
    """
    import torch

    params = params or MLPParams()
    data = augment(rows, params.augment, params.seed)
    dv = DictVectorizer(sparse=False).fit([features(r) for r in data])
    vocabulary = [str(n) for n in dv.feature_names_]
    x = vectorize(data, vocabulary)
    classes = sorted({r["category"] for r in data})
    y = np.array([classes.index(r["category"]) for r in data])
    groups = [r["story_id"] for r in data]

    def fit(xs: np.ndarray, ys: np.ndarray) -> torch.nn.Module:
        torch.manual_seed(params.seed)
        net = torch.nn.Sequential(
            torch.nn.Linear(len(vocabulary), params.hidden),
            torch.nn.ReLU(),
            torch.nn.Linear(params.hidden, len(classes)),
        )
        opt = torch.optim.Adam(
            net.parameters(), lr=params.learning_rate, weight_decay=params.weight_decay
        )
        xt, yt = torch.from_numpy(xs), torch.from_numpy(ys)
        for _ in range(params.epochs):
            opt.zero_grad()
            loss = torch.nn.functional.cross_entropy(net(xt), yt)
            loss.backward()
            opt.step()
        return net.eval()

    oof = np.zeros((len(data), len(classes)), dtype=np.float32)
    folds = StratifiedGroupKFold(n_splits=params.folds, shuffle=True, random_state=params.seed)
    for tr, va in folds.split(x, y, groups):
        net = fit(x[tr], y[tr])
        with torch.no_grad():
            oof[va] = net(torch.from_numpy(x[va])).numpy()
    temperature = _fit_temperature(oof, y)
    oof_proba = _softmax(oof / temperature)
    thresholds, precision = _thresholds(
        oof_proba,
        [classes[i] for i in y],
        classes,
        TrainParams(params.target_precision, params.min_threshold, params.augment, params.seed),
    )

    class Calibrated(torch.nn.Module):  # type: ignore[misc]
        def __init__(self, net: torch.nn.Module, t: float) -> None:
            super().__init__()
            self.net, self.t = net, t

        def forward(self, xs: torch.Tensor) -> torch.Tensor:
            return torch.softmax(self.net(xs) / self.t, dim=1)

    calibrated = Calibrated(fit(x, y), temperature).eval()

    def native(rs: list[Example]) -> np.ndarray:
        with torch.no_grad():
            out: np.ndarray = calibrated(torch.from_numpy(vectorize(rs, vocabulary))).numpy()
        return out

    version = torch.__version__
    return Exported(
        framework="pytorch",
        framework_version=version,
        model_version=mlp_version(training_set_version, params, version),
        onnx=_export_torch(calibrated, len(vocabulary)),
        vocabulary=vocabulary,
        classes=classes,
        thresholds=thresholds,
        oof_precision=precision,
        calibration={"method": "temperature", "temperature": round(temperature, 4)},
        training_set_version=training_set_version,
        examples=len(rows),
        augmented_examples=len(data),
        params=asdict(params),
        native_proba=native,
    )


def _softmax(z: np.ndarray) -> np.ndarray:
    e = np.exp(z - z.max(axis=1, keepdims=True))
    out: np.ndarray = e / e.sum(axis=1, keepdims=True)
    return out


def _fit_temperature(logits: np.ndarray, y: np.ndarray) -> float:
    """Temperature that minimizes the out-of-fold negative log-likelihood (grid search)."""
    best, best_nll = 1.0, float("inf")
    for t in np.round(np.arange(0.25, 5.01, 0.05), 2):
        p = _softmax(logits / t)[np.arange(len(y)), y]
        nll = float(-np.log(np.clip(p, 1e-12, None)).mean())
        if nll < best_nll:
            best, best_nll = float(t), nll
    return best


def _export_torch(module: Any, n_features: int) -> bytes:
    import torch

    program = torch.onnx.export(
        module,
        (torch.zeros(2, n_features),),
        input_names=[INPUT],
        output_names=[OUTPUT],
        dynamic_shapes=({0: torch.export.Dim("batch")},),
        opset_version=OPSET,
        dynamo=True,
        verbose=False,
    )
    data: bytes = program.model_proto.SerializeToString()
    return data
