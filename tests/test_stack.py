"""The stack: what it accepts, what it refuses, what it composes, and that it outlives the process."""

import asyncio
import copy
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient
from mcp import types
from mcp.shared.exceptions import MCPError

from sentinel import __version__
from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.reading import take
from sentinel.readings.crash import STOPS_BASIS
from sentinel.readings.event_coverage import LOG_WINDOW_COVERAGE_BASIS
from sentinel.readings.events import RECORD_COVERAGE_BASIS
from sentinel.stack import PRESET_PROMPTS, STOP_REF_LOGS, Item, Prompts, Stack, StoreUnavailable, _item_lines
from tests.conftest import FakeBridge, LogBridge, identity_result, log_collector_result
from tests.test_crash import collection_for, faults_fixture, payload
from tests.test_crash import crash as take_crash_fixture
from tests.test_crash import faults as take_faults_fixture

TOKEN = "test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MCP_HEADERS = {**AUTH, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

EVENTS = [
    {
        "RecordId": 307001,
        "Id": 41,
        "Level": 1,
        "LevelDisplayName": "Critical",
        "ProviderName": "Microsoft-Windows-Kernel-Power",
        "MachineName": "TESTBOX",
        "TimeCreated": "2026-09-20T18:04:11.204Z",
        "Message": "The system has rebooted without cleanly shutting down first. | on TESTBOX for tester at C:\\Users\\tester\\x.",
    },
    {
        "RecordId": 307002,
        "Id": 6008,
        "Level": 2,
        "LevelDisplayName": "Error",
        "ProviderName": "EventLog",
        "MachineName": "TESTBOX",
        "TimeCreated": "2026-09-20T18:05:00.000Z",
        "Message": "The previous system shutdown was unexpected.",
    },
]


def app_for(bridge: FakeBridge) -> TestClient:
    return TestClient(create_app(State(bridge=bridge, token=TOKEN)))


@pytest.fixture
def bridge() -> FakeBridge:
    return LogBridge(
        result=BridgeResult("ok", items=EVENTS, took_ms=5),
        by_marker={"$env:COMPUTERNAME": identity_result("TESTBOX", "tester")},
    )


@pytest.fixture
def client(bridge: FakeBridge):
    with app_for(bridge) as c:
        c.bridge = bridge
        yield c


def add(client: TestClient, **body) -> dict:
    response = client.post("/api/stack/items", headers=AUTH, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def full_item(client: TestClient, item_id: str) -> dict:
    response = client.get(f"/api/stack/items/{item_id}", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def crash_envelope(*, incomplete_reports: bool = False) -> dict:
    body = payload()
    body["collection"] = collection_for(body, 5)
    if incomplete_reports:
        body["collection"]["reports"]["log_oldest"] = "2026-09-20T00:00:00Z"
    return take_crash_fixture(body, count=5).to_dict()


def faults_envelope() -> dict:
    return take_faults_fixture(faults_fixture(), count=30, since="2026-09-01T00:00:00Z").to_dict()


def handoff(envelope: dict, *, ids: list[int | str] | None = None, verbosity: str = "summary") -> str:
    return "\n".join(_item_lines(1, {"kind": "selection" if ids is not None else "reading", "reading": envelope, "ids": ids, "verbosity": verbosity}))


def projected_sections(text: str) -> list[dict]:
    return json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])


def test_dump_handoff_starts_with_interpretation_and_keeps_raw_bytes_available():
    envelope = {
        "reading": "dump_header", "params": {}, "asked_at": "2026-09-21T00:00:00Z", "outcome": "ok", "method": {"kind": "powershell"},
        "sections": [
            {"name": "file", "class": "raw", "data": {"name": "example.dmp"}},
            {"name": "header", "class": "raw", "data": {"bytes_hex": "DE AD BE EF"}},
            {"name": "inspection", "class": "derived", "data": {"format": "stream minidump", "exception": {"code": "0xC0000005"}}},
        ],
    }
    summary = "\n".join(_item_lines(1, {"kind": "reading", "title": "Dump inspection", "reading": envelope, "verbosity": "summary"}))
    full = "\n".join(_item_lines(1, {"kind": "reading", "title": "Dump inspection", "reading": envelope, "verbosity": "full"}))
    assert "example.dmp" in summary and "0xC0000005" in summary
    assert "DE AD BE EF" not in summary and "DE AD BE EF" in full


def test_change_handoff_leads_with_meaning_and_keeps_raw_selection_available():
    envelope = {
        "reading": "changes", "params": {}, "asked_at": "2026-09-21T00:00:00Z", "outcome": "ok", "count": 1,
        "method": {"kind": "powershell"},
        "sections": [
            {"name": "records", "class": "raw", "data": [{"Log": "System", "RecordId": 7, "Id": 19, "Data": {"updateTitle": "Synthetic update"}}]},
            {"name": "changes", "class": "derived", "basis": "Synthetic interpretation", "data": [{"at": "2026-09-20T00:00:00Z", "kind": "update_installed", "subject": "Synthetic update", "outside_window": True, "ref": {"log": "System", "record_id": 7}, "fields": {"updateTitle": "Synthetic update"}}]},
            {"name": "summary", "class": "derived", "data": {"returned": 1}},
            {"name": "collection", "class": "raw", "data": {"windows_update": {"outcome": "ok"}}},
            {"name": "coverage", "class": "derived", "data": {"windows_update": {"complete": False}}},
        ],
    }
    summary = "\n".join(_item_lines(1, {"kind": "reading", "title": "Changes", "reading": envelope, "verbosity": "summary"}))
    selected = "\n".join(_item_lines(1, {"kind": "reading", "title": "Changes", "reading": envelope, "verbosity": "full", "ids": ["System:7"]}))
    assert "update_installed" in summary and '"complete": false' in summary and '"outside_window": true' in summary
    assert '"fields"' not in summary and '"Id": 19' not in summary
    assert "update_installed" in selected and '"Id": 19' in selected
    assert selected.index('"kind": "update_installed"') < selected.index('"Id": 19')

    malformed = {**envelope, "sections": [*envelope["sections"][:1], {"name": "changes", "class": "derived", "data": [{"ref": None}, 1]}]}
    safe = "\n".join(_item_lines(1, {"kind": "selection", "title": "Malformed", "reading": malformed, "verbosity": "full", "ids": ["System:7"]}))
    assert '"Id": 19' in safe

    failed = {**envelope, "outcome": "failed", "error": {"detail": "one source failed"}}
    unsuccessful = "\n".join(_item_lines(1, {"kind": "reading", "title": "Changes", "reading": failed, "verbosity": "summary"}))
    assert "one source failed" in unsuccessful and '"complete": false' in unsuccessful

    unknown = {**envelope, "sections": [
        envelope["sections"][0],
        {**envelope["sections"][1], "data": [{**envelope["sections"][1]["data"][0], "outside_window": None}]},
        *envelope["sections"][2:],
    ]}
    unknown_summary = "\n".join(_item_lines(1, {"kind": "reading", "title": "Changes", "reading": unknown, "verbosity": "summary"}))
    assert '"outside_window": null' in unknown_summary
    inside = {**unknown, "sections": [
        unknown["sections"][0],
        {**unknown["sections"][1], "data": [{**unknown["sections"][1]["data"][0], "outside_window": False}]},
        *unknown["sections"][2:],
    ]}
    inside_summary = "\n".join(_item_lines(1, {"kind": "reading", "title": "Changes", "reading": inside, "verbosity": "summary"}))
    assert '"outside_window"' not in inside_summary

    failed_storm = {**failed, "reading": "storms"}
    storm_handoff = "\n".join(_item_lines(1, {"kind": "reading", "title": "Hardware errors", "reading": failed_storm, "verbosity": "summary"}))
    assert "one source failed" in storm_handoff and '"complete": false' in storm_handoff


def test_whea_handoff_keeps_header_severity_with_exact_cross_log_selection(client: TestClient):
    channel = "Microsoft-Windows-Kernel-WHEA/Errors"
    payload = "43504552" + "A1" * 128
    envelope = {
        "reading": "whea", "params": {"count": 2}, "asked_at": "2026-09-23T00:00:00Z", "outcome": "ok", "count": 2,
        "method": {"kind": "powershell"}, "sections": [
            {"name": "records", "class": "raw", "data": [
                {"Log": "System", "RecordId": 42, "TimeCreated": "2026-09-22T01:00:00Z", "LevelDisplayName": "Information", "RawData": "SYSTEM-BYTES"},
                {"Log": channel, "RecordId": 42, "TimeCreated": "2026-09-22T02:00:00Z", "LevelDisplayName": "Information", "RawData": payload},
            ]},
            {"name": "identity", "class": "derived", "data": [
                {"Log": "System", "RecordId": 42, "cper": {"severity": "corrected", "previous_session": False}},
                {"Log": channel, "RecordId": 42, "cper": {"severity": "fatal", "previous_session": True}},
            ]},
            {"name": "decoded", "class": "derived", "data": [
                {"Log": "System", "RecordId": 42, "decoded": {"kind": "synthetic System detail"}},
                {"Log": channel, "RecordId": 42, "error": "detail decoding deferred"},
            ]},
            {"name": "collection", "class": "raw", "data": {"limit": 2, "returned": 2, "truncated": False}},
            {"name": "coverage", "class": "derived", "data": {"complete": True}},
        ],
    }
    selected = "\n".join(_item_lines(1, {"kind": "selection", "title": "Channel record", "reading": envelope, "ids": [f"{channel}:42"], "verbosity": "full"}))
    assert '"severity": "fatal"' in selected and '"previous_session": true' in selected
    assert payload in selected and "SYSTEM-BYTES" not in selected
    assert '"severity": "corrected"' not in selected and "synthetic System detail" not in selected
    assert '"complete": true' in selected and "detail decoding deferred" in selected

    summary = "\n".join(_item_lines(1, {"kind": "reading", "title": "Hardware errors", "reading": envelope, "verbosity": "summary"}))
    assert '"severity": "fatal"' in summary and '"previous_session": true' in summary
    assert payload not in summary and "SYSTEM-BYTES" not in summary
    assert "bounded preview" not in summary  # saved full rows keep their original meaning

    add(client, kind="selection", ids=[f"{channel}:42"], envelope=envelope)
    composed = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert '"severity": "fatal"' in composed and '"previous_session": true' in composed
    assert payload not in composed and "<cper bytes withheld" in composed


def test_whea_preview_handoff_names_the_exact_read_and_original_lengths():
    envelope = {
        "reading": "whea", "params": {"count": 100}, "asked_at": "2026-09-23T03:00:00Z",
        "method": {"kind": "powershell"}, "outcome": "ok", "count": 1, "warnings": [],
        "sections": [
            {"name": "records", "class": "raw", "data": [{"Log": "System", "RecordId": 9,
                "TimeCreated": "2026-09-23T02:00:00Z", "Id": 18, "Level": 2,
                "Message": "short preview", "MessageChars": 2000, "PayloadBytes": 20480,
                "HeaderHex": "43504552"}]},
            {"name": "identity", "class": "derived", "data": [{"Log": "System", "RecordId": 9,
                "cper": {"severity": "fatal", "previous_session": False}}]},
            {"name": "collection", "class": "raw", "data": {"limit": 100, "returned": 1}},
            {"name": "coverage", "class": "derived", "data": {"complete": True}},
        ],
    }
    summary = "\n".join(_item_lines(1, {"kind": "reading", "title": "Preview", "reading": envelope, "verbosity": "summary"}))
    assert "bounded preview" in summary and "take whea_record" in summary
    assert '"MessageChars": 2000' in summary and '"PayloadBytes": 20480' in summary


