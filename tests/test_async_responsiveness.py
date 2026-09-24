"""A slow identity lookup must delay its own redacted answer without freezing other clients."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx
import pytest
from mcp import types

from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.mcp_server import Surface
from sentinel.stack import Item
from tests.conftest import FakeBridge, identity_result

TOKEN = "test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class DelayedIdentity(FakeBridge):
    def __init__(self, entered: threading.Event, release: threading.Event):
        super().__init__(BridgeResult("empty", items=[]))
        self.entered = entered
        self.release = release
        self.lookups = 0

    def run(self, script: str, *, timeout: float = 60, depth: int = 6) -> BridgeResult:
        if "$env:COMPUTERNAME" in script:
            self.lookups += 1
            self.entered.set()
            self.release.wait(4)
            return identity_result("TESTBOX", "tester")
        return super().run(script, timeout=timeout, depth=depth)


class CrashedIdentity(FakeBridge):
    def __init__(self):
        super().__init__()
        self.lookups = 0
        self.fail = True

    def run(self, script: str, *, timeout: float = 60, depth: int = 6) -> BridgeResult:
        if "$env:COMPUTERNAME" in script:
            self.lookups += 1
            if self.fail:
                raise RuntimeError("synthetic identity failure")
            return identity_result("TESTBOX", "tester")
        return super().run(script, timeout=timeout, depth=depth)


async def _observe(verb: str, path: str, data: dict | None = None) -> tuple[bool, bool, httpx.Response]:
    entered, release = threading.Event(), threading.Event()
    bridge = DelayedIdentity(entered, release)
    app = create_app(State(bridge=bridge, token=TOKEN), mcp=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        safety = threading.Timer(2, release.set)
        safety.start()
        try:
            request = asyncio.create_task(client.request(verb, path, json=data, headers=AUTH))
            assert await asyncio.to_thread(entered.wait, 3), "identity lookup never started"
            loop_free = not release.is_set()
            request_pending = not request.done()
        finally:
            release.set()
            safety.cancel()
        response = await request
    assert bridge.lookups == 1
    return loop_free, request_pending, response


def test_async_stack_add_does_not_freeze_other_clients_during_identity_lookup(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    free, pending, response = asyncio.run(_observe("POST", "/api/stack/items", {"kind": "note", "note": "TESTBOX tester"}))
    assert free and pending
    assert response.status_code == 201
    assert "TESTBOX" not in response.text and "tester" not in response.text
    assert "<host>" in response.text and "<user>" in response.text


def test_sync_stack_index_keeps_loop_free_while_its_lookup_waits(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    free, pending, response = asyncio.run(_observe("GET", "/api/stack"))
    assert free and pending and response.status_code == 200


def test_concurrent_http_and_mcp_callers_join_one_lookup_without_weak_redaction(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    entered, release = threading.Event(), threading.Event()
    bridge = DelayedIdentity(entered, release)
    state = State(bridge=bridge, token=TOKEN)
    state.stack.add(Item(id="seed", added_at="2026-09-24T00:00:00Z", kind="note", title="TESTBOX", note="TESTBOX tester"))
    app = create_app(state, mcp=False)
    surface = Surface(state)

    async def run():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = asyncio.create_task(client.post("/api/stack/items", json={"kind": "note", "note": "TESTBOX first"}, headers=AUTH))
            safety = threading.Timer(3, release.set)
            safety.start()
            try:
                assert await asyncio.to_thread(entered.wait, 4)
                second = asyncio.create_task(client.post("/api/stack/items", json={"kind": "note", "note": "tester second"}, headers=AUTH))
                listing = asyncio.create_task(client.get("/api/stack", headers=AUTH))
                agent = asyncio.create_task(surface.call_tool(None, types.CallToolRequestParams(name="stack_list", arguments={})))
                await asyncio.sleep(0.05)
                assert not any(task.done() for task in (first, second, listing, agent))
                # Waiting callers must not occupy the executor used by new readings.
                assert await asyncio.wait_for(asyncio.to_thread(lambda: "free"), 1) == "free"
                assert not release.is_set()
            finally:
                release.set()
                safety.cancel()
            answers = await asyncio.gather(first, second, listing, agent)
            assert bridge.lookups == 1
            for answer in answers[:3]:
                assert answer.status_code in (200, 201)
                assert "TESTBOX" not in answer.text and "tester" not in answer.text
            assert "<host>" in answers[2].text and "<user>" in answers[2].text
            assert "TESTBOX" not in answers[3].content[0].text and "tester" not in answers[3].content[0].text

    asyncio.run(run())


def test_cancelling_one_waiter_does_not_cancel_the_shared_lookup(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    entered, release = threading.Event(), threading.Event()
    bridge = DelayedIdentity(entered, release)
    state = State(bridge=bridge, token=TOKEN)
    app = create_app(state, mcp=False)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = asyncio.create_task(client.post("/api/stack/items", json={"kind": "note", "note": "TESTBOX cancelled"}, headers=AUTH))
            safety = threading.Timer(3, release.set)
            safety.start()
            try:
                assert await asyncio.to_thread(entered.wait, 4)
                second = asyncio.create_task(client.post("/api/stack/items", json={"kind": "note", "note": "TESTBOX kept"}, headers=AUTH))
                await asyncio.sleep(0)
                first.cancel()
                try:
                    await first
                except asyncio.CancelledError:
                    pass
                assert not second.done() and bridge.lookups == 1
            finally:
                release.set()
                safety.cancel()
            response = await second
            assert response.status_code == 201 and "<host> kept" in response.text
            assert "cancelled" not in response.text and len(state.stack.state()["items"]) == 1

    asyncio.run(run())


def test_crashed_identity_lookup_refuses_before_an_edit_and_retries_after_interval(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    bridge = CrashedIdentity()
    state = State(bridge=bridge, token=TOKEN)
    app = create_app(state, mcp=False)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
            for expected_lookups in (1, 1):
                response = await client.post("/api/stack/items", json={"kind": "note", "note": "TESTBOX should not be saved"}, headers=AUTH)
                assert response.status_code == 500 and "TESTBOX" not in response.text
                assert state.stack.state()["items"] == [] and bridge.lookups == expected_lookups
            state._learned_at = 0  # an expired refusal permits another bounded attempt
            retry = await client.post("/api/stack/items", json={"kind": "note", "note": "TESTBOX should not be saved"}, headers=AUTH)
            assert retry.status_code == 500 and bridge.lookups == 2 and state.stack.state()["items"] == []

    asyncio.run(run())
    bridge.fail = False
    state.learn()  # a later successful direct learning must not be trapped by a stale error
    assert state.redactor.identity.host == "TESTBOX" and bridge.lookups == 3


def test_crashed_identity_lookup_refuses_all_sync_edits_before_writing(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    bridge = CrashedIdentity()
    state = State(bridge=bridge, token=TOKEN)
    state.stack.add(Item(id="seed", added_at="2026-09-24T00:00:00Z", kind="note", title="Seed", note="keep"))
    state.performance_store.configure(True, 60)
    state.performance_store.append({"at": datetime.now(UTC).isoformat(), "cpu_percent": 5})
    stack_path = state.stack.store.path
    settings_path = state.performance_store.home / "settings.json"
    status_path = state.performance_store.home / "status.json"
    samples = list(state.performance_store.home.glob("*.jsonl"))
    assert len(samples) == 1
    original = (stack_path.read_bytes(), settings_path.read_bytes(), samples[0].read_bytes())
    app = create_app(state, mcp=False)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
            invalid = await client.put(
                "/api/performance/collection",
                json={"enabled": True, "interval_seconds": 10},
                headers=AUTH,
            )
            assert invalid.status_code == 422 and bridge.lookups == 0
            edits = (
                ("PATCH", "/api/stack", {"system_prompt": False}),
                ("DELETE", "/api/stack", None),
                ("PATCH", "/api/stack/items/seed", {"rank": 1}),
                ("DELETE", "/api/stack/items/seed", None),
                ("PUT", "/api/performance/collection", {"enabled": False, "interval_seconds": 120}),
                ("DELETE", "/api/performance/history", None),
            )
            for verb, path, data in edits:
                response = await client.request(verb, path, json=data, headers=AUTH)
                assert response.status_code == 500, (path, response.text)
                assert (stack_path.read_bytes(), settings_path.read_bytes(), samples[0].read_bytes()) == original
                assert not status_path.exists()
                assert state.stack.state()["items"][0]["id"] == "seed"
            assert bridge.lookups == 1

    asyncio.run(run())


def test_sync_redactor_cannot_be_called_on_the_event_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    state = State(bridge=FakeBridge(), token=TOKEN)

    async def read_wrongly():
        with pytest.raises(RuntimeError, match="await State.redaction"):
            _ = state.redactor

    asyncio.run(read_wrongly())
