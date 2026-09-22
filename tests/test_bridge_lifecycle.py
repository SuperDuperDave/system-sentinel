"""Portable bridge lifecycle races, without starting an executable or querying Windows."""

from contextlib import contextmanager, nullcontext
from io import BytesIO
from threading import Event, Thread

import pytest

import sentinel.bridge as bridge_module
from sentinel.bridge import Bridge, BridgeResult, Session


def test_a_session_finishing_start_after_shutdown_is_discarded_before_lending(monkeypatch):
    startup_entered = Event()
    finish_startup = Event()
    actions = []

    class ControlledSession:
        answered = 0
        discarded = None
        age = 0.0

        @property
        def alive(self):
            return self.discarded is None

        def ask(self, script, *, timeout, depth):
            actions.append(("session asked", script))
            self.answered += 1
            return BridgeResult("ok", items=[{"transport": "late session"}])

        def discard(self, why, *, grace=0.0):
            actions.append(("discarded", why))
            self.discarded = why

    session = ControlledSession()
    bridge = Bridge(exe="controlled-powershell", cwd=None)
    script = "the pending question"
    fallback = BridgeResult("ok", items=[{"answer": 42}], took_ms=5, returncode=0)

    def start(cls, located, *, timeout):
        assert located is bridge and timeout == 10
        startup_entered.set()
        assert finish_startup.wait(10), "the test did not release controlled startup"
        return session

    def launch(located, question, *, timeout, depth):
        assert located is bridge and question == script and timeout == 10 and depth == 4
        actions.append(("one-shot answered", question))
        return fallback

    monkeypatch.setattr(bridge_module, "POOL_SIZE", 1)
    monkeypatch.setattr(bridge_module, "_launch_slot", lambda timeout: nullcontext())
    monkeypatch.setattr(Session, "start", classmethod(start))
    monkeypatch.setattr(Bridge, "_run_once", launch)
    pool = bridge_module._pool_for(bridge)
    assert pool is not None
    results, errors = [], []

    def ask():
        try:
            results.append(bridge.run(script, timeout=10, depth=4))
        except BaseException as exc:
            errors.append(exc)

    worker = Thread(target=ask, daemon=True)
    worker.start()
    try:
        assert startup_entered.wait(10), "the pending request never began startup"
        assert pool._starting == 1 and pool._sessions == []
        pool.shutdown()
        assert pool._closed and pool._sessions == [] and pool._idle == []
        assert session.discarded is None  # startup still owns the session at this point
    finally:
        finish_startup.set()
        worker.join(10)
    assert not worker.is_alive(), "the pending request did not finish"
    assert errors == []
    assert actions == [("discarded", "shutdown"), ("one-shot answered", script)]
    assert results == [fallback]
    assert session.discarded == "shutdown" and session.answered == 0
    assert pool._closed and pool._starting == 0 and pool._sessions == [] and pool._idle == []
    stats = pool.stats()
    assert stats["alive"] == stats["idle"] == stats["answered"] == stats["start_failures"] == 0
    assert stats["fell_back"] == 1 and stats["discarded"] == {"shutdown": 1}
    assert stats["last_start_failure"] is None
    pool.shutdown()
    assert pool.stats() == stats  # the late child is retired exactly once


