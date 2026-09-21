"""The authentication boundary.

One access token, created on first start and kept in the data directory with
owner-only permissions. Every route under ``/api/`` and the MCP endpoint require
it, as ``Authorization: Bearer <token>`` (agents) or as the session cookie the
dashboard sets once through ``POST /api/session`` (people, on any device).
Nothing else is authenticated because nothing else carries machine data.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
import time
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from .paths import data_dir

COOKIE = "sentinel_session"
TOKEN_FILE = "token"
PROTECTED_PREFIXES = ("/api/", "/mcp")
OPEN_PATHS = ("/api/session", "/api/session/open")
CODE_TTL = 60.0
#: A code carried to another device has to survive being read off a screen and scanned.
LINK_TTL = 300.0


def token_path() -> Path:
    return data_dir() / TOKEN_FILE


def load_or_create_token() -> str:
    path = token_path()
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(32)
    path.write_text(value + "\n", encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return value


def bearer(request: Request) -> str | None:
    """The token presented in an ``Authorization`` header, or None when none was.

    A request carrying only the session cookie presents no token: the cookie holds a value derived
    from it, which opens the dashboard and nothing else. Keeping the two apart is what lets one
    route — quitting the machine's tool — ask for the token itself and refuse a browser.
    """
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    return header[7:].strip() or None


def matches(expected: str, presented: str | None) -> bool:
    return presented is not None and hmac.compare_digest(expected.encode(), presented.encode())


def mint_code(token: str, now: float | None = None, ttl: float = CODE_TTL) -> str:
    """A one-time code the launcher spends at ``GET /api/session/open``, so the person never sees
    the token.

    Signed with the token rather than stored, so any process that can read the token — the launcher
    starting the server, or a second double-click finding it already running — can mint one, and the
    server needs no shared state to trust it. Whether a code has been spent is the serving process's
    to remember; this side only says what a valid, unexpired code looks like.

    The code carries its own expiry, so how long one lasts is the minting side's choice: a moment for
    the browser this machine is about to open (:data:`CODE_TTL`), longer for one crossing to another
    device by hand or by camera (:data:`LINK_TTL`).
    """
    body = f"{int((time.time() if now is None else now) + ttl)}.{secrets.token_urlsafe(12)}"
    return f"{body}.{_signature(token, body)}"


def code_valid(token: str, code: str, now: float | None = None) -> bool:
    """Whether this code was minted from this token and has not run out. Not whether it was spent."""
    body, _, signature = code.rpartition(".")
    expires, _, nonce = body.partition(".")
    if not nonce or not expires.isdigit():
        return False
    if int(expires) < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(_signature(token, body), signature)


def code_expiry(code: str) -> float:
    """When this code runs out, as a Unix time. Anything that is not a code at all runs out at 0.0.

    Unsigned on purpose: the caller has already decided whether to trust the code, and a spent one
    only has to be remembered for as long as it could still be worth spending.
    """
    body, _, _ = code.rpartition(".")
    expires, _, nonce = body.partition(".")
    return float(expires) if nonce and expires.isdigit() else 0.0


def _signature(token: str, body: str) -> str:
    return hmac.new(token.encode(), body.encode(), hashlib.sha256).hexdigest()


class TokenMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, token: str) -> None:
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path.startswith(PROTECTED_PREFIXES) and path not in OPEN_PATHS:
            if not authorized(self.token, request):
                return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        return await call_next(request)


def session_value(token: str) -> str:
    """What the dashboard's cookie holds: a value derived from the token, never the token.

    A browser, or a phone through whatever transport fronts the machine, then holds something
    that opens the dashboard and nothing else; the token stays where agents read it on purpose."""
    return _signature(token, "session")


def authorized(token: str, request: Request) -> bool:
    """A bearer header carrying the token, or the session cookie carrying the value derived from it."""
    if request.headers.get("authorization", "").lower().startswith("bearer "):
        return matches(token, bearer(request))
    return matches(session_value(token), request.cookies.get(COOKIE))


def session_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(COOKIE, session_value(token), httponly=True, samesite="lax", secure=secure, max_age=60 * 60 * 24 * 365, path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")
