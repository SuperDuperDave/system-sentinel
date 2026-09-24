"""The boundary: the token guards every /api and /mcp route; readings arrive redacted unless asked by name."""

import json

import pytest
from fastapi.testclient import TestClient

from sentinel.app import State, create_app
from sentinel.bridge import Bridge, BridgeResult
from sentinel.readings import health
from tests.conftest import FakeBridge, LogBridge, identity_result, log_collector_result

TOKEN = "test-token-0123456789"

EVENT = {
    "RecordId": 1,
    "Id": 41,
    "ProviderName": "Microsoft-Windows-Kernel-Power",
    "MachineName": "TESTBOX",
    "LevelDisplayName": "Critical",
    "TimeCreated": "2026-09-20T18:04:11.204Z",
    "Message": r"The system has rebooted without cleanly shutting down first. Logged on TESTBOX for tester at C:\Users\tester\x.",
}


@pytest.fixture
def client():
    bridge = LogBridge(
        result=BridgeResult("ok", items=[EVENT], took_ms=5),
        by_marker={"$env:COMPUTERNAME": identity_result("TESTBOX", "tester")},
    )
    app = create_app(State(bridge=bridge, token=TOKEN))
    with TestClient(app) as c:
        c.bridge = bridge
        yield c


AUTH = {"Authorization": f"Bearer {TOKEN}"}
MCP_HEADERS = {**AUTH, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_api_is_closed_without_the_token(client: TestClient):
    r = client.get("/api/readings")
    assert r.status_code == 401 and r.json() == {"error": "unauthorized"}
    assert r.headers["WWW-Authenticate"] == "Bearer"
    assert client.get("/api/readings/events", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/mcp", json={}, headers={"Accept": "application/json"}).status_code == 401


def test_openapi_is_also_behind_the_token(client: TestClient):
    assert client.get("/api/openapi.json").status_code == 401
    spec = client.get("/api/openapi.json", headers=AUTH).json()
    assert "/api/readings/{name}" in spec["paths"]


def test_catalog(client: TestClient):
    body = client.get("/api/readings", headers=AUTH).json()
    names = {r["name"] for r in body["readings"]}
    assert {"health", "events", "record"} <= names
    events = next(r for r in body["readings"] if r["name"] == "events")
    assert [p["name"] for p in events["params"]] == ["log", "levels", "count", "since", "before"]
    assert events["private"]


def test_reading_arrives_redacted_by_default(client: TestClient):
    body = client.get("/api/readings/events?count=1", headers=AUTH).json()
    assert body["outcome"] == "ok" and body["count"] == 1
    rec = body["sections"][0]["data"][0]
    assert rec["MachineName"] == "<host>"
    assert "TESTBOX" not in rec["Message"] and "tester" not in rec["Message"]
    assert r"C:\Users\<user>\x" in rec["Message"]
    assert body["redacted"] == ["host", "user"]
    assert body["params"] == {"log": "System", "levels": [1, 2], "count": 1, "since": "", "before": ""}
    assert body["method"]["kind"] == "powershell" and "Get-WinEvent" in body["method"]["query"]


def test_bounded_window_reaches_an_agent_through_the_authenticated_route():
    start, end = "2026-09-20T00:00:00.000Z", "2026-09-21T00:00:00.000Z"
    bridge = FakeBridge(result=log_collector_result([], limit=5, window_start=start, window_end=end, queried_at=end),
                        by_marker={"$env:COMPUTERNAME": identity_result("TESTBOX", "tester")})
    with TestClient(create_app(State(bridge=bridge, token=TOKEN))) as client:
        assert client.get(f"/api/readings/events?since={start}&before={end}&count=5").status_code == 401
        response = client.get(f"/api/readings/events?since={start}&before={end}&count=5", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "empty" and body["params"]["before"] == end
    coverage = next(section["data"] for section in body["sections"] if section["name"] == "coverage")
    assert coverage["complete"] is True and coverage["covered_until"] == end


def test_a_failed_relearn_keeps_names_already_learned():
    bridge = FakeBridge(by_marker={"$env:COMPUTERNAME": identity_result("TESTBOX", "tester")})
    state = State(bridge=bridge, token=TOKEN)
    state.learn()
    bridge.by_marker["$env:COMPUTERNAME"] = BridgeResult("unavailable", error="temporary outage")
    state.learn()
    assert state.identity.host == "TESTBOX"
    assert state.redactor.attach({"Message": "TESTBOX signed in tester"})["Message"] == "<host> signed in <user>"


def test_native_windows_identity_fallback_keeps_the_bridge_failure_visible(monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    class FailingBridge(Bridge):
        def run(self, script: str, *, timeout: float = 60, depth: int = 6) -> BridgeResult:
            return BridgeResult("unavailable", error="temporary outage")

    monkeypatch.setattr(health, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setenv("COMPUTERNAME", "TESTBOX")
    monkeypatch.setenv("USERNAME", "tester")
    bridge = FailingBridge(exe="powershell.exe")
    identity, facts = health.learn_identity(bridge)
    assert identity.host == "TESTBOX" and identity.user == "tester"
    assert facts == {"outcome": "unavailable", "error": "temporary outage"}


def test_unredacted_by_name(client: TestClient):
    body = client.get("/api/readings/events?unredacted=true", headers=AUTH).json()
    rec = body["sections"][0]["data"][0]
    assert rec["MachineName"] == "TESTBOX" and "tester" in rec["Message"]
    assert body["redacted"] == []


def test_unknown_reading_and_bad_parameter(client: TestClient):
    assert client.get("/api/readings/nope", headers=AUTH).status_code == 404
    r = client.get("/api/readings/events?count=lots", headers=AUTH)
    assert r.status_code == 422 and "count" in r.json()["detail"]
    r = client.get("/api/readings/record?before=yesterday", headers=AUTH)
    assert r.status_code == 422 and "before" in r.json()["detail"]


def test_count_bounds_are_rejected_before_the_bridge(client: TestClient):
    client.bridge.scripts.clear()
    for name, params in (("events", ""), ("record", "&before=2026-09-20T18%3A04%3A11Z"), ("whea", ""), ("faults", ""), ("drivers", "")):
        response = client.get(f"/api/readings/{name}?count=0{params}", headers=AUTH)
        assert response.status_code == 422 and "count" in response.json()["detail"], name
    assert client.bridge.scripts == []
    catalog = {spec["name"]: spec for spec in client.get("/api/readings", headers=AUTH).json()["readings"]}
    for name in ("events", "record", "whea", "faults", "drivers"):
        count = next(p for p in catalog[name]["params"] if p["name"] == "count")
        assert count["minimum"] == 1


def test_failure_is_visible_on_the_wire(client: TestClient):
    client.bridge.result = BridgeResult("denied", error="Access is denied.", took_ms=2)
    body = client.get("/api/readings/events", headers=AUTH).json()
    assert body["outcome"] == "denied" and body["error"] == {"kind": "denied", "detail": "Access is denied."}
    assert body["sections"] == [] and body["count"] is None
    client.bridge.result = BridgeResult("empty", took_ms=2)
    body = client.get("/api/readings/events", headers=AUTH).json()
    assert body["outcome"] == "empty" and body["count"] == 0 and body["sections"][0]["data"] == []


def test_session_cookie_flow(client: TestClient):
    assert client.post("/api/session", json={"token": "wrong"}).status_code == 401
    r = client.post("/api/session", json={"token": TOKEN})
    assert r.status_code == 200 and "sentinel_session" in r.cookies
    assert client.get("/api/readings").status_code == 200  # the cookie now carries it
    client.delete("/api/session")
    assert client.get("/api/readings").status_code == 401


def test_quitting_needs_the_token_itself_and_this_machine(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """The dashboard's cookie opens the dashboard. Switching the machine's tool off is the token's,
    which is to say the launcher's and a local agent's: a browser on any device cannot do it."""
    monkeypatch.setattr(State, "is_local", lambda self, request: True)  # a browser here, not elsewhere
    asked: list[str] = []
    client.app.state.sentinel.on_quit = lambda: asked.append("quit")

    client.post("/api/session", json={"token": TOKEN})  # a browser, properly signed in
    refused = client.post("/api/quit")
    assert refused.status_code == 401 and "token" in refused.json()["detail"]
    assert client.get("/api/readings").status_code == 200  # the same cookie still reads the machine
    assert asked == []

    accepted = client.post("/api/quit", headers=AUTH)
    assert accepted.status_code == 202 and accepted.json() == {"quitting": True}
    assert asked == ["quit"]  # the task runs once the answer is on the wire


def test_quitting_is_refused_from_anywhere_but_this_machine(monkeypatch: pytest.MonkeyPatch):
    asked: list[str] = []
    state = State(bridge=FakeBridge(), token=TOKEN)
    state.on_quit = lambda: asked.append("quit")
    with TestClient(create_app(state)) as elsewhere:  # TestClient's client host is not loopback
        assert elsewhere.post("/api/quit", headers=AUTH).status_code == 401
        assert asked == []
        monkeypatch.setattr(State, "is_local", lambda self, request: True)
        assert elsewhere.post("/api/quit", headers=AUTH).status_code == 202
    assert asked == ["quit"]


def test_a_server_that_cannot_stop_itself_says_so(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """``serve --reload`` owns the process it restarts, so nothing set a quit callback. The route
    answers what is true rather than accepting and never going."""
    monkeypatch.setattr(State, "is_local", lambda self, request: True)
    assert client.app.state.sentinel.on_quit is None
    refused = client.post("/api/quit", headers=AUTH)
    assert refused.status_code == 409 and "reload" in refused.json()["detail"]


def test_health_reading_reports_the_bridge(client: TestClient):
    body = client.get("/api/readings/health", headers=AUTH).json()
    assert body["outcome"] == "ok"
    bridge = body["sections"][0]["data"]["bridge"]
    assert bridge["available"] is True and bridge["powershell"] == "5.1"


def test_mcp_lists_the_catalog_and_calls_a_reading(client: TestClient):
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
    assert client.post("/mcp", json=init, headers=MCP_HEADERS).status_code == 200
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=MCP_HEADERS)
    assert r.status_code == 200, r.text
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert {"health", "events", "record"} <= set(tools)
    assert tools["record"]["inputSchema"]["required"] == ["before"]
    assert "unredacted" in tools["events"]["inputSchema"]["properties"]
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "events", "arguments": {"count": 1}}}, headers=MCP_HEADERS)
    assert r.status_code == 200, r.text
    text = r.json()["result"]["content"][0]["text"]
    assert json.loads(text)["outcome"] == "ok" and "<host>" in text and "TESTBOX" not in text


def test_the_cookie_is_derived_from_the_token_not_the_token(client: TestClient):
    from sentinel.auth import session_value

    r = client.post("/api/session", json={"token": TOKEN})
    cookie = r.cookies["sentinel_session"]
    assert cookie != TOKEN and cookie == session_value(TOKEN)
    # the token itself in the cookie opens nothing; the derived value as a bearer opens nothing
    assert client.get("/api/readings", cookies={"sentinel_session": TOKEN}).status_code == 401
    assert client.get("/api/readings", headers={"Authorization": f"Bearer {cookie}"}).status_code == 401


def test_unknown_parameters_are_refused(client: TestClient):
    r = client.get("/api/readings/events?lvl=3", headers=AUTH)
    assert r.status_code == 422 and "lvl" in r.json()["detail"]
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "events", "arguments": {"lvl": [3]}}}, headers=MCP_HEADERS)
    assert r.json()["result"]["isError"] is True


def test_host_field_is_redacted_even_before_the_machine_names_are_learned():
    """The bridge answers the log but not the identity probe: MachineName still leaves as <host>."""
    bridge = LogBridge(result=BridgeResult("ok", items=[EVENT], took_ms=5), by_marker={"$env:COMPUTERNAME": BridgeResult("unavailable", error="no interop")})
    with TestClient(create_app(State(bridge=bridge, token=TOKEN))) as c:
        rec = c.get("/api/readings/events", headers=AUTH).json()["sections"][0]["data"][0]
        assert rec["MachineName"] == "<host>"


def test_stack_update_and_prompt_are_tools_too(client: TestClient):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=MCP_HEADERS)
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"stack_update", "stack_prompt", "hardware_cpu"} <= names and "hardware.cpu" not in names
