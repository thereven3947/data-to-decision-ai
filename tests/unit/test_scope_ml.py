from __future__ import annotations

from pathlib import Path

from d2d.api.scope import Scope, load_roles
from d2d.config import Household, load_categories
from d2d.hitl.training_set import read_training_set, training_set_version
from d2d.ml.evaluate import evaluate_classifier
from d2d.ml.infer import in_audit_sample
from d2d.ml.train import Classifier, TrainParams, augment, model_version, save, train

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "evals" / "golden" / "stories.jsonl"


def _scope(role: str, household: Household) -> Scope:
    roles = load_roles(ROOT / "config" / "roles.toml")
    return Scope(roles[role], household, load_categories(ROOT / "config" / "categories.toml"))


def test_role_scopes(household: Household) -> None:
    owner, family = _scope("owner", household), _scope("family", household)
    advisor, guest = _scope("energy_advisor", household), _scope("guest", household)
    assert owner.devices == set(household.devices)
    assert "smart_plug_lamp" not in family.devices  # tagged private
    assert {household.devices[d].room for d in guest.devices} <= {
        "living",
        "kitchen",
        "front_door",
        "front_yard",
    }
    story = {"status": "published", "category": "load_shift", "device_ids": ["washer"]}
    assert advisor.story_visible(story)
    assert not advisor.story_visible({**story, "category": "firmware_rollout"})
    assert not guest.story_visible(story)  # laundry is not a shared room
    assert not guest.story_visible({**story, "status": "needs_review", "device_ids": ["fridge"]})
    assert "room" not in advisor.redact_device({"device_id": "washer", "room": "laundry"})
    assert owner.policy.can_label and not family.policy.can_label


def test_augment_and_version_are_deterministic() -> None:
    rows = read_training_set(GOLDEN)
    assert augment(rows, 3, 7) == augment(rows, 3, 7)
    v = training_set_version(rows)
    assert model_version(v, TrainParams()) == model_version(v, TrainParams())
    assert model_version(v, TrainParams()) != model_version(v, TrainParams(seed=8))


def test_classifier_meets_golden_gate(tmp_path: Path) -> None:
    rows = read_training_set(GOLDEN)
    model, meta = train(rows, training_set_version(rows))
    save(model, meta, tmp_path)
    clf = Classifier(tmp_path)
    r = evaluate_classifier(clf, rows)
    assert r.min_precision >= 0.9 and r.coverage >= 0.7
    # A single event from a multi-event story is less certain than the full story.
    full = max(rows, key=lambda x: len(x["events"]))
    partial = {**full, "events": full["events"][:1]}
    p_full, p_part = clf.predict([full, partial])
    assert p_full.confidence >= p_part.confidence


def test_audit_sample_is_deterministic() -> None:
    ids = [f"{i:016x}" for i in range(2000)]
    picked = [i for i in ids if in_audit_sample(i, 0.2)]
    assert picked == [i for i in ids if in_audit_sample(i, 0.2)]
    assert 0.15 < len(picked) / len(ids) < 0.25
    assert not any(in_audit_sample(i, 0.0) for i in ids)
