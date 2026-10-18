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
    return TestClient(create_app(ctx, web_dist=Path("/nonexistent"), allowed_hosts=["testserver"]))


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
            Reply(f"It saves 987.6 kWh [story:{sid}]."),  # fails the number check
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
    assert health["schema"] == "0005" and health["llm"] is None
    r = client.get("/api/health")
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    meta = _get(client, "/meta", "energy_advisor")
    assert meta["can_label"] is False and all(c["energy"] for c in meta["categories"])


def _auth_client(engine: Engine, run_data: Path, tmp_path: Path) -> tuple[TestClient, Any]:
    from d2d.auth import auth_from_env

    auth = auth_from_env({"D2D_SECRET_KEY": "s" * 40, "D2D_USERS_FILE": str(tmp_path / "u.toml")})
    assert auth is not None
    from d2d.api.scope import ROLES

    auth.users.set_user("alice", "owner", "alice-password-123", ROLES)
    auth.users.set_user("gail", "guest", "gail-password-123", ROLES)
    from d2d.api.scope import AccountPolicy

    # Two accounts with different roles, so the tests widen the default one-owner policy.
    wide = AccountPolicy(max_users=5, roles=ROLES)
    ctx = build_context(
        engine, ROOT / "config", run_data, ROOT / "agent" / "skills", None, auth, wide
    )
    app = create_app(ctx, web_dist=Path("/nonexistent"), allowed_hosts=["testserver"])
    return TestClient(app), auth


