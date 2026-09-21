"""The launcher's door: a one-time code signs the browser in once, from this machine only."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from sentinel import auth, launcher
from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from tests.conftest import FakeBridge, identity_result

TOKEN = "test-token-0123456789"
OPEN = "/api/session/open"


def _state() -> State:
    bridge = FakeBridge(
        result=BridgeResult("ok", items=[{"Id": 41}], took_ms=1),
        by_marker={"$env:COMPUTERNAME": identity_result("TESTBOX", "tester")},
    )
    return State(bridge=bridge, token=TOKEN)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    """A client the app treats as local. TestClient's client host is ``testclient``, so the
    locality check is the seam a test moves, not the route's meaning."""
    monkeypatch.setattr(State, "is_local", lambda self, request: True)
    with TestClient(create_app(_state())) as c:
        yield c


@pytest.fixture
def elsewhere():
    """The same app with the real locality check: every request arrives from somewhere else."""
    with TestClient(create_app(_state())) as c:
        yield c


def test_the_route_is_open_at_the_middleware():
    assert OPEN in auth.OPEN_PATHS
    assert OPEN.startswith(auth.PROTECTED_PREFIXES)


def test_a_fresh_code_signs_the_browser_in_once(client: TestClient):
    code = auth.mint_code(TOKEN)
    r = client.get(f"{OPEN}?code={code}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert auth.COOKIE in r.cookies
    assert client.get("/api/readings").status_code == 200  # the cookie now carries the session

    client.cookies.clear()
    spent = client.get(f"{OPEN}?code={code}", follow_redirects=False)
    assert spent.status_code == 401 and spent.json() == {"error": "unauthorized"}
    assert client.get("/api/readings").status_code == 401


def test_an_unknown_missing_or_expired_code_is_refused(client: TestClient):
    for code in ("", "nonsense", auth.mint_code("another-machines-token"), auth.mint_code(TOKEN, now=time.time() - 2 * auth.CODE_TTL)):
        r = client.get(f"{OPEN}?code={code}", follow_redirects=False)
        assert r.status_code == 401, code
    assert client.get("/api/readings").status_code == 401


def test_a_caller_from_elsewhere_is_refused_before_the_code_is_spent(elsewhere: TestClient, monkeypatch: pytest.MonkeyPatch):
    code = auth.mint_code(TOKEN)
    assert elsewhere.get(f"{OPEN}?code={code}", follow_redirects=False).status_code == 401
    monkeypatch.setattr(State, "is_local", lambda self, request: True)
    assert elsewhere.get(f"{OPEN}?code={code}", follow_redirects=False).status_code == 303


def test_already_serving_knows_this_machines_server(client: TestClient):
    def get(url: str, headers: dict) -> int:
        return client.get(url, headers=headers).status_code

    assert launcher.already_serving("http://testserver", TOKEN, get) is True
    assert launcher.already_serving("http://testserver", "another-machines-token", get) is False


def test_nothing_listening_is_not_a_running_server():
    assert launcher.already_serving("http://127.0.0.1:1", TOKEN) is False


def _browser_asked() -> None:
    """The browser is asked on its own thread; a test waits for that thread before looking."""
    for thread in threading.enumerate():
        if thread.name == "sentinel-browser":
            thread.join(5)


def test_the_link_the_launcher_opens_carries_a_code_and_not_the_token(monkeypatch: pytest.MonkeyPatch):
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    url = launcher.open_dashboard("http://127.0.0.1:8000", TOKEN)
    _browser_asked()
    assert opened == [url]
    assert url.startswith(f"http://127.0.0.1:8000{OPEN}?code=")
    assert TOKEN not in url
    assert auth.code_valid(TOKEN, url.split("code=", 1)[1])


def test_the_tray_can_open_the_dashboard_on_the_sign_in_link_for_another_device(monkeypatch: pytest.MonkeyPatch):
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    url = launcher.open_dashboard("http://127.0.0.1:8000", TOKEN, to="link")
    _browser_asked()
    assert opened == [url]
    assert url.startswith(f"http://127.0.0.1:8000{OPEN}?code=") and url.endswith("&to=link")
    assert TOKEN not in url


def test_a_browser_that_never_answers_does_not_hold_the_launcher(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """On a machine with no handler for http, Windows shows a dialog and the call sits behind it
    until someone answers; the tray must not wait for that, and the log must not carry the code."""
    stuck = threading.Event()
    monkeypatch.setattr(launcher.webbrowser, "open", lambda _url: stuck.wait(10))
    started = time.monotonic()
    with caplog.at_level("INFO", logger="sentinel.launcher"):
        url = launcher.open_dashboard("http://127.0.0.1:8000", TOKEN)
    assert time.monotonic() - started < 1
    stuck.set()
    _browser_asked()
    assert url.split("code=", 1)[1].split("&")[0] not in caplog.text
    assert "one-time code" in caplog.text


def test_the_tray_has_five_entries_and_none_of_them_is_the_token():
    if launcher.pystray is None:
        pytest.skip("pystray is not installed here; the tray is checked on the Windows side")
    labels = [item.text for item in launcher.tray_menu(None, "http://127.0.0.1:8000", TOKEN).items if item.text]
    assert labels == ["Open dashboard", "Sign in another device…", "Copy address for agents", "Start with Windows", "Quit"]
    assert TOKEN not in " ".join(labels)


def test_start_with_windows_reads_its_own_file_and_never_raises():
    assert launcher.startup_enabled() is (launcher.startup_shortcut() is not None and launcher.startup_shortcut().exists())
    if launcher.startup_dir() is None:  # not Windows: the menu item is present and says so
        assert "Windows-only" in launcher.set_startup(True)
        assert launcher.startup_enabled() is False


def test_local_means_a_loopback_client_or_a_connection_accepted_on_loopback():
    """Tailscale serve dials the loopback listener from the machine's own address: local by construction."""
    from types import SimpleNamespace

    from sentinel.app import State
    from sentinel.bridge import Bridge

    state = State(bridge=Bridge(exe=None), token="t")

    def request(client_host, server_host):
        return SimpleNamespace(client=SimpleNamespace(host=client_host) if client_host else None, scope={"server": (server_host, 8000) if server_host else None})

    assert state.is_local(request("127.0.0.1", "127.0.0.1"))
    assert state.is_local(request("::1", "::1"))
    assert state.is_local(request("100.76.48.118", "127.0.0.1"))  # a proxy on this machine reaching the loopback listener
    assert state.is_local(request("127.0.0.1", "0.0.0.0"))  # a loopback client on a wide listener
    assert not state.is_local(request("100.81.173.116", "100.76.48.118"))  # a peer reaching a wide listener directly
    assert not state.is_local(request("testclient", "testserver"))
    assert not state.is_local(request(None, None))
