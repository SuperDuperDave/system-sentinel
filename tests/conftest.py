"""Fixtures: a fake powershell.exe for the bridge, a fake bridge for everything above it, and a private data directory."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

import pytest

import sentinel.bridge
from sentinel.bridge import Bridge, BridgeResult

FAKE_POWERSHELL = r'''#!/usr/bin/env python3
"""A powershell.exe that answers by the directive it finds in the script: `# fake: <mode>`.

Both of the bridge's transports arrive here: a one-shot launch carrying a small `-EncodedCommand`
bootstrap and the payload on stdin, and a live session fed framed questions on stdin. One table of modes
answers both, so a mode written once is exercised through both and neither can drift from the other.
"""
import base64, json, os, re, sys, time

STATE = {"count": 0}

CLIXML = '#< CLIXML\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04"><S S="Error">Get-WinEvent : No events were found that match the specified selection criteria._x000D__x000A_</S><S S="Error">At line:1 char:1_x000D__x000A_</S></Objs>'
INTEROP = "<3>WSL (530414 - ) ERROR: UtilAcceptVsock:271: accept4 failed 110"
BIND_ERROR = "<3>WSL (530414 - ) ERROR: UtilBindVsockAnyPort:307: socket failed 1"


def answer(script):
    """What the machine says to this script: (stdout, stderr, exit code). A mode that raises in a
    launch exits non-zero; in a session the frame's catch reports exactly the same two things."""
    if not script.strip():  # the real startup probe asks an empty question
        return "", "", 0
    m = re.search(r"# fake: ([a-z0-9-]+)", script)
    mode = m.group(1) if m else "ok-list"
    if mode == "ok-list":
        return json.dumps([{"Id": 41, "ProviderName": "Microsoft-Windows-Kernel-Power"}, {"Id": 6008, "ProviderName": "EventLog"}]), "", 0
    if mode == "ok-object":
        return json.dumps({"CPU": "x"}), "", 0
    if mode == "echo":
        return json.dumps({"text": re.search(r"# echo: (.*)", script).group(1)}), "", 0
    if mode == "warn":
        return json.dumps([{"Id": 1}]), "Get-CimInstance : Invalid class", 0
    if mode == "failed":
        return "", 'Get-WinEvent : There is not an event log on the localhost computer that matches "Nope".', 1
    if mode == "denied":
        return "", "Get-WinEvent : Attempted to perform an unauthorized operation.", 1
    if mode == "denied-quiet":
        return "", "Access is denied.", 0
    if mode == "delayed-stderr":
        return "", "Get-WinEvent : The event log is unavailable.", 1
    if mode == "missing-stderr-mark":
        return "", "", 0
    if mode == "clixml":
        return "", CLIXML, 1
    if mode == "notjson":
        return "hello", "", 0
    if mode == "wsl-interop":
        return "", INTEROP, 1
    if mode == "wsl-bind-error":
        return "", BIND_ERROR, 1
    if mode == "wsl-interop-once":
        flag = os.path.join(os.path.dirname(sys.argv[0]), "interop-flag")
        if not os.path.exists(flag):
            open(flag, "w").close()
            return "", INTEROP, 1
        return json.dumps([{"Id": 7}]), "", 0
    if mode == "count":
        # How many times this process has been asked: one per launch, one more per question in a session.
        STATE["count"] += 1
        return json.dumps([{"n": STATE["count"]}]), "", 0
    if mode == "mark-lookalike":
        # A line shaped exactly like the session's own frame marker, before the real answer.
        return "d" * 32 + "\t0\n" + json.dumps([{"Id": 1}]), "", 0
    if mode == "sleep":
        time.sleep(5)
    return "", "", 0


def one_shot(args):
    script = base64.b64decode(args[args.index("-EncodedCommand") + 1]).decode("utf-16le")
    if "[Console]::In.ReadToEnd()" in script:
        script = base64.b64decode(sys.stdin.buffer.read()).decode("utf-16le")
    out, err, code = answer(script)
    sys.stdout.write(out)
    if err:
        sys.stderr.write(err + "\n")
    sys.exit(code)


