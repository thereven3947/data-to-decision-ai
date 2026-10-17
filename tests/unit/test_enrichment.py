from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
import pytest

from d2d.config import Household
from d2d.enrichment import enrich, in_window, load_enrichment, window_hours
from d2d.hitl.sim_reviewer import label_stories

CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture(scope="module")
def cfg():  # type: ignore[no-untyped-def]
    return load_enrichment(CONFIG / "enrichment.toml", CONFIG / "semantics.toml")


def _stories(run_data: Path) -> list[tuple[str | None, list[dict]]]:  # type: ignore[type-arg]
    labels = label_stories(run_data)
    events = {
        e["event_id"]: e for e in pq.read_table(run_data / "events/events.parquet").to_pylist()
    }
    return [
        (labels.get(s["story_id"]), [events[i] for i in s["event_ids"]])
        for s in pq.read_table(run_data / "stories/stories.parquet").to_pylist()
    ]


def test_windows() -> None:
    assert in_window("17:00", ("16:00", "21:00"))
    assert not in_window("21:00", ("16:00", "21:00"))
    assert in_window("02:00", ("22:00", "06:00")) and in_window("23:00", ("22:00", "06:00"))
    assert window_hours("18:00", "19:30") == 1.5
    assert window_hours("23:00", "03:00") == 4.0


def test_load_shift_numbers_are_exact(run_data: Path, household: Household, cfg) -> None:  # type: ignore[no-untyped-def]
    ((category, events),) = [(c, e) for c, e in _stories(run_data) if c == "load_shift"]
    r = enrich(category, events, household, cfg)
    # dishwasher 1800 W x 1.5 h + washer 2000 W x 1 h = 4.7 kWh; x (0.52 - 0.28) $/kWh = $1.128
    assert r["facts"]["kwh_shifted"] == 4.7
    assert r["facts"]["usd_per_day"] == 1.13
    assert "4.7 kWh" in r["summary"] and "$1.13" in r["summary"]


def test_circuit_load_percentage(run_data: Path, household: Household, cfg) -> None:  # type: ignore[no-untyped-def]
    ((category, events),) = [(c, e) for c, e in _stories(run_data) if c == "circuit_reassignment"]
    move = enrich(category, events, household, cfg)["facts"]["moves"][0]
    capacity = household.circuits[move["to_circuit"]].rating_amps * 120
    assert move["capacity_w"] == capacity
    assert move["load_pct"] == round(100 * household.devices["ev_charger"].rated_w / capacity, 2)


def test_every_story_renders_its_category_template(
    run_data: Path, household: Household, cfg
) -> None:  # type: ignore[no-untyped-def]
    for category, events in _stories(run_data):
        r = enrich(category, events, household, cfg)
        assert r["title"] and r["summary"]
        assert "{{" not in r["summary"]
        if category in cfg.templates:
            assert r["title"] != enrich(None, events, household, cfg)["title"], category


def test_broken_template_falls_back(household: Household, cfg) -> None:  # type: ignore[no-untyped-def]
    from dataclasses import replace

    broken = replace(cfg, templates={"load_shift": {"title": "{{ facts.nope.x }}", "summary": "x"}})
    from datetime import UTC, datetime

    ev = [
        {
            "device_id": "washer",
            "property": "mode",
            "old_value": "a",
            "new_value": "b",
            "semantic_tag": "mode_change",
            "change_kind": "config",
            "event_ts": datetime(2026, 10, 1, tzinfo=UTC),
        }
    ]
    assert enrich("load_shift", ev, household, broken)["title"] == "1 change on Washer"
