"""The MCP projection: every reading is one tool, on the same token, at ``/mcp``.

The catalog in :data:`sentinel.reading.REGISTRY` is the only source of reading tools; nothing is
listed here that is not also a route. A tool returns the same envelope a route does, redacted the
same way. The stack and capture tools are the same projection of the stack and capture routes: an
agent chooses evidence, reads the composed handoff and takes a capture where a person would use
the dashboard.

The surface an agent meets is the whole protocol, not only tools. Each tool says what it does to
the machine (``readOnlyHint``, ``destructiveHint``) so a client that confirms destructive calls
stops confirming the harmless ones; every reading declares one shared ``outputSchema`` and answers
with ``structuredContent`` beside its text, so a client branches on ``outcome`` and ``class`` as
typed fields instead of parsing a string; the prompt library is offered as prompts; and the
catalog and the composed handoff are resources, so a client can hold the handoff open beside the
work instead of calling a tool for it. :func:`build_mcp` wires handoff changes to subscribed clients.

:class:`Surface` holds every method, over one ``State``; :func:`build_mcp` wires it to the
transport. Nothing in the surface knows about JSON-RPC.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler, ResourceUpdated
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from starlette.applications import Starlette

from . import __version__, capture, readings  # noqa: F401  (readings registers the catalog)
from .reading import REGISTRY, Spec
from .redact import Redactor
from .serialization import json_safe_integers
from .stack import Duplicate, StoreUnavailable, compose, index_entry, index_state, new_item

if TYPE_CHECKING:
    from .app import State

_JSON_TYPES = {"int": "integer", "float": "number", "bool": "boolean", "str": "string", "list[int]": "array"}
logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "A stethoscope for this Windows computer. Each reading tool takes one reading and returns an envelope: "
    "'outcome' says whether the machine was observed (ok, empty) or not (failed, unavailable, denied, timeout); "
    "'unavailable' with error.kind 'busy' means Sentinel's bridge was busy; inspect the detail and retry later. "
    "'sections' keep raw, derived, invariant and inferred apart; 'method' is the query so you can reproduce it. "
    "Take 'health' first, then 'crash': the last unplanned stops, each with its bug check, its dump and the machine's "
    "last System record before the next start, or 'crash' with a 'moment' when the person names a time. "
    "That last record may be later than the estimated stop, so compare timestamps. Then 'record' before a stop's started_at, "
    "'faults' for what went wrong while it kept running, 'storms' for System WHEA report traffic, "
    "'whea' for a bounded newest-record preview across both WHEA logs, 'whea_window' for one source's exact filing-time window in either direction, and 'whea_record' for one exact retained report's raw fields and decoded detail; 'signals' last. "
    "A burst, a gap or a correlation is a lead, never a diagnosis. "
    "Stack list and change tools return a provenance index; 'stack_item' returns one complete stored item, "
    "and 'compose' returns the handoff at each item's chosen verbosity, with a structured prompt status and same-snapshot Stack index. "
    "The catalog and that handoff are also resources: sentinel://catalog and sentinel://handoff."
)

CATALOG_URI = "sentinel://catalog"
HANDOFF_URI = "sentinel://handoff"

_NO_ARGUMENTS: dict[str, Any] = {"type": "object", "properties": {}}

# What a tool does to the machine. ``openWorldHint`` is false on every one of them: this tool reads
# the computer it runs on and writes files on it, and reaches nothing beyond it. ``destructiveHint``
# is stated rather than left out because its default is true — saying "this one only adds" is what
# stops a client asking the person to confirm twenty-two harmless calls.
ANNOTATIONS: dict[str, types.ToolAnnotations] = {
    "reads": types.ToolAnnotations(read_only_hint=True, open_world_hint=False),
    "changes": types.ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
    "destroys": types.ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False),
}
READS = ANNOTATIONS["reads"]

NEEDS_REASON = (
    "an unredacted answer needs a 'reason' beside 'unredacted': it carries serial numbers, the computer name, "
    "user names and MAC addresses, and the reason is recorded in the answer's warnings so whoever reads it later "
    "knows why the real values were taken"
)

ENVELOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "The reading envelope: what was asked, whether the machine was observed, and the evidence kept apart by class.",
    "properties": {
        "reading": {"type": "string", "description": "The reading's name in the catalog."},
        "sentinel_version": {"type": "string", "description": "The System Sentinel version that produced this envelope. Older stored envelopes may omit it."},
        "params": {"type": "object", "description": "What was asked for, after the catalog's defaults and coercion."},
        "asked_at": {"type": "string", "description": "Stamped when the reading was composed near its answer, UTC by this computer's clock; not when collection began."},
        "took_ms": {"type": "integer", "description": "Milliseconds from Sentinel accepting the reading to its completed evidence, including Sentinel's own waiting. Excludes redaction and response encoding."},
        "outcome": {
            "type": "string",
            "enum": ["ok", "empty", "failed", "unavailable", "denied", "timeout"],
            "description": "'ok' and 'empty' mean the machine was observed and there were findings or none; the other four mean it was not observed, and they are four different reasons.",
        },
        "method": {"type": "object", "description": "How it was taken, so the evidence can be reproduced by hand."},
        "count": {"type": ["integer", "null"], "description": "Records in the reading, where it counts records. Null is not zero."},
        "sections": {
            "type": "array",
            "description": "The evidence. A section's class says what kind of claim it is.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "class": {
                        "type": "string",
                        "enum": ["raw", "derived", "invariant", "inferred"],
                        "description": "Raw is what Windows said; invariant is what does not change; derived and inferred were computed here and carry a basis.",
                    },
                    "basis": {"type": "string", "description": "For derived and inferred sections: the inputs and the rule, in one sentence."},
                    "data": {"description": "The section's payload; the shape is the reading's own."},
                },
                "required": ["name", "class", "data"],
            },
        },
        "error": {"type": ["object", "null"], "description": "Why the machine was not observed. Null when it was."},
        "warnings": {"type": "array", "items": {"type": "string"}, "description": "What the tool noticed and did not let stop the reading."},
        "redacted": {"type": "array", "items": {"type": "string"}, "description": "What the redaction removed, by name."},
    },
    "required": ["reading", "asked_at", "took_ms", "outcome", "method", "sections", "warnings", "redacted"],
}


@dataclass(frozen=True)
class Answer:
    """A tool's answer where the text a caller reads and the data a caller branches on differ.

    ``compose`` is the case: the text is the handoff Markdown, and the data is the route's object
    around it. Everything else answers with one payload and needs no pair.
    """

    text: str
    data: dict[str, Any] | None = None


@dataclass(frozen=True)
class PreparedChange:
    """Async validation is done; the saved edit and its notice must now run together."""

    save: Callable[[], Any]
    answer: Callable[[Any], Any]


@dataclass(frozen=True)
class RouteTool:
    """A tool over a route rather than over the machine. Each one is a route as well."""

    name: str
    description: str
    schema: dict[str, Any]
    call: Callable[[State, dict[str, Any], Redactor | None], Awaitable[Any]]
    effect: Literal["reads", "changes", "destroys"] = "reads"
    """What this tool does to the machine: ``reads``, ``changes`` or ``destroys``. :data:`ANNOTATIONS` says it in the protocol's words."""
    carries_machine_data: bool = True
    """Whether what it returns passes through the redaction, and so takes ``unredacted``."""
    updates: str | None = None
    """The resource this tool changes, published after the saved edit even if its caller leaves."""


