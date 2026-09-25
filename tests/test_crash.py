"""The stop: the rule that composes one, the records it is composed from, and the moment.

The shapes that matter here are not all on any one machine: this machine's System log holds no
WER-SystemErrorReporting 1001 at all, and every Kernel-Power 41 in it wrote bug check code 0. So
every rule is held to a fixture shaped exactly as the host's projection returns a record — numbers
as numbers, booleans as booleans, the 6008's binary value as the hex string the projection writes
— and the host tests establish only that the machine answers and in what shape.
"""

from __future__ import annotations

import asyncio
import json
import re
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel import readings  # noqa: F401  (registers the catalog)
from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.reading import REGISTRY, take
from sentinel.readings import crash as module
from sentinel.readings.crash import (
    BUGCHECKS,
    KERNEL_POWER_41,
    WER_REPORT,
    attached_dumps,
    crash_script,
    decode,
    decode_faults,
    faults_script,
    faults_summary,
    named,
    since_clause,
)
from sentinel.readings.diagnostics import take_signals_sync
from tests.conftest import FakeBridge, identity_result, log_collector_marker, log_collector_result, real_bridge_or_skip
from tests.test_dump_inventory import dump_inventory

FIXTURES = Path(__file__).parent / "fixtures"
TOKEN = "test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def load() -> dict[str, Any]:
    return json.loads((FIXTURES / "crash-records.json").read_text(encoding="utf-8"))


def faults_fixture() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "fault-records.json").read_text(encoding="utf-8"))["records"]


def payload(**overrides: Any) -> dict[str, Any]:
    """The one object the script returns, newest first as Get-WinEvent hands it over."""
    doc = load()
    out = {
        "system": sorted(doc["system"], key=lambda r: r["TimeCreated"], reverse=True),
        "reports": sorted(doc["reports"], key=lambda r: r["TimeCreated"], reverse=True),
        "dump_inventory": dump_inventory(doc["dumps"]),
        "before": doc["before"],
        "warnings": [],
    }
    if "dumps" in overrides:
        overrides["dump_inventory"] = dump_inventory(overrides.pop("dumps"))
    out.update(overrides)
    return out


