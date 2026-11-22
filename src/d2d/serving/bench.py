"""Latency, throughput, CPU and memory: native vs ONNX Runtime, single vs batched, in-process
and over HTTP (v2 infer against a real ``d2d model serve`` process on localhost).

Each in-process runtime runs in its own subprocess so peak RSS is measured per runtime.
"""

from __future__ import annotations

import json
import os
import resource
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from d2d.hitl.training_set import Example, read_training_set
from d2d.ml.train import Prediction

BATCHES = (1, 64)


def _rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 2**20 if sys.platform == "darwin" else peak / 2**10


def _percentiles(ms: list[float]) -> dict[str, float]:
    a = np.array(ms)
    return {f"p{q}_ms": round(float(np.percentile(a, q)), 4) for q in (50, 95, 99)}


def _loop(predict: Any, rows: list[Example], batch: int, n: int) -> dict[str, Any]:
    batches = [[rows[(i * batch + j) % len(rows)] for j in range(batch)] for i in range(n)]
    for b in batches[:20]:
        predict(b)
    lat = []
    cpu0, t0 = time.process_time(), time.perf_counter()
    for b in batches:
        s = time.perf_counter()
        predict(b)
        lat.append((time.perf_counter() - s) * 1000)
    wall, cpu = time.perf_counter() - t0, time.process_time() - cpu0
    return {
        "batch": batch,
        "requests": n,
        **_percentiles(lat),
        "rows_per_s": round(n * batch / wall, 1),
        "cpu_cores": round(cpu / wall, 2),
    }


def inproc(runtime: str, package: Path, training_set: Path, n_single: int) -> dict[str, Any]:
    """Runs inside the subprocess: load one runtime, then time vectorize + predict + decide."""
    base = _rss_mb()
    rows = read_training_set(training_set)
    manifest = json.loads((package / "manifest.json").read_text("utf-8"))
    predict: Callable[[list[Example]], list[Prediction]]
    if runtime == "onnx":
        from d2d.serving.runtime import OnnxClassifier

        predict = OnnxClassifier(package).predict
    elif manifest["framework"] == "sklearn":
        import tempfile

        from d2d.ml.train import Classifier, save, train

        model, meta = train(rows, manifest["training"]["training_set_version"])
        if meta.model_version != manifest["version"]:
            raise RuntimeError("retrained native model does not match the package version")
        with tempfile.TemporaryDirectory() as tmp:
            save(model, meta, Path(tmp))
            predict = Classifier(Path(tmp)).predict
    else:
        from d2d.serving.export import train_mlp
        from d2d.serving.runtime import decide

        exp = train_mlp(rows, manifest["training"]["training_set_version"])

        def predict(b: list[Example]) -> list[Prediction]:
            return decide(exp.native_proba(b), exp.classes, exp.thresholds)

    loaded = _rss_mb()
    results = [
        _loop(predict, rows, b, n_single if b == 1 else max(n_single // 10, 50)) for b in BATCHES
    ]
    return {
        "runtime": runtime,
        "framework": manifest["framework"],
        "version": manifest["version"],
        "rss_mb": {
            "before_load": round(base, 1),
            "after_load": round(loaded, 1),
            "peak": round(_rss_mb(), 1),
        },
        "results": results,
    }


def run_inproc(runtime: str, package: Path, training_set: Path, n_single: int) -> dict[str, Any]:
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "d2d.serving.bench",
            runtime,
            str(package),
            str(training_set),
            str(n_single),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result: dict[str, Any] = json.loads(out.stdout.strip().splitlines()[-1])
    return result


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _ps(pid: int) -> tuple[float, float]:
    """(RSS MB, cumulative CPU seconds) of a process, via ps."""
    out = subprocess.run(
        ["ps", "-o", "rss=,time=", "-p", str(pid)], check=True, capture_output=True, text=True
    ).stdout.split()
    parts = [float(p) for p in out[1].replace("-", ":").split(":")]
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return float(out[0]) / 1024, secs


def run_http(
    registry: Path, name: str, rows: list[Example], n_single: int, concurrency: int
) -> dict[str, Any]:
    import httpx

    port = _free_port()
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    cmd = [sys.executable, "-c", "from d2d.cli import app; app()", "model", "serve"]
    cmd += ["--registry", str(registry), "--port", str(port)]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=base, timeout=10) as c:
            for _ in range(200):
                try:
                    if c.get("/v2/health/ready").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("model server did not become ready")
            rss0, cpu0 = _ps(proc.pid)

            def body(batch: list[Example], rid: str) -> dict[str, Any]:
                data = [
                    json.dumps({"events": r["events"], "link_reasons": r["link_reasons"]})
                    for r in batch
                ]
                return {
                    "id": rid,
                    "inputs": [
                        {"name": "story", "datatype": "BYTES", "shape": [len(data)], "data": data}
                    ],
                }

            results = []
            for batch_size in BATCHES:
                n = n_single if batch_size == 1 else max(n_single // 10, 50)
                batches = [
                    [rows[(i * batch_size + j) % len(rows)] for j in range(batch_size)]
                    for i in range(n)
                ]
                for i, b in enumerate(batches[:20]):
                    c.post(f"/v2/models/{name}/infer", json=body(b, f"warm-{i}"))
                lat, errors = [], 0
                t0 = time.perf_counter()
                for i, b in enumerate(batches):
                    s = time.perf_counter()
                    r = c.post(f"/v2/models/{name}/infer", json=body(b, f"b{batch_size}-{i}"))
                    lat.append((time.perf_counter() - s) * 1000)
                    errors += r.status_code != 200
                wall = time.perf_counter() - t0
                results.append(
                    {
                        "batch": batch_size,
                        "clients": 1,
                        "requests": n,
                        **_percentiles(lat),
                        "rows_per_s": round(n * batch_size / wall, 1),
                        "error_rate": errors / n,
                    }
                )
            results.append(_concurrent(base, name, rows, n_single, concurrency, body))
            rss1, cpu1 = _ps(proc.pid)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    return {
        "server": "d2d model serve (uvicorn, 1 worker), localhost",
        "results": results,
        "server_rss_mb": {"ready": round(rss0, 1), "after": round(rss1, 1)},
        "server_cpu_s": round(cpu1 - cpu0, 2),
    }


def _concurrent(
    base: str, name: str, rows: list[Example], n: int, clients: int, body: Any
) -> dict[str, Any]:
    import httpx

    lat: list[float] = []
    errors = [0]
    lock = threading.Lock()

    def worker(k: int) -> None:
        with httpx.Client(base_url=base, timeout=10) as c:
            for i in range(k, n, clients):
                s = time.perf_counter()
                r = c.post(f"/v2/models/{name}/infer", json=body([rows[i % len(rows)]], f"c-{i}"))
                with lock:
                    lat.append((time.perf_counter() - s) * 1000)
                    errors[0] += r.status_code != 200

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(clients)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0
    return {
        "batch": 1,
        "clients": clients,
        "requests": n,
        **_percentiles(lat),
        "rows_per_s": round(n / wall, 1),
        "error_rate": errors[0] / n,
    }


if __name__ == "__main__":
    runtime, package, training_set, n = sys.argv[1:5]
    print(json.dumps(inproc(runtime, Path(package), Path(training_set), int(n))))
