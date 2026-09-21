"""The launcher: the door a one-time code opens, and the decision a start makes about itself."""

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentinel import __version__, auth, launcher
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


def _through(client: TestClient):
    """The launcher's one request seam, answered by the app itself."""

    def ask(url: str, headers: dict, method: str = "GET"):
        response = client.request(method, url, headers=headers)
        return response.status_code, response.text

    return ask


def test_already_serving_knows_this_machines_server_and_which_version_it_is(client: TestClient):
    ask = _through(client)
    assert launcher.serving_version("http://testserver", TOKEN, ask) == __version__
    assert launcher.already_serving("http://testserver", TOKEN, ask) is True
    assert launcher.serving_version("http://testserver", "another-machines-token", ask) is None
    assert launcher.already_serving("http://testserver", "another-machines-token", ask) is False


def test_nothing_listening_is_not_a_running_server():
    assert launcher.already_serving("http://127.0.0.1:1", TOKEN) is False
    assert launcher.serving_version("http://127.0.0.1:1", TOKEN) is None
    assert launcher.port_free(1) is True


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


def test_the_tray_says_what_it_does_and_never_the_token():
    """Four things done often, then the version — which is both the answer to *which one am I
    running* and where the two things done once live — then Quit."""
    if launcher.pystray is None:
        pytest.skip("pystray is not installed here; the tray is checked on the Windows side")
    menu = launcher.tray_menu(None, "http://127.0.0.1:8000", TOKEN)
    labels = [item.text for item in menu.items if item is not launcher.pystray.Menu.SEPARATOR]  # the separator has a label of its own
    assert labels == [
        "Open dashboard",
        "Sign in another device…",
        "Copy address for agents",
        "Start with Windows",
        f"System Sentinel {__version__}",
        "Quit",
    ]
    version_item = next(item for item in menu.items if item.text == f"System Sentinel {__version__}")
    assert [item.text for item in version_item.submenu.items] == ["Check for updates…", "Remove from this computer…"]
    assert TOKEN not in " ".join(labels)


def test_checking_for_updates_opens_the_releases_page_and_asks_nothing_itself(monkeypatch: pytest.MonkeyPatch):
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    assert launcher.open_releases() == launcher.RELEASES
    _browser_asked()
    assert opened == [launcher.RELEASES]
    assert launcher.RELEASES.startswith("https://github.com/")


def test_removing_names_what_goes_and_what_stays(private_home: Path):
    question = launcher.removal_question(private_home)
    for named in (str(private_home), "the program", "the access token", "every capture", "Start with Windows", "claude mcp remove system-sentinel"):
        assert named in question
    assert "the copy you downloaded" in question


PLACEMENTS = [
    # frozen, at home, serving, the installed copy's version, the installed copy is these bytes
    ("a development run is never an install", dict(frozen=False, at_home=False, serving=None, installed_version=None, same=False), launcher.SERVE),
    ("the installed copy with nothing serving serves", dict(frozen=True, at_home=True, serving=None, installed_version=None, same=False), launcher.SERVE),
    ("the installed copy with the tool already up opens it", dict(frozen=True, at_home=True, serving="1.0.1", installed_version=None, same=False), launcher.OPEN),
    ("a download with no installed copy installs", dict(frozen=True, at_home=False, serving=None, installed_version=None, same=False), launcher.INSTALL),
    ("a download already installed byte for byte just starts it", dict(frozen=True, at_home=False, serving=None, installed_version="1.0.1", same=True), launcher.START),
    ("a download over an older installed copy installs", dict(frozen=True, at_home=False, serving=None, installed_version="1.0.0", same=False), launcher.INSTALL),
    ("a download over an installed copy whose version cannot be read installs", dict(frozen=True, at_home=False, serving=None, installed_version=None, same=False), launcher.INSTALL),
    ("a download older than the installed copy starts that one instead", dict(frozen=True, at_home=False, serving=None, installed_version="1.1.0", same=False), launcher.START),
    ("a download while the same version serves opens the dashboard", dict(frozen=True, at_home=False, serving="1.0.1", installed_version=None, same=False), launcher.OPEN),
    ("a download while an older version serves takes over", dict(frozen=True, at_home=False, serving="1.0.0", installed_version="1.0.0", same=False), launcher.REPLACE),
    ("a download while a newer version serves does not downgrade", dict(frozen=True, at_home=False, serving="1.2.0", installed_version="1.2.0", same=False), launcher.NEWER),
]


@pytest.mark.parametrize("name, situation, expected", PLACEMENTS, ids=[case[0] for case in PLACEMENTS])
def test_the_placement_decision(name: str, situation: dict, expected: str):
    """Where a start goes is one function of what it can see, so every branch can be asked for
    without an executable, a port or a machine."""
    step = launcher.plan(mine="1.0.1", **situation)
    assert step.do == expected, f"{name}: {step}"
    assert step.why  # every branch can say why, because one of them says it to the person


def test_a_version_that_cannot_be_read_is_never_the_newer_one():
    assert launcher._parts("1.0.10") > launcher._parts("1.0.9")
    assert launcher._parts("1.0.1") > launcher._parts(None) < launcher._parts("0.0.1")
    assert launcher.file_version(Path(__file__)) is None  # not a Windows executable, and not Windows


def test_the_startup_shortcut_points_at_the_installed_copy(monkeypatch: pytest.MonkeyPatch):
    """Never at whichever file was double-clicked: a download can be moved or tidied away, and the
    copy in the data directory is the one every update replaces."""
    home = launcher.installed_exe()
    monkeypatch.setattr(launcher, "frozen", lambda: True)
    home.write_bytes(b"the installed copy")
    assert launcher.launch_command() == (str(home), "")
    home.unlink()
    assert launcher.launch_command()[0] != str(home)  # nothing installed: the interpreter, as in development


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
