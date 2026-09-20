"""The API: one boundary for the dashboard, a phone and a local agent.

Routes under ``/api/`` and the MCP endpoint at ``/mcp`` are behind the token.
The built dashboard is served from ``sentinel/static`` at ``/`` when present.
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from . import __version__, capture, readings  # noqa: F401  (readings registers the catalog)
from .auth import CODE_TTL, TokenMiddleware, clear_session_cookie, code_valid, load_or_create_token, matches, session_cookie
from .bridge import Bridge
from .reading import REGISTRY, Reading, take
from .readings.health import learn_identity
from .redact import Identity, Redactor
from .stack import Duplicate, Prompts, Stack, compose, new_item
from .stream import Stream

STATIC = Path(__file__).parent / "static"

SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


class Session(BaseModel):
    token: str


class TakeRequest(BaseModel):
    """Read the machine now, and stack what comes back."""

    name: str
    params: dict[str, Any] = {}


class NewStackItem(BaseModel):
    kind: Literal["reading", "selection", "note"] = "reading"
    title: str | None = None
    rank: int | None = None
    verbosity: Literal["summary", "full"] | None = None
    ids: list[int] | None = None
    note: str | None = None
    take: TakeRequest | None = None
    envelope: dict[str, Any] | None = None


class ItemChange(BaseModel):
    rank: int | None = None
    verbosity: Literal["summary", "full"] | None = None
    title: str | None = None


class StackChoice(BaseModel):
    prompt_id: str | None = None
    system_prompt: bool | None = None


class NewPrompt(BaseModel):
    name: str
    description: str = ""
    content: str = ""


class PromptChange(BaseModel):
    name: str | None = None
    description: str | None = None
    content: str | None = None


class State:
    """What the app knows once: the bridge, the token, the machine's names, the stack it keeps."""

    def __init__(self, bridge: Bridge | None = None, token: str | None = None):
        self.bridge = bridge or Bridge.locate()
        self.token = token or load_or_create_token()
        self.identity = Identity()
        self.redactor = Redactor(self.identity)
        self.facts: dict[str, Any] = {}
        self.stack = Stack()
        self.prompts = Prompts()
        self.spent_codes: dict[str, float] = {}

    def is_local(self, request: Request) -> bool:
        """Whether the request came from this machine. A method so a test can say otherwise."""
        client = request.client
        return client is not None and client.host in ("127.0.0.1", "::1")

    def spend_code(self, code: str) -> bool:
        """Accept a launcher's one-time code once: minted from this token, unexpired, unspent.

        Spent codes are remembered only as long as an unspent one could still be worth anything,
        so the dict stays the size of one double-click rather than growing with the session.
        """
        now = time.time()
        self.spent_codes = {spent: until for spent, until in self.spent_codes.items() if until > now}
        if not code or code in self.spent_codes or not code_valid(self.token, code, now):
            return False
        self.spent_codes[code] = now + CODE_TTL
        return True

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

    def guarded(payload: Any, unredacted: bool = False, status_code: int = 200) -> JSONResponse:
        """The one way anything leaves: redacted unless the caller asked for the real values by name."""
        if unredacted:
            return JSONResponse(payload, status_code=status_code)
        body, removed = state.redactor.redact(payload)
        if isinstance(body, dict):
            body["redacted"] = removed
        return JSONResponse(body, status_code=status_code)

    def envelope(reading: Reading, unredacted: bool) -> JSONResponse:
        return guarded(reading.to_dict(), unredacted)

    @app.post("/api/session", tags=["session"])
    def open_session(session: Session, request: Request) -> Response:
        """Exchange the token for the session cookie the dashboard uses on any device."""
        if not matches(state.token, session.token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        response = JSONResponse({"ok": True})
        session_cookie(response, state.token, secure=request.url.scheme == "https")
        return response

    @app.get("/api/session/open", tags=["session"])
    def open_session_by_code(request: Request, code: str = "") -> Response:
        """The launcher's door: spend a one-time code minted on this machine for the session cookie
        and land on the dashboard. A person who double-clicks the tool never holds the token; a
        caller from anywhere else is refused before the code is spent, so it survives for its owner."""
        if not state.is_local(request) or not state.spend_code(code):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        response = RedirectResponse("/", status_code=303)
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

    @app.get("/api/stream", tags=["stream"])
    async def stream(request: Request, unredacted: bool = False) -> Response:
        """The log as it happens: ``record`` for each new record matching the presets, ``heartbeat``
        every poll, ``bridge`` when a poll did not observe the machine. Server-sent events."""
        source = Stream(state.bridge, None if unredacted else state.redactor)
        return StreamingResponse(source.events(request.is_disconnected), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.get("/api/stack", tags=["stack"])
    def stack_state(unredacted: bool = False) -> Response:
        """The evidence chosen for handoff, in the order it was added, with the prompt it leads with."""
        return guarded(state.stack.state(), unredacted)

    @app.patch("/api/stack", tags=["stack"])
    def stack_choose(choice: StackChoice, unredacted: bool = False) -> Response:
        """Change which prompt leads the handoff, or whether one does at all."""
        changed = state.stack.choose(prompt_id=choice.prompt_id, system_prompt=choice.system_prompt, set_prompt="prompt_id" in choice.model_fields_set)
        return guarded(changed, unredacted)

    @app.delete("/api/stack", tags=["stack"])
    def stack_clear() -> Response:
        state.stack.clear()
        return guarded(state.stack.state())

    @app.post("/api/stack/items", tags=["stack"], status_code=201)
    async def stack_add(item: NewStackItem, unredacted: bool = False) -> Response:
        """Add evidence: a reading the server takes now, a reading the caller holds, some of its
        records, or a note. The same reading with the same parameters and records is refused."""
        try:
            added = await new_item(state.stack, state.bridge, item.model_dump(exclude_unset=True))
            return guarded(state.stack.add(added).to_dict(), unredacted, status_code=201)
        except Duplicate as exc:
            return JSONResponse({"error": "duplicate", "detail": "this evidence is already on the stack", "id": str(exc)}, status_code=409)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch("/api/stack/items/{item_id}", tags=["stack"])
    def stack_change(item_id: str, change: ItemChange, unredacted: bool = False) -> Response:
        try:
            return guarded(state.stack.update(item_id, rank=change.rank, verbosity=change.verbosity, title=change.title), unredacted)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"no item {item_id!r}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/stack/items/{item_id}", tags=["stack"])
    def stack_remove(item_id: str) -> Response:
        try:
            state.stack.remove(item_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"no item {item_id!r}") from exc
        return guarded(state.stack.state())

    @app.get("/api/stack/composed", tags=["stack"])
    def stack_composed(unredacted: bool = False) -> dict[str, Any]:
        """The handoff as Markdown: the prompt, then the evidence by rank, each with its provenance."""
        return compose(state.stack, state.prompts, None if unredacted else state.redactor)

    @app.get("/api/prompts", tags=["stack"])
    def prompts_list() -> dict[str, Any]:
        """The prompt library. The six the tool ships with are marked ``builtin``; all are editable."""
        return {"prompts": state.prompts.all()}

    @app.post("/api/prompts", tags=["stack"], status_code=201)
    def prompts_add(prompt: NewPrompt) -> Response:
        try:
            return JSONResponse(state.prompts.add(prompt.name, prompt.description, prompt.content), status_code=201)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch("/api/prompts/{prompt_id}", tags=["stack"])
    def prompts_change(prompt_id: str, change: PromptChange) -> dict[str, Any]:
        try:
            return state.prompts.update(prompt_id, **change.model_dump(exclude_unset=True))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"no prompt {prompt_id!r}") from exc

    @app.delete("/api/prompts/{prompt_id}", tags=["stack"])
    def prompts_remove(prompt_id: str) -> dict[str, Any]:
        try:
            state.prompts.remove(prompt_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"no prompt {prompt_id!r}") from exc
        return {"prompts": state.prompts.all()}

    @app.post("/api/captures", tags=["captures"])
    async def captures_create(unredacted: bool = False) -> Response:
        """Take every reading now, write the ZIP into the data directory and return it. Takes as
        long as the slowest query on this machine; nothing is sent anywhere."""
        made = await capture.create(state.bridge, state.stack, state.prompts, None if unredacted else state.redactor)
        return FileResponse(made.path, media_type="application/zip", filename=made.name, headers={"X-Capture-Name": made.name})

    @app.get("/api/captures", tags=["captures"])
    def captures_list() -> dict[str, Any]:
        """What is on disk, newest first. Captures are never deleted by the tool."""
        return {"captures": capture.listing()}

    @app.get("/api/captures/{name}", tags=["captures"])
    def captures_get(name: str) -> Response:
        path = capture.find(name)
        if path is None:
            raise HTTPException(status_code=404, detail=f"no capture {name!r}")
        return FileResponse(path, media_type="application/zip", filename=path.name)

    if mcp_app is not None:
        # The MCP route joins the main router at exactly /mcp. A mounted sub-app would match
        # only /mcp/, and the static mount at / would answer /mcp with 405 first.
        app.router.routes.extend(mcp_app.routes)

    if STATIC.is_dir():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="dashboard")

    return app
