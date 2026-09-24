"""Captures: what is in the ZIP, what the manifest says about it, and that names cannot wander."""

import asyncio
import io
import json
import os
import threading
import time
import zipfile
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import sentinel.capture as capture
from sentinel import __version__
from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.capture import MAX_LIST_MANIFEST_BYTES, STALE_PENDING_SECONDS, listing
from sentinel.paths import captures_dir
from sentinel.reading import REGISTRY, Reading, Section, take
from sentinel.readings.diagnostics import SIGNAL_INPUTS
from sentinel.redact import Redactor
from sentinel.stack import Item, Prompts, Stack
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
    assert listed["composed.md"]["prompt"]["state"] == "included"
    assert "exact WHEA fields" in listed["readings/whea.json"]["scope"]
    assert json.loads(files["readings/events.json"])["sections"][0]["data"][0]["Id"] == 41
    assert json.loads(files["readings/events.json"])["sentinel_version"] == __version__
    assert "it froze while idle" in files["composed.md"].decode()
    assert json.loads(files["stack.json"])["items"][0]["note"] == "it froze while idle"


def test_capture_takes_each_signals_source_once_at_its_cited_scope(monkeypatch: pytest.MonkeyPatch):
    called: dict[str, list[dict]] = {name: [] for name, _ in SIGNAL_INPUTS}
    for name, _ in SIGNAL_INPUTS:
        spec = REGISTRY[name]

        def counted(bridge, params, *, original=spec.take, source=name):
            called[source].append(dict(params))
            return original(bridge, params)

        monkeypatch.setitem(REGISTRY, name, replace(spec, take=counted))

    result = asyncio.run(capture.create(LogBridge(result=BridgeResult("ok", items=EVENTS, took_ms=5)), Stack(), Prompts()))
    files = members(result.path.read_bytes())
    manifest = {item["reading"]: item for item in json.loads(files["manifest.json"])["members"] if "reading" in item}
    signals = json.loads(files["readings/signals.json"])
    inputs = {item["name"]: item for item in signals["method"]["readings"]}
    for name, want in SIGNAL_INPUTS:
        assert len(called[name]) == 1
        assert all(called[name][0][key] == value for key, value in want.items())
        source = json.loads(files[f"readings/{name}.json"])
        assert manifest[name]["observed_by"] == "signals" and manifest[name]["params"] == source["params"]
        assert source["asked_at"] == inputs[name]["asked_at"]
    assert manifest["crash"]["params"]["count"] == 20
    assert manifest["events"]["params"]["count"] == 200


def test_capture_crash_references_resolve_inside_its_own_zip(monkeypatch: pytest.MonkeyPatch):
    times = [f"2026-09-{24 - i:02d}T12:00:00.0000000Z" for i in range(6)]
    rows = [{"Log": "System", "RecordId": 100 + i, "TimeCreated": at, "MachineName": "TESTBOX"} for i, at in enumerate(times)]
    stops = [{"started_at": at, "bugcheck": {"code": "0x133", "name": "DPC_WATCHDOG_VIOLATION"},
              "records": {"power_41": 100 + i}} for i, at in enumerate(times)]
    calls: list[int] = []

    def crash_at_scope(_bridge, params):
        count = params["count"]
        calls.append(count)
        return Reading("crash", params, "ok", {"kind": "synthetic"}, sections=[
            Section("records", "raw", rows[:count]), Section("stops", "derived", stops[:count]),
            Section("collection", "raw", {"system": {"outcome": "ok", "bound_reached": False},
                                          "reports": {"outcome": "ok", "bound_reached": False}}),
        ], count=min(count, len(stops)))

    monkeypatch.setitem(REGISTRY, "crash", replace(REGISTRY["crash"], take=crash_at_scope))
    result = asyncio.run(capture.create(FakeBridge(), Stack(), Prompts(), redactor=Redactor()))
    files = members(result.path.read_bytes())
    assert b"TESTBOX" not in files["readings/crash.json"]
    saved_crash = json.loads(files["readings/crash.json"])
    saved_signals = json.loads(files["readings/signals.json"])
    raw = next(section["data"] for section in saved_crash["sections"] if section["name"] == "records")
    leads = next(section["data"] for section in saved_signals["sections"] if section["name"] == "signals")
    repeated = next(lead for lead in leads if lead["id"] == "transition:repeated-stop:0x133")
    assert calls == [20] and len(repeated["evidence"]["refs"]) == 6
    assert all(any(row["Log"] == ref["params"]["log"] and row["RecordId"] == ref["params"]["record_id"]
                   and row["TimeCreated"] == ref["params"]["time_created"] for row in raw)
               for ref in repeated["evidence"]["refs"])


