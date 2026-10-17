from __future__ import annotations

from pathlib import Path

import pytest

from d2d.config import Household, PipelineConfig, load_household, load_pipeline

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@pytest.fixture(scope="session")
def household() -> Household:
    return load_household(CONFIG_DIR / "household.toml")


@pytest.fixture(scope="session")
def pipeline() -> PipelineConfig:
    return load_pipeline(CONFIG_DIR / "pipeline.toml")


@pytest.fixture(scope="session")
def run_data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One `d2d simulate` + `d2d run` with the default config, shared by the whole session."""
    from typer.testing import CliRunner

    from d2d.cli import app

    data = tmp_path_factory.mktemp("run_data")
    runner = CliRunner()
    for cmd in ("simulate", "run"):
        result = runner.invoke(app, [cmd, "--data-dir", str(data), "--config", str(CONFIG_DIR)])
        assert result.exit_code == 0, result.output
    return data
