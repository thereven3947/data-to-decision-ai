# ADR 0003 · ONNX model packages with a manifest, served over Open Inference Protocol v2

Status: accepted (2026-11-21)

## Context

The story classifier was a scikit-learn `joblib` file loaded in-process by `d2d classify`.
That ties serving to the training library and version, gives no schema to check requests against,
and has no record of what data, calibration and thresholds a deployed model came from.
M16 asks for a framework-neutral artifact, one serving path for batch and live traffic,
and safe rollout (shadow, canary, rollback) with drift monitoring.

## Decision

- A model package is a directory `models/registry/packages/<name>/<version>/` with
  `model.onnx`, `manifest.json` and `MODEL_CARD.md`. Packages are immutable; the version is a hash
  of the training set and parameters.
- `model.onnx` outputs calibrated probabilities. The abstain thresholds and the feature vocabulary
  stay in the manifest, so the decision rule is identical in batch (`d2d classify --package`) and
  live (`/v2/models/.../infer`).
- The manifest records the input/output schema, training-set hash, calibration, thresholds,
  golden eval, native-vs-ONNX parity and file SHA-256s. `d2d model validate` checks all of it, and
  the server refuses to load a package that fails.
- Both scikit-learn (skl2onnx) and PyTorch (torch.onnx, dynamo) export to the same format, opset 18.
- The server speaks the Open Inference Protocol v2 (the KServe and Triton REST protocol), so a
  package can move to KServe or Triton without client changes. Rollout state (`stable`,
  `candidate`, `mode`, `weight`, history) is one JSON file next to the packages.
- ONNX Runtime, skl2onnx and torch live in the optional `serving` dependency group. The default
  install and the existing `joblib` path are unchanged.

## Consequences

- ONNX Runtime is the only runtime in production; training libraries are not needed to serve.
- The DictVectorizer step is not exported; it is a JSON vocabulary, applied in Python. A request can
  also send the FP32 feature tensor directly.
- onnxruntime has no wheel for Python 3.15 yet; the `serving` group is marked `< 3.15` and the CI job
  uses Python 3.12.
- The in-repo server is a reference implementation of v2. It has not been run under KServe or
  Triton.
