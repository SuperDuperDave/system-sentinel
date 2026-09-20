"""The MCP projection: every reading is one tool, on the same token, at ``/mcp``.

The catalog in :data:`sentinel.reading.REGISTRY` is the only source of reading tools; nothing is
listed here that is not also a route. A tool returns the same envelope a route does, redacted the
same way. The stack tools are the same projection of the stack routes: an agent chooses evidence
and reads the composed handoff where a person would copy it to the clipboard.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from . import __version__
from .reading import REGISTRY, Spec, take
from .redact import Redactor
from .stack import Duplicate, compose, new_item

if TYPE_CHECKING:
    from .app import State

_JSON_TYPES = {"int": "integer", "float": "number", "bool": "boolean", "str": "string", "list[int]": "array"}

INSTRUCTIONS = (
    "A stethoscope for this Windows computer. Each reading tool takes one reading and returns an envelope: "
    "'outcome' says whether the machine was observed (ok, empty) or not (failed, unavailable, denied, timeout); "
    "'sections' keep raw, derived, invariant and inferred apart; 'method' is the query so you can reproduce it. "
    "Take 'health' first. Take 'events' to find a start (Kernel-Power 41, EventLog 6008), then 'record' with that "
    "timestamp to see what the machine was doing before it froze. A burst, a gap or a correlation is a lead, never a diagnosis. "
    "The stack tools hold the evidence you have chosen; 'compose' returns it as the handoff text, with each item's provenance."
)

_NO_ARGUMENTS: dict[str, Any] = {"type": "object", "properties": {}}


@dataclass(frozen=True)
class StackTool:
    """A tool over the stack rather than the machine. Each one is a route as well."""

    name: str
    description: str
    schema: dict[str, Any]
    call: Callable[["State", dict[str, Any], Redactor | None], Awaitable[Any]]
    carries_machine_data: bool = True
    """Whether what it returns passes through the redaction, and so takes the ``unredacted`` argument."""


async def _stack_list(state: "State", _arguments: dict[str, Any], redactor: Redactor | None) -> Any:
    return _redacted(state.stack.state(), redactor)


async def _stack_add(state: "State", arguments: dict[str, Any], redactor: Redactor | None) -> Any:
    item = await new_item(state.stack, state.bridge, arguments)
    return _redacted(state.stack.add(item).to_dict(), redactor)


async def _stack_remove(state: "State", arguments: dict[str, Any], redactor: Redactor | None) -> Any:
    state.stack.remove(str(arguments.get("id") or ""))
    return _redacted(state.stack.state(), redactor)


async def _stack_clear(state: "State", _arguments: dict[str, Any], redactor: Redactor | None) -> Any:
    state.stack.clear()
    return _redacted(state.stack.state(), redactor)


async def _compose(state: "State", _arguments: dict[str, Any], redactor: Redactor | None) -> Any:
    return compose(state.stack, state.prompts, redactor)["text"]


async def _prompts_list(state: "State", _arguments: dict[str, Any], _redactor: Redactor | None) -> Any:
    return {"prompts": state.prompts.all()}


STACK_TOOLS: dict[str, StackTool] = {
    tool.name: tool
    for tool in (
        StackTool("stack_list", "The evidence currently chosen for handoff, with the prompt it leads with.", _NO_ARGUMENTS, _stack_list),
        StackTool(
            "stack_add",
            "Add evidence to the stack: a reading the tool takes now ('take'), a reading you already hold ('envelope'), "
            "some of its records ('selection' with 'ids'), or a note you wrote. The same reading with the same parameters "
            "and records is refused rather than stacked twice.",
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["reading", "selection", "note"], "default": "reading"},
                    "title": {"type": "string", "description": "Optional; one is derived from the reading otherwise."},
                    "rank": {"type": "integer", "description": "1 first to 5 last in the composed handoff.", "default": 3},
                    "verbosity": {"type": "string", "enum": ["summary", "full"], "default": "full"},
                    "ids": {"type": "array", "items": {"type": "integer"}, "description": "For a selection: the RecordIds to keep."},
                    "note": {"type": "string", "description": "For a note: the text, carried into the handoff verbatim."},
                    "take": {
                        "type": "object",
                        "description": "Read the machine now: the reading's name and its parameters.",
                        "properties": {"name": {"type": "string"}, "params": {"type": "object"}},
                        "required": ["name"],
                    },
                    "envelope": {"type": "object", "description": "A reading you already hold, stored with its own provenance."},
                },
            },
            _stack_add,
        ),
        StackTool("stack_remove", "Remove one item from the stack by its id.", {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}, _stack_remove),
        StackTool("stack_clear", "Remove every item from the stack.", _NO_ARGUMENTS, _stack_clear),
        StackTool("compose", "The handoff as Markdown: the prompt, then the evidence by rank, each with its provenance and outcome.", _NO_ARGUMENTS, _compose),
        StackTool("prompts_list", "The prompt library: the six the tool ships with and any that were added.", _NO_ARGUMENTS, _prompts_list, carries_machine_data=False),
    )
}


def _redacted(payload: Any, redactor: Redactor | None) -> Any:
    if redactor is None:
        return payload
    body, removed = redactor.redact(payload)
    if isinstance(body, dict):
        body["redacted"] = removed
    return body


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


def _with_unredacted(schema: dict[str, Any]) -> dict[str, Any]:
    properties = dict(schema.get("properties") or {})
    properties["unredacted"] = {
        "type": "boolean",
        "default": False,
        "description": "Include serial numbers, the computer name, user names and MAC addresses. Only with a reason.",
    }
    return {**schema, "properties": properties}


def tool_name(reading: str) -> str:
    """A reading's name as a tool: MCP clients allow letters, digits, underscore and hyphen, so the dot in
    ``hardware.cpu`` becomes an underscore. The route keeps the dot; the two are the same reading."""
    return reading.replace(".", "_")


def reading_for(tool: str) -> str | None:
    return next((name for name in REGISTRY if tool_name(name) == tool), None)


def tools() -> list[types.Tool]:
    reading_tools = [types.Tool(name=tool_name(spec.name), description=spec.description, input_schema=input_schema(spec)) for spec in REGISTRY.values()]
    stack_tools = [types.Tool(name=t.name, description=t.description, input_schema=_with_unredacted(t.schema) if t.carries_machine_data else t.schema) for t in STACK_TOOLS.values()]
    return reading_tools + stack_tools


def build_mcp(state: "State") -> Starlette:
    async def on_list_tools(_ctx, _params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools())

    async def on_call_tool(_ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        arguments = dict(params.arguments or {})
        unredacted = bool(arguments.pop("unredacted", False))
        stack_tool = STACK_TOOLS.get(params.name)
        if stack_tool is not None:
            try:
                payload = await stack_tool.call(state, arguments, None if unredacted else state.redactor)
            except Duplicate:
                return types.CallToolResult(content=[types.TextContent(type="text", text="this evidence is already on the stack")], is_error=True)
            except KeyError as exc:
                return types.CallToolResult(content=[types.TextContent(type="text", text=f"nothing on the stack with id {exc}")], is_error=True)
            except ValueError as exc:
                return types.CallToolResult(content=[types.TextContent(type="text", text=str(exc))], is_error=True)
            text = payload if isinstance(payload, str) else json.dumps(payload, indent=1)
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)])
        reading_name = reading_for(params.name)
        if reading_name is None:
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"no reading named {params.name!r}")], is_error=True)
        try:
            reading = await take(reading_name, state.bridge, arguments)
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
    return server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True, transport_security=security)
