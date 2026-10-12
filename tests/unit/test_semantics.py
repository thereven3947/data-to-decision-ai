from __future__ import annotations

from pathlib import Path

import pytest

from d2d.config import ConfigError
from d2d.pipeline.semantics import Semantics, load_semantics

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture(scope="module")
def semantics() -> Semantics:
    return load_semantics(CONFIG_DIR / "semantics.toml")


@pytest.mark.parametrize(
    ("prop", "old", "new", "expected"),
    [
        ("schedule.start", "18:00", "02:00", ("config", "schedule_shift_off_peak")),
        ("schedule.start", "10:00", "11:00", ("config", "schedule_change")),
        ("schedule.end", "19:30", "03:30", ("config", "schedule_change")),
        ("settings.child_lock", "true", "false", ("config", "safety_child_lock_change")),
        ("settings.max_temp_c", "60", "70", ("config", "safety_setting_change")),
        ("presence", "absent", "present", ("topology", "device_added")),
        ("presence", "present", "absent", ("topology", "device_removed")),
        ("state", "idle", "error", ("operational", "device_error")),
        ("state", "error", "idle", ("operational", "device_recovered")),
        ("error_code", None, "E21", ("operational", "error_raised")),
        ("error_code", "E21", None, ("operational", "error_cleared")),
        ("rssi_dbm", "-66", "-46", ("operational", "signal_shift")),
    ],
)
def test_rules_classify_meaningful_changes(
    semantics: Semantics,
    prop: str,
    old: str | None,
    new: str | None,
    expected: tuple[str, str],
) -> None:
    assert semantics.classify(prop, old, new) == expected


@pytest.mark.parametrize(
    ("prop", "old", "new"),
    [
        ("state", "idle", "running"),
        ("state", "running", "idle"),
        ("rssi_dbm", "-60", "-54"),
        ("rssi_dbm", "-60", None),
        ("power_w", "1.5", "1800"),
    ],
)
def test_expected_operation_is_not_emitted(
    semantics: Semantics, prop: str, old: str | None, new: str | None
) -> None:
    assert semantics.classify(prop, old, new) is None


def test_unknown_window_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "semantics.toml"
    path.write_text(
        '[[rule]]\nproperty="x"\nchange_kind="config"\ntag="t"\nnew_in_window="nope"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown window"):
        load_semantics(path)
