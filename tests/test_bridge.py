"""The bridge's outcome is part of the type: every way powershell.exe can answer maps to one
outcome, whichever transport carried the question, and no session outlives the process that
started it.

Every test that takes the ``bridge`` fixture runs twice — once against a process per question,
once against a live session — because that is the contract: nothing above this seam can tell
which transport answered it.
"""

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from contextlib import nullcontext
from pathlib import Path

import pytest

import sentinel.bridge
from sentinel.bridge import WSL_LAUNCH_SLOTS, Bridge, BridgeResult, classify, clean_stderr, sessions_report
from sentinel.readings.health import take_health


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


def test_output_shaped_like_a_marker_is_the_same_failure_either_way(bridge: Bridge):
    """A script that writes a line shaped like a session's frame marker has written something that
    is not the JSON document the bridge asked for — the same failure a launch would report."""
    r = bridge.run("# fake: mark-lookalike")
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
    bind = bridge.run("# fake: wsl-bind-error")
    assert bind.outcome == "unavailable"
    assert "UtilBindVsockAnyPort" in bind.error


def test_wsl_interop_transient_is_retried_once(bridge: Bridge):
    r = bridge.run("# fake: wsl-interop-once")
    assert r.outcome == "ok" and r.items == [{"Id": 7}]


def test_classify_is_the_one_rule_both_transports_ask():
    """Neither transport decides what an answer means; this does, from the three facts both of
    them have: what went to stdout, what went to stderr, and whether the script raised."""
    assert classify("[]", "", 0, 3).outcome == "empty"
    assert classify('{"a": 1}', "", 0, 3).items == [{"a": 1}]
    assert classify("", "", 0, 3).outcome == "empty"
    assert classify("", "Access is denied.", 0, 3).outcome == "denied"
    assert classify("", "Get-WinEvent : nothing here", 1, 3).outcome == "failed"
    assert classify("", "<3>WSL (1 - ) ERROR: UtilAcceptVsock:271: accept4 failed 110", 1, 3).outcome == "unavailable"
    assert classify("\ufeff[{}]", "", 0, 3).outcome == "ok"  # the console encoding's mark is not the answer


# --- the session transport ---------------------------------------------------------------------


def test_one_live_session_answers_question_after_question(session_bridge: Bridge):
    """The point of the whole thing: the second question does not pay for a second process."""
    first = session_bridge.run("# fake: count")
    second = session_bridge.run("# fake: count")
    assert [first.items, second.items] == [[{"n": 1}], [{"n": 2}]]  # one process, asked twice
    assert sessions_report(session_bridge)["alive"] == 1


def test_a_one_shot_launch_starts_from_nothing_every_time(one_shot_bridge: Bridge):
    assert one_shot_bridge.run("# fake: count").items == [{"n": 1}]
    assert one_shot_bridge.run("# fake: count").items == [{"n": 1}]
    assert sessions_report(one_shot_bridge)["alive"] == 0


def test_a_frame_that_never_closes_is_a_timeout_and_the_session_goes_with_it(session_bridge: Bridge):
    """The script is still running in there, so nothing else can be asked down that pipe."""
    r = session_bridge.run("# fake: sleep", timeout=0.5)
    assert r.outcome == "timeout" and not r.observed
    report = sessions_report(session_bridge)
    assert report["discarded"].get("timeout") == 1
    assert report["alive"] == 0
    assert session_bridge.run("# fake: ok-list").outcome == "ok"  # and the next question gets a fresh one


def test_a_session_that_died_is_replaced(session_bridge: Bridge):
    assert session_bridge.run("# fake: ok-list").outcome == "ok"
    pool = sentinel.bridge._pool_for(session_bridge)
    assert pool is not None
    gone = pool._idle[-1]
    gone._proc.kill()
    gone._proc.wait(timeout=5)

    assert session_bridge.run("# fake: ok-list").outcome == "ok"
    report = sessions_report(session_bridge)
    assert report["discarded"].get("died") == 1
    assert report["alive"] == 1


def test_a_marker_that_is_not_this_questions_marker_never_closes_its_frame(session_bridge: Bridge):
    """Output that looks like a frame's marker must not be mistaken for one — the marker is fresh
    per question — and the session must still be in step for the question after it."""
    r = session_bridge.run("# fake: mark-lookalike")
    assert r.outcome == "failed" and "not JSON" in r.error  # everything the script wrote, not half of it
    assert session_bridge.run("# fake: count").items == [{"n": 1}]  # same session, still in step
    assert sessions_report(session_bridge)["alive"] == 1


