"""The agent's surface: what a client is told about each tool, and what comes back when it calls one.

Every tool here is a projection of a route, so most of what this asserts is that the projection is
faithful — the same envelope, the same redaction, the same refusals. The rest is what only the
protocol can carry: what a tool does to the machine, the typed answer beside the text one, the
prompt library, and the two resources.

The surface is exercised directly where the question is about the projection, and over the wire
where the question is about what a client actually receives.
"""

import asyncio
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp import types
from mcp.server.subscriptions import ResourceUpdated, ServerEvent
from mcp.shared.exceptions import MCPError

from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.mcp_server import (
    CATALOG_URI,
    ENVELOPE_SCHEMA,
    HANDOFF_URI,
    ROUTE_TOOLS,
    Surface,
    check_tool_names,
    tool_name,
    tools,
)
from sentinel.paths import captures_dir
from sentinel.reading import REGISTRY
from sentinel.stack import PRESET_PROMPTS, slug
from tests.conftest import FakeBridge, identity_result
from tests.test_stream import serve

TOKEN = "test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MCP_HEADERS = {**AUTH, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

EVENT = {
    "RecordId": 1,
    "Id": 41,
    "ProviderName": "Microsoft-Windows-Kernel-Power",
    "MachineName": "TESTBOX",
    "LevelDisplayName": "Critical",
    "TimeCreated": "2026-09-20T18:04:11.204Z",
    "Message": r"The system has rebooted without cleanly shutting down first. Logged on TESTBOX for tester at C:\Users\tester\x.",
}

READING_TOOLS = {tool_name(name) for name in REGISTRY}


def machine() -> FakeBridge:
    return FakeBridge(result=BridgeResult("ok", items=[EVENT], took_ms=5), by_marker={"$env:COMPUTERNAME": identity_result("TESTBOX", "tester")})


@pytest.fixture
def surface() -> Surface:
    return Surface(State(bridge=machine(), token=TOKEN))


@pytest.fixture
def client():
    with TestClient(create_app(State(bridge=machine(), token=TOKEN))) as c:
        yield c


def call(surface: Surface, name: str, **arguments) -> types.CallToolResult:
    """One tool call, the way the transport makes it. Synchronous like the rest of the suite."""
    return asyncio.run(surface.call_tool(None, types.CallToolRequestParams(name=name, arguments=arguments)))


def prompt(surface: Surface, name: str) -> types.GetPromptResult:
    return asyncio.run(surface.get_prompt(None, types.GetPromptRequestParams(name=name)))


def resource(surface: Surface, uri: str) -> types.ReadResourceResult:
    return asyncio.run(surface.read_resource(None, types.ReadResourceRequestParams(uri=uri)))


def payload(result: types.CallToolResult):
    """What the caller reads, parsed. The text block is the answer; ``structuredContent`` is the same one typed."""
    return json.loads(result.content[0].text)


