"""A slow identity lookup must delay its own redacted answer without freezing other clients."""

import asyncio
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime

import anyio
import httpx
import pytest
from mcp import types
from mcp.server.subscriptions import ResourceUpdated
from mcp.shared.exceptions import MCPError

import sentinel.app as app_module
from sentinel import capture
from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.mcp_server import HANDOFF_URI, Surface
from sentinel.redact import Identity, Redactor
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
        self.unavailable = False

    def run(self, script: str, *, timeout: float = 60, depth: int = 6) -> BridgeResult:
        if "$env:COMPUTERNAME" in script:
            self.lookups += 1
            if self.fail:
                raise RuntimeError(r"TESTBOX tester C:\Users\tester synthetic identity failure")
            if self.unavailable:
                return BridgeResult("unavailable", error="synthetic bridge unavailable")
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
                assert response.status_code == 503 and "TESTBOX" not in response.text and "tester" not in response.text
                assert response.json() == {
                    "error": "redaction_withheld", "reason": "identity_lookup_failed",
                    "detail": "Sentinel could not look up this computer's names, so it cannot mask them inside message text. Redacted answers are withheld until the next lookup. Where a request offers an explicit unredacted option, it can bypass this refusal but may expose real values.",
                    "retry_after": int(response.headers["Retry-After"]),
                }
                assert 1 <= int(response.headers["Retry-After"]) <= 60
                assert state.stack.state()["items"] == [] and bridge.lookups == expected_lookups
            state._learned_at = time.monotonic() - 61  # an expired refusal permits another bounded attempt
            retry = await client.post("/api/stack/items", json={"kind": "note", "note": "TESTBOX should not be saved"}, headers=AUTH)
            assert retry.status_code == 503 and bridge.lookups == 2 and state.stack.state()["items"] == []

    asyncio.run(run())
    bridge.fail = False
    state.learn()  # a later successful direct learning must not be trapped by a stale error
    assert state.redactor.identity.host == "TESTBOX" and bridge.lookups == 3


def test_a_finished_retry_clears_its_slot_before_waking_waiters(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    published, release = threading.Event(), threading.Event()

    class HeldCompletion(Future[None]):
        def set_exception(self, exception):
            super().set_exception(exception)
            published.set()
            release.wait(3)

    monkeypatch.setattr(app_module, "Future", HeldCompletion)
    bridge = CrashedIdentity()
    state = State(bridge=bridge, token=TOKEN)

    try:
        pending = state._pending_relearn()
        assert pending is not None and published.wait(3)
        bridge.fail = False
        state.learn()
        assert asyncio.run(state.redaction()).identity.host == "TESTBOX"
        assert bridge.lookups == 2
        with pytest.raises(app_module.RedactionWithheld):
            pending.result()
    finally:
        release.set()


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
                assert response.status_code == 503 and response.json()["error"] == "redaction_withheld", (path, response.text)
                assert "TESTBOX" not in response.text and "tester" not in response.text
                assert (stack_path.read_bytes(), settings_path.read_bytes(), samples[0].read_bytes()) == original
                assert not status_path.exists()
                assert state.stack.state()["items"][0]["id"] == "seed"
            assert bridge.lookups == 1

    asyncio.run(run())


def test_refusal_covers_redacted_reading_handoff_and_capture_without_writing_a_zip(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    bridge = CrashedIdentity()
    state = State(bridge=bridge, token=TOKEN)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state, mcp=False)), base_url="http://test") as client:
            for method, path in (("GET", "/api/readings/events"), ("GET", "/api/stack/composed"), ("POST", "/api/captures")):
                response = await client.request(method, path, headers=AUTH)
                assert response.status_code == 503 and response.json()["error"] == "redaction_withheld"
                assert response.headers["Retry-After"]
                assert "TESTBOX" not in response.text and "tester" not in response.text
            assert bridge.lookups == 1

    asyncio.run(run())
    assert capture.listing() == [] and state.stack.state()["items"] == []