async def _stack_list(state: State, _arguments: dict[str, Any], redactor: Redactor | None) -> Any:
    return await anyio.to_thread.run_sync(lambda: _redacted(index_state(state.stack.state()), redactor))


async def _stack_item(state: State, arguments: dict[str, Any], redactor: Redactor | None) -> Any:
    return await anyio.to_thread.run_sync(lambda: _redacted(state.stack.item(str(arguments.get("id") or "")), redactor))


async def _stack_add(state: State, arguments: dict[str, Any], redactor: Redactor | None) -> Any:
    item = await new_item(state.stack, state.bridge, arguments, reader=state.readings.take)
    return PreparedChange(lambda: state.stack.add(item), lambda saved: _redacted(index_entry(saved.to_dict()), redactor))


async def _stack_remove(state: State, arguments: dict[str, Any], redactor: Redactor | None) -> Any:
    return PreparedChange(lambda: state.stack.remove(str(arguments.get("id") or "")), lambda saved: _redacted(index_state(saved), redactor))


async def _stack_clear(state: State, _arguments: dict[str, Any], redactor: Redactor | None) -> Any:
    return PreparedChange(state.stack.clear, lambda saved: _redacted(index_state(saved), redactor))


async def _compose(state: State, _arguments: dict[str, Any], redactor: Redactor | None) -> Any:
    composed = await anyio.to_thread.run_sync(compose, state.stack, state.prompts, redactor)
    return Answer(text=composed["text"], data=composed)


