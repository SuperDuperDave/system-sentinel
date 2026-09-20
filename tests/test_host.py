"""Against the real Windows host through powershell.exe. Skipped where there is no bridge.

These establish what this machine returned today, nothing more: the shape of the
envelope and that the outcome is one the machine can answer with.
"""

import asyncio

import pytest

from sentinel import readings  # noqa: F401
from sentinel.reading import take
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
