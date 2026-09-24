"""The envelope: every bridge outcome maps to a reading; parameters are coerced; the catalog is one table."""

import asyncio
import dataclasses

import pytest

from sentinel import readings  # noqa: F401
from sentinel.bridge import BridgeResult
from sentinel.reading import REGISTRY, Param, Section, Spec, from_bridge, from_object, take
from sentinel.readings.events import _utc_stamp, events_script, record_script, since_clause
from sentinel.stack import _outcome_text
from tests.conftest import FakeBridge, log_collector_result


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


def test_busy_bridge_keeps_the_unavailable_outcome_and_explains_its_own_queue():
    detail = "Sentinel's bridge was busy: no bridge session came free within 1s; this attempt could not reach Windows"
    reading = from_bridge("events", {}, "q", BridgeResult("unavailable", error=detail, cause="busy")).to_dict()
    assert reading["outcome"] == "unavailable" and reading["error"] == {"kind": "busy", "detail": detail}
    assert "could not get an answer" in _outcome_text(reading)
    old = from_bridge("events", {}, "q", BridgeResult("unavailable", error="no bridge session came free within 1s")).to_dict()
    assert "could not get an answer" in _outcome_text(old)
    assert "local performance history" in _outcome_text({"outcome": "unavailable", "error": {"kind": "local_store"}})


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
    assert spec.coerce({}) == {"log": "System", "levels": [1, 2], "count": 50, "since": "", "before": "", "order": "newest"}
    assert spec.coerce({"levels": "1,2,3", "count": "5", "log": "Application"}) == {"log": "Application", "levels": [1, 2, 3], "count": 5, "since": "", "before": "", "order": "newest"}
    with pytest.raises(ValueError):
        spec.coerce({"log": "Security"})
    with pytest.raises(ValueError):
        spec.coerce({"count": "many"})
    assert spec.coerce({"count": None})["count"] == 50


def test_numeric_bounds_are_shared_by_the_catalog_and_all_callers():
    for spec in REGISTRY.values():
        for param in spec.params:
            if param.default is not None and param.minimum is not None:
                assert param.default >= param.minimum, (spec.name, param.name)
            if param.default is not None and param.maximum is not None:
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


def test_every_automatic_reading_has_parameters_bulk_callers_can_supply():
    from datetime import UTC, datetime

    from sentinel.reading import automatic_params

    at = datetime(2026, 9, 20, tzinfo=UTC)
    for name, spec in REGISTRY.items():
        if spec.requires_selection:
            with pytest.raises(ValueError, match="requires an exact selection"):
                automatic_params(name, at)
        else:
            spec.coerce(automatic_params(name, at))


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
    assert "-FilterXml ([xml]$xml)" in s and "-MaxEvents 8" in s and "-ErrorAction Stop" in s
    assert "Read-LogMetadata 'Application'" in s
    assert "FilterHashtable" not in s  # StartTime there does not honour a timestamp's Kind


def test_a_window_is_a_clause_against_the_index_and_boot_is_resolved_on_the_machine():
    assert since_clause("") == ("", "")
    prelude, clause = since_clause("2026-09-20T18:04:11Z")
    assert prelude == "" and clause == " and TimeCreated[@SystemTime&gt;='2026-09-20T18:04:11.000Z']"
    prelude, clause = since_clause("BOOT")
    assert "LastBootUpTime" in prelude and "AddTicks(-($boot.Ticks % 10000))" in prelude and clause == " and TimeCreated[@SystemTime&gt;='$since']"
    with pytest.raises(ValueError, match="'boot'"):
        since_clause("the other day")


def test_events_since_a_moment_and_since_boot():
    s = events_script("System", [1, 2], 5, "2026-09-20T18:04:11Z")
    assert "*[System[(Level=1 or Level=2) and TimeCreated[@SystemTime&gt;='2026-09-20T18:04:11.000Z']]]" in s
    boot = events_script("System", [1, 2], 5, "boot")
    assert "$boot = (Get-CimInstance Win32_OperatingSystem" in boot and "AddTicks(-($boot.Ticks % 10000))" in boot
    assert "TimeCreated[@SystemTime&gt;='$xpathStart']" in boot and "$fromTicks = $boot.Ticks" in boot
    assert '@"' in boot  # an expanding here-string: $xpathStart is the machine's answer, not a literal


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
    assert "@SystemTime&lt;'2026-09-20T18:04:11.002Z'" in s and "Select-Object -First 21" in s
    assert "Where-Object" in s and "-MaxEvents 21" not in s