def test_a_late_error_stream_is_still_part_of_its_answer(session_bridge: Bridge):
    """A slow stderr reader must never make a failed query look like an observed empty one."""
    result = session_bridge.run("# fake: delayed-stderr", timeout=1)
    assert result.outcome == "failed"
    assert "event log is unavailable" in result.error
    assert session_bridge.run("# fake: count").items == [{"n": 1}]
    assert sessions_report(session_bridge)["alive"] == 1


def test_a_missing_error_stream_mark_is_not_an_empty_reading(session_bridge: Bridge):
    result = session_bridge.run("# fake: missing-stderr-mark", timeout=0.5)
    assert result.outcome == "unavailable"
    assert not result.observed
    assert "error stream did not close" in result.error
    assert sessions_report(session_bridge)["discarded"].get("stderr") == 1
    assert session_bridge.run("# fake: count").items == [{"n": 1}]


def test_a_session_is_retired_at_its_bound(session_bridge: Bridge, monkeypatch):
    """A session is measured steady to two hundred questions; past a bound it is replaced rather
    than trusted past the evidence."""
    monkeypatch.setattr(sentinel.bridge, "SESSION_QUESTIONS", 2)
    answers = [session_bridge.run("# fake: count").items[0]["n"] for _ in range(3)]
    assert answers == [1, 2, 1]  # two from one process, then a fresh one
    report = sessions_report(session_bridge)
    assert report["discarded"].get("recycled") == 1
    assert report["answered"] == 3


