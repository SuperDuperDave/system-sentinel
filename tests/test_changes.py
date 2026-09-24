"""A change timeline must distinguish a logged result from a gap in one of its sources."""

from __future__ import annotations

import pytest

from sentinel.bridge import BridgeResult
from sentinel.readings.changes import _change, changes_script, take_changes
from sentinel.redact import redact

BEFORE = "2026-09-22T00:00:00Z"
START = "2026-09-21T00:00:00Z"
LOGS = {
    "windows_update": ("System", "Microsoft-Windows-WindowsUpdateClient", 19),
    "device_configuration": ("Microsoft-Windows-Kernel-PnP/Configuration", "Microsoft-Windows-Kernel-PnP", 400),
    "msi": ("Application", "MsiInstaller", 1033),
}


def row(source: str, record_id: int, at: str, data: dict, *, event_id: int | None = None, version: int | None = None) -> dict:
    log, provider, default_id = LOGS[source]
    return {
        "Log": log, "RecordId": record_id, "Id": event_id or default_id, "ProviderName": provider,
        "Version": version if version is not None else (0 if source == "msi" else 1), "Level": 4,
        "TimeCreated": at, "Data": data, "FieldCount": len(data), "OmittedFieldCount": 0, "ProjectionError": None,
    }


def source(name: str, records: list[dict] | None = None, *, outcome: str | None = None, truncated: bool = False, oldest: str = "2026-09-20T00:00:00Z") -> dict:
    records = records or []
    log = LOGS[name][0]
    return {
        "name": name, "log": log, "outcome": outcome or ("ok" if records else "empty"),
        "error": "synthetic source failure" if outcome in ("failed", "denied") else None,
        "returned": len(records), "limit": 3, "truncated": truncated, "records": records,
        "log_enabled": True, "log_mode": "Circular", "log_state": "ok", "log_error": None,
        "log_oldest": oldest, "oldest_state": "ok", "oldest_error": None,
    }


class FakeBridge:
    def __init__(self, sources: list[dict]):
        self.sources = sources

    def run(self, script: str, *, depth: int) -> BridgeResult:
        assert depth >= 8 and "Get-WinEvent" in script
        return BridgeResult("ok", items=[{"window_start": START, "window_end": BEFORE, "queried_at": BEFORE, "sources": self.sources}])


def take(sources: list[dict]):
    return take_changes(FakeBridge(sources), {"before": BEFORE, "hours": 24, "count": 3})


def take_payload(payload: dict, *, before: str = BEFORE, hours: int = 24):
    bridge = FakeBridge(payload["sources"])
    bridge.run = lambda script, *, depth: BridgeResult("ok", items=[payload])
    return take_changes(bridge, {"before": before, "hours": hours, "count": 3})


def test_three_source_results_keep_exact_events_and_explain_what_they_mean():
    update = row("windows_update", 14, "2026-09-21T20:00:00Z", {"updateTitle": "Synthetic update (KB1234567)"})
    device = row("device_configuration", 14, "2026-09-21T20:00:01Z", {"DriverName": "oem9.inf", "DriverVersion": "1.2", "DriverProvider": "Example", "DeviceUpdated": "false"})
    failed_msi = row("msi", 8, "2026-09-21T20:00:02Z", {"[0]": "Example app", "[1]": "2.0", "[2]": "1033", "[3]": "1603", "[4]": "Example"})
    reading = take([source("windows_update", [update]), source("device_configuration", [device]), source("msi", [failed_msi])])
    assert reading.outcome == "ok" and reading.count == 3
    assert [item["ref"] for item in reading.section("changes").data] == [
        {"log": "System", "record_id": 14},
        {"log": "Microsoft-Windows-Kernel-PnP/Configuration", "record_id": 14},
        {"log": "Application", "record_id": 8},
    ]
    update_change, device_change, msi_change = reading.section("changes").data
    assert update_change["kind"] == "update_installed" and update_change["kb"] == "KB1234567"
    assert device_change["kind"] == "device_configured" and device_change["device_updated"] is False
    assert device_change["subject"] == "oem9.inf" and device_change["publisher"] == "Example"
    assert msi_change["kind"] == "msi_install_failed" and msi_change["status"] == 1603 and msi_change["publisher"] == "Example"
    assert "caused" in reading.section("changes").basis