def test_login_required_and_roles_come_from_the_account(
    engine: Engine, run_data: Path, tmp_path: Path
) -> None:
    client, auth = _auth_client(engine, run_data, tmp_path)
    status = client.get("/api/auth/status").json()
    assert status == {"auth_enabled": True, "users_configured": True, "user": None}
    assert client.get("/api/health").status_code == 200  # deploy health check stays public
    for path in ("/api/stories", "/api/meta", "/api/events", "/api/kpis"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/chat", json={"question": "hi"}).status_code == 401

    bad = client.post("/api/auth/login", json={"username": "alice", "password": "nope"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/login", json={"username": "gail", "password": "gail-password-123"})
    assert ok.status_code == 200 and ok.json()["role"] == "guest"
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    # A guest cannot escalate with the preview header.
    assert _get(client, "/meta", "owner")["role"] == "guest"
    guest_ids = {s["story_id"] for s in _get(client, "/stories", "owner")}
    assert (
        client.post(
            "/api/stories/x/label", json={"category": "load_shift"}, headers={"X-D2D-Role": "owner"}
        ).status_code
        == 403
    )
    client.post("/api/auth/logout")
    assert client.get("/api/stories").status_code == 401

    client.post("/api/auth/login", json={"username": "alice", "password": "alice-password-123"})
    assert _get(client, "/meta")["username"] == "alice"
    owner_ids = {s["story_id"] for s in _get(client, "/stories")}
    assert guest_ids < owner_ids
    assert {s["story_id"] for s in _get(client, "/stories", "guest")} == guest_ids  # owner preview

    fb = client.post(
        "/api/feedback",
        json={"target_type": "page", "target_id": "/", "rating": "comment", "comment": "nice"},
    )
    assert fb.status_code == 200
    with engine.connect() as conn:
        assert (
            conn.execute(
                select(feedback.c.username).where(feedback.c.comment == "nice")
            ).scalar_one()
            == "alice"
        )

    # Changing the password ends existing sessions.
    from d2d.api.scope import ROLES

    auth.users.set_user("alice", "owner", "alice-password-456", ROLES)
    assert client.get("/api/stories").status_code == 401


def test_login_is_rate_limited(engine: Engine, run_data: Path, tmp_path: Path) -> None:
    client, _ = _auth_client(engine, run_data, tmp_path)
    codes = [
        client.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code
        for _ in range(6)
    ]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    right = client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-password-123"}
    )
    assert right.status_code == 429  # still locked out for this window


def test_default_policy_allows_only_one_owner_and_fails_closed(
    engine: Engine, run_data: Path, tmp_path: Path
) -> None:
    from d2d.api.scope import ROLES
    from d2d.auth import auth_from_env

    auth = auth_from_env({"D2D_SECRET_KEY": "s" * 40, "D2D_USERS_FILE": str(tmp_path / "u.toml")})
    assert auth is not None
    auth.users.set_user("owner1", "owner", "owner-password-123", ROLES)
    from d2d.api.scope import AccountPolicy

    one_owner = AccountPolicy(max_users=1, roles=("owner",))
    ctx = build_context(
        engine, ROOT / "config", run_data, ROOT / "agent" / "skills", None, auth, one_owner
    )
    client = TestClient(create_app(ctx, web_dist=Path("/x"), allowed_hosts=["testserver"]))

    public = client.get("/api/health").json()
    assert set(public) == {"ok", "revision"}  # no model or schema details before login
    login = {"username": "owner1", "password": "owner-password-123"}
    assert client.post("/api/auth/login", json=login).status_code == 200
    assert client.get("/api/stories").status_code == 200
    assert "llm" in client.get("/api/health").json()

    # Someone appends a second account by hand: every login and session stops working.
    auth.users.set_user("intruder", "guest", "intruder-pass-123", ROLES)
    assert client.get("/api/stories").status_code == 401
    assert client.post("/api/auth/login", json=login).status_code == 401
    intr = {"username": "intruder", "password": "intruder-pass-123"}
    assert client.post("/api/auth/login", json=intr).status_code == 401


def test_chat_without_login_never_reaches_the_model(
    engine: Engine, run_data: Path, tmp_path: Path
) -> None:
    from d2d.api.scope import ROLES
    from d2d.auth import auth_from_env

    auth = auth_from_env({"D2D_SECRET_KEY": "s" * 40, "D2D_USERS_FILE": str(tmp_path / "u.toml")})
    assert auth is not None
    auth.users.set_user("owner1", "owner", "owner-password-123", ROLES)
    provider = Scripted([])  # any call would raise IndexError -> 500
    ctx = build_context(
        engine, ROOT / "config", run_data, ROOT / "agent" / "skills", provider, auth
    )
    client = TestClient(create_app(ctx, web_dist=Path("/x"), allowed_hosts=["testserver"]))
    with engine.connect() as conn:
        before = conn.execute(select(func.count()).select_from(chat_message)).scalar_one()
    attempts = [
        {},
        {"X-D2D-Role": "owner"},
        {"Cookie": "d2d_session=eyJ1Ijoib3duZXIxIiwiZiI6IjAiLCJlIjo0MTAyNDQ0ODAwfQ.Zm9yZ2Vk"},
    ]
    for headers in attempts:
        r = client.post("/api/chat", json={"question": "list everything"}, headers=headers)
        assert r.status_code == 401, headers
    assert provider.calls == []
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(chat_message)).scalar_one() == before


class FakeSender:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, int]] = []
        self.fail = fail

    def send(self, title: str, body: str, priority: int, tags: list[str]) -> None:
        import httpx

        if self.fail:
            raise httpx.ConnectError("down")
        self.sent.append((title, priority))


