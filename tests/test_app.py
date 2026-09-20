"""The boundary: the token guards every /api and /mcp route; readings arrive redacted unless asked by name."""

import pytest
from fastapi.testclient import TestClient

from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from tests.conftest import FakeBridge, identity_result

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
    bridge = FakeBridge(
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
    assert [p["name"] for p in events["params"]] == ["log", "levels", "count"]
    assert events["private"]


def test_reading_arrives_redacted_by_default(client: TestClient):
    body = client.get("/api/readings/events?count=1", headers=AUTH).json()
    assert body["outcome"] == "ok" and body["count"] == 1
    rec = body["sections"][0]["data"][0]
    assert rec["MachineName"] == "<host>"
    assert "TESTBOX" not in rec["Message"] and "tester" not in rec["Message"]
    assert r"C:\Users\<user>\x" in rec["Message"]
    assert body["redacted"] == ["host", "user"]
    assert body["params"] == {"log": "System", "levels": [1, 2], "count": 1}
    assert body["method"]["kind"] == "powershell" and "Get-WinEvent" in body["method"]["query"]


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
    assert '"outcome": "ok"' in text and "<host>" in text and "TESTBOX" not in text
