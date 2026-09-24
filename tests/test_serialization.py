"""Exact Windows integers survive client parsing without changing internal evidence."""

from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from sentinel import cli
from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.paths import captures_dir
from sentinel.reading import REGISTRY, Reading, Section
from sentinel.redact import Identity, Redactor
from sentinel.serialization import MAX_SAFE_INTEGER, json_safe_integers
from sentinel.stack import Item, Stack
from sentinel.stream import Stream
from tests.conftest import FakeBridge, LogBridge, identity_result
from tests.test_crash import faults_fixture
from tests.test_mcp import rpc
from tests.test_stream import take_frames

TOKEN = "test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
FIRST_ID = (1 << 53)
SECOND_ID = FIRST_ID + 1
CREATED = 133444736000000001


def browser_json(text):
    # JSON numbers become IEEE-754 doubles in a browser. Decimal strings do not.
    return json.loads(text, parse_int=float)


def section(envelope, name):
    return next(part["data"] for part in envelope["sections"] if part["name"] == name)


def json_blocks(markdown):
    return [browser_json(body) for body in re.findall(r"```json\n(.*?)\n```", markdown, flags=re.DOTALL)]


def selected_section(block, name):
    return next(part["data"] for part in block if part["name"] == name)


def tool(client, name, **arguments):
    result = rpc(client, "tools/call", {"name": name, "arguments": arguments})["result"]
    assert not result.get("isError"), result
    text = browser_json(result["content"][0]["text"])
    structured = browser_json(json.dumps(result["structuredContent"]))
    assert structured == text
    return structured


@pytest.fixture
def machine():
    seed = next(record for record in faults_fixture() if record["RecordId"] == 3000)
    records = []
    for index, record_id in enumerate((FIRST_ID, SECOND_ID)):
        record = deepcopy(seed)
        record["RecordId"] = record_id
        record["Properties"][9] = CREATED + index
        records.append(record)
    bridge = LogBridge(
        result=BridgeResult("unavailable", error="no synthetic answer for this reading"),
        by_marker={
            "$env:COMPUTERNAME": identity_result("WORKBENCH", "someone"),
            "Provider[@Name='Application Error']": BridgeResult("ok", items=records, took_ms=5),
        },
    )
    return State(bridge=bridge, token=TOKEN), records


@pytest.fixture
def client(machine):
    state, _records = machine
    with TestClient(create_app(state)) as client:
        yield client


def seed_persisted_selection(state):
    envelope = asyncio.run(state.readings.take("faults", {"count": 2})).to_dict()
    assert section(envelope, "records")[0]["RecordId"] == FIRST_ID
    assert section(envelope, "decoded")[0]["fields"]["ProcessCreationTime"] == CREATED
    state.stack.add(Item(
        id="earlier-selection", added_at="2026-09-20T00:00:00Z", kind="selection",
        title="An earlier exact fault", reading=envelope, ids=[FIRST_ID],
    ))
    # Read the older representation from disk, as a restarted server would.
    state.stack = Stack()
    return deepcopy(envelope)


def assert_exact_faults(envelope):
    records = section(envelope, "records")
    decoded = section(envelope, "decoded")
    assert [record["RecordId"] for record in records] == [str(FIRST_ID), str(SECOND_ID)]
    assert [record["Properties"][9] for record in records] == [str(CREATED), str(CREATED + 1)]
    assert [entry["RecordId"] for entry in decoded] == [str(FIRST_ID), str(SECOND_ID)]
    assert [entry["fields"]["ProcessCreationTime"] for entry in decoded] == [str(CREATED), str(CREATED + 1)]
    assert records[0]["Id"] == 1000 and decoded[0]["fields"]["ProcessId"] == 4321
    assert decoded[0]["exception"]["code"] == "0xc0000409"


def test_integer_encoding_preserves_signed_limits_and_other_scalar_meanings_without_mutation():
    assert MAX_SAFE_INTEGER == 9007199254740991
    original = {
        "limits": [MAX_SAFE_INTEGER, -MAX_SAFE_INTEGER, FIRST_ID, SECOND_ID, -FIRST_ID, -SECOND_ID],
        "nested": ({"value": -(1 << 63)}, [True, False, None, 1.25, float(1 << 60), str(SECOND_ID)]),
        SECOND_ID: "dictionary keys are unchanged",
    }
    before = deepcopy(original)
    encoded = json_safe_integers(original)
    assert encoded["limits"] == [MAX_SAFE_INTEGER, -MAX_SAFE_INTEGER, str(FIRST_ID), str(SECOND_ID), str(-FIRST_ID), str(-SECOND_ID)]
    assert encoded["nested"][0]["value"] == str(-(1 << 63))
    values = encoded["nested"][1]
    assert values[:3] == [True, False, None] and values[0] is True and values[1] is False
    assert values[3:] == [1.25, float(1 << 60), str(SECOND_ID)]
    assert type(values[3]) is float and type(values[4]) is float
    assert SECOND_ID in encoded and encoded[SECOND_ID] == original[SECOND_ID]
    assert original == before and isinstance(original["nested"], tuple)
    # Encoded containers also cannot be used to mutate the source evidence later.
    encoded["nested"][0]["value"] = "changed output"
    assert original == before