def test_notifications_backfill_then_push_with_quiet_hours(engine: Engine, run_data: Path) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import delete, update

    from d2d.api.scope import Scope, load_roles
    from d2d.db import change_story, notification
    from d2d.notify import load_notify, run_notifications

    client = _client(engine, run_data)
    ctx = build_context(engine, ROOT / "config", run_data, ROOT / "agent" / "skills", None)
    owner = Scope(
        load_roles(ROOT / "config" / "roles.toml")["owner"], ctx.onto.household, ctx.onto.taxonomy
    )
    cfg = load_notify(ROOT / "config" / "notifications.toml")
    with engine.begin() as conn:
        conn.execute(delete(notification))
    noon = datetime(2026, 10, 18, 19, 0, tzinfo=UTC)  # 12:00 local
    night = datetime(2026, 10, 18, 7, 0, tzinfo=UTC)  # 00:00 local, quiet hours

    first = run_notifications(ctx.onto, owner, cfg, FakeSender(), now=noon)
    assert first.backfill == first.new > 0 and first.sent == 0  # never floods on first run
    assert run_notifications(ctx.onto, owner, cfg, FakeSender(), now=noon).new == 0

    # Two "new" stories appear: an alert and a notice, at night.
    published = [s for s in ctx.onto.list_stories(owner, status="published")]
    alert = next(s for s in published if cfg.severity.get(s["category"]) == "alert")
    notice = next(s for s in published if cfg.severity.get(s["category"]) == "notice")
    with engine.begin() as conn:
        conn.execute(
            delete(notification).where(
                notification.c.story_id.in_([alert["story_id"], notice["story_id"]])
            )
        )
    sender = FakeSender()
    s = run_notifications(ctx.onto, owner, cfg, sender, now=night)
    assert s.new == 2 and s.sent == 1 and s.deferred == 1
    assert sender.sent == [(alert["title"], 5)]  # alert bypasses quiet hours
    s = run_notifications(ctx.onto, owner, cfg, sender, now=noon)
    assert s.sent == 1 and sender.sent[-1][0] == notice["title"]  # notice after quiet hours
    assert run_notifications(ctx.onto, owner, cfg, sender, now=noon).sent == 0  # one push per story
    detail = _get(client, f"/stories/{alert['story_id']}")
    assert detail["notification"]["status"] == "sent"

    # Without a push channel, due pushes are recorded instead.
    with engine.begin() as conn:
        conn.execute(
            update(notification)
            .where(notification.c.story_id == alert["story_id"])
            .values(status="pending")
        )
    assert run_notifications(ctx.onto, owner, cfg, None, now=noon).recorded == 1
    _ = change_story


def test_feedback_is_routed_and_visible_in_insights(
    engine: Engine, run_data: Path, tmp_path: Path
) -> None:
    from d2d.calibration import calibrate, mark_exported
    from d2d.db import eval_item, review_item
    from d2d.feedback import route_feedback

    client = _client(engine, run_data)
    sid = _get(client, "/stories")[0]["story_id"]
    posts = [
        {"target_type": "story", "target_id": sid, "rating": "down", "reason": "wrong_category"},
        {"target_type": "story", "target_id": sid, "rating": "down", "reason": "wrong_grouping"},
        {
            "target_type": "story",
            "target_id": sid,
            "rating": "down",
            "reason": "missing_data",
            "comment": "no kWh",
        },
        {
            "target_type": "message",
            "target_id": "999999",
            "rating": "down",
            "reason": "unhelpful_answer",
        },
        {
            "target_type": "page",
            "target_id": "/",
            "rating": "comment",
            "comment": "love it",
            "page": "/",
        },
        {"target_type": "story", "target_id": sid, "rating": "up"},
    ]
    for p in posts:
        assert client.post("/api/feedback", json=p).status_code == 200
    counts = route_feedback(engine)
    assert {"labels", "linking", "data", "conversations", "ux", "kpi"} <= set(counts)
    assert route_feedback(engine) == {}  # idempotent
    with engine.connect() as conn:
        item = (
            conn.execute(select(review_item).where(review_item.c.story_id == sid)).mappings().one()
        )
        assert item["reason"] == "feedback" and item["resolved_at"] is None
        sets = set(conn.execute(select(eval_item.c.set_name)).scalars())
    assert {"labels", "linking", "data", "conversations", "ux"} <= sets

    data = _get(client, "/insights")
    assert data["feedback"]["unrouted"] == 0 and data["feedback"]["by_route"]["ux"] >= 1
    assert any(f["comment"] == "love it" for f in data["feedback"]["recent"])
    assert {"copilot", "review", "notifications", "eval_sets", "pipeline"} <= data.keys()
    assert client.get("/api/insights", headers={"X-D2D-Role": "guest"}).status_code == 403

    out = tmp_path / "calib"
    summary = calibrate(
        run_data,
        ROOT / "config",
        tmp_path / "no-models",
        ROOT / "evals/golden/stories.jsonl",
        "home_demo",
        out,
        engine,
    )
    assert summary["eval_items"]["linking"] >= 1 and (out / "report.md").is_file()
    assert summary["linking"]["best"]["violations"] <= summary["linking"]["current"]["violations"]
    assert (out / "evals" / "feedback" / "data.jsonl").read_text().count("no kWh") == 1
    mark_exported(engine, summary["item_ids"])
    again = calibrate(
        run_data,
        ROOT / "config",
        tmp_path / "no-models",
        ROOT / "evals/golden/stories.jsonl",
        "home_demo",
        tmp_path / "calib2",
        engine,
    )
    assert again["eval_items"] == {}


