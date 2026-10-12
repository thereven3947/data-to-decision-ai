from __future__ import annotations

from collections import Counter

from d2d.config import Household, PipelineConfig
from d2d.simulator import generate_cdc


def test_cdc_routine_is_deterministic_sorted_and_plausible(
    household: Household, pipeline: PipelineConfig
) -> None:
    events = generate_cdc(household, start=pipeline.start, days=3, seed=pipeline.seed)
    assert events == generate_cdc(household, start=pipeline.start, days=3, seed=pipeline.seed)
    stamps = [e["event_ts"] for e in events]
    assert stamps == sorted(stamps)
    assert all(pipeline.start <= ts for ts in stamps)
    assert {e["device_id"] for e in events} == {
        "garage_door",
        "light_living",
        "light_porch",
        "ring_camera_front",
        "ring_doorbell",
    }

    garage = [e["event"] for e in events if e["device_id"] == "garage_door"]
    assert garage[0] == "open" and garage == ["open", "close"] * (len(garage) // 2)

    porch = Counter(e["source"] for e in events if e["device_id"] == "light_porch")
    assert porch == {"automation": porch["automation"]} and porch["automation"] >= 4

    rings = [e for e in events if e["event"] == "ring"]
    answers = [e for e in events if e["event"] == "answered"]
    assert len(rings) == len(answers)
