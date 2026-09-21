"""The bridge's outcome is part of the type: every way powershell.exe can answer maps to one
outcome, and no more than a fixed number of launches are ever in flight at once."""

import sys
import threading
import time
from contextlib import nullcontext

import pytest

import sentinel.bridge

from sentinel.bridge import MAX_CONCURRENT_LAUNCHES, WSL_LAUNCH_SLOTS, Bridge, BridgeResult, clean_stderr


def test_ok_list(bridge: Bridge):
    r = bridge.run("# fake: ok-list\nGet-WinEvent")
    assert r.outcome == "ok"
    assert r.observed
    assert [i["Id"] for i in r.items] == [41, 6008]
    assert r.returncode == 0
    assert r.error is None
    assert r.took_ms >= 0


def test_single_object_becomes_a_list_of_one(bridge: Bridge):
    r = bridge.run("# fake: ok-object")
    assert r.outcome == "ok"
    assert r.items == [{"CPU": "x"}]


def test_empty_is_a_finding_not_a_failure(bridge: Bridge):
    r = bridge.run("# fake: empty")
    assert r.outcome == "empty"
    assert r.observed
    assert r.items == []
    assert r.error is None


def test_failed_carries_the_error(bridge: Bridge):
    r = bridge.run("# fake: failed")
    assert r.outcome == "failed"
    assert not r.observed
    assert "event log" in r.error
    assert r.returncode == 1


def test_denied_is_recognized(bridge: Bridge):
    assert bridge.run("# fake: denied").outcome == "denied"
    assert bridge.run("# fake: denied-quiet").outcome == "denied"


def test_clixml_stderr_is_unwrapped(bridge: Bridge):
    r = bridge.run("# fake: clixml")
    assert r.outcome == "failed"
    assert "No events were found" in r.error
    assert "CLIXML" not in r.error
    assert "_x000D_" not in r.error


def test_warnings_survive_an_ok_result(bridge: Bridge):
    r = bridge.run("# fake: warn")
    assert r.outcome == "ok"
    assert r.items == [{"Id": 1}]
    assert any("Invalid class" in w for w in r.warnings)


def test_non_json_output_is_a_failure(bridge: Bridge):
    r = bridge.run("# fake: notjson")
    assert r.outcome == "failed"
    assert "not JSON" in r.error


def test_timeout(bridge: Bridge):
    r = bridge.run("# fake: sleep", timeout=0.5)
    assert r.outcome == "timeout"
    assert not r.observed


def test_unavailable_when_there_is_no_powershell():
    r = Bridge(exe=None).run("anything")
    assert r.outcome == "unavailable"
    assert not r.observed
    r2 = Bridge(exe="/nonexistent/powershell.exe").run("anything")
    assert r2.outcome == "unavailable"


def test_wsl_interop_failure_is_unavailable_not_failed(bridge: Bridge):
    r = bridge.run("# fake: wsl-interop")
    assert r.outcome == "unavailable"
    assert "WSL could not start powershell.exe" in r.error


def test_wsl_interop_transient_is_retried_once(bridge: Bridge):
    r = bridge.run("# fake: wsl-interop-once")
    assert r.outcome == "ok" and r.items == [{"Id": 7}]


def test_no_more_than_the_cap_are_in_flight_at_once(bridge: Bridge, monkeypatch):
    """Friction F2: WSL's interop layer fails when several launches are handed over together, and
    one reading now takes seven others at once. The cap is what keeps one process from causing it.
    The cross-process slot is stood down here so the cap is what is measured."""
    monkeypatch.setattr(sentinel.bridge, "_launch_slot", nullcontext)
    lock = threading.Lock()
    inside = peak = 0
    launch = Bridge._run_once

    def counted(self, script, *, timeout, depth):
        nonlocal inside, peak
        with lock:
            inside += 1
            peak = max(peak, inside)
        try:
            time.sleep(0.05)  # long enough for the threads behind this one to pile up on the cap
            return launch(self, script, timeout=timeout, depth=depth)
        finally:
            with lock:
                inside -= 1

    monkeypatch.setattr(Bridge, "_run_once", counted)
    together = threading.Barrier(8)
    results: list[BridgeResult] = []

    def run_one():
        together.wait()
        results.append(bridge.run("# fake: ok-list"))

    threads = [threading.Thread(target=run_one) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert len(results) == 8 and all(r.outcome == "ok" for r in results)  # every one of them still answered
    assert 1 < peak <= MAX_CONCURRENT_LAUNCHES, peak  # they did contend, and the cap held


@pytest.mark.skipif(sys.platform == "win32", reason="the slots exist only where launches cross WSL's interop layer")
def test_under_wsl_launches_share_a_few_slots_across_processes(bridge: Bridge, monkeypatch):
    """Friction F2, the cross-process half: a suite beside a live dashboard failed every launch on
    2026-09-21. The slots are flock'd files, and flock is held per open file description, so
    threads that each open the files are the same contention processes would be."""
    lock = threading.Lock()
    inside = peak = 0
    launch = Bridge._run_once

    def counted(self, script, *, timeout, depth):
        nonlocal inside, peak
        with lock:
            inside += 1
            peak = max(peak, inside)
        try:
            time.sleep(0.05)
            return launch(self, script, timeout=timeout, depth=depth)
        finally:
            with lock:
                inside -= 1

    monkeypatch.setattr(Bridge, "_run_once", counted)
    together = threading.Barrier(6)
    results: list[BridgeResult] = []

    def run_one():
        together.wait()
        results.append(bridge.run("# fake: ok-list"))

    threads = [threading.Thread(target=run_one) for _ in range(6)]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert len(results) == 6 and all(r.outcome == "ok" for r in results)
    assert peak == WSL_LAUNCH_SLOTS == 1, peak  # six launches together, never more than one in flight
    assert time.monotonic() - started >= 6 * 0.05  # and they did queue rather than skip the slot


@pytest.mark.skipif(sys.platform == "win32", reason="the slots exist only where launches cross WSL's interop layer")
def test_a_slot_held_elsewhere_for_longer_than_the_timeout_is_unavailable_not_a_wait_without_end(bridge: Bridge):
    """Another process holding the slot (a paused suite, a hung debugger) must not hang every
    reading in every other process: the launch is reported as not observed, with the reason."""
    import fcntl
    import os

    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), "system-sentinel-launch-0.lock")
    with open(path, "w") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            result = bridge.run("# fake: ok-list", timeout=0.3)
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
    assert result.outcome == "unavailable" and "launch slot" in (result.error or "")


def test_clean_stderr_plain_text():
    assert clean_stderr("line one\r\n\r\nline two\r\n") == "line one\nline two"


def test_result_is_immutable():
    r = BridgeResult("ok", items=[1])
    try:
        r.outcome = "failed"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("BridgeResult must be frozen")