def test_take_events_through_a_fake_bridge():
    bridge = FakeBridge(log_collector_result([{"RecordId": 1, "Id": 41}], limit=3))
    r = asyncio.run(take("events", bridge, {"count": "3"}))
    assert r.outcome == "ok" and r.count == 1
    assert r.params == {"log": "System", "levels": [1, 2], "count": 3, "since": "", "before": "", "order": "newest"}
    assert "-MaxEvents 4" in bridge.scripts[0]


def test_a_time_window_reports_an_exact_record_cutoff_and_retains_only_the_requested_rows():
    rows = [{"RecordId": number, "Id": number, "TimeCreated": f"2026-09-20T{number:02d}:00:00.000Z"} for number in (4, 3, 2, 1)]  # Windows returns newest first
    bridge = FakeBridge(log_collector_result(rows, limit=3))
    reading = asyncio.run(take("events", bridge, {"count": 3, "since": "boot"}))
    assert [row["Id"] for row in reading.section("records").data] == [4, 3, 2]
    assert reading.count == 3 and reading.section("collection").data["truncated"] is True
    assert reading.section("coverage").data["covered_from"] == rows[2]["TimeCreated"]
    assert reading.section("coverage").data["complete"] is False
    assert any("older matching records" in warning for warning in reading.warnings)

    exactly = asyncio.run(take("events", FakeBridge(log_collector_result(rows[:3], limit=3)), {"count": 3, "since": "boot"}))
    assert exactly.section("collection").data["truncated"] is False and exactly.warnings == []
    recent = asyncio.run(take("events", bridge, {"count": 3}))
    assert recent.section("collection").data["truncated"] is True and recent.warnings == []

    empty = asyncio.run(take("events", FakeBridge(log_collector_result([], limit=3)), {"count": 3, "since": "boot"}))
    assert empty.outcome == "empty" and empty.section("collection").data["returned"] == 0 and empty.section("collection").data["truncated"] is False
    failed = asyncio.run(take("events", FakeBridge(BridgeResult("timeout")), {"count": 3, "since": "boot"}))
    assert failed.count is None and failed.sections == []


def test_oldest_system_events_keep_the_first_rows_and_bound_later_reach():
    moment = "2026-09-20T10:00:00.1234567Z"
    rows = [
        {"RecordId": 10, "TimeCreated": moment},
        {"RecordId": 11, "TimeCreated": "2026-09-20T10:00:00.1234568Z"},
    ]
    result = log_collector_result(rows, limit=2, window_start=moment, queried_at="2026-09-21T00:00:00.0000000Z")
    result.items[0].update(truncated=True, probe_time="2026-09-20T10:00:00.1234569Z")
    reading = asyncio.run(take("events", FakeBridge(result), {"since": moment, "levels": [], "order": "oldest", "count": 2}))
    assert reading.outcome == "ok" and reading.section("records").data == rows
    assert reading.section("collection").data["order"] == "oldest"
    reach = reading.section("coverage").data
    assert reach["covered_from"] == moment and reach["covered_from_inclusive"] is True
    assert reach["covered_until"] == "2026-09-20T10:00:00.1234569Z" and reach["complete"] is False
    assert reach["returned_time_ordered"] is True
    assert "-Oldest" in reading.method["query"] and "Level=" not in reading.method["query"]
    assert any("later matching records" in warning for warning in reading.warnings)


def test_oldest_system_events_keep_inverted_rows_without_claiming_time_reach():
    moment = "2026-09-20T10:00:00.0000000Z"
    rows = [
        {"RecordId": 10, "TimeCreated": "2026-09-20T10:00:02.0000000Z"},
        {"RecordId": 11, "TimeCreated": "2026-09-20T10:00:01.0000000Z"},
    ]
    result = log_collector_result(rows, limit=2, window_start=moment)
    result.items[0]["probe_time"] = None
    reading = asyncio.run(take("events", FakeBridge(result), {"since": moment, "levels": [], "order": "oldest", "count": 2}))
    assert reading.section("records").data == rows
    assert reading.section("coverage").data["returned_time_ordered"] is False
    assert reading.section("coverage").data["covered_until"] is None
    assert any("System record times moved backward" in warning for warning in reading.warnings)


