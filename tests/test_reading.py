"""The envelope: every bridge outcome maps to a reading; parameters are coerced; the catalog is one table."""

import asyncio
import dataclasses

import pytest

from sentinel import readings  # noqa: F401
from sentinel.bridge import BridgeResult
from sentinel.reading import REGISTRY, Param, Section, Spec, from_bridge, from_object, take
from sentinel.readings.events import _utc_stamp, events_script, record_script, since_clause
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


def _object_reading(adapter, result, built):
    def build(payload):
        built.append(payload)
        return [Section("snapshot", "raw", payload)]

    args = ("synthetic", {"selected": "example"}, "  synthetic object collector  ", result)
    if adapter == "from_object":
        return from_object(*args, build)
    return from_bridge(*args, section="snapshot", shape="object")


@pytest.mark.parametrize("adapter", ["from_object", "from_bridge"])
@pytest.mark.parametrize(("outcome", "items"), [
    pytest.param("ok", [], id="ok-without-an-object"),
    pytest.param("empty", [], id="empty-stream"),
    pytest.param("empty", [{"devices": []}], id="empty-outcome-with-an-object"),
    pytest.param("ok", [None], id="null"),
    pytest.param("ok", ["synthetic scalar"], id="string"),
    pytest.param("ok", [7], id="number"),
    pytest.param("ok", [[]], id="empty-array-object"),
    pytest.param("ok", [[{"devices": []}]], id="nested-array-object"),
    pytest.param("ok", [{"devices": []}, {"devices": [{"Name": "synthetic problem"}]}], id="two-conflicting-objects"),
    pytest.param("ok", [{"devices": []}, "stray output"], id="object-then-scalar"),
    pytest.param("ok", ["stray output", {"devices": []}], id="scalar-then-object"),
])
def test_object_readings_reject_unexpected_output_before_building_sections(adapter, outcome, items):
    result = BridgeResult(outcome, items=items, took_ms=17, warnings=["synthetic bridge warning"])
    built = []
    reading = _object_reading(adapter, result, built)
    assert reading.outcome == "failed" and not reading.observed
    assert reading.count is None and reading.sections == [] and built == []
    assert reading.error and reading.error["kind"] == "failed"
    assert "object" in reading.error["detail"].lower()
    assert reading.method == {"kind": "powershell", "query": "synthetic object collector"}
    assert reading.params == {"selected": "example"} and reading.took_ms == 17
    assert reading.warnings == ["synthetic bridge warning"]
    assert result.outcome == outcome and result.items == items


@pytest.mark.parametrize("adapter", ["from_object", "from_bridge"])
def test_one_object_with_empty_arrays_remains_available_to_the_collector(adapter):
    payload = {"devices": [], "records": []}
    result = BridgeResult("ok", items=[payload], took_ms=13, warnings=["synthetic bridge warning"])
    built = []
    reading = _object_reading(adapter, result, built)
    assert reading.outcome == "ok" and reading.observed and reading.error is None
    assert reading.count is None
    assert [(section.name, section.cls, section.data) for section in reading.sections] == [
        ("snapshot", "raw", {"devices": [], "records": []}),
    ]
    assert built == ([payload] if adapter == "from_object" else [])
    assert reading.took_ms == 13 and reading.warnings == ["synthetic bridge warning"]


@pytest.mark.parametrize("adapter", ["from_object", "from_bridge"])
@pytest.mark.parametrize("outcome", ["failed", "denied", "unavailable", "timeout"])
def test_object_readings_preserve_real_bridge_failures_without_building(adapter, outcome):
    result = BridgeResult(outcome, error="synthetic collection failure", took_ms=19, warnings=["synthetic warning"])
    built = []
    reading = _object_reading(adapter, result, built)
    assert reading.outcome == outcome and not reading.observed
    assert reading.error == {"kind": outcome, "detail": "synthetic collection failure"}
    assert reading.sections == [] and reading.count is None and built == []
    assert reading.method == {"kind": "powershell", "query": "synthetic object collector"}
    assert reading.took_ms == 19 and reading.warnings == ["synthetic warning"]