async def _prompts_list(state: State, _arguments: dict[str, Any], _redactor: Redactor | None) -> Any:
    return {"prompts": await anyio.to_thread.run_sync(state.prompts.all)}


async def _stack_update(state: State, arguments: dict[str, Any], redactor: Redactor | None) -> Any:
    return PreparedChange(
        save=lambda: state.stack.update(
            str(arguments.get("id") or ""), rank=arguments.get("rank"), verbosity=arguments.get("verbosity"), title=arguments.get("title")
        ),
        answer=lambda saved: _redacted(index_entry(saved), redactor),
    )


async def _stack_prompt(state: State, arguments: dict[str, Any], redactor: Redactor | None) -> Any:
    return PreparedChange(
        save=lambda: state.stack.choose(
            prompt_id=arguments.get("prompt_id"), system_prompt=arguments.get("system_prompt"), set_prompt="prompt_id" in arguments
        ),
        answer=lambda saved: _redacted(index_state(saved), redactor),
    )


async def _capture_create(state: State, arguments: dict[str, Any], redactor: Redactor | None) -> Any:
    """Take readings that need no exact selection into one ZIP and say where it landed.

    The route hands back the file itself; a tool cannot, so it answers with the capture's name and
    its manifest — which already says whether it was written unredacted and what was removed — and
    the file stays on the machine for a person to send.
    """
    made = await capture.create(state.bridge, state.stack, state.prompts, redactor, reason=arguments.get("reason"), reader=state.readings.take)
    return {"capture": made.name, "manifest": made.manifest}


async def _capture_list(_state: State, _arguments: dict[str, Any], _redactor: Redactor | None) -> Any:
    return {"captures": await anyio.to_thread.run_sync(capture.listing)}


