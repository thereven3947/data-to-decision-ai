"""API + review + ML + agent against Postgres. Runs only when D2D_TEST_DATABASE_URL is set."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select

from d2d.agent.provider import ProviderError, Reply, ToolCall
from d2d.api.main import build_context, create_app
from d2d.config import Household, load_categories
from d2d.db import chat_message, feedback, load_run, review_item
from d2d.hitl.review import ReviewError, add_to_case, close_case, create_case, simulate_reviews
from d2d.hitl.sim_reviewer import label_stories
from d2d.hitl.training_set import build_training_set, training_set_version
from d2d.ml.infer import classify_new
from d2d.ml.train import Classifier, save, train

ROOT = Path(__file__).resolve().parents[2]
TEST_URL = os.environ.get("D2D_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="D2D_TEST_DATABASE_URL not set")


class Scripted:
    """Fake LLM: replays a list of replies (or raises) so the agent loop is deterministic."""

    model_tag = "scripted"

    def __init__(self, replies: list[Reply | Exception]) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Reply:
        self.calls.append(messages)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(scope="module")
def engine(
    run_data: Path, household: Household, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Engine]:
    assert TEST_URL
    mp = pytest.MonkeyPatch()
    mp.setenv("D2D_DATABASE_URL", TEST_URL)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    eng = create_engine(TEST_URL)
    load_run(eng, run_data, household)
    rows = build_training_set(run_data, household)
    model, meta = train(rows, training_set_version(rows))
    models = tmp_path_factory.mktemp("models")
    save(model, meta, models)
    taxonomy = load_categories(ROOT / "config" / "categories.toml")
    summary = classify_new(eng, Classifier(models), taxonomy)
    assert summary.classified == len(rows) and summary.audit > 0
    yield eng
    eng.dispose()
    mp.undo()


def _client(engine: Engine, run_data: Path, provider: Any = None) -> TestClient:
    ctx = build_context(engine, ROOT / "config", run_data, ROOT / "agent" / "skills", provider)
    return TestClient(create_app(ctx, web_dist=Path("/nonexistent")))


def _get(client: TestClient, path: str, role: str = "owner") -> Any:
    r = client.get(f"/api{path}", headers={"X-D2D-Role": role})
    assert r.status_code == 200, r.text
    return r.json()


def test_classify_queues_audits_and_review_resolves_them(engine: Engine, run_data: Path) -> None:
    client = _client(engine, run_data)
    queue = _get(client, "/review")
    assert queue and all(i["review"]["reason"] in {"audit", "abstain"} for i in queue)
    sid = queue[0]["story_id"]
    assert _get(client, f"/stories/{sid}")["status"] == "needs_review"

    denied = client.post(
        f"/api/stories/{sid}/label",
        json={"category": "load_shift"},
        headers={"X-D2D-Role": "family"},
    )
    assert denied.status_code == 403
    bad = client.post(f"/api/stories/{sid}/label", json={"category": "nope"})
    assert bad.status_code == 400

    category = queue[0]["review"]["suggested_category"]
    ok = client.post(f"/api/stories/{sid}/label", json={"category": category, "note": "checked"})
    assert ok.status_code == 200, ok.text
    story = ok.json()["story"]
    assert story["status"] == "published" and story["label_source"] == "human"
    assert [lb["source"] for lb in story["labels"]] == ["model", "human"]
    assert sid not in {i["story_id"] for i in _get(client, "/review")}

    taxonomy = load_categories(ROOT / "config" / "categories.toml")
    simulate_reviews(engine, label_stories(run_data), taxonomy)
    with engine.connect() as conn:
        open_items = conn.execute(
            select(func.count()).select_from(review_item).where(review_item.c.resolved_at.is_(None))
        ).scalar_one()
    assert open_items == 0
    kpis = _get(client, "/kpis")
    assert kpis["open_reviews"] == 0 and kpis["audit_agreement"] is not None


def test_human_labels_feed_the_training_set(
    engine: Engine, run_data: Path, household: Household
) -> None:
    rows = build_training_set(run_data, household, engine, include_ground_truth=False)
    assert rows and {r["source"] for r in rows} <= {"human", "sim_reviewer"}
    assert any(r["source"] == "human" and r["reviewer"] == "owner" for r in rows)


def test_roles_see_scoped_data(engine: Engine, run_data: Path) -> None:
    client = _client(engine, run_data)
    counts = {
        role: (
            len(_get(client, "/stories", role)),
            len(_get(client, "/events", role)),
            len(_get(client, "/devices", role)),
        )
        for role in ("owner", "family", "energy_advisor", "guest")
    }
    assert counts["owner"][0] > counts["family"][0] > counts["guest"][0] > 0
    assert counts["owner"][2] > counts["family"][2] > counts["guest"][2]
    assert all("room" not in d for d in _get(client, "/devices", "energy_advisor"))
    for s in _get(client, "/stories", "energy_advisor"):
        assert s["category"] in {
            "load_shift",
            "circuit_reassignment",
            "weather_dr_response",
            "fault_degradation",
            "lighting_change",
        }
    private = [s for s in _get(client, "/stories") if "smart_plug_lamp" in s["device_ids"]]
    assert private
    hidden = client.get(f"/api/stories/{private[0]['story_id']}", headers={"X-D2D-Role": "family"})
    assert hidden.status_code == 404
    assert client.get("/api/stories", headers={"X-D2D-Role": "admin"}).status_code == 400
    assert len(_get(client, "/changelog")) == len(_get(client, "/events"))


def test_story_detail_has_enrichment_and_playbook(engine: Engine, run_data: Path) -> None:
    client = _client(engine, run_data)
    shift = next(s for s in _get(client, "/stories") if s["category"] == "load_shift")
    detail = _get(client, f"/stories/{shift['story_id']}")
    assert detail["enrichment"]["facts"]["kwh_shifted"] == 4.7
    assert detail["playbook"]["actions"] and len(detail["events"]) == detail["event_count"]
    washer = _get(client, "/devices/washer")
    assert washer["device"]["latest"]["state"] and washer["events"]


def test_cases(engine: Engine, run_data: Path) -> None:
    client = _client(engine, run_data)
    sids = [s["story_id"] for s in _get(client, "/stories")[:2]]
    r = client.post("/api/cases", json={"title": "Check laundry", "story_ids": sids[:1]})
    case_id = r.json()["case_id"]
    add_to_case(engine, case_id, sids[1])
    assert _get(client, "/cases")[0]["story_ids"] == sorted(sids)
    close_case(engine, case_id, "done")
    with pytest.raises(ReviewError):
        add_to_case(engine, case_id, sids[0])
    assert (
        client.post("/api/cases", json={"title": "x"}, headers={"X-D2D-Role": "guest"}).status_code
        == 403
    )
    assert create_case(engine, "empty", "owner", []) > case_id


def test_chat_with_tool_loop_grounding_retry_and_record(engine: Engine, run_data: Path) -> None:
    client = _client(engine, run_data)
    shift = next(s for s in _get(client, "/stories") if s["category"] == "load_shift")
    sid = shift["story_id"]
    provider = Scripted(
        [
            Reply("", [ToolCall("get_story", {"story_id": sid})], 100, 10),
            Reply(f"It saves 99 kWh [story:{sid}]."),  # fails the number check
            Reply(f"Schedules moved off-peak, saving about $1.13 per day [story:{sid}]."),
        ]
    )
    client = _client(engine, run_data, provider)
    r = client.post("/api/chat", json={"question": "Why did the dishwasher run at 2 a.m.?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["skill"] == "why_question" and body["llm_model_tag"] == "scripted"
    assert body["checker"] == {"ok": True, "errors": [], "attempts": 2}
    assert body["cited_ids"] == [sid] and body["citations"][sid] == shift["title"]
    assert [t["name"] for t in body["tool_calls"]] == ["list_stories", "get_story"]
    assert "grounding check" in provider.calls[-1][-1]["content"]

    fb = client.post(
        "/api/feedback",
        json={
            "target_type": "message",
            "target_id": str(body["message_id"]),
            "rating": "down",
            "reason": "unhelpful_answer",
        },
    )
    assert fb.status_code == 200
    assert (
        client.post(
            "/api/feedback",
            json={"target_type": "message", "target_id": "1", "rating": "down", "reason": "bad"},
        ).status_code
        == 400
    )
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(chat_message)).scalar_one() >= 1
        assert conn.execute(select(feedback.c.reason)).scalars().first() == "unhelpful_answer"

    history = _get(client, f"/chat/{body['session_id']}")
    assert history[0]["question"].startswith("Why")


def test_chat_falls_back_when_llm_is_down_or_ungrounded(engine: Engine, run_data: Path) -> None:
    down = _client(engine, run_data, Scripted([ProviderError("connection refused")]))
    body = down.post("/api/chat", json={"question": "Brief me on this week"}).json()
    assert body["skill"] == "household_briefing"
    assert body["llm_model_tag"] == "scripted+fallback"
    assert "[story:" in body["answer"] and body["checker"]["ok"] is False

    liar = _client(
        engine,
        run_data,
        Scripted([Reply("See [story:ffffffffffffffff]."), Reply("See [story:ffffffffffffffff].")]),
    )
    body = liar.post("/api/chat", json={"question": "what is new?"}).json()
    assert "ffffffffffffffff" not in body["answer"]
    assert any("ffffffffffffffff" in e for e in body["checker"]["errors"])

    guest = _client(engine, run_data, None)
    body = guest.post(
        "/api/chat", json={"question": "Brief me"}, headers={"X-D2D-Role": "guest"}
    ).json()
    visible = {s["story_id"] for s in _get(guest, "/stories", "guest")}
    assert set(body["cited_ids"]) <= visible and body["llm_model_tag"] == "fallback"


def test_health_and_meta(engine: Engine, run_data: Path) -> None:
    client = _client(engine, run_data)
    health = _get(client, "/health")
    assert health["schema"] == "0003" and health["llm"] is None
    meta = _get(client, "/meta", "energy_advisor")
    assert meta["can_label"] is False and all(c["energy"] for c in meta["categories"])
