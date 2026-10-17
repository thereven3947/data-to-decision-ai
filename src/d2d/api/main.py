"""FastAPI app: read API + review/feedback writes + agent chat, and the built web UI at `/`."""

from __future__ import annotations

import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Engine, create_engine, insert, select, text

from d2d import __version__
from d2d.agent.provider import OllamaProvider, Provider, load_llm
from d2d.agent.runtime import Agent
from d2d.agent.skills import load_skills
from d2d.api.repo import Ontology
from d2d.api.scope import ROLES, RolePolicy, Scope, load_roles
from d2d.config import config_dir, load_categories, load_household
from d2d.db import FEEDBACK_REASONS, chat_message, database_url, feedback
from d2d.enrichment import load_enrichment
from d2d.hitl.review import ReviewError, create_case, human_label

ROOT = Path.cwd()


@dataclass
class AppContext:
    onto: Ontology
    roles: dict[str, RolePolicy]
    agent: Agent
    provider: Provider | None


def build_context(
    engine: Engine,
    cfg_dir: Path,
    data_dir: Path,
    skills_dir: Path,
    provider: Provider | None,
) -> AppContext:
    household = load_household(cfg_dir / "household.toml")
    taxonomy = load_categories(cfg_dir / "categories.toml")
    enrichment = load_enrichment(cfg_dir / "enrichment.toml", cfg_dir / "semantics.toml")
    onto = Ontology(engine, household, taxonomy, enrichment, data_dir)
    max_calls = load_llm(cfg_dir / "llm.toml").max_tool_calls
    agent = Agent(onto, provider, load_skills(skills_dir), max_calls)
    return AppContext(onto, load_roles(cfg_dir / "roles.toml"), agent, provider)


def default_context() -> AppContext:
    cfg_dir = config_dir()
    llm = load_llm(cfg_dir / "llm.toml")
    provider = None if os.environ.get("D2D_LLM") == "off" else OllamaProvider(llm)
    return build_context(
        create_engine(database_url(), pool_pre_ping=True),
        cfg_dir,
        Path(os.environ.get("D2D_DATA_DIR") or ROOT / "data"),
        Path(os.environ.get("D2D_SKILLS_DIR") or ROOT / "agent" / "skills"),
        provider,
    )


class LabelIn(BaseModel):
    category: str
    note: str | None = None


class CaseIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    story_ids: list[str] = []
    note: str | None = None


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


class FeedbackIn(BaseModel):
    target_type: str = Field(pattern="^(story|message|page)$")
    target_id: str = Field(min_length=1, max_length=200)
    rating: str = Field(pattern="^(up|down|comment)$")
    reason: str | None = None
    comment: str | None = Field(default=None, max_length=4000)
    page: str | None = Field(default=None, max_length=500)


def get_ctx(request: Request) -> AppContext:
    if request.app.state.ctx is None:
        request.app.state.ctx = default_context()
    c: AppContext = request.app.state.ctx
    return c


Ctx = Annotated[AppContext, Depends(get_ctx)]


def get_scope(c: Ctx, x_d2d_role: Annotated[str, Header()] = "owner") -> Scope:
    if x_d2d_role not in c.roles:
        raise HTTPException(400, f"unknown role {x_d2d_role!r}; expected one of {ROLES}")
    return Scope(c.roles[x_d2d_role], c.onto.household, c.onto.taxonomy)


S = Annotated[Scope, Depends(get_scope)]


def require_owner(s: Scope) -> None:
    if not s.policy.can_label:
        raise HTTPException(403, f"role {s.role} cannot change labels or cases")