def test_exact_whea_window_handoff_keeps_the_window_reach_with_a_selected_preview():
    envelope = {
        "reading": "whea_window", "params": {"source": "system", "since": "2026-09-22T00:00:00.0000000Z",
            "before": "2026-09-23T00:00:00.0000000Z", "order": "newest", "count": 25},
        "asked_at": "2026-09-23T03:00:00Z", "method": {"kind": "powershell"},
        "outcome": "ok", "count": 1, "warnings": [],
        "sections": [
            {"name": "records", "class": "raw", "data": [{"Log": "System", "RecordId": 9,
                "TimeCreated": "2026-09-22T02:00:00.1234567Z", "Id": 18, "Level": 2,
                "Message": "bounded preview", "MessageChars": 2000, "PayloadBytes": 20480,
                "HeaderHex": "43504552"}]},
            {"name": "identity", "class": "derived", "data": [{"Log": "System", "RecordId": 9,
                "cper": {"severity": "fatal", "previous_session": False}}]},
            {"name": "collection", "class": "raw", "data": {"source": "system", "limit": 25,
                "returned": 1, "truncated": False, "window_start": "2026-09-22T00:00:00.0000000Z"}},
            {"name": "coverage", "class": "derived", "data": {"complete": True,
                "covered_from": "2026-09-22T00:00:00.0000000Z", "covered_until": "2026-09-23T00:00:00.0000000Z"}},
        ],
    }
    item = {"kind": "selection", "title": "Window report", "reading": envelope,
            "verbosity": "summary", "ids": ["System:9"]}
    text = "\n".join(_item_lines(1, item))
    assert "bounded preview" in text and "take whea_record" in text
    assert '"covered_until": "2026-09-23T00:00:00.0000000Z"' in text
    assert '"RecordId": 9' in text


def test_an_exact_whea_report_keeps_previous_session_meaning_in_compact_and_selected_handoffs():
    channel = "Microsoft-Windows-Kernel-WHEA/Errors"
    record = {"Log": channel, "RecordId": 73, "TimeCreated": "2026-09-23T02:00:00Z", "Id": 20,
              "LevelDisplayName": "Information", "RawData": "SYNTHETIC-CPER-BYTES"}
    envelope = {
        "reading": "whea_record", "params": {"source": "kernel_whea", "record_id": 73},
        "asked_at": "2026-09-23T03:00:00Z", "method": {"kind": "powershell"},
        "outcome": "ok", "count": 1, "warnings": [],
        "sections": [
            {"name": "records", "class": "raw", "data": [record]},
            {"name": "identity", "class": "derived", "data": [
                {"Log": channel, "RecordId": 73, "cper": {"severity": "fatal", "previous_session": True,
                 "header_time": {"bytes": "2218160018031A14", "as_integers": "2026-03-24T22:24:34",
                                 "as_bcd": None, "reading": "as_integers", "precise": False, "reserved_bits": False}}}
            ]},
            {"name": "decoded", "class": "derived", "data": [{"Log": channel, "RecordId": 73, "error": "detail decoding deferred"}]},
            {"name": "collection", "class": "raw", "data": {"source": "kernel_whea", "outcome": "ok"}},
        ],
    }
    compact = "\n".join(_item_lines(1, {"kind": "reading", "title": "Exact report", "reading": envelope, "verbosity": "summary"}))
    assert '"previous_session": true' in compact and '"severity": "fatal"' in compact
    assert '"header_time"' in compact and '"reading": "as_integers"' in compact
    assert "SYNTHETIC-CPER-BYTES" not in compact and "detail decoding deferred" in compact
    selected = "\n".join(_item_lines(1, {"kind": "selection", "title": "Exact report", "reading": envelope,
                                       "ids": [f"{channel}:73"], "verbosity": "full"}))
    assert '"previous_session": true' in selected and '"severity": "fatal"' in selected
    assert '"header_time"' in selected and '"as_integers": "2026-03-24T22:24:34"' in selected
    assert "SYNTHETIC-CPER-BYTES" in selected and "detail decoding deferred" in selected


def test_a_week_of_storm_buckets_has_a_bounded_default_handoff_with_full_evidence_available(client: TestClient):
    from tests.test_whea import _powershell_stamp, load, storms

    moment = time.time()
    reading = storms(load(now=moment), host_now=moment, hours=168, bucket_seconds=60).to_dict()
    signatures = next(section["data"] for section in reading["sections"] if section["name"] == "signatures")
    signatures[0]["sample"]["Message"] = r"C:\Users\SentinelPrivateName\Desktop\synthetic.txt"
    collection = next(section["data"] for section in reading["sections"] if section["name"] == "collection")
    collection["system"]["log_error"] = r"C:\Users\SentinelPrivateName\Desktop\synthetic.log"
    saved = add(client, kind="reading", title="Synthetic storm week", envelope=reading)
    assert saved["verbosity"] == "summary"
    composed = client.get("/api/stack/composed", headers=AUTH)
    assert composed.status_code == 200
    assert len(composed.json()["text"]) < 10_000 and "Bounded storm summary" in composed.json()["text"]
    assert "SentinelPrivateName" not in composed.json()["text"]
    assert "<user>" in composed.json()["text"] and "synthetic.log" in composed.json()["text"]
    expanded = client.patch(f"/api/stack/items/{saved['id']}", headers=AUTH, json={"verbosity": "full"})
    assert expanded.status_code == 200 and expanded.json()["verbosity"] == "full"
    expanded_handoff = client.get("/api/stack/composed", headers=AUTH)
    assert expanded_handoff.status_code == 200 and len(expanded_handoff.json()["text"]) > 70_000
    assert "SentinelPrivateName" not in expanded_handoff.json()["text"]

    item = {"kind": "reading", "title": "Synthetic storm week", "reading": reading, "verbosity": "summary"}
    compact = "\n".join(_item_lines(1, item))
    full = "\n".join(_item_lines(1, {**item, "verbosity": "full"}))
    assert len(compact) < 8000 and len(full) > 70_000
    assert "Quiet does not clear Kernel-WHEA/Errors" in compact
    assert '"state": "burst"' in compact and '"complete": true' in compact
    assert '"bucket_count": 10080' in compact and '"active_buckets": 29' in compact
    assert '"other_active_buckets":' in compact and '"highlighted_active":' in compact and '"unknown_runs": 0' in compact
    assert '"top_signatures":' in compact and '"mci_status":' in compact
    assert '"header_unreadable_reasons":' in compact and '"not_marked_burst":' in compact
    assert '"sample"' not in compact and '"sample"' in full
    assert compact.count('"sample_ref":') == 3 and '"name": "reports"' not in compact

    detailed = storms(load(now=moment), host_now=moment, hours=168, bucket_seconds=60, references=True).to_dict()
    with_references = "\n".join(_item_lines(1, {**item, "reading": detailed}))
    assert len(with_references) < 8000
    assert with_references.count('"sample_ref":') == 3
    assert '"other_reports": 40' in with_references and '"shown": []' in with_references

    gap = storms([], outcome="empty", oldest=_powershell_stamp(moment - 12 * 3600), host_now=moment, hours=168).to_dict()
    unknown = "\n".join(_item_lines(1, {**item, "reading": gap}))
    assert '"state": "unknown"' in unknown and '"unknown_runs": 1' in unknown
    assert '"unknown_buckets":' in unknown and "does not cover" in unknown

    malformed = json.loads(json.dumps(reading))
    next(section for section in malformed["sections"] if section["name"] == "buckets")["data"]["totals"] = "broken"
    uncertain = "\n".join(_item_lines(1, {**item, "reading": malformed}))
    assert '"covered_buckets": null' in uncertain and '"unknown_runs": null' in uncertain

    warned = {**reading, "warnings": ["synthetic warning " + "x" * 400] * 12}
    bounded = "\n".join(_item_lines(1, {**item, "reading": warned}))
    assert "(+2 more in the stored reading)" in bounded and "…" in bounded
    assert len(bounded) < 10_000


def test_historical_storm_handoff_preserves_anchor_and_coverage_without_live_urgency():
    from tests.test_whea import _powershell_stamp, load, storms

    query_time = time.time()
    anchor_time = query_time - 3 * 86400
    reading = storms(load(now=anchor_time), host_now=query_time, before=_powershell_stamp(anchor_time),
                     oldest=_powershell_stamp(anchor_time - 2 * 86400), references=True).to_dict()
    compact = "\n".join(_item_lines(1, {"kind": "reading", "title": "Historical System reports",
                                         "reading": reading, "verbosity": "summary"}))
    assert len(compact) < 10_000
    assert 'before=' in compact and '"covered_until":' in compact
    assert '"queried_at":' in compact and '"highlighted_active":' in compact
    assert '"state": "burst"' not in compact and '"name": "status"' not in compact
    assert "No live burst, acceleration or quiet status is inferred by design" in compact
    assert '"name": "reports"' in compact and '"other_reports":' in compact


def test_old_saved_storm_handoff_keeps_missing_header_facts_unknown():
    from sentinel.stack import _bucket_rows
    from tests.test_whea import load, storms

    reading = storms(load(), references=True).to_dict()
    reading["sections"] = [section for section in reading["sections"] if section["name"] != "reports"]
    for section in reading["sections"]:
        data = section["data"]
        if section["name"] == "status":
            for key in ("recent_composition", "not_marked_peak", "not_marked_burst"):
                data.pop(key, None)
        elif section["name"] == "buckets":
            signatures = next(item["data"] for item in reading["sections"] if item["name"] == "signatures")
            old_rows, valid = _bucket_rows(data, signatures)
            assert valid
            data["active"] = old_rows
            data.pop("returned")
            data.pop("signature_pairs")
            for key in ("previous_session", "header_unreadable", "header_unreadable_reasons"):
                data.pop(key, None)
            for row in data["active"]:
                row.pop("previous_session", None)
                row.pop("header_unreadable", None)
        elif section["name"] == "signatures":
            for row in data:
                row.pop("previous_session", None)
                row.pop("header_unreadable", None)
                row["sample"].pop("previous_session", None)
    compact = "\n".join(_item_lines(1, {"kind": "reading", "title": "Older saved storm", "reading": reading, "verbosity": "summary"}))
    assert '"previous_session": null' in compact and '"header_unreadable": null' in compact
    assert '"not_marked_burst"' not in compact
    assert '"sample_ref":' in compact and '"name": "reports"' not in compact