def test_capture_display_reset_references_resolve_in_its_saved_power_member(monkeypatch: pytest.MonkeyPatch):
    at = "2026-09-24T12:00:00.1234567Z"
    transitions = [
        {"RecordId": 71, "Id": 4101, "ProviderName": "Display", "TimeCreated": at},
        {"RecordId": 72, "Id": 1, "ProviderName": "Microsoft-Windows-Power-Troubleshooter", "TimeCreated": at},
    ]
    calls = 0

    def power_at_scope(_bridge, params):
        nonlocal calls
        calls += 1
        return Reading("power", params, "ok", {"kind": "synthetic"}, sections=[
            Section("raw", "raw", {"transitions": transitions}),
            Section("derived", "derived", {"ledger": {"counts": {"display driver reset": 1, "wake": 1},
                                                  "records": 2, "limit": 120, "limit_reached": False}}),
        ])

    monkeypatch.setitem(REGISTRY, "power", replace(REGISTRY["power"], take=power_at_scope))
    result = asyncio.run(capture.create(FakeBridge(), Stack(), Prompts()))
    files = members(result.path.read_bytes())
    power = json.loads(files["readings/power.json"])
    signals = json.loads(files["readings/signals.json"])
    lead = next(lead for lead in next(section["data"] for section in signals["sections"] if section["name"] == "signals")
                if lead["id"] == "transition:display-reset-near-wake")
    refs = lead["evidence"]["refs"]
    assert calls == 1 and len(refs) == 1
    assert next(item for item in signals["method"]["readings"] if item["name"] == "power")["asked_at"] == power["asked_at"]
    saved_rows = next(section["data"]["transitions"] for section in power["sections"] if section["name"] == "raw")
    assert all(any(row["RecordId"] == ref["params"]["record_id"] and row["TimeCreated"] == ref["params"]["time_created"]
                   for row in saved_rows) for ref in refs)


def test_capture_keeps_a_signals_input_exception_without_retry(monkeypatch: pytest.MonkeyPatch):
    calls = 0

    def transient_power(_bridge, params):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic first attempt stopped")
        return Reading("power", params, "empty", {"kind": "synthetic"})

    monkeypatch.setitem(REGISTRY, "power", replace(REGISTRY["power"], take=transient_power))
    result = asyncio.run(capture.create(FakeBridge(), Stack(), Prompts()))
    files = members(result.path.read_bytes())
    saved_power = json.loads(files["readings/power.json"])
    saved_signals = json.loads(files["readings/signals.json"])
    sources = {item["name"]: item for item in saved_signals["method"]["readings"]}
    assert calls == 1 and saved_power["outcome"] == "failed"
    assert "synthetic first attempt stopped" in saved_power["error"]["detail"]
    assert "synthetic first attempt stopped" in sources["power"]["outcome"]


def test_capture_keeps_gathered_sources_when_signals_composition_raises(monkeypatch: pytest.MonkeyPatch):
    calls = 0

    def counted_power(_bridge, params):
        nonlocal calls
        calls += 1
        return Reading("power", params, "empty", {"kind": "synthetic"})

    def broken_compose(*_args):
        raise RuntimeError("PRIVATE_CANARY_42 synthetic composition failure")

    monkeypatch.setitem(REGISTRY, "power", replace(REGISTRY["power"], take=counted_power))
    monkeypatch.setattr(capture, "compose_signals", broken_compose)
    result = asyncio.run(capture.create(FakeBridge(), Stack(), Prompts()))
    files = members(result.path.read_bytes())
    assert b"PRIVATE_CANARY_42" not in b"".join(files.values())
    signals = json.loads(files["readings/signals.json"])
    power = json.loads(files["readings/power.json"])
    assert signals["outcome"] == "failed" and "Signals composition raised RuntimeError" in signals["error"]["detail"]
    assert power["outcome"] == "empty" and calls == 1


