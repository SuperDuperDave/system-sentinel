"""The stack: what it accepts, what it refuses, what it composes, and that it outlives the process."""

import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient
from mcp import types
from mcp.shared.exceptions import MCPError

from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.stack import PRESET_PROMPTS, Prompts, Stack, _item_lines
from tests.conftest import FakeBridge, LogBridge, identity_result

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
            {"name": "changes", "class": "derived", "basis": "Synthetic interpretation", "data": [{"at": "2026-09-20T00:00:00Z", "kind": "update_installed", "subject": "Synthetic update", "ref": {"log": "System", "record_id": 7}, "fields": {"updateTitle": "Synthetic update"}}]},
            {"name": "summary", "class": "derived", "data": {"returned": 1}},
            {"name": "collection", "class": "raw", "data": {"windows_update": {"outcome": "ok"}}},
            {"name": "coverage", "class": "derived", "data": {"windows_update": {"complete": False}}},
        ],
    }
    summary = "\n".join(_item_lines(1, {"kind": "reading", "title": "Changes", "reading": envelope, "verbosity": "summary"}))
    selected = "\n".join(_item_lines(1, {"kind": "reading", "title": "Changes", "reading": envelope, "verbosity": "full", "ids": ["System:7"]}))
    assert "update_installed" in summary and '"complete": false' in summary
    assert '"fields"' not in summary and '"Id": 19' not in summary
    assert "update_installed" in selected and '"Id": 19' in selected
    assert selected.index('"kind": "update_installed"') < selected.index('"Id": 19')

    malformed = {**envelope, "sections": [*envelope["sections"][:1], {"name": "changes", "class": "derived", "data": [{"ref": None}, 1]}]}
    safe = "\n".join(_item_lines(1, {"kind": "selection", "title": "Malformed", "reading": malformed, "verbosity": "full", "ids": ["System:7"]}))
    assert '"Id": 19' in safe

    failed = {**envelope, "outcome": "failed", "error": {"detail": "one source failed"}}
    unsuccessful = "\n".join(_item_lines(1, {"kind": "reading", "title": "Changes", "reading": failed, "verbosity": "summary"}))
    assert "one source failed" in unsuccessful and '"complete": false' in unsuccessful

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

    add(client, kind="selection", ids=[f"{channel}:42"], envelope=envelope)
    composed = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert '"severity": "fatal"' in composed and '"previous_session": true' in composed
    assert payload not in composed and "<cper bytes withheld" in composed


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
                {"Log": channel, "RecordId": 73, "cper": {"severity": "fatal", "previous_session": True}}
            ]},
            {"name": "decoded", "class": "derived", "data": [{"Log": channel, "RecordId": 73, "error": "detail decoding deferred"}]},
            {"name": "collection", "class": "raw", "data": {"source": "kernel_whea", "outcome": "ok"}},
        ],
    }
    compact = "\n".join(_item_lines(1, {"kind": "reading", "title": "Exact report", "reading": envelope, "verbosity": "summary"}))
    assert '"previous_session": true' in compact and '"severity": "fatal"' in compact
    assert "SYNTHETIC-CPER-BYTES" not in compact and "detail decoding deferred" in compact
    selected = "\n".join(_item_lines(1, {"kind": "selection", "title": "Exact report", "reading": envelope,
                                       "ids": [f"{channel}:73"], "verbosity": "full"}))
    assert '"previous_session": true' in selected and '"severity": "fatal"' in selected
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
    assert '"sample"' not in compact and '"sample"' in full

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


def test_adding_a_reading_takes_it_now_and_keeps_its_provenance(client: TestClient):
    item = add(client, kind="reading", take={"name": "events", "params": {"count": 2}})
    assert item["kind"] == "reading" and item["rank"] == 3 and item["verbosity"] == "full"
    assert item["title"] == "events (log=System, levels=1,2, count=2)"
    assert item["reading"]["outcome"] == "ok" and item["reading"]["method"]["kind"] == "powershell"
    assert item["reading"]["asked_at"] and item["reading"]["params"]["count"] == 2
    assert item["reading"]["sections"][0]["data"][0]["MachineName"] == "<host>"


