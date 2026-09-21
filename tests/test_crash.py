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
from tests.conftest import FakeBridge, identity_result, real_bridge_or_skip

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
        "dumps": doc["dumps"],
        "before": doc["before"],
        "warnings": [],
    }
    out.update(overrides)
    return out


def from_moment(moment: str) -> dict[str, Any]:
    """The same payload as the script returns for a moment: both logs from it, oldest first."""
    doc = load()
    keep = lambda rows: sorted([r for r in rows if r["TimeCreated"] >= moment], key=lambda r: r["TimeCreated"])
    return {"system": keep(doc["system"]), "reports": keep(doc["reports"]), "dumps": doc["dumps"], "before": doc["before"], "warnings": []}


def crash(body: dict[str, Any] | None = None, outcome: str = "ok", **params: Any):
    items = [body] if body is not None else []
    return asyncio.run(take("crash", FakeBridge(BridgeResult(outcome, items=items, took_ms=12)), params))


def faults(records: list[dict[str, Any]], outcome: str = "ok", **params: Any):
    return asyncio.run(take("faults", FakeBridge(BridgeResult(outcome, items=records, took_ms=8)), params))


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
        day = f"2026-08-{i + 1:02d}"
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


# ---------------------------------------------------------------- the queries


def test_one_launch_asks_both_logs_the_inventory_and_the_record_before_each_start():
    script = crash_script(5, None)
    for provider in ("Microsoft-Windows-Kernel-General", "Microsoft-Windows-Kernel-Power", "EventLog", "Microsoft-Windows-WER-SystemErrorReporting"):
        assert f"Provider[@Name='{provider}']" in script
    assert "EventData[Data[@Name='EventName']='BlueScreen']" in script
    assert "-MaxEvents 84" in script and "-MaxEvents 21" in script  # 12*5+24 records, 3*5+6 report records
    assert "Anchor = $anchor" in script and "-MaxEvents 1" in script
    assert "-Oldest" not in script
    # The dump inventory is the dumps reading's own query, embedded once and not written again.
    assert script.count("Get-ChildItem") == module.DUMPS_SCRIPT.count("Get-ChildItem")
    assert "C:\\Windows\\Minidump\\*.dmp" in script
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
    assert reading.section("decoded").basis.startswith("the positional properties named by this build's event manifests")


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


def test_a_stop_whose_start_is_beyond_the_logs_retention():
    stop = next(s for s in crash(payload(), count=5).section("stops").data if s["records"]["power_41"] == 900)
    assert stop["started_at"] is None and stop["announced_at"] == "2026-09-02T07:00:05.123Z"
    assert stop["no_bugcheck_recorded"] is True and stop["bugcheck"] is None
    assert stop["dump"] is None and stop["down_seconds"] is None
    assert stop["last_record_before"]["RecordId"] == 895  # taken before the announcement, there being no start


def test_a_report_older_than_the_system_log_is_a_stop_of_its_own():
    stop = next(s for s in crash(payload(), count=5).section("stops").data if s["records"]["report"] == [2100])
    assert stop["started_at"] is None and stop["announced_at"] is None and stop["stopped_at"] is None
    assert stop["bugcheck"]["code"] == "0x3b" and stop["bugcheck"]["source"] == "BlueScreen report"
    assert stop["bugcheck"]["bucket"].startswith("0x3b_c0000005_nt!")
    # The report named the file; the disk no longer has it, which is not the same as no dump.
    assert stop["dump"] == {"name": "070426-99999-01.dmp", "path": "C:\\Windows\\Minidump\\070426-99999-01.dmp", "bytes": None, "modified": None, "matched_by": "report"}


def test_a_start_whose_time_arrives_as_a_json_date_still_dates_the_stop():
    """ConvertTo-Json writes a DateTime property as /Date(milliseconds)/, which is how the host
    hands over Kernel-General 12's StartTime; a start the reading could not date would leave every
    stop without one."""
    from datetime import datetime, timezone

    body = payload()
    expected: dict[int, str] = {}
    for start in (r for r in body["system"] if r["Id"] == 12):
        moment = datetime.fromisoformat(start["Properties"][6].replace("Z", "+00:00"))
        start["Properties"][6] = f"/Date({int(moment.timestamp() * 1000)})/"
        expected[start["RecordId"]] = moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
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
    whole = next(f for f in body["dumps"] if f["name"] == "MEMORY.DMP")  # written in September, by a later stop

    stop = next(s for s in crash(body, count=5).section("stops").data if s["records"]["report"] == [2100])
    assert stop["dump"] == {"name": "070426-99999-01.dmp", "path": "C:\\Windows\\Minidump\\070426-99999-01.dmp", "bytes": None, "modified": None, "matched_by": "report"}

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
    assert reading.warnings == ["the query's record bound was reached after 0 stops; ask for fewer, or take `events` over the window"]


