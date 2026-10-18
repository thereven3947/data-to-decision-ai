from __future__ import annotations

from pathlib import Path

import pytest

from d2d.agent.checker import check_answer, cited_story_ids
from d2d.agent.runtime import normalize_citations
from d2d.agent.skills import load_skills

SKILLS = Path(__file__).resolve().parents[2] / "agent" / "skills"
A, B = "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"


@pytest.mark.parametrize(
    ("question", "skill"),
    [
        ("Brief me on what happened this week", "household_briefing"),
        (f"What happened in {A}?", "explain_story"),
        ("Why did the dishwasher run at 2 a.m.?", "why_question"),
        ("What should I do about the washer?", "action_items"),
        ("Show me the history of the dryer", "device_history"),
        ("What's been going on with the garage door?", "device_history"),
        ("What's new?", "household_briefing"),
        ("hello there", "general"),
        ("q2 are you robot", "about"),
        ("seems like you are not agent based answering machine", "about"),
        ("Who are you?", "about"),
        ("Which devices had data problems?", "general"),
    ],
)
def test_router(question: str, skill: str) -> None:
    assert load_skills(SKILLS).route(question).name == skill


def test_skill_sha_is_stable() -> None:
    a, b = load_skills(SKILLS), load_skills(SKILLS)
    assert {k: v.sha for k, v in a.skills.items()} == {k: v.sha for k, v in b.skills.items()}
    assert set(a.tones) == {"owner", "family", "energy_advisor", "guest"}


def test_normalize_citations() -> None:
    text = f"x [story:{A}, story:{B}] y story:{A} z [get_story: {A}]."
    assert normalize_citations(text) == f"x [story:{A}] [story:{B}] y [story:{A}] z."
    assert cited_story_ids(f"[story:{A}] and {B}") == [A, B]


def _check(answer: str, outputs: list[dict] | None = None):  # type: ignore[no-untyped-def,type-arg]
    return check_answer(
        answer,
        outputs or [{"stories": [{"story_id": A, "kwh": 4.7, "usd": 1.13}]}],
        visible_story_ids={A},
        story_categories={A: "load_shift"},
        hidden_device_ids={"smart_plug_lamp"},
        category_ids={"load_shift", "fault_degradation"},
    )


def test_checker_accepts_grounded_answer() -> None:
    r = _check(f"Schedules moved off-peak (load_shift) [story:{A}]: 4.7 kWh, about $1.13 per day.")
    assert r.ok, r.errors
    assert r.cited_ids == [A]


def test_checker_rejects_unknown_story_hidden_device_relabel_and_numbers() -> None:
    assert not _check(f"See [story:{B}].").ok
    assert not _check("The `smart_plug_lamp` was updated.").ok
    assert not _check(f"This was fault_degradation [story:{A}].").ok
    r = _check(f"It saves 9.9 kWh [story:{A}].")
    assert not r.ok and "9.9 kWh" in r.errors[0]