def test_one_observation_is_idempotent_but_a_new_take_is_new_evidence(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from sentinel import reading as reading_module

    stamps = iter(("2026-09-20T18:00:00.000Z", "2026-09-20T18:00:01.000Z", "2026-09-20T18:00:02.000Z"))
    monkeypatch.setattr(reading_module, "_now", lambda: next(stamps))
    first = add(client, kind="reading", take={"name": "events", "params": {"count": 2}})
    again = client.post("/api/stack/items", headers=AUTH, json={"kind": "reading", "envelope": first["reading"]})
    assert again.status_code == 409 and again.json()["id"] == first["id"]
    assert again.json()["asked_at"] == first["reading"]["asked_at"]
    later = add(client, kind="reading", take={"name": "events", "params": {"count": 2}})
    assert later["id"] != first["id"]
    assert first["reading"]["asked_at"] in client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert later["reading"]["asked_at"] in client.get("/api/stack/composed", headers=AUTH).json()["text"]
    # Different parameters and a selection of the same reading are distinct too.
    assert client.post("/api/stack/items", headers=AUTH, json={"kind": "reading", "take": {"name": "events", "params": {"count": 5}}}).status_code == 201
    assert client.post("/api/stack/items", headers=AUTH, json={"kind": "selection", "ids": [307001], "envelope": first["reading"]}).status_code == 201


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
        ("get", "/api/stack/composed", None),
        ("post", "/api/stack/items", {"kind": "note", "note": "new evidence"}),
        ("patch", "/api/stack", {"system_prompt": False}),
        ("delete", "/api/stack", None),
    ):
        response = client.request(method, route, headers=AUTH, json=body)
        assert response.status_code == 503 and response.json()["error"] == "saved_context_unavailable"
        assert path.read_bytes() == contents


def test_unreadable_prompt_library_is_reported_and_never_reseeded(client: TestClient):
    path = client.app.state.sentinel.prompts.store.path
    path.write_bytes(b"{broken")
    for method, route, body in (
        ("get", "/api/prompts", None),
        ("post", "/api/prompts", {"name": "New prompt"}),
        ("get", "/api/stack/composed", None),
    ):
        response = client.request(method, route, headers=AUTH, json=body)
        assert response.status_code == 503 and path.read_bytes() == b"{broken"


def test_mcp_resources_name_unavailable_saved_context(client: TestClient):
    surface = client.app.state.mcp_surface
    path = client.app.state.sentinel.stack.store.path
    path.write_bytes(b"{broken")
    with pytest.raises(MCPError, match="stack.json is not valid JSON"):
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
    assert response.status_code == 503
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
    assert item["reading"]["asked_at"] == held["asked_at"]


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
    assert "RMA EVIDENCE REPORT" not in client.get("/api/stack/composed", headers=AUTH).json()["text"]


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


def test_the_composed_handoff(client: TestClient):
    summary = add(client, kind="reading", rank=1, verbosity="summary", take={"name": "events", "params": {"count": 2}})
    add(client, kind="selection", rank=2, ids=[307002], take={"name": "events", "params": {"count": 5}})
    add(client, kind="note", rank=5, note="It froze twice this evening; both times while idle.")
    body = client.get("/api/stack/composed", headers=AUTH).json()
    text = body["text"]
    assert body["items"] == 3 and body["redacted"] == ["host", "user"]

    assert text.startswith("# System Sentinel handoff")
    assert text.index("QUANTUM DIAGNOSTICIAN") < text.index(f"## 1. {summary['title']}") < text.index("## 2.") < text.index("## 3.")
    assert "- reading: `events` (log=System, levels=1,2, count=2)" in text
    assert "- outcome: ok — the machine was observed" in text and "- reading count: 2" in text
    assert "- record cutoff: limit=2, returned=2, truncated=" in text
    assert "- method: powershell" in text and "- class: raw" in text and "- kind: selection" in text

    assert "| Time | Level | Provider | Id | Message |" in text
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


def test_the_agent_sees_the_same_stack(client: TestClient):
    listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=MCP_HEADERS).json()["result"]["tools"]
    names = {t["name"] for t in listed}
    assert {"stack_list", "stack_add", "stack_remove", "stack_clear", "compose", "prompts_list"} <= names
    assert "unredacted" in next(t for t in listed if t["name"] == "compose")["inputSchema"]["properties"]

    added = json.loads(call(client, "stack_add", {"kind": "reading", "verbosity": "summary", "take": {"name": "events", "params": {"count": 2}}})["content"][0]["text"])
    assert added["reading"]["outcome"] == "ok" and "TESTBOX" not in json.dumps(added)
    assert json.loads(call(client, "stack_list")["content"][0]["text"])["items"][0]["id"] == added["id"]

    refused = call(client, "stack_add", {"kind": "reading", "envelope": added["reading"]})
    assert refused["isError"] is True and added["id"] in refused["content"][0]["text"]

    composed = call(client, "compose")["content"][0]["text"]
    assert composed.startswith("# System Sentinel handoff") and "| Time | Level |" in composed
    assert [p["name"] for p in json.loads(call(client, "prompts_list")["content"][0]["text"])["prompts"]] == [p["name"] for p in PRESET_PROMPTS]

    assert call(client, "stack_remove", {"id": "nope"})["isError"] is True
    assert json.loads(call(client, "stack_remove", {"id": added["id"]})["content"][0]["text"])["items"] == []
    add(client, kind="note", note="and one more")
    assert json.loads(call(client, "stack_clear")["content"][0]["text"])["items"] == []