def test_shutdown_between_checkout_and_write_falls_back(monkeypatch):
    """A checked-out session can lose its input pipe before its question is sent."""
    class ControlledProcess:
        def __init__(self):
            self.stdin = BytesIO()
            self.stdout = None
            self.stderr = None
            self.pid = 1
            self.exited = False

        def poll(self):
            return 0 if self.exited else None

        def wait(self, timeout=None):
            self.exited = True
            return 0

        def kill(self):
            self.exited = True

    process = ControlledProcess()
    session = Session(process)
    bridge = Bridge(exe="controlled-powershell", cwd=None)
    fallback = BridgeResult("ok", items=[{"CPU": "x"}])
    monkeypatch.setattr(bridge_module, "POOL_SIZE", 1)
    monkeypatch.setattr(bridge_module, "_launch_slot", lambda timeout: nullcontext())
    monkeypatch.setattr(Bridge, "_run_once", lambda self, script, *, timeout, depth: fallback)
    pool = bridge_module._pool_for(bridge)
    assert pool is not None
    with pool._lock:
        pool._sessions.append(session)
        pool._idle.append(session)
    assert pool.stats()["idle"] == 1
    about_to_ask = Event()
    release = Event()
    real_ask = Session.ask
    results, errors = [], []

    def held_ask(self, script, *, timeout, depth):
        if script == "# fake: ok-object":
            about_to_ask.set()
            assert release.wait(10), "shutdown did not release the checked-out session"
        return real_ask(self, script, timeout=timeout, depth=depth)

    monkeypatch.setattr(Session, "ask", held_ask)

    def ask():
        try:
            results.append(bridge.run("# fake: ok-object"))
        except BaseException as exc:
            errors.append(exc)

    worker = Thread(target=ask, daemon=True)
    worker.start()
    try:
        assert about_to_ask.wait(10), "the question was not lent its session"
        pool.shutdown()
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive() and errors == [], errors
    assert results == [fallback]
    assert session.discarded == "shutdown" and process.exited and process.stdin.closed
    stats = pool.stats()
    assert stats["alive"] == stats["idle"] == 0
    assert stats["discarded"] == {"shutdown": 1} and stats["fell_back"] == 1


def test_final_shutdown_does_not_start_a_new_session(monkeypatch):
    bridge = Bridge(exe="controlled-powershell", cwd=None)
    launches = []

    def launch(self, script, *, timeout, depth):
        launches.append(script)
        raise AssertionError("a final shutdown must not launch one-shot PowerShell")

    def forbidden_start(cls, located, *, timeout):
        raise AssertionError("a final shutdown must not start a new session")

    monkeypatch.setattr(bridge_module, "POOL_SIZE", 1)
    monkeypatch.setattr(bridge_module, "_launch_slot", lambda timeout: nullcontext())
    monkeypatch.setattr(Bridge, "_run_once", launch)
    monkeypatch.setattr(Session, "start", classmethod(forbidden_start))
    bridge_module.shutdown_sessions()
    result = bridge.run("pending question")
    assert result.outcome == "unavailable" and result.error == "the bridge is shutting down"
    assert launches == []
    assert bridge_module._pool_for(bridge) is None
    assert bridge_module.sessions_report(bridge)["transport"] == "stopped"


def test_explicit_reset_reopens_a_terminal_session_pool(monkeypatch):
    bridge = Bridge(exe="controlled-powershell", cwd=None)
    monkeypatch.setattr(bridge_module, "POOL_SIZE", 1)
    bridge_module.shutdown_sessions()
    assert bridge_module._pool_for(bridge) is None
    bridge_module.reset_sessions(2)
    pool = bridge_module._pool_for(bridge)
    assert pool is not None and pool.size == bridge_module.POOL_SIZE == 2
    assert bridge_module.sessions_report(bridge)["transport"] == "session"


def test_shutdown_during_a_launch_slot_wait_refuses_the_launch(monkeypatch):
    bridge = Bridge(exe="controlled-powershell", cwd=None)
    waiting, release = Event(), Event()
    launches, results = [], []

    @contextmanager
    def held_slot(timeout):
        waiting.set()
        assert release.wait(10)
        yield

    def launch(self, script, *, timeout, depth):
        launches.append(script)
        raise AssertionError("shutdown must stop a queued one-shot launch")

    monkeypatch.setattr(bridge_module, "POOL_SIZE", 0)
    monkeypatch.setattr(bridge_module, "_launch_slot", held_slot)
    monkeypatch.setattr(Bridge, "_run_once", launch)
    worker = Thread(target=lambda: results.append(bridge.run("waiting question")), daemon=True)
    worker.start()
    try:
        assert waiting.wait(10)
        bridge_module.shutdown_sessions()
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert len(results) == 1 and results[0].outcome == "unavailable"
    assert launches == []


