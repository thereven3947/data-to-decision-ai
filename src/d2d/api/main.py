"""FastAPI app: read API + review/feedback writes + agent chat, and the built web UI at `/`."""

from __future__ import annotations

import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Engine, create_engine, insert, select, text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from d2d import __version__
from d2d.agent.provider import OllamaProvider, Provider, load_llm
from d2d.agent.runtime import Agent
from d2d.agent.skills import load_skills
from d2d.api.repo import Ontology
from d2d.api.scope import (
    ROLES,
    AccountPolicy,
    RolePolicy,
    Scope,
    load_account_policy,
    load_roles,
)
from d2d.auth import COOKIE, AuthConfig, auth_from_env, fingerprint, verify_password
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
    auth: AuthConfig | None = None
    accounts: AccountPolicy = field(default_factory=lambda: AccountPolicy(1, ("owner",)))


def build_context(
    engine: Engine,
    cfg_dir: Path,
    data_dir: Path,
    skills_dir: Path,
    provider: Provider | None,
    auth: AuthConfig | None = None,
    accounts: AccountPolicy | None = None,
) -> AppContext:
    household = load_household(cfg_dir / "household.toml")
    taxonomy = load_categories(cfg_dir / "categories.toml")
    enrichment = load_enrichment(cfg_dir / "enrichment.toml", cfg_dir / "semantics.toml")
    onto = Ontology(engine, household, taxonomy, enrichment, data_dir)
    max_calls = load_llm(cfg_dir / "llm.toml").max_tool_calls
    agent = Agent(onto, provider, load_skills(skills_dir), max_calls)
    policy = accounts or load_account_policy(cfg_dir / "roles.toml")
    return AppContext(onto, load_roles(cfg_dir / "roles.toml"), agent, provider, auth, policy)


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
        auth_from_env(),
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


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


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


def session_user(c: AppContext, request: Request) -> tuple[str, str] | None:
    """(username, role) of a valid session cookie, or None."""
    if c.auth is None:
        return None
    token = request.cookies.get(COOKIE)
    claims = c.auth.signer.verify(token) if token else None
    if claims is None:
        return None
    users = c.auth.users.users()
    user = users.get(claims[0])
    if user is None or fingerprint(user.password) != claims[1] or user.role not in c.roles:
        return None
    if not c.accounts.allows({u.username: u.role for u in users.values()}, user.username):
        return None
    return user.username, user.role


def get_scope(
    c: Ctx, request: Request, x_d2d_role: Annotated[str | None, Header()] = None
) -> Scope:
    """Role of the logged-in user. Only owners may preview another role with X-D2D-Role.

    With login turned off (D2D_AUTH=off, local development) the header alone picks the role.
    """
    if x_d2d_role is not None and x_d2d_role not in c.roles:
        raise HTTPException(400, f"unknown role {x_d2d_role!r}; expected one of {ROLES}")
    if c.auth is None:
        role = x_d2d_role or "owner"
        return Scope(c.roles[role], c.onto.household, c.onto.taxonomy, username=None)
    found = session_user(c, request)
    if found is None:
        raise HTTPException(401, "login required")
    username, role = found
    if role == "owner" and x_d2d_role:
        role = x_d2d_role
    return Scope(c.roles[role], c.onto.household, c.onto.taxonomy, username=username)


S = Annotated[Scope, Depends(get_scope)]


_DUMMY_HASH = "scrypt$32768$8$1$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _owned_by(s: Scope) -> Any:
    """Chat history belongs to the logged-in user (or to the role when login is off)."""
    if s.username is not None:
        return chat_message.c.username == s.username
    return chat_message.c.user_role == s.role


def require_owner(s: Scope) -> None:
    if not s.policy.can_label:
        raise HTTPException(403, f"role {s.role} cannot change labels or cases")