def session():
    """Read framed questions on stdin and answer each one, then write its mark back."""
    no_session = os.environ.get("SENTINEL_FAKE_NO_SESSION")
    if no_session == "interop":
        sys.stderr.write(INTEROP + " PRIVATE_CANARY_42\n")
        sys.stderr.flush()
        sys.exit(1)
    if no_session and no_session != "prelude-note":  # a machine where a session will not start
        sys.exit(1)
    frame = re.compile(r"FromBase64String\('([A-Za-z0-9+/=]*)'\).*WriteLine\(\"([0-9a-f]+)\"\)")
    for line in sys.stdin:
        found = frame.search(line)
        if found is None:
            if no_session == "prelude-note":
                sys.stderr.write("startup note PRIVATE_CANARY_42\n")
                sys.stderr.flush()
            continue  # the prelude, or anything else that is not a question
        script = base64.b64decode(found.group(1)).decode("utf-16le")
        out, err, code = answer(script)
        mode = re.search(r"# fake: ([a-z0-9-]+)", script)
        delayed = mode is not None and mode.group(1) == "delayed-stderr"
        missing = mode is not None and mode.group(1) == "missing-stderr-mark"
        if not delayed:
            if err:
                sys.stderr.write(err + "\n")
            if not missing:
                sys.stderr.write(found.group(2) + "\n")  # the frame closes stderr with the mark first
            sys.stderr.flush()
        if out:
            sys.stdout.write(out + "\n")
        sys.stdout.write(found.group(2) + "\t" + str(code) + "\n")
        sys.stdout.flush()
        if delayed:
            time.sleep(0.1)  # slower than the old 50 ms grace after stdout's mark
            sys.stderr.write(err + "\n" + found.group(2) + "\n")
            sys.stderr.flush()


args = sys.argv[1:]
if "-EncodedCommand" in args:
    one_shot(args)
else:
    session()
sys.exit(0)
'''


@pytest.fixture(autouse=True)
def private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def no_session_outlives_a_test():
    """A live session is a process: no test may leave one running, and none may inherit one."""
    sentinel.bridge.reset_sessions()
    yield
    sentinel.bridge.shutdown_sessions()


@pytest.fixture
def fake_powershell(tmp_path: Path) -> Path:
    exe = tmp_path / "powershell.exe"
    exe.write_text(FAKE_POWERSHELL, encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


@pytest.fixture(params=("one-shot", "session"))
def bridge(request: pytest.FixtureRequest, fake_powershell: Path, monkeypatch: pytest.MonkeyPatch) -> Bridge:
    """The bridge, through each of its two transports in turn.

    Every outcome is the same outcome whichever way the question was asked, so every test that
    takes this fixture states that twice: once against a process per question, once against a
    live session. A test that is about one transport takes ``one_shot_bridge`` or ``session_bridge``.
    """
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0 if request.param == "one-shot" else 4)
    return Bridge(exe=str(fake_powershell), cwd=None)


@pytest.fixture
def one_shot_bridge(fake_powershell: Path, monkeypatch: pytest.MonkeyPatch) -> Bridge:
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    return Bridge(exe=str(fake_powershell), cwd=None)


@pytest.fixture
def session_bridge(fake_powershell: Path, monkeypatch: pytest.MonkeyPatch) -> Bridge:
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 4)
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


def log_collector_marker(log: str) -> str:
    """Select only the single-object log collector, not another reading's metadata probe."""
    return f"$meta = Read-LogMetadata '{log}'"


def log_collector_result(
    records: list[dict[str, Any]], *, log: str = "System", oldest: str = "2026-01-01T00:00:00.0000000Z",
    window_start: str | None = "2026-09-20T00:00:00.000Z", window_end: str | None = None,
    queried_at: str = "2026-09-21T00:00:00.000Z", limit: int | None = None, took_ms: int = 5,
) -> BridgeResult:
    """The object Windows PowerShell returns even when the matching query is empty."""
    meta = {
        "log": log, "log_enabled": True, "log_mode": "Circular", "log_state": "ok", "log_error": None,
        "log_oldest": oldest, "oldest_state": "ok", "oldest_error": None,
    }
    cap = limit if limit is not None else max(1, len(records))
    kept = records[:cap]
    return BridgeResult("ok", items=[{
        "log": log, "outcome": "ok" if kept else "empty", "error": None, "records": kept,
        "returned": len(kept), "limit": cap, "truncated": len(records) > cap, "stopped": None,
        "metadata": meta, "window_start": window_start, "window_end": window_end, "queried_at": queried_at,
    }], took_ms=took_ms)


class LogBridge(FakeBridge):
    """Broad integration fake: adapt its canned rows to the log collector's Windows object.

    Focused source tests use ``FakeBridge`` with an explicit object, so malformed collector
    shapes still have an independent failure test rather than being repaired by this adapter.
    """

    def run(self, script: str, *, timeout: float = 60, depth: int = 6) -> BridgeResult:
        result = super().run(script, timeout=timeout, depth=depth)
        if "$found = [System.Collections.Generic.List[object]]::new()" not in script or "records = @($records)" not in script:
            return result
        if result.outcome not in ("ok", "empty"):
            return result
        if result.items and isinstance(result.items[0], dict) and "records" in result.items[0]:
            return result
        log = "Application" if log_collector_marker("Application") in script else "System"
        cap = re.search(r"Get-WinEvent -FilterXml \(\[xml\]\$xml\) -MaxEvents (\d+)", script)
        if cap is None:
            cap = re.search(r"Select-Object -First (\d+) \|\s*& \{ process \{ \[void\]\$found.Add", script)
        limit = int(cap.group(1)) - 1 if cap else max(1, len(result.items))
        return log_collector_result(result.items, log=log, limit=limit, took_ms=result.took_ms)


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
