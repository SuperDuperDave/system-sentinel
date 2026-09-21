"""Against the real Windows host through powershell.exe. Skipped where there is no bridge.

These establish what this machine returned today, nothing more: the shape of the
envelope and that the outcome is one the machine can answer with.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

import sentinel.bridge
from sentinel import readings  # noqa: F401
from sentinel.bridge import OUTCOMES, sessions_report
from sentinel.reading import REGISTRY, take
from sentinel.readings.health import learn_identity
from tests.conftest import real_bridge_or_skip

pytestmark = pytest.mark.host


def test_health_answers():
    bridge = real_bridge_or_skip()
    r = asyncio.run(take("health", bridge, {}))
    assert r.outcome == "ok", r.error
    data = r.section("bridge").data
    assert data["bridge"]["available"] and data["bridge"]["powershell"]
    assert data["decoder"]["present"] is True


def test_identity_is_learned_and_never_empty():
    bridge = real_bridge_or_skip()
    identity, facts = learn_identity(bridge)
    assert facts["outcome"] == "ok"
    assert identity.host and identity.user


def test_events_from_the_system_log():
    bridge = real_bridge_or_skip()
    r = asyncio.run(take("events", bridge, {"count": 5}))
    assert r.outcome in ("ok", "empty"), r.error
    if r.outcome == "ok":
        rec = r.section("records").data[0]
        assert {"RecordId", "Id", "LevelDisplayName", "ProviderName", "TimeCreated", "Message"} <= set(rec)
        assert rec["TimeCreated"].endswith("Z")
        assert rec["LevelDisplayName"] in ("Critical", "Error")


def test_record_before_a_moment_and_before_the_log_began():
    bridge = real_bridge_or_skip()
    latest = asyncio.run(take("events", bridge, {"count": 1, "levels": [1, 2, 3, 4]}))
    if latest.outcome != "ok":
        pytest.skip("no records to anchor on")
    moment = latest.section("records").data[0]["TimeCreated"]
    r = asyncio.run(take("record", bridge, {"before": moment, "count": 10}))
    assert r.outcome in ("ok", "empty"), r.error
    if r.outcome == "ok":
        stamps = [x["TimeCreated"] for x in r.section("records").data]
        assert stamps == sorted(stamps)  # oldest first
        assert all(s < moment for s in stamps)
    ancient = asyncio.run(take("record", bridge, {"before": "2000-01-01T00:00:00Z", "count": 5}))
    assert ancient.outcome == "empty", ancient.error  # a clean no-match is a finding, not a failure


def test_a_log_that_does_not_exist_is_a_failure_not_an_empty_result():
    bridge = real_bridge_or_skip()
    from sentinel.readings.events import events_script

    result = bridge.run(events_script("NoSuchLogHere", [1, 2], 5))
    assert result.outcome == "failed", (result.outcome, result.error)
    assert result.error


def test_events_since_boot_holds_to_the_machines_own_idea_of_its_start():
    bridge = real_bridge_or_skip()
    snapshot = asyncio.run(take("system", bridge, {}))
    assert snapshot.outcome == "ok", snapshot.error
    boot = _moment(snapshot.section("snapshot").data["boot_time"])

    r = asyncio.run(take("events", bridge, {"count": 200, "levels": [1, 2, 3, 4], "since": "boot"}))
    assert r.outcome in ("ok", "empty"), r.error
    if r.outcome == "ok":
        # A minute of slack: the script asks the machine for its boot time a moment after this test did.
        assert all(_moment(rec["TimeCreated"]) >= boot - timedelta(minutes=1) for rec in r.section("records").data)


def test_events_since_a_moment_returns_nothing_earlier():
    bridge = real_bridge_or_skip()
    recent = asyncio.run(take("events", bridge, {"count": 20, "levels": [1, 2, 3, 4]}))
    if recent.outcome != "ok":
        pytest.skip("no records to anchor a moment on")
    moment = min(rec["TimeCreated"] for rec in recent.section("records").data)

    r = asyncio.run(take("events", bridge, {"count": 50, "levels": [1, 2, 3, 4], "since": moment}))
    assert r.outcome in ("ok", "empty"), r.error
    if r.outcome == "ok":
        # The XPath stamp is milliseconds; a record inside the same millisecond is not earlier.
        assert all(_moment(rec["TimeCreated"]) >= _moment(moment) - timedelta(milliseconds=1) for rec in r.section("records").data)


def test_reliability_answers_or_windows_kept_no_record():
    bridge = real_bridge_or_skip()
    r = asyncio.run(take("reliability", bridge, {"days": 7}))
    assert r.outcome in ("ok", "empty"), r.error
    assert [s.name for s in r.sections] == ["records", "stability", "days"]
    rollup = r.section("days").data
    assert set(rollup) >= {"from", "to", "days", "sources", "index_now", "index_lowest"}
    if r.outcome == "ok":
        assert r.section("records").data or r.section("stability").data


def test_memory_carries_windows_own_memory_test_and_how_far_back_it_looked():
    bridge = real_bridge_or_skip()
    r = asyncio.run(take("memory", bridge, {}))
    if r.outcome != "ok":
        pytest.skip("memory was not observed")
    derived = r.section("derived").data
    diagnostic = derived["memory_diagnostic"]
    assert set(diagnostic) == {"last_result", "log_begins"}
    assert diagnostic["last_result"] is None or {"Id", "TimeCreated", "Message"} <= set(diagnostic["last_result"])
    assert "bugcheck" not in derived["ledger"]["counts"]  # the stops are the crash reading's


def test_the_power_ledger_names_the_logs_own_start_and_stop_when_they_are_there():
    bridge = real_bridge_or_skip()
    r = asyncio.run(take("power", bridge, {}))
    if r.outcome != "ok":
        pytest.skip("power was not observed")
    records = r.section("raw").data["transitions"]
    counts = r.section("derived").data["ledger"]["counts"]
    for event_id, name in ((6005, "log started"), (6006, "log stopped")):
        if any(rec.get("Id") == event_id and rec.get("ProviderName") == "EventLog" for rec in records):
            assert counts.get(name), (event_id, counts)


def test_signals_draws_on_the_stops_and_on_windows_own_record():
    bridge = real_bridge_or_skip()
    r = asyncio.run(take("signals", bridge, {}))
    assert r.outcome in ("ok", "empty"), r.error
    inputs = {entry["name"]: entry["outcome"] for entry in r.method["readings"]}
    assert {"crash", "reliability"} <= set(inputs)
    assert inputs["crash"] in ("ok", "empty") and inputs["reliability"] in ("ok", "empty"), inputs


# --- the two transports, against the real machine -------------------------------------------------


def test_a_reading_through_a_live_session_matches_one_through_a_launch(monkeypatch):
    """The transport is not part of the contract: the same question asked both ways comes back the
    same. A raw script for the exact items, and a reading for what the catalog answers with."""
    bridge = real_bridge_or_skip()
    script = "[pscustomobject]@{ name = 'one'; n = 1 }, [pscustomobject]@{ name = 'two'; n = 2 }"

    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    launched = bridge.run(script)
    launched_snapshot = asyncio.run(take("system", bridge, {}))

    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 4)
    answered = bridge.run(script)
    session_snapshot = asyncio.run(take("system", bridge, {}))

    assert launched.outcome == answered.outcome == "ok", (launched.error, answered.error)
    assert launched.items == answered.items == [{"name": "one", "n": 1}, {"name": "two", "n": 2}]
    assert sessions_report(bridge)["answered"] >= 2  # the second pair really did go through a session

    assert launched_snapshot.outcome == session_snapshot.outcome == "ok"
    assert [s.name for s in launched_snapshot.sections] == [s.name for s in session_snapshot.sections]
    # The machine did not restart between the two takes, so this is the same fact twice.
    assert launched_snapshot.section("snapshot").data["boot_time"] == session_snapshot.section("snapshot").data["boot_time"]


def test_a_question_in_a_live_session_cannot_see_the_one_before_it(monkeypatch):
    """Each question runs in a child scope, which is the whole of the isolation a session needs:
    one process answering everything must not let one reading's variables reach the next."""
    bridge = real_bridge_or_skip()
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 1)  # one session, so both questions go to it

    first = bridge.run("$__sentinel_probe = 41; [pscustomobject]@{ set = $true }")
    assert first.outcome == "ok", first.error
    second = bridge.run("[pscustomobject]@{ leaked = ($null -ne $__sentinel_probe) }")
    assert second.outcome == "ok", second.error
    assert second.items == [{"leaked": False}]

    report = sessions_report(bridge)
    assert report["alive"] == 1 and report["answered"] == 2  # and it was one session that answered both


def test_the_pool_survives_the_whole_catalog_taken_twice(monkeypatch):
    """The whole catalog, twice, through one pool: no session dies, none has to be replaced, and
    nothing falls back to a launch. This is the run that would show a leak or a wedged frame."""
    bridge = real_bridge_or_skip()
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 4)
    moment = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"record": {"before": moment}}

    seen: dict[str, list[str]] = {}
    for _ in range(2):
        for name in list(REGISTRY):
            reading = asyncio.run(take(name, bridge, params.get(name, {})))
            assert reading.outcome in OUTCOMES, (name, reading.outcome)
            assert reading.outcome != "unavailable", (name, reading.error)  # the bridge itself never stopped answering
            seen.setdefault(name, []).append(reading.outcome)

    report = sessions_report(bridge)
    assert report["transport"] == "session"
    assert report["answered"] >= 2 * len(REGISTRY)
    assert report["alive"] <= report["size"]
    assert report["discarded"].get("died", 0) == 0, report
    assert report["fell_back"] == 0 and report["start_failures"] == 0, report
    assert len(seen) == len(REGISTRY)


def _moment(stamp: str) -> datetime:
    return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
