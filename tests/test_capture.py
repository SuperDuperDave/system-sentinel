"""Captures: what is in the ZIP, what the manifest says about it, and that names cannot wander."""

import asyncio
import io
import json
import os
import time
import zipfile
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import sentinel.capture as capture
from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.capture import MAX_LIST_MANIFEST_BYTES, STALE_PENDING_SECONDS, listing
from sentinel.paths import captures_dir
from sentinel.reading import REGISTRY, Reading
from sentinel.stack import Prompts, Stack
from tests.conftest import FakeBridge, LogBridge, identity_result, real_bridge_or_skip
from tests.test_stack import EVENTS

TOKEN = "test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client():
    bridge = LogBridge(
        result=BridgeResult("ok", items=EVENTS, took_ms=5),
        by_marker={"$env:COMPUTERNAME": identity_result("TESTBOX", "tester")},
    )
    with TestClient(create_app(State(bridge=bridge, token=TOKEN))) as c:
        c.bridge = bridge
        yield c


def members(body: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


AUTOMATIC = {name for name, spec in REGISTRY.items() if not spec.requires_selection}
SELECTED = {name for name, spec in REGISTRY.items() if spec.requires_selection}


def test_a_capture_holds_automatic_readings_the_stack_and_the_handoff(client: TestClient):
    client.post("/api/stack/items", headers=AUTH, json={"kind": "note", "note": "it froze while idle"})
    response = client.post("/api/captures", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]

    files = members(response.content)
    assert {"stack.json", "composed.md", "manifest.json"} <= set(files)
    assert {f"readings/{name}.json" for name in AUTOMATIC} <= set(files)
    assert not {f"readings/{name}.json" for name in SELECTED} & set(files)

    manifest = json.loads(files["manifest.json"])
    assert manifest["tool"] == "system-sentinel" and manifest["unredacted"] is False
    assert manifest["readings"] == len(AUTOMATIC) and manifest["created_at"].endswith("Z")
    assert {entry["reading"] for entry in manifest["omitted"]} == SELECTED
    assert all(entry["reason"] == "requires an exact selection" for entry in manifest["omitted"])
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
    assert summary["readings"] == len(AUTOMATIC)
    assert summary["omitted"] == len(SELECTED)
    assert sum(summary["outcomes"].values()) == len(AUTOMATIC)
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


def test_concurrent_captures_appear_only_when_complete_and_keep_both_names(monkeypatch: pytest.MonkeyPatch):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    monkeypatch.setattr(capture, "datetime", FixedClock)
    monkeypatch.setattr(capture, "REGISTRY", {"health": REGISTRY["health"]})
    entered = asyncio.Event()
    release = asyncio.Event()
    arrivals = 0

    async def reader(name, params):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            entered.set()
        await release.wait()
        return Reading(reading=name, params=params, outcome="empty", method={"kind": "synthetic"})

    async def exercise():
        tasks = [asyncio.create_task(capture.create(FakeBridge(), Stack(), Prompts(), reader=reader)) for _ in range(2)]
        await asyncio.wait_for(entered.wait(), 3)
        assert listing() == []
        assert len(list(captures_dir().glob("*.pending"))) == 2
        release.set()
        first, second = await asyncio.gather(*tasks)
        assert {first.name, second.name} == {"capture-20260923T120000Z.zip", "capture-20260923T120000Z-2.zip"}
        for made in (first, second):
            with zipfile.ZipFile(made.path) as archive:
                assert archive.testzip() is None
                assert json.loads(archive.read("manifest.json"))["readings"] == 1
        assert len(listing()) == 2
        assert not list(captures_dir().glob("*.pending"))

    asyncio.run(exercise())


def test_cancelled_capture_leaves_no_listed_or_pending_file(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(capture, "REGISTRY", {"health": REGISTRY["health"]})
    entered = asyncio.Event()
    pause = asyncio.Event()

    async def reader(name, params):
        entered.set()
        await pause.wait()
        return Reading(reading=name, params=params, outcome="empty", method={"kind": "synthetic"})

    async def exercise():
        task = asyncio.create_task(capture.create(FakeBridge(), Stack(), Prompts(), reader=reader))
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert listing() == []
        assert not list(captures_dir().iterdir())

    asyncio.run(exercise())


def test_publish_failure_leaves_no_partial_capture(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(capture, "REGISTRY", {"health": REGISTRY["health"]})

    async def reader(name, params):
        return Reading(reading=name, params=params, outcome="empty", method={"kind": "synthetic"})

    def refuse(*_args):
        raise PermissionError("synthetic publication refusal")

    monkeypatch.setattr(capture.os, "rename" if os.name == "nt" else "link", refuse)
    with pytest.raises(PermissionError, match="synthetic publication refusal"):
        asyncio.run(capture.create(FakeBridge(), Stack(), Prompts(), reader=reader))
    assert listing() == []
    assert not list(captures_dir().iterdir())


def test_listing_reaps_abandoned_pending_files_without_touching_recent_ones():
    directory = captures_dir()
    old = directory / ".capture-old.pending"
    recent = directory / ".capture-recent.pending"
    old.write_bytes(b"partial private evidence")
    recent.write_bytes(b"active private evidence")
    stale_at = time.time() - STALE_PENDING_SECONDS - 60
    os.utime(old, (stale_at, stale_at))
    assert listing() == []
    assert not old.exists() and recent.exists()


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


def test_listing_counts_omissions_without_echoing_untrusted_manifest_strings():
    base = {"tool": "system-sentinel", "created_at": "2026-09-20T00:00:00Z", "unredacted": False, "readings": 0, "members": []}
    cases = {
        "capture-20260920T000010Z.zip": ({**base}, "read", 0),
        "capture-20260920T000011Z.zip": ({**base, "omitted": [{"reading": "PRIVATE-HOST", "reason": "future reason"}]}, "read", 1),
        "capture-20260920T000012Z.zip": ({**base, "omitted": [{"reading": "whea_record"}]}, "unreadable", None),
        "capture-20260920T000013Z.zip": ({**base, "readings": 1, "members": [{"reading": "whea_record", "outcome": "ok"}], "omitted": [{"reading": "whea_record", "reason": "requires an exact selection"}]}, "unreadable", None),
    }
    for name, (manifest, _, _) in cases.items():
        with zipfile.ZipFile(captures_dir() / name, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
    listed = {capture["name"]: capture["manifest"] for capture in listing()}
    for name, (_, status, omitted) in cases.items():
        assert listed[name]["status"] == status
        if omitted is not None:
            assert listed[name]["omitted"] == omitted
        assert "PRIVATE-HOST" not in json.dumps(listed[name])


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
        assert set(outcomes) == AUTOMATIC
        assert {entry["reading"] for entry in manifest["omitted"]} == SELECTED
        assert state.identity.host, "the tool learned this machine's name, so it can remove it"
        assert all(state.identity.host.encode() not in body for body in files.values())
        print(f"\ncapture took {took} ms, {len(response.content)} bytes; outcomes {outcomes}")
        print("took_ms " + ", ".join(f"{m['reading']}={m['took_ms']}" for m in manifest["members"] if "reading" in m))