def test_guest_chat_is_rate_limited_and_owner_is_not(
    engine: Engine, run_data: Path, tmp_path: Path
) -> None:
    from d2d.api.guard import ChatGuard, ChatLimits
    from d2d.api.scope import ROLES
    from d2d.auth import auth_from_env

    auth = auth_from_env({"D2D_SECRET_KEY": "s" * 40, "D2D_USERS_FILE": str(tmp_path / "u.toml")})
    assert auth is not None
    auth.users.set_user("owner1", "owner", "owner-password-123", ROLES)
    auth.users.set_user("tester", "guest", "tester-password-123", ROLES)
    ctx = build_context(engine, ROOT / "config", run_data, ROOT / "agent" / "skills", None, auth)
    ctx.guard = ChatGuard(ChatLimits(per_user_per_hour=2, max_concurrent=1, max_waiting=0))
    app = create_app(ctx, web_dist=Path("/x"), allowed_hosts=["testserver"])
    guest, owner = TestClient(app), TestClient(app)
    guest.post("/api/auth/login", json={"username": "tester", "password": "tester-password-123"})
    owner.post("/api/auth/login", json={"username": "owner1", "password": "owner-password-123"})
    q = {"question": "Brief me"}
    answers = [guest.post("/api/chat", json=q) for _ in range(3)]
    assert [r.status_code for r in answers] == [200, 200, 429]
    refused = answers[2]
    assert "limit" in refused.json()["detail"] and int(refused.headers["retry-after"]) > 0
    assert all(owner.post("/api/chat", json=q).status_code == 200 for _ in range(4))
    # Guest answers only cite stories the guest may see.
    visible = {s["story_id"] for s in guest.get("/api/stories").json()}
    assert set(answers[0].json()["cited_ids"]) <= visible


def test_list_stories_tool_reports_coverage_totals_and_date_filters(
    engine: Engine, run_data: Path
) -> None:
    from d2d.agent.tools import ToolError, run_tool
    from d2d.api.scope import Scope, load_roles

    ctx = build_context(engine, ROOT / "config", run_data, ROOT / "agent" / "skills", None)
    owner = Scope(
        load_roles(ROOT / "config" / "roles.toml")["owner"], ctx.onto.household, ctx.onto.taxonomy
    )
    total = len(ctx.onto.list_stories(owner, limit=100_000))
    out = run_tool("list_stories", {"limit": 5}, ctx.onto, owner)
    cov = out["data_covers"]
    assert out["total_matching"] == total > 5 and out["returned"] == 5 == len(out["stories"])
    assert sum(out["by_category"].values()) == total
    assert cov["days"] >= 3 and cov["from"] < cov["to"]
    # "past 15 days" is longer than the data: everything matches, and the coverage says so.
    longer = run_tool("list_stories", {"last_days": 15}, ctx.onto, owner)
    assert longer["total_matching"] == total and "only has data" in longer["coverage_note"]
    assert "coverage_note" not in out
    last_day = run_tool("list_stories", {"last_days": 1}, ctx.onto, owner)
    assert 0 < last_day["total_matching"] < total and last_day["filter"]["since"] == cov["to"]
    one = run_tool("list_stories", {"since": cov["from"], "until": cov["from"]}, ctx.onto, owner)
    assert 0 < one["total_matching"] < total
    with pytest.raises(ToolError):
        run_tool("list_stories", {"since": "yesterday"}, ctx.onto, owner)