STACK_TOOLS: dict[str, RouteTool] = {
    tool.name: tool
    for tool in (
        RouteTool("stack_list", "A compact provenance index of evidence chosen for handoff, with its prompt. Use stack_item for one complete stored item.", _NO_ARGUMENTS, _stack_list),
        RouteTool("stack_item", "One complete saved Stack item by id, including its reading; redacted unless explicitly requested with a reason.",
                  {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}, _stack_item),
        RouteTool(
            "stack_add",
            "Add evidence to the stack: a reading the tool takes now ('take'), a reading you already hold ('envelope'), "
            "some of its records or signals ('selection' with 'ids'), or a note you wrote. Re-adding the same observed "
            "envelope and selected ids is refused; a new take is a new observation. After an uncertain add, inspect the Stack. "
            "If the item appears in its index, the add succeeded; do not re-add it. If absent, retry with an original held "
            "envelope when possible; retrying a 'take' makes a new observation. A redacted saved reading is not a retry key.",
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["reading", "selection", "note"], "default": "reading"},
                    "title": {"type": "string", "description": "Optional; one is derived from the reading otherwise."},
                    "rank": {"type": "integer", "description": "1 first to 5 last in the composed handoff.", "default": 3},
                    "verbosity": {"type": "string", "enum": ["summary", "full"], "description": "Defaults to a derived summary for whole crash, faults, storms and whea_reports readings, or a bounded row summary for events, record and whea over 100 rows; full for selections and other readings. Events and record summaries keep source and retention reach with citable row IDs. A requested summary that uses full stored sections says so; a compact projection may still carry every interpreted entry up to its reading limit. Can be changed later."},
                    "ids": {"type": "array", "items": {"type": ["integer", "string"]}, "description": "For a selection: RecordIds unique within the reading, Log:RecordId for an exact record across logs, or signal ids for a signals reading. Each id must be present in the reading."},
                    "note": {"type": "string", "description": "For a note: the text, carried into the handoff verbatim."},
                    "take": {
                        "type": "object",
                        "description": "Read the machine now: the reading's name and its parameters.",
                        "properties": {"name": {"type": "string"}, "params": {"type": "object"}},
                        "required": ["name"],
                    },
                    "envelope": {"type": "object", "description": "A reading you already hold, stored as supplied and marked held. Sentinel checks its shape, not its content."},
                },
            },
            _stack_add,
            effect="changes",
            updates=HANDOFF_URI,
        ),
        RouteTool(
            "stack_remove",
            "Remove one item from the stack by its id.",
            {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            _stack_remove,
            effect="destroys",
            updates=HANDOFF_URI,
        ),
        RouteTool("stack_clear", "Remove every item from the stack.", _NO_ARGUMENTS, _stack_clear, effect="destroys", updates=HANDOFF_URI),
        RouteTool(
            "stack_update",
            "Change one item's place in the handoff (rank 1 first to 5 last), how much of it is rendered (verbosity summary or full), or its title.",
            {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "rank": {"type": "integer", "minimum": 1, "maximum": 5},
                    "verbosity": {"type": "string", "enum": ["summary", "full"]},
                    "title": {"type": "string"},
                },
                "required": ["id"],
            },
            _stack_update,
            effect="changes",
            updates=HANDOFF_URI,
        ),
        RouteTool(
            "stack_prompt",
            "Choose the prompt that leads the handoff (by id from prompts_list; null for none) or whether one leads it at all.",
            {"type": "object", "properties": {"prompt_id": {"type": ["string", "null"]}, "system_prompt": {"type": "boolean"}}},
            _stack_prompt,
            effect="changes",
            updates=HANDOFF_URI,
        ),
        RouteTool("compose", "The handoff as Markdown: the prompt, then the evidence by rank, each with its provenance and outcome.", _NO_ARGUMENTS, _compose),
        RouteTool("prompts_list", "The prompt library: the six the tool ships with and any that were added.", _NO_ARGUMENTS, _prompts_list, carries_machine_data=False),
    )
}

CAPTURE_TOOLS: dict[str, RouteTool] = {
    tool.name: tool
    for tool in (
        RouteTool(
            "capture_create",
            "Take readings that need no exact selection and write them with saved Stack context and a manifest into "
            "one ZIP in the captures directory. The manifest names readings omitted because they need a selection "
            "and saved context unavailable at capture time. "
            "Readings are taken in turn, so their costs add up. Nothing is sent anywhere.",
            _NO_ARGUMENTS,
            _capture_create,
            effect="changes",
        ),
        # Only bounded outcome counts and privacy state leave the manifest; there are no identity
        # fields to reveal through an unredacted variant of this tool.
        RouteTool("capture_list", "The captures on disk, newest first, with each readable manifest's redaction state, reading outcomes and omitted count. Captures are never deleted by the tool.", _NO_ARGUMENTS, _capture_list, carries_machine_data=False),
    )
}

ROUTE_TOOLS: dict[str, RouteTool] = {**STACK_TOOLS, **CAPTURE_TOOLS}

