"""``d2d`` command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from d2d import __version__
from d2d.config import config_dir, load_household, load_pipeline, parse_utc
from d2d.simulator import simulate as run_simulation
from d2d.simulator import write_ground_truth, write_snapshots

app = typer.Typer(no_args_is_help=True, help="Data to Decision with AI (D2D)")

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


if __name__ == "__main__":
    app()
