"""The MCP projection: every reading is one tool, on the same token, at ``/mcp``.

The catalog in :data:`sentinel.reading.REGISTRY` is the only source; nothing is
listed here that is not also a route. A tool returns the same envelope a route
does, redacted the same way.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from . import __version__
from .reading import REGISTRY, Spec, take

if TYPE_CHECKING:
    from .app import State

_JSON_TYPES = {"int": "integer", "float": "number", "bool": "boolean", "str": "string", "list[int]": "array"}

INSTRUCTIONS = (
    "A stethoscope for this Windows computer. Each tool takes one reading and returns an envelope: "
    "'outcome' says whether the machine was observed (ok, empty) or not (failed, unavailable, denied, timeout); "
    "'sections' keep raw, derived, invariant and inferred apart; 'method' is the query so you can reproduce it. "
    "Take 'health' first. Take 'events' to find a start (Kernel-Power 41, EventLog 6008), then 'record' with that "
    "timestamp to see what the machine was doing before it froze. A burst, a gap or a correlation is a lead, never a diagnosis."
)


def input_schema(spec: Spec) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for p in spec.params:
        prop: dict[str, Any] = {"type": _JSON_TYPES[p.type], "description": p.description}
        if p.type == "list[int]":
            prop["items"] = {"type": "integer"}
        if p.default is not None:
            prop["default"] = p.default
        if p.choices:
            prop["enum"] = list(p.choices)
        props[p.name] = prop
    props["unredacted"] = {
        "type": "boolean",
        "default": False,
        "description": "Include serial numbers, the computer name, user names and MAC addresses. Only with a reason.",
    }
    return {"type": "object", "properties": props, "required": [p.name for p in spec.params if p.default is None]}


def tools() -> list[types.Tool]:
    return [types.Tool(name=spec.name, description=spec.description, input_schema=input_schema(spec)) for spec in REGISTRY.values()]


def build_mcp(state: "State") -> Starlette:
    async def on_list_tools(_ctx, _params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools())

    async def on_call_tool(_ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        arguments = dict(params.arguments or {})
        unredacted = bool(arguments.pop("unredacted", False))
        if params.name not in REGISTRY:
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"no reading named {params.name!r}")], is_error=True)
        try:
            reading = await take(params.name, state.bridge, arguments)
        except ValueError as exc:
            return types.CallToolResult(content=[types.TextContent(type="text", text=str(exc))], is_error=True)
        body = reading.to_dict()
        if not unredacted:
            body, removed = state.redactor.redact(body)
            body["redacted"] = removed
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(body, indent=1))])

    server = Server("system-sentinel", version=__version__, instructions=INSTRUCTIONS, on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    # The token is the boundary; host-header checks would only refuse the tailnet name a phone uses.
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return server.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True, transport_security=security)
