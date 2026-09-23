"""Supported fault templates yield exact, independently qualified process facts."""

import json
from copy import deepcopy

from fastapi.testclient import TestClient

from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.readings.crash import decode, decode_faults, faults_script
from sentinel.readings.events import RECORD_SELECT
from sentinel.readings.fault_process import process_identity
from tests.conftest import LogBridge, identity_result
from tests.test_crash import faults_fixture
from tests.test_mcp import AUTH, TOKEN, rpc

EXAMPLE_TICKS = 134336772001234567
EXAMPLE_START = "2026-09-12T09:00:00.1234567Z"
LAST_CALENDAR_TICK = 2650467743999999999


def fault(record_id=3000):
    return next(record for record in faults_fixture() if record["RecordId"] == record_id)


def process_with(pid=4321, ticks=EXAMPLE_TICKS):
    record = fault()
    record["Properties"][8:10] = [pid, ticks]
    return process_identity(record)


def section(envelope, name):
    return next(section["data"] for section in envelope["sections"] if section["name"] == name)


def test_known_crash_and_hang_templates_preserve_raw_fields_and_source_positions():
    records = faults_fixture()
    before = deepcopy(records)
    entries = {entry["RecordId"]: entry for entry in decode_faults(records)}
    crash, hang, other = (entries[record_id] for record_id in (3000, 3010, 3002))
    for entry in (crash, hang):
        process = entry["process"]
        assert process["id"] == 4321 and process["id_status"] == "ok"
        assert process["created_at"] == EXAMPLE_START
        assert process["creation_filetime"] == str(EXAMPLE_TICKS)
        assert process["creation_resolution_ns"] == 100 and process["creation_status"] == "ok"
        assert process["id_reason"] is None and process["creation_reason"] is None
    assert crash["process"]["source"].items() >= {
        "provider_id": "a0e9b465-b939-57d7-b27d-95d8e925ff57", "event_id": 1000, "version": 0,
        "id_field": "ProcessId", "id_property_index": 8,
        "creation_field": "ProcessCreationTime", "creation_property_index": 9,
    }.items()
    assert hang["process"]["source"].items() >= {
        "provider_id": "c631c3dc-c676-59e4-2db3-5c0af00f9675", "event_id": 1002, "version": 0,
        "id_field": "ProcessId", "id_property_index": 2,
        "creation_field": "StartTime", "creation_property_index": 3,
    }.items()
    assert crash["process"]["source"]["creation_encoding"] == hang["process"]["source"]["creation_encoding"] == "FILETIME"
    assert "ProcessCreationTime" in crash["process"]["source"]["creation_basis"]
    assert "weaker support" in hang["process"]["source"]["creation_basis"]
    assert other["process"]["created_at"] == "2026-09-11T21:00:00.0000017Z"
    assert crash["fields"]["ProcessCreationTime"] == EXAMPLE_TICKS
    assert hang["fields"]["TerminationTime"] == 4294967295 and hang["fields"]["HangType"] == "Cross-thread"
    assert crash["exception"] == {"code": "0xc0000409", "name": "stack buffer overrun"}
    assert all("process" not in entry for entry in entries.values() if entry["kind"] == "live kernel event")
    assert records == before


def test_a_missing_or_bad_fact_does_not_discard_the_other_process_fact():
    for missing in (None, ""):
        missing_pid = process_with(pid=missing)
        assert missing_pid["id"] is None and missing_pid["id_status"] == "absent"
        assert missing_pid["created_at"] == EXAMPLE_START and missing_pid["creation_status"] == "ok"
        missing_start = process_with(ticks=missing)
        assert missing_start["id"] == 4321 and missing_start["id_status"] == "ok"
        assert missing_start["created_at"] is None and missing_start["creation_status"] == "absent"
        assert missing_start["creation_filetime"] is None and missing_start["creation_resolution_ns"] is None
    bad_pid = process_with(pid=1 << 32)
    assert bad_pid["id_status"] == "malformed" and bad_pid["id"] is None
    assert bad_pid["creation_status"] == "ok" and bad_pid["created_at"] == EXAMPLE_START
    bad_start = process_with(ticks=1 << 64)
    assert bad_start["id"] == 4321 and bad_start["id_status"] == "ok"
    assert bad_start["creation_status"] == "malformed" and bad_start["creation_filetime"] is None
    assert bad_pid["id_reason"] and bad_start["creation_reason"]


