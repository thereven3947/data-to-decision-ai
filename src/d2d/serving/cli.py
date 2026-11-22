"""``d2d model``: package, validate, serve, bench, rollout and drift (M16)."""

from __future__ import annotations

import json
import random
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from d2d.config import config_dir
from d2d.hitl.training_set import (
    Example,
    read_training_set,
    training_set_version,
    write_training_set,
)
from d2d.serving.data import SHIFTS, TRAFFIC_START, TRAIN_START, labeled_runs
from d2d.serving.data import shift as shift_rows
from d2d.serving.settings import load_serving

if TYPE_CHECKING:
    from d2d.serving.server import ModelServer

app = typer.Typer(no_args_is_help=True, help="M16 model packages, serving, rollout and drift.")

Registry = Annotated[Path, typer.Option(help="Registry directory (gitignored).")]
DataDir = Annotated[Path, typer.Option(help="Simulated runs for serving (gitignored).")]
ConfigDir = Annotated[Path | None, typer.Option(help="Config directory (default ./config).")]
Golden = Annotated[Path, typer.Option(help="Frozen golden stories.")]
DEFAULT_REGISTRY = Path("models/registry")
DEFAULT_DATA = Path("data/serving")
DEFAULT_GOLDEN = Path("evals/golden/stories.jsonl")


@app.command("package")
def package_cmd(
    framework: Annotated[str, typer.Option(help="sklearn or pytorch.")] = "sklearn",
    registry: Registry = DEFAULT_REGISTRY,
    data_dir: DataDir = DEFAULT_DATA,
    golden: Golden = DEFAULT_GOLDEN,
    label_noise: Annotated[
        float, typer.Option(help="Demo only: relabel this share of stories at random.")
    ] = 0.0,
    config: ConfigDir = None,
) -> None:
    """Train on simulated runs, export to ONNX, check parity and golden eval, write a package."""
    from d2d.ml.train import train
    from d2d.serving.export import export_sklearn, train_mlp
    from d2d.serving.package import validate, write_package

    cfg_dir = config_dir(config)
    cfg = load_serving(cfg_dir / "serving.toml")
    rows = labeled_runs(
        cfg_dir, data_dir / "runs", cfg.data.train_seeds, TRAIN_START, cfg.data.days, "train"
    )
    sources: dict[str, object] = {
        "simulated_runs": [f"train-{s}" for s in cfg.data.train_seeds],
        "days_per_run": cfg.data.days,
    }
    if label_noise:
        rng = random.Random(0)
        cats = sorted({r["category"] for r in rows})
        rows = [
            {**r, "category": rng.choice(cats)} if rng.random() < label_noise else r for r in rows
        ]
        sources["label_noise"] = label_noise
    ts_dir = data_dir / "training_sets" / training_set_version(rows)
    write_training_set(rows, ts_dir)
    ts_path = ts_dir / "training_set.jsonl"
    rows = read_training_set(ts_path)
    version = training_set_version(rows)
    if framework == "sklearn":
        exp = export_sklearn(*train(rows, version))
    elif framework == "pytorch":
        exp = train_mlp(rows, version)
    else:
        raise typer.BadParameter("framework must be sklearn or pytorch")
    golden_rows = read_training_set(golden)
    pkg = write_package(exp, registry, cfg.package.name, ts_path, golden_rows, rows, sources)
    m = json.loads((pkg / "manifest.json").read_text("utf-8"))
    g, p = m["eval"]["golden"], m["parity"]
    typer.echo(
        f"{framework} {exp.model_version}: {exp.examples} stories, {len(exp.vocabulary)} features,"
        f" {len(exp.classes)} classes -> {pkg}\n"
        f"golden coverage {g['coverage']:.3f}, min precision {g['min_precision']:.3f}; "
        f"parity max |diff| {p['max_abs_diff']:.2e} on {p['rows']} stories, "
        f"decisions identical: {p['decisions_identical']}"
    )
    errors = validate(pkg, golden_rows, cfg.package)
    typer.echo("validate: PASS" if not errors else "validate: FAIL\n  " + "\n  ".join(errors))
    if errors:
        raise typer.Exit(code=1)


@app.command("validate")
def validate_cmd(
    package: Annotated[Path, typer.Argument(help="Package directory <name>/<version>.")],
    golden: Golden = DEFAULT_GOLDEN,
    config: ConfigDir = None,
) -> None:
    """Check a package (files, hashes, schema, parity, eval gate); exit 1 if it cannot be served."""
    from d2d.serving.package import validate

    cfg = load_serving(config_dir(config) / "serving.toml")
    errors = validate(package, read_training_set(golden), cfg.package)
    for e in errors:
        typer.echo(f"  {e}")
    typer.echo("validate: PASS" if not errors else "validate: FAIL")
    if errors:
        raise typer.Exit(code=1)