def test_failure_and_retention_are_not_an_observed_absence():
    reading = take([source("windows_update"), source("device_configuration", outcome="failed"), source("msi")])
    assert reading.outcome == "failed" and reading.count is None
    assert reading.section("collection").data["device_configuration"]["outcome"] == "failed"
    assert any("device_configuration did not answer" in warning for warning in reading.warnings)

    empty = take([source("windows_update"), source("device_configuration", oldest="2026-09-21T12:00:00Z"), source("msi")])
    assert empty.outcome == "empty" and empty.count == 0
    assert empty.section("coverage").data["device_configuration"] == {"covered_from": "2026-09-21T12:00:00Z", "covered_from_inclusive": False, "covered_until": BEFORE, "complete": False}
    assert any("does not cover the whole requested window" in warning for warning in empty.warnings)

    at_boundary = take([source("windows_update", oldest=START), source("device_configuration"), source("msi")])
    assert at_boundary.section("coverage").data["windows_update"] == {"covered_from": START, "covered_from_inclusive": False, "covered_until": BEFORE, "complete": False}

    no_metadata = source("windows_update")
    no_metadata["log_state"] = "failed"
    unknown = take([no_metadata, source("device_configuration"), source("msi")])
    assert unknown.section("coverage").data["windows_update"]["covered_from"] is None
    assert any("windows_update log coverage could not be established" in warning for warning in unknown.warnings)


def test_truncation_and_bad_collector_rows_are_visible():
    latest = row("windows_update", 10, "2026-09-21T23:00:00Z", {"updateTitle": "Synthetic update"})
    limited = source("windows_update", [latest], truncated=True)
    limited["limit"] = 1
    reading = take_changes(FakeBridge([limited, source("device_configuration"), source("msi")]), {"before": BEFORE, "hours": 24, "count": 1})
    assert reading.outcome == "ok" and reading.count == 1
    assert reading.section("coverage").data["windows_update"] == {"covered_from": "2026-09-21T23:00:00Z", "covered_from_inclusive": False, "covered_until": BEFORE, "complete": False}
    assert any("reached its 1-record limit" in warning for warning in reading.warnings)

    leaked = row("device_configuration", 1, "2026-09-21T10:00:00Z", {"DeviceInstanceId": "USB\\VID_0000\\SERIAL123"})
    bad = take([source("windows_update"), source("device_configuration", [leaked]), source("msi")])
    assert bad.outcome == "failed" and bad.count is None
    assert bad.section("collection").data["device_configuration"]["outcome"] == "failed"
    redacted, _ = redact(bad.to_dict())
    assert "SERIAL123" not in str(redacted)

    wrong_count = source("windows_update", [latest])
    wrong_count["returned"] = 2
    assert take([wrong_count, source("device_configuration"), source("msi")]).outcome == "failed"


def test_unsupported_layout_stays_raw_with_an_error():
    unsupported = row("msi", 7, "2026-09-21T12:00:00Z", {"[0]": "Example"}, version=1)
    change = _change(unsupported)
    assert change["kind"] == "unmapped_event" and change["error"]
    assert change["fields"] == {"[0]": "Example"}


def test_failed_update_and_msi_removal_are_distinct_from_successful_changes():
    failed_update = row("windows_update", 6, "2026-09-21T12:00:00Z", {"updateTitle": "Synthetic update", "errorCode": "0x80070002"}, event_id=20)
    removed = row("msi", 7, "2026-09-21T12:01:00Z", {"[0]": "Example", "[1]": "1.0", "[2]": "1033", "[3]": "3010", "[4]": "Example"}, event_id=1034)
    assert _change(failed_update)["kind"] == "update_failed"
    assert _change(failed_update)["error_code"] == "0x80070002"
    result = _change(removed)
    assert result["kind"] == "msi_removal_succeeded" and result["succeeded"] is True and result["status"] == 3010
    assert result["restart"] == "required"
    initiated = row("msi", 8, "2026-09-21T12:02:00Z", {"[0]": "Example", "[1]": "1.0", "[2]": "1033", "[3]": "1641", "[4]": "Example"})
    assert _change(initiated)["restart"] == "initiated"