def test_capture_does_not_retry_a_source_after_the_gather_itself_raises(monkeypatch: pytest.MonkeyPatch):
    calls = 0

    def counted_power(_bridge, params):
        nonlocal calls
        calls += 1
        return Reading("power", params, "empty", {"kind": "synthetic"})

    async def broken_gather(bridge):
        await take("power", bridge, {})
        raise RuntimeError("PRIVATE_CANARY_42 synthetic gather lost its result")

    monkeypatch.setitem(REGISTRY, "power", replace(REGISTRY["power"], take=counted_power))
    monkeypatch.setattr(capture, "gather_signal_inputs", broken_gather)
    result = asyncio.run(capture.create(FakeBridge(), Stack(), Prompts()))
    files = members(result.path.read_bytes())
    assert b"PRIVATE_CANARY_42" not in b"".join(files.values())
    signals = json.loads(files["readings/signals.json"])
    power = json.loads(files["readings/power.json"])
    events = json.loads(files["readings/events.json"])
    assert calls == 1 and signals["outcome"] == "failed"
    assert power["outcome"] == events["outcome"] == "failed"
    assert "did not retry" in power["error"]["detail"]


def test_cancel_during_signals_gather_discards_the_pending_archive(monkeypatch: pytest.MonkeyPatch):
    entered = asyncio.Event()

    async def held_gather(_bridge):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(capture, "gather_signal_inputs", held_gather)

    async def cancel():
        task = asyncio.create_task(capture.create(FakeBridge(), Stack(), Prompts()))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    assert not list(captures_dir().iterdir())