def test_exact_carriers_are_accepted_without_guessing_ambiguous_or_rounded_values():
    for pid, ticks in ((4321, EXAMPLE_TICKS), ("4321", str(EXAMPLE_TICKS)), ("0x10e1", hex(EXAMPLE_TICKS))):
        process = process_with(pid=pid, ticks=ticks)
        assert process["id"] == 4321 and process["created_at"] == EXAMPLE_START
        assert process["creation_filetime"] == str(EXAMPLE_TICKS)
    assert process_with(pid="0xFFFFFFFF")["id"] == 4294967295
    for invalid in (True, False, 4321.0, float(EXAMPLE_TICKS), "01dcf0a1b2c3d4e5", "+1", "-1", -1, "01", " 1", "1e3", {}):
        process = process_with(pid=invalid, ticks=invalid)
        assert process["id_status"] == process["creation_status"] == "malformed", repr(invalid)
        assert process["id"] is None and process["created_at"] is None
        assert process["creation_filetime"] is None and process["creation_resolution_ns"] is None


def test_filetime_keeps_one_tick_differences_at_epoch_second_rollover_and_calendar_limit():
    examples = (
        (1, "1601-01-01T00:00:00.0000001Z"),
        (9_999_999, "1601-01-01T00:00:00.9999999Z"),
        (10_000_000, "1601-01-01T00:00:01.0000000Z"),
        (EXAMPLE_TICKS, EXAMPLE_START),
        (EXAMPLE_TICKS + 1, "2026-09-12T09:00:00.1234568Z"),
        (LAST_CALENDAR_TICK, "9999-12-31T23:59:59.9999999Z"),
    )
    for ticks, expected in examples:
        process = process_with(ticks=ticks)
        assert process["created_at"] == expected, ticks
        assert process["creation_filetime"] == str(ticks) and process["creation_status"] == "ok"
        assert process["creation_resolution_ns"] == 100
    for ticks in (LAST_CALENDAR_TICK + 1, (1 << 64) - 1):
        process = process_with(ticks=ticks)
        assert process["created_at"] is None and process["creation_status"] == "unsupported"
        assert process["creation_filetime"] == str(ticks) and process["creation_resolution_ns"] == 100
        assert process["creation_reason"] and process["id"] == 4321


def test_zero_fields_remain_explicitly_unsupported_with_exact_zero_ticks_retained():
    process = process_with(pid=0, ticks=0)
    assert process["id"] is None and process["id_status"] == "unsupported"
    assert process["created_at"] is None and process["creation_status"] == "unsupported"
    assert process["creation_filetime"] == "0" and process["creation_resolution_ns"] == 100
    assert process["id_reason"] and process["creation_reason"]
    assert process_with(pid=0)["created_at"] == EXAMPLE_START
    assert process_with(ticks=0)["id"] == 4321


def test_a_start_after_the_event_warns_at_one_tick_precision_without_inventing_a_comparison():
    record = fault()
    for stamp in (EXAMPLE_START, "2026-09-12T05:00:00.1234567-04:00", "2026-09-12T10:00:00.1234567+01:00"):
        record["TimeCreated"] = stamp
        record["Properties"][9] = EXAMPLE_TICKS
        assert process_identity(record)["warnings"] == []
        record["Properties"][9] = EXAMPLE_TICKS + 1
        process = process_identity(record)
        assert len(process["warnings"]) == 1 and "later than the event" in process["warnings"][0]
        assert process["creation_status"] == "ok" and process["created_at"] == "2026-09-12T09:00:00.1234568Z"
        assert process["creation_filetime"] == str(EXAMPLE_TICKS + 1)
    for stamp in (None, "not a timestamp", "2026-09-12T09:00:00", "2026-02-30T09:00:00Z", "2026-09-12T09:00:00.1234567+00:60"):
        record["TimeCreated"] = stamp
        process = process_identity(record)
        assert process["warnings"] == [], stamp
        assert process["created_at"] == "2026-09-12T09:00:00.1234568Z"