@pytest.mark.parametrize(("outcome", "exit_code"), [("ok", 0), ("unavailable", 1)])
def test_cli_check_prints_exact_integers_and_keeps_observation_exit_code(monkeypatch, capsys, outcome, exit_code):
    bridge = object()
    payload = {"first": FIRST_ID, "second": SECOND_ID, "available": True}
    reading = Reading("health", {}, outcome, {"kind": "synthetic"}, sections=[Section("counters", "raw", payload)])
    original = deepcopy(reading.to_dict())

    def take(located, params):
        assert located is bridge and params == {}
        return reading

    monkeypatch.setattr(cli.Bridge, "locate", staticmethod(lambda: bridge))
    monkeypatch.setitem(REGISTRY, "health", replace(REGISTRY["health"], take=take))
    assert cli.main(["check"]) == exit_code
    printed = capsys.readouterr()
    assert printed.err == ""
    output = browser_json(printed.out)
    assert output["outcome"] == outcome
    assert section(output, "counters") == {"first": str(FIRST_ID), "second": str(SECOND_ID), "available": True}
    assert reading.to_dict() == original and type(payload["first"]) is int


@pytest.mark.parametrize("unredacted", [False, True])
def test_api_and_mcp_keep_adjacent_raw_and_decoded_integers_distinct(client, machine, unredacted):
    state, records = machine
    original = deepcopy(records)
    response = client.get("/api/readings/faults", headers=AUTH, params={"count": 2, "unredacted": str(unredacted).lower()})
    assert response.status_code == 200
    api = browser_json(response.text)
    arguments = {"count": 2, **({"unredacted": True, "reason": "check exact diagnostic integers"} if unredacted else {})}
    mcp = tool(client, "faults", **arguments)
    for envelope in (api, mcp):
        assert_exact_faults(envelope)
        assert section(envelope, "records")[0]["MachineName"] == ("WORKBENCH" if unredacted else "<host>")
        assert ("host" in envelope["redacted"]) is (not unredacted)
    assert api["sections"] == mcp["sections"]
    assert records == original
    internal = asyncio.run(state.readings.take("faults", {"count": 2})).to_dict()
    assert type(section(internal, "records")[0]["RecordId"]) is int
    assert type(section(internal, "decoded")[0]["fields"]["ProcessCreationTime"]) is int


def test_persisted_large_record_selection_survives_serving_and_browser_resubmission(client, machine):
    state, records = machine
    original_records = deepcopy(records)
    original_envelope = seed_persisted_selection(state)
    original_file = state.stack.store.path.read_bytes()
    response = client.get("/api/stack", headers=AUTH)
    assert response.status_code == 200
    held = browser_json(response.text)["items"][0]
    assert held["ids"] == [str(FIRST_ID)]
    composed = client.get("/api/stack/composed", headers=AUTH)
    assert composed.status_code == 200
    assert browser_json(composed.text)["stack"]["items"][0]["ids"] == [str(FIRST_ID)]
    assert "reading" not in held and held["provenance"]["reading"] == "faults"
    assert tool(client, "stack_list")["items"][0] == held
    full = browser_json(client.get(f"/api/stack/items/{held['id']}", headers=AUTH).text)
    assert_exact_faults(full["reading"])
    assert tool(client, "stack_item", id=held["id"]) == full
    assert state.stack.store.path.read_bytes() == original_file

    # The browser returns the held envelope and selects the adjacent record. These
    # two IDs would collapse together if either round trip used a JSON number.
    added = client.post("/api/stack/items", headers=AUTH, json={
        "kind": "selection", "ids": [str(SECOND_ID)], "envelope": full["reading"],
    })
    assert added.status_code == 201, added.text
    assert added.json()["ids"] == [str(SECOND_ID)]
    composed = client.get("/api/stack/composed", headers=AUTH).json()["text"]
    blocks = json_blocks(composed)
    assert [selected_section(block, "records")[0]["RecordId"] for block in blocks] == [str(FIRST_ID), str(SECOND_ID)]
    assert [selected_section(block, "records")[0]["Properties"][9] for block in blocks] == [str(CREATED), str(CREATED + 1)]
    assert [selected_section(block, "decoded")[0]["fields"]["ProcessCreationTime"] for block in blocks] == [str(CREATED), str(CREATED + 1)]
    mcp = rpc(client, "tools/call", {"name": "compose", "arguments": {}})["result"]
    assert not mcp.get("isError")
    assert mcp["content"][0]["text"] == mcp["structuredContent"]["text"] == composed

    stored = state.stack.state()["items"]
    assert stored[0]["reading"] == original_envelope and stored[0]["ids"] == [FIRST_ID]
    assert type(stored[0]["ids"][0]) is int and type(stored[1]["ids"][0]) is int
    assert stored[1]["ids"] == [SECOND_ID]
    assert records == original_records


