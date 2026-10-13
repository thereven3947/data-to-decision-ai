"""Mac mini operations: launchd agent for the scheduled pipeline (see docs/deployment.md)."""

from __future__ import annotations

import plistlib
from pathlib import Path

ENVIRONMENTS = ("staging", "production")


def pipeline_label(env: str) -> str:
    return f"com.d2d.pipeline.{env}"


def pipeline_plist(env: str, env_dir: Path, cadence_minutes: int) -> bytes:
    """launchd agent that runs ``deploy/pipeline.sh`` from ``<env_dir>/current`` every cadence."""
    if env not in ENVIRONMENTS:
        raise ValueError(f"unknown environment {env!r}, expected one of {ENVIRONMENTS}")
    if cadence_minutes <= 0:
        raise ValueError("cadence_minutes must be positive")
    logs = env_dir / "logs"
    agent = {
        "Label": pipeline_label(env),
        "ProgramArguments": [
            "/bin/bash",
            str(env_dir / "current" / "deploy" / "pipeline.sh"),
            str(env_dir),
        ],
        "StartInterval": cadence_minutes * 60,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "pipeline.log"),
        "StandardErrorPath": str(logs / "pipeline.log"),
    }
    return plistlib.dumps(agent)
