"""The API: one boundary for the dashboard, a phone and a local agent.

Routes under ``/api/`` and the MCP endpoint at ``/mcp`` are behind the token.
The built dashboard is served from ``sentinel/static`` at ``/`` when present.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from . import __version__, readings  # noqa: F401  (registers the catalog)
from .auth import TokenMiddleware, clear_session_cookie, load_or_create_token, matches, session_cookie
from .bridge import Bridge
from .reading import REGISTRY, Reading, take
from .readings.health import learn_identity
from .redact import Identity, Redactor

STATIC = Path(__file__).parent / "static"


class Session(BaseModel):
    token: str


class State:
    """What the app knows once: the bridge, the token, the machine's names."""

    def __init__(self, bridge: Bridge | None = None, token: str | None = None):
        self.bridge = bridge or Bridge.locate()
        self.token = token or load_or_create_token()
        self.identity = Identity()
        self.redactor = Redactor(self.identity)
        self.facts: dict[str, Any] = {}

    def learn(self) -> None:
        self.identity, self.facts = learn_identity(self.bridge)
        self.redactor = Redactor(self.identity)


def create_app(state: State | None = None, mcp: bool = True) -> FastAPI:
    state = state or State()
    mcp_app = None

    if mcp:
        from .mcp_server import build_mcp

        mcp_app = build_mcp(state)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        state.learn()
        async with contextlib.AsyncExitStack() as stack:
            if mcp_app is not None:
                # A mounted app's lifespan does not run by itself; the MCP session manager needs it.
                await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
            yield

    app = FastAPI(
        title="System Sentinel",
        version=__version__,
        description="A stethoscope for a Windows computer. Every route returns a reading whose outcome says whether the machine was observed.",
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.sentinel = state
    app.add_middleware(TokenMiddleware, token=state.token)

    def envelope(reading: Reading, unredacted: bool) -> JSONResponse:
        body = reading.to_dict()
        if not unredacted:
            body, removed = state.redactor.redact(body)
            body["redacted"] = removed
        return JSONResponse(body)

    @app.post("/api/session", tags=["session"])
    def open_session(session: Session, request: Request) -> Response:
        """Exchange the token for the session cookie the dashboard uses on any device."""
        if not matches(state.token, session.token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        response = JSONResponse({"ok": True})
        session_cookie(response, state.token, secure=request.url.scheme == "https")
        return response

    @app.delete("/api/session", tags=["session"])
    def close_session() -> Response:
        response = JSONResponse({"ok": True})
        clear_session_cookie(response)
        return response

    @app.get("/api/readings", tags=["readings"])
    def catalog() -> dict[str, Any]:
        """Every reading: name, description, classes, parameters, what redaction removes."""
        return {"readings": [spec.to_dict() for spec in REGISTRY.values()], "version": __version__}

    @app.get("/api/readings/{name}", tags=["readings"])
    async def reading(name: str, request: Request, unredacted: bool = False) -> Response:
        """Take a reading. Parameters are query parameters; see the catalog for each reading's."""
        if name not in REGISTRY:
            raise HTTPException(status_code=404, detail=f"no reading named {name!r}")
        params = {k: v for k, v in request.query_params.items() if k != "unredacted"}
        try:
            result = await take(name, state.bridge, params)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return envelope(result, unredacted)

    if mcp_app is not None:
        # The MCP route joins the main router at exactly /mcp. A mounted sub-app would match
        # only /mcp/, and the static mount at / would answer /mcp with 405 first.
        app.router.routes.extend(mcp_app.routes)

    if STATIC.is_dir():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="dashboard")

    return app