def test_final_shutdown_queued_behind_reset_stays_final(monkeypatch):
    bridge = Bridge(exe="controlled-powershell", cwd=None)
    monkeypatch.setattr(bridge_module, "POOL_SIZE", 1)
    pool = bridge_module._pool_for(bridge)
    assert pool is not None
    closing, release, final_started = Event(), Event(), Event()

    def held_close():
        closing.set()
        assert release.wait(10)

    monkeypatch.setattr(pool, "shutdown", held_close)

    def final_shutdown():
        final_started.set()
        bridge_module.shutdown_sessions()

    reset = Thread(target=bridge_module.reset_sessions, daemon=True)
    reset.start()
    try:
        assert closing.wait(10)
        final = Thread(target=final_shutdown, daemon=True)
        final.start()
        assert final_started.wait(10)
        final.join(0.2)
        assert final.is_alive(), "final shutdown must wait until the reset closes old children"
    finally:
        release.set()
        reset.join(10)
    final.join(10)
    assert not reset.is_alive() and not final.is_alive()
    assert bridge_module._pool_for(bridge) is None


def test_final_shutdown_kills_a_running_one_shot(monkeypatch):
    running, killed = Event(), Event()
    launches, results, errors = [], [], []

    class Process:
        def __init__(self, cmd, **kwargs):
            self.returncode = None
            launches.append(cmd)

        def poll(self):
            return self.returncode

        def communicate(self, input=None, timeout=None):
            running.set()
            assert killed.wait(10), "the running one-shot child was not stopped"
            return b"", b""

        def kill(self):
            self.returncode = 1
            killed.set()

    monkeypatch.setattr(bridge_module, "POOL_SIZE", 0)
    monkeypatch.setattr(bridge_module, "_launch_slot", lambda timeout: nullcontext())
    monkeypatch.setattr(bridge_module, "_POPEN", Process)
    bridge = Bridge(exe="controlled-powershell", cwd=None)

    def ask():
        try:
            results.append(bridge.run("the running question"))
        except BaseException as exc:
            errors.append(exc)

    worker = Thread(target=ask, daemon=True)
    worker.start()
    try:
        assert running.wait(10)
        bridge_module.shutdown_sessions()
    finally:
        worker.join(10)
    assert not worker.is_alive() and errors == []
    assert len(launches) == 1 and killed.is_set()
    assert len(results) == 1 and results[0].outcome == "unavailable"
    assert results[0].error == "the bridge is shutting down"
    assert bridge.run("a later question").outcome == "unavailable" and len(launches) == 1


def test_final_shutdown_breaks_a_session_launch_slot_wait(monkeypatch, tmp_path):
    if bridge_module.sys.platform == "win32":
        pytest.skip("the cross-process launch slot is specific to WSL")
    import fcntl

    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(bridge_module, "WSL_LAUNCH_SLOTS", 1)
    monkeypatch.setattr(bridge_module, "POOL_SIZE", 1)
    monkeypatch.setattr(bridge_module, "_POPEN", lambda *args, **kwargs: pytest.fail("shutdown launched a child"))
    held = (tmp_path / "system-sentinel-launch-0.lock").open("w")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    attempted = Event()
    original_flock = fcntl.flock

    def observed_flock(handle, operation):
        if operation & fcntl.LOCK_NB:
            attempted.set()
        return original_flock(handle, operation)

    monkeypatch.setattr(fcntl, "flock", observed_flock)
    bridge = Bridge(exe="controlled-powershell", cwd=None)
    results, errors = [], []

    def ask():
        try:
            results.append(bridge.run("a queued question", timeout=5))
        except BaseException as exc:
            errors.append(exc)

    worker = Thread(target=ask, daemon=True)
    worker.start()
    try:
        assert attempted.wait(5), "the question did not reach the occupied launch slot"
        bridge_module.shutdown_sessions()
        worker.join(1)
        assert not worker.is_alive(), "shutdown left the launch-slot wait running"
    finally:
        original_flock(held, fcntl.LOCK_UN)
        held.close()
        worker.join(5)
    assert errors == [] and len(results) == 1 and results[0].outcome == "unavailable"
    assert results[0].error == "the bridge is shutting down"