RESOURCES: tuple[types.Resource, ...] = (
    types.Resource(
        uri=CATALOG_URI,
        name="catalog",
        title="The catalog",
        description="Every reading this machine offers: name, description, classes, parameters and what the redaction removes. The same answer as GET /api/readings.",
        mime_type="application/json",
    ),
    types.Resource(
        uri=HANDOFF_URI,
        name="handoff",
        title="The composed handoff",
        description="The evidence on the stack as Markdown, in rank order, each item with its provenance. It changes whenever the stack does, so read it again after a stack tool. Always redacted: a resource takes no arguments, so there is no way to ask it for the real values.",
        mime_type="text/markdown",
    ),
)


def _redacted(payload: Any, redactor: Redactor | None) -> Any:
    return payload if redactor is None else redactor.attach(payload)


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
        if p.minimum is not None:
            prop["minimum"] = p.minimum
        if p.maximum is not None:
            prop["maximum"] = p.maximum
        props[p.name] = prop
    return _with_unredacted({"type": "object", "properties": props, "required": [p.name for p in spec.params if p.default is None]})


def _with_unredacted(schema: dict[str, Any]) -> dict[str, Any]:
    """Add the two arguments that go together: the real values, and why they were asked for.

    ``reason`` is required only when ``unredacted`` is true, said as JSON Schema so a client can
    see the rule, and enforced in :meth:`Surface.call_tool` so a client that cannot read the rule
    is still refused. A required field rather than an elicitation: elicitation is a capability a
    client may not have, and this is certain.
    """
    properties = dict(schema.get("properties") or {})
    properties["unredacted"] = {
        "type": "boolean",
        "default": False,
        "description": "Include serial numbers, the computer name, user names and MAC addresses. Only with a reason.",
    }
    properties["reason"] = {
        "type": "string",
        "description": "Why the real values are needed. Required with 'unredacted', and recorded in the answer's warnings.",
    }
    return {
        **schema,
        "properties": properties,
        "if": {"properties": {"unredacted": {"const": True}}, "required": ["unredacted"]},
        "then": {"required": ["reason"]},
    }


def tool_name(reading: str) -> str:
    """A reading's name as a tool: MCP clients allow letters, digits, underscore and hyphen, so the dot in
    ``hardware.cpu`` becomes an underscore. The route keeps the dot; the two are the same reading."""
    return reading.replace(".", "_")


def reading_for(tool: str) -> str | None:
    return next((name for name in REGISTRY if tool_name(name) == tool), None)


def check_tool_names(catalog: Iterable[str], route_tools: Iterable[str]) -> None:
    """One tool name per reading, and no reading hidden behind a route tool.

    ``tool_name`` is not injective on its own: a reading named ``hardware_cpu`` beside
    ``hardware.cpu`` would claim one tool name, and ``reading_for`` would hand every call to
    whichever came first in the catalog — a tool that answers a different question than the one
    asked, with a clean outcome. Route tools are checked in the same namespace because a call is
    matched against them first. Run when this module is imported, so it cannot ship.
    """
    claimed: dict[str, str] = {name: name for name in route_tools}
    for name in catalog:
        tool = tool_name(name)
        if tool in claimed:
            raise RuntimeError(f"two things claim the MCP tool name {tool!r}: {claimed[tool]!r} and {name!r}")
        claimed[tool] = name


check_tool_names(REGISTRY, ROUTE_TOOLS)


def tools() -> list[types.Tool]:
    reading_tools = [
        types.Tool(
            name=tool_name(spec.name),
            description=spec.description,
            input_schema=input_schema(spec),
            output_schema=ENVELOPE_SCHEMA,
            annotations=READS,
        )
        for spec in REGISTRY.values()
    ]
    route_tools = [
        types.Tool(
            name=t.name,
            description=t.description,
            input_schema=_with_unredacted(t.schema) if t.carries_machine_data else t.schema,
            annotations=ANNOTATIONS[t.effect],
        )
        for t in ROUTE_TOOLS.values()
    ]
    return reading_tools + route_tools