def rpc(client: TestClient, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": request_id, "method": method, **({"params": params} if params is not None else {})}
    response = client.post("/mcp", json=body, headers=MCP_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


# --- what a client is told before it calls anything ------------------------------------------


def test_every_tool_says_what_it_does_to_the_machine():
    """A client that asks the person to confirm a destructive call should ask about two tools, not
    thirty-two. Nothing here reaches past this computer, so nothing claims an open world."""
    listed = {t.name: t for t in tools()}
    assert READING_TOOLS <= set(listed) and set(ROUTE_TOOLS) <= set(listed)

    for tool in listed.values():
        assert tool.annotations is not None, tool.name
        assert tool.annotations.open_world_hint is False, tool.name

    reads = {name for name, tool in listed.items() if tool.annotations.read_only_hint}
    destroys = {name for name, tool in listed.items() if tool.annotations.destructive_hint}
    assert READING_TOOLS <= reads  # taking a reading asks the machine and changes nothing
    assert {"stack_list", "compose", "prompts_list", "capture_list"} <= reads
    assert destroys == {"stack_remove", "stack_clear"}
    for name in ("stack_add", "stack_update", "stack_prompt", "capture_create"):
        assert listed[name].annotations.read_only_hint is False and listed[name].annotations.destructive_hint is False, name


def test_every_reading_declares_the_same_envelope_and_the_route_tools_declare_none():
    listed = {t.name: t for t in tools()}
    for name in READING_TOOLS:
        assert listed[name].output_schema == ENVELOPE_SCHEMA, name
    assert {"outcome", "sections", "error", "count", "warnings", "redacted"} <= set(ENVELOPE_SCHEMA["properties"])
    assert ENVELOPE_SCHEMA["properties"]["outcome"]["enum"] == ["ok", "empty", "failed", "unavailable", "denied", "timeout"]
    assert ENVELOPE_SCHEMA["properties"]["sections"]["items"]["properties"]["class"]["enum"] == ["raw", "derived", "invariant", "inferred"]
    # The stack and capture tools answer with the route's own shape, which is not an envelope.
    assert all(listed[name].output_schema is None for name in ROUTE_TOOLS)


def test_a_reason_is_asked_for_only_where_it_could_be_needed():
    listed = {t.name: t for t in tools()}
    carrying = listed["events"].input_schema
    assert carrying["then"] == {"required": ["reason"]}
    assert carrying["if"] == {"properties": {"unredacted": {"const": True}}, "required": ["unredacted"]}
    # prompts_list is the person's own text; capture_list has bounded status metadata but no identity fields to unredact.
    assert "unredacted" not in listed["prompts_list"].input_schema["properties"]
    assert "unredacted" not in listed["capture_list"].input_schema["properties"]


def test_two_things_cannot_claim_one_tool_name():
    """``tool_name`` turns a dot into an underscore, so the catalog could grow a second claim on one
    tool name and one reading would quietly answer for the other. The guard runs at import."""
    check_tool_names(REGISTRY, ROUTE_TOOLS)  # what ships

    with pytest.raises(RuntimeError, match="hardware_cpu"):
        check_tool_names(["hardware.cpu", "hardware_cpu"], ())
    with pytest.raises(RuntimeError, match="stack_list"):
        check_tool_names(["stack.list"], ROUTE_TOOLS)


# --- what comes back -------------------------------------------------------------------------


def test_the_typed_answer_is_the_text_answer(client: TestClient):
    """``structuredContent`` is there so a client can branch on ``outcome`` and on a section's
    ``class`` as fields. It must be the same answer, not a second one."""
    result = rpc(client, "tools/call", {"name": "events", "arguments": {"count": 1}})["result"]
    text, typed = result["content"][0]["text"], result["structuredContent"]
    assert typed == json.loads(text)
    assert typed["outcome"] == "ok" and typed["sections"][0]["class"] == "raw"
    assert set(ENVELOPE_SCHEMA["required"]) <= set(typed)
    assert typed["sections"][0]["data"][0]["MachineName"] == "<host>"  # redacted like the route

    refused = rpc(client, "tools/call", {"name": "events", "arguments": {"lvl": [3]}}, 2)["result"]
    assert refused["isError"] is True and "structuredContent" not in refused


def test_a_reading_that_did_not_observe_the_machine_says_so_in_the_typed_answer(client: TestClient):
    client.app.state.sentinel.bridge.result = BridgeResult("denied", error="Access is denied.", took_ms=2)
    typed = rpc(client, "tools/call", {"name": "events"})["result"]["structuredContent"]
    assert typed["outcome"] == "denied" and typed["error"] == {"kind": "denied", "detail": "Access is denied."}
    assert typed["sections"] == [] and typed["count"] is None  # a collection failure, not no findings


def test_unredacted_without_a_reason_is_refused_and_with_one_is_recorded(surface: Surface):
    refused = call(surface, "events", count=1, unredacted=True)
    assert refused.is_error is True and "reason" in refused.content[0].text

    given = call(surface, "events", count=1, unredacted=True, reason="building an RMA case")
    body = payload(given)
    assert body["sections"][0]["data"][0]["MachineName"] == "TESTBOX"
    assert body["warnings"] == ["unredacted, because: building an RMA case"]
    assert given.structured_content == body

    # Without the flag the reason is meaningless, and it is never mistaken for a reading's parameter.
    quiet = call(surface, "events", count=1, reason="curiosity")
    assert payload(quiet)["warnings"] == [] and payload(quiet)["sections"][0]["data"][0]["MachineName"] == "<host>"


def test_the_handoff_reads_as_markdown_and_still_answers_as_data(surface: Surface):
    """``compose`` is the one tool whose text is not JSON: a caller pastes it. The typed answer
    beside it is the route's own object, which is where the note about an unredacted view lands."""
    composed = call(surface, "compose", unredacted=True, reason="pasting it into the RMA form")
    assert composed.content[0].text.startswith("# System Sentinel handoff")
    assert composed.structured_content["text"] == composed.content[0].text
    assert composed.structured_content["items"] == 0
    assert composed.structured_content["warnings"] == ["unredacted, because: pasting it into the RMA form"]


# --- the prompt library and the two resources --------------------------------------------------


def test_the_prompt_library_is_offered_as_prompts(surface: Surface):
    listed = asyncio.run(surface.list_prompts())
    assert [p.title for p in listed.prompts] == [p["name"] for p in PRESET_PROMPTS]
    assert [p.name for p in listed.prompts] == [slug(p["name"]) for p in PRESET_PROMPTS]

    first = PRESET_PROMPTS[0]
    got = prompt(surface, slug(first["name"]))
    assert got.description == first["description"]
    assert got.messages[0].role == "user" and got.messages[0].content.text == first["content"]

    with pytest.raises(MCPError, match="no prompt"):
        prompt(surface, "nothing-like-that")


def test_the_catalog_and_the_handoff_are_resources(surface: Surface):
    listed = asyncio.run(surface.list_resources())
    assert {str(r.uri) for r in listed.resources} == {CATALOG_URI, HANDOFF_URI}

    catalog = resource(surface, CATALOG_URI)
    assert catalog.contents[0].mime_type == "application/json"
    assert {r["name"] for r in json.loads(catalog.contents[0].text)["readings"]} == set(REGISTRY)

    call(surface, "stack_add", kind="note", note="it froze while idle")
    handoff = resource(surface, HANDOFF_URI)
    assert handoff.contents[0].mime_type == "text/markdown"
    assert handoff.contents[0].text.startswith("# System Sentinel handoff")
    assert "it froze while idle" in handoff.contents[0].text

    with pytest.raises(MCPError, match="no resource"):
        resource(surface, "sentinel://nothing")


def test_a_stack_change_publishes_the_handoff(surface: Surface):
    """What a subscribed client would be told. Nothing is subscribed on this transport yet — see
    ``build_mcp`` — so what is asserted here is the half the surface owns: the stack tools that
    change the handoff publish it, and the ones that only read it stay quiet."""
    published: list[ServerEvent] = []
    surface.bus.subscribe(published.append)

    added = payload(call(surface, "stack_add", kind="note", note="one"))
    assert published == [ResourceUpdated(HANDOFF_URI)]

    call(surface, "stack_list")
    call(surface, "compose")
    assert len(published) == 1  # reading the stack changes nothing

    call(surface, "stack_update", id=added["id"], rank=1)
    call(surface, "stack_remove", id=added["id"])
    assert published == [ResourceUpdated(HANDOFF_URI)] * 3

    refused = call(surface, "stack_remove", id="nothing-like-that")
    assert refused.is_error is True and len(published) == 3  # a refusal is not a change


def test_a_dashboard_edit_notifies_a_subscribed_agent():
    """The MCP listener and dashboard routes share one bus in the running server."""
    app = create_app(State(bridge=machine(), token=TOKEN))
    published: list[ServerEvent] = []
    app.state.mcp_surface.bus.subscribe(published.append)
    server, thread, port = serve(app)
    body = {
        "jsonrpc": "2.0",
        "id": 17,
        "method": "subscriptions/listen",
        "params": {
            "notifications": {"resourceSubscriptions": [HANDOFF_URI]},
            "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}},
        },
    }
    headers = {**MCP_HEADERS, "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "subscriptions/listen"}
    try:
        with httpx.stream("POST", f"http://127.0.0.1:{port}/mcp", headers=headers, json=body, timeout=10) as response:
            assert response.status_code == 200, response.text
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = response.iter_lines()
            acknowledged = json.loads(next(line.removeprefix("data: ") for line in lines if line.startswith("data: ")))
            assert acknowledged["method"] == "notifications/subscriptions/acknowledged"
            assert acknowledged["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == 17

            refused = httpx.delete(f"http://127.0.0.1:{port}/api/stack/items/missing", headers=AUTH, timeout=10)
            assert refused.status_code == 404 and published == []

            added = httpx.post(f"http://127.0.0.1:{port}/api/stack/items", headers=AUTH, json={"kind": "note", "note": "one"}, timeout=10)
            assert added.status_code == 201, added.text
            updated = json.loads(next(line.removeprefix("data: ") for line in lines if line.startswith("data: ")))
            assert updated["method"] == "notifications/resources/updated"
            assert updated["params"]["uri"] == HANDOFF_URI
            assert updated["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == 17
            assert published == [ResourceUpdated(HANDOFF_URI)]

            changed = httpx.patch(f"http://127.0.0.1:{port}/api/prompts/emergency-triage", headers=AUTH, json={"content": "Updated triage"}, timeout=10)
            assert changed.status_code == 200, changed.text
            prompt_update = json.loads(next(line.removeprefix("data: ") for line in lines if line.startswith("data: ")))
            assert prompt_update["method"] == "notifications/resources/updated"
            assert prompt_update["params"]["uri"] == HANDOFF_URI
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert not thread.is_alive()


# --- captures --------------------------------------------------------------------------------


def test_a_capture_is_a_tool_as_well_as_a_route(surface: Surface):
    """An agent can take the most complete artifact this tool makes without the dashboard. The ZIP
    stays on the machine; what comes back is its name and its manifest."""
    assert payload(call(surface, "capture_list")) == {"captures": []}

    made = payload(call(surface, "capture_create"))
    name, manifest = made["capture"], made["manifest"]
    assert manifest["readings"] == len(REGISTRY) and manifest["unredacted"] is False
    assert {m["path"] for m in manifest["members"]} >= {"readings/health.json", "stack.json", "composed.md"}

    on_disk = captures_dir() / name
    with zipfile.ZipFile(on_disk) as archive:
        assert json.loads(archive.read("manifest.json")) == manifest
        assert "TESTBOX" not in archive.read("readings/events.json").decode("utf-8")

    listed = payload(call(surface, "capture_list"))["captures"]
    assert [c["name"] for c in listed] == [name]
    assert listed[0]["manifest"]["unredacted"] is False
    assert listed[0]["manifest"]["readings"] == manifest["readings"]
    assert sum(listed[0]["manifest"]["outcomes"].values()) == manifest["readings"]


def test_an_unredacted_capture_still_needs_a_reason(surface: Surface):
    assert (call(surface, "capture_create", unredacted=True)).is_error is True
    made = payload(call(surface, "capture_create", unredacted=True, reason="sending it to the board vendor"))
    assert made["manifest"]["unredacted"] is True
    assert made["manifest"]["reason"] == "sending it to the board vendor"
    assert made["warnings"] == ["unredacted, because: sending it to the board vendor"]
    with zipfile.ZipFile(captures_dir() / made["capture"]) as archive:
        assert json.loads(archive.read("manifest.json"))["reason"] == "sending it to the board vendor"