def test_a_sub_query_that_did_not_answer_is_a_warning_not_a_silence():
    reading = crash(payload(dumps=[], warnings=["The dump inventory did not read: access is denied."]), count=5)
    assert reading.warnings == ["The dump inventory did not read: access is denied."]
    assert stop_named(reading, "2026-09-12T06:14:58.000Z")["dump"] is None


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
        "no Kernel-Power 41 and no bug check report in that session"
    ]


def test_a_moment_with_no_start_after_it_is_empty_and_says_so():
    reading = crash(from_moment("2026-09-20T00:00:00.0000000Z"), count=5, moment="2026-09-20T00:00:00Z")
    assert reading.outcome == "empty" and reading.count == 0
    assert reading.warnings == ["no start follows 2026-09-20T00:00:00Z; nothing after it announced a stop"]


def test_a_log_that_held_nothing_at_all_is_empty_not_failed():
    reading = crash(None, outcome="empty", count=5)
    assert reading.outcome == "empty" and reading.count == 0 and reading.error is None
    assert [s.name for s in reading.sections] == ["records", "decoded", "stops"]


# ---------------------------------------------------------------- faults


def test_the_faults_query_asks_the_three_selectors_and_the_window():
    script = faults_script(30, "")
    for provider in ("Application Error", "Application Hang", "Windows Error Reporting"):
        assert f"Provider[@Name='{provider}']" in script
    assert "EventData[Data[@Name='EventName']='LiveKernelEvent']" in script
    assert "-MaxEvents 30" in script and "NoMatchingEventsFound" in script
    assert "TimeCreated[@SystemTime" not in script

    at_boot = faults_script(30, "boot")
    assert "Win32_OperatingSystem" in at_boot and "TimeCreated[@SystemTime&gt;='$since']" in at_boot
    assert "TimeCreated[@SystemTime&gt;='2026-09-12T00:00:00.000Z']" in faults_script(30, "2026-09-12T00:00:00Z")


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
    assert [(s.name, s.cls) for s in reading.sections] == [("records", "raw"), ("decoded", "derived"), ("summary", "derived")]
    assert reading.section("decoded").basis and reading.section("summary").basis
    assert faults([], outcome="empty").outcome == "empty"


# ---------------------------------------------------------------- the boundary


@pytest.fixture
def client():
    bridge = FakeBridge(
        result=BridgeResult("ok", items=[payload()], took_ms=5),
        by_marker={"$env:COMPUTERNAME": identity_result("WORKBENCH", "someone")},
    )
    app = create_app(State(bridge=bridge, token=TOKEN))
    with TestClient(app) as c:
        yield c


def test_the_catalog_lists_both_readings_with_their_parameters(client: TestClient):
    body = client.get("/api/readings", headers=AUTH).json()
    listed = {r["name"]: r for r in body["readings"]}
    assert [p["name"] for p in listed["crash"]["params"]] == ["count", "moment"]
    assert [p["name"] for p in listed["faults"]["params"]] == ["count", "since"]
    assert listed["crash"]["classes"] == ["raw", "derived"] and listed["faults"]["private"]
    assert REGISTRY["crash"].heavy is False


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
    "power", "dump", "last_record_before", "quiet_seconds", "records",
}


@pytest.mark.host
def test_crash_answers_on_this_machine():
    reading = asyncio.run(take("crash", real_bridge_or_skip(), {"count": 3}))
    assert reading.outcome in ("ok", "empty"), reading.error
    assert [s.name for s in reading.sections] == ["records", "decoded", "stops"]
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


@pytest.mark.host
def test_faults_answers_on_this_machine():
    reading = asyncio.run(take("faults", real_bridge_or_skip(), {"count": 20}))
    assert reading.outcome in ("ok", "empty"), reading.error
    if reading.outcome == "empty":
        return
    assert [s.name for s in reading.sections] == ["records", "decoded", "summary"]
    kinds = {e["kind"] for e in reading.section("decoded").data}
    assert kinds <= {"application crash", "application hang", "live kernel event", "report"}
    for entry in reading.section("decoded").data:
        assert entry["fields"] and "RecordId" in entry
    assert set(reading.section("summary").data) == {"by_kind", "applications", "live_kernel"}


@pytest.mark.host
def test_a_window_since_boot_answers_on_this_machine():
    reading = asyncio.run(take("faults", real_bridge_or_skip(), {"count": 20, "since": "boot"}))
    assert reading.outcome in ("ok", "empty"), reading.error