def _answer(payload: Any) -> types.CallToolResult:
    """One answer, twice: the text a client without structured output reads, and the same fact typed.

    A client that understands ``structuredContent`` branches on ``outcome`` and on a section's
    ``class`` as fields; one that does not reads exactly what it read before.
    """
    if isinstance(payload, Answer):
        text, data = payload.text, json_safe_integers(payload.data)
    else:
        # Structured output is a JSON object before 2026-07-28, so anything else travels as text alone.
        payload = json_safe_integers(payload)
        text, data = json.dumps(payload, separators=(",", ":")), payload if isinstance(payload, dict) else None
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], structured_content=data)


def _refused(detail: str) -> types.CallToolResult:
    """A refusal the model can see and correct, rather than a protocol error it cannot."""
    return types.CallToolResult(content=[types.TextContent(type="text", text=detail)], is_error=True)


def _warned(payload: Any, reason: str) -> Any:
    """Record why the real values were asked for, in the answer itself.

    The envelope already has a ``warnings`` list for what the tool noticed; an unredacted view is
    exactly that. An answer that is a text (the composed handoff) carries the note on its data.
    """
    if not reason:
        return payload
    note = f"unredacted, because: {reason}"
    if isinstance(payload, Answer):
        data = {**(payload.data or {})}
        data["warnings"] = [*data.get("warnings", []), note]
        return Answer(text=payload.text, data=data)
    if isinstance(payload, dict):
        payload["warnings"] = [*payload.get("warnings", []), note]
    return payload


