"""Open Inference Protocol v2 (KServe / Triton) routes over the model server."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from d2d import __version__
from d2d.serving.server import InferError, ModelServer, NotReady, Registry
from d2d.serving.settings import PackageGates


def _error(exc: Exception) -> JSONResponse:
    if isinstance(exc, InferError):
        return JSONResponse({"error": str(exc)}, status_code=400)
    if isinstance(exc, NotReady):
        code = 404 if "not found" in str(exc) else 503
        return JSONResponse({"error": str(exc)}, status_code=code)
    return JSONResponse({"error": "inference failed"}, status_code=500)


def v2_router(server: ModelServer, dependencies: Sequence[Callable[..., Any]] = ()) -> APIRouter:
    r = APIRouter(prefix="/v2", dependencies=[Depends(d) for d in dependencies])

    @r.get("")
    def server_metadata() -> dict[str, Any]:
        return {"name": "d2d-model-server", "version": __version__, "extensions": []}

    @r.get("/health/live")
    def live() -> dict[str, bool]:
        return {"live": True}

    @r.get("/health/ready", response_model=None)
    def ready() -> dict[str, bool] | JSONResponse:
        try:
            for name, s in server.registry.states().items():
                if s.stable:
                    server.model(name, s.stable)
        except NotReady:
            return JSONResponse({"ready": False}, status_code=503)
        return {"ready": True}

    @r.get("/models/{name}", response_model=None)
    @r.get("/models/{name}/versions/{version}", response_model=None)
    def model_metadata(name: str, version: str | None = None) -> dict[str, Any] | JSONResponse:
        try:
            return server.metadata(name, version)
        except NotReady as exc:
            return _error(exc)

    @r.get("/models/{name}/ready", response_model=None)
    @r.get("/models/{name}/versions/{version}/ready", response_model=None)
    def model_ready(name: str, version: str | None = None) -> dict[str, Any] | JSONResponse:
        try:
            server.model(name, version or server.stable(name))
        except NotReady:
            return JSONResponse({"name": name, "ready": False}, status_code=503)
        return {"name": name, "ready": True}

    @r.post("/models/{name}/infer", response_model=None)
    @r.post("/models/{name}/versions/{version}/infer", response_model=None)
    def infer(
        name: str, body: Annotated[dict[str, Any], Body()], version: str | None = None
    ) -> dict[str, Any] | JSONResponse:
        try:
            return server.infer(name, version, body)
        except Exception as exc:  # mapped to the v2 error body
            return _error(exc)

    @r.get("/stats")
    def stats() -> dict[str, Any]:
        """Per-version request counts, error rates and latency percentiles (not part of v2)."""
        return {
            "versions": {f"{n}/{v}": s.summary() for (n, v), s in server.stats.items()},
            "shadow": {f"{n}/{v}": t for (n, v), t in server.shadow.items()},
            "state": {k: v.__dict__ for k, v in server.registry.states().items()},
        }

    return r


def create_model_app(
    registry: Path, gates: PackageGates, hosts: list[str] | None = None
) -> FastAPI:
    """Standalone model server (``d2d model serve``); binds to localhost by default."""
    app = FastAPI(title="D2D model server", version=__version__, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts or ["127.0.0.1", "localhost"])
    app.state.server = ModelServer(Registry(registry), gates)
    app.include_router(v2_router(app.state.server))
    return app