def test_the_pool_bounds_how_many_questions_are_in_flight(session_bridge: Bridge, monkeypatch):
    """The pool is the cap, and the only one: eight questions at once are answered by at most
    POOL_SIZE processes, which is what keeps WSL's interop layer from being handed more launches
    than it can take (friction F2)."""
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 3)
    lock = threading.Lock()
    inside = peak = 0
    ask = sentinel.bridge.Session.ask

    def counted(self, script, *, timeout, depth):
        if not script:  # the session's own first question, asked before anyone else's
            return ask(self, script, timeout=timeout, depth=depth)
        nonlocal inside, peak
        with lock:
            inside += 1
            peak = max(peak, inside)
        try:
            time.sleep(0.05)  # long enough for the questions behind this one to pile up on the cap
            return ask(self, script, timeout=timeout, depth=depth)
        finally:
            with lock:
                inside -= 1

    monkeypatch.setattr(sentinel.bridge.Session, "ask", counted)
    together = threading.Barrier(8)
    results: list[BridgeResult] = []

    def run_one():
        together.wait()
        results.append(session_bridge.run("# fake: ok-list"))

    threads = [threading.Thread(target=run_one) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert len(results) == 8 and all(r.outcome == "ok" for r in results)  # every one of them still answered
    assert 1 < peak <= 3, peak  # they did contend, and the cap held
    report = sessions_report(session_bridge)
    assert report["alive"] <= 3 and report["answered"] == 8


def test_the_one_shot_transport_answers_when_a_session_will_not_start(session_bridge: Bridge, monkeypatch):
    """Never lose a reading because a session would not start: the question is launched instead,
    and the fallback is a number on health rather than a failure."""
    monkeypatch.setenv("SENTINEL_FAKE_NO_SESSION", "1")
    r = session_bridge.run("# fake: ok-list")
    assert r.outcome == "ok" and r.items[0]["Id"] == 41
    report = sessions_report(session_bridge)
    assert report["transport"] == "session"  # what was asked for
    assert report["alive"] == 0 and report["start_failures"] == 1 and report["fell_back"] == 1  # what happened
    assert report["last_start_failure"] == "probe_lost"


def test_a_session_that_dies_in_the_middle_of_a_question_hands_it_to_a_launch(session_bridge: Bridge, monkeypatch):
    """The question is not lost with the session: it is asked again the other way, and the loss is
    a count on health rather than a reading nobody got."""
    real = sentinel.bridge.Session.ask

    def dies(self, script, *, timeout, depth):
        if "ok-list" in script:
            self.discard("died")
            raise sentinel.bridge.SessionLost("the session ended in the middle of a question")
        return real(self, script, timeout=timeout, depth=depth)

    monkeypatch.setattr(sentinel.bridge.Session, "ask", dies)
    r = session_bridge.run("# fake: ok-list")
    assert r.outcome == "ok" and r.items[0]["Id"] == 41  # a launch answered it
    report = sessions_report(session_bridge)
    assert report["discarded"].get("died") == 1 and report["fell_back"] == 1


def test_shutdown_ends_every_session_and_the_pool_forgets_them(session_bridge: Bridge):
    session_bridge.run("# fake: ok-list")
    pool = sentinel.bridge._pool_for(session_bridge)
    assert pool is not None
    pids = [session.pid for session in pool._sessions]
    assert pids

    sentinel.bridge.shutdown_sessions()
    assert [pid for pid in pids if _running(pid)] == []
    assert sessions_report(session_bridge)["alive"] == 0
    assert session_bridge.run("# fake: ok-list").outcome == "ok"  # and the next question starts again


def test_a_session_does_not_outlive_the_process_that_started_it(fake_powershell: Path):
    """A leaked powershell.exe per run is a defect a person would find in Task Manager, and this
    tool exists to make such things visible. Run a question in a child process, let it exit the
    ordinary way, and look for what it started."""
    code = textwrap.dedent(
        f"""
        import json, sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
        from sentinel.bridge import Bridge, _POOLS
        result = Bridge(exe={str(fake_powershell)!r}, cwd=None).run("# fake: ok-list")
        assert result.outcome == "ok", result
        print(json.dumps([session.pid for pool in _POOLS.values() for session in pool._idle]))
        """
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    pids = json.loads(done.stdout.strip().splitlines()[-1])
    assert pids, "the question should have been answered by a live session"

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(_running(pid) for pid in pids):
        time.sleep(0.05)
    assert [pid for pid in pids if _running(pid)] == []


def _running(pid: int) -> bool:
    if sys.platform == "win32":
        listed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in listed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_health_reports_the_pool(session_bridge: Bridge):
    """A reading whose job is the bridge says what the bridge now is. Counts, never a verdict."""
    session_bridge.run("# fake: ok-list")
    sessions = take_health(session_bridge, {}).section("bridge").data["bridge"]["sessions"]
    assert sessions["transport"] == "session"
    assert sessions["alive"] == 1 and sessions["answered"] >= 2
    assert sessions["oldest_seconds"] is not None
    assert sessions["discarded"] == {} and sessions["fell_back"] == 0


def test_health_says_one_shot_where_the_sessions_are_switched_off(one_shot_bridge: Bridge):
    sessions = take_health(one_shot_bridge, {}).section("bridge").data["bridge"]["sessions"]
    assert sessions["transport"] == "one-shot"
    assert sessions["alive"] == 0 and sessions["size"] == 0


def test_health_warns_when_a_question_was_launched_instead(session_bridge: Bridge, monkeypatch):
    monkeypatch.setenv("SENTINEL_FAKE_NO_SESSION", "1")
    reading = take_health(session_bridge, {})
    assert reading.outcome == "ok"  # the machine still answered
    assert any("one-shot" in warning for warning in reading.warnings)


# --- the launch slot, which starting a session still takes ---------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="the slots exist only where launches cross WSL's interop layer")
def test_under_wsl_launches_share_a_few_slots_across_processes(one_shot_bridge: Bridge, monkeypatch):
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
        results.append(one_shot_bridge.run("# fake: ok-list"))

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
    reading in every other process: the launch is reported as not observed, with the reason. A
    session cannot be started while the slot is held either, so both transports say the same."""
    import fcntl

    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), "system-sentinel-launch-0.lock")
    with open(path, "w") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            result = bridge.run("# fake: ok-list", timeout=0.3)
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
    assert result.outcome == "unavailable" and "launch slot" in (result.error or "")


def test_a_bridge_without_a_slot_still_launches(one_shot_bridge: Bridge, monkeypatch):
    """A lock file that cannot be opened is no coordination at all; the reading still happens."""
    monkeypatch.setattr(sentinel.bridge, "_launch_slot", nullcontext)
    assert one_shot_bridge.run("# fake: ok-list").outcome == "ok"


def test_clean_stderr_plain_text():
    assert clean_stderr("line one\r\n\r\nline two\r\n") == "line one\nline two"


def test_result_is_immutable():
    r = BridgeResult("ok", items=[1])
    try:
        r.outcome = "failed"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("BridgeResult must be frozen")
