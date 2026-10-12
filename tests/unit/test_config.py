from __future__ import annotations

from pathlib import Path

import pytest

from d2d.config import ConfigError, Household, load_household, load_pipeline


def test_household_topology_is_consistent(household: Household) -> None:
    assert household.id == "home_demo"
    assert "dishwasher" in household.devices
    assert not household.devices["dishwasher_2"].active
    assert household.devices["smart_plug_lamp"].tags == ("private",)
    assert household.upstream_ids("circuit_kitchen_2", "router_mesh_1") == [
        "panel_main",
        "router_mesh_1",
    ]


def test_unknown_circuit_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "household.toml"
    path.write_text(
        '[home]\nid="h"\ntimezone="UTC"\n'
        '[[upstream]]\nid="r"\nkind="router"\n'
        '[[device]]\nid="d"\nkind="plug"\nroom="x"\ncircuit="nope"\nnetwork="r"\nrated_w=1\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown circuit"):
        load_household(path)


def test_cadence_must_divide_an_hour(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.toml"
    path.write_text("[pipeline]\ncadence_minutes = 7\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="divisor"):
        load_pipeline(path)