def from_moment(moment: str) -> dict[str, Any]:
    """The same payload as the script returns for a moment: both logs from it, oldest first."""
    doc = load()

    def keep(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted([r for r in rows if r["TimeCreated"] >= moment], key=lambda r: r["TimeCreated"])

    return {"system": keep(doc["system"]), "reports": keep(doc["reports"]), "dump_inventory": dump_inventory(doc["dumps"]), "before": doc["before"], "warnings": []}


def crash(body: dict[str, Any] | None = None, outcome: str = "ok", **params: Any):
    if body is not None and "collection" not in body:
        body = {**body, "collection": collection_for(body, params.get("count", 5), params.get("moment"))}
    items = [body] if body is not None else []
    return asyncio.run(take("crash", FakeBridge(BridgeResult(outcome, items=items, took_ms=12)), params))


def collection_for(body: dict[str, Any], count: int = 5, moment: str | None = None) -> dict[str, Any]:
    """Successful synthetic queries, including explicit bounds rather than inferred absence."""
    result: dict[str, Any] = {}
    for name, limit, log, oldest in (
        ("system", module.record_cap(count, moment), "System", "2026-09-02T07:00:05.1230000Z"),
        ("reports", 3 * count + 6, "Application", "2026-06-01T00:00:00.0000000Z"),
    ):
        returned = len(body.get(name, []))
        result[name] = {
            "outcome": "ok" if returned else "empty", "returned": returned, "limit": limit,
            "bound_reached": returned == limit, "error": None, "log": log,
            "log_enabled": True, "log_mode": "Circular", "log_state": "ok", "log_error": None,
            "log_oldest": oldest, "oldest_state": "ok", "oldest_error": None,
        }
    starts = {row["RecordId"]: row["TimeCreated"] for row in body.get("system", [])}
    result["before"] = [{"anchor": row["Anchor"], "at": starts.get(row["Anchor"]), "outcome": "ok", "returned": 1, "error": None}
                        for row in body.get("before", []) if row["Anchor"] in starts]
    return result


def faults(records: list[dict[str, Any]], outcome: str = "ok", **params: Any):
    result = log_collector_result(records, log="Application", window_start="2026-09-01T00:00:00.000Z", queried_at="2026-10-01T00:00:00.000Z", limit=int(params.get("count", 30))) if outcome in ("ok", "empty") else BridgeResult(outcome, took_ms=8)
    return asyncio.run(take("faults", FakeBridge(result), params))


def record(record_id: int) -> dict[str, Any]:
    doc = load()
    return next(r for r in doc["system"] + doc["reports"] if r["RecordId"] == record_id)


def stop_named(reading, started_at: str | None) -> dict[str, Any]:
    return next(s for s in reading.section("stops").data if s["started_at"] == started_at)


def clean_sessions(count: int) -> list[dict[str, Any]]:
    """Sessions that started and stopped as they were asked to: records that cost the cap and
    announce nothing."""
    out = []
    for i in range(count):
        day = (datetime(2026, 8, 1, tzinfo=UTC) + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append(
            {"RecordId": 5000 + i * 2, "Id": 12, "ProviderName": "Microsoft-Windows-Kernel-General", "Log": "System",
             "LevelDisplayName": "Information", "TimeCreated": f"{day}T08:00:00.0000000Z", "Message": "The operating system started.",
             "Properties": [10, 0, 26200, 1742, 0, 0, f"{day}T07:59:58.0000000Z"]}
        )
        out.append(
            {"RecordId": 5001 + i * 2, "Id": 13, "ProviderName": "Microsoft-Windows-Kernel-General", "Log": "System",
             "LevelDisplayName": "Information", "TimeCreated": f"{day}T22:00:00.0000000Z", "Message": "The operating system is shutting down.",
             "Properties": [f"{day}T22:00:00.0000000Z"]}
        )
    return sorted(out, key=lambda r: r["TimeCreated"], reverse=True)


def marker_estimate(row: dict[str, Any], at: str) -> dict[str, Any]:
    """Put a precise synthetic UTC stop estimate into both 6008 SYSTEMTIME slots."""
    value = datetime.fromisoformat(at.replace("Z", "+00:00"))
    words = (value.year, value.month, value.weekday(), value.day, value.hour, value.minute, value.second, value.microsecond // 1000)
    part = struct.pack("<8H", *words)
    properties = list(row["Properties"])
    properties[7] = (part + part).hex().upper()
    return {**row, "Properties": properties}


# ---------------------------------------------------------------- the queries


def test_one_launch_asks_both_logs_the_inventory_and_the_record_before_each_start():
    script = crash_script(5, None)
    for provider in ("Microsoft-Windows-Kernel-General", "Microsoft-Windows-Kernel-Power", "EventLog", "Microsoft-Windows-WER-SystemErrorReporting"):
        assert f"Provider[@Name='{provider}']" in script
    assert "EventData[Data[@Name='EventName']='BlueScreen']" in script
    assert "-MaxEvents 84" in script and "-MaxEvents 21" in script  # 12*5+24 records, 3*5+6 report records
    assert "Anchor = $anchor" in script and "-MaxEvents 1" in script
    assert "-MaxEvents 84 -Oldest" not in script and "-MaxEvents 21 -Oldest" not in script
    assert "Read-LogMetadata 'System'" in script and "Read-LogMetadata 'Application'" in script
    # The dump inventory is the dumps reading's own query, embedded once and not written again.
    assert script.count("Get-ChildItem") == module.DUMPS_SCRIPT.count("Get-ChildItem")
    assert "Join-Path $env:SystemRoot 'Minidump'" in script
    assert crash(payload(), count=5).method["launches"] == 1


def test_a_moment_asks_the_log_forward_from_it():
    script = crash_script(5, "2026-09-05T18:00:00.000Z")
    assert "-Oldest" in script and "-MaxEvents 48" in script
    assert "TimeCreated[@SystemTime&gt;='2026-09-05T18:00:00.000Z']" in script


def test_a_moment_that_is_not_a_timestamp_is_refused():
    with pytest.raises(ValueError, match="moment"):
        crash(payload(), moment="last tuesday")
    with pytest.raises(ValueError, match="count"):
        crash(payload(), count=99)


# ---------------------------------------------------------------- decoding


def test_the_41_is_read_under_the_names_this_builds_manifest_gives_it():
    entry = decode(record(1001))
    assert entry["kind"] == "unexpected shutdown" and entry["Log"] == "System"
    assert list(entry["fields"]) == list(KERNEL_POWER_41)
    assert entry["fields"]["BugcheckCode"] == 307 and entry["fields"]["SleepInProgress"] is False
    assert entry["bugcheck"] == {"code": "0x133", "name": "DPC_WATCHDOG_VIOLATION", "parameters": ["0x1", "0x1e9e", "0x1f4", "0x0"], "bucket": None}
    assert "error" not in entry


def test_a_41_that_wrote_no_code_carries_no_bug_check():
    entry = decode(record(900))
    assert entry["fields"]["BugcheckCode"] == 0 and "bugcheck" not in entry


def test_a_parameter_that_arrived_signed_is_the_same_address_unsigned():
    assert decode(record(1201))["bugcheck"]["parameters"] == ["0x411", "0xffffffffffffffff", "0x0", "0x0"]


def test_a_code_the_table_does_not_know_keeps_its_number_and_no_name():
    unknown = {**record(1001), "Properties": [0x765432] + record(1001)["Properties"][1:]}
    assert 0x765432 not in BUGCHECKS
    assert decode(unknown)["bugcheck"] == {"code": "0x765432", "name": None, "parameters": ["0x1", "0x1e9e", "0x1f4", "0x0"], "bucket": None}


def test_the_6008_stop_time_is_read_from_the_binary_value_not_the_locale_text():
    entry = decode(record(1002))
    assert entry["kind"] == "unexpected shutdown, logged at the next start"
    assert entry["fields"]["stopped_at"] == "2026-09-05T18:12:44.113Z"
    assert entry["fields"]["stopped_at_local"] == "2026-09-05T20:12:44.113"
    assert entry["fields"]["[0]"] == "8:12:44 PM"  # the locale text is kept, and is not what was read
    assert "error" not in entry


def test_a_6008_whose_binary_value_is_not_two_systemtimes_says_so():
    short = record(1002)
    truncated = {**short, "Properties": short["Properties"][:7] + [short["Properties"][7][:40]]}
    entry = decode(truncated)
    assert "SYSTEMTIME" in entry["error"] and "stopped_at" not in entry["fields"]


def test_a_record_that_does_not_fit_its_map_says_so_and_still_reads_what_it_can():
    """The System 1001's names are Microsoft's template, not an observation on this machine, so the
    message text is the second reading of the same record."""
    entry = decode({**record(1003), "Properties": []})
    assert "3 properties" not in entry["error"] and "0 properties" in entry["error"]
    assert entry["bugcheck"]["code"] == "0x133" and entry["bugcheck"]["name"] == "DPC_WATCHDOG_VIOLATION"
    assert entry["bugcheck"]["parameters"] == ["0x1", "0x1e9e", "0x1f4", "0x0"]


def test_a_report_is_read_as_bare_hex_and_names_the_failing_module():
    entry = decode(record(2002))
    assert entry["kind"] == "bug check report" and list(entry["fields"]) == list(WER_REPORT)
    assert entry["bugcheck"]["code"] == "0x1a" and entry["bugcheck"]["name"] == "MEMORY_MANAGEMENT"
    assert entry["bugcheck"]["parameters"] == ["0x411", "0xfffff8000f2a1000", "0x0", "0x0"]
    assert entry["bugcheck"]["bucket"].startswith("0x1a_411_dxgmms2!")


def test_the_minidump_comes_before_the_whole_memory_dump():
    attached = named(record(2002)["Properties"], WER_REPORT)["AttachedFiles"]
    assert attached_dumps(attached) == ["C:\\Windows\\Minidump\\090926-11111-01.dmp", "C:\\Windows\\MEMORY.DMP"]


def test_every_fetched_record_is_decoded_in_the_records_order():
    reading = crash(payload(), count=5)
    records, decoded = reading.section("records").data, reading.section("decoded").data
    assert len(decoded) == len(records) == 17
    assert [d["RecordId"] for d in decoded] == [r["RecordId"] for r in records]
    assert {d["kind"] for d in decoded} == {
        "start", "clean shutdown", "unexpected shutdown", "unexpected shutdown, logged at the next start", "bug check", "bug check report"
    }
    basis = reading.section("decoded").basis
    assert basis is not None and "Windows event manifests" in basis
    assert re.search(r"\b20\d\d-\d\d-\d\d\b", basis) is None


# ---------------------------------------------------------------- the rule


def test_the_stops_are_the_sessions_that_held_a_41_newest_first():
    reading = crash(payload(), count=5)
    assert reading.outcome == "ok" and reading.count == 5
    started = [s["started_at"] for s in reading.section("stops").data]
    assert started == ["2026-09-12T06:14:58.000Z", "2026-09-09T09:59:57.000Z", "2026-09-05T18:29:58.500Z", None, None]
    assert "2026-09-07T07:59:58.000Z" not in started  # the session that shut down cleanly is not a stop
    assert reading.section("stops").basis.startswith("Sessions are bounded by Kernel-General 12")


def test_a_stop_with_every_record_in_its_session():
    stop = stop_named(crash(payload(), count=5), "2026-09-05T18:29:58.500Z")
    assert stop["announced_at"] == "2026-09-05T18:30:03.000Z"
    assert stop["stopped_at"] == "2026-09-05T18:12:44.113Z"
    assert stop["down_seconds"] == 1034
    assert stop["bugcheck"] == {"code": "0x133", "name": "DPC_WATCHDOG_VIOLATION", "parameters": ["0x1", "0x1e9e", "0x1f4", "0x0"], "bucket": None, "source": "Kernel-Power 41"}
    assert stop["no_bugcheck_recorded"] is False
    assert stop["power"] == {"sleep_in_progress": False, "power_button_timestamp": 0, "whea_boot_error_count": 0, "boot_app_status": 0, "checkpoint": 0}
    assert stop["dump"] == {"name": "MEMORY.DMP", "path": "C:\\Windows\\MEMORY.DMP", "bytes": 2147483648, "modified": "2026-09-12T06:11:20.0000000Z", "matched_by": "1001"}
    assert stop["last_record_before"] == {
        "RecordId": 995, "TimeCreated": "2026-09-05T18:12:40.0000000Z", "ProviderName": "storahci", "Id": 129,
        "LevelDisplayName": "Warning", "Message": "Reset to device, \\Device\\RaidPort1, was issued.",
    }
    assert stop["quiet_seconds"] == 1038
    assert stop["records"] == {"start": 1000, "power_41": 1001, "eventlog_6008": 1002, "wer_1001": 1003, "report": []}


def test_a_lone_6008_with_an_estimate_between_returned_starts_is_a_cited_stop():
    # A prior clean 13 does not refute the later 6008: both can be in the returned history.
    system = clean_sessions(1) + [record(1000), record(1002)]
    reading = crash(payload(system=sorted(system, key=lambda row: row["TimeCreated"], reverse=True), reports=[], before=[]))
    assert reading.outcome == "ok" and reading.count == 1 and not reading.warnings
    stop = reading.section("stops").data[0]
    assert stop["stopped_at"] == "2026-09-05T18:12:44.113Z" and stop["down_seconds"] == 1034
    assert stop["announced_at"] is None and stop["power"] is None and stop["bugcheck"] is None
    assert stop["no_bugcheck_recorded"] is False
    assert stop["records"] == {"start": 1000, "power_41": None, "eventlog_6008": 1002, "wer_1001": None, "report": []}
    assert stop["last_record_collection"]["outcome"] == "not_requested"
    assert "No Kernel-Power 41" in stop["last_record_collection"]["error"]
    lead = next(item for item in take_signals_sync({"crash": reading})[0] if item["id"] == "transition:unexpected-shutdown")
    assert lead["evidence"]["returned"] == 1
    assert any(ref["role"] == "eventlog_6008" and ref["params"]["record_id"] == 1002
               for fact in lead["evidence"]["stops"] for ref in fact["refs"])


@pytest.mark.parametrize("case, reason", [
    ("after_start", "at or after the next start"),
    ("before_previous", "before the previous returned start"),
    ("malformed", "binary stop estimate could not be read"),
    ("repeated", "2 shutdown markers"),
    ("no_start", "next start was not returned"),
])
def test_a_lone_6008_is_not_placed_without_consistent_single_marker_evidence(case: str, reason: str):
    start, marker = record(1000), record(1002)
    system = [start, marker]
    if case == "after_start":
        system[1] = marker_estimate(marker, "2026-09-05T18:31:00Z")
    elif case == "before_previous":
        previous = record(1000)
        previous["RecordId"] = 999
        previous["TimeCreated"] = "2026-09-05T18:20:00.0000000Z"
        previous["Properties"][6] = "2026-09-05T18:19:58.0000000Z"
        system.insert(0, previous)
    elif case == "malformed":
        broken = {**marker, "Properties": list(marker["Properties"])}
        broken["Properties"][7] = broken["Properties"][7][:40]
        system[1] = broken
    elif case == "repeated":
        system.append({**marker, "RecordId": 1004})
    else:
        system = [marker]
    reading = crash(payload(system=sorted(system, key=lambda row: row["TimeCreated"], reverse=True), reports=[], before=[]))
    assert reading.outcome == "empty" and reading.count == 0 and reading.section("stops").data == []
    assert len(reading.section("records").data) == len(reading.section("decoded").data) == len(system)
    assert any("EventLog 6008 record 1002" in warning and reason in warning for warning in reading.warnings)


def test_a_lone_6008_before_the_previous_clean_shutdown_is_not_another_stop():
    previous = record(1000)
    previous["RecordId"] = 999
    previous["TimeCreated"] = "2026-09-05T18:00:00.0000000Z"
    previous["Properties"][6] = "2026-09-05T17:59:58.0000000Z"
    clean = clean_sessions(1)[0]
    clean["TimeCreated"] = "2026-09-05T18:20:00.0000000Z"
    system = [previous, clean, record(1000), record(1002)]
    reading = crash(payload(system=sorted(system, key=lambda row: row["TimeCreated"], reverse=True), reports=[], before=[]))
    assert reading.outcome == "empty" and reading.section("stops").data == []
    assert any("at or before a previous returned clean shutdown" in warning for warning in reading.warnings)


def test_a_system_bug_check_record_without_an_anchoring_stop_stays_visible_but_unplaced():
    system = [record(1000), record(1003)]
    body = payload(system=sorted(system, key=lambda row: row["TimeCreated"], reverse=True), reports=[], before=[])
    reading = crash(body)
    assert reading.outcome == "empty" and reading.section("stops").data == []
    assert any("System bug-check record 1003" in warning and "cannot place" in warning for warning in reading.warnings)
    moment = crash(body, moment="2026-09-05T18:00:00Z")
    assert moment.outcome == "empty" and any("could not be classified" in warning for warning in moment.warnings)
    assert not any("announced no unplanned stop" in warning for warning in moment.warnings)


def test_a_system_bug_check_record_joins_a_stop_placed_by_6008_without_a_41():
    system = [record(1000), record(1002), record(1003)]
    reading = crash(payload(system=sorted(system, key=lambda row: row["TimeCreated"], reverse=True), reports=[], before=[]))
    assert reading.outcome == "ok" and reading.count == 1 and not reading.warnings
    stop = reading.section("stops").data[0]
    assert stop["records"]["power_41"] is None and stop["records"]["eventlog_6008"] == 1002
    assert stop["records"]["wer_1001"] == 1003
    assert stop["bugcheck"]["source"] == "WER-SystemErrorReporting 1001"


def test_a_session_placed_report_without_a_41_did_not_request_the_pre_start_lookup():
    report = record(2100)
    report["TimeCreated"] = "2026-09-05T18:31:00.0000000Z"
    # Even an inconsistent returned lookup must not become this no-41 stop's pre-start row.
    reading = crash(payload(system=[record(1000)], reports=[report], before=[load()["before"][0]]))
    assert reading.outcome == "ok" and reading.count == 1
    stop = reading.section("stops").data[0]
    assert stop["records"]["power_41"] is None and stop["records"]["report"] == [2100]
    assert stop["last_record_collection"]["outcome"] == "not_requested"
    assert stop["last_record_before"] is None and stop["quiet_seconds"] is None


def test_a_stop_whose_start_is_beyond_the_logs_retention():
    reading = crash(payload(), count=5)
    stop = next(s for s in reading.section("stops").data if s["records"]["power_41"] == 900)
    assert stop["started_at"] is None and stop["announced_at"] == "2026-09-02T07:00:05.123Z"
    assert stop["no_bugcheck_recorded"] is True and stop["bugcheck"] is None
    assert stop["dump"] is None and stop["down_seconds"] is None
    assert stop["last_record_before"] is None and stop["quiet_seconds"] is None
    assert stop["last_record_collection"]["outcome"] == "not_requested"
    assert "next start was not returned" in stop["last_record_collection"]["error"]
    assert not any(source["anchor"] == 900 for source in reading.section("collection").data["before"])


def test_a_report_older_than_the_system_log_is_a_stop_of_its_own():
    stop = next(s for s in crash(payload(), count=5).section("stops").data if s["records"]["report"] == [2100])
    assert stop["started_at"] is None and stop["announced_at"] is None and stop["stopped_at"] is None
    assert stop["bugcheck"]["code"] == "0x3b" and stop["bugcheck"]["source"] == "BlueScreen report"
    assert stop["bugcheck"]["bucket"].startswith("0x3b_c0000005_nt!")
    # The report named the file; the disk no longer has it, which is not the same as no dump.
    assert stop["dump"] == {"name": "070426-99999-01.dmp", "path": "C:\\Windows\\Minidump\\070426-99999-01.dmp", "bytes": None, "modified": None, "matched_by": "report", "inventory": {"outcome": "empty", "detail": "No exact match was returned in the observed dump inventory."}}


def test_a_start_whose_time_arrives_as_a_json_date_still_dates_the_stop():
    """ConvertTo-Json writes a DateTime property as /Date(milliseconds)/, which is how the host
    hands over Kernel-General 12's StartTime; a start the reading could not date would leave every
    stop without one."""
    from datetime import datetime

    body = payload()
    expected: dict[int, str] = {}
    for start in (r for r in body["system"] if r["Id"] == 12):
        moment = datetime.fromisoformat(start["Properties"][6].replace("Z", "+00:00"))
        start["Properties"][6] = f"/Date({int(moment.timestamp() * 1000)})/"
        expected[start["RecordId"]] = moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    stops = [s for s in crash(body, count=5).section("stops").data if s["records"]["start"] is not None]
    assert stops
    assert all(stop["started_at"] == expected[stop["records"]["start"]] for stop in stops)
    assert all(stop["down_seconds"] is not None for stop in stops if stop["stopped_at"])


def test_the_whole_memory_dump_belongs_to_a_stop_only_when_it_was_written_in_the_day_before_it():
    """MEMORY.DMP is one file every later crash overwrites: a report older than the copy on disk
    named it, but the copy is not this stop's, so the stop keeps the minidump the report named and
    the disk no longer holds."""
    body = payload()
    report = next(r for r in body["reports"] if r["RecordId"] == 2100)  # 2026-07-04, older than the System log
    report["Properties"][15] = report["Properties"][15] + "\n\\\\?\\C:\\Windows\\MEMORY.DMP"
    whole = next(f for source in body["dump_inventory"]["locations"] for f in source["files"] if f["name"] == "MEMORY.DMP")  # written in September, by a later stop

    stop = next(s for s in crash(body, count=5).section("stops").data if s["records"]["report"] == [2100])
    assert stop["dump"] == {"name": "070426-99999-01.dmp", "path": "C:\\Windows\\Minidump\\070426-99999-01.dmp", "bytes": None, "modified": None, "matched_by": "report", "inventory": {"outcome": "empty", "detail": "No exact match was returned in the observed dump inventory."}}

    whole["modified"] = "2026-07-04T08:30:00.0000000Z"  # written in the half hour before the report: this stop's
    stop = next(s for s in crash(body, count=5).section("stops").data if s["records"]["report"] == [2100])
    assert stop["dump"]["name"] == "MEMORY.DMP" and stop["dump"]["bytes"] == whole["bytes"] and stop["dump"]["matched_by"] == "report"


def test_one_report_however_many_records_it_was_written_across():
    stop = stop_named(crash(payload(), count=5), "2026-09-09T09:59:57.000Z")
    assert stop["records"]["report"] == [2000, 2001, 2002]
    assert stop["bugcheck"]["source"] == "Kernel-Power 41"  # the 41 named the code
    assert stop["bugcheck"]["bucket"].startswith("0x1a_411_dxgmms2!")  # only the report names the module
    assert stop["dump"]["matched_by"] == "report" and stop["dump"]["name"] == "090926-11111-01.dmp"
    assert stop["power"]["whea_boot_error_count"] == 2


def test_late_conflicting_report_stays_raw_without_lending_its_bucket_or_dump():
    body = payload()
    for report in body["reports"]:
        if report["RecordId"] in (2000, 2001, 2002):
            report["TimeCreated"] = f"2026-09-12T06:{17 + report['RecordId'] - 2000:02}:10.0000000Z"
    body["reports"].sort(key=lambda row: row["TimeCreated"], reverse=True)
    reading = crash(body, count=20)
    stop = stop_named(reading, "2026-09-12T06:14:58.000Z")
    assert reading.outcome == "ok" and reading.count == 5
    assert all(not ({2000, 2001, 2002} & set(item["records"]["report"])) for item in reading.section("stops").data)
    assert stop["bugcheck"]["code"] == "0x133" and stop["bugcheck"]["bucket"] is None
    assert stop["dump"]["name"] == "091226-12345-01.dmp" and stop["dump"]["matched_by"] == "time"
    assert stop["records"]["report"] == [] and stop["reported_at"] is None
    assert {row["RecordId"] for row in reading.section("records").data} >= {2000, 2001, 2002}
    assert {row["RecordId"] for row in reading.section("decoded").data} >= {2000, 2001, 2002}
    assert len(reading.warnings) == 1 and all(value in reading.warnings[0] for value in ("2000, 2001, 2002", "0x1a", "1301", "0x133", "not attached"))
    # The earlier stop can still use its own minidump by time. A later live-kernel WATCHDOG
    # file in the same window is a different kind of observation, not this stop's dump.
    earlier = stop_named(reading, "2026-09-09T09:59:57.000Z")
    assert earlier["dump"]["name"] == "090926-11111-01.dmp" and earlier["dump"]["matched_by"] == "time"
    signals, basis = take_signals_sync({"crash": reading})
    lead = next(item for item in signals if item["id"] == "transition:unexpected-shutdown")
    assert lead["evidence"]["returned"] == 5
    assert "Observed with warnings: crash" in basis


def test_only_conflicting_report_group_is_detached_from_a_41_session():
    body = payload()
    for report in body["reports"]:
        if report["RecordId"] in (2000, 2001, 2002):
            report["TimeCreated"] = f"2026-09-12T06:{17 + report['RecordId'] - 2000:02}:10.0000000Z"
    consistent = record(2002)
    consistent["RecordId"] = 2200
    consistent["TimeCreated"] = "2026-09-12T06:20:10.0000000Z"
    consistent["Properties"][0] = "0x133_synthetic"
    consistent["Properties"][5] = "133"
    consistent["Properties"][15] = "\\\\?\\C:\\Windows\\Minidump\\091226-12345-01.dmp"
    consistent["Properties"][19] = "synthetic-133-report"
    body["reports"].append(consistent)
    body["reports"].sort(key=lambda row: row["TimeCreated"], reverse=True)
    reading = crash(body, count=5)
    stop = stop_named(reading, "2026-09-12T06:14:58.000Z")
    assert stop["records"]["report"] == [2200] and stop["reported_at"] == "2026-09-12T06:20:10.000Z"
    assert stop["bugcheck"]["bucket"] == "0x133_synthetic"
    assert len(reading.warnings) == 1 and "2000, 2001, 2002" in reading.warnings[0]


@pytest.mark.parametrize("reported_code", ["0x1a", "0000001a", "", "0"])
def test_a_matching_or_unreadable_report_code_does_not_refute_its_41_session(reported_code: str):
    body = payload()
    latest = next(row for row in body["reports"] if row["RecordId"] == 2002)
    latest["Properties"][5] = reported_code
    reading = crash(body, count=5)
    stop = stop_named(reading, "2026-09-09T09:59:57.000Z")
    assert stop["records"]["report"] == [2000, 2001, 2002]
    assert stop["dump"]["matched_by"] == "report" and not reading.warnings


def test_a_zero_code_41_cannot_refute_a_report_filed_in_its_session():
    body = payload()
    for report in body["reports"]:
        if report["RecordId"] in (2000, 2001, 2002):
            report["TimeCreated"] = f"2026-09-02T07:{5 + report['RecordId'] - 2000:02}:10.0000000Z"
    body["reports"].sort(key=lambda row: row["TimeCreated"], reverse=True)
    reading = crash(body, count=5)
    stop = next(item for item in reading.section("stops").data if item["records"]["power_41"] == 900)
    assert stop["bugcheck"]["code"] == "0x1a" and stop["bugcheck"]["source"] == "BlueScreen report"
    assert stop["records"]["report"] == [2000, 2001, 2002]
    assert not reading.warnings


def test_a_live_kernel_file_alone_is_not_the_time_matched_dump_for_a_stop():
    live = next(file for file in load()["dumps"] if "LiveKernelReports" in file["path"])
    reading = crash(payload(reports=[], dumps=[live]), count=5)
    assert stop_named(reading, "2026-09-09T09:59:57.000Z")["dump"] is None


def test_a_dump_with_nothing_to_name_it_is_matched_by_the_time_it_was_written():
    stop = stop_named(crash(payload(), count=5), "2026-09-12T06:14:58.000Z")
    assert stop["records"]["wer_1001"] is None and stop["records"]["report"] == []
    # Written 42 seconds after the start: the kernel puts the dump in the pagefile, and the file
    # appears while the machine comes back, which is the window the rule looks in.
    assert stop["dump"] == {"name": "091226-12345-01.dmp", "path": "C:\\Windows\\Minidump\\091226-12345-01.dmp", "bytes": 1048576, "modified": "2026-09-12T06:15:40.0000000Z", "matched_by": "time"}
    assert stop["down_seconds"] == 236


def test_a_session_the_bound_cut_is_not_a_stop_the_log_lost():
    """The query is bounded and newest first, so the oldest fetched records can be a session whose
    start was simply not fetched. That is a larger count away, not a stop with no known start."""
    cut_41 = {"RecordId": 300, "Id": 41, "ProviderName": "Microsoft-Windows-Kernel-Power", "Log": "System", "LevelDisplayName": "Critical",
              "TimeCreated": "2026-07-20T08:00:05.0000000Z", "Message": "The system has rebooted without cleanly shutting down first.",
              "Properties": [0, 0, 0, 0, 0, 0, 0, 0, 0, False, 0, 0, False, 0, 0, False, False, 0, 0, 3, 0]}
    cut_6008 = {"RecordId": 301, "Id": 6008, "ProviderName": "EventLog", "Log": "System", "LevelDisplayName": "Error",
                "TimeCreated": "2026-07-20T08:00:09.0000000Z", "Message": "The previous system shutdown was unexpected.", "Properties": ["", "", "", "", 1, "", "", ""]}
    system = clean_sessions(17) + [cut_6008, cut_41]  # 36 records: exactly the bound for count=1
    assert len(system) == module.record_cap(1, None)
    reading = crash(payload(system=system, reports=[record(2100)], before=[]), count=1)
    assert reading.outcome == "empty" and reading.count == 0
    assert reading.section("stops").data == []  # neither the cut session nor the older report is a stop here
    assert any("record bound was reached after 0 stops" in w for w in reading.warnings)

    # The same records with the bound not reached are a stop the log lost the start of, as before.
    reading = crash(payload(system=system[:10] + [cut_6008, cut_41], reports=[], before=[]), count=1)
    assert reading.count == 1 and reading.section("stops").data[0]["started_at"] is None


def test_every_stop_carries_when_its_report_was_filed():
    stops = crash(payload(), count=5).section("stops").data
    orphan = next(s for s in stops if s["records"]["report"] == [2100])
    assert orphan["reported_at"] == "2026-07-04T09:00:00.000Z"
    with_report = next(s for s in stops if s["records"]["report"] == [2000, 2001, 2002])
    assert with_report["reported_at"] == min(record(i)["TimeCreated"] for i in (2000, 2001, 2002))[:23] + "Z"
    assert all("reported_at" in s for s in stops)


def test_the_count_is_how_many_stops_were_asked_for():
    reading = crash(payload(), count=2)
    assert reading.count == 2
    assert [s["started_at"] for s in reading.section("stops").data] == ["2026-09-12T06:14:58.000Z", "2026-09-09T09:59:57.000Z"]
    assert reading.warnings == []  # the log simply held what it held; the cap did not bite


def test_the_cap_warns_when_it_bites_rather_than_reporting_a_quiet_machine():
    reading = crash(payload(system=clean_sessions(18), reports=[]), count=1)
    assert reading.outcome == "empty" and reading.count == 0
    assert reading.warnings == ["the query's record bound was reached after 0 stops; the 36-record System bound is shared with clean starts and shutdowns; a larger count (up to 20) raises the bound and may reach older retained records."]
    assert crash(payload(system=clean_sessions(18), reports=[]), count=2).section("collection").data["system"]["returned"] == 36
    at_limit = crash(payload(system=clean_sessions(132), reports=[]), count=20)
    assert at_limit.outcome == "empty" and "take `events` over an older System window" in at_limit.warnings[0]
    moment = crash(payload(system=clean_sessions(24), reports=[]), count=1, moment="2026-08-01T00:00:00Z")
    assert any("fixed 48-record System window" in warning and "Moving the moment later skips intervening history" in warning for warning in moment.warnings)


def test_a_sub_query_that_did_not_answer_is_a_warning_not_a_silence():
    reading = crash(payload(dumps=[], warnings=["The dump inventory did not read: access is denied."]), count=5)
    assert reading.warnings == ["The dump inventory did not read: access is denied."]
    assert stop_named(reading, "2026-09-12T06:14:58.000Z")["dump"] is None


def test_failed_dump_locations_preserve_stops_and_leave_reported_file_presence_unknown():
    body = payload()
    source = next(row for row in body["dump_inventory"]["locations"] if row["id"] == "minidump")
    source.update(outcome="denied", error_count=1, errors=[{"kind": "denied", "detail": "synthetic denial"}])
    reading = crash(body, count=5)
    assert reading.outcome == "ok" and reading.count == 5 and reading.warnings
    assert reading.section("collection").data["dumps"]["complete"] is False
    orphan = next(stop for stop in reading.section("stops").data if stop["records"]["report"] == [2100])
    assert orphan["dump"]["inventory"]["outcome"] == "denied"
    assert "unknown" in orphan["dump"]["inventory"]["detail"]
    assert stop_named(reading, "2026-09-09T09:59:57.000Z")["dump"]["bytes"] is not None


def test_failed_inventory_does_not_change_an_observed_absence_of_stops():
    reading = crash(payload(system=[], reports=[], before=[], dump_inventory=None), count=5)
    assert reading.outcome == "empty" and reading.count == 0 and reading.warnings
    assert reading.section("collection").data["dumps"]["complete"] is False


@pytest.mark.parametrize("complete", [True, False])
def test_stop_without_named_or_matched_dump_retains_inventory_coverage(complete):
    body = payload(dumps=[])
    if not complete:
        source = body["dump_inventory"]["locations"][0]
        source.update(outcome="denied", present=None, error_count=1, errors=[{"kind": "denied", "detail": "synthetic denial"}])
    reading = crash(body, count=5)
    stop = stop_named(reading, "2026-09-12T06:14:58.000Z")
    assert stop["dump"] is None and stop["dump_inventory_complete"] is complete
    assert bool(reading.warnings) is not complete


def test_time_matched_candidate_keeps_the_incomplete_inventory_qualification():
    body = payload()
    source = next(row for row in body["dump_inventory"]["locations"] if row["id"] == "live_kernel")
    source.update(outcome="denied", present=True, error_count=1, errors=[{"kind": "denied", "detail": "synthetic denial"}])
    stop = stop_named(crash(body, count=5), "2026-09-12T06:14:58.000Z")
    assert stop["dump"]["matched_by"] == "time" and stop["dump"]["bytes"] is not None
    assert stop["dump_inventory_complete"] is False


def test_unmatched_historical_memory_dump_does_not_claim_the_current_file_is_absent():
    body = payload()
    report = next(row for row in body["reports"] if row["RecordId"] == 2100)
    report["Properties"][15] = r"C:\Windows\MEMORY.DMP"
    stop = next(stop for stop in crash(body, count=5).section("stops").data if stop["records"]["report"] == [2100])
    assert stop["dump"]["bytes"] is None
    assert stop["dump"]["inventory"] == {"outcome": "ok", "detail": "A file with this path is inventoried, but was not matched to this stop."}


def test_a_bridge_that_did_not_answer_is_not_an_absence_of_stops():
    reading = crash(None, outcome="denied", count=5)
    assert reading.outcome == "denied" and reading.sections == [] and reading.count is None
    assert reading.error == {"kind": "denied", "detail": ""}


# ---------------------------------------------------------------- the moment


def test_a_moment_reports_the_stop_the_next_start_announced_and_continues_forward():
    reading = crash(from_moment("2026-09-05T18:00:00.0000000Z"), count=5, moment="2026-09-05T18:00:00Z")
    assert reading.outcome == "ok" and reading.count == 3 and reading.warnings == []
    assert [s["started_at"] for s in reading.section("stops").data] == [
        "2026-09-05T18:29:58.500Z", "2026-09-09T09:59:57.000Z", "2026-09-12T06:14:58.000Z"
    ]


def test_a_moment_whose_next_start_announced_nothing_is_empty_and_says_which_start():
    reading = crash(from_moment("2026-09-06T00:00:00.0000000Z"), count=5, moment="2026-09-06T00:00:00Z")
    assert reading.outcome == "empty" and reading.count == 0
    assert reading.section("stops").data == []
    assert reading.warnings == [
        "the first start after 2026-09-06T00:00:00Z, at 2026-09-07T08:00:00.000Z, announced no unplanned stop: "
        "no Kernel-Power 41, EventLog 6008 or bug check report in that session"
    ]


def test_a_moment_with_only_a_6008_returns_the_first_starts_stop_or_its_uncertainty():
    system = [record(1000), record(1002)]
    body = payload(system=sorted(system, key=lambda row: row["TimeCreated"]), reports=[], before=[])
    observed = crash(body, moment="2026-09-05T18:00:00Z")
    assert observed.outcome == "ok" and observed.count == 1
    assert observed.section("coverage").data["first_start"]["established"] is True
    assert not any("announced no unplanned stop" in warning for warning in observed.warnings)

    broken = record(1002)
    broken["Properties"][7] = broken["Properties"][7][:40]
    body = payload(system=sorted([record(1000), broken], key=lambda row: row["TimeCreated"]), reports=[], before=[])
    uncertain = crash(body, moment="2026-09-05T18:00:00Z")
    assert uncertain.outcome == "empty" and uncertain.count == 0
    assert any("first start" in warning and "could not be classified" in warning for warning in uncertain.warnings)
    assert not any("announced no unplanned stop" in warning for warning in uncertain.warnings)


def test_a_moment_with_no_start_after_it_is_empty_and_says_so():
    reading = crash(from_moment("2026-09-20T00:00:00.0000000Z"), count=5, moment="2026-09-20T00:00:00Z")
    assert reading.outcome == "empty" and reading.count == 0
    assert reading.warnings == ["no start follows 2026-09-20T00:00:00Z; nothing after it announced a stop"]


@pytest.mark.parametrize("moment", ["2026-09-06T00:00:00+00:00", "2026-09-06"])
def test_a_local_or_offset_moment_uses_the_same_utc_boundary_as_its_query(moment):
    reading = crash(from_moment("2026-09-06T00:00:00.0000000Z"), count=5, moment=moment)
    coverage = reading.section("coverage").data
    assert coverage["system"]["reaches_moment"] is True
    assert coverage["reports"]["reaches_moment"] is True
    assert coverage["first_start"]["established"] is True


def test_a_moment_before_system_retention_keeps_report_only_and_startless_stops():
    moment = "2026-07-01T00:00:00Z"
    reading = crash(from_moment("2026-07-01T00:00:00.0000000Z"), count=5, moment=moment)
    assert reading.outcome == "ok" and reading.count == 5
    stops = reading.section("stops").data
    assert stops[0]["records"]["report"] == [2100] and stops[0]["started_at"] is None
    assert stops[1]["records"]["power_41"] == 900 and stops[1]["started_at"] is None
    assert [stop["records"]["power_41"] for stop in stops[2:]] == [1001, 1201, 1301]
    coverage = reading.section("coverage").data
    assert coverage["system"]["reaches_moment"] is False
    assert coverage["reports"]["reaches_moment"] is True
    assert coverage["first_start"]["record_id"] == 1000 and coverage["first_start"]["established"] is False
    assert any("first start cannot be established" in warning for warning in reading.warnings)


def test_proven_system_retention_excludes_the_session_before_a_moment():
    moment = "2026-07-01T00:00:00Z"
    body = from_moment("2026-07-01T00:00:00.0000000Z")
    body["collection"] = collection_for(body, moment=moment)
    body["collection"]["system"]["log_oldest"] = "2026-06-30T23:59:59.9999999Z"
    reading = crash(body, count=5, moment=moment)
    assert reading.outcome == "ok" and reading.count == 3 and reading.warnings == []
    first_start = reading.section("coverage").data["first_start"]
    assert first_start["established"] is True
    assert first_start["at"] == "2026-09-05T18:30:00.000Z"
    assert first_start["started_at"] == "2026-09-05T18:29:58.500Z"
    assert all(stop["records"]["power_41"] != 900 for stop in reading.section("stops").data)
    body["collection"]["system"]["log_oldest"] = moment
    at_boundary = crash(body, count=5, moment=moment)
    assert at_boundary.section("coverage").data["system"]["reaches_moment"] is False
    assert at_boundary.section("stops").data[0]["records"]["report"] == [2100]


def test_submillisecond_moment_uses_the_same_floor_as_the_event_query():
    moment = "2026-07-01T00:00:00.0000009Z"
    body = from_moment("2026-07-01T00:00:00.0000000Z")
    body["collection"] = collection_for(body, moment=moment)
    body["collection"]["system"]["log_oldest"] = "2026-07-01T00:00:00.0000000Z"
    reading = crash(body, moment=moment)
    assert reading.section("coverage").data["system"]["reaches_moment"] is False
    assert reading.section("coverage").data["first_start"]["established"] is False


def test_unavailable_retention_never_discards_returned_stops_or_claims_a_first_start():
    moment = "2026-07-01T00:00:00Z"
    body = from_moment("2026-07-01T00:00:00.0000000Z")
    body["collection"] = collection_for(body, moment=moment)
    body["collection"]["system"].update(log_state="denied", oldest_state="denied", log_oldest=None)
    reading = crash(body, moment=moment)
    assert reading.outcome == "ok" and reading.count == 5
    assert reading.section("coverage").data["system"]["reaches_moment"] is None
    assert reading.section("coverage").data["first_start"]["established"] is False
    assert any("could not be established" in warning for warning in reading.warnings)


def test_metadata_from_another_log_cannot_certify_retention_or_erase_reports():
    moment = "2026-07-01T00:00:00Z"
    body = from_moment("2026-07-01T00:00:00.0000000Z")
    body["collection"] = collection_for(body, moment=moment)
    body["collection"]["reports"]["log"] = "System"
    reading = crash(body, moment=moment)
    assert reading.outcome == "ok" and reading.section("stops").data[0]["records"]["report"] == [2100]
    assert reading.section("coverage").data["reports"] == {"retained_from": None, "reaches_moment": None}
    assert reading.section("collection").data["reports"]["log_state"] == "failed"


def test_application_retention_must_reach_a_clean_start_before_it_is_called_clean():
    moment = "2026-09-06T00:00:00Z"
    body = from_moment("2026-09-06T00:00:00.0000000Z")
    body["collection"] = collection_for(body, moment=moment)
    body["collection"]["reports"]["log_oldest"] = "2026-09-08T00:00:00.0000000Z"
    reading = crash(body, moment=moment)
    assert reading.outcome == "ok" and reading.count == 2
    assert reading.section("coverage").data["first_start"]["established"] is True
    assert any("could not be classified" in warning for warning in reading.warnings)
    assert not any("announced no unplanned stop" in warning for warning in reading.warnings)


def test_a_zero_bugcheck_code_does_not_certify_absence_before_application_retention():
    body = payload()
    body["collection"] = collection_for(body)
    body["collection"]["reports"]["log_oldest"] = "2026-09-03T00:00:00.0000000Z"
    reading = crash(body, count=5)
    stop = next(stop for stop in reading.section("stops").data if stop["records"]["power_41"] == 900)
    assert stop["bugcheck"] is None and stop["no_bugcheck_recorded"] is None


def test_a_log_that_held_nothing_at_all_is_empty_not_failed():
    reading = crash(payload(system=[], reports=[], before=[]), count=5)
    assert reading.outcome == "empty" and reading.count == 0 and reading.error is None
    assert [s.name for s in reading.sections] == ["records", "decoded", "stops", "collection", "coverage"]


def test_an_empty_bridge_payload_cannot_certify_that_both_logs_answered():
    reading = crash(None, outcome="empty")
    assert reading.outcome == "failed" and not reading.observed and reading.count is None


@pytest.mark.parametrize("system,reports,expected", [
    ("failed", "failed", "failed"), ("denied", "denied", "denied"),
    ("failed", "denied", "failed"), ("empty", "failed", "failed"),
    ("denied", "empty", "denied"),
])
def test_primary_source_gaps_cannot_certify_no_stops(system, reports, expected):
    body = payload(system=[], reports=[], before=[])
    body["collection"] = collection_for(body)
    for name, outcome in (("system", system), ("reports", reports)):
        if outcome != "empty":
            body["collection"][name].update(outcome=outcome, bound_reached=None, error="synthetic refusal")
    reading = crash(body)
    assert reading.outcome == expected and reading.count is None and not reading.observed
    assert reading.error["kind"] == expected


@pytest.mark.parametrize("moment", [None, "2026-01-01T00:00:00Z"])
def test_surviving_reports_remain_stops_when_system_collection_fails(moment):
    body = payload()
    body["collection"] = collection_for(body, moment=moment)
    body["collection"]["system"].update(outcome="failed", bound_reached=None, error="synthetic System failure")
    reading = crash(body, moment=moment or "")
    assert reading.outcome == "ok" and reading.count > 0
    assert all(stop["started_at"] is None and stop["records"]["report"] for stop in reading.section("stops").data)
    assert all(row["Log"] == "Application" for row in reading.section("records").data)
    assert not any("no start follows" in warning for warning in reading.warnings)


def test_failed_or_limited_reports_cannot_certify_no_bugcheck():
    for failed in (True, False):
        body = payload(reports=[] if failed else [record(2100)] * 21)
        body["collection"] = collection_for(body)
        if failed:
            body["collection"]["reports"].update(outcome="failed", bound_reached=None, error="synthetic Application failure")
        reading = crash(body)
        stop = next(s for s in reading.section("stops").data if s["records"]["power_41"] == 900)
        assert reading.outcome == "ok" and stop["no_bugcheck_recorded"] is None
        assert reading.warnings


def test_a_clean_looking_start_with_failed_reports_is_not_called_clean():
    moment = "2026-09-06T00:00:00Z"
    body = from_moment(moment)
    body["collection"] = collection_for(body, moment=moment)
    body["collection"]["reports"].update(outcome="failed", bound_reached=None, error="synthetic Application failure")
    reading = crash(body, moment=moment)
    assert reading.outcome == "ok"  # Later stops survive, while the first start remains unknown.
    assert any("could not be classified" in warning for warning in reading.warnings)
    assert not any("announced no unplanned stop" in warning for warning in reading.warnings)


@pytest.mark.parametrize("collection", [None, {}, {"system": {"outcome": "empty", "returned": 0}}])
def test_missing_query_results_do_not_certify_observation(collection):
    body = payload(system=[], reports=[], before=[], collection=collection)
    reading = crash(body)
    assert reading.outcome == "failed" and not reading.observed


def test_contradictory_query_counts_are_discarded_with_a_valid_other_source():
    body = payload(system=[], reports=[], before=[])
    body["collection"] = collection_for(body)
    body["collection"]["system"]["returned"] = 1
    reading = crash(body)
    assert reading.outcome == "failed" and reading.count is None
    assert reading.section("collection").data["reports"]["outcome"] == "empty"


def test_failed_before_lookup_does_not_reuse_partial_rows():
    body = payload()
    body["collection"] = collection_for(body)
    failed = body["collection"]["before"][0]
    failed.update(outcome="denied", returned=0, error="synthetic last-record denial")
    reading = crash(body)
    stop = next(s for s in reading.section("stops").data if (s["records"]["start"] or s["records"]["power_41"]) == failed["anchor"])
    assert reading.outcome == "ok" and stop["last_record_before"] is None and stop["quiet_seconds"] is None
    assert stop["last_record_collection"]["outcome"] == "denied"
    assert any("synthetic last-record denial" in warning for warning in reading.warnings)


def test_duplicate_lookup_outcomes_cannot_pair_an_old_row_with_a_new_failure():
    body = payload()
    body["collection"] = collection_for(body)
    first = body["collection"]["before"][0]
    body["collection"]["before"].append({**first, "outcome": "denied", "returned": 0, "error": "synthetic second lookup failure"})
    reading = crash(body)
    stop = next(s for s in reading.section("stops").data if (s["records"]["start"] or s["records"]["power_41"]) == first["anchor"])
    assert stop["last_record_before"] is None and stop["last_record_collection"]["outcome"] == "failed"
    assert "duplicate lookup outcomes" in stop["last_record_collection"]["error"]


def test_missing_lookup_metadata_does_not_certify_that_no_lookup_was_needed():
    body = payload()
    body["collection"] = collection_for(body)
    body["collection"]["before"] = []
    reading = crash(body)
    stops = [s for s in reading.section("stops").data if s["records"]["power_41"]]
    assert stops
    assert all(s["last_record_before"] is None for s in stops)
    assert all(s["last_record_collection"]["outcome"] == ("not_returned" if s["records"]["start"] else "not_requested") for s in stops)


def test_reports_beyond_a_capped_moment_window_keep_unknown_session_association():
    moment = "2026-08-01T00:00:00Z"
    body = payload(system=list(reversed(clean_sessions(24))), reports=[record(2000)], before=[])
    assert len(body["system"]) == module.record_cap(5, moment)
    body["collection"] = collection_for(body, moment=moment)
    body["collection"]["system"]["log_oldest"] = "2026-07-31T23:59:59.0000000Z"
    reading = crash(body, moment=moment)
    assert reading.outcome == "ok" and reading.count == 1
    assert reading.section("coverage").data["first_start"]["established"] is True
    stop = reading.section("stops").data[0]
    assert stop["reported_at"] and stop["started_at"] is None
    assert stop["records"]["start"] is None and stop["records"]["report"] == [2000]
    assert any("session association is unknown" in warning for warning in reading.warnings)
    assert any("record bound was reached" in warning for warning in reading.warnings)


# ---------------------------------------------------------------- faults


def test_the_faults_query_asks_the_three_selectors_and_the_window():
    script = faults_script(30, "")
    for provider in ("Application Error", "Application Hang", "Windows Error Reporting"):
        assert f"Provider[@Name='{provider}']" in script
    assert "EventData[Data[@Name='EventName']='LiveKernelEvent']" in script
    assert "-MaxEvents 31" in script and "NoMatchingEventsFound" in script
    assert "TimeCreated[@SystemTime" not in script

    at_boot = faults_script(30, "boot")
    assert "Win32_OperatingSystem" in at_boot and "TimeCreated[@SystemTime&gt;='$xpathStart']" in at_boot
    assert "TimeCreated[@SystemTime&gt;='2026-09-12T00:00:00.000Z']" in faults_script(30, "2026-09-12T00:00:00Z")
    anchored = faults_script(30, "2026-09-12T00:00:00Z", "2026-09-13T00:00:00Z")
    assert "@SystemTime&gt;='2026-09-12T00:00:00.000Z'" in anchored
    assert "@SystemTime&lt;'2026-09-13T00:00:00.002Z'" in anchored
    oldest = faults_script(30, "2026-09-12T00:00:00.1234567Z", "2026-09-13T00:00:00Z", "oldest")
    assert "Get-WinEvent -FilterXml ([xml]$xml) -Oldest -ErrorAction Stop" in oldest
    assert "probe_time =" in oldest
    assert f" -ge {module.exact_stamp('2026-09-12T00:00:00.1234567Z', 'since')[1]}" in oldest
    assert " -Oldest -ErrorAction Stop" not in anchored and "probe_time =" not in anchored
    with pytest.raises(ValueError, match="explicit inclusive timestamp"):
        faults_script(30, "", order="oldest")
    with pytest.raises(ValueError, match="explicit inclusive timestamp"):
        faults_script(30, "boot", order="oldest")


def test_a_window_that_is_neither_boot_nor_a_timestamp_is_refused():
    with pytest.raises(ValueError, match="boot"):
        since_clause("since lunch")
    with pytest.raises(ValueError, match="since"):
        faults(faults_fixture(), since="since lunch")
    with pytest.raises(ValueError, match="count"):
        faults(faults_fixture(), count=0)


def test_each_fault_is_named_with_its_application_its_module_and_its_exception():
    reading = faults(faults_fixture(), count=30)
    assert reading.outcome == "ok" and reading.count == 7  # count is the records; the decoding is below
    decoded = reading.section("decoded").data
    crash_entry = next(e for e in decoded if e["RecordId"] == 3000)
    assert crash_entry["kind"] == "application crash"
    assert crash_entry["fields"]["AppName"] == "example.exe" and crash_entry["fields"]["ModuleName"] == "ucrtbase.dll"
    assert crash_entry["fields"]["ProcessId"] == 4321
    assert crash_entry["exception"] == {"code": "0xc0000409", "name": "stack buffer overrun"}
    hang = next(e for e in decoded if e["RecordId"] == 3010)
    assert hang["kind"] == "application hang" and hang["fields"]["ExeFileName"] == "example.exe" and "exception" not in hang
    assert next(e for e in decoded if e["RecordId"] == 3002)["exception"] == {"code": "0xe0434352", "name": ".NET exception"}


def test_a_live_kernel_event_is_one_entry_per_report_not_one_per_record():
    decoded = decode_faults(faults_fixture())
    assert len(decoded) == 6  # seven records, two of them one report
    live = [e for e in decoded if e["kind"] == "live kernel event"]
    assert [e["RecordId"] for e in live] == [4000, 4100]  # the latest record of each report
    assert sorted(live[0]["report"]["records"]) == [3999, 4000]
    assert live[0]["report"]["code"] == "0x141" and live[0]["report"]["bucket"].startswith("LKD_0x141_Tdr")
    assert live[0]["report"]["dump_path"] == "C:\\Windows\\LiveKernelReports\\WATCHDOG\\WATCHDOG-20260912-1120.dmp"
    assert "bugcheck" not in live[0]  # the machine kept running: this is a report, not a stop


def test_the_summary_counts_what_failed_and_how_often():
    records = faults_fixture()
    summary = faults_summary(records, decode_faults(records))
    assert summary["by_kind"] == {"application crash": 3, "live kernel event": 2, "application hang": 1}
    assert summary["applications"] == [
        {"name": "example.exe", "count": 3, "first": "2026-09-12T09:15:00.0000000Z", "last": "2026-09-12T10:05:00.0000000Z", "modules": ["nvwgf2umx.dll", "ucrtbase.dll"]},
        {"name": "other.exe", "count": 1, "first": "2026-09-11T22:00:00.0000000Z", "last": "2026-09-11T22:00:00.0000000Z", "modules": ["KERNELBASE.dll"]},
    ]
    assert [e["code"] for e in summary["live_kernel"]] == ["0x141", "0x193"]
    assert summary["live_kernel"][0]["count"] == 1 and summary["live_kernel"][0]["last"] == "2026-09-12T11:20:30.0000000Z"


def test_faults_keeps_the_sections_apart():
    reading = faults(faults_fixture(), count=30)
    assert [(s.name, s.cls) for s in reading.sections] == [("records", "raw"), ("collection", "raw"), ("coverage", "derived"), ("decoded", "derived"), ("summary", "derived")]
    assert reading.section("decoded").basis and reading.section("summary").basis
    assert faults([], outcome="empty").outcome == "empty"


def test_faults_cutoff_limits_raw_and_derived_evidence_together():
    records = faults_fixture()
    reading = faults(records, count=2, since="boot")
    assert reading.count == 2
    assert reading.section("records").data == records[:2]
    assert reading.section("collection").data["truncated"] is True
    assert all(entry["RecordId"] in {row["RecordId"] for row in records[:2]} for entry in reading.section("decoded").data)
    assert any("older matching records" in warning for warning in reading.warnings)
    assert faults(records, count=2).warnings == []


def test_fault_window_reach_is_application_report_retention_only():
    reading = faults([], count=5, since="2026-09-01T00:00:00Z")
    assert reading.outcome == "empty"
    assert reading.section("collection").data["log"] == "Application"
    assert reading.section("coverage").data["log"] == "Application"
    assert reading.section("coverage").data["complete"] is True


def test_fault_window_future_end_is_pending_and_keeps_report_filing_times():
    report = faults_fixture()[0]
    result = log_collector_result([report], log="Application", limit=5,
                                  window_start="2026-09-01T00:00:00.000Z", window_end="2026-10-02T00:00:00.000Z",
                                  queried_at="2026-10-01T00:00:00.000Z")
    reading = asyncio.run(take("faults", FakeBridge(result), {"since": "2026-09-01T00:00:00Z", "before": "2026-10-02T00:00:00Z", "count": 5}))
    assert reading.section("records").data == [report]
    assert reading.section("coverage").data["covered_until"] == "2026-10-01T00:00:00.000Z"
    assert reading.section("coverage").data["complete"] is False


def test_fault_outside_window_row_is_decoded_but_cannot_prove_complete_reach():
    report = faults_fixture()[0]
    end = report["TimeCreated"]
    result = log_collector_result([report], log="Application", limit=5,
                                  window_start="2026-09-01T00:00:00.000Z", window_end=end,
                                  queried_at="2026-10-01T00:00:00.000Z")
    reading = asyncio.run(take("faults", FakeBridge(result), {"since": "2026-09-01T00:00:00Z", "before": end, "count": 5}))
    assert reading.section("records").data == [report]
    assert any(item["RecordId"] == report["RecordId"] for item in reading.section("decoded").data)
    assert reading.section("collection").data["row_issues"]["outside_window"] == 1
    assert reading.section("coverage").data["complete"] is False


def test_oldest_fault_window_uses_a_probe_for_exclusive_later_reach():
    start, end = "2026-09-12T10:00:00.0000000Z", "2026-09-12T12:00:00.0000000Z"
    times = ["2026-09-12T11:00:00.1234567Z", "2026-09-12T11:00:00.1234568Z"]
    rows = [{**faults_fixture()[0], "RecordId": 8000 + i, "TimeCreated": at} for i, at in enumerate(times)]
    result = log_collector_result(rows, log="Application", window_start=start, window_end=end,
                                  queried_at="2026-09-13T00:00:00.0000000Z", limit=2)
    result.items[0].update(truncated=True, probe_time="2026-09-12T11:00:00.1234569Z")
    reading = asyncio.run(take("faults", FakeBridge(result), {"since": start, "before": end, "count": 2, "order": "oldest"}))
    reach = reading.section("coverage").data
    assert reading.outcome == "ok" and [row["RecordId"] for row in reading.section("records").data] == [8000, 8001]
    assert reach["covered_from"] == start and reach["covered_from_inclusive"] is True
    assert reach["covered_until"] == "2026-09-12T11:00:00.1234569Z" and reach["complete"] is False
    assert reach["returned_time_ordered"] is True
    assert any("later matching records" in warning for warning in reading.warnings)


@pytest.mark.parametrize("change", [
    {"probe_time": None},
    {"probe_time": "2026-09-12T09:59:59.0000000Z"},
    {"window_end": "2026-09-12T12:00:00.0000001Z"},
])
def test_oldest_fault_window_keeps_rows_but_refuses_unverified_reach(change):
    start, end = "2026-09-12T10:00:00.0000000Z", "2026-09-12T12:00:00.0000000Z"
    row = {**faults_fixture()[0], "RecordId": 8000, "TimeCreated": "2026-09-12T11:00:00.0000000Z"}
    result = log_collector_result([row], log="Application", window_start=start, window_end=end,
                                  queried_at="2026-09-13T00:00:00.0000000Z", limit=1)
    result.items[0].update(truncated=True, probe_time="2026-09-12T11:00:01.0000000Z")
    result.items[0].update(change)
    reading = asyncio.run(take("faults", FakeBridge(result), {"since": start, "before": end, "count": 1, "order": "oldest"}))
    assert reading.outcome == "ok" and reading.section("records").data == [row]
    assert reading.section("coverage").data["covered_until"] is None
    assert reading.section("coverage").data["complete"] is False
    assert reading.warnings


def test_oldest_fault_window_clock_inversion_and_partial_stop_do_not_overclaim():
    start, end = "2026-09-12T10:00:00.0000000Z", "2026-09-12T12:00:00.0000000Z"
    first = {**faults_fixture()[0], "RecordId": 8100, "TimeCreated": "2026-09-12T11:00:10.0000000Z"}
    second = {**faults_fixture()[0], "RecordId": 8101, "TimeCreated": "2026-09-12T11:00:02.0000000Z"}
    result = log_collector_result([first, second], log="Application", window_start=start, window_end=end,
                                  queried_at="2026-09-13T00:00:00.0000000Z", limit=2)
    result.items[0]["probe_time"] = None
    params = {"since": start, "before": end, "count": 2, "order": "oldest"}
    inverted = asyncio.run(take("faults", FakeBridge(result), params))
    assert inverted.section("records").data == [first, second]
    assert inverted.section("coverage").data["returned_time_ordered"] is False
    assert inverted.section("coverage").data["covered_until"] is None
    result.items[0]["records"] = [second, first]
    result.items[0].update(truncated=None, stopped={"kind": "failed", "detail": "the log stopped"})
    stopped = asyncio.run(take("faults", FakeBridge(result), params))
    assert stopped.outcome == "ok" and stopped.section("coverage").data["covered_until"] == first["TimeCreated"]
    assert stopped.section("coverage").data["complete"] is False


def test_oldest_fault_window_future_end_is_incomplete_and_wrong_empty_echo_fails():
    start, end = "2026-09-12T10:00:00.0000000Z", "2026-09-12T12:00:00.0000000Z"
    result = log_collector_result([], log="Application", window_start=start, window_end=end,
                                  queried_at="2026-09-12T11:00:00.0000000Z", limit=2)
    result.items[0]["probe_time"] = None
    params = {"since": start, "before": end, "count": 2, "order": "oldest"}
    reading = asyncio.run(take("faults", FakeBridge(result), params))
    assert reading.outcome == "empty" and reading.section("coverage").data["covered_until"] == result.items[0]["queried_at"]
    assert reading.section("coverage").data["complete"] is False
    result.items[0]["window_end"] = "2026-09-12T12:00:00.0000001Z"
    wrong = asyncio.run(take("faults", FakeBridge(result), params))
    assert wrong.outcome == "failed" and wrong.count is None
    assert wrong.section("coverage").data["covered_until"] is None


# ---------------------------------------------------------------- the boundary


@pytest.fixture
def client():
    body = payload()
    body["collection"] = collection_for(body, count=1)
    bridge = FakeBridge(
        result=BridgeResult("ok", items=[body], took_ms=5),
        by_marker={"$env:COMPUTERNAME": identity_result("WORKBENCH", "someone"), log_collector_marker("Application"): log_collector_result(faults_fixture(), log="Application")},
    )
    app = create_app(State(bridge=bridge, token=TOKEN))
    with TestClient(app) as c:
        yield c


def test_the_catalog_lists_both_readings_with_their_parameters(client: TestClient):
    body = client.get("/api/readings", headers=AUTH).json()
    listed = {r["name"]: r for r in body["readings"]}
    assert [p["name"] for p in listed["crash"]["params"]] == ["count", "moment"]
    assert [p["name"] for p in listed["faults"]["params"]] == ["count", "since", "before", "order"]
    assert listed["crash"]["classes"] == ["raw", "derived"] and listed["faults"]["private"]
    assert REGISTRY["crash"].heavy is False


def test_oldest_faults_require_an_explicit_start_at_the_http_boundary(client: TestClient):
    missing = client.get("/api/readings/faults?order=oldest", headers=AUTH)
    boot = client.get("/api/readings/faults?order=oldest&since=boot", headers=AUTH)
    assert missing.status_code == 422 and "since" in missing.text
    assert boot.status_code == 422 and "since" in boot.text


def test_a_count_out_of_range_and_an_unreadable_moment_are_refused_by_the_route(client: TestClient):
    r = client.get("/api/readings/crash?count=50", headers=AUTH)
    assert r.status_code == 422 and "count" in r.json()["detail"]
    r = client.get("/api/readings/crash?moment=yesterday", headers=AUTH)
    assert r.status_code == 422 and "moment" in r.json()["detail"]
    r = client.get("/api/readings/faults?since=lunchtime", headers=AUTH)
    assert r.status_code == 422 and "since" in r.json()["detail"]


def test_a_stop_arrives_redacted_like_every_other_reading(client: TestClient):
    body = client.get("/api/readings/crash?count=1", headers=AUTH).json()
    assert body["outcome"] == "ok" and body["count"] == 1
    assert body["sections"][0]["data"][0]["MachineName"] == "<host>"
    assert "WORKBENCH" not in json.dumps(body)


# ---------------------------------------------------------------- on the host

STOP_KEYS = {
    "started_at", "announced_at", "stopped_at",
    "reported_at", "down_seconds", "bugcheck", "no_bugcheck_recorded",
    "power", "dump", "dump_inventory_complete", "last_record_before", "last_record_collection", "quiet_seconds", "records",
}


@pytest.mark.host
def test_crash_answers_on_this_machine():
    reading = asyncio.run(take("crash", real_bridge_or_skip(), {"count": 3}))
    assert reading.outcome in ("ok", "empty"), reading.error
    assert [s.name for s in reading.sections] == ["records", "decoded", "stops", "collection", "coverage"]
    for stop in reading.section("stops").data:
        assert set(stop) == STOP_KEYS
        assert set(stop["records"]) == {"start", "power_41", "eventlog_6008", "wer_1001", "report"}
        for moment in (stop["started_at"], stop["announced_at"], stop["stopped_at"]):
            assert moment is None or moment.endswith("Z")
    for entry in reading.section("decoded").data:
        if entry["kind"] == "unexpected shutdown":
            assert list(entry["fields"])[: len(KERNEL_POWER_41)] == list(KERNEL_POWER_41)


@pytest.mark.host
def test_a_moment_answers_on_this_machine():
    """Whatever this machine holds, the answer is one of the three: a stop, a clean start, or no
    start at all. Every one of them is an observation."""
    reading = asyncio.run(take("crash", real_bridge_or_skip(), {"count": 1, "moment": "2026-01-01T00:00:00Z"}))
    assert reading.outcome in ("ok", "empty"), reading.error
    assert reading.count == len(reading.section("stops").data)
    assert reading.outcome == "ok" or reading.warnings
    coverage = reading.section("coverage").data
    if coverage["system"]["reaches_moment"] is not True:
        assert coverage["first_start"]["established"] is False


@pytest.mark.host
def test_faults_answers_on_this_machine():
    reading = asyncio.run(take("faults", real_bridge_or_skip(), {"count": 20}))
    assert reading.outcome in ("ok", "empty"), reading.error
    if reading.outcome == "empty":
        return
    assert [s.name for s in reading.sections] == ["records", "collection", "coverage", "decoded", "summary"]
    kinds = {e["kind"] for e in reading.section("decoded").data}
    assert kinds <= {"application crash", "application hang", "live kernel event", "report"}
    for entry in reading.section("decoded").data:
        assert entry["fields"] and "RecordId" in entry
    assert set(reading.section("summary").data) == {"by_kind", "applications", "live_kernel"}


@pytest.mark.host
def test_a_window_since_boot_answers_on_this_machine():
    reading = asyncio.run(take("faults", real_bridge_or_skip(), {"count": 20, "since": "boot"}))
    assert reading.outcome in ("ok", "empty"), reading.error