def test_object_warning_extraction_and_builder_changes_preserve_the_bridge_payload():
    devices = []
    payload = {"devices": devices, "warnings": ["synthetic source gap"]}
    result = BridgeResult("ok", items=[payload], warnings=["synthetic bridge warning"])
    built = []

    def build(received):
        assert "warnings" not in received
        received["builder_marker"] = True
        built.append(received)
        return [Section("snapshot", "raw", received)]

    first = from_object("synthetic", {}, "query", result, build)
    second = from_object("synthetic", {}, "query", result, build)
    assert first.warnings == second.warnings == ["synthetic bridge warning", "synthetic source gap"]
    assert result.items == [{"devices": [], "warnings": ["synthetic source gap"]}]
    assert built[0] is not payload and built[1] is not payload and built[0] is not built[1]
    assert all(received["devices"] is devices for received in built)
    assert all(reading.section("snapshot").data["builder_marker"] is True for reading in (first, second))
    first.warnings.append("local reading note")
    assert result.warnings == ["synthetic bridge warning"]
    assert payload["warnings"] == ["synthetic source gap"]
    assert second.warnings == ["synthetic bridge warning", "synthetic source gap"]


def test_spec_coerces_defaults_types_and_choices():
    spec = REGISTRY["events"]
    assert spec.coerce({}) == {"log": "System", "levels": [1, 2], "count": 50, "since": ""}
    assert spec.coerce({"levels": "1,2,3", "count": "5", "log": "Application"}) == {"log": "Application", "levels": [1, 2, 3], "count": 5, "since": ""}
    with pytest.raises(ValueError):
        spec.coerce({"log": "Security"})
    with pytest.raises(ValueError):
        spec.coerce({"count": "many"})
    assert spec.coerce({"count": None})["count"] == 50


def test_numeric_bounds_are_shared_by_the_catalog_and_all_callers():
    for spec in REGISTRY.values():
        for param in spec.params:
            if param.minimum is not None:
                assert param.default >= param.minimum, (spec.name, param.name)
            if param.maximum is not None:
                assert param.default <= param.maximum, (spec.name, param.name)
    for name in ("events", "record", "whea", "faults", "crash", "drivers"):
        spec = REGISTRY[name]
        count = next(p for p in spec.params if p.name == "count")
        published = next(p for p in spec.to_dict()["params"] if p["name"] == "count")
        assert published["minimum"] == 1
        if count.maximum is not None:
            assert published["maximum"] == count.maximum
            with pytest.raises(ValueError, match="count"):
                spec.coerce({"count": count.maximum + 1, **({"before": "2026-09-20T18:04:11Z"} if name == "record" else {})})
        for bad in (0, -1, True, 1.5):
            with pytest.raises(ValueError, match="count"):
                spec.coerce({"count": bad, **({"before": "2026-09-20T18:04:11Z"} if name == "record" else {})})
    with pytest.raises(ValueError, match="finite"):
        Spec("synthetic", "", ("raw",), lambda b, p: None, (Param("rate", "float", 1.0, "", minimum=0),)).coerce({"rate": float("nan")})

    for name, param_name, maximum in (("changes", "hours", 2160), ("changes", "count", 500), ("reliability", "days", 366), ("performance_history", "hours", 48), ("storms", "hours", 43800)):
        param = next(p for p in REGISTRY[name].to_dict()["params"] if p["name"] == param_name)
        assert param["minimum"] == 1 and param["maximum"] == maximum


def test_required_param_is_refused_when_missing():
    spec = REGISTRY["record"]
    with pytest.raises(ValueError, match="'before' is required"):
        spec.coerce({})
    assert spec.coerce({"before": "2026-09-20T18:04:11Z"})["before"] == "2026-09-20T18:04:11Z"


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


def test_events_script_asks_the_log_index_for_the_levels():
    s = events_script("Application", [1, 2, 3], 7)
    assert "<Select Path='Application'>*[System[(Level=1 or Level=2 or Level=3)]]</Select>" in s
    assert "-FilterXml $xml" in s and "-MaxEvents 8" in s and "-ErrorAction Stop" in s
    assert "FilterHashtable" not in s  # StartTime there does not honour a timestamp's Kind


def test_a_window_is_a_clause_against_the_index_and_boot_is_resolved_on_the_machine():
    assert since_clause("") == ("", "")
    prelude, clause = since_clause("2026-09-20T18:04:11Z")
    assert prelude == "" and clause == " and TimeCreated[@SystemTime&gt;='2026-09-20T18:04:11.000Z']"
    prelude, clause = since_clause("BOOT")
    assert "LastBootUpTime" in prelude and clause == " and TimeCreated[@SystemTime&gt;='$since']"
    with pytest.raises(ValueError, match="'boot'"):
        since_clause("the other day")


