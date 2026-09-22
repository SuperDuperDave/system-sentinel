"""Portable bridge lifecycle races, without starting an executable or querying Windows."""

from contextlib import nullcontext
from io import BytesIO
from threading import Event, Thread

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