def test_take_record_reverses_to_oldest_first_and_rejects_bad_timestamps():
    rows = [{"RecordId": number, "Id": number} for number in (3, 2, 1)]
    bridge = FakeBridge(log_collector_result(rows, limit=50))
    r = asyncio.run(take("record", bridge, {"before": "2026-09-20T18:04:11Z"}))
    assert [x["Id"] for x in r.section("records").data] == [1, 2, 3]
    with pytest.raises(ValueError):
        asyncio.run(take("record", bridge, {"before": "yesterday"}))

    capped = asyncio.run(take("record", FakeBridge(log_collector_result(rows, limit=2)), {"before": "2026-09-20T18:04:11Z", "count": 2}))
    assert [row["Id"] for row in capped.section("records").data] == [2, 3]
    assert capped.section("collection").data["truncated"] is True


def test_empty_window_before_retention_is_not_a_quiet_machine():
    result = log_collector_result([], limit=5, oldest="2026-07-01T00:00:00.0000000Z", window_start="2026-03-01T00:00:00.000Z")
    reading = asyncio.run(take("events", FakeBridge(result), {"since": "2026-03-01T00:00:00Z", "count": 5}))
    assert reading.outcome == "empty" and reading.count == 0
    reach = reading.section("coverage").data
    assert reach["log"] == "System" and reach["retained_from"] == "2026-07-01T00:00:00.0000000Z"
    assert reach["covered_from"] == reach["retained_from"] and reach["complete"] is False
    assert any("oldest retained record" in warning for warning in reading.warnings)


def test_retention_must_begin_strictly_before_the_requested_start():
    start = "2026-09-20T00:00:00.0000000Z"
    exact = log_collector_result([], limit=5, oldest=start, window_start=start)
    at_boundary = asyncio.run(take("events", FakeBridge(exact), {"since": start, "count": 5}))
    assert at_boundary.section("coverage").data["complete"] is False
    earlier = log_collector_result([], limit=5, oldest="2026-09-19T23:59:59.9999999Z", window_start=start)
    reached = asyncio.run(take("events", FakeBridge(earlier), {"since": start, "count": 5}))
    assert reached.section("coverage").data["complete"] is True


def test_a_future_start_cannot_establish_a_complete_window_even_when_the_log_is_healthy():
    result = log_collector_result([], limit=5, window_start="2026-10-01T00:00:00.000Z", queried_at="2026-09-21T00:00:00.000Z")
    reading = asyncio.run(take("events", FakeBridge(result), {"since": "2026-10-01T00:00:00Z", "count": 5}))
    assert reading.outcome == "empty" and reading.section("coverage").data["complete"] is False
    assert any("window completeness" in warning for warning in reading.warnings)


def test_events_exclusive_end_normalizes_offsets_and_refuses_an_empty_window_before_querying():
    script = events_script("System", [1, 2], 5, "2026-09-20T10:00:00+02:00", "2026-09-20T11:00:00+02:00")
    assert "@SystemTime&gt;='2026-09-20T08:00:00.000Z'" in script
    assert "@SystemTime&lt;'2026-09-20T09:00:00.002Z'" in script
    assert "window_end = '2026-09-20T09:00:00.0000000Z'" in script
    bridge = FakeBridge()
    for before in ("2026-09-20T08:00:00Z", "2026-09-20T09:00:00"):
        with pytest.raises(ValueError, match="before"):
            asyncio.run(take("events", bridge, {"since": "2026-09-20T08:00:00Z", "before": before}))
    assert "Select-Object -First 6" in events_script("System", [], 5, "2026-09-20T08:00:00Z", "2026-09-20T08:00:00.0000001Z")
    assert bridge.scripts == []
    boot_script = events_script("System", [], 5, "boot", "2026-09-20T09:00:00Z")
    assert "Win32_OperatingSystem" in boot_script and "@SystemTime&gt;='$xpathStart'" in boot_script
    assert "@SystemTime&lt;'2026-09-20T09:00:00.002Z'" in boot_script


