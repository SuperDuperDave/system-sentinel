"""The reading: one query against the machine, in one envelope.

A :class:`Reading` is what every route returns and what every MCP tool returns.
Its ``outcome`` comes from the bridge and says whether the machine was observed;
its ``sections`` keep raw, derived, invariant and inferred apart; its ``method``
says how it was taken so the evidence can be reproduced by hand.

Readings are registered in :data:`REGISTRY` by name with their parameters, so
the catalog, the routes and the MCP tools are all projections of one table.
"""

from __future__ import annotations

import asyncio
import inspect
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal

from .bridge import Bridge, BridgeResult, Outcome

Class = Literal["raw", "derived", "invariant", "inferred"]


@dataclass
class Section:
    name: str
    cls: Class
    data: Any
    basis: str | None = None
    """For derived and inferred sections: the inputs and the rule, in one sentence."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "class": self.cls, "data": self.data}
        if self.basis:
            d["basis"] = self.basis
        return d


@dataclass
class Reading:
    reading: str
    params: dict[str, Any]
    outcome: Outcome
    method: dict[str, Any]
    sections: list[Section] = field(default_factory=list)
    asked_at: str = field(default_factory=lambda: _now())
    took_ms: int = 0
    count: int | None = None
    error: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    redacted: list[str] = field(default_factory=list)

    @property
    def observed(self) -> bool:
        return self.outcome in ("ok", "empty")

    def section(self, name: str) -> Section | None:
        return next((s for s in self.sections if s.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reading": self.reading,
            "params": self.params,
            "asked_at": self.asked_at,
            "took_ms": self.took_ms,
            "outcome": self.outcome,
            "method": self.method,
            "count": self.count,
            "sections": [s.to_dict() for s in self.sections],
            "error": self.error,
            "warnings": self.warnings,
            "redacted": self.redacted,
        }


@dataclass(frozen=True)
class Param:
    name: str
    type: Literal["int", "str", "float", "bool", "list[int]"]
    default: Any
    description: str
    choices: tuple[Any, ...] | None = None


Taker = Callable[..., "Reading | Awaitable[Reading]"]


@dataclass(frozen=True)
class Spec:
    """One entry in the catalog."""

    name: str
    description: str
    classes: tuple[Class, ...]
    take: Taker
    params: tuple[Param, ...] = ()
    private: tuple[str, ...] = ()
    """What this reading may carry that the default redaction removes."""
    heavy: bool = False
    """Takes seconds: the dashboard loads it on demand."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "classes": list(self.classes),
            "params": [
                {"name": p.name, "type": p.type, "default": p.default, "description": p.description, **({"choices": list(p.choices)} if p.choices else {})}
                for p in self.params
            ],
            "private": list(self.private),
            "heavy": self.heavy,
        }

    def coerce(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Apply defaults and types to caller-supplied parameters; unknown names are ignored."""
        out: dict[str, Any] = {}
        for p in self.params:
            value = raw.get(p.name, p.default)
            if value is None:
                if p.default is None:
                    raise ValueError(f"parameter {p.name!r} is required")
                out[p.name] = None
                continue
            try:
                out[p.name] = _coerce(p, value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"parameter {p.name!r}: {exc}") from exc
            if p.choices is not None and out[p.name] not in p.choices:
                raise ValueError(f"parameter {p.name!r} must be one of {list(p.choices)}")
        return out


def _coerce(p: Param, value: Any) -> Any:
    if p.type == "int":
        return int(value)
    if p.type == "float":
        return float(value)
    if p.type == "bool":
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on")
        return bool(value)
    if p.type == "list[int]":
        if isinstance(value, str):
            return [int(v) for v in value.split(",") if v.strip()]
        return [int(v) for v in value]
    return str(value)


REGISTRY: dict[str, Spec] = {}


def register(spec: Spec) -> Spec:
    if spec.name in REGISTRY:
        raise ValueError(f"reading {spec.name!r} registered twice")
    REGISTRY[spec.name] = spec
    return spec


async def take(name: str, bridge: Bridge, raw_params: dict[str, Any] | None = None) -> Reading:
    """Take a reading by name. Unknown names and bad parameters raise ``KeyError``/``ValueError``.

    A synchronous taker runs in a worker thread: the bridge blocks on a process, and the
    server must keep answering other clients while a slow query runs.
    """
    spec = REGISTRY[name]
    params = spec.coerce(raw_params or {})
    if inspect.iscoroutinefunction(spec.take):
        return await spec.take(bridge, params)
    return await asyncio.to_thread(spec.take, bridge, params)


def from_bridge(
    name: str,
    params: dict[str, Any],
    script: str,
    result: BridgeResult,
    *,
    section: str = "records",
    cls: Class = "raw",
    shape: Literal["list", "object"] = "list",
) -> Reading:
    """Turn a bridge result into a reading with one section of the given class."""
    method = {"kind": "powershell", "query": textwrap.dedent(script).strip()}
    reading = Reading(reading=name, params=params, outcome=result.outcome, method=method, took_ms=result.took_ms, warnings=list(result.warnings))
    if result.outcome == "ok":
        data: Any = result.items if shape == "list" else result.items[0]
        reading.sections = [Section(section, cls, data)]
        reading.count = len(result.items) if shape == "list" else None
    elif result.outcome == "empty":
        reading.sections = [Section(section, cls, [] if shape == "list" else None)]
        reading.count = 0
    else:
        reading.error = {"kind": result.outcome, "detail": result.error or ""}
    return reading


def from_object(
    name: str,
    params: dict[str, Any],
    script: str,
    result: BridgeResult,
    build: Callable[[dict[str, Any]], list[Section]],
) -> Reading:
    """Turn one object from the machine into a reading whose sections ``build`` decides.

    ``build`` receives the payload and returns the sections; it is where every derivation and
    every observation lives, so a test can hold the rule rather than the passthrough. A
    ``warnings`` list in the payload is lifted into the envelope and never reaches a section: it
    says which sub-query did not answer, so an absence the tool could not look at is distinguishable
    from an observed nothing. On ``empty``, ``build`` sees an empty payload and decides the sections.
    """
    reading = Reading(
        reading=name,
        params=params,
        outcome=result.outcome,
        method={"kind": "powershell", "query": textwrap.dedent(script).strip()},
        took_ms=result.took_ms,
        warnings=list(result.warnings),
    )
    if result.outcome == "ok":
        payload = result.items[0]
        if not isinstance(payload, dict):
            reading.outcome = "failed"
            reading.error = {"kind": "failed", "detail": "the query did not return an object"}
            return reading
        reading.warnings.extend(str(w) for w in (payload.pop("warnings", None) or []))
        reading.sections = build(payload)
    elif result.outcome == "empty":
        reading.sections = build({})
    else:
        reading.error = {"kind": result.outcome, "detail": result.error or ""}
    return reading


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
