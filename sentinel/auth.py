"""The authentication boundary.

One access token, created on first start and kept in the data directory with
owner-only permissions. Every route under ``/api/`` and the MCP endpoint require
it, as ``Authorization: Bearer <token>`` (agents) or as the session cookie the
dashboard sets once through ``POST /api/session`` (people, on any device).
Nothing else is authenticated because nothing else carries machine data.
"""

from __future__ import annotations

import hmac
import os
import secrets
import stat
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .paths import data_dir

COOKIE = "sentinel_session"
TOKEN_FILE = "token"
PROTECTED_PREFIXES = ("/api/", "/mcp")
OPEN_PATHS = ("/api/session",)


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