DEFAULT_HOSTS = ("127.0.0.1", "localhost")
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def create_app(
    context: AppContext | None = None,
    web_dist: Path | None = None,
    allowed_hosts: list[str] | None = None,
) -> FastAPI:
    # Interactive API docs only on request (D2D_API_DOCS=on); the public demo does not need them.
    docs = os.environ.get("D2D_API_DOCS") == "on"
    app = FastAPI(
        title="Change Control Beta API",
        version=__version__,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )
    app.state.ctx = context
    # Only answer requests addressed to this machine (blocks DNS-rebinding from other websites).
    # Add a tunnel hostname later with D2D_ALLOWED_HOSTS=127.0.0.1,localhost,demo.example.com.
    hosts = allowed_hosts or [
        h.strip() for h in os.environ.get("D2D_ALLOWED_HOSTS", ",".join(DEFAULT_HOSTS)).split(",")
    ]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[h for h in hosts if h])

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    @app.get("/api/health")
    def health(c: Ctx, request: Request) -> dict[str, Any]:
        rev = ROOT / "REVISION"
        revision = rev.read_text().strip() if rev.is_file() else None
        if c.auth is not None and session_user(c, request) is None:
            # Public: just enough for the deploy health check.
            return {"ok": True, "revision": revision}
        with c.onto.engine.connect() as conn:
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        llm = c.provider.model_tag if c.provider else None
        available = isinstance(c.provider, OllamaProvider) and c.provider.available()
        return {
            "ok": True,
            "version": __version__,
            "revision": revision,
            "schema": revision,
            "llm": llm,
            "llm_available": available,
        }

    def secure_cookie(request: Request) -> bool:
        return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"

    @app.get("/api/auth/status")
    def auth_status(c: Ctx, request: Request) -> dict[str, Any]:
        found = session_user(c, request)
        return {
            "auth_enabled": c.auth is not None,
            "users_configured": bool(c.auth and c.auth.users.users()),
            "user": {"username": found[0], "role": found[1]} if found else None,
        }

    @app.post("/api/auth/login")
    def login(c: Ctx, request: Request, response: Response, body: LoginIn) -> dict[str, Any]:
        if c.auth is None:
            raise HTTPException(400, "login is turned off (D2D_AUTH=off)")
        client = request.client.host if request.client else "unknown"
        keys = (f"user:{body.username.lower()}", f"ip:{client}")
        if c.auth.limiter.blocked(*keys):
            raise HTTPException(429, "too many failed logins; try again in 15 minutes")
        users = c.auth.users.users()
        user = users.get(body.username.lower())
        # Verify against a dummy hash for unknown users so timing does not reveal valid names.
        encoded = user.password if user else _DUMMY_HASH
        allowed = user is not None and c.accounts.allows(
            {u.username: u.role for u in users.values()}, user.username
        )
        if not verify_password(body.password, encoded) or not allowed or user is None:
            c.auth.limiter.fail(*keys)
            raise HTTPException(401, "wrong username or password")
        c.auth.limiter.reset(*keys)
        response.set_cookie(
            COOKIE,
            c.auth.signer.issue(user.username, fingerprint(user.password)),
            max_age=c.auth.signer.ttl,
            httponly=True,
            samesite="strict",
            secure=secure_cookie(request),
            path="/",
        )
        return {"username": user.username, "role": user.role}

    @app.post("/api/auth/logout")
    def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/meta")
    def meta(c: Ctx, s: S) -> dict[str, Any]:
        return {
            "home": {"id": c.onto.household.id, "timezone": c.onto.household.timezone},
            "role": s.role,
            "username": s.username,
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
                c.onto.engine, c.onto.taxonomy, story_id, body.category, s.actor, body.note
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
            case_id = create_case(c.onto.engine, body.title, s.actor, body.story_ids, body.note)
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
                    .where(_owned_by(s))
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
                    username=s.username,
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
                    .where(_owned_by(s))
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
                .values(**body.model_dump(), user_role=s.role, username=s.username)
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