def test_warehouse_export_with_database(
    engine: Engine, run_data: Path, household: Household, tmp_path: Path
) -> None:
    import pyarrow.parquet as pq

    from d2d.analytics import export_warehouse

    taxonomy = load_categories(ROOT / "config" / "categories.toml")
    counts = export_warehouse(run_data, tmp_path, household, taxonomy, engine)
    stories = pq.read_table(tmp_path / "fact_story.parquet").to_pylist()
    assert counts["fact_story"] == len(stories) > 0
    labeled = [s for s in stories if s["category_id"]]
    assert labeled and all(s["category_id"] in taxonomy.categories for s in labeled)
    assert all(s["label_source"] for s in labeled)
    answers = pq.read_table(tmp_path / "fact_agent_answer.parquet").to_pylist()
    assert counts["fact_agent_answer"] == len(answers) > 0
    assert all(isinstance(a["grounded"], bool) and a["latency_ms"] >= 0 for a in answers)
    assert counts["fact_feedback"] > 0


def test_search_stories_tool_ranks_by_meaning_within_scope(engine: Engine, run_data: Path) -> None:
    from d2d.agent.search import HashingEmbedder, StorySearch
    from d2d.agent.tools import ToolError, run_tool
    from d2d.api.scope import Scope, load_roles

    ctx = build_context(engine, ROOT / "config", run_data, ROOT / "agent" / "skills", None)
    ctx.onto.search = StorySearch(HashingEmbedder())
    roles = load_roles(ROOT / "config" / "roles.toml")
    owner = Scope(roles["owner"], ctx.onto.household, ctx.onto.taxonomy)
    guest = Scope(roles["guest"], ctx.onto.household, ctx.onto.taxonomy)

    out = run_tool("search_stories", {"query": "firmware update rollout"}, ctx.onto, owner)
    assert out["method"] == "lexical" and 0 < len(out["stories"]) <= 5
    assert out["stories"][0]["category"] == "firmware_rollout"
    assert out["playbooks"][0]["category"] == "firmware_rollout"
    scores = [s["score"] for s in out["stories"]]
    assert scores == sorted(scores, reverse=True)

    visible = {s["story_id"] for s in ctx.onto.list_stories(guest, limit=100_000)}
    found = run_tool("search_stories", {"query": "garage door", "limit": 10}, ctx.onto, guest)
    assert {s["story_id"] for s in found["stories"]} <= visible
    with pytest.raises(ToolError):
        run_tool("search_stories", {"query": "  "}, ctx.onto, owner)


def test_mcp_server_serves_scoped_ontology(engine: Engine, run_data: Path) -> None:
    import anyio
    from mcp.client.client import Client

    from d2d.agent.search import HashingEmbedder, StorySearch
    from d2d.api.scope import Scope, load_roles
    from d2d.mcp_server import build_server

    ctx = build_context(engine, ROOT / "config", run_data, ROOT / "agent" / "skills", None)
    ctx.onto.search = StorySearch(HashingEmbedder())
    guest = Scope(
        load_roles(ROOT / "config" / "roles.toml")["guest"], ctx.onto.household, ctx.onto.taxonomy
    )
    visible = {s["story_id"] for s in ctx.onto.list_stories(guest, limit=100_000)}

    async def main() -> tuple[Any, Any, Any]:
        async with Client(build_server(ctx.onto, guest)) as client:
            listed = await client.call_tool("list_stories", {"limit": 25})
            found = await client.call_tool("search_stories", {"query": "garage door"})
            hidden = await client.call_tool(
                "get_device_history", {"device_id": "smart_plug_office"}
            )
            return listed, found, hidden

    listed, found, hidden = anyio.run(main)
    assert not listed.is_error and listed.structured_content["total_matching"] == len(visible)
    assert {s["story_id"] for s in listed.structured_content["stories"]} <= visible
    assert {s["story_id"] for s in found.structured_content["stories"]} <= visible
    assert hidden.is_error  # a bedroom device is outside the guest's shared rooms