def test_seven_digit_utc_fractions_do_not_invent_a_retention_gap():
    before = "2026-09-22T00:00:00.123Z"
    start = "2026-09-21T00:00:00.1230000Z"
    oldest = "2026-09-20T00:00:00.1234567Z"
    bridge = FakeBridge([source(name, oldest=oldest) for name in LOGS])
    bridge.run = lambda script, *, depth: BridgeResult("ok", items=[{"window_start": start, "window_end": before, "queried_at": before, "sources": bridge.sources}])
    reading = take_changes(bridge, {"before": before, "hours": 24, "count": 3})
    assert reading.outcome == "empty" and not reading.warnings
    assert all(reach == {"covered_from": start, "covered_from_inclusive": True, "covered_until": before, "complete": True} for reach in reading.section("coverage").data.values())
    assert all("covered_from" not in raw for raw in reading.section("collection").data.values() if isinstance(raw, dict))


def test_seventh_digit_changes_reach_and_rejects_a_row_before_the_window():
    before = "2026-09-22T00:00:00.123Z"
    start = "2026-09-21T00:00:00.1230000Z"
    oldest = "2026-09-21T00:00:00.1229999Z"
    bridge = FakeBridge([source(name, oldest=oldest) for name in LOGS])
    bridge.run = lambda script, *, depth: BridgeResult("ok", items=[{"window_start": start, "window_end": before, "queried_at": before, "sources": bridge.sources}])
    reading = take_changes(bridge, {"before": before, "hours": 24, "count": 3})
    assert reading.section("coverage").data["windows_update"] == {"covered_from": start, "covered_from_inclusive": True, "covered_until": before, "complete": True}

    old_row = row("windows_update", 2, oldest, {"updateTitle": "Synthetic update"})
    bridge.sources[0] = source("windows_update", [old_row], oldest=oldest)
    outside = take_changes(bridge, {"before": before, "hours": 24, "count": 3})
    assert outside.section("records").data == [old_row]
    assert outside.section("collection").data["windows_update"]["row_issues"]["outside_window"] == 1
    assert outside.section("coverage").data["windows_update"]["complete"] is False
    assert any("outside the requested window" in warning for warning in outside.warnings)

    later_oldest = "2026-09-21T00:00:00.1230001Z"
    bridge.sources[0] = source("windows_update", oldest=later_oldest)
    limited = take_changes(bridge, {"before": before, "hours": 24, "count": 3})
    assert limited.section("coverage").data["windows_update"] == {"covered_from": later_oldest, "covered_from_inclusive": False, "covered_until": before, "complete": False}


@pytest.mark.parametrize("metadata_key, metadata_value", [("log_enabled", False), ("log_mode", "AutoBackup"), ("oldest_state", "failed")])
def test_an_empty_source_without_retention_evidence_is_not_complete(metadata_key: str, metadata_value: object):
    uncertain = source("windows_update")
    uncertain[metadata_key] = metadata_value
    reading = take([uncertain, source("device_configuration"), source("msi")])
    assert reading.outcome == "empty"
    assert reading.section("coverage").data["windows_update"] == {"covered_from": None, "covered_from_inclusive": None, "covered_until": None, "complete": False}
    assert any("windows_update log coverage could not be established" in warning for warning in reading.warnings)


def test_a_failed_source_has_unknown_reach_and_surviving_records_remain_useful():
    install = row("msi", 3, "2026-09-21T12:00:00Z", {"[0]": "Example", "[1]": "1.0", "[2]": "1033", "[3]": "0", "[4]": "Example"})
    reading = take([source("windows_update", outcome="denied"), source("device_configuration"), source("msi", [install])])
    assert reading.outcome == "ok" and reading.count == 1
    assert reading.section("coverage").data["windows_update"] == {"covered_from": None, "covered_from_inclusive": None, "covered_until": None, "complete": None}
    assert any("windows_update did not answer" in warning for warning in reading.warnings)