def create_app(context: AppContext | None = None, web_dist: Path | None = None) -> FastAPI:
    app = FastAPI(title="D2D Change Control API", version=__version__)
    app.state.ctx = context

    @app.get("/api/health")
    def health(c: Ctx) -> dict[str, Any]:
        with c.onto.engine.connect() as conn:
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        llm = c.provider.model_tag if c.provider else None
        available = isinstance(c.provider, OllamaProvider) and c.provider.available()
        rev = ROOT / "REVISION"
        return {
            "ok": True,
            "version": __version__,
            "revision": rev.read_text().strip() if rev.is_file() else None,
            "schema": revision,
            "llm": llm,
            "llm_available": available,
        }

    @app.get("/api/meta")
    def meta(c: Ctx, s: S) -> dict[str, Any]:
        return {
            "home": {"id": c.onto.household.id, "timezone": c.onto.household.timezone},
            "role": s.role,
            "can_label": s.policy.can_label,
            "roles": [{"name": r.name, "description": r.description} for r in c.roles.values()],
            "categories": [
                {"id": k.id, "name": k.name, "plain": k.plain, "energy": k.energy}
                for k in c.onto.taxonomy.categories.values()
                if s.category_visible(k.id)
            ],
            "feedback_reasons": list(FEEDBACK_REASONS),
            "skills": {k: v.intent for k, v in c.agent.skills.skills.items()},
        }

    @app.get("/api/stories")
    def stories(
        c: Ctx,
        s: S,
        status: str | None = None,
        category: str | None = None,
        device_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return c.onto.list_stories(s, status, category, device_id)

    @app.get("/api/stories/{story_id}")
    def story(c: Ctx, s: S, story_id: str) -> dict[str, Any]:
        found = c.onto.get_story(s, story_id)
        if found is None:
            raise HTTPException(404, "story not found")
        return found

    @app.post("/api/stories/{story_id}/label")
    def label_story(c: Ctx, s: S, story_id: str, body: LabelIn) -> dict[str, Any]:
        require_owner(s)
        if c.onto.get_story(s, story_id) is None:
            raise HTTPException(404, "story not found")
        try:
            label_id = human_label(
                c.onto.engine, c.onto.taxonomy, story_id, body.category, s.role, body.note
            )
        except (ReviewError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"label_id": label_id, "story": c.onto.get_story(s, story_id)}

    @app.get("/api/review")
    def review(c: Ctx, s: S) -> list[dict[str, Any]]:
        return c.onto.review_queue(s)

    @app.get("/api/events")
    def events(
        c: Ctx,
        s: S,
        device_id: str | None = None,
        change_kind: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        return c.onto.list_events(s, device_id, change_kind, limit=min(limit, 5000))

    @app.get("/api/changelog")
    def changelog(
        c: Ctx, s: S, device_id: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        return c.onto.changelog(s, device_id, min(limit, 10_000))

    @app.get("/api/devices")
    def devices(c: Ctx, s: S) -> list[dict[str, Any]]:
        return c.onto.list_devices(s)

    @app.get("/api/devices/{device_id}")
    def device(c: Ctx, s: S, device_id: str) -> dict[str, Any]:
        found = c.onto.device_history(s, device_id)
        if found is None:
            raise HTTPException(404, "device not found")
        return found

    @app.get("/api/cases")
    def cases(c: Ctx, s: S) -> list[dict[str, Any]]:
        return c.onto.list_cases(s)

    @app.post("/api/cases")
    def new_case(c: Ctx, s: S, body: CaseIn) -> dict[str, Any]:
        require_owner(s)
        try:
            case_id = create_case(c.onto.engine, body.title, s.role, body.story_ids, body.note)
        except ReviewError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"case_id": case_id}

    @app.get("/api/kpis")
    def kpis(c: Ctx, s: S) -> dict[str, Any]:
        return c.onto.kpis(s)

    @app.post("/api/chat")
    def chat(c: Ctx, s: S, body: ChatIn) -> dict[str, Any]:
        session_id = body.session_id or uuid.uuid4().hex
        with c.onto.engine.connect() as conn:
            history = [
                (r.question, r.answer)
                for r in conn.execute(
                    select(chat_message.c.question, chat_message.c.answer)
                    .where(chat_message.c.session_id == session_id)
                    .order_by(chat_message.c.message_id)
                )
            ]
        rec = c.agent.answer(body.question, s, history)
        tool_calls = [asdict(t) for t in rec.tool_calls]
        with c.onto.engine.begin() as conn:
            message_id = conn.execute(
                insert(chat_message)
                .values(
                    session_id=session_id,
                    user_role=s.role,
                    question=body.question,
                    answer=rec.answer,
                    skill=rec.skill,
                    skill_sha=rec.skill_sha,
                    llm_model_tag=rec.llm_model_tag,
                    tool_calls=tool_calls,
                    cited_ids=rec.cited_ids,
                    checker_result=rec.checker,
                    latency_ms=rec.latency_ms,
                    tokens_in=rec.tokens_in,
                    tokens_out=rec.tokens_out,
                )
                .returning(chat_message.c.message_id)
            ).scalar_one()
        return {"message_id": message_id, "session_id": session_id, **asdict(rec)}

    @app.get("/api/chat/{session_id}")
    def chat_history(c: Ctx, s: S, session_id: str) -> list[dict[str, Any]]:
        with c.onto.engine.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    select(chat_message)
                    .where(chat_message.c.session_id == session_id)
                    .where(chat_message.c.user_role == s.role)
                    .order_by(chat_message.c.message_id)
                ).mappings()
            ]

    @app.post("/api/feedback")
    def post_feedback(c: Ctx, s: S, body: FeedbackIn) -> dict[str, Any]:
        if body.reason and body.reason not in FEEDBACK_REASONS:
            raise HTTPException(400, f"reason must be one of {FEEDBACK_REASONS}")
        with c.onto.engine.begin() as conn:
            fid = conn.execute(
                insert(feedback)
                .values(**body.model_dump(), user_role=s.role)
                .returning(feedback.c.feedback_id)
            ).scalar_one()
        return {"feedback_id": fid}

    dist = web_dist or Path(os.environ.get("D2D_WEB_DIST") or ROOT / "web" / "dist")
    if (dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(404)
            file = (dist / path).resolve()
            if path and file.is_file() and file.is_relative_to(dist.resolve()):
                return FileResponse(file)
            return FileResponse(dist / "index.html")

    return app