def test_window_end_reports_observed_reach_without_completing_unobserved_future():
    start = "2026-09-20T00:00:00.000Z"
    now = "2026-09-21T00:00:00.000Z"
    future = "2026-09-22T00:00:00.000Z"
    observed = log_collector_result([], limit=5, window_start=start, window_end=now, queried_at=now)
    complete = asyncio.run(take("events", FakeBridge(observed), {"since": start, "before": now, "count": 5}))
    assert complete.section("coverage").data["complete"] is True
    assert complete.section("coverage").data["covered_until"] == now

    pending = log_collector_result([], limit=5, window_start=start, window_end=future, queried_at=now)
    reading = asyncio.run(take("events", FakeBridge(pending), {"since": start, "before": future, "count": 5}))
    assert reading.section("coverage").data["covered_until"] == now
    assert reading.section("coverage").data["complete"] is False
    assert any("after the machine's query time" in warning for warning in reading.warnings)

    pending.items[0].pop("queried_at")
    unknown = asyncio.run(take("events", FakeBridge(pending), {"since": start, "before": future, "count": 5}))
    assert unknown.section("coverage").data["covered_until"] is None
    assert unknown.section("coverage").data["complete"] is None


def test_outside_window_event_remains_raw_and_cannot_prove_reach():
    start, end = "2026-09-20T00:00:00.000Z", "2026-09-21T00:00:00.000Z"
    outlier = {"RecordId": 7, "TimeCreated": end}
    result = log_collector_result([outlier], limit=5, window_start=start, window_end=end, queried_at=end)
    reading = asyncio.run(take("events", FakeBridge(result), {"since": start, "before": end, "count": 5}))
    assert reading.section("records").data == [outlier]
    assert reading.section("collection").data["row_issues"]["outside_window"] == 1
    assert reading.section("coverage").data["covered_from"] == start
    assert reading.section("coverage").data["complete"] is False


def test_window_entirely_after_query_time_names_time_as_the_gap():
    start, end, observed = "2026-09-23T00:00:00.000Z", "2026-09-24T00:00:00.000Z", "2026-09-22T00:00:00.000Z"
    result = log_collector_result([], limit=5, window_start=start, window_end=end, queried_at=observed)
    reading = asyncio.run(take("events", FakeBridge(result), {"since": start, "before": end, "count": 5}))
    assert reading.section("coverage").data["complete"] is False
    assert reading.section("coverage").data["covered_until"] is None
    assert any("start is at or after the machine's query time" in warning for warning in reading.warnings)
    assert not any("retention reach could not" in warning for warning in reading.warnings)


def test_record_boundary_outlier_keeps_raw_evidence_without_a_completeness_claim():
    moment = "2026-09-20T18:04:11.000Z"
    row = {"RecordId": 9, "TimeCreated": moment}
    result = log_collector_result([row], limit=5, window_start=None, window_end=moment)
    reading = asyncio.run(take("record", FakeBridge(result), {"before": moment, "count": 5}))
    assert reading.section("records").data == [row]
    assert reading.section("coverage").data["reaches_before"] is True
    assert any("outside the requested window" in warning for warning in reading.warnings)
    assert not any("completeness" in warning for warning in reading.warnings)


def test_recent_records_show_retention_without_claiming_a_complete_window():
    result = log_collector_result([{"RecordId": 7, "TimeCreated": "2026-09-20T12:00:00.000Z"}], limit=5)
    reading = asyncio.run(take("events", FakeBridge(result), {"count": 5}))
    reach = reading.section("coverage").data
    assert reach["retained_from"] == "2026-01-01T00:00:00.0000000Z"
    assert reach["covered_from"] is None and reach["complete"] is None
    assert reading.warnings == []


def test_metadata_failure_does_not_erase_returned_records_or_claim_complete_reach():
    row = {"RecordId": 7, "Id": 41, "TimeCreated": "2026-09-20T12:00:00.000Z"}
    result = log_collector_result([row], limit=5)
    result.items[0]["metadata"] = {"log": "System", "log_state": "denied", "oldest_state": "denied"}
    reading = asyncio.run(take("events", FakeBridge(result), {"since": "2026-09-20T00:00:00Z", "count": 5}))
    assert reading.outcome == "ok" and reading.section("records").data == [row]
    assert reading.section("coverage").data["retained_from"] is None
    assert reading.section("coverage").data["covered_from"] is None
    assert any("retention reach could not be established" in warning for warning in reading.warnings)


