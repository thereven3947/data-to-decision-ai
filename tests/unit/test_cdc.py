from __future__ import annotations

from collections import Counter
from pathlib import Path

from d2d.config import Household, PipelineConfig
from d2d.pipeline.cdc import cdc_changes
from d2d.pipeline.semantics import Rule, Semantics, load_semantics
from d2d.simulator import generate_cdc

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def test_cdc_rows_have_change_event_shape(household: Household, pipeline: PipelineConfig) -> None:
    rows = generate_cdc(household, start=pipeline.start, days=3, seed=pipeline.seed)
    assert rows == generate_cdc(household, start=pipeline.start, days=3, seed=pipeline.seed)
    assert [r["event_ts"] for r in rows] == sorted(r["event_ts"] for r in rows)
    assert {r["device_id"] for r in rows} == {
        "garage_door",
        "light_living",
        "light_porch",
        "ring_camera_front",
        "ring_doorbell",
    }
    assert all(r["old_value"] != r["new_value"] for r in rows)

    garage = [(r["old_value"], r["new_value"]) for r in rows if r["device_id"] == "garage_door"]
    assert garage == [("closed", "open"), ("open", "closed")] * (len(garage) // 2)

    porch = Counter(r["source"] for r in rows if r["device_id"] == "light_porch")
    assert set(porch) == {"automation"} and porch["automation"] >= 4

    presses = [r for r in rows if r["property"] == "button"]
    assert all(r["new_value"] in {"pressed", "idle"} for r in presses)


def test_routine_cdc_is_dropped_and_rules_promote_changes(
    household: Household, pipeline: PipelineConfig
) -> None:
    rows = generate_cdc(household, start=pipeline.start, days=3, seed=pipeline.seed)
    assert cdc_changes(rows, load_semantics(CONFIG_DIR / "semantics.toml")) == []

    door_rule = Rule(
        property="door", new_in=("open",), change_kind="operational", tag="door_opened"
    )
    changes = cdc_changes(rows, Semantics(rules=(door_rule,), windows={}))
    opened = [r for r in rows if r["property"] == "door" and r["new_value"] == "open"]
    assert len(changes) == len(opened) > 0
    assert {c.semantic_tag for c in changes} == {"door_opened"}
    assert {c.snapshot_run_id for c in changes} == {"cdc"}
