"""A sign-in link for another device: what the machine publishes, the code it carries, and the QR of it."""

import json
import time
from urllib.parse import urlsplit

import pytest
import segno
from fastapi.testclient import TestClient

from sentinel import auth
from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.link import qr_svg
from tests.conftest import FakeBridge, identity_result

TOKEN = "test-token-0123456789"
LINK = "/api/session/link"
HOST = "sentinel.example.ts.net"

# What `tailscale serve status --json` printed on this machine, with the tailnet name replaced.
SERVE_JSON = json.dumps({"TCP": {"443": {"HTTPS": True}}, "Web": {f"{HOST}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8000"}}}}})


def _client(serve: BridgeResult, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The app with Tailscale answering as given, reached as this machine on the port it serves.

    The base URL carries the port because the route asks the connection which port it arrived on:
    what Tailscale has to publish is the listener a proxy on this machine dials, not a setting.
    """
    monkeypatch.setattr(State, "is_local", lambda self, request: True)
    bridge = FakeBridge(
        result=BridgeResult("ok", items=[{"Id": 41}], took_ms=1),
        by_marker={"$env:COMPUTERNAME": identity_result("TESTBOX", "tester"), "tailscale": serve},
    )
    return TestClient(create_app(State(bridge=bridge, token=TOKEN)), base_url="http://testserver:8000")


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch):
    with _client(BridgeResult("ok", items=[{"installed": True, "serve": SERVE_JSON}], took_ms=40), monkeypatch) as c:
        c.post("/api/session", json={"token": TOKEN})  # the person is signed in here, as the dashboard is
        yield c


def test_the_link_is_behind_the_boundary_like_every_other_route(monkeypatch: pytest.MonkeyPatch):
    with _client(BridgeResult("ok", items=[{"installed": False}]), monkeypatch) as c:
        assert c.post(LINK).status_code == 401
        assert c.post(LINK).json() == {"error": "unauthorized"}
        assert c.post(LINK, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_a_published_machine_answers_with_a_link_a_qr_code_and_no_token(published: TestClient):
    body = published.post(LINK).json()
    assert body["outcome"] == "ok"
    assert body["address"] == f"https://{HOST}"
    assert body["via"] == "Tailscale"
    assert body["url"].startswith(f"https://{HOST}/api/session/open?code=")
    assert body["qr"].startswith("<svg")
    assert body["expires_at"] and body["ttl_seconds"] == 300
    assert TOKEN not in published.post(LINK).text


def test_the_link_signs_another_device_in_once_and_lands_on_the_sign_in_link(published: TestClient):
    url = published.post(LINK).json()["url"]
    parts = urlsplit(url)
    door = f"{parts.path}?{parts.query}&to=link"

    published.cookies.clear()
    opened = published.get(door, follow_redirects=False)
    assert opened.status_code == 303 and opened.headers["location"] == "/#link"
    assert auth.COOKIE in opened.cookies
    assert published.get("/api/readings").status_code == 200

    published.cookies.clear()
    assert published.get(door, follow_redirects=False).status_code == 401
    assert published.get("/api/readings").status_code == 401


def test_tailscale_missing_and_publishing_nothing_are_both_empty_and_told_apart(monkeypatch: pytest.MonkeyPatch):
    with _client(BridgeResult("ok", items=[{"installed": False}]), monkeypatch) as c:
        body = c.post(LINK, headers={"Authorization": f"Bearer {TOKEN}"}).json()
        assert body["outcome"] == "empty" and body["installed"] is False
        assert body["url"] is None and body["qr"] is None and body["address"] is None
        assert body["detail"] == "Tailscale is not installed on this machine"
        assert body["port"] == 8000

    with _client(BridgeResult("ok", items=[{"installed": True, "serve": "No serve config\n"}]), monkeypatch) as c:
        body = c.post(LINK, headers={"Authorization": f"Bearer {TOKEN}"}).json()
        assert body["outcome"] == "empty" and body["installed"] is True
        assert body["url"] is None and body["qr"] is None
        assert "tailscale serve --bg 8000" in body["detail"]


def test_a_bridge_that_could_not_ask_is_not_a_no(monkeypatch: pytest.MonkeyPatch):
    with _client(BridgeResult("failed", error="powershell.exe fell over"), monkeypatch) as c:
        body = c.post(LINK, headers={"Authorization": f"Bearer {TOKEN}"}).json()
        assert body["outcome"] == "failed" and body["detail"] == "powershell.exe fell over"
        assert body["installed"] is None


def test_a_code_for_another_device_lasts_five_minutes_and_says_so(monkeypatch: pytest.MonkeyPatch):
    now = time.time()
    code = auth.mint_code(TOKEN, now=now, ttl=auth.LINK_TTL)
    assert auth.code_expiry(code) == int(now + 300)
    assert auth.code_valid(TOKEN, code, now + 290)
    assert not auth.code_valid(TOKEN, code, now + 310)
    assert auth.code_expiry("nonsense") == 0.0
    assert auth.code_expiry("") == 0.0


def test_a_spent_code_is_remembered_until_its_own_expiry(monkeypatch: pytest.MonkeyPatch):
    """The launcher's code lasts a minute and a link's five, so a fixed memory would let one back in."""
    state = State(bridge=FakeBridge(), token=TOKEN)
    code = auth.mint_code(TOKEN, ttl=auth.LINK_TTL)
    assert state.spend_code(code) is True

    later = time.time() + 90
    monkeypatch.setattr(time, "time", lambda: later)
    assert state.spend_code(code) is False
    assert code in state.spent_codes

    monkeypatch.setattr(time, "time", lambda: later + auth.LINK_TTL)
    assert state.spend_code(code) is False
    assert code not in state.spent_codes


def test_the_qr_code_is_one_path_on_a_grid_with_its_quiet_zone():
    text = f"https://{HOST}/api/session/open?code={auth.mint_code(TOKEN, ttl=auth.LINK_TTL)}"
    svg = qr_svg(text)
    modules = segno.make(text, error="m").symbol_size(scale=1, border=0)[0]
    assert svg.count("<svg") == 1 and svg.count("<path") == 1
    assert f'viewBox="0 0 {modules + 8} {modules + 8}"' in svg
    assert 'fill="currentColor"' in svg and svg.endswith("</svg>")