@pytest.mark.parametrize(("surface", "unredacted"), [("api", False), ("mcp", True)])
def test_new_capture_keeps_fresh_and_stored_evidence_exact_and_preserves_old_zip(client, machine, surface, unredacted):
    state, records = machine
    original_records = deepcopy(records)
    original_envelope = seed_persisted_selection(state)
    stack_bytes = state.stack.store.path.read_bytes()
    legacy = captures_dir() / "capture-20000101T000000Z.zip"
    with zipfile.ZipFile(legacy, "w") as archive:
        archive.writestr("readings/faults.json", json.dumps(original_envelope))
    legacy_bytes = legacy.read_bytes()

    if surface == "api":
        response = client.post("/api/captures", headers=AUTH)
    else:
        made = tool(client, "capture_create", unredacted=True, reason="check exact captured integers")
        response = client.get(f"/api/captures/{made['capture']}", headers=AUTH)
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        members = {name: archive.read(name).decode() for name in archive.namelist()}
    fresh = browser_json(members["readings/faults.json"])
    stored = browser_json(members["stack.json"])["items"][0]
    assert_exact_faults(fresh)
    assert_exact_faults(stored["reading"])
    assert stored["ids"] == [str(FIRST_ID)]
    selected = json_blocks(members["composed.md"])[0]
    assert selected_section(selected, "records")[0]["RecordId"] == str(FIRST_ID)
    assert selected_section(selected, "records")[0]["Properties"][9] == str(CREATED)
    assert selected_section(selected, "decoded")[0]["fields"]["ProcessCreationTime"] == str(CREATED)
    assert browser_json(members["manifest.json"])["unredacted"] is unredacted
    assert section(fresh, "records")[0]["MachineName"] == ("WORKBENCH" if unredacted else "<host>")
    assert state.stack.store.path.read_bytes() == stack_bytes and records == original_records
    assert legacy.read_bytes() == legacy_bytes
    assert client.get(f"/api/captures/{legacy.name}", headers=AUTH).content == legacy_bytes


@pytest.mark.parametrize("unredacted", [False, True])
def test_stream_frames_preserve_large_ids_while_cursors_keep_integer_arithmetic(machine, unredacted):
    _state, records = machine
    original = deepcopy(records)
    cursor_result = BridgeResult("ok", items=[{"log": log, "record": FIRST_ID - 1} for log in ("System", "Application")])
    bridge = FakeBridge(
        result=BridgeResult("ok", items=[{"log": "Application", "record": record} for record in records]),
        by_marker={"Get-WinEvent -LogName $log": cursor_result},
    )
    redactor = None if unredacted else Redactor(Identity(host="WORKBENCH", user="someone"))
    stream = Stream(bridge, redactor, interval=0)
    frames = asyncio.run(take_frames(stream, polls=2))
    returned = [payload["record"] for kind, payload in frames if kind == "record"]
    assert [record["RecordId"] for record in returned] == [str(FIRST_ID), str(SECOND_ID)]
    assert [record["Properties"][9] for record in returned] == [str(CREATED), str(CREATED + 1)]
    assert returned[0]["MachineName"] == ("WORKBENCH" if unredacted else "<host>")
    assert frames[-1][0] == "heartbeat"
    assert frames[-1][1]["cursors"] == {"System": MAX_SAFE_INTEGER, "Application": str(SECOND_ID)}
    assert stream.cursors == {"System": MAX_SAFE_INTEGER, "Application": SECOND_ID}
    assert all(type(value) is int for value in stream.cursors.values())
    assert records == original and cursor_result.items[1]["record"] == MAX_SAFE_INTEGER