def test_duplicate_source_results_and_projection_errors_never_claim_a_clean_history():
    duplicated = take([source("windows_update"), source("windows_update"), source("device_configuration"), source("msi")])
    assert duplicated.outcome == "failed" and duplicated.count is None
    assert duplicated.section("collection").data["windows_update"]["outcome"] == "failed"

    projected = row("windows_update", 9, "2026-09-21T12:00:00Z", {})
    projected["ProjectionError"] = "the event data could not be projected"
    reading = take([source("windows_update", [projected]), source("device_configuration"), source("msi")])
    assert reading.outcome == "ok" and reading.count == 1
    assert reading.section("changes").data[0]["kind"] == "unmapped_event"
    assert reading.section("changes").data[0]["error"] == projected["ProjectionError"]


def test_window_and_count_are_bounded_in_the_log_query():
    script = changes_script(BEFORE, 24, 3)
    assert "$until = $until.AddTicks(-($until.Ticks % 10000))" in script
    assert "[datetimeoffset]::Parse('2026-09-22T00:00:00.000Z', [Globalization.CultureInfo]::InvariantCulture)" in script
    assert script.count("Get-WinEvent -FilterXml") == 1  # one source query inside a three-source loop
    assert "Microsoft-Windows-Kernel-PnP/Configuration" in script
    assert "(EventID=19 or EventID=20)" in script and "(EventID=1033 or EventID=1034)" in script
    assert "@SystemTime&gt;='$startIso' and @SystemTime&lt;'$endIso'" in script
    assert "$queriedAt = (Get-Date).ToUniversalTime().ToString('o')" in script
    for before, hours, count in (("not a time", 24, 3), ("1601-01-01T00:00:00Z", 1, 3), (BEFORE, 0, 3), (BEFORE, 24, 501)):
        with pytest.raises(ValueError):
            changes_script(before, hours, count)


def test_changes_future_end_does_not_claim_a_complete_requested_window():
    bridge = FakeBridge([source(name) for name in LOGS])
    bridge.run = lambda script, *, depth: BridgeResult("ok", items=[{
        "window_start": START, "window_end": BEFORE, "queried_at": "2026-09-21T12:00:00Z", "sources": bridge.sources,
    }])
    reading = take_changes(bridge, {"before": BEFORE, "hours": 24, "count": 3})
    assert all(reach["complete"] is False and reach["covered_until"] == "2026-09-21T12:00:00Z"
               for reach in reading.section("coverage").data.values())
    assert any("after the machine's query time" in warning for warning in reading.warnings)

    bridge.run = lambda script, *, depth: BridgeResult("ok", items=[{
        "window_start": START, "window_end": BEFORE, "sources": bridge.sources,
    }])
    unknown = take_changes(bridge, {"before": BEFORE, "hours": 24, "count": 3})
    assert all(reach["complete"] is None and reach["covered_until"] is None
               for reach in unknown.section("coverage").data.values())


def test_a_wrong_echo_cannot_make_an_empty_changes_window_look_complete():
    payload = {"window_start": START, "window_end": "2026-09-22T00:00:00.001Z",
               "queried_at": "2026-09-22T00:00:01Z", "sources": [source(name) for name in LOGS]}
    reading = take_payload(payload)
    assert reading.outcome == "failed" and reading.count is None
    assert reading.section("collection").data["window_end"] == payload["window_end"]
    assert all(reach == {"covered_from": None, "covered_from_inclusive": None, "covered_until": None, "complete": None}
               for reach in reading.section("coverage").data.values())
    assert any("collector's window does not match" in warning for warning in reading.warnings)

    payload["window_end"] = BEFORE
    payload["window_start"] = "2026-09-21T00:00:00.001Z"
    shifted_start = take_payload(payload)
    assert shifted_start.outcome == "failed" and all(reach["complete"] is None for reach in shifted_start.section("coverage").data.values())


def test_millisecond_rounding_is_verified_but_an_unrounded_collector_echo_is_not():
    before = "2026-09-22T00:00:00.0005Z"
    payload = {"window_start": START, "window_end": BEFORE, "queried_at": "2026-09-22T00:00:01Z",
               "sources": [source(name) for name in LOGS]}
    reading = take_payload(payload, before=before)
    assert reading.outcome == "empty" and all(reach["complete"] is True for reach in reading.section("coverage").data.values())
    payload["window_end"] = before
    unrounded = take_payload(payload, before=before)
    assert unrounded.outcome == "failed" and all(reach["complete"] is None for reach in unrounded.section("coverage").data.values())


