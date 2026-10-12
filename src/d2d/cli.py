"""``d2d`` command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from d2d import __version__
from d2d.config import config_dir, load_household, load_pipeline, parse_utc
from d2d.evaluation import evaluate_changelog, evaluate_linking
from d2d.pipeline.events import run_pipeline
from d2d.pipeline.linking import load_linking
from d2d.pipeline.semantics import load_semantics
from d2d.simulator import simulate as run_simulation
from d2d.simulator import write_ground_truth, write_snapshots

app = typer.Typer(no_args_is_help=True, help="Data to Decision with AI (D2D)")
eval_app = typer.Typer(no_args_is_help=True, help="Deterministic evals against ground truth.")
app.add_typer(eval_app, name="eval")

DataDir = Annotated[Path, typer.Option(help="Data directory (gitignored).")]
ConfigDir = Annotated[Path | None, typer.Option(help="Config directory (default ./config).")]


@app.command()
def version() -> None:
    """Print the d2d version."""
    typer.echo(__version__)


@app.command()
def simulate(
    data_dir: DataDir = Path("data"),
    config: ConfigDir = None,
    days: Annotated[int | None, typer.Option(help="Days to simulate.")] = None,
    seed: Annotated[int | None, typer.Option(help="Random seed.")] = None,
    start: Annotated[str | None, typer.Option(help="Start time (ISO 8601, UTC).")] = None,
    scenario: Annotated[
        list[str] | None, typer.Option(help="Only inject these scenario ids (repeatable).")
    ] = None,
) -> None:
    """Stage 1: write device snapshots and scenario ground truth."""
    cfg_dir = config_dir(config)
    household = load_household(cfg_dir / "household.toml")
    pipeline = load_pipeline(cfg_dir / "pipeline.toml")
    result = run_simulation(
        household,
        start=parse_utc(start) if start else pipeline.start,
        days=days if days is not None else pipeline.days,
        cadence_minutes=pipeline.cadence_minutes,
        seed=seed if seed is not None else pipeline.seed,
        scenario_ids=set(scenario) if scenario else None,
    )
    partitions = write_snapshots(result.rows, data_dir)
    gt_path = write_ground_truth(result.ground_truth, data_dir)
    gt_events = sum(len(s["events"]) for s in result.ground_truth)
    typer.echo(
        f"{partitions} snapshot runs, {len(result.rows)} rows -> {data_dir / 'snapshots'}\n"
        f"{len(result.ground_truth)} scenarios, {gt_events} ground-truth events -> {gt_path}"
    )


@app.command()
def run(data_dir: DataDir = Path("data"), config: ConfigDir = None) -> None:
    """Stages 2-4: semantic changelog, change events and linked change stories."""
    cfg_dir = config_dir(config)
    s = run_pipeline(
        data_dir,
        load_semantics(cfg_dir / "semantics.toml"),
        load_linking(cfg_dir / "linking.toml"),
        load_household(cfg_dir / "household.toml").id,
    )
    kinds = ", ".join(f"{k}={v}" for k, v in s.by_kind.items())
    typer.echo(f"{s.snapshot_rows} snapshot rows -> {s.changes} changes -> {s.events} events")
    typer.echo(f"by kind: {kinds}")
    typer.echo(f"{s.stories} change stories")


@eval_app.command("linking")
def eval_linking(
    data_dir: DataDir = Path("data"),
    min_f1: Annotated[float, typer.Option(help="Gate: pairwise F1.")] = 0.9,
    min_precision: Annotated[float, typer.Option(help="Gate: pairwise precision.")] = 1.0,
) -> None:
    """Score linking vs scenario ground truth; exit 1 if a gate fails."""
    r = evaluate_linking(data_dir)
    for name, value in r.items():
        typer.echo(f"{name:<15}{value:.3f}")
    ok = r["pair_f1"] >= min_f1 and r["pair_precision"] >= min_precision
    typer.echo("gate: PASS" if ok else "gate: FAIL")
    if not ok:
        raise typer.Exit(code=1)


@eval_app.command("changelog")
def eval_changelog(
    data_dir: DataDir = Path("data"),
    min_recall: Annotated[float, typer.Option(help="Gate: event recall.")] = 1.0,
    min_precision: Annotated[float, typer.Option(help="Gate: event precision.")] = 1.0,
    min_dq_recall: Annotated[float, typer.Option(help="Gate: data-quality recall.")] = 0.95,
) -> None:
    """Score change events against scenario ground truth; exit 1 if a gate fails."""
    r = evaluate_changelog(data_dir)
    typer.echo(f"recall    {r.recall:.3f}  ({r.matched}/{r.expected})")
    typer.echo(f"precision {r.precision:.3f}  ({r.matched}/{r.found})")
    typer.echo(f"dq recall {r.dq_recall:.3f}  ({r.dq_matched}/{r.dq_expected})")
    for key in r.missed[:10]:
        typer.echo(f"  missed     {key}")
    for key in r.unexpected[:10]:
        typer.echo(f"  unexpected {key}")
    gates = [
        r.recall >= min_recall,
        r.precision >= min_precision,
        r.dq_recall >= min_dq_recall,
    ]
    typer.echo("gate: PASS" if all(gates) else "gate: FAIL")
    if not all(gates):
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