@pytest.mark.parametrize("damage", [
    "unequal_columns", "duplicate_bucket", "outside_bucket", "negative_count", "non_utc_start",
    "bad_signature_position", "duplicate_signature_pair", "missing_signature_pairs",
    "wrong_pair_total", "wrong_covered_total", "impossible_header_counts",
])
def test_malformed_saved_sparse_buckets_do_not_make_a_false_handoff(damage: str):
    from tests.test_whea import load, storms

    reading = storms(load()).to_dict()
    buckets = next(section["data"] for section in reading["sections"] if section["name"] == "buckets")
    returned, pairs = buckets["returned"], buckets["signature_pairs"]
    if damage == "unequal_columns":
        returned["count"].pop()
    elif damage == "duplicate_bucket":
        returned["index"][1] = returned["index"][0]
    elif damage == "outside_bucket":
        returned["index"][0] = buckets["bucket_count"]
    elif damage == "negative_count":
        returned["count"][0] = -1
    elif damage == "non_utc_start":
        buckets["from"] = "2026-09-23T06:00:00-04:00"
    elif damage == "bad_signature_position":
        pairs["signature"][0] = 999
    elif damage == "missing_signature_pairs":
        buckets.pop("signature_pairs")
    elif damage == "wrong_pair_total":
        pairs["count"][0] += 1
    elif damage == "wrong_covered_total":
        buckets["totals"][returned["index"][0]] += 1
    elif damage == "impossible_header_counts":
        returned["previous_session"][0] = returned["count"][0] + 1
    else:
        for key in ("index", "signature", "count"):
            pairs[key].append(pairs[key][0])
    compact = "\n".join(_item_lines(1, {"kind": "reading", "title": "Older saved storm",
                                          "reading": reading, "verbosity": "summary"}))
    assert '"active_buckets": null' in compact
    assert '"other_active_buckets": null' in compact


def test_storm_handoff_counts_malformed_saved_references_without_losing_signature_samples():
    from tests.test_whea import load, storms

    reading = storms(load(), references=True).to_dict()
    report_section = next(section for section in reading["sections"] if section["name"] == "reports")
    report_section["data"] = [None, report_section["data"][0]]
    item = {"kind": "reading", "title": "Saved storm", "reading": reading, "verbosity": "summary"}
    compact = "\n".join(_item_lines(1, item))
    assert '"returned": 2' in compact and '"other_reports": 1' in compact and '"invalid_rows": 1' in compact
    assert '"sample_ref":' in compact

    report_section["data"] = {"broken": True}
    fallback = "\n".join(_item_lines(1, item))
    assert '"name": "reports"' in fallback and '"available": false' in fallback and '"sample_ref":' in fallback


def test_a_kernel_report_timeline_has_a_bounded_default_handoff(client: TestClient):
    returned = 150
    bucket_count = 10080
    reading = {
        "reading": "whea_reports", "outcome": "ok", "count": returned,
        "params": {"hours": 168, "bucket_seconds": 60}, "method": {"kind": "powershell", "query": "synthetic bounded query"},
        "asked_at": "2026-09-23T06:00:00Z", "error": None, "warnings": [],
        "sections": [
            {"name": "reports", "class": "derived", "data": [
                {"record_id": i + 1, "reported_at": "2026-09-23T06:00:00Z", "header": {"severity": "fatal", "previous_session": True}, "header_error": None}
                for i in range(returned)
            ]},
            {"name": "buckets", "class": "derived", "data": {
                "from": "2026-09-16T06:00:00Z", "to": "2026-09-23T06:00:00Z", "bucket_seconds": 60,
                "bucket_count": bucket_count, "total": returned, "unplaced": 0, "unknown_buckets": 0,
                "previous_session": returned, "header_unreadable": 0,
                "totals": [1] * returned + [0] * (bucket_count - returned),
                "active": [{"index": i, "start": "2026-09-16T06:00:00Z", "total": 1, "complete": True,
                            "previous_session": 1, "header_unreadable": 0} for i in range(returned)],
            }},
            {"name": "collection", "class": "raw", "data": {"kernel_whea": {"outcome": "ok", "returned": returned}}},
            {"name": "coverage", "class": "derived", "data": {"kernel_whea": {"complete": True}}},
        ],
    }
    saved = add(client, kind="reading", title="Synthetic Kernel-WHEA reports", envelope=reading)
    assert saved["verbosity"] == "summary"
    compact = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert len(compact) < 10_000 and "Bounded Kernel-WHEA report-time summary" in compact
    assert '"previous_session": 150' in compact and '"other_reports": 140' in compact
    assert '"highlighted_active":' in compact and '"totals":' not in compact
    expanded = client.patch(f"/api/stack/items/{saved['id']}", headers=AUTH, json={"verbosity": "full"})
    assert expanded.status_code == 200
    full = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert len(full) > len(compact) * 10 and '"totals":' in full


def test_a_malformed_supplied_envelope_is_refused_and_an_older_bad_item_does_not_break_the_handoff(client: TestClient):
    from sentinel.stack import render

    malformed = {"reading": "storms", "outcome": "ok", "params": {}, "method": {}, "sections": [1]}
    response = client.post("/api/stack/items", headers=AUTH, json={"kind": "reading", "envelope": malformed})
    assert response.status_code == 422 and "envelope" in response.json()["detail"]

    older = {"kind": "reading", "title": "Older stored item", "reading": {**malformed, "params": [], "method": [], "sections": 1, "outcome": ["ok"], "error": []}}
    note = {"kind": "note", "title": "Person's note", "note": "The machine restarted while idle."}
    handoff = render(None, [older, note])
    assert "stored outcome is malformed" in handoff and "The machine restarted while idle." in handoff


def test_the_stack_starts_empty_with_a_prompt_chosen(client: TestClient):
    state = client.get("/api/stack", headers=AUTH).json()
    assert state["items"] == [] and state["system_prompt"] is True
    assert state["prompt_id"] == "quantum-diagnostician"


def test_stack_index_keeps_provenance_without_looking_like_a_partial_reading(client: TestClient):
    reading = add(client, kind="reading", take={"name": "events", "params": {"count": 2}})
    note = add(client, kind="note", note="Compare the two stops")
    expected = {"id", "added_at", "kind", "title", "rank", "verbosity", "ids", "note", "provenance"}
    for item in (reading, note):
        assert set(item) == expected | {"redacted", "redaction_gaps"}
        assert item["redaction_gaps"] == []
        assert "reading" not in item and "sections" not in item and "method" not in item
    saved = full_item(client, reading["id"])["reading"]
    assert reading["provenance"] == {"reading": "events", "params": saved["params"],
                                     "asked_at": saved["asked_at"], "outcome": "ok", "count": 2,
                                     "origin": "taken", "sentinel_version": __version__}
    assert note["provenance"] is None
    listed = client.get("/api/stack", headers=AUTH).json()
    assert listed["items"] == [{key: value for key, value in item.items() if key not in ("redacted", "redaction_gaps")} for item in (reading, note)]
    assert full_item(client, reading["id"])["reading"]["sections"]
    assert client.get("/api/stack/items/nope", headers=AUTH).status_code == 404


def test_stack_index_redacts_its_own_fields_while_exact_item_keeps_full_evidence(client: TestClient):
    held = client.get("/api/readings/events?count=1", headers=AUTH).json()
    held["params"]["path"] = r"C:\Users\tester\dump.dmp"
    item = add(client, kind="reading", envelope=held, title=r"TESTBOX C:\Users\tester\dump.dmp")
    default = client.get("/api/stack", headers=AUTH).json()
    assert "TESTBOX" not in json.dumps(default) and "tester" not in json.dumps(default)
    assert "<host>" in default["items"][0]["title"] and "<user>" in default["items"][0]["title"]
    assert "redacted" in default and "reading" not in default["items"][0]
    exact = full_item(client, item["id"])
    assert "TESTBOX" not in json.dumps(exact)
    raw = client.get(f"/api/stack/items/{item['id']}?unredacted=true", headers=AUTH).json()
    assert "TESTBOX" in raw["title"] and raw["reading"]["params"]["path"] == held["params"]["path"]
    assert raw == client.app.state.sentinel.stack.item(item["id"])


def test_malformed_saved_reading_has_unknown_index_fields_without_losing_the_item(client: TestClient):
    item = add(client, kind="reading", take={"name": "events", "params": {"count": 2}})
    path = client.app.state.sentinel.stack.store.path
    state = json.loads(path.read_text())
    reading = state["items"][0]["reading"]
    reading["params"] = ["wrong shape"]
    reading["asked_at"] = 17
    reading.pop("outcome")
    reading["count"] = "3"
    path.write_text(json.dumps(state))
    before = path.read_bytes()
    listed = client.get("/api/stack", headers=AUTH).json()["items"][0]
    assert listed["id"] == item["id"] and listed["provenance"] == {
        "reading": "events", "params": None, "asked_at": None, "outcome": None, "count": None,
        "origin": "taken", "sentinel_version": __version__,
    }
    assert full_item(client, item["id"])["reading"]["params"] == ["wrong shape"]
    assert path.read_bytes() == before

    state["items"][0]["reading"] = None
    path.write_text(json.dumps(state))
    missing = client.get("/api/stack", headers=AUTH).json()["items"][0]
    assert missing["kind"] == "reading" and missing["provenance"] == {
        "reading": None, "params": None, "asked_at": None, "outcome": None, "count": None,
        "origin": "taken", "sentinel_version": None,
    }
    assert full_item(client, item["id"])["reading"] is None


def test_stack_index_size_does_not_scale_with_stored_record_rows(client: TestClient):
    state = client.app.state.sentinel.stack
    reading = {"reading": "record", "params": {"log": "System", "count": 2000},
               "asked_at": "2026-09-24T00:00:00Z", "outcome": "ok", "count": 2000,
               "method": {"query": "Get-WinEvent"},
               "sections": [{"name": "records", "class": "raw", "data": [
                   {"RecordId": number, "Message": "M" * 1024, "Properties": ["AA" * 128]}
                   for number in range(2000)
               ]}]}
    for number in range(3):
        state.add(Item(id=f"synthetic-{number}", added_at="2026-09-24T00:00:00Z", kind="selection",
                       title=f"Selected {number}", reading=reading, ids=[number]))
    full_bytes = len(json.dumps(state.state(), separators=(",", ":")).encode())
    response = client.get("/api/stack", headers=AUTH)
    index_bytes = len(response.content)
    mcp = call(client, "stack_list")["content"][0]["text"]
    assert response.status_code == 200 and len(mcp.encode()) < 2_000
    assert full_bytes > 7_000_000 and index_bytes < 2_000
    assert index_bytes < full_bytes // 1000

    def compact(payload: dict) -> None:
        entries = payload["items"] if "items" in payload else [payload]
        assert len(json.dumps(payload).encode()) < 2_000
        assert all("reading" not in entry and "sections" not in entry and "method" not in entry for entry in entries)

    compact(client.patch("/api/stack", headers=AUTH, json={"system_prompt": False}).json())
    compact(client.patch("/api/stack/items/synthetic-0", headers=AUTH, json={"title": "First selection"}).json())
    compact(json.loads(call(client, "stack_update", {"id": "synthetic-0", "rank": 1})["content"][0]["text"]))
    compact(json.loads(call(client, "stack_prompt", {"system_prompt": True})["content"][0]["text"]))
    compact(json.loads(call(client, "stack_remove", {"id": "synthetic-0"})["content"][0]["text"]))
    compact(client.delete("/api/stack/items/synthetic-1", headers=AUTH).json())
    compact(json.loads(call(client, "stack_clear")["content"][0]["text"]))