def test_a_bad_echo_keeps_safe_rows_and_places_them_against_the_request():
    inside = row("windows_update", 10, "2026-09-21T12:00:00Z", {"updateTitle": "Inside"})
    after = row("windows_update", 11, "2026-09-22T00:00:00.0005Z", {"updateTitle": "After"})
    payload = {"window_start": START, "window_end": "2026-09-22T00:00:00.001Z",
               "queried_at": "2026-09-22T00:00:01Z",
               "sources": [source("windows_update", [after, inside]), source("device_configuration"), source("msi")]}
    reading = take_payload(payload)
    assert reading.outcome == "ok" and reading.count == 1
    assert reading.section("records").data == [inside, after]
    assert [entry["outside_window"] for entry in reading.section("changes").data] == [False, True]
    assert reading.section("collection").data["windows_update"]["row_issues"]["outside_window"] == 1
    assert all(reach["complete"] is None for reach in reading.section("coverage").data.values())

    payload["sources"][0] = source("windows_update", [after])
    only_after = take_payload(payload)
    assert only_after.outcome == "failed" and only_after.section("records").data == [after]

    payload["window_end"] = None
    payload["sources"][0] = source("windows_update", [inside])
    malformed_echo = take_payload(payload)
    assert malformed_echo.outcome == "ok" and malformed_echo.count == 1
    assert malformed_echo.section("records").data == [inside]
    assert malformed_echo.section("coverage").data["windows_update"]["complete"] is None


def test_a_verified_live_window_can_answer_empty_with_observed_reach():
    payload = {"window_start": "2026-09-21T23:00:00.3450000Z",
               "window_end": "2026-09-22T00:00:00.3450000Z",
               "queried_at": "2026-09-22T00:00:00.3460000Z",
               "sources": [source(name) for name in LOGS]}
    reading = take_payload(payload, before="", hours=1)
    assert reading.outcome == "empty" and reading.count == 0
    assert all(reach["complete"] is True and reach["covered_until"] == payload["window_end"]
               for reach in reading.section("coverage").data.values())


@pytest.mark.parametrize("start,end,queried", [
    (START, "2026-09-22T00:00:00.0005Z", "2026-09-22T00:00:01Z"),
    ("2026-09-21T01:00:00Z", BEFORE, "2026-09-22T00:00:01Z"),
    (START, BEFORE, "2026-09-21T23:59:59Z"),
])
def test_live_changes_refuses_an_unverified_host_window_but_keeps_safe_rows(start: str, end: str, queried: str):
    inside = row("msi", 4, "2026-09-21T12:00:00Z", {"[0]": "Observed"})
    payload = {"window_start": start, "window_end": end, "queried_at": queried,
               "sources": [source("windows_update"), source("device_configuration"), source("msi", [inside])]}
    reading = take_payload(payload, before="")
    assert reading.outcome == "failed" and reading.count is None
    assert reading.section("records").data == [inside]
    assert reading.section("changes").data[0]["outside_window"] is None
    assert all(reach["complete"] is None for reach in reading.section("coverage").data.values())


def test_changes_does_not_call_a_future_or_unclocked_window_empty():
    payload = {"window_start": "2026-09-23T00:00:00Z", "window_end": "2026-09-24T00:00:00Z",
               "queried_at": "2026-09-22T00:00:00Z", "sources": [source(name) for name in LOGS]}
    future = take_payload(payload, before="2026-09-24T00:00:00Z")
    assert future.outcome == "failed" and future.count is None
    assert any("begins at or after" in warning for warning in future.warnings)
    payload.update(window_start=START, window_end=BEFORE, queried_at=None)
    unclocked = take_payload(payload)
    assert unclocked.outcome == "failed" and unclocked.count is None
    assert all(reach["covered_until"] is None for reach in unclocked.section("coverage").data.values())
