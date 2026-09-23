"""The API: one boundary for the dashboard, a phone and a local agent.

Routes under ``/api/`` and the MCP endpoint at ``/mcp`` are behind the token.
The built dashboard is served from ``sentinel/static`` at ``/`` when present.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Literal

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.staticfiles import StaticFiles

from . import __version__, capture, readings  # noqa: F401  (readings registers the catalog)
from .auth import LINK_TTL, TokenMiddleware, bearer, clear_session_cookie, code_expiry, code_valid, load_or_create_token, matches, session_cookie
from .bridge import Bridge, shutdown_sessions
from .link import qr_svg, reach, sign_in_link
from .performance import KEEP_DAYS, PerformanceCollector, PerformanceStore
from .reading import REGISTRY, Reading
from .readings.health import learn_identity
from .redact import Identity, Redactor
from .serialization import json_safe_integers
from .service import ReadingService
from .stack import Duplicate, Prompts, Stack, compose, new_item
from .stream import Stream

STATIC = Path(__file__).parent / "static"
RELEARN_SECONDS = 60.0
DEFAULT_PORT = 8000


def _loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


def _served_port(request: Request) -> int:
    """The port this connection arrived on — the one Tailscale has to publish for a link to work.

    Not configuration: a proxy on this machine dials the loopback listener, so a request that came
    through Tailscale arrives on exactly the port Tailscale was pointed at. Every ASGI server sets
    it; the tool's own default stands in if one ever does not."""
    server = request.scope.get("server")
    return int(server[1]) if server and server[1] else DEFAULT_PORT


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
    ids: list[int | str] | None = None
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


class PerformanceCollectionChange(BaseModel):
    enabled: bool
    interval_seconds: int = 60


class State:
    """What the app knows once: the bridge, the token, the machine's names, the stack it keeps."""

    def __init__(self, bridge: Bridge | None = None, token: str | None = None, collect_performance: bool = False):
        self.bridge = bridge or Bridge.locate()
        self.readings = ReadingService(self.bridge)
        self.token = token or load_or_create_token()
        #: How this process ends when it is asked to. Whoever runs the server sets it — the tray
        #: launcher and ``serve`` both do — and ``POST /api/quit`` is the only caller. Left unset
        #: (``serve --reload``, a test app), there is no way to stop this process from inside it,
        #: and the route says so rather than pretending.
        self.on_quit: Callable[[], None] | None = None
        self._redactor = Redactor()
        self._learned_at: float | None = None
        self.stack = Stack()
        self.prompts = Prompts()
        self.spent_codes: dict[str, float] = {}
        self.performance_store = PerformanceStore()
        self.performance_collector = PerformanceCollector(self.bridge, self.performance_store) if collect_performance else None

    def is_local(self, request: Request) -> bool:
        """Whether the request came from this machine. A method so a test can say otherwise.

        Local means the client is a loopback address, or the connection was accepted on one:
        a server bound to loopback can only be reached from this machine, whether by a browser
        the launcher opened or by a proxy running here for an authenticated private network
        (Tailscale serve dials the loopback listener, not from 127.0.0.1)."""
        client = request.client
        server = request.scope.get("server")
        return _loopback(client.host if client else None) or _loopback(server[0] if server else None)

    def spend_code(self, code: str) -> bool:
        """Accept a launcher's one-time code once: minted from this token, unexpired, unspent.

        A spent code is remembered until its own expiry, not for a fixed while: a code minted to
        cross to another device outlives the launcher's by minutes, and remembering it for less
        than it is valid for would let it be spent twice. Once it has run out, ``code_valid``
        refuses it on its own and the dict drops it, so this stays the size of one double-click
        rather than growing with the session.
        """
        now = time.time()
        self.spent_codes = {spent: until for spent, until in self.spent_codes.items() if until > now}
        if not code or code in self.spent_codes or not code_valid(self.token, code, now):
            return False
        self.spent_codes[code] = code_expiry(code)
        return True

    def learn(self) -> None:
        """Ask the machine its names. Field-name redaction never depends on this; replacing the
        names inside message text does, so an answer that did not come is asked for again later."""
        self._learned_at = time.time()
        learned, facts = learn_identity(self.bridge)
        previous = self.identity
        # One policy owns the identity. A later failed lookup must not erase names already learned.
        if facts["outcome"] == "ok":
            identity = Identity(host=learned.host or previous.host, user=learned.user or previous.user)
        else:
            identity = Identity(host=previous.host or learned.host, user=previous.user or learned.user)
        self._redactor = Redactor(identity)

    @property
    def identity(self) -> Identity:
        return self._redactor.identity

    @property
    def redactor(self) -> Redactor:
        """The policy with the machine's names in it. If the names were never learned (the bridge
        was not answering when the server started), try again, at most once a minute, so the
        default cannot quietly stay weaker than it should for the life of the process."""
        if self.identity.host is None and (self._learned_at is None or time.time() - self._learned_at > RELEARN_SECONDS):
            self.learn()
        return self._redactor


