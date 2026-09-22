"""The stack: what it accepts, what it refuses, what it composes, and that it outlives the process."""

import json

import pytest
from fastapi.testclient import TestClient

from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.stack import PRESET_PROMPTS, _item_lines
from tests.conftest import FakeBridge, identity_result

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
    return FakeBridge(
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


def test_the_same_evidence_twice_is_refused(client: TestClient):
    first = add(client, kind="reading", take={"name": "events", "params": {"count": 2}})
    again = client.post("/api/stack/items", headers=AUTH, json={"kind": "reading", "take": {"name": "events", "params": {"count": 2}}})
    assert again.status_code == 409 and again.json()["id"] == first["id"]
    # Different parameters are different evidence; so is a selection of the same reading.
    assert client.post("/api/stack/items", headers=AUTH, json={"kind": "reading", "take": {"name": "events", "params": {"count": 5}}}).status_code == 201
    assert client.post("/api/stack/items", headers=AUTH, json={"kind": "selection", "ids": [307001], "take": {"name": "events", "params": {"count": 2}}}).status_code == 201


def test_one_signal_can_be_handed_on_with_its_basis_and_evidence(client: TestClient):
    envelope = {
        "reading": "signals", "params": {}, "asked_at": "2026-09-21T00:00:00Z", "outcome": "ok", "count": 2,
        "method": {"kind": "readings", "readings": [{"name": "events", "outcome": "ok"}, {"name": "whea", "outcome": "denied"}]},
        "sections": [{"name": "signals", "class": "inferred", "basis": "WHEA was not observed.", "data": [
            {"id": "pressure:events", "class": "pressure", "title": "The event log is busy", "summary": "A lead to inspect.", "readings": ["events"], "evidence": {"count": 12}},
            {"id": "gaps:whea", "class": "gaps", "title": "WHEA has a gap", "summary": "A missing input.", "readings": ["whea"], "evidence": {"reason": "denied"}},
        ]}],
    }
    item = add(client, kind="selection", ids=["pressure:events"], envelope=envelope)
    assert item["title"] == "1 signal from signals" and item["ids"] == ["pressure:events"]
    text = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    assert "- selected: 1 of the reading's signals, by signal id" in text
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

    refused = call(client, "stack_add", {"kind": "reading", "take": {"name": "events", "params": {"count": 2}}})
    assert refused["isError"] is True and "already on the stack" in refused["content"][0]["text"]

    composed = call(client, "compose")["content"][0]["text"]
    assert composed.startswith("# System Sentinel handoff") and "| Time | Level |" in composed
    assert [p["name"] for p in json.loads(call(client, "prompts_list")["content"][0]["text"])["prompts"]] == [p["name"] for p in PRESET_PROMPTS]

    assert call(client, "stack_remove", {"id": "nope"})["isError"] is True
    assert json.loads(call(client, "stack_remove", {"id": added["id"]})["content"][0]["text"])["items"] == []
    add(client, kind="note", note="and one more")
    assert json.loads(call(client, "stack_clear")["content"][0]["text"])["items"] == []
