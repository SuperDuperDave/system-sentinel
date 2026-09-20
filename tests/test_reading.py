"""The envelope: every bridge outcome maps to a reading; parameters are coerced; the catalog is one table."""

import asyncio

import pytest

from sentinel import readings  # noqa: F401
from sentinel.bridge import BridgeResult
from sentinel.reading import REGISTRY, Param, Spec, from_bridge, take
from sentinel.readings.events import _utc_stamp, events_script, record_script
from tests.conftest import FakeBridge


def test_ok_reading_has_one_raw_section_and_a_count():
    r = from_bridge("events", {"count": 2}, "  Get-WinEvent  ", BridgeResult("ok", items=[{"Id": 1}, {"Id": 2}], took_ms=12))
    d = r.to_dict()
    assert d["outcome"] == "ok" and d["count"] == 2 and d["took_ms"] == 12
    assert d["sections"] == [{"name": "records", "class": "raw", "data": [{"Id": 1}, {"Id": 2}]}]
    assert d["method"] == {"kind": "powershell", "query": "Get-WinEvent"}
    assert d["error"] is None and d["redacted"] == []
    assert d["asked_at"].endswith("Z")


def test_empty_reading_is_distinguishable_from_failure():
    empty = from_bridge("events", {}, "q", BridgeResult("empty")).to_dict()
    failed = from_bridge("events", {}, "q", BridgeResult("failed", error="boom")).to_dict()
    unavailable = from_bridge("events", {}, "q", BridgeResult("unavailable", error="no powershell")).to_dict()
    assert empty["outcome"] == "empty" and empty["count"] == 0 and empty["sections"][0]["data"] == [] and empty["error"] is None
    assert failed["outcome"] == "failed" and failed["count"] is None and failed["sections"] == [] and failed["error"] == {"kind": "failed", "detail": "boom"}
    assert unavailable["outcome"] == "unavailable" and unavailable["error"]["kind"] == "unavailable"


def test_object_shape_unwraps_the_single_item():
    r = from_bridge("system", {}, "q", BridgeResult("ok", items=[{"CPU": "x"}]), section="snapshot", shape="object")
    assert r.sections[0].data == {"CPU": "x"}
    assert r.count is None


def test_spec_coerces_defaults_types_and_choices():
    spec = REGISTRY["events"]
    assert spec.coerce({}) == {"log": "System", "levels": [1, 2], "count": 50}
    assert spec.coerce({"levels": "1,2,3", "count": "5", "log": "Application"}) == {"log": "Application", "levels": [1, 2, 3], "count": 5}
    with pytest.raises(ValueError):
        spec.coerce({"log": "Security"})
    with pytest.raises(ValueError):
        spec.coerce({"count": "many"})


def test_required_param_stays_none_until_given():
    spec = REGISTRY["record"]
    assert spec.coerce({})["before"] is None


def test_catalog_lists_every_registered_reading_once():
    names = [s.name for s in REGISTRY.values()]
    assert len(names) == len(set(names))
    assert {"health", "events", "record"} <= set(names)
    for spec in REGISTRY.values():
        d = spec.to_dict()
        assert d["name"] and d["description"] and d["classes"]


def test_register_twice_is_an_error():
    from sentinel.reading import register

    with pytest.raises(ValueError):
        register(Spec(name="events", description="dup", classes=("raw",), take=lambda b, p: None))


def test_events_script_uses_the_parameters():
    s = events_script("Application", [1, 2, 3], 7)
    assert "LogName='Application'" in s and "Level=1,2,3" in s and "-MaxEvents 7" in s
    assert "-ErrorAction Stop" in s


def test_record_stamp_is_utc_milliseconds():
    assert _utc_stamp("2026-09-20T18:04:11.204Z") == "2026-09-20T18:04:11.204Z"
    assert _utc_stamp("2026-09-20T20:04:11+02:00") == "2026-09-20T18:04:11.000Z"
    with pytest.raises(ValueError):
        _utc_stamp("not-a-date")


def test_record_script_filters_before_the_moment():
    s = record_script("System", "2026-09-20T18:04:11Z", 20)
    assert "@SystemTime&lt;'2026-09-20T18:04:11.000Z'" in s and "-MaxEvents 20" in s


def test_take_events_through_a_fake_bridge():
    bridge = FakeBridge()
    r = asyncio.run(take("events", bridge, {"count": "3"}))
    assert r.outcome == "ok" and r.count == 1
    assert r.params == {"log": "System", "levels": [1, 2], "count": 3}
    assert "-MaxEvents 3" in bridge.scripts[0]


def test_take_record_reverses_to_oldest_first_and_rejects_bad_timestamps():
    bridge = FakeBridge(BridgeResult("ok", items=[{"Id": 3}, {"Id": 2}, {"Id": 1}]))
    r = asyncio.run(take("record", bridge, {"before": "2026-09-20T18:04:11Z"}))
    assert [x["Id"] for x in r.section("records").data] == [1, 2, 3]
    with pytest.raises(ValueError):
        asyncio.run(take("record", bridge, {"before": "yesterday"}))


def test_unknown_reading_is_a_key_error():
    with pytest.raises(KeyError):
        asyncio.run(take("nope", FakeBridge(), {}))


def test_param_dataclass_is_frozen():
    p = Param("x", "int", 1, "d")
    with pytest.raises(Exception):
        p.default = 2  # type: ignore[misc]
