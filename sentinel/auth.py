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

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .paths import data_dir

COOKIE = "sentinel_session"
TOKEN_FILE = "token"
PROTECTED_PREFIXES = ("/api/", "/mcp")
OPEN_PATHS = ("/api/session", "/api/session/open")
CODE_TTL = 60.0


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


def presented_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(COOKIE)


def matches(expected: str, presented: str | None) -> bool:
    return presented is not None and hmac.compare_digest(expected.encode(), presented.encode())


def mint_code(token: str, now: float | None = None) -> str:
    """A one-time code the launcher spends at ``GET /api/session/open``, so the person never sees
    the token.

    Signed with the token rather than stored, so any process that can read the token — the launcher
    starting the server, or a second double-click finding it already running — can mint one, and the
    server needs no shared state to trust it. Whether a code has been spent is the serving process's
    to remember; this side only says what a valid, unexpired code looks like.
    """
    body = f"{int((time.time() if now is None else now) + CODE_TTL)}.{secrets.token_urlsafe(12)}"
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


def _signature(token: str, body: str) -> str:
    return hmac.new(token.encode(), body.encode(), hashlib.sha256).hexdigest()


class TokenMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path.startswith(PROTECTED_PREFIXES) and path not in OPEN_PATHS:
            if not matches(self.token, presented_token(request)):
                return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        return await call_next(request)


def session_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=secure, max_age=60 * 60 * 24 * 365, path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")