def test_capture_handoff_uses_the_same_stack_snapshot_as_its_saved_member(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    stack = client.app.state.sentinel.stack
    stack.add(Item(id="earlier", added_at="2026-09-24T00:00:00Z", kind="note", title="Earlier", note="Earlier note"))
    original_state = stack.state
    changed = False

    def edit_after_first_snapshot():
        nonlocal changed
        snapshot = original_state()
        if not changed:
            changed = True
            stack.add(Item(id="later", added_at="2026-09-24T00:00:01Z", kind="note", title="Later", note="Later note"))
        return snapshot

    monkeypatch.setattr(stack, "state", edit_after_first_snapshot)
    response = client.post("/api/captures", headers=AUTH)
    assert response.status_code == 200
    files = members(response.content)
    snapshot = json.loads(files["stack.json"])
    assert [item["note"] for item in snapshot["items"]] == ["Earlier note"]
    assert "Earlier note" in files["composed.md"].decode()
    assert "Later note" not in files["composed.md"].decode()
    assert {item["note"] for item in original_state()["items"]} == {"Earlier note", "Later note"}


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


def test_a_capture_marks_the_free_text_gap_when_machine_names_are_unknown(client: TestClient):
    state = client.app.state.sentinel
    state._redactor = Redactor()  # The identity lookup returned unavailable; named fields still mask.
    state._learned_at = time.monotonic()

    response = client.post("/api/captures", headers=AUTH)
    assert response.status_code == 200
    files = members(response.content)
    manifest = json.loads(files["manifest.json"])
    event = json.loads(files["readings/events.json"])
    assert manifest["redaction_gaps"] == ["host", "user"]
    assert event["redaction_gaps"] == ["host", "user"]
    assert event["sections"][0]["data"][0]["MachineName"] == "<host>"
    assert "TESTBOX" in event["sections"][0]["data"][0]["Message"]
    assert "Redaction note" in files["composed.md"].decode().splitlines()[2]
    listed = {item["name"]: item["manifest"] for item in client.get("/api/captures", headers=AUTH).json()["captures"]}
    assert listed[response.headers["X-Capture-Name"]]["redaction_gaps"] == ["host", "user"]


def test_a_reading_that_cannot_be_taken_is_written_with_its_outcome(client: TestClient):
    client.bridge.result = BridgeResult("unavailable", error="powershell.exe was not found")
    files = members(client.post("/api/captures", headers=AUTH).content)
    events = json.loads(files["readings/events.json"])
    assert events["outcome"] == "unavailable" and events["error"]["kind"] == "unavailable"
    assert events["sections"] == []
    # `record` needs a moment; a capture's moment is its own, so it is taken like the rest.
    assert json.loads(files["readings/record.json"])["params"]["before"].endswith("Z")


def test_capture_keeps_machine_readings_when_saved_stack_is_unavailable(client: TestClient):
    path = client.app.state.sentinel.stack.store.path
    path.write_bytes(b"{broken")
    response = client.post("/api/captures", headers=AUTH)
    assert response.status_code == 200
    files = members(response.content)
    manifest = json.loads(files["manifest.json"])
    assert {"stack.json", "composed.md"}.isdisjoint(files)
    assert {entry["member"] for entry in manifest["unavailable"]} == {"stack.json", "composed.md"}
    assert "readings/events.json" in files and path.read_bytes() == b"{broken"
    listed = client.get("/api/captures", headers=AUTH).json()["captures"][0]["manifest"]
    assert set(listed["unavailable"]) == {"stack.json", "composed.md"}


def test_capture_keeps_handoff_when_selected_prompt_library_is_damaged(client: TestClient):
    client.post("/api/stack/items", headers=AUTH, json={"kind": "note", "note": "Keep this evidence"})
    path = client.app.state.sentinel.prompts.store.path
    path.write_bytes(b"{broken")
    response = client.post("/api/captures", headers=AUTH)
    assert response.status_code == 200
    files = members(response.content)
    manifest = json.loads(files["manifest.json"])
    composed = next(entry for entry in manifest["members"] if entry["path"] == "composed.md")
    assert composed["prompt"] == {"id": "quantum-diagnostician", "state": "unavailable", "reason": "invalid"}
    assert "prompt library could not be used (invalid)" in files["composed.md"].decode()
    assert "Keep this evidence" in files["composed.md"].decode()
    assert "stack.json" in files and manifest["unavailable"] == []
    listed = client.get("/api/captures", headers=AUTH).json()["captures"][0]["manifest"]
    assert listed["prompt_state"] == "unavailable" and path.read_bytes() == b"{broken"


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


def test_cancelled_capture_preserves_cancellation_when_discarded_zip_close_fails(monkeypatch: pytest.MonkeyPatch, caplog):
    monkeypatch.setattr(capture, "REGISTRY", {"health": REGISTRY["health"]})
    entered = asyncio.Event()
    pause = asyncio.Event()
    original_zip = capture.zipfile.ZipFile

    def zip_with_failed_close(*args, **kwargs):
        archive = original_zip(*args, **kwargs)
        original_close = archive.close

        def close_once_then_fail():
            archive.close = original_close
            original_close()
            raise OSError("synthetic discarded-ZIP close failure")

        archive.close = close_once_then_fail
        return archive

    monkeypatch.setattr(capture.zipfile, "ZipFile", zip_with_failed_close)

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
        assert listing() == [] and not list(captures_dir().iterdir())

    asyncio.run(exercise())
    assert "unfinished capture could not be closed" in caplog.text


def test_capture_stack_tail_keeps_the_event_loop_free(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(capture, "REGISTRY", {"health": REGISTRY["health"]})
    stack = Stack()
    stack.add(Item(id="seed", added_at="2026-09-24T00:00:00Z", kind="note", title="Seed", note="Keep evidence"))
    entered, release = threading.Event(), threading.Event()
    original_state = stack.state

    def gated_state():
        entered.set()
        release.wait(3)
        return original_state()

    monkeypatch.setattr(stack, "state", gated_state)

    async def reader(name, params):
        await asyncio.sleep(0)
        return Reading(reading=name, params=params, outcome="empty", method={"kind": "synthetic"})

    async def exercise():
        task = asyncio.create_task(capture.create(FakeBridge(), stack, Prompts(), reader=reader))
        safety = threading.Timer(2, release.set)
        safety.start()
        try:
            assert await asyncio.to_thread(entered.wait, 3), "capture did not reach saved Stack"
            assert not release.is_set(), "capture assembly blocked the event loop"
        finally:
            release.set()
            safety.cancel()
        made = await task
        with zipfile.ZipFile(made.path) as archive:
            assert archive.testzip() is None
            assert json.loads(archive.read("stack.json"))["items"][0]["note"] == "Keep evidence"
            assert "Keep evidence" in archive.read("composed.md").decode()

    asyncio.run(exercise())


@pytest.mark.parametrize("cancel_kind", ["task", "anyio"])
def test_cancelled_capture_tail_finishes_one_complete_archive(monkeypatch: pytest.MonkeyPatch, cancel_kind: str):
    monkeypatch.setattr(capture, "REGISTRY", {"health": REGISTRY["health"]})
    stack = Stack()
    stack.add(Item(id="seed", added_at="2026-09-24T00:00:00Z", kind="note", title="Seed", note="Keep evidence"))
    entered, release = threading.Event(), threading.Event()
    original_state = stack.state

    def gated_state():
        entered.set()
        release.wait(3)
        return original_state()

    monkeypatch.setattr(stack, "state", gated_state)

    async def reader(name, params):
        return Reading(reading=name, params=params, outcome="empty", method={"kind": "synthetic"})

    async def exercise():
        safety = threading.Timer(3, release.set)
        safety.start()
        try:
            if cancel_kind == "anyio":
                async def take_capture():
                    await capture.create(FakeBridge(), stack, Prompts(), reader=reader)

                async with capture.anyio.create_task_group() as group:
                    group.start_soon(take_capture)
                    assert await asyncio.to_thread(entered.wait, 4)
                    group.cancel_scope.cancel()
                    release.set()
            else:
                task = asyncio.create_task(capture.create(FakeBridge(), stack, Prompts(), reader=reader))
                assert await asyncio.to_thread(entered.wait, 4)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        finally:
            release.set()
            safety.cancel()
        for _ in range(100):
            files = listing()
            if len(files) == 1 and not list(captures_dir().glob("*.pending")):
                break
            await asyncio.sleep(0.01)
        assert len(files) == 1 and files[0]["manifest"]["status"] == "read"
        assert not list(captures_dir().glob("*.pending"))
        with zipfile.ZipFile(captures_dir() / files[0]["name"]) as archive:
            assert archive.testzip() is None
            assert set(archive.namelist()) == {"readings/health.json", "stack.json", "composed.md", "manifest.json"}
            assert json.loads(archive.read("stack.json"))["items"][0]["note"] == "Keep evidence"
            assert "Keep evidence" in archive.read("composed.md").decode()

    asyncio.run(exercise())


def test_capture_cancelled_before_tail_worker_claims_cleans_up(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(capture, "REGISTRY", {"health": REGISTRY["health"]})
    entered = asyncio.Event()
    release = asyncio.Event()
    held = {}
    original_run = capture.anyio.to_thread.run_sync

    async def delayed_run(func, *args):
        held["call"] = (func, args)
        entered.set()
        await release.wait()
        return await original_run(func, *args)

    monkeypatch.setattr(capture.anyio.to_thread, "run_sync", delayed_run)

    async def reader(name, params):
        return Reading(reading=name, params=params, outcome="empty", method={"kind": "synthetic"})

    async def exercise():
        task = asyncio.create_task(capture.create(FakeBridge(), Stack(), Prompts(), reader=reader))
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert listing() == [] and not list(captures_dir().iterdir())
        func, args = held["call"]
        assert await asyncio.to_thread(func, *args) is None  # a late worker cannot reopen the cleaned ZIP

    asyncio.run(exercise())


def test_capture_tail_write_failure_leaves_no_partial_file(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(capture, "REGISTRY", {"health": REGISTRY["health"]})
    original_write = capture._write

    def fail_stack_member(archive, member, text):
        if member == "stack.json":
            raise OSError("synthetic Stack member write failure")
        return original_write(archive, member, text)

    monkeypatch.setattr(capture, "_write", fail_stack_member)

    async def reader(name, params):
        return Reading(reading=name, params=params, outcome="empty", method={"kind": "synthetic"})

    with pytest.raises(OSError, match="synthetic Stack member write failure"):
        asyncio.run(capture.create(FakeBridge(), Stack(), Prompts(), reader=reader))
    assert listing() == [] and not list(captures_dir().iterdir())


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
        "capture-20260920T000014Z.zip": ({**base, "redaction_gaps": [{"host": "PRIVATE-HOST"}]}, "unreadable", None),
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