def test_historical_or_unknown_metadata_cannot_authorize_positional_process_facts():
    original = fault()
    historical = {key: value for key, value in original.items() if key not in ("ProviderId", "Version")}
    unsupported = [
        historical,
        {**original, "ProviderId": "00000000-0000-0000-0000-000000000000"},
        {**original, "Version": 1},
        {**original, "Version": False},
        {**original, "Version": "0"},
        {**original, "Id": 1000.0},
    ]
    for record in unsupported:
        before = deepcopy(record)
        entry = decode(record)
        process = entry["process"]
        assert process["id_status"] == process["creation_status"] == "unsupported"
        assert process["id"] is None and process["creation_filetime"] is None and process["created_at"] is None
        assert set(process["source"]) == {"provider_id", "event_id", "version"}
        assert process["id_reason"] and process["creation_reason"]
        assert entry["exception"]["code"] == "0xc0000409" and entry["fields"]["ProcessId"] == 4321
        assert record == before
    uppercase = {**original, "ProviderId": original["ProviderId"].upper()}
    assert process_identity(uppercase)["id_status"] == "ok"
    assert process_identity({**original, "Id": 1001})["id_status"] == "unsupported"
    assert process_identity({**original, "ProviderName": "Another provider"}) is None


def test_a_short_appended_or_non_array_layout_keeps_process_positions_untrusted():
    original = fault()
    properties = original["Properties"]
    for value in (properties[:-1], [*properties, "unexpected"], tuple(properties), {str(i): item for i, item in enumerate(properties)}, None):
        record = {**original, "Properties": value}
        before = deepcopy(record)
        entry = decode(record)
        process = entry["process"]
        assert process["id_status"] == process["creation_status"] == "unsupported"
        assert process["id"] is None and process["created_at"] is None
        assert "id_property_index" not in process["source"] and "creation_property_index" not in process["source"]
        assert record == before
        if isinstance(value, list):
            assert entry["exception"]["code"] == "0xc0000409"
        else:
            assert entry["error"] and entry["fields"] == {}


def test_shared_projection_carries_template_identity_into_fault_queries():
    # This verifies query construction; the Windows host check establishes the returned shape.
    assert "ProviderId = " in RECORD_SELECT and "$_.ProviderId.ToString('D')" in RECORD_SELECT
    assert "Version = $_.Version" in RECORD_SELECT
    script = faults_script(2, "")
    assert "ProviderId = " in script and "Version = $_.Version" in script


def test_api_and_mcp_keep_process_facts_and_raw_ticks_exact_on_client_roundtrip():
    records = [fault(), fault(3010)]
    records[1]["Properties"][3] = EXAMPLE_TICKS + 1
    before = deepcopy(records)
    bridge = LogBridge(
        result=BridgeResult("unavailable", error="no synthetic answer"),
        by_marker={
            "$env:COMPUTERNAME": identity_result("WORKBENCH", "someone"),
            "Provider[@Name='Application Error']": BridgeResult("ok", items=records, took_ms=5),
        },
    )
    with TestClient(create_app(State(bridge=bridge, token=TOKEN))) as client:
        response = client.get("/api/readings/faults", headers=AUTH, params={"count": 2})
        assert response.status_code == 200
        envelope = response.json()
        result = rpc(client, "tools/call", {"name": "faults", "arguments": {"count": 2}})["result"]
        assert not result.get("isError")
        assert result["structuredContent"] == json.loads(result["content"][0]["text"])
        assert envelope["sections"] == result["structuredContent"]["sections"]
    raw, entries = section(envelope, "records"), section(envelope, "decoded")
    assert [raw[0]["Properties"][9], raw[1]["Properties"][3]] == [str(EXAMPLE_TICKS), str(EXAMPLE_TICKS + 1)]
    assert [entry["process"]["created_at"] for entry in entries] == [EXAMPLE_START, "2026-09-12T09:00:00.1234568Z"]
    assert [entry["process"]["creation_filetime"] for entry in entries] == [str(EXAMPLE_TICKS), str(EXAMPLE_TICKS + 1)]
    for record, entry in zip(raw, entries, strict=True):
        assert record["MachineName"] == "<host>"
        assert record["ProviderId"] == entry["process"]["source"]["provider_id"]
        assert record["Version"] == entry["process"]["source"]["version"] == 0
        assert process_identity(record) == entry["process"]
    assert records == before
