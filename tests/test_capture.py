"""Captures: what is in the ZIP, what the manifest says about it, and that names cannot wander."""

import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.capture import MAX_LIST_MANIFEST_BYTES, listing
from sentinel.paths import captures_dir
from sentinel.reading import REGISTRY
from tests.conftest import FakeBridge, identity_result, real_bridge_or_skip
from tests.test_stack import EVENTS

TOKEN = "test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client():
    bridge = FakeBridge(
        result=BridgeResult("ok", items=EVENTS, took_ms=5),
        by_marker={"$env:COMPUTERNAME": identity_result("TESTBOX", "tester")},
    )
    with TestClient(create_app(State(bridge=bridge, token=TOKEN))) as c:
        c.bridge = bridge
        yield c


def members(body: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_a_capture_holds_every_reading_the_stack_and_the_handoff(client: TestClient):
    client.post("/api/stack/items", headers=AUTH, json={"kind": "note", "note": "it froze while idle"})
    response = client.post("/api/captures", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]

    files = members(response.content)
    assert {"stack.json", "composed.md", "manifest.json"} <= set(files)
    assert {f"readings/{name}.json" for name in REGISTRY} <= set(files)

    manifest = json.loads(files["manifest.json"])
    assert manifest["tool"] == "system-sentinel" and manifest["unredacted"] is False
    assert manifest["readings"] == len(REGISTRY) and manifest["created_at"].endswith("Z")
    listed = {m["path"]: m for m in manifest["members"]}
    assert set(listed) == set(files) - {"manifest.json"}
    for path, member in listed.items():
        assert member["bytes"] == len(files[path]) > 0
    assert listed["readings/events.json"]["outcome"] == "ok"
    assert json.loads(files["readings/events.json"])["sections"][0]["data"][0]["Id"] == 41
    assert "it froze while idle" in files["composed.md"].decode()
    assert json.loads(files["stack.json"])["items"][0]["note"] == "it froze while idle"


def test_a_capture_is_redacted_unless_asked_by_name(client: TestClient):
    client.post("/api/stack/items", headers=AUTH, json={"kind": "reading", "take": {"name": "events", "params": {"count": 2}}})
    redacted_response = client.post("/api/captures", headers=AUTH)
    files = members(redacted_response.content)
    assert all(b"TESTBOX" not in body and b"tester" not in body for body in files.values())
    assert "host" in json.loads(files["manifest.json"])["redacted"]
    assert json.loads(files["readings/events.json"])["redacted"] == ["host", "user"]

    unredacted_response = client.post("/api/captures?unredacted=true", headers=AUTH)
    open_files = members(unredacted_response.content)
    assert b"TESTBOX" in open_files["readings/events.json"]
    assert json.loads(open_files["manifest.json"])["unredacted"] is True
    listed = {capture["name"]: capture["manifest"] for capture in client.get("/api/captures", headers=AUTH).json()["captures"]}
    assert listed[redacted_response.headers["X-Capture-Name"]]["unredacted"] is False
    assert listed[unredacted_response.headers["X-Capture-Name"]]["unredacted"] is True


def test_a_reading_that_cannot_be_taken_is_written_with_its_outcome(client: TestClient):
    client.bridge.result = BridgeResult("unavailable", error="powershell.exe was not found")
    files = members(client.post("/api/captures", headers=AUTH).content)
    events = json.loads(files["readings/events.json"])
    assert events["outcome"] == "unavailable" and events["error"]["kind"] == "unavailable"
    assert events["sections"] == []
    # `record` needs a moment; a capture's moment is its own, so it is taken like the rest.
    assert json.loads(files["readings/record.json"])["params"]["before"].endswith("Z")


def test_captures_are_listed_and_fetched_and_nothing_wanders(client: TestClient):
    first = client.post("/api/captures", headers=AUTH)
    name = first.headers["X-Capture-Name"]
    listed = client.get("/api/captures", headers=AUTH).json()["captures"]
    assert [c["name"] for c in listed] == [name]
    assert listed[0]["bytes"] == len(first.content) and (captures_dir() / name).is_file()
    summary = listed[0]["manifest"]
    assert summary["status"] == "read" and summary["unredacted"] is False
    assert summary["readings"] == len(REGISTRY)
    assert sum(summary["outcomes"].values()) == len(REGISTRY)
    assert summary["captured_at"] == json.loads(members(first.content)["manifest.json"])["created_at"]

    again = client.get(f"/api/captures/{name}", headers=AUTH)
    assert again.status_code == 200 and again.content == first.content
    assert client.get("/api/captures/capture-20260920T000000Z.zip", headers=AUTH).status_code == 404
    assert client.get("/api/captures/..%2F..%2Ftoken", headers=AUTH).status_code == 404
    assert client.get("/api/captures/token", headers=AUTH).status_code == 404
    assert client.get(f"/api/captures/{name}").status_code == 401


def test_two_captures_in_the_same_second_do_not_overwrite_each_other(client: TestClient):
    names = {client.post("/api/captures", headers=AUTH).headers["X-Capture-Name"] for _ in range(2)}
    assert len(names) == 2
    assert len(client.get("/api/captures", headers=AUTH).json()["captures"]) == 2


def test_listing_keeps_missing_damaged_and_oversized_manifests_explicit():
    examples = {
        "capture-20260920T000000Z.zip": ("other.txt", "no manifest", "missing"),
        "capture-20260920T000001Z.zip": ("manifest.json", "{broken", "unreadable"),
        "capture-20260920T000002Z.zip": ("manifest.json", " " * (MAX_LIST_MANIFEST_BYTES + 1), "limit"),
    }
    for name, (member, content, _) in examples.items():
        with zipfile.ZipFile(captures_dir() / name, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(member, content)
    (captures_dir() / "capture-20260920T000003Z.zip").write_bytes(b"not a zip")

    listed = {capture["name"]: capture["manifest"] for capture in listing()}
    assert {name: listed[name]["status"] for name in examples} == {name: example[2] for name, example in examples.items()}
    assert listed["capture-20260920T000003Z.zip"] == {"status": "unreadable"}
    assert all("unredacted" not in summary and "outcomes" not in summary for summary in listed.values())


@pytest.mark.host
def test_a_capture_of_this_machine():
    state = State(bridge=real_bridge_or_skip(), token=TOKEN)
    with TestClient(create_app(state, mcp=False)) as client:
        started = time.perf_counter()
        response = client.post("/api/captures", headers=AUTH)
        took = int((time.perf_counter() - started) * 1000)
        assert response.status_code == 200
        files = members(response.content)
        manifest = json.loads(files["manifest.json"])
        outcomes = {m["reading"]: m["outcome"] for m in manifest["members"] if "reading" in m}
        assert set(outcomes) == set(REGISTRY)
        assert state.identity.host, "the tool learned this machine's name, so it can remove it"
        assert all(state.identity.host.encode() not in body for body in files.values())
        print(f"\ncapture took {took} ms, {len(response.content)} bytes; outcomes {outcomes}")
        print("took_ms " + ", ".join(f"{m['reading']}={m['took_ms']}" for m in manifest["members"] if "reading" in m))
