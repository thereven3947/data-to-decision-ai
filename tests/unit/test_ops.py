from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from d2d.cli import app
from d2d.ops import pipeline_label, pipeline_plist

ROOT = Path(__file__).resolve().parents[2]


def test_pipeline_plist_runs_pipeline_script_every_cadence() -> None:
    env_dir = Path("/Users/me/d2d/production")
    agent = plistlib.loads(pipeline_plist("production", env_dir, 15))

    assert agent["Label"] == pipeline_label("production") == "com.d2d.pipeline.production"
    assert agent["StartInterval"] == 900
    assert agent["ProgramArguments"] == [
        "/bin/bash",
        "/Users/me/d2d/production/current/deploy/pipeline.sh",
        "/Users/me/d2d/production",
    ]
    assert agent["StandardOutPath"] == "/Users/me/d2d/production/logs/pipeline.log"


@pytest.mark.parametrize(("env", "cadence"), [("dev", 15), ("staging", 0)])
def test_pipeline_plist_rejects_bad_input(env: str, cadence: int) -> None:
    with pytest.raises(ValueError):
        pipeline_plist(env, Path("/tmp/x"), cadence)


def test_launchd_plist_cli_uses_pipeline_cadence(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "launchd-plist",
            "--env",
            "staging",
            "--env-dir",
            str(tmp_path),
            "--config",
            str(ROOT / "config"),
        ],
    )
    assert result.exit_code == 0, result.output
    agent = plistlib.loads(result.output.encode("utf-8"))
    assert agent["Label"] == "com.d2d.pipeline.staging"
    assert agent["StartInterval"] == 15 * 60


def test_launchd_plist_cli_rejects_unknown_env(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["launchd-plist", "--env", "dev", "--env-dir", str(tmp_path)])
    assert result.exit_code != 0


@pytest.mark.parametrize(
    "script",
    [
        "deploy/deploy.sh",
        "deploy/pipeline.sh",
        "deploy/runner/install.sh",
        "analytics/bigquery/load.sh",
    ],
)
def test_deploy_scripts_are_valid_bash(script: str) -> None:
    subprocess.run(["bash", "-n", str(ROOT / script)], check=True)


def test_api_plist_keeps_the_api_running() -> None:
    from d2d.ops import api_label, api_plist

    env_dir = Path("/Users/me/d2d/staging")
    agent = plistlib.loads(api_plist("staging", env_dir))
    assert agent["Label"] == api_label("staging") == "com.d2d.api.staging"
    assert agent["KeepAlive"] is True and agent["RunAtLoad"] is True
    assert agent["ProgramArguments"][1] == "/Users/me/d2d/staging/current/deploy/api.sh"
    assert agent["StandardOutPath"] == "/Users/me/d2d/staging/logs/api.log"
    with pytest.raises(ValueError):
        api_plist("dev", env_dir)

    result = CliRunner().invoke(
        app,
        [
            "launchd-plist",
            "--env",
            "staging",
            "--env-dir",
            str(env_dir),
            "--service",
            "api",
            "--config",
            str(ROOT / "config"),
        ],
    )
    assert result.exit_code == 0 and "com.d2d.api.staging" in result.output


def test_backup_plist_runs_daily() -> None:
    from d2d.ops import backup_label, backup_plist

    agent = plistlib.loads(backup_plist("production", Path("/Users/me/d2d/production")))
    assert agent["Label"] == backup_label("production") == "com.d2d.backup.production"
    assert agent["StartCalendarInterval"] == {"Hour": 3, "Minute": 30}
    assert agent["ProgramArguments"][1].endswith("/current/deploy/backup.sh")
