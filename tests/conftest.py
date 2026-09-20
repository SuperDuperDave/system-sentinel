"""Fixtures: a fake powershell.exe for the bridge, a fake bridge for everything above it, and a private data directory."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from sentinel.bridge import Bridge, BridgeResult

FAKE_POWERSHELL = r'''#!/usr/bin/env python3
"""A powershell.exe that answers by the directive it finds in the decoded script: `# fake: <mode>`."""
import base64, json, re, sys, time
args = sys.argv[1:]
encoded = args[args.index("-EncodedCommand") + 1]
script = base64.b64decode(encoded).decode("utf-16le")
m = re.search(r"# fake: ([a-z0-9-]+)", script)
mode = m.group(1) if m else "ok-list"
if mode == "ok-list":
    sys.stdout.write(json.dumps([{"Id": 41, "ProviderName": "Microsoft-Windows-Kernel-Power"}, {"Id": 6008, "ProviderName": "EventLog"}]))
elif mode == "ok-object":
    sys.stdout.write(json.dumps({"CPU": "x"}))
elif mode == "empty":
    pass
elif mode == "warn":
    sys.stdout.write(json.dumps([{"Id": 1}]))
    sys.stderr.write("Get-CimInstance : Invalid class\n")
elif mode == "failed":
    sys.stderr.write("Get-WinEvent : There is not an event log on the localhost computer that matches \"Nope\".\n")
    sys.exit(1)
elif mode == "denied":
    sys.stderr.write("Get-WinEvent : Attempted to perform an unauthorized operation.\n")
    sys.exit(1)
elif mode == "denied-quiet":
    sys.stderr.write("Access is denied.\n")
elif mode == "clixml":
    sys.stderr.write('#< CLIXML\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04"><S S="Error">Get-WinEvent : No events were found that match the specified selection criteria._x000D__x000A_</S><S S="Error">At line:1 char:1_x000D__x000A_</S></Objs>')
    sys.exit(1)
elif mode == "notjson":
    sys.stdout.write("hello")
elif mode == "wsl-interop":
    sys.stderr.write("<3>WSL (530414 - ) ERROR: UtilAcceptVsock:271: accept4 failed 110\n")
    sys.exit(1)
elif mode == "wsl-interop-once":
    import os
    flag = os.path.join(os.path.dirname(sys.argv[0]), "interop-flag")
    if not os.path.exists(flag):
        open(flag, "w").close()
        sys.stderr.write("<3>WSL (530414 - ) ERROR: UtilAcceptVsock:271: accept4 failed 110\n")
        sys.exit(1)
    sys.stdout.write(json.dumps([{"Id": 7}]))
elif mode == "sleep":
    time.sleep(5)
sys.exit(0)
'''


@pytest.fixture(autouse=True)
def private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(home))
    return home


@pytest.fixture
def fake_powershell(tmp_path: Path) -> Path:
    exe = tmp_path / "powershell.exe"
    exe.write_text(FAKE_POWERSHELL, encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


@pytest.fixture
def bridge(fake_powershell: Path) -> Bridge:
    return Bridge(exe=str(fake_powershell), cwd=None)


class FakeBridge:
    """A bridge above the process boundary: answers with a canned result and records what it was asked."""

    def __init__(self, result: BridgeResult | None = None, by_marker: dict[str, BridgeResult] | None = None):
        self.result = result or BridgeResult("ok", items=[{"Id": 41, "ProviderName": "Microsoft-Windows-Kernel-Power", "Message": "The system has rebooted without cleanly shutting down first."}], took_ms=7)
        self.by_marker = by_marker or {}
        self.scripts: list[str] = []
        self.exe = "fake"
        self.available = True

    def run(self, script: str, *, timeout: float = 60, depth: int = 6) -> BridgeResult:
        self.scripts.append(script)
        for marker, result in self.by_marker.items():
            if marker in script:
                return result
        return self.result


@pytest.fixture
def fake_bridge() -> FakeBridge:
    return FakeBridge()


def identity_result(host: str = "TESTBOX", user: str = "tester") -> BridgeResult:
    return BridgeResult("ok", items=[{"host": host, "user": user, "ps": "5.1", "os": "10.0"}], took_ms=3)


def real_bridge_or_skip() -> Bridge:
    b = Bridge.locate()
    if not b.available or os.environ.get("SENTINEL_SKIP_HOST"):
        pytest.skip("no powershell.exe on this machine")
    return b


def fake_any(value: Any) -> Any:
    return value


@pytest.fixture(autouse=True)
def one_host_suite_at_a_time(request: pytest.FixtureRequest):
    """Host tests take the bridge one process at a time.

    WSL's interop layer failed repeatedly on 2026-09-20 when several pytest processes launched
    ``powershell.exe`` at once, and never when one suite ran alone (friction F2). A lock file
    outside every test's private data directory serialises host tests across processes, so the
    rule "run host tests serially" is a mechanism rather than something to remember. Unit tests
    and platforms without ``fcntl`` are untouched.
    """
    if request.node.get_closest_marker("host") is None:
        yield
        return
    try:
        import fcntl
    except ImportError:  # Windows: one process at a time is the norm there
        yield
        return
    lock_path = Path(os.environ.get("TMPDIR", "/tmp")) / "system-sentinel-host-tests.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
