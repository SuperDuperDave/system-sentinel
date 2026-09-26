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
import math
import textwrap
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal

from . import __version__
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
    """Completed reading wall time at the request boundary; direct taker values are provisional."""
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
        # Keep the verdict and qualifications ahead of potentially large evidence arrays and
        # scripts. JSON field order is not the contract, but text-only clients can read this
        # useful prefix before reaching the bulk of the answer.
        return {
            "reading": self.reading,
            "sentinel_version": __version__,
            "params": self.params,
            "outcome": self.outcome,
            "count": self.count,
            "error": self.error,
            "warnings": self.warnings,
            "redacted": self.redacted,
            "asked_at": self.asked_at,
            "took_ms": self.took_ms,
            "sections": [s.to_dict() for s in self.sections],
            "method": self.method,
        }


@dataclass(frozen=True)
class Param:
    name: str
    type: Literal["int", "str", "float", "bool", "list[int]"]
    default: Any
    description: str
    choices: tuple[Any, ...] | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None


Taker = Callable[..., "Reading | Awaitable[Reading]"]
ReadingCall = Callable[[str, dict[str, Any] | None], Awaitable[Reading]]


@dataclass(frozen=True)
class Spec:
    """One entry in the catalog."""

    name: str
    description: str
    classes: tuple[Class, ...]
    take: Taker
    params: tuple[Param, ...] = ()
    private: tuple[str, ...] = ()
    """Fields or content that may contain details affected by default redaction."""
    heavy: bool = False
    """Takes seconds: the dashboard loads it on demand."""
    requires_selection: bool = False
    """Taken only when asked for by name: it needs an exact user or agent reference, or (``space``) its
    cost must never be a side effect. Bulk capture and bench leave it out."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "classes": list(self.classes),
            "params": [
                {
                    "name": p.name, "type": p.type, "default": p.default, "description": p.description,
                    **({"choices": list(p.choices)} if p.choices else {}),
                    **({"minimum": p.minimum} if p.minimum is not None else {}),
                    **({"maximum": p.maximum} if p.maximum is not None else {}),
                }
                for p in self.params
            ],
            "private": list(self.private),
            "heavy": self.heavy,
            "requires_selection": self.requires_selection,
        }

    def coerce(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Apply defaults and types to caller-supplied parameters. An unknown name is refused: a
        misspelled parameter silently ignored would read the wrong evidence with a clean outcome."""
        unknown = sorted(set(raw) - {p.name for p in self.params})
        if unknown:
            raise ValueError(f"unknown parameter(s) {unknown}; this reading takes {[p.name for p in self.params] or 'none'}")
        out: dict[str, Any] = {}
        for p in self.params:
            value = raw.get(p.name, p.default)
            if value is None:
                if p.default is None:
                    raise ValueError(f"parameter {p.name!r} is required")
                value = p.default
            try:
                out[p.name] = _coerce(p, value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"parameter {p.name!r}: {exc}") from exc
            if p.choices is not None and out[p.name] not in p.choices:
                raise ValueError(f"parameter {p.name!r} must be one of {list(p.choices)}")
            if p.type in ("int", "float"):
                if p.minimum is not None and out[p.name] < p.minimum:
                    raise ValueError(f"parameter {p.name!r}: must be at least {p.minimum}")
                if p.maximum is not None and out[p.name] > p.maximum:
                    raise ValueError(f"parameter {p.name!r}: must be at most {p.maximum}")
        return out


def _coerce(p: Param, value: Any) -> Any:
    if p.type == "int":
        if isinstance(value, bool) or isinstance(value, float) and not value.is_integer():
            raise ValueError("must be an integer")
        return int(value)
    if p.type == "float":
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("must be finite")
        return number
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


def automatic_params(name: str, at: datetime | None = None) -> dict[str, Any]:
    """Parameters bulk capture, bench and transport soak can choose without guessing a target."""
    spec = REGISTRY[name]
    if spec.requires_selection:
        raise ValueError(f"reading {name!r} requires an exact selection")
    if name == "record":
        moment = at or datetime.now(UTC)
        return {"before": moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
    return {}


async def take(name: str, bridge: Bridge, raw_params: dict[str, Any] | None = None) -> Reading:
    """Take a reading by name. Unknown names and bad parameters raise ``KeyError``/``ValueError``.

    A synchronous taker runs in a worker thread: the bridge blocks on a process, and the
    server must keep answering other clients while a slow query runs. The returned reading's
    duration includes waiting for that worker and composing its evidence. A shallow copy leaves
    the taker's own ``took_ms`` untouched; its evidence containers remain shared.
    """
    started = time.perf_counter()
    spec = REGISTRY[name]
    params = spec.coerce(raw_params or {})
    # Which of the two a taker is decides where it runs; what it hands back decides whether there is
    # anything left to await. Asking the value rather than the function is also what lets a type
    # checker follow this.
    taken = spec.take(bridge, params) if inspect.iscoroutinefunction(spec.take) else await asyncio.to_thread(spec.take, bridge, params)
    reading = await taken if inspect.isawaitable(taken) else taken
    return replace(reading, took_ms=int((time.perf_counter() - started) * 1000))


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
    """Turn a bridge result into one section; an object collector must return exactly one object."""
    method = {"kind": "powershell", "query": textwrap.dedent(script).strip()}
    reading = Reading(reading=name, params=params, outcome=result.outcome, method=method, took_ms=result.took_ms, warnings=list(result.warnings))
    if shape == "object" and reading.observed:
        if result.outcome != "ok" or len(result.items) != 1 or not isinstance(result.items[0], dict):
            reading.outcome = "failed"
            reading.error = {"kind": "failed", "detail": "The collector must return exactly one object; its response was missing or had an unexpected shape."}
            return reading
    if result.outcome == "ok":
        data: Any = result.items if shape == "list" else result.items[0]
        reading.sections = [Section(section, cls, data)]
        reading.count = len(result.items) if shape == "list" else None
    elif result.outcome == "empty":
        reading.sections = [Section(section, cls, [] if shape == "list" else None)]
        reading.count = 0
    else:
        reading.error = {"kind": result.error_kind, "detail": result.error or ""}
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
    from an observed nothing. The collector must return exactly one object, even when its inner
    collections are empty. Missing or ambiguous output never reaches ``build``. The payload is
    copied before warnings are lifted, preserving the bridge result for its other consumers.
    """
    reading = from_bridge(name, params, script, result, shape="object")
    if not reading.observed:
        return reading
    payload = reading.sections[0].data.copy()
    reading.warnings.extend(str(w) for w in (payload.pop("warnings", None) or []))
    reading.sections = build(payload)
    return reading


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