def test_partial_query_keeps_its_returned_prefix_and_marks_the_unseen_tail():
    row = {"RecordId": 7, "Id": 41, "TimeCreated": "2026-09-20T12:00:00.000Z"}
    result = log_collector_result([row], limit=5)
    result.items[0]["truncated"] = None
    result.items[0]["stopped"] = {"kind": "failed", "detail": "synthetic interruption"}
    reading = asyncio.run(take("events", FakeBridge(result), {"since": "2026-09-20T00:00:00Z", "count": 5}))
    assert reading.outcome == "ok" and reading.section("records").data == [row]
    assert reading.section("collection").data["stopped"]["kind"] == "failed"
    assert reading.section("coverage").data["covered_from"] == row["TimeCreated"]
    assert reading.section("coverage").data["complete"] is False
    assert any("stopped early" in warning for warning in reading.warnings)


@pytest.mark.parametrize("mutation", [
    "returned_count", "wrong_log", "two_objects", "stopped_with_truncated", "truncated_without_full_page",
    "empty_with_rows", "ok_with_error", "missing_record_id", "zero_record_id", "malformed_stopped",
])
def test_invalid_log_collector_shape_never_becomes_observed_evidence(mutation):
    result = log_collector_result([{"RecordId": 7, "TimeCreated": "2026-09-20T12:00:00.000Z"}], limit=5)
    if mutation == "returned_count":
        result.items[0]["returned"] = 2
    elif mutation == "wrong_log":
        result.items[0]["log"] = "Application"
    elif mutation == "stopped_with_truncated":
        result.items[0]["stopped"] = {"kind": "failed", "detail": "interrupted"}
    elif mutation == "truncated_without_full_page":
        result.items[0]["truncated"] = True
    elif mutation == "empty_with_rows":
        result.items[0]["outcome"] = "empty"
    elif mutation == "ok_with_error":
        result.items[0]["error"] = "unexpected error"
    elif mutation == "missing_record_id":
        del result.items[0]["records"][0]["RecordId"]
    elif mutation == "zero_record_id":
        result.items[0]["records"][0]["RecordId"] = 0
    elif mutation == "malformed_stopped":
        result.items[0]["stopped"] = {"kind": "failed", "detail": ""}
        result.items[0]["truncated"] = None
    else:
        result.items.append(result.items[0].copy())
    reading = asyncio.run(take("events", FakeBridge(result), {"count": 5}))
    assert reading.outcome == "failed" and reading.count is None and reading.sections == []


@pytest.mark.parametrize("outcome", ["failed", "denied"])
def test_log_collector_source_failure_keeps_its_outcome_and_no_evidence(outcome):
    result = log_collector_result([], limit=5)
    result.items[0]["outcome"] = outcome
    result.items[0]["error"] = "synthetic query failure"
    reading = asyncio.run(take("events", FakeBridge(result), {"count": 5}))
    assert reading.outcome == outcome and reading.count is None and reading.sections == []
    assert reading.error == {"kind": outcome, "detail": "synthetic query failure"}


def test_failed_log_collector_for_another_log_is_rejected():
    result = log_collector_result([], limit=5)
    result.items[0].update(outcome="failed", log="Application", error="synthetic query failure")
    reading = asyncio.run(take("events", FakeBridge(result), {"count": 5}))
    assert reading.outcome == "failed" and reading.sections == []
    assert "invalid query result" in reading.error["detail"]


def test_record_before_retained_history_reports_the_boundary():
    result = log_collector_result([], limit=5, oldest="2026-07-01T00:00:00.0000000Z", window_start=None)
    reading = asyncio.run(take("record", FakeBridge(result), {"before": "2026-03-01T00:00:00Z", "count": 5}))
    assert reading.outcome == "empty" and reading.section("coverage").data["reaches_before"] is False
    assert any("earlier records are unavailable" in warning for warning in reading.warnings)


def test_returned_record_proves_reach_even_if_log_wrapped_before_metadata_check():
    row = {"RecordId": 7, "TimeCreated": "2026-03-01T00:00:00.000Z"}
    result = log_collector_result([row], limit=5, oldest="2026-07-01T00:00:00.0000000Z", window_start=None)
    reading = asyncio.run(take("record", FakeBridge(result), {"before": "2026-04-01T00:00:00Z", "count": 5}))
    assert reading.outcome == "ok" and reading.section("coverage").data["reaches_before"] is True
    assert reading.section("records").data == [row]
    assert any("may have wrapped" in warning for warning in reading.warnings)


def test_unknown_reading_is_a_key_error():
    with pytest.raises(KeyError):
        asyncio.run(take("nope", FakeBridge(), {}))


def test_param_dataclass_is_frozen():
    p = Param("x", "int", 1, "d")
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.default = 2  # type: ignore[misc]
