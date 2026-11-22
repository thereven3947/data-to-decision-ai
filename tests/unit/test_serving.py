"""M16 model serving: ONNX packages, v2 API, rollout guardrails, drift (`--group serving`)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("onnxruntime")
pytest.importorskip("skl2onnx")

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from d2d.config import Household
from d2d.hitl.training_set import (
    Example,
    build_training_set,
    training_set_version,
    write_training_set,
)
from d2d.ml.train import TrainParams, train
from d2d.serving.api import create_model_app, v2_router
from d2d.serving.data import shift
from d2d.serving.drift import drift_report, ece, psi
from d2d.serving.export import export_sklearn
from d2d.serving.package import validate, write_package
from d2d.serving.rollout import run_rollout
from d2d.serving.runtime import OnnxClassifier
from d2d.serving.server import InferError, ModelServer, Registry, bucket, decode_inputs
from d2d.serving.settings import DriftPolicy, PackageGates, RolloutPolicy

NAME = "story-classifier"
GATES = PackageGates(NAME, min_precision=0.9, min_coverage=0.7, max_parity_diff=1e-5)
POLICY = RolloutPolicy(
    canary_steps=(0.5, 1.0),
    requests_per_step=60,
    shadow_requests=40,
    max_error_rate=0.01,
    max_p95_ms=1000.0,
    min_precision=0.9,
    min_agreement=0.8,
)


@pytest.fixture(scope="module")
def rows(run_data: Path, household: Household) -> list[Example]:
    return build_training_set(run_data, household, None, True)


@pytest.fixture(scope="module")
def training_set(rows: list[Example], tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("ts")
    write_training_set(rows, out)
    return out / "training_set.jsonl"


def _package(registry: Path, rows: list[Example], ts: Path, seed: int = 7) -> Path:
    exp = export_sklearn(*train(rows, training_set_version(rows), TrainParams(seed=seed)))
    return write_package(exp, registry, NAME, ts, rows, rows, {"test": True})


@pytest.fixture(scope="module")
def registry(
    rows: list[Example], training_set: Path, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    root = tmp_path_factory.mktemp("registry")
    _package(root, rows, training_set, seed=7)
    _package(root, rows, training_set, seed=8)
    return root


def _fresh(registry: Path, tmp_path: Path) -> ModelServer:
    """Copy of the module registry, so each test starts with no rollout state."""
    import shutil

    shutil.copytree(registry / "packages", tmp_path / "packages")
    return ModelServer(Registry(tmp_path), GATES)


def test_sklearn_package_parity_and_validation(registry: Path, rows: list[Example]) -> None:
    versions = Registry(registry).versions(NAME)
    assert len(versions) == 2
    pkg = registry / "packages" / NAME / versions[0]
    m = json.loads((pkg / "manifest.json").read_text())
    assert m["parity"]["decisions_identical"]
    assert m["parity"]["max_abs_diff"] < 1e-5
    assert m["inputs"][0]["shape"] == [-1, len(m["vocabulary"])]
    assert validate(pkg, rows, GATES) == []
    assert "All training and evaluation data is simulated" in (pkg / "MODEL_CARD.md").read_text()


def test_validate_catches_tampering_and_gate(
    registry: Path, rows: list[Example], tmp_path: Path
) -> None:
    import shutil

    version = Registry(registry).versions(NAME)[0]
    pkg = tmp_path / NAME / version
    shutil.copytree(registry / "packages" / NAME / version, pkg)
    strict = PackageGates(NAME, min_precision=0.9, min_coverage=1.01, max_parity_diff=1e-5)
    assert any("coverage" in e for e in validate(pkg, rows, strict))
    with (pkg / "model.onnx").open("ab") as f:
        f.write(b"\0")
    assert any("sha256" in e for e in validate(pkg, rows, GATES))
    (pkg / "manifest.json").unlink()
    assert validate(pkg, rows, GATES) == ["manifest.json missing"]


def test_pytorch_mlp_parity(rows: list[Example], training_set: Path, tmp_path: Path) -> None:
    pytest.importorskip("torch")
    pytest.importorskip("onnxscript")
    from d2d.serving.export import MLPParams, train_mlp

    exp = train_mlp(rows, training_set_version(rows), MLPParams(epochs=60, folds=2))
    pkg = write_package(exp, tmp_path, NAME, training_set, rows, rows, {})
    m = json.loads((pkg / "manifest.json").read_text())
    assert m["framework"] == "pytorch"
    assert m["parity"]["decisions_identical"]
    assert m["parity"]["max_abs_diff"] < 1e-5


def test_v2_protocol(registry: Path, rows: list[Example], tmp_path: Path) -> None:
    _fresh(registry, tmp_path)
    app = create_model_app(tmp_path, GATES, hosts=["testserver"])
    server: ModelServer = app.state.server
    client = TestClient(app)

    assert client.get("/v2/health/live").json() == {"live": True}
    assert client.get(f"/v2/models/{NAME}/ready").status_code == 503  # nothing deployed yet
    stable = Registry(tmp_path).versions(NAME)[0]
    assert run_rollout(server, NAME, stable, rows, rows, POLICY)["outcome"] == "initial"
    assert client.get(f"/v2/models/{NAME}/ready").json() == {"name": NAME, "ready": True}

    meta = client.get(f"/v2/models/{NAME}").json()
    assert meta["platform"] == "onnxruntime_onnx"
    assert {i["name"] for i in meta["inputs"]} == {"features", "story"}

    stories = [json.dumps({"events": r["events"], "link_reasons": r["link_reasons"]}) for r in rows]
    story_in = {"name": "story", "datatype": "BYTES", "shape": [3], "data": stories[:3]}
    body = {"id": "r1", "inputs": [story_in]}
    out = client.post(f"/v2/models/{NAME}/infer", json=body).json()
    assert (out["model_version"], out["id"]) == (stable, "r1")
    by_name = {o["name"]: o for o in out["outputs"]}
    n_classes = meta["outputs"][0]["shape"][1]
    assert by_name["probabilities"]["shape"] == [3, n_classes]
    assert len(by_name["probabilities"]["data"]) == 3 * n_classes
    assert len(by_name["label"]["data"]) == 3

    k = meta["inputs"][0]["shape"][1]
    feats = {
        "inputs": [{"name": "features", "datatype": "FP32", "shape": [1, k], "data": [0.0] * k}],
        "outputs": [{"name": "label"}],
    }
    out = client.post(f"/v2/models/{NAME}/versions/{stable}/infer", json=feats).json()
    assert [o["name"] for o in out["outputs"]] == ["label"]

    bad = {"inputs": [{"name": "features", "datatype": "FP32", "shape": [1, k + 1], "data": []}]}
    assert client.post(f"/v2/models/{NAME}/infer", json=bad).status_code == 400
    assert client.post("/v2/models/nope/versions/x/infer", json=body).status_code == 404


def test_v2_router_honours_auth_dependency(registry: Path, tmp_path: Path) -> None:
    def deny() -> None:
        raise HTTPException(401, "login required")

    app = FastAPI()
    app.include_router(v2_router(_fresh(registry, tmp_path), dependencies=[deny]))
    assert TestClient(app).get("/v2/health/live").status_code == 401


def test_rollout_promotes_and_rolls_back(
    registry: Path, rows: list[Example], tmp_path: Path
) -> None:
    server = _fresh(registry, tmp_path)
    a, b = Registry(tmp_path).versions(NAME)
    assert run_rollout(server, NAME, a, rows, rows, POLICY)["outcome"] == "initial"

    server.faults[(NAME, b)] = 1.0
    result = run_rollout(server, NAME, b, rows, rows, POLICY, skip_shadow=True)
    assert result["outcome"] == "rolled_back"
    assert result["stable"] == a
    assert "error rate" in result["reason"]
    state = Registry(tmp_path).state(NAME)
    assert (state.stable, state.candidate, state.weight) == (a, None, 0.0)

    server.faults.clear()
    result = run_rollout(server, NAME, b, rows, rows, POLICY)
    assert result["outcome"] == "promoted", result["reason"]
    phases = [s.phase for s in result["steps"]]
    assert phases == ["gate", "shadow", "canary", "canary"]
    assert Registry(tmp_path).state(NAME).stable == b
    assert [h["outcome"] for h in Registry(tmp_path).state(NAME).history] == [
        "initial",
        "rolled_back",
        "promoted",
    ]


def test_rollout_rejects_invalid_candidate(
    registry: Path, rows: list[Example], tmp_path: Path
) -> None:
    server = _fresh(registry, tmp_path)
    a, b = Registry(tmp_path).versions(NAME)
    run_rollout(server, NAME, a, rows, rows, POLICY)
    (tmp_path / "packages" / NAME / b / "MODEL_CARD.md").write_text("edited")
    result = run_rollout(server, NAME, b, rows, rows, POLICY)
    assert result["outcome"] == "rejected"
    assert Registry(tmp_path).state(NAME).stable == a


def test_drift_alerts_on_shift_only(registry: Path, rows: list[Example]) -> None:
    pkg = registry / "packages" / NAME / Registry(registry).versions(NAME)[0]
    clf = OnnxClassifier(pkg)
    policy = DriftPolicy(psi_alert=0.25, ks_pvalue=0.01, ece_increase=0.05, min_window=10)
    assert drift_report(clf, rows, shift(rows, "none"), policy)["alert"] is False
    report = drift_report(clf, rows, shift(rows, "linking_bug"), policy)
    assert report["alert"] is True
    assert any("input drift" in a for a in report["alerts"])
    small = DriftPolicy(psi_alert=0.25, ks_pvalue=0.01, ece_increase=0.05, min_window=1000)
    assert drift_report(clf, rows, shift(rows, "linking_bug"), small)["alert"] is False


def test_psi_ece_bucket() -> None:
    assert psi([0.5, 0.5], [0.5, 0.5]) == 0.0
    assert psi([0.9, 0.1], [0.1, 0.9]) > 1.0
    assert ece(np.array([1.0, 1.0]), np.array([1.0, 1.0])) == 0.0
    assert ece(np.array([0.9, 0.9]), np.array([0.0, 0.0])) == pytest.approx(0.9)
    assert bucket("abc") == bucket("abc")
    assert 0.0 <= bucket("abc") < 1.0
    share = np.mean([bucket(f"r{i}") < 0.25 for i in range(4000)])
    assert 0.22 < share < 0.28


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"inputs": []},
        {"inputs": [{"name": "features", "datatype": "FP32", "shape": [0, 3], "data": []}]},
        {"inputs": [{"name": "features", "datatype": "FP32", "shape": [2000, 3], "data": []}]},
        {"inputs": [{"name": "story", "datatype": "BYTES", "shape": [1], "data": ["{}"]}]},
        {"inputs": [{"name": "story", "datatype": "BYTES", "shape": [1], "data": ["not json"]}]},
        {"inputs": [{"name": "x", "datatype": "INT8", "shape": [1], "data": [1]}]},
    ],
)
def test_decode_inputs_rejects_bad_requests(registry: Path, body: dict[str, object]) -> None:
    pkg = registry / "packages" / NAME / Registry(registry).versions(NAME)[0]
    with pytest.raises(InferError):
        decode_inputs(body, OnnxClassifier(pkg))


def test_decode_inputs_rejects_non_finite(registry: Path) -> None:
    pkg = registry / "packages" / NAME / Registry(registry).versions(NAME)[0]
    clf = OnnxClassifier(pkg)
    k = len(clf.vocabulary)
    data = [float("nan")] + [0.0] * (k - 1)
    body = {"inputs": [{"name": "features", "datatype": "FP32", "shape": [1, k], "data": data}]}
    with pytest.raises(InferError):
        decode_inputs(body, clf)