class Surface:
    """Every method an agent can reach, over one :class:`sentinel.app.State`.

    Kept apart from the transport so what an agent is offered can be read, and tested, without a
    wire: :func:`build_mcp` hands these to the SDK's ``Server``. The bus is the one place a change
    made here is announced; what is listening on it is the transport's business, not this class's.
    """

    def __init__(self, state: State):
        self.state = state
        self.bus = InMemorySubscriptionBus()

    async def handoff_changed(self) -> None:
        """Tell subscribed agents to refetch after a dashboard edit to the handoff."""
        await self.bus.publish(ResourceUpdated(HANDOFF_URI))

    def _commit(self, change: PreparedChange, uri: str) -> Any:
        """Keep a saved edit and its notice together even if its caller disconnects."""
        saved = change.save()
        try:
            anyio.from_thread.run(self.bus.publish, ResourceUpdated(uri))
        except Exception:
            # The edit landed. A notification failure must not invite a duplicate retry.
            logger.exception("saved Stack change could not notify subscribers")
        return change.answer(saved)

    async def list_tools(self, _ctx: Any = None, _params: Any = None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools())

    async def call_tool(self, _ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        arguments = dict(params.arguments or {})
        unredacted = bool(arguments.pop("unredacted", False))
        # Popped whether or not it was asked for: a reading refuses a parameter it does not have,
        # and 'reason' belongs to this boundary rather than to the query. It is recorded only on an
        # answer that is actually unredacted; on a redacted one it would be a note about nothing.
        reason = str(arguments.pop("reason", "") or "").strip()
        if not unredacted:
            reason = ""
        elif not reason:
            return _refused(NEEDS_REASON)
        tool = ROUTE_TOOLS.get(params.name)
        if tool is not None:
            redactor = None if unredacted else await self.state.redaction()
            if params.name == "capture_create" and reason:
                # The capture outlives this tool result; keep the stated reason inside its ZIP.
                arguments["reason"] = reason
            try:
                payload = await tool.call(self.state, arguments, redactor)
                if tool.updates:
                    if not isinstance(payload, PreparedChange):
                        raise RuntimeError(f"{tool.name} did not prepare its saved change")
                    payload = await anyio.to_thread.run_sync(self._commit, payload, tool.updates)
            except Duplicate as exc:
                return _refused(f"this observation ({exc.asked_at or 'time unknown'}) is already on the stack as item {exc}")
            except StoreUnavailable as exc:
                return _refused(f"{exc.reason}: {exc}")
            except KeyError as exc:
                return _refused(f"nothing on the stack with id {exc}")
            except ValueError as exc:
                return _refused(str(exc))
            return _answer(_warned(payload, reason))

        reading_name = reading_for(params.name)
        if reading_name is None:
            return _refused(f"no reading named {params.name!r}")
        try:
            reading = await self.state.readings.take(reading_name, arguments)
        except ValueError as exc:
            return _refused(str(exc))
        policy = None if unredacted else await self.state.redaction()
        body = await asyncio.to_thread(lambda: reading.to_dict() if policy is None else policy.attach(reading.to_dict()))
        return _answer(_warned(body, reason))

    async def list_prompts(self, _ctx: Any = None, _params: Any = None) -> types.ListPromptsResult:
        """The prompt library, as prompts. The id is the name a client calls back with; the person's
        own title and description are what they see."""
        try:
            prompts = await anyio.to_thread.run_sync(self.state.prompts.all)
        except StoreUnavailable as exc:
            raise MCPError(types.INTERNAL_ERROR, f"{exc.reason}: {exc}") from exc
        return types.ListPromptsResult(
            prompts=[types.Prompt(name=p["id"], title=p.get("name"), description=p.get("description") or None) for p in prompts]
        )

    async def get_prompt(self, _ctx: Any, params: types.GetPromptRequestParams) -> types.GetPromptResult:
        try:
            prompt = await anyio.to_thread.run_sync(self.state.prompts.get, params.name)
        except StoreUnavailable as exc:
            raise MCPError(types.INTERNAL_ERROR, f"{exc.reason}: {exc}") from exc
        if prompt is None:
            raise MCPError(types.INVALID_PARAMS, f"no prompt {params.name!r}")
        return types.GetPromptResult(
            description=prompt.get("description") or None,
            messages=[types.PromptMessage(role="user", content=types.TextContent(type="text", text=prompt.get("content") or ""))],
        )

    async def list_resources(self, _ctx: Any = None, _params: Any = None) -> types.ListResourcesResult:
        return types.ListResourcesResult(resources=list(RESOURCES))

    async def read_resource(self, _ctx: Any, params: types.ReadResourceRequestParams) -> types.ReadResourceResult:
        uri = str(params.uri)
        if uri == CATALOG_URI:
            body = json.dumps({"readings": [spec.to_dict() for spec in REGISTRY.values()], "version": __version__}, indent=1)
            return _resource(uri, "application/json", body)
        if uri == HANDOFF_URI:
            try:
                policy = await self.state.redaction()
                text = (await asyncio.to_thread(compose, self.state.stack, self.state.prompts, policy))["text"]
            except StoreUnavailable as exc:
                raise MCPError(types.INTERNAL_ERROR, f"{exc.reason}: {exc}") from exc
            return _resource(uri, "text/markdown", text)
        raise MCPError(types.INVALID_PARAMS, f"no resource at {uri!r}")


def _resource(uri: str, mime_type: str, text: str) -> types.ReadResourceResult:
    return types.ReadResourceResult(contents=[types.TextResourceContents(uri=uri, mime_type=mime_type, text=text)])


def build_mcp(state: State) -> Starlette:
    surface = Surface(state)
    listen = ListenHandler(surface.bus)
    server = Server(
        "system-sentinel",
        version=__version__,
        instructions=INSTRUCTIONS,
        on_list_tools=surface.list_tools,
        on_call_tool=surface.call_tool,
        on_list_prompts=surface.list_prompts,
        on_get_prompt=surface.get_prompt,
        on_list_resources=surface.list_resources,
        on_read_resource=surface.read_resource,
        on_subscriptions_listen=listen,
    )
    # The token is the boundary; host-header checks would only refuse the tailnet name a phone uses.
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    # Ordinary calls retain their one JSON body. A subscriptions/listen request is an SSE stream
    # even in JSON-response mode; its notifications tell a client to refetch the handoff resource.
    app = server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, transport_security=security
    )
    app.state.surface = surface
    app.state.listen = listen
    return app
