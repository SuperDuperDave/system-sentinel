"""A redacted dump inventory stays actionable without carrying private selection secrets."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import struct
import zipfile
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.readings.dumps import ALL_DUMPS_SCRIPT
from tests.conftest import FakeBridge, identity_result
from tests.test_dump_header import mdmp_item
from tests.test_dump_inventory import APPLICATION_ROOT, dump_inventory
from tests.test_mcp import rpc

TOKEN = "test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PATH = APPLICATION_ROOT + r"\example.dmp"


def application_item():
    item = mdmp_item()
    item["path"] = PATH
    item["inventory"] = dump_inventory([item], application=True)
    return item


class DumpBridge(FakeBridge):
    """Separate the inventory answer from the later bounded inspection answer."""

    def __init__(self):
        super().__init__()
        self.inspection = application_item()
        self.inventory = deepcopy(self.inspection["inventory"])

    def run(self, script, *, timeout=60, depth=6):
        self.scripts.append(script)
        if script.strip() == ALL_DUMPS_SCRIPT.strip():
            return BridgeResult("ok", items=[self.inventory], took_ms=5)
        if "$selected = " in script:
            return BridgeResult("ok", items=[self.inspection], took_ms=7)
        if "$env:COMPUTERNAME" in script:
            return identity_result("TESTBOX", "example-user")
        # Capture still asks every catalog entry. Other readings explicitly lack a
        # synthetic answer; none can accidentally reach the real Windows bridge.
        return BridgeResult("unavailable", error="no synthetic answer for this reading")


@pytest.fixture
def machine():
    bridge = DumpBridge()
    return State(bridge=bridge, token=TOKEN), bridge


@pytest.fixture
def client(machine):
    state, _bridge = machine
    with TestClient(create_app(state)) as client:
        yield client


def section(envelope, name):
    return next(part for part in envelope["sections"] if part["name"] == name)


def take(state, name, **params):
    return asyncio.run(state.readings.take(name, params))


def reference(state):
    return take(state, "dumps").section("inspection_targets").data[0]["ref"]


def tool(client, name, **arguments):
    result = rpc(client, "tools/call", {"name": name, "arguments": arguments})["result"]
    assert not result.get("isError"), result
    envelope = json.loads(result["content"][0]["text"])
    assert result["structuredContent"] == envelope
    return envelope


def assert_private_values_absent(value, state):
    encoded = json.dumps(value)
    assert "example-user" not in encoded and "TESTBOX" not in encoded
    assert state.readings._key.hex() not in encoded
    assert base64.b64encode(state.readings._key).decode() not in encoded


@pytest.mark.parametrize("listing_surface", ["api", "mcp"])
def test_default_redacted_inventory_reference_can_be_inspected_by_either_client(client, machine, listing_surface):
    state, bridge = machine
    original = deepcopy((bridge.inventory, bridge.inspection))
    listing = client.get("/api/readings/dumps", headers=AUTH).json() if listing_surface == "api" else tool(client, "dumps")
    assert listing["outcome"] == "ok" and listing["count"] == 1
    files = section(listing, "files")
    targets = section(listing, "inspection_targets")
    assert files["class"] == "raw" and targets["class"] == "derived"
    assert "<user>" in files["data"][0]["path"]
    selected = targets["data"][0]
    assert selected == {"file_index": 0, "source": "application", "ref": selected["ref"]}
    assert selected["ref"] and "current file" in targets["basis"] and "restarts" in targets["basis"]
    assert_private_values_absent(listing, state)

    bridge.scripts.clear()
    inspected = tool(client, "dump_header", ref=selected["ref"]) if listing_surface == "api" else client.get(
        "/api/readings/dump_header", headers=AUTH, params={"ref": selected["ref"]},
    ).json()
    assert inspected["outcome"] == "ok" and inspected["count"] == 1
    assert inspected["params"] == {"path": "", "ref": selected["ref"]}
    assert section(inspected, "inspection")["data"]["exception"]["name"] == "access violation"
    assert section(inspected, "selection")["data"] == {"ref": selected["ref"], "source": "application"}
    assert inspected["took_ms"] == 12
    assert len(bridge.scripts) == 2 and bridge.scripts[0].strip() == ALL_DUMPS_SCRIPT.strip()
    assert f"$selected = '{PATH}'" in bridge.scripts[1]
    assert "[IO.File]::Open($file.path" in bridge.scripts[1]
    assert "[IO.File]::Open($selected" not in bridge.scripts[1]
    assert all(selected["ref"] not in script and state.readings._key.hex() not in script for script in bridge.scripts)
    assert len(inspected["method"]["queries"]) == 2
    assert_private_values_absent(inspected, state)
    assert (bridge.inventory, bridge.inspection) == original


@pytest.mark.parametrize("invalid", ["neither", "both", "malformed", "retired", "redacted_path"])
def test_invalid_selection_is_refused_by_api_and_mcp_before_dump_io(client, machine, invalid):
    state, bridge = machine
    if invalid == "retired":
        old_state = State(bridge=DumpBridge(), token=TOKEN)
        params = {"ref": reference(old_state)}
    elif invalid == "both":
        params = {"path": PATH, "ref": reference(state)}
    else:
        params = {
            "neither": {},
            "malformed": {"ref": "not-an-inventory-reference"},
            "redacted_path": {"path": PATH.replace("example-user", "<user>")},
        }[invalid]
    bridge.scripts.clear()
    response = client.get("/api/readings/dump_header", headers=AUTH, params=params)
    assert response.status_code == 422 and response.json()["detail"]
    refused = rpc(client, "tools/call", {"name": "dump_header", "arguments": params})["result"]
    assert refused["isError"] is True
    assert bridge.scripts == []


def test_direct_path_inspection_remains_available(client, machine):
    state, bridge = machine
    bridge.scripts.clear()
    response = client.get("/api/readings/dump_header", headers=AUTH, params={"path": PATH})
    assert response.status_code == 200
    envelope = response.json()
    assert envelope["outcome"] == "ok"
    assert section(envelope, "inspection")["data"]["thread_count"] == 12
    assert len(bridge.scripts) == 1 and f"$selected = '{PATH}'" in bridge.scripts[0]
    assert_private_values_absent(envelope, state)


def test_altered_reference_cannot_select_an_existing_dump(machine):
    state, bridge = machine
    selected = reference(state)
    altered = selected[:-1] + ("0" if selected[-1] != "0" else "1")
    bridge.scripts.clear()
    reading = take(state, "dump_header", ref=altered)
    assert reading.outcome == "empty" and reading.count == 0
    assert reading.section("header") is None and reading.section("inspection") is None
    assert len(bridge.scripts) == 1 and bridge.scripts[0].strip() == ALL_DUMPS_SCRIPT.strip()


@pytest.mark.parametrize(("failure", "expected"), [
    ("none", "empty"), ("selected_denied", "denied"), ("selected_failed", "failed"),
    ("selected_missing_metadata", "failed"), ("unrelated_denied", "empty"),
])
def test_reference_not_found_uses_coverage_of_its_own_source(machine, failure, expected):
    state, bridge = machine
    selected = reference(state)
    bridge.inventory = dump_inventory(application=True)
    if failure == "selected_missing_metadata":
        bridge.inventory["locations"] = [row for row in bridge.inventory["locations"] if row["id"] != "application"]
    elif failure != "none":
        source = "live_kernel" if failure == "unrelated_denied" else "application"
        outcome = "failed" if failure == "selected_failed" else "denied"
        row = next(row for row in bridge.inventory["locations"] if row["id"] == source)
        row.update(present=None, outcome=outcome, error_count=1, errors=[{"kind": outcome, "detail": "synthetic collection failure"}])
    bridge.scripts.clear()
    reading = take(state, "dump_header", ref=selected)
    assert reading.outcome == expected
    assert reading.count == (0 if expected == "empty" else None)
    assert reading.observed is (expected == "empty")
    assert reading.section("inspection") is None and reading.section("header") is None
    assert reading.section("collection") is not None
    assert len(bridge.scripts) == 1 and bridge.scripts[0].strip() == ALL_DUMPS_SCRIPT.strip()
    if expected != "empty":
        assert "unknown" in reading.error["detail"]


def test_later_header_inventory_can_observe_that_a_referenced_file_disappeared(machine):
    state, bridge = machine
    selected = reference(state)
    bridge.inspection = {"status": "not_inventoried", "inventory": dump_inventory(application=True)}
    bridge.scripts.clear()
    reading = take(state, "dump_header", ref=selected)
    assert reading.outcome == "empty" and reading.count == 0
    assert reading.section("header") is None and reading.section("inspection") is None
    assert len(bridge.scripts) == 2


def test_reference_tracks_the_current_path_and_reports_replacement_contents(machine):
    state, bridge = machine
    selected = reference(state)
    replacement = application_item()
    replacement.update(bytes=2048, modified="2026-09-22T00:00:00Z")
    replacement["samples"][2]["data"] = base64.b64encode(struct.pack("<I", 18)).decode()
    replacement["inventory"] = dump_inventory([replacement], application=True)
    bridge.inspection = replacement
    bridge.inventory = deepcopy(replacement["inventory"])
    assert reference(state) == selected
    reading = take(state, "dump_header", ref=selected)
    assert reading.outcome == "ok"
    assert reading.section("file").data["modified"] == replacement["modified"]
    assert reading.section("file").data["bytes"] == 2048
    assert reading.section("inspection").data["thread_count"] == 18
    assert "current file" in reading.section("selection").basis


@pytest.mark.parametrize("surface", ["api", "mcp"])
def test_stack_takes_a_fresh_reference_inspection_and_preserves_its_selection(client, machine, surface):
    state, bridge = machine
    selected = reference(state)
    body = {"take": {"name": "dump_header", "params": {"ref": selected}}}
    bridge.scripts.clear()
    if surface == "api":
        response = client.post("/api/stack/items", headers=AUTH, json=body)
        assert response.status_code == 201
        item = response.json()
    else:
        item = tool(client, "stack_add", **body)
    assert len(bridge.scripts) == 2
    assert item["provenance"]["outcome"] == "ok" and item["provenance"]["params"]["ref"] == selected
    exact = client.get(f"/api/stack/items/{item['id']}", headers=AUTH).json() if surface == "api" else tool(client, "stack_item", id=item["id"])
    assert section(exact["reading"], "selection")["data"]["ref"] == selected
    assert_private_values_absent(item, state)
    assert_private_values_absent(exact, state)
    composed = client.get("/api/stack/composed", headers=AUTH).json()
    assert selected in composed["text"] and "access violation" in composed["text"]
    assert_private_values_absent(composed, state)


@pytest.mark.parametrize("surface", ["api", "mcp"])
def test_capture_preserves_derived_inventory_targets_without_private_values(client, machine, surface):
    state, _bridge = machine
    selected = reference(state)
    tool(client, "stack_add", take={"name": "dumps"})
    if surface == "api":
        response = client.post("/api/captures", headers=AUTH)
    else:
        made = tool(client, "capture_create")
        response = client.get(f"/api/captures/{made['capture']}", headers=AUTH)
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        members = {name: archive.read(name).decode() for name in archive.namelist()}
    listing = json.loads(members["readings/dumps.json"])
    targets = section(listing, "inspection_targets")
    assert targets["class"] == "derived" and targets["data"][0]["ref"] == selected
    stacked = json.loads(members["stack.json"])["items"][0]["reading"]
    assert section(stacked, "inspection_targets")["data"] == targets["data"]
    assert selected in members["composed.md"]
    assert json.loads(members["manifest.json"])["unredacted"] is False
    assert_private_values_absent(members, state)