def test_adding_a_reading_takes_it_now_and_keeps_its_provenance(client: TestClient):
    item = add(client, kind="reading", take={"name": "events", "params": {"count": 2}})
    assert item["kind"] == "reading" and item["rank"] == 3 and item["verbosity"] == "full"
    assert item["title"] == "events (log=System, levels=1,2, count=2, order=newest)"
    assert "reading" not in item and item["provenance"]["outcome"] == "ok"
    assert item["provenance"]["asked_at"] and item["provenance"]["params"]["count"] == 2
    saved = full_item(client, item["id"])
    assert saved["reading"]["method"]["kind"] == "powershell"
    assert saved["reading"]["sections"][0]["data"][0]["MachineName"] == "<host>"


def test_one_observation_is_idempotent_but_a_new_take_is_new_evidence(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from sentinel import reading as reading_module

    stamps = iter(("2026-09-20T18:00:00.000Z", "2026-09-20T18:00:01.000Z", "2026-09-20T18:00:02.000Z"))
    monkeypatch.setattr(reading_module, "_now", lambda: next(stamps))
    first = add(client, kind="reading", take={"name": "events", "params": {"count": 2}})
    held = full_item(client, first["id"])["reading"]
    again = client.post("/api/stack/items", headers=AUTH, json={"kind": "reading", "envelope": held})
    assert again.status_code == 409 and again.json()["id"] == first["id"]
    assert again.json()["asked_at"] == first["provenance"]["asked_at"]
    later = add(client, kind="reading", take={"name": "events", "params": {"count": 2}})
    assert later["id"] != first["id"]
    assert first["provenance"]["asked_at"] in client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert later["provenance"]["asked_at"] in client.get("/api/stack/composed", headers=AUTH).json()["text"]
    # Different parameters and a selection of the same reading are distinct too.
    assert client.post("/api/stack/items", headers=AUTH, json={"kind": "reading", "take": {"name": "events", "params": {"count": 5}}}).status_code == 201
    assert client.post("/api/stack/items", headers=AUTH, json={"kind": "selection", "ids": [307001], "envelope": held}).status_code == 201


def test_equivalent_time_and_record_id_forms_are_one_observation(client: TestClient):
    held = client.get("/api/readings/events?count=2", headers=AUTH).json()
    held["sections"][0]["data"][0]["Log"] = "System"
    first = add(client, kind="selection", ids=[307001], envelope=held)
    equivalent = {**held, "asked_at": held["asked_at"].replace(".000Z", "Z")}
    if equivalent["asked_at"] == held["asked_at"]:
        equivalent["asked_at"] = held["asked_at"].replace("Z", "+00:00")
    again = client.post("/api/stack/items", headers=AUTH, json={"kind": "selection", "ids": ["System:307001"], "envelope": equivalent})
    assert again.status_code == 409 and again.json()["id"] == first["id"]


@pytest.mark.parametrize("asked_at", [None, 17, "2026-09-20T18:00:00", "not a time"])
def test_supplied_stack_observation_needs_an_explicit_time(client: TestClient, asked_at):
    held = client.get("/api/readings/events?count=2", headers=AUTH).json()
    held["asked_at"] = asked_at
    response = client.post("/api/stack/items", headers=AUTH, json={"kind": "reading", "envelope": held})
    assert response.status_code == 422 and client.get("/api/stack", headers=AUTH).json()["items"] == []


@pytest.mark.parametrize("contents", [b"", b"{broken", b"\xff", b"[]", b'{"items":[{}]}'])
def test_unreadable_stack_is_reported_and_never_overwritten(client: TestClient, contents: bytes):
    path = client.app.state.sentinel.stack.store.path
    path.write_bytes(contents)
    for method, route, body in (
        ("get", "/api/stack", None),
        ("get", "/api/stack/items/one", None),
        ("get", "/api/stack/composed", None),
        ("post", "/api/stack/items", {"kind": "note", "note": "new evidence"}),
        ("patch", "/api/stack", {"system_prompt": False}),
        ("delete", "/api/stack", None),
    ):
        response = client.request(method, route, headers=AUTH, json=body)
        assert response.status_code == 503 and response.json()["error"] == "saved_context_unavailable"
        assert response.json()["reason"] == "invalid"
        assert path.read_bytes() == contents


def test_unreadable_prompt_library_is_reported_and_never_reseeded(client: TestClient):
    path = client.app.state.sentinel.prompts.store.path
    path.write_bytes(b"{broken")
    for method, route, body in (
        ("get", "/api/prompts", None),
        ("post", "/api/prompts", {"name": "New prompt"}),
    ):
        response = client.request(method, route, headers=AUTH, json=body)
        assert response.status_code == 503 and path.read_bytes() == b"{broken"
    composed = client.get("/api/stack/composed", headers=AUTH)
    assert composed.status_code == 200
    assert composed.json()["prompt"] == {"id": "quantum-diagnostician", "state": "unavailable", "reason": "invalid"}
    assert "prompt library could not be used (invalid)" in composed.json()["text"]
    assert composed.json()["stack"]["items"] == [] and path.read_bytes() == b"{broken"


def test_missing_selected_prompt_is_explicit_in_handoff_and_mcp(client: TestClient):
    add(client, kind="note", note="Keep this evidence")
    client.patch("/api/stack", headers=AUTH, json={"prompt_id": "does-not-exist"})
    body = client.get("/api/stack/composed", headers=AUTH).json()
    assert body["prompt"] == {"id": "does-not-exist", "state": "missing", "reason": None}
    assert "chosen prompt no longer exists" in body["text"] and "Keep this evidence" in body["text"]
    assert len(body["stack"]["items"]) == 1
    answer = call(client, "compose")
    assert answer["structuredContent"]["prompt"] == body["prompt"]
    assert "chosen prompt no longer exists" in answer["content"][0]["text"]
    resource = asyncio.run(client.app.state.mcp_surface.read_resource(None, types.ReadResourceRequestParams(uri="sentinel://handoff")))
    assert "chosen prompt no longer exists" in resource.contents[0].text


def test_composed_index_and_text_share_one_snapshot(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    stack = client.app.state.sentinel.stack
    add(client, kind="note", note="Earlier evidence")
    original_state = stack.state
    calls = 0

    def edit_after_snapshot():
        nonlocal calls
        calls += 1
        snapshot = original_state()
        stack.add(Item(id="later", added_at="2026-09-24T00:00:01Z", kind="note", title="Later", note="Later evidence"))
        return snapshot

    monkeypatch.setattr(stack, "state", edit_after_snapshot)
    body = client.get("/api/stack/composed", headers=AUTH).json()
    assert calls == 1
    assert [item["note"] for item in body["stack"]["items"]] == ["Earlier evidence"]
    assert "Earlier evidence" in body["text"] and "Later evidence" not in body["text"]
    assert len(original_state()["items"]) == 2


def test_mcp_resources_name_unavailable_saved_context(client: TestClient):
    surface = client.app.state.mcp_surface
    path = client.app.state.sentinel.stack.store.path
    path.write_bytes(b"{broken")
    answer = call(client, "stack_list")
    assert answer.get("isError") is True and "invalid: stack.json is not valid JSON" in answer["content"][0]["text"]
    with pytest.raises(MCPError, match="invalid: stack.json is not valid JSON"):
        asyncio.run(surface.read_resource(None, types.ReadResourceRequestParams(uri="sentinel://handoff")))
    path.unlink()
    prompt_path = client.app.state.sentinel.prompts.store.path
    prompt_path.write_bytes(b"{broken")
    with pytest.raises(MCPError, match="prompts.json is not valid JSON"):
        asyncio.run(surface.list_prompts())


@pytest.mark.parametrize("arguments", [
    {"system_prompt": "false"}, {"system_prompt": 1}, {"prompt_id": {"x": 1}},
])
def test_mcp_cannot_write_a_stack_state_it_would_refuse_to_read(client: TestClient, arguments):
    surface = client.app.state.mcp_surface
    path = client.app.state.sentinel.stack.store.path
    result = asyncio.run(surface.call_tool(None, types.CallToolRequestParams(name="stack_prompt", arguments=arguments)))
    assert result.is_error is True
    assert not path.exists()
    assert client.get("/api/stack", headers=AUTH).status_code == 200


def test_mcp_cannot_write_a_nontext_title(client: TestClient):
    surface = client.app.state.mcp_surface
    result = asyncio.run(surface.call_tool(None, types.CallToolRequestParams(name="stack_add", arguments={"kind": "note", "note": "Observation", "title": {"bad": True}})))
    assert result.is_error is True
    assert client.get("/api/stack", headers=AUTH).json()["items"] == []


def test_transient_stack_read_failure_is_not_an_empty_stack(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    path = client.app.state.sentinel.stack.store.path
    original = Path.read_text

    def unavailable(self: Path, *args, **kwargs):
        if self == path:
            raise PermissionError("simulated temporary file lock")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unavailable)
    response = client.post("/api/stack/items", headers=AUTH, json={"kind": "note", "note": "new evidence"})
    assert response.status_code == 503 and response.json()["reason"] == "unreadable"
    assert path.exists() is False


def test_separate_store_instances_keep_concurrent_additions(tmp_path):
    path = tmp_path / "stack.json"

    def add_note(index: int) -> None:
        stack = Stack(path)
        from sentinel.stack import Item
        stack.add(Item(id=str(index), added_at=f"2026-09-23T00:00:{index:02d}Z", kind="note", title=str(index), note=str(index)))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(add_note, range(20)))
    assert {item["id"] for item in Stack(path).state()["items"]} == {str(index) for index in range(20)}


def test_separate_prompt_libraries_keep_concurrent_edits(tmp_path):
    path = tmp_path / "prompts.json"

    def add_prompt(index: int) -> None:
        Prompts(path).add(f"Prompt {index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(add_prompt, range(20)))
    prompts = Prompts(path).all()
    assert len(prompts) == len(PRESET_PROMPTS) + 20
    assert {prompt["name"] for prompt in prompts} >= {f"Prompt {index}" for index in range(20)}


def test_a_stack_reader_holds_the_same_lock_as_a_writer(tmp_path):
    from sentinel.stack import Item

    path = tmp_path / "stack.json"
    reader, writer = Stack(path), Stack(path)
    writer.add(Item(id="first", added_at="2026-09-23T00:00:00Z", kind="note", title="first", note="first"))
    started, release = Event(), Event()

    def held_read():
        with path.open(encoding="utf-8") as handle:
            started.set()
            assert release.wait(5)
            return json.load(handle)

    reader.store.read = held_read
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(reader.state)
        assert started.wait(5)
        writing = pool.submit(writer.add, Item(id="second", added_at="2026-09-23T00:00:01Z", kind="note", title="second", note="second"))
        time.sleep(0.05)
        assert not writing.done()
        release.set()
        reading.result(timeout=5)
        writing.result(timeout=5)
    assert {item["id"] for item in writer.state()["items"]} == {"first", "second"}


def test_separate_stores_queue_before_the_bounded_file_lock(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from sentinel import stack as stack_module
    from sentinel.performance import locked as file_locked

    path = tmp_path / "stack.json"
    first, second = Stack(path), Stack(path)
    assert Stack(tmp_path / "child" / ".." / "stack.json").store._lock is first.store._lock
    monkeypatch.setattr(stack_module, "locked", lambda file: file_locked(file, timeout=0.05))
    entered, release = Event(), Event()

    def hold_first_store():
        with first.store.transaction():
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        held = pool.submit(hold_first_store)
        assert entered.wait(5)
        waiting = pool.submit(second.state)
        try:
            time.sleep(0.15)
            assert not waiting.done()
        finally:
            release.set()
        held.result(timeout=5)
        assert waiting.result(timeout=5)["items"] == []


def test_local_queue_still_respects_a_separate_file_lock_holder(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from sentinel import stack as stack_module
    from sentinel.performance import locked as file_locked

    path = tmp_path / "stack.json"
    Stack(path).add(Item(id="saved", added_at="2026-09-24T00:00:00Z", kind="note", title="saved", note="saved"))
    original = path.read_bytes()
    monkeypatch.setattr(stack_module, "locked", lambda file: file_locked(file, timeout=0.05))
    with file_locked(tmp_path / "stack.json.lock"):
        with pytest.raises(StoreUnavailable) as blocked:
            Stack(path).add(Item(id="one", added_at="2026-09-24T00:00:00Z", kind="note", title="one", note="one"))
    assert blocked.value.reason == "busy"
    assert path.read_bytes() == original


def test_lock_open_failure_is_unreadable_without_waiting_for_contention(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "stack.json"
    lock_path = tmp_path / "stack.json.lock"
    original_open = Path.open
    attempts = 0

    def blocked_open(self: Path, *args, **kwargs):
        nonlocal attempts
        if self == lock_path:
            attempts += 1
            raise PermissionError("synthetic lock file refusal")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", blocked_open)
    with pytest.raises(StoreUnavailable) as unavailable:
        Stack(path).state()
    assert unavailable.value.reason == "unreadable" and attempts == 1


def test_hard_exit_leaves_old_stack_intact_and_preserves_recovery_scratch(tmp_path):
    path = tmp_path / "stack.json"
    Stack(path).add(Item(id="saved", added_at="2026-09-24T00:00:00Z", kind="note", title="saved", note="saved"))
    original = path.read_bytes()
    child = """
import os
import sys
from pathlib import Path
from sentinel.stack import Item, Stack
path = Path(sys.argv[1])
Path.replace = lambda self, target: os._exit(3)
Stack(path).add(Item(id='interrupted', added_at='2026-09-24T00:00:01Z', kind='note', title='interrupted', note='interrupted'))
"""
    result = subprocess.run([sys.executable, "-c", child, str(path)], cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=20, check=False)
    assert result.returncode == 3, result.stderr.decode(errors="replace")
    assert path.read_bytes() == original
    orphans = list(tmp_path.glob(".stack.json.*.tmp"))
    assert len(orphans) == 1 and orphans[0].stat().st_size > 0
    unrelated = [tmp_path / ".stack.json.nothex.tmp", tmp_path / f".prompts.json.{'a' * 32}.tmp", tmp_path / "stack.json.bak"]
    for file in unrelated:
        file.write_text("leave alone")
    assert [item["id"] for item in Stack(path).state()["items"]] == ["saved"]
    Stack(path).clear()
    assert orphans[0].exists() and all(file.exists() for file in unrelated)


def test_interrupted_first_save_does_not_look_committed_or_erase_scratch(tmp_path):
    path = tmp_path / "stack.json"
    child = """
import os
import sys
from pathlib import Path
from sentinel.stack import Item, Stack
Path.replace = lambda self, target: os._exit(3)
Stack(Path(sys.argv[1])).add(Item(id='first', added_at='2026-09-24T00:00:00Z', kind='note', title='first', note='first'))
"""
    result = subprocess.run([sys.executable, "-c", child, str(path)], cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=20, check=False)
    assert result.returncode == 3, result.stderr.decode(errors="replace")
    orphan = next(tmp_path.glob(".stack.json.*.tmp"))
    assert not path.exists()
    assert Stack(path).state()["items"] == []
    assert orphan.exists()  # an interrupted first save is available for manual recovery


def test_save_failure_distinguishes_unapplied_and_uncertain_changes(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    path = client.app.state.sentinel.stack.store.path
    first = client.post("/api/stack/items", headers=AUTH, json={"kind": "note", "note": "first"})
    assert first.status_code == 201
    original = path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError(28, "synthetic full disk")))
        failed = client.post("/api/stack/items", headers=AUTH, json={"kind": "note", "note": "second"})
    assert failed.status_code == 503 and failed.json()["reason"] == "not_saved"
    assert path.read_bytes() == original

    original_replace = Path.replace
    with monkeypatch.context() as patch:
        def replaced_then_lost_reply(self: Path, target: Path):
            original_replace(self, target)
            raise OSError("synthetic lost replacement reply")

        patch.setattr(Path, "replace", replaced_then_lost_reply)
        uncertain = client.post("/api/stack/items", headers=AUTH, json={"kind": "note", "note": "second"})
    assert uncertain.status_code == 503 and uncertain.json()["reason"] == "uncertain"
    assert path.read_bytes() != original
    assert [item["note"] for item in client.get("/api/stack", headers=AUTH).json()["items"]] == ["first", "second"]


def test_unlock_error_after_save_does_not_invite_duplicate_retry(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from sentinel import performance

    native_lock = performance._native_lock

    def release_fails(handle, acquire, blocking=False):
        if not acquire:
            raise OSError("synthetic unlock failure")
        return native_lock(handle, acquire, blocking)

    path = tmp_path / "stack.json"
    with monkeypatch.context() as patch:
        patch.setattr(performance, "_native_lock", release_fails)
        Stack(path).add(Item(id="saved", added_at="2026-09-24T00:00:00Z", kind="note", title="saved", note="saved"))
    assert [item["id"] for item in Stack(path).state()["items"]] == ["saved"]


def test_prompt_delete_answer_is_from_its_transaction(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    prompts = client.app.state.sentinel.prompts
    removed_id = prompts.all()[0]["id"]
    original_remove = prompts.remove

    def remove_then_another_client_adds(prompt_id: str):
        remaining = original_remove(prompt_id)
        prompts.add("Later prompt")
        return remaining

    monkeypatch.setattr(prompts, "remove", remove_then_another_client_adds)
    response = client.delete(f"/api/prompts/{removed_id}", headers=AUTH)
    assert response.status_code == 200
    assert "Later prompt" not in {prompt["name"] for prompt in response.json()["prompts"]}
    assert "Later prompt" in {prompt["name"] for prompt in prompts.all()}


def test_remove_answer_is_the_state_its_transaction_wrote(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    stack = client.app.state.sentinel.stack
    for item_id in ("first", "second"):
        stack.add(Item(id=item_id, added_at="2026-09-24T00:00:00Z", kind="note", title=item_id, note=item_id))
    original_remove = stack.remove

    def remove_then_another_client_adds(item_id: str):
        removed_state = original_remove(item_id)
        stack.add(Item(id="later", added_at="2026-09-24T00:00:01Z", kind="note", title="later", note="later"))
        return removed_state

    monkeypatch.setattr(stack, "remove", remove_then_another_client_adds)
    response = client.delete("/api/stack/items/first", headers=AUTH)
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == ["second"]
    assert [item["id"] for item in stack.state()["items"]] == ["second", "later"]


def test_mcp_clear_answer_is_the_state_its_transaction_wrote(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    stack = client.app.state.sentinel.stack
    stack.add(Item(id="first", added_at="2026-09-24T00:00:00Z", kind="note", title="first", note="first"))
    original_clear = stack.clear

    def clear_then_another_client_adds():
        cleared_state = original_clear()
        stack.add(Item(id="later", added_at="2026-09-24T00:00:01Z", kind="note", title="later", note="later"))
        return cleared_state

    monkeypatch.setattr(stack, "clear", clear_then_another_client_adds)
    answer = call(client, "stack_clear")
    assert answer.get("isError") is not True
    assert json.loads(answer["content"][0]["text"])["items"] == []
    assert [item["id"] for item in stack.state()["items"]] == ["later"]


@pytest.mark.skipif(os.name != "nt", reason="Windows text-mode descriptor behavior")
def test_windows_stack_json_has_no_doubled_carriage_returns(tmp_path):
    from sentinel.stack import Item

    path = tmp_path / "stack.json"
    Stack(path).add(Item(id="first", added_at="2026-09-23T00:00:00Z", kind="note", title="first", note="first"))
    assert b"\r\r\n" not in path.read_bytes()


def test_one_signal_can_be_handed_on_with_its_basis_and_evidence(client: TestClient):
    envelope = {
        "reading": "signals", "params": {}, "asked_at": "2026-09-21T00:00:00Z", "outcome": "ok", "count": 2,
        "method": {"kind": "readings", "readings": [{"name": "events", "outcome": "ok"}, {"name": "power", "outcome": "ok", "warnings": ["transition query returned only part of its window"], "warnings_total": 1}, {"name": "whea", "outcome": "denied"}]},
        "warnings": ["power answered with 1 warning; first: transition query returned only part of its window"],
        "sections": [{"name": "signals", "class": "inferred", "basis": "WHEA was not observed.", "data": [
            {"id": "pressure:events", "class": "pressure", "title": "The event log is busy", "summary": "A lead to inspect.", "readings": ["events"], "evidence": {"count": 12}},
            {"id": "gaps:whea", "class": "gaps", "title": "WHEA has a gap", "summary": "A missing input.", "readings": ["whea"], "evidence": {"reason": "denied"}},
        ]}],
    }
    item = add(client, kind="selection", ids=["pressure:events"], envelope=envelope)
    assert item["title"] == "1 signal from signals" and item["ids"] == ["pressure:events"]
    text = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert "- selected: 1 of the reading's signals, by signal id" in text
    assert "power answered with 1 warning" in text
    assert "- reading count: 2" in text and "2 records" not in text
    assert '"basis": "WHEA was not observed."' in text and '"count": 12' in text
    assert "gaps:whea" not in text and "denied" not in text.split("```json")[-1]
    assert client.post("/api/stack/items", headers=AUTH, json={"kind": "selection", "ids": ["pressure:events"], "envelope": envelope}).status_code == 409

    summary = client.patch(f"/api/stack/items/{item['id']}", headers=AUTH, json={"verbosity": "summary"})
    assert summary.status_code == 200
    text = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert "A lead to inspect." in text and '"count": 12' not in text
    assert '"basis": "WHEA was not observed."' in text
    later = {**envelope, "asked_at": "2026-09-21T00:05:00Z"}
    assert add(client, kind="selection", ids=["pressure:events"], envelope=later)["id"] != item["id"]
    whole = add(client, kind="reading", envelope=envelope)
    assert add(client, kind="reading", envelope=later)["id"] != whole["id"]
    assert client.post("/api/stack/items", headers=AUTH, json={"kind": "reading", "envelope": later}).status_code == 409


def test_a_selection_cannot_name_evidence_absent_from_its_reading(client: TestClient):
    envelope = client.get("/api/readings/events?count=2", headers=AUTH).json()
    for ids in ([307001, 307001], [999999], ["not-a-record"]):
        response = client.post("/api/stack/items", headers=AUTH, json={"kind": "selection", "ids": ids, "envelope": envelope})
        assert response.status_code == 422, response.text
    signals = {"reading": "signals", "outcome": "ok", "sections": [{"name": "signals", "data": [{"id": "lead:one"}]}]}
    for ids in (["lead:one", "lead:one"], ["lead:other"], [1]):
        response = client.post("/api/stack/items", headers=AUTH, json={"kind": "selection", "ids": ids, "envelope": signals})
        assert response.status_code == 422, response.text


def test_cross_log_record_selection_needs_the_log_when_ids_collide(client: TestClient):
    envelope = {
        "reading": "crash", "params": {}, "asked_at": "2026-09-21T00:00:00Z", "outcome": "ok", "method": {"kind": "powershell"},
        "sections": [{"name": "records", "class": "raw", "data": [
            {"Log": "System", "RecordId": 42, "TimeCreated": "2026-09-20T18:00:00Z", "Message": "System evidence"},
            {"Log": "Application", "RecordId": 42, "TimeCreated": "2026-09-20T18:00:01Z", "Message": "Application evidence"},
        ]}],
    }
    ambiguous = client.post("/api/stack/items", headers=AUTH, json={"kind": "selection", "ids": [42], "envelope": envelope})
    assert ambiguous.status_code == 422 and "ambiguous" in ambiguous.json()["detail"]

    item = add(client, kind="selection", ids=["System:42"], envelope=envelope)
    assert item["ids"] == ["System:42"]
    rendered = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert "System evidence" in rendered and "Application evidence" not in rendered

    # A selection saved before qualified IDs existed must not silently gain the other log's row.
    legacy = "\n".join(_item_lines(1, {"kind": "selection", "title": "Older selection", "ids": [42], "reading": envelope}))
    assert "ambiguous" in legacy and "System evidence" not in legacy and "Application evidence" not in legacy

    unambiguous = {**envelope, "sections": [{"name": "records", "class": "raw", "data": [envelope["sections"][0]["data"][0]]}]}
    duplicate = client.post("/api/stack/items", headers=AUTH, json={"kind": "selection", "ids": [42, "System:42"], "envelope": unambiguous})
    assert duplicate.status_code == 422 and "distinct" in duplicate.json()["detail"]


def test_notes_are_never_duplicates(client: TestClient):
    add(client, kind="note", note="it froze twice this evening")
    add(client, kind="note", note="it froze twice this evening")
    assert len(client.get("/api/stack", headers=AUTH).json()["items"]) == 2


def test_what_cannot_be_evidence_is_refused(client: TestClient):
    def refused(**body) -> str:
        response = client.post("/api/stack/items", headers=AUTH, json=body)
        assert response.status_code == 422, response.text
        return response.json()["detail"]

    assert "either" in refused(kind="reading")
    assert "either" in refused(kind="reading", take={"name": "events"}, envelope={"reading": "events", "outcome": "ok"})
    assert "RecordIds" in refused(kind="selection", take={"name": "events"})
    assert "text" in refused(kind="note", note="   ")
    assert "rank" in refused(kind="note", note="x", rank=9)
    assert "no reading named" in refused(kind="reading", take={"name": "nope"})
    assert "envelope" in refused(kind="reading", envelope={"not": "a reading"})


def test_an_envelope_the_caller_holds_is_stored_as_given(client: TestClient):
    held = client.get("/api/readings/events?count=1", headers=AUTH).json()
    item = add(client, kind="reading", envelope=held, title="What I already had")
    assert item["title"] == "What I already had"
    assert item["provenance"]["asked_at"] == held["asked_at"]
    assert full_item(client, item["id"])["reading"]["asked_at"] == held["asked_at"]
    assert item["provenance"]["origin"] == "supplied"
    assert item["provenance"]["sentinel_version"] == __version__


def test_stack_names_who_supplied_an_outcome_without_claiming_it_as_an_observation(client: TestClient):
    held = client.get("/api/readings/events?count=1", headers=AUTH).json()
    held["sentinel_version"] = "a-client-claim"
    held["origin"] = "taken"
    added = add(client, kind="reading", envelope=held, origin="taken")
    assert added["provenance"]["origin"] == "supplied"
    assert added["provenance"]["sentinel_version"] == "a-client-claim"
    saved = full_item(client, added["id"])
    assert saved["origin"] == "supplied" and saved["reading"] == held
    text = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert "- origin: held reading, stored as received" in text
    assert "- outcome as held: ok — the envelope says the machine was observed" in text
    assert "- outcome: ok — the machine was observed" not in text

    denied = {**held, "asked_at": "2026-09-24T18:00:00Z", "outcome": "denied", "error": {"kind": "denied", "detail": "Access refused"}}
    add(client, kind="reading", envelope=denied)
    text = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert "- outcome as held: denied — the envelope says not observed: Windows refused" in text
    assert "The envelope says the machine was not observed: Access refused." in text


def test_legacy_stack_origin_stays_unknown_after_edit(client: TestClient):
    item = add(client, kind="reading", take={"name": "events", "params": {"count": 1}})
    path = client.app.state.sentinel.stack.store.path
    state = json.loads(path.read_text())
    state["items"][0].pop("origin")
    state["items"][0]["reading"].pop("sentinel_version")
    path.write_text(json.dumps(state))
    listed = client.get("/api/stack", headers=AUTH).json()["items"][0]
    assert listed["provenance"]["origin"] is None and listed["provenance"]["sentinel_version"] is None
    assert "- outcome as saved: ok — the envelope says the machine was observed" in client.get("/api/stack/composed", headers=AUTH).json()["text"]
    edited = client.patch(f"/api/stack/items/{item['id']}", headers=AUTH, json={"rank": 1}).json()
    assert edited["provenance"]["origin"] is None
    assert json.loads(path.read_text())["items"][0]["origin"] is None


@pytest.mark.parametrize("bad_origin", [None, 7, "forged", ["taken"]])
def test_unknown_saved_origin_is_not_promoted_to_taken(client: TestClient, bad_origin):
    item = add(client, kind="reading", take={"name": "events", "params": {"count": 1}})
    path = client.app.state.sentinel.stack.store.path
    state = json.loads(path.read_text())
    state["items"][0]["origin"] = bad_origin
    path.write_text(json.dumps(state))
    assert client.get("/api/stack", headers=AUTH).json()["items"][0]["provenance"]["origin"] is None
    client.patch(f"/api/stack/items/{item['id']}", headers=AUTH, json={"rank": 1})
    assert json.loads(path.read_text())["items"][0]["origin"] is None


def test_rank_verbosity_and_title_change_and_items_go_away(client: TestClient):
    item = add(client, kind="reading", take={"name": "events", "params": {"count": 2}})
    changed = client.patch(f"/api/stack/items/{item['id']}", headers=AUTH, json={"rank": 1, "verbosity": "summary", "title": "The freeze"}).json()
    assert (changed["rank"], changed["verbosity"], changed["title"]) == (1, "summary", "The freeze")
    assert client.patch(f"/api/stack/items/{item['id']}", headers=AUTH, json={"rank": 0}).status_code == 422
    assert client.patch("/api/stack/items/nope", headers=AUTH, json={"rank": 1}).status_code == 404
    assert client.delete(f"/api/stack/items/{item['id']}", headers=AUTH).json()["items"] == []
    assert client.delete("/api/stack/items/nope", headers=AUTH).status_code == 404
    add(client, kind="note", note="something")
    assert client.delete("/api/stack", headers=AUTH).json()["items"] == []


def test_which_prompt_leads_the_handoff(client: TestClient):
    state = client.patch("/api/stack", headers=AUTH, json={"prompt_id": "rma-prosecutor"}).json()
    assert state["prompt_id"] == "rma-prosecutor" and state["system_prompt"] is True
    state = client.patch("/api/stack", headers=AUTH, json={"system_prompt": False}).json()
    assert state["prompt_id"] == "rma-prosecutor" and state["system_prompt"] is False
    off = client.get("/api/stack/composed", headers=AUTH).json()
    assert "RMA EVIDENCE REPORT" not in off["text"]
    assert off["prompt"] == {"id": "rma-prosecutor", "state": "off", "reason": None}
    client.patch("/api/stack", headers=AUTH, json={"prompt_id": None, "system_prompt": True})
    none = client.get("/api/stack/composed", headers=AUTH).json()
    assert none["prompt"] == {"id": None, "state": "none", "reason": None}


def test_the_prompt_library_ships_with_six_and_takes_more(client: TestClient):
    prompts = client.get("/api/prompts", headers=AUTH).json()["prompts"]
    assert [p["name"] for p in prompts] == [p["name"] for p in PRESET_PROMPTS]
    assert all(p["builtin"] for p in prompts)
    assert prompts[0]["content"] == PRESET_PROMPTS[0]["content"] and prompts[0]["id"] == "quantum-diagnostician"
    mine = client.post("/api/prompts", headers=AUTH, json={"name": "My prompt", "content": "Look at the storage first."}).json()
    assert mine["id"] == "my-prompt" and mine["builtin"] is False
    assert client.patch(f"/api/prompts/{mine['id']}", headers=AUTH, json={"content": "Look at the memory first."}).json()["content"] == "Look at the memory first."
    assert client.patch("/api/prompts/quantum-diagnostician", headers=AUTH, json={"name": "Quantum"}).json()["name"] == "Quantum"
    assert client.delete(f"/api/prompts/{mine['id']}", headers=AUTH).status_code == 200
    assert client.delete(f"/api/prompts/{mine['id']}", headers=AUTH).status_code == 404
    assert len(client.get("/api/prompts", headers=AUTH).json()["prompts"]) == len(PRESET_PROMPTS)


def test_large_log_defaults_to_a_bounded_summary_with_full_evidence_on_demand(client: TestClient):
    rows = [
        {"RecordId": number, "Id": number, "LevelDisplayName": "Information", "ProviderName": "Synthetic-Provider",
         "TimeCreated": "2026-09-20T18:04:11Z", "Message": f"synthetic row {number}"}
        for number in range(1, 201)
    ]
    envelope = {
        "reading": "record", "params": {"count": 200, "before": "2026-09-20T19:00:00Z"},
        "asked_at": "2026-09-20T19:00:00Z", "outcome": "ok", "method": {"kind": "fixture"}, "count": 200,
        "error": None, "warnings": [], "redacted": [],
        "sections": [
            {"name": "records", "class": "raw", "data": rows},
            {"name": "collection", "class": "raw", "data": {"limit": 200, "returned": 200, "truncated": True}},
        ],
    }
    item = add(client, kind="reading", envelope=envelope)
    assert item["verbosity"] == "summary"
    summary = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert len(summary) < 5000
    assert "synthetic row 1" in summary and "synthetic row 200" in summary
    assert "synthetic row 100" not in summary and "- record cutoff: limit=200, returned=200, truncated=true" in summary
    assert "Leading sources: Synthetic-Provider (200)." in summary
    stopped = {**envelope, "sections": [envelope["sections"][0], {
        "name": "collection", "class": "raw", "data": {"limit": 200, "returned": 200, "truncated": None, "stopped": {"kind": "failed", "detail": "interrupted"}},
    }]}
    stopped_summary = "\n".join(_item_lines(1, {"kind": "reading", "title": "Stopped frame", "reading": stopped, "verbosity": "summary"}))
    assert "truncated=unknown (query stopped early)" in stopped_summary
    assert client.patch(f"/api/stack/items/{item['id']}", headers=AUTH, json={"verbosity": "full"}).status_code == 200
    full = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert "synthetic row 100" in full


def test_log_handoffs_keep_retention_and_citable_rows(client: TestClient):
    envelope = {
        "reading": "events", "params": {"log": "System", "count": 2, "since": "boot"},
        "asked_at": "2026-09-20T19:00:00Z", "outcome": "ok", "method": {"kind": "fixture"}, "count": 2,
        "error": None, "warnings": [], "redacted": [],
        "sections": [
            {"name": "records", "class": "raw", "data": EVENTS},
            {"name": "collection", "class": "raw", "data": {"log": "System", "limit": 2, "returned": 2, "truncated": False, "log_enabled": True, "log_mode": "Circular"}},
            {"name": "coverage", "class": "derived", "basis": "Synthetic window reach", "data": {"log": "System", "complete": True, "covered_from": "2026-09-20T18:00:00Z", "retained_from": "2026-09-01T00:00:00Z"}},
        ],
    }
    item = add(client, kind="reading", envelope=envelope, verbosity="summary")
    summary = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert "| Time | Level | Provider | Id | Record | Message |" in summary
    assert "System:307001" in summary and '"complete": true' in summary
    assert '"retained_from": "2026-09-01T00:00:00Z"' in summary and "Synthetic window reach" in summary
    oldest = {**envelope, "sections": [envelope["sections"][0],
              {**envelope["sections"][1], "data": {**envelope["sections"][1]["data"], "order": "oldest"}},
              envelope["sections"][2]]}
    assert "Returned rows are oldest first." in handoff(oldest)
    assert "Returned rows are newest first." in handoff(envelope)
    assert "No bounded summary" not in summary
    compact_selection = handoff(envelope, ids=["System:307001"])
    assert "by log and RecordId" in compact_selection
    assert compact_selection.index('"name": "coverage"') < compact_selection.index("| Time | Level")

    selected = add(client, kind="selection", envelope=envelope, ids=["System:307001"])
    selected_text = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert selected["verbosity"] == "full" and '"projection": "selected raw records"' in selected_text
    assert '"RecordId": 307001' in selected_text and '"complete": true' in selected_text
    duplicate = client.post("/api/stack/items", headers=AUTH, json={"kind": "selection", "envelope": envelope, "ids": [307001]})
    assert duplicate.status_code == 409 and duplicate.json()["id"] == selected["id"]
    assert client.patch(f"/api/stack/items/{item['id']}", headers=AUTH, json={"verbosity": "full"}).status_code == 200


def test_record_handoff_keeps_before_reach_and_legacy_gaps(client: TestClient):
    envelope = {
        "reading": "record", "params": {"log": "Application", "count": 2, "before": "2026-09-20T19:00:00Z"},
        "asked_at": "2026-09-20T19:00:00Z", "outcome": "ok", "method": {"kind": "fixture"}, "count": 2,
        "sections": [
            {"name": "records", "class": "raw", "data": [{**row, "RecordId": index} for index, row in enumerate(EVENTS, 1)]},
            {"name": "collection", "class": "raw", "data": {"log": "Application", "limit": 2, "returned": 2, "truncated": None, "stopped": {"detail": "interrupted"}}},
            {"name": "coverage", "class": "derived", "basis": "Synthetic before reach", "data": {"reaches_before": True, "retained_from": "2026-09-01T00:00:00Z"}},
        ],
    }
    summary = handoff(envelope)
    assert "truncated=unknown (query stopped early)" in summary and "Application:1" in summary
    assert '"reaches_before": true' in summary and "Synthetic before reach" in summary
    legacy = {**envelope, "sections": envelope["sections"][:1]}
    text = handoff(legacy)
    assert "no collection section" in text and "no coverage section" in text
    assert "| 1 |" in text and "Application:1" not in text
    assert client.post("/api/stack/items", headers=AUTH, json={"kind": "selection", "envelope": legacy, "ids": ["Application:1"]}).status_code == 422
    assert add(client, kind="selection", envelope=legacy, ids=[1])["ids"] == [1]


def test_real_log_collector_sections_survive_compact_handoffs():
    events = asyncio.run(take("events", FakeBridge(log_collector_result(EVENTS, limit=2)), {"count": 2, "since": "2026-09-20T00:00:00Z"})).to_dict()
    event_handoff = handoff(events)
    assert LOG_WINDOW_COVERAGE_BASIS in event_handoff
    assert '"complete": true' in event_handoff and '"queried_at"' in event_handoff
    assert "Returned rows are newest first." in event_handoff

    record = asyncio.run(take("record", FakeBridge(log_collector_result(EVENTS, limit=2, window_start=None)), {"count": 2, "before": "2026-09-20T19:00:00Z"})).to_dict()
    record_handoff = handoff(record)
    assert RECORD_COVERAGE_BASIS in record_handoff
    assert '"reaches_before": true' in record_handoff and '"window_end"' in record_handoff
    assert "strictly before the requested moment" in record_handoff


def test_unprojected_summary_says_it_carries_full_sections():
    envelope = {"reading": "health", "params": {}, "asked_at": "2026-09-20T19:00:00Z", "outcome": "ok", "method": {"kind": "fixture"},
                "sections": [{"name": "status", "class": "derived", "data": {"alive": True}}]}
    assert "summary verbosity this observation carries its full stored sections" in handoff(envelope).lower()
    assert '"alive": true' in handoff(envelope)
    assert "summary verbosity" not in handoff(envelope, verbosity="full").lower()
    empty_whea = {**envelope, "reading": "whea", "outcome": "empty", "sections": [{"name": "records", "class": "raw", "data": []}]}
    assert "summary verbosity this observation carries its full stored sections" in handoff(empty_whea).lower()


def test_crash_summary_carries_stops_and_coverage_without_raw_event_rows(client: TestClient):
    envelope = crash_envelope(incomplete_reports=True)
    stops = next(section["data"] for section in envelope["sections"] if section["name"] == "stops")
    assert set(stops[0]["records"]) == set(STOP_REF_LOGS) | {"report"}
    assert stops[3]["no_bugcheck_recorded"] is None  # The report log's retained boundary is too recent.
    summary = handoff(envelope)
    full = handoff(envelope, verbosity="full")
    assert add(client, kind="reading", envelope=envelope)["verbosity"] == "summary"
    routed = client.get("/api/stack/composed", headers=AUTH)
    assert routed.status_code == 200 and '"no_bugcheck_recorded": null' in routed.json()["text"]
    assert len(summary) < len(full) * 0.4
    assert STOPS_BASIS in summary
    assert '"stopped_at": "2026-09-12T06:11:02.000Z"' in summary
    assert '"reported_at": "2026-07-04T09:00:00.000Z"' in summary
    assert '"no_bugcheck_recorded": null' in summary
    assert '"bound_reached": false' in summary and '"retained_from"' in summary
    assert "Display driver nvlddmkm" in summary
    assert '"Properties"' not in summary and '"Properties"' in full
    assert "| Time | Level | Provider | Id | Record | Message |" not in summary
    issues = next(section for section in projected_sections(summary) if section["name"] == "decode_issues")
    assert issues["basis"] and issues["data"]["available"] is True


def test_saved_crash_with_more_than_twenty_stops_names_the_omission():
    envelope = crash_envelope()
    stops = next(section["data"] for section in envelope["sections"] if section["name"] == "stops")
    stops.extend(copy.deepcopy(stops[0]) for _ in range(20))
    summary = handoff(envelope)
    projected = next(section for section in projected_sections(summary) if section["name"] == "stops")["data"]
    assert projected["returned"] == 25 and len(projected["shown"]) == 20 and projected["omitted"] == 5
    assert len(summary) < 40_000


def test_selected_crash_stop_carries_its_interpretation_and_exact_rows(client: TestClient):
    envelope = crash_envelope(incomplete_reports=True)
    stops = next(section["data"] for section in envelope["sections"] if section["name"] == "stops")
    assert stops[3]["no_bugcheck_recorded"] is None
    selected = handoff(envelope, ids=["System:900"], verbosity="full")
    selected_summary = handoff(envelope, ids=["System:900"], verbosity="summary")
    assert add(client, kind="selection", envelope=envelope, ids=["System:900"])["verbosity"] == "full"
    assert "Composed stops referencing the selected records: 1" in selected
    assert '"no_bugcheck_recorded": null' in selected
    assert '"no_bugcheck_recorded": null' in selected_summary
    assert '"RecordId": 900' in selected and '"RecordId": 1301' not in selected
    assert '"collection"' in selected and '"coverage"' in selected
    assert '"Properties"' in selected
    clean = handoff(envelope, ids=["System:1100"], verbosity="full")
    assert "No composed stop in the stored reading references these records." in clean
    report_only = handoff(envelope, ids=["Application:2100"], verbosity="full")
    assert '"stopped_at": null' in report_only and '"reported_at": "2026-07-04T09:00:00.000Z"' in report_only
    assert "A report time alone does not establish stop time." in report_only
    decoded = next(section["data"] for section in envelope["sections"] if section["name"] == "decoded")
    next(entry for entry in decoded if entry["RecordId"] == 900)["error"] = "synthetic property mismatch"
    selected_with_issue = handoff(envelope, ids=["System:900"], verbosity="summary")
    assert '"error": "synthetic property mismatch"' in selected_with_issue
    assert next(section for section in projected_sections(selected_with_issue) if section["name"] == "decode_issues")["data"]["available"] is True


def test_fault_summary_carries_grouped_meaning_and_retention(client: TestClient):
    envelope = faults_envelope()
    summary = handoff(envelope)
    full = handoff(envelope, verbosity="full")
    assert add(client, kind="reading", envelope=envelope)["verbosity"] == "summary"
    routed = client.get("/api/stack/composed", headers=AUTH)
    assert routed.status_code == 200 and '"by_kind"' in routed.json()["text"]
    summary_evidence = summary[summary.index("\n```json\n"):]
    full_evidence = full[full.index("\n```json\n"):]
    assert len(summary_evidence) < len(full_evidence) / 2
    assert '"by_kind"' in summary and '"applications"' in summary
    assert '"name": "example.exe"' in summary and "ucrtbase.dll" in summary
    assert '"name": "access violation"' in summary and '"code": "0x141"' in summary
    assert '"complete": true' in summary and '"retained_from": "2026-01-01T00:00:00.0000000Z"' in summary
    assert "Leading sources" not in summary and '"Properties"' not in summary


def test_selected_fault_report_keeps_grouping_and_raw_record():
    envelope = faults_envelope()
    selected = handoff(envelope, ids=["Application:3999"], verbosity="full")
    selected_summary = handoff(envelope, ids=["Application:3999"], verbosity="summary")
    assert "Derived fault entries referencing the selected records: 1" in selected
    assert '"RecordId": 4000' in selected and '"RecordId": 3999' in selected
    assert '"code": "0x141"' in selected and '"bucket": "LKD_0x141_Tdr:6_IMAGE_nvlddmkm.sys_Ampere"' in selected
    assert '"coverage"' in selected and '"Properties"' in selected
    assert '"code": "0x141"' in selected_summary and "Selected raw records" in selected_summary


def test_older_selected_rows_without_log_keep_inferable_interpretations():
    crash = crash_envelope()
    for section in crash["sections"]:
        if section["name"] in ("records", "decoded"):
            for row in section["data"]:
                row.pop("Log", None)
    selected_stop = handoff(crash, ids=[900], verbosity="full")
    assert "Composed stops referencing the selected records: 1" in selected_stop
    projected = projected_sections(selected_stop)
    assert len(next(section for section in projected if section["name"] == "decoded")["data"]) == 1
    report_only = handoff(crash, ids=[2100], verbosity="full")
    assert '"reported_at": "2026-07-04T09:00:00.000Z"' in report_only
    raw = next(section["data"] for section in crash["sections"] if section["name"] == "records")
    next(row for row in raw if row["RecordId"] == 900).pop("ProviderName", None)
    unknown_source = handoff(crash, ids=[900], verbosity="full")
    assert "stop association is unknown" in unknown_source and "No composed stop" not in unknown_source

    faults = faults_envelope()
    for section in faults["sections"]:
        if section["name"] in ("records", "decoded"):
            for row in section["data"]:
                row.pop("Log", None)
    selected_report = handoff(faults, ids=[3999], verbosity="full")
    assert "Derived fault entries referencing the selected records: 1" in selected_report
    assert '"RecordId": 4000' in selected_report


def test_large_fault_projection_names_the_groups_and_entries_it_omits():
    envelope = faults_envelope()
    summary = next(section["data"] for section in envelope["sections"] if section["name"] == "summary")
    summary["applications"] = [
        {"name": f"app-{index}.exe", "count": 1, "first": None, "last": None, "modules": [f"module-{part}" for part in range(8)]}
        for index in range(150)
    ]
    decoded = next(section["data"] for section in envelope["sections"] if section["name"] == "decoded")
    decoded.extend({"Log": "Application", "RecordId": 10000 + index, "kind": "application crash", "fields": {"AppName": f"app-{index}.exe"}} for index in range(150))
    text = handoff(envelope)
    assert '"other_applications": 140' in text and '"other_application_entries": 140' in text
    assert '"other_modules": 3' in text and '"entries": 156' in text and '"omitted_entries": 146' in text
    assert '"application": "app-149.exe"' in text and '"application": "app-70.exe"' not in text
    assert len(text) < 13_000


def test_malformed_saved_derived_sections_do_not_claim_zero_findings():
    crash = crash_envelope()
    next(section for section in crash["sections"] if section["name"] == "stops")["data"] = {"broken": True}
    crash_text = handoff(crash)
    assert "stop count is unknown" in crash_text and '"available": false' in crash_text
    crash = crash_envelope()
    next(section for section in crash["sections"] if section["name"] == "decoded")["data"].pop()
    issues = next(section for section in projected_sections(handoff(crash)) if section["name"] == "decode_issues")
    assert issues["data"]["available"] is False and issues["data"]["returned"] is None
    faults = faults_envelope()
    next(section for section in faults["sections"] if section["name"] == "decoded")["data"] = {"broken": True}
    faults_text = handoff(faults)
    assert "entry count is unknown" in faults_text and '"available": false' in faults_text
    next(section for section in faults["sections"] if section["name"] == "summary")["data"] = {"by_kind": {"application crash": 3}, "applications": "broken"}
    malformed_grouping = handoff(faults)
    assert '"applications": null' in malformed_grouping and '"other_applications": null' in malformed_grouping
    assert '"available": false' in malformed_grouping
    faults["sections"] = [section for section in faults["sections"] if section["name"] != "summary"]
    assert "grouped counts are unknown" in handoff(faults)


def test_the_composed_handoff(client: TestClient):
    summary = add(client, kind="reading", rank=1, verbosity="summary", take={"name": "events", "params": {"count": 2}})
    add(client, kind="selection", rank=2, ids=[307002], take={"name": "events", "params": {"count": 5}})
    add(client, kind="note", rank=5, note="It froze twice this evening; both times while idle.")
    body = client.get("/api/stack/composed", headers=AUTH).json()
    text = body["text"]
    assert body["items"] == 3 and body["redacted"] == ["host", "user"]
    assert body["prompt"] == {"id": "quantum-diagnostician", "state": "included", "reason": None}
    assert body["stack"] == client.get("/api/stack", headers=AUTH).json()
    assert "TESTBOX" not in json.dumps(body["stack"])

    assert text.startswith("# System Sentinel handoff")
    assert text.index("QUANTUM DIAGNOSTICIAN") < text.index(f"## 1. {summary['title']}") < text.index("## 2.") < text.index("## 3.")
    assert "- reading: `events` (log=System, levels=1,2, count=2, order=newest)" in text
    assert "- outcome: ok — the machine was observed" in text and "- reading count: 2" in text
    assert "- origin: taken by Sentinel for this Stack" in text
    assert "- record cutoff: limit=2, returned=2, truncated=" in text
    assert "- method: powershell" in text and "- class: raw" in text and "- kind: selection" in text

    assert "| Time | Level | Provider | Id | Record | Message |" in text
    assert "| 2026-09-20T18:04:11.204Z | Critical | Microsoft-Windows-Kernel-Power | 41 |" in text
    assert "shutting down first. \\| on <host> for <us…" in text  # the message's own pipe cannot break the table; 80 characters of it are carried
    assert "TESTBOX" not in text and "tester" not in text

    selected = text[text.index("## 2.") : text.index("## 3.")]
    assert "- selected: 1 of the reading's records, by RecordId" in selected
    assert '"RecordId": 307002' in selected and '"RecordId": 307001' not in selected
    assert text.rstrip().endswith("It froze twice this evening; both times while idle.")


def test_the_composed_handoff_unredacted_by_name(client: TestClient):
    add(client, kind="reading", take={"name": "events", "params": {"count": 2}})
    body = client.get("/api/stack/composed?unredacted=true", headers=AUTH).json()
    assert "TESTBOX" in body["text"] and body["redacted"] == []


def test_a_reading_that_failed_cannot_pass_as_a_finding(client: TestClient):
    client.bridge.result = BridgeResult("denied", error="Access is denied.", took_ms=2)
    add(client, kind="reading", take={"name": "events"})
    text = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert "- outcome: denied — not observed: Windows refused" in text
    assert "The machine was not observed: Access is denied." in text
    assert "```json" not in text


def test_an_empty_stack_composes_to_something_honest(client: TestClient):
    body = client.get("/api/stack/composed", headers=AUTH).json()
    assert body["items"] == 0 and "No evidence on the stack." in body["text"]


def test_the_stack_outlives_the_process(bridge: FakeBridge):
    with app_for(bridge) as first:
        item = add(first, kind="note", note="kept across restarts")
        first.patch("/api/stack", headers=AUTH, json={"prompt_id": "emergency-triage"})
    with app_for(bridge) as second:
        state = second.get("/api/stack", headers=AUTH).json()
        assert [i["id"] for i in state["items"]] == [item["id"]]
        assert state["prompt_id"] == "emergency-triage"
        assert "EMERGENCY TRIAGE" in second.get("/api/stack/composed", headers=AUTH).json()["text"]


def test_the_stack_is_behind_the_token(client: TestClient):
    for method, path in (("get", "/api/stack"), ("get", "/api/stack/composed"), ("get", "/api/prompts")):
        assert getattr(client, method)(path).status_code == 401
    assert client.post("/api/stack/items", json={"kind": "note", "note": "x"}).status_code == 401


def call(client: TestClient, name: str, arguments: dict | None = None) -> dict:
    client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}, headers=MCP_HEADERS)
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}}, headers=MCP_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_mcp_stack_add_cannot_claim_a_held_envelope_was_taken(client: TestClient):
    held = client.get("/api/readings/events?count=1", headers=AUTH).json()
    held["origin"] = "taken"
    added = json.loads(call(client, "stack_add", {"kind": "reading", "origin": "taken", "envelope": held})["content"][0]["text"])
    assert added["provenance"]["origin"] == "supplied"
    exact = json.loads(call(client, "stack_item", {"id": added["id"]})["content"][0]["text"])
    assert exact["origin"] == "supplied" and exact["reading"]["origin"] == "taken"


def test_the_agent_sees_the_same_stack(client: TestClient):
    listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=MCP_HEADERS).json()["result"]["tools"]
    names = {t["name"] for t in listed}
    assert {"stack_list", "stack_item", "stack_add", "stack_remove", "stack_clear", "compose", "prompts_list"} <= names
    assert "unredacted" in next(t for t in listed if t["name"] == "compose")["inputSchema"]["properties"]

    added = json.loads(call(client, "stack_add", {"kind": "reading", "verbosity": "summary", "take": {"name": "events", "params": {"count": 2}}})["content"][0]["text"])
    assert added["provenance"]["outcome"] == "ok" and "reading" not in added and "TESTBOX" not in json.dumps(added)
    assert json.loads(call(client, "stack_list")["content"][0]["text"])["items"][0]["id"] == added["id"]

    exact = json.loads(call(client, "stack_item", {"id": added["id"]})["content"][0]["text"])
    assert exact["reading"]["outcome"] == "ok" and "TESTBOX" not in json.dumps(exact)
    needs_reason = call(client, "stack_item", {"id": added["id"], "unredacted": True})
    assert needs_reason["isError"] is True and "reason" in needs_reason["content"][0]["text"]
    refused = call(client, "stack_add", {"kind": "reading", "envelope": exact["reading"]})
    assert refused["isError"] is True and added["id"] in refused["content"][0]["text"]

    composed = call(client, "compose")["content"][0]["text"]
    assert composed.startswith("# System Sentinel handoff") and "| Time | Level |" in composed
    assert [p["name"] for p in json.loads(call(client, "prompts_list")["content"][0]["text"])["prompts"]] == [p["name"] for p in PRESET_PROMPTS]

    assert call(client, "stack_remove", {"id": "nope"})["isError"] is True
    assert json.loads(call(client, "stack_remove", {"id": added["id"]})["content"][0]["text"])["items"] == []
    add(client, kind="note", note="and one more")
    assert json.loads(call(client, "stack_clear")["content"][0]["text"])["items"] == []