def create_app(state: State | None = None, mcp: bool = True) -> FastAPI:
    state = state or State(collect_performance=True)
    mcp_app = None

    if mcp:
        from .mcp_server import build_mcp

        mcp_app = build_mcp(state)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state.learn()
        if state.performance_collector is not None:
            state.performance_collector.start()
        try:
            async with contextlib.AsyncExitStack() as stack:
                if mcp_app is not None:
                    # A mounted app's lifespan does not run by itself; the MCP session manager needs it.
                    await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
                yield
        finally:
            if state.performance_collector is not None:
                await asyncio.to_thread(state.performance_collector.stop)
            if mcp_app is not None:
                mcp_app.state.listen.close()
            # Normal lifespan shutdown ends the bridge's child sessions; atexit covers exits that
            # skip this lifespan. A powershell.exe left behind by a stopped server would be exactly
            # the kind of thing this tool exists to make visible.
            await asyncio.to_thread(shutdown_sessions)

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
    app.state.mcp_surface = mcp_app.state.surface if mcp_app is not None else None
    app.add_middleware(TokenMiddleware, token=state.token)

    async def handoff_changed() -> None:
        if mcp_app is not None:
            await mcp_app.state.surface.handoff_changed()

    def handoff_changed_from_route() -> None:
        # FastAPI runs synchronous routes in a worker thread. Publish on the server's event loop
        # so the subscription stream can receive the event without moving file I/O onto that loop.
        if mcp_app is not None:
            anyio.from_thread.run(mcp_app.state.surface.handoff_changed)

    def guarded(payload: Any, unredacted: bool = False, status_code: int = 200) -> JSONResponse:
        """The one way anything leaves: redacted unless the caller asked for the real values by name."""
        if not unredacted:
            payload = state.redactor.attach(payload)
        return JSONResponse(json_safe_integers(payload), status_code=status_code)

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
    def open_session_by_code(request: Request, code: str = "", to: str = "") -> Response:
        """The launcher's door: spend a one-time code minted on this machine for the session cookie
        and land on the dashboard. A person who double-clicks the tool never holds the token; a
        caller from anywhere else is refused before the code is spent, so it survives for its owner.

        ``to=link`` lands on the dashboard's sign-in link for another device, which is how the tray
        hands a phone over. It is the only value with a destination of its own; anything else lands
        on the dashboard, so the parameter cannot send anyone anywhere but here."""
        if not state.is_local(request) or not state.spend_code(code):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        response = RedirectResponse("/#link" if to == "link" else "/", status_code=303)
        session_cookie(response, state.token, secure=request.url.scheme == "https")
        return response

    @app.post("/api/session/link", tags=["session"])
    async def session_link(request: Request) -> Response:
        """A sign-in link for another device: the address this machine publishes on a private
        network, a one-time code that lasts five minutes and is spent by the first device to
        follow it, and a QR code of the link so a camera can carry it across.

        The answer is a reading of the machine rather than a setting. ``outcome`` is ``ok`` when
        Tailscale publishes the port this request arrived on, ``empty`` when the machine answered
        and publishes nothing for it (``installed`` says whether Tailscale is there at all, and
        ``port`` is the port it would have to publish), and ``unavailable`` or ``failed`` when it
        could not be asked — three different things, and ``detail`` says which in a sentence a
        person can act on. ``url``, ``expires_at`` and ``qr`` are null unless there is an address.

        Whoever is signed in here may sign in another device, so this needs the session cookie or
        the bearer header like any other route and nothing more. The token is never in the answer.
        Agents have no use for it: they send the header.
        """
        port = _served_port(request)
        found = await asyncio.to_thread(reach, state.bridge, port)
        url, expires_at = sign_in_link(state.token, found.address) if found.outcome == "ok" and found.address else (None, None)
        # Not through guarded(): a tailnet name usually derives from the computer name, and
        # replacing it with <host> would hand the person a link that goes nowhere. This one answer
        # is the machine's address, for the person already signed in to the machine.
        return JSONResponse(
            {
                "outcome": found.outcome,
                "installed": found.installed,
                "port": port,
                "address": found.address,
                "via": found.via,
                "detail": found.detail,
                "url": url,
                "expires_at": expires_at,
                "ttl_seconds": int(LINK_TTL),
                "qr": qr_svg(url) if url else None,
            }
        )

    @app.delete("/api/session", tags=["session"])
    def close_session() -> Response:
        response = JSONResponse({"ok": True})
        clear_session_cookie(response)
        return response

    @app.post("/api/quit", tags=["server"], status_code=202)
    def quit_server(request: Request) -> Response:
        """Stop the tool on this machine. It answers first and exits a moment later, gracefully.

        Two conditions, and the second is the interesting one. The caller must be on this machine,
        like the launcher's door. And it must carry the access token in an ``Authorization``
        header: the session cookie a browser holds — on this machine, on a phone across a private
        network — opens the dashboard and is refused here. Reading the machine's record from a
        phone is one thing; switching the machine's tool off from one is another, and only
        something holding the token, which is to say this machine's launcher or an agent running
        on it, may do it.

        This is how a downloaded newer copy replaces a running older one: ask, wait for the port,
        then take its place.
        """
        if not matches(state.token, bearer(request)):
            return JSONResponse(
                {"error": "unauthorized", "detail": "quitting needs the access token in an Authorization header; the dashboard's session cookie cannot stop the tool"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not state.is_local(request):
            return JSONResponse({"error": "unauthorized", "detail": "quitting is for this machine"}, status_code=401)
        if state.on_quit is None:
            return JSONResponse(
                {"error": "unsupported", "detail": "this server cannot stop itself; it was started in a way that owns its own lifetime (serve --reload). Stop it where it was started."},
                status_code=409,
            )
        # The task runs once the answer is on the wire, so the caller learns the tool is going
        # rather than losing the connection and having to guess whether it heard.
        return JSONResponse({"quitting": True}, status_code=202, background=BackgroundTask(state.on_quit))

    @app.get("/api/readings", tags=["readings"])
    def catalog() -> dict[str, Any]:
        """Every reading: name, description, classes, parameters, what redaction removes."""
        return {"readings": [spec.to_dict() for spec in REGISTRY.values()], "version": __version__}

    @app.get("/api/performance/collection", tags=["performance"])
    def performance_collection() -> Response:
        """Collection is local, on by default, and its last attempt can be inspected without taking a new host reading."""
        return guarded({"settings": state.performance_store.settings(), "last_attempt": state.performance_store.status(), "retention_days": KEEP_DAYS})

    @app.put("/api/performance/collection", tags=["performance"])
    def configure_performance(change: PerformanceCollectionChange) -> Response:
        """Stop or resume background sampling, and choose its cost/precision cadence."""
        try:
            settings = state.performance_store.configure(change.enabled, change.interval_seconds)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, TimeoutError) as exc:
            raise HTTPException(status_code=503, detail="Local performance settings are unavailable") from exc
        return guarded({"settings": settings, "last_attempt": state.performance_store.status(), "retention_days": KEEP_DAYS})

    @app.delete("/api/performance/history", tags=["performance"])
    def clear_performance() -> Response:
        """Clear locally kept numeric samples. Future samples resume if collection remains enabled."""
        try:
            cleared = state.performance_store.clear()
        except (OSError, TimeoutError) as exc:
            raise HTTPException(status_code=503, detail="Local performance history is unavailable") from exc
        return guarded({"cleared_files": cleared, "settings": state.performance_store.settings()})

    @app.get("/api/readings/{name}", tags=["readings"])
    async def reading(name: str, request: Request, unredacted: bool = False) -> Response:
        """Take a reading. Parameters are query parameters; see the catalog for each reading's."""
        if name not in REGISTRY:
            raise HTTPException(status_code=404, detail=f"no reading named {name!r}")
        params = {k: v for k, v in request.query_params.items() if k != "unredacted"}
        try:
            result = await state.readings.take(name, params)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return envelope(result, unredacted)

    @app.get("/api/stream", tags=["stream"])
    async def stream(request: Request, unredacted: bool = False) -> Response:
        """The log as it happens: ``record`` for each new record matching the presets, ``heartbeat``
        every poll, ``bridge`` when a poll did not observe the machine. Server-sent events."""
        source = Stream(state.bridge, None if unredacted else lambda: state.redactor)
        return StreamingResponse(source.events(request.is_disconnected), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.get("/api/stack", tags=["stack"])
    def stack_state(unredacted: bool = False) -> Response:
        """The evidence chosen for handoff, in the order it was added, with the prompt it leads with."""
        return guarded(state.stack.state(), unredacted)

    @app.patch("/api/stack", tags=["stack"])
    def stack_choose(choice: StackChoice, unredacted: bool = False) -> Response:
        """Change which prompt leads the handoff, or whether one does at all."""
        changed = state.stack.choose(prompt_id=choice.prompt_id, system_prompt=choice.system_prompt, set_prompt="prompt_id" in choice.model_fields_set)
        handoff_changed_from_route()
        return guarded(changed, unredacted)

    @app.delete("/api/stack", tags=["stack"])
    def stack_clear() -> Response:
        state.stack.clear()
        handoff_changed_from_route()
        return guarded(state.stack.state())

    @app.post("/api/stack/items", tags=["stack"], status_code=201)
    async def stack_add(item: NewStackItem, unredacted: bool = False) -> Response:
        """Add evidence: a reading the server takes now, a reading the caller holds, some of its
        records, or a note. The same reading with the same parameters and records is refused."""
        try:
            added = await new_item(state.stack, state.bridge, item.model_dump(exclude_unset=True), reader=state.readings.take)
            response = guarded(state.stack.add(added).to_dict(), unredacted, status_code=201)
            await handoff_changed()
            return response
        except Duplicate as exc:
            return JSONResponse({"error": "duplicate", "detail": "this evidence is already on the stack", "id": str(exc)}, status_code=409)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch("/api/stack/items/{item_id}", tags=["stack"])
    def stack_change(item_id: str, change: ItemChange, unredacted: bool = False) -> Response:
        try:
            response = guarded(state.stack.update(item_id, rank=change.rank, verbosity=change.verbosity, title=change.title), unredacted)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"no item {item_id!r}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        handoff_changed_from_route()
        return response

    @app.delete("/api/stack/items/{item_id}", tags=["stack"])
    def stack_remove(item_id: str) -> Response:
        try:
            state.stack.remove(item_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"no item {item_id!r}") from exc
        handoff_changed_from_route()
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
            changed = state.prompts.update(prompt_id, **change.model_dump(exclude_unset=True))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"no prompt {prompt_id!r}") from exc
        handoff_changed_from_route()
        return changed

    @app.delete("/api/prompts/{prompt_id}", tags=["stack"])
    def prompts_remove(prompt_id: str) -> dict[str, Any]:
        try:
            state.prompts.remove(prompt_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"no prompt {prompt_id!r}") from exc
        handoff_changed_from_route()
        return {"prompts": state.prompts.all()}

    @app.post("/api/captures", tags=["captures"])
    async def captures_create(unredacted: bool = False) -> Response:
        """Take readings that need no exact selection, write the ZIP into the data directory and return it. Takes as
        long as the slowest query on this machine; nothing is sent anywhere."""
        made = await capture.create(state.bridge, state.stack, state.prompts, None if unredacted else state.redactor, reader=state.readings.take)
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