def test_a_later_routine_unavailable_lookup_lifts_the_exception_refusal(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    bridge = CrashedIdentity()
    state = State(bridge=bridge, token=TOKEN)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state, mcp=False)), base_url="http://test") as client:
            refused = await client.post("/api/stack/items", json={"kind": "note", "note": "TESTBOX tester"}, headers=AUTH)
            assert refused.status_code == 503 and state.stack.state()["items"] == []
            bridge.fail = False
            bridge.unavailable = True
            state._learned_at = time.monotonic() - 61
            served = await client.post("/api/stack/items", json={"kind": "note", "note": "TESTBOX tester"}, headers=AUTH)
            assert served.status_code == 201 and served.json()["redaction_gaps"] == ["host", "user"]
            assert served.json()["note"] == "TESTBOX tester"
            assert bridge.lookups == 2

    asyncio.run(run())


def test_a_crashed_startup_explains_redaction_refusal_without_exposing_the_exception(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    bridge = CrashedIdentity()
    with TestClient(create_app(State(bridge=bridge, token=TOKEN), mcp=False)) as client:
        refused = client.get("/api/stack", headers=AUTH)
        assert refused.status_code == 503 and refused.json()["error"] == "redaction_withheld"
        assert "TESTBOX" not in refused.text and "tester" not in refused.text
        assert client.get("/api/readings", headers=AUTH).status_code == 200
        assert client.get("/api/readings/events?unredacted=true", headers=AUTH).status_code == 200
        health = client.get("/api/readings/health?unredacted=true", headers=AUTH)
        assert health.status_code == 200 and health.json()["outcome"] == "failed"
        assert "internal error" in health.json()["error"]["detail"]
        assert "TESTBOX" not in health.text and "tester" not in health.text


def test_mcp_names_a_redaction_refusal_and_preserves_the_explicit_unredacted_path(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    state = State(bridge=CrashedIdentity(), token=TOKEN)
    surface = Surface(state)

    async def run():
        for name in ("stack_list", "events"):
            refused = await surface.call_tool(None, types.CallToolRequestParams(name=name, arguments={}))
            assert refused.is_error and "redaction_withheld" in refused.content[0].text
            assert "unredacted with a reason" in refused.content[0].text
            assert "TESTBOX" not in refused.content[0].text and "tester" not in refused.content[0].text
            given = await surface.call_tool(None, types.CallToolRequestParams(name=name, arguments={"unredacted": True, "reason": "inspect synthetic failure"}))
            assert not given.is_error
        for name in ("prompts_list", "capture_list"):
            available = await surface.call_tool(None, types.CallToolRequestParams(name=name, arguments={}))
            assert not available.is_error
        edit = await surface.call_tool(None, types.CallToolRequestParams(name="stack_add", arguments={"kind": "note", "note": "TESTBOX tester"}))
        assert edit.is_error and state.stack.state()["items"] == []
        with pytest.raises(MCPError, match="redaction_withheld"):
            await surface.read_resource(None, types.ReadResourceRequestParams(uri=HANDOFF_URI))

    asyncio.run(run())


def test_sync_redactor_cannot_be_called_on_the_event_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    state = State(bridge=FakeBridge(), token=TOKEN)

    async def read_wrongly():
        with pytest.raises(RuntimeError, match="await State.redaction"):
            _ = state.redactor

    asyncio.run(read_wrongly())


def test_saved_stack_work_in_async_http_and_mcp_keeps_loop_free(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    state = State(bridge=FakeBridge(), token=TOKEN)
    state._redactor = Redactor(Identity(host="TESTBOX", user="tester"))
    state._learned_at = time.monotonic()
    state.stack.add(Item(id="seed", added_at="2026-09-24T00:00:00Z", kind="note", title="TESTBOX", note="TESTBOX tester"))
    surface = Surface(state)
    app = create_app(state, mcp=False)
    entered, release = threading.Event(), threading.Event()
    original_read = state.stack.store.read

    def gated_read():
        entered.set()
        release.wait(3)
        return original_read()

    monkeypatch.setattr(state.stack.store, "read", gated_read)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def mcp(name, arguments):
                answer = await surface.call_tool(None, types.CallToolRequestParams(name=name, arguments=arguments))
                assert not answer.is_error
                return answer.content[0].text

            operations = (
                (lambda: mcp("stack_list", {}), "<host>"),
                (lambda: mcp("compose", {}), "<host>"),
                (lambda: mcp("stack_prompt", {"system_prompt": False}), "<host>"),
                (lambda: client.post("/api/stack/items", json={"kind": "note", "note": "TESTBOX kept"}, headers=AUTH), "<host>"),
                (lambda: client.get("/api/stack", headers=AUTH), "<host>"),
            )
            for operation, expected in operations:
                entered.clear()
                release.clear()
                safety = threading.Timer(2, release.set)
                safety.start()
                try:
                    task = asyncio.create_task(operation())
                    assert await asyncio.to_thread(entered.wait, 3), "saved Stack read never began"
                    assert not release.is_set(), "saved Stack work blocked the event loop"
                finally:
                    release.set()
                    safety.cancel()
                answer = await task
                body = answer.text if isinstance(answer, httpx.Response) else answer
                assert expected in body and "TESTBOX" not in body
                if isinstance(answer, httpx.Response):
                    assert answer.status_code in (200, 201)

    asyncio.run(run())


def test_prompt_library_reads_in_async_mcp_keep_loop_free(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    state = State(bridge=FakeBridge(), token=TOKEN)
    state._redactor = Redactor(Identity(host="TESTBOX", user="tester"))
    state._learned_at = time.monotonic()
    surface = Surface(state)
    state.prompts.all()  # seed before the gate
    entered, release = threading.Event(), threading.Event()
    original_read = state.prompts.store.read

    def gated_read():
        entered.set()
        release.wait(3)
        return original_read()

    monkeypatch.setattr(state.prompts.store, "read", gated_read)

    async def run():
        for operation in (
            lambda: surface.call_tool(None, types.CallToolRequestParams(name="prompts_list", arguments={})),
            lambda: surface.list_prompts(),
        ):
            entered.clear()
            release.clear()
            safety = threading.Timer(2, release.set)
            safety.start()
            try:
                task = asyncio.create_task(operation())
                assert await asyncio.to_thread(entered.wait, 3)
                assert not release.is_set(), "prompt read blocked the event loop"
            finally:
                release.set()
                safety.cancel()
            assert await task is not None

    asyncio.run(run())


def test_mcp_stack_read_waiting_on_another_edit_keeps_loop_free(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    state = State(bridge=FakeBridge(), token=TOKEN)
    state._redactor = Redactor(Identity(host="TESTBOX", user="tester"))
    state._learned_at = time.monotonic()
    surface = Surface(state)
    holder_entered, request_entered, release = threading.Event(), threading.Event(), threading.Event()
    original_state = state.stack.state

    def waiting_state():
        request_entered.set()
        return original_state()

    monkeypatch.setattr(state.stack, "state", waiting_state)

    def hold_transaction():
        with state.stack.store.transaction():
            holder_entered.set()
            release.wait(3)

    holder = threading.Thread(target=hold_transaction)
    holder.start()

    async def run():
        assert await asyncio.to_thread(holder_entered.wait, 3)
        safety = threading.Timer(2, release.set)
        safety.start()
        try:
            task = asyncio.create_task(surface.call_tool(None, types.CallToolRequestParams(name="stack_list", arguments={})))
            assert await asyncio.to_thread(request_entered.wait, 3)
            assert not release.is_set(), "waiting for another saved edit blocked the event loop"
        finally:
            release.set()
            safety.cancel()
        assert not (await task).is_error

    try:
        asyncio.run(run())
    finally:
        release.set()
        holder.join(timeout=4)
    assert not holder.is_alive()


@pytest.mark.parametrize(("client_kind", "cancel_kind"), [("mcp_remove", "task"), ("http_add", "task"), ("mcp_remove", "anyio")])
def test_cancelled_saved_edit_still_notifies_subscribers(monkeypatch, tmp_path, client_kind, cancel_kind):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    state = State(bridge=FakeBridge(), token=TOKEN)
    state._redactor = Redactor(Identity(host="TESTBOX", user="tester"))
    state._learned_at = time.monotonic()
    state.stack.add(Item(id="seed", added_at="2026-09-24T00:00:00Z", kind="note", title="Seed", note="keep"))
    app = create_app(state)
    surface = app.state.mcp_surface
    published = []
    surface.bus.subscribe(published.append)
    entered, release, write_done = threading.Event(), threading.Event(), threading.Event()
    original_write = state.stack.store.write

    def gated_write(saved):
        entered.set()
        release.wait(3)
        original_write(saved)
        write_done.set()

    monkeypatch.setattr(state.stack.store, "write", gated_write)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            if client_kind == "mcp_remove":
                operation = surface.call_tool(None, types.CallToolRequestParams(name="stack_remove", arguments={"id": "seed"}))
            else:
                operation = client.post("/api/stack/items", json={"kind": "note", "note": "TESTBOX new"}, headers=AUTH)
            safety = threading.Timer(3, release.set)
            safety.start()
            try:
                if cancel_kind == "anyio":
                    async def perform():
                        await operation

                    async with anyio.create_task_group() as group:
                        group.start_soon(perform)
                        assert await asyncio.to_thread(entered.wait, 4), "save did not begin"
                        group.cancel_scope.cancel()
                        release.set()
                else:
                    task = asyncio.create_task(operation)
                    assert await asyncio.to_thread(entered.wait, 4), "save did not begin"
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
            finally:
                release.set()
                safety.cancel()
            assert await asyncio.to_thread(write_done.wait, 4), "cancelled worker did not finish its save"
            for _ in range(100):
                if published:
                    break
                await asyncio.sleep(0.01)
            assert published == [ResourceUpdated(HANDOFF_URI)]
            ids = [item["id"] for item in state.stack.state()["items"]]
            if client_kind == "mcp_remove":
                assert "seed" not in ids
            else:
                assert "seed" in ids and len(ids) == 2

    asyncio.run(run())


@pytest.mark.parametrize("client_kind", ["mcp_remove", "http_add"])
def test_notification_failure_does_not_report_a_saved_edit_as_failed(monkeypatch, tmp_path, caplog, client_kind):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    state = State(bridge=FakeBridge(), token=TOKEN)
    state._redactor = Redactor(Identity(host="TESTBOX", user="tester"))
    state._learned_at = time.monotonic()
    state.stack.add(Item(id="seed", added_at="2026-09-24T00:00:00Z", kind="note", title="Seed", note="keep"))
    app = create_app(state)
    surface = app.state.mcp_surface

    async def broken_publish(_event):
        raise RuntimeError("synthetic subscriber failure")

    monkeypatch.setattr(surface.bus, "publish", broken_publish)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            if client_kind == "mcp_remove":
                answer = await surface.call_tool(None, types.CallToolRequestParams(name="stack_remove", arguments={"id": "seed"}))
                assert not answer.is_error
                assert state.stack.state()["items"] == []
            else:
                answer = await client.post("/api/stack/items", json={"kind": "note", "note": "TESTBOX saved"}, headers=AUTH)
                assert answer.status_code == 201 and "<host> saved" in answer.text
                assert len(state.stack.state()["items"]) == 2

    asyncio.run(run())
    assert "saved Stack change could not notify subscribers" in caplog.text