def _traffic(cfg_dir: Path, data_dir: Path) -> tuple[list[Example], list[Example], Path]:
    """(reference, window, last run dir): labeled traffic runs split in halves by run."""
    cfg = load_serving(cfg_dir / "serving.toml")
    seeds = cfg.data.traffic_seeds
    half = len(seeds) // 2
    runs = data_dir / "runs"
    ref = labeled_runs(cfg_dir, runs, seeds[:half], TRAFFIC_START, cfg.data.days, "traffic")
    start = TRAFFIC_START + timedelta(days=7 * half)
    win = labeled_runs(cfg_dir, runs, seeds[half:], start, cfg.data.days, "traffic")
    return ref, win, runs / f"traffic-{seeds[-1]}"


def _server(registry: Path, cfg_dir: Path) -> ModelServer:
    from d2d.serving.server import ModelServer, Registry

    return ModelServer(Registry(registry), load_serving(cfg_dir / "serving.toml").package)


@app.command("serve")
def serve_cmd(
    registry: Registry = DEFAULT_REGISTRY,
    host: Annotated[str, typer.Option(help="Bind address (keep 127.0.0.1).")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port.")] = 8100,
    config: ConfigDir = None,
) -> None:
    """Serve the registry over the Open Inference Protocol v2 (KServe/Triton compatible)."""
    import uvicorn

    from d2d.serving.api import create_model_app

    gates = load_serving(config_dir(config) / "serving.toml").package
    uvicorn.run(create_model_app(registry, gates), host=host, port=port, log_level="warning")


@app.command("status")
def status_cmd(registry: Registry = DEFAULT_REGISTRY) -> None:
    """Packages, stable and candidate versions, and the last rollout decisions."""
    from d2d.serving.server import Registry as Reg

    reg = Reg(registry)
    states = reg.states()
    for name in reg.names():
        s = states.get(name)
        typer.echo(f"{name}: versions {', '.join(reg.versions(name))}")
        if s:
            typer.echo(f"  stable {s.stable}  candidate {s.candidate}  {s.mode} {s.weight:.0%}")
            for h in s.history[-5:]:
                typer.echo(f"  {h['at']}  {h['candidate']}: {h['outcome']} ({h['reason']})")


def _stable_package(registry: Path, cfg_dir: Path) -> Path:
    from d2d.serving.server import Registry as Reg

    name = load_serving(cfg_dir / "serving.toml").package.name
    stable = Reg(registry).state(name).stable
    if stable is None:
        raise typer.BadParameter("no stable version; run `d2d model rollout <version>` first")
    return registry / "packages" / name / stable


@app.command("bench")
def bench_cmd(
    registry: Registry = DEFAULT_REGISTRY,
    package: Annotated[Path | None, typer.Option(help="Package (default: stable).")] = None,
    requests: Annotated[int, typer.Option(help="Single-story requests per run.")] = 2000,
    clients: Annotated[int, typer.Option(help="Concurrent HTTP clients.")] = 8,
    out: Annotated[Path, typer.Option(help="Results JSON.")] = DEFAULT_DATA / "bench.json",
    config: ConfigDir = None,
) -> None:
    """Native vs ONNX Runtime and HTTP v2 latency, throughput, CPU and memory vs targets."""
    from d2d.serving.bench import run_http, run_inproc

    cfg_dir = config_dir(config)
    cfg = load_serving(cfg_dir / "serving.toml")
    pkg = package or _stable_package(registry, cfg_dir)
    manifest = json.loads((pkg / "manifest.json").read_text("utf-8"))
    ts = DEFAULT_DATA / "training_sets" / manifest["training"]["training_set_version"]
    ts_path = ts / "training_set.jsonl"
    rows = read_training_set(ts_path)
    result: dict[str, Any] = {
        "package": f"{manifest['name']}/{manifest['version']}",
        "inproc": [run_inproc(rt, pkg, ts_path, requests) for rt in ("native", "onnx")],
        "http": run_http(registry, manifest["name"], rows, requests, clients)
        if package is None
        else None,
    }
    t = cfg.targets
    checks = []
    onnx_single = result["inproc"][1]["results"][0]
    checks.append(("in-process p95 (1 story)", onnx_single["p95_ms"], t.inproc_p95_ms, "<="))
    if result["http"]:
        single, batch = result["http"]["results"][0], result["http"]["results"][1]
        errs = max(r["error_rate"] for r in result["http"]["results"])
        checks += [
            ("HTTP p50 (1 story)", single["p50_ms"], t.http_p50_ms, "<="),
            ("HTTP p95 (1 story)", single["p95_ms"], t.http_p95_ms, "<="),
            ("HTTP p99 (1 story)", single["p99_ms"], t.http_p99_ms, "<="),
            ("HTTP rows/s (64/request)", batch["rows_per_s"], t.http_batch_rows_per_s, ">="),
            ("HTTP error rate", errs, t.max_error_rate, "<="),
        ]
    result["targets"] = [
        {"metric": m, "value": v, "target": tgt, "pass": v <= tgt if op == "<=" else v >= tgt}
        for m, v, tgt, op in checks
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", "utf-8")
    for r in result["inproc"]:
        for x in r["results"]:
            typer.echo(
                f"in-process {r['runtime']:<6} batch {x['batch']:>3}: p50 {x['p50_ms']:.3f} "
                f"p95 {x['p95_ms']:.3f} p99 {x['p99_ms']:.3f} ms, {x['rows_per_s']:.0f} rows/s, "
                f"{x['cpu_cores']} cores, peak RSS {r['rss_mb']['peak']} MB"
            )
    if result["http"]:
        for x in result["http"]["results"]:
            typer.echo(
                f"HTTP batch {x['batch']:>3} x{x['clients']} clients: p50 {x['p50_ms']:.2f} "
                f"p95 {x['p95_ms']:.2f} p99 {x['p99_ms']:.2f} ms, {x['rows_per_s']:.0f} rows/s, "
                f"errors {x['error_rate']:.3%}"
            )
    for c in result["targets"]:
        typer.echo(
            f"target {c['metric']}: {c['value']} vs {c['target']} -> "
            f"{'PASS' if c['pass'] else 'FAIL'}"
        )
    typer.echo(f"-> {out}")


@app.command("rollout")
def rollout_cmd(
    version: Annotated[str, typer.Argument(help="Candidate package version.")],
    registry: Registry = DEFAULT_REGISTRY,
    data_dir: DataDir = DEFAULT_DATA,
    golden: Golden = DEFAULT_GOLDEN,
    skip_shadow: Annotated[bool, typer.Option(help="Go straight to the canary steps.")] = False,
    inject_errors: Annotated[
        float, typer.Option(help="Demo only: share of candidate calls that fail.")
    ] = 0.0,
    config: ConfigDir = None,
) -> None:
    """Gate, shadow and canary a candidate on simulated traffic; promote or roll back."""
    from d2d.serving.rollout import run_rollout

    cfg_dir = config_dir(config)
    cfg = load_serving(cfg_dir / "serving.toml")
    server = _server(registry, cfg_dir)
    name = cfg.package.name
    if inject_errors:
        server.faults[(name, version)] = inject_errors
    ref, win, _ = _traffic(cfg_dir, data_dir)
    result = run_rollout(
        server, name, version, ref + win, read_training_set(golden), cfg.rollout, skip_shadow
    )
    for s in result["steps"]:
        extra = "" if s.precision is None else f" precision {s.precision:.3f}"
        extra += "" if s.agreement is None else f" agreement {s.agreement:.3f}"
        typer.echo(
            f"{s.phase:<7}{s.weight:>5.0%}  {s.candidate_requests:>4} candidate requests, "
            f"errors {s.error_rate:.3f}, p95 {s.p95_ms:.2f} ms{extra}  "
            f"{'ok' if s.ok else 'FAIL: ' + s.reason}"
        )
    typer.echo(f"{result['outcome']}: {result['reason']}; stable = {result['stable']}")
    if result["outcome"] in ("rejected", "rolled_back"):
        raise typer.Exit(code=1)


@app.command("drift")
def drift_cmd(
    registry: Registry = DEFAULT_REGISTRY,
    data_dir: DataDir = DEFAULT_DATA,
    shift: Annotated[str, typer.Option(help="Injected shift: " + ", ".join(SHIFTS))] = "none",
    out: Annotated[Path, typer.Option(help="Output directory.")] = DEFAULT_DATA / "drift",
    propose: Annotated[
        bool, typer.Option(help="On alert, write a retraining proposal via `d2d calibrate`.")
    ] = False,
    models_dir: Annotated[Path, typer.Option(help="Deployed joblib model dir.")] = Path("models"),
    golden: Golden = DEFAULT_GOLDEN,
    config: ConfigDir = None,
) -> None:
    """Input, prediction and calibration drift of the stable model on simulated traffic."""
    from d2d.calibration import calibrate
    from d2d.config import load_household
    from d2d.serving.drift import drift_report
    from d2d.serving.runtime import OnnxClassifier

    cfg_dir = config_dir(config)
    cfg = load_serving(cfg_dir / "serving.toml")
    clf = OnnxClassifier(_stable_package(registry, cfg_dir))
    ref, win, last_run = _traffic(cfg_dir, data_dir)
    report = drift_report(clf, ref, shift_rows(win, shift), cfg.drift)
    report["shift"] = shift
    out.mkdir(parents=True, exist_ok=True)
    (out / "drift.json").write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    top = report["top_feature_psi"][0]
    typer.echo(
        f"shift={shift}: reference {report['reference_n']} vs window {report['window_n']} stories; "
        f"max feature PSI {top['psi']:.3f} ({top['feature']}), prediction PSI "
        f"{report['prediction_psi']:.3f}, confidence KS p={report['confidence_ks']['pvalue']:.2g}, "
        f"ECE {report['ece']['reference']:.3f} -> {report['ece']['window']:.3f}, "
        f"accuracy {report['accuracy']['reference']:.3f} -> {report['accuracy']['window']:.3f}"
    )
    for a in report["alerts"]:
        typer.echo(f"  ALERT {a}")
    typer.echo(("drift: ALERT" if report["alert"] else "drift: ok") + f" -> {out / 'drift.json'}")
    if propose and report["alert"]:
        home_id = load_household(cfg_dir / "household.toml").id
        calibrate(last_run, cfg_dir, models_dir, golden, home_id, out / "proposal", None, report)
        typer.echo(f"retraining proposal -> {out / 'proposal' / 'report.md'}")
