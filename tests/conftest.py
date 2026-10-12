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