def test_events_since_a_moment_and_since_boot():
    s = events_script("System", [1, 2], 5, "2026-09-20T18:04:11Z")
    assert "*[System[(Level=1 or Level=2) and TimeCreated[@SystemTime&gt;='2026-09-20T18:04:11.000Z']]]" in s
    boot = events_script("System", [1, 2], 5, "boot")
    assert boot.startswith("$since = (Get-CimInstance Win32_OperatingSystem")
    assert "TimeCreated[@SystemTime&gt;='$since']" in boot
    assert '@"' in boot  # an expanding here-string: $since is the machine's answer, not a literal


def test_no_level_asked_for_is_every_level():
    assert "<Select Path='System'>*</Select>" in events_script("System", [], 5)
    assert "*[System[TimeCreated[@SystemTime&gt;='2026-09-20T18:04:11.000Z']]]" in events_script("System", [], 5, "2026-09-20T18:04:11Z")


def test_a_bad_window_is_refused_before_the_machine_is_asked():
    bridge = FakeBridge()
    with pytest.raises(ValueError, match="since"):
        asyncio.run(take("events", bridge, {"since": "yesterday"}))
    assert bridge.scripts == []


def test_record_stamp_is_utc_milliseconds():
    assert _utc_stamp("2026-09-20T18:04:11.204Z") == "2026-09-20T18:04:11.204Z"
    assert _utc_stamp("2026-09-20T20:04:11+02:00") == "2026-09-20T18:04:11.000Z"
    with pytest.raises(ValueError):
        _utc_stamp("not-a-date")


def test_record_script_filters_before_the_moment():
    s = record_script("System", "2026-09-20T18:04:11Z", 20)
    assert "@SystemTime&lt;'2026-09-20T18:04:11.000Z'" in s and "-MaxEvents 21" in s


def test_take_events_through_a_fake_bridge():
    bridge = FakeBridge()
    r = asyncio.run(take("events", bridge, {"count": "3"}))
    assert r.outcome == "ok" and r.count == 1
    assert r.params == {"log": "System", "levels": [1, 2], "count": 3, "since": ""}
    assert "-MaxEvents 4" in bridge.scripts[0]


def test_a_time_window_reports_an_exact_record_cutoff_and_retains_only_the_requested_rows():
    rows = [{"Id": number} for number in (4, 3, 2, 1)]  # Windows returns newest first
    bridge = FakeBridge(BridgeResult("ok", items=rows))
    reading = asyncio.run(take("events", bridge, {"count": 3, "since": "boot"}))
    assert [row["Id"] for row in reading.section("records").data] == [4, 3, 2]
    assert reading.count == 3 and reading.section("collection").data == {"limit": 3, "returned": 3, "truncated": True}
    assert any("older matching records" in warning for warning in reading.warnings)

    exactly = asyncio.run(take("events", FakeBridge(BridgeResult("ok", items=rows[:3])), {"count": 3, "since": "boot"}))
    assert exactly.section("collection").data["truncated"] is False and exactly.warnings == []
    recent = asyncio.run(take("events", bridge, {"count": 3}))
    assert recent.section("collection").data["truncated"] is True and recent.warnings == []

    empty = asyncio.run(take("events", FakeBridge(BridgeResult("empty")), {"count": 3, "since": "boot"}))
    assert empty.outcome == "empty" and empty.section("collection").data == {"limit": 3, "returned": 0, "truncated": False}
    failed = asyncio.run(take("events", FakeBridge(BridgeResult("timeout")), {"count": 3, "since": "boot"}))
    assert failed.count is None and failed.sections == []


def test_take_record_reverses_to_oldest_first_and_rejects_bad_timestamps():
    bridge = FakeBridge(BridgeResult("ok", items=[{"Id": 3}, {"Id": 2}, {"Id": 1}]))
    r = asyncio.run(take("record", bridge, {"before": "2026-09-20T18:04:11Z"}))
    assert [x["Id"] for x in r.section("records").data] == [1, 2, 3]
    with pytest.raises(ValueError):
        asyncio.run(take("record", bridge, {"before": "yesterday"}))

    capped = asyncio.run(take("record", bridge, {"before": "2026-09-20T18:04:11Z", "count": 2}))
    assert [row["Id"] for row in capped.section("records").data] == [2, 3]
    assert capped.section("collection").data == {"limit": 2, "returned": 2, "truncated": True}


def test_unknown_reading_is_a_key_error():
    with pytest.raises(KeyError):
        asyncio.run(take("nope", FakeBridge(), {}))


def test_param_dataclass_is_frozen():
    p = Param("x", "int", 1, "d")
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.default = 2  # type: ignore[misc]
