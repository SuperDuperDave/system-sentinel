"""The bench: what a reading costs on this machine, measured rather than claimed.

``system-sentinel bench`` takes every reading in the catalog N times through the real bridge and
reports the distribution of each envelope's own ``took_ms`` — the number the caller waited for,
not a stopwatch held around a different piece of code. It only ever takes readings, so it reads
the machine and never writes to it.

A reading that was not observed is reported with its outcome and no time. A failure is not a
measurement, and folding one into a median is how a number stops meaning anything.

Which transport is measured is set by :func:`apply_transport` before the first question, and the
run reports what the bridge then did with it, fall-backs included: a table that named a transport
it had not actually measured would be worse than no table. Run the command once per transport; the
document keeps both, because the claim this phase makes is a comparison and a comparison needs
both sides written down.

Nothing this module writes may name a host, a person or a path. That is a guard in
:func:`check_clean`, run over the text before it is written, rather than a rule to remember.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from . import bridge as bridge_module
from .bridge import Bridge
from .reading import REGISTRY, Reading, take

TRANSPORTS: tuple[str, ...] = ("session", "one-shot")
DEFAULT_RUNS = 3

# Where the transport lives: the variable a person or a service sets, and the size the pool itself
# reads, which it takes from that variable once when the module is imported. Zero is the one-shot
# transport the tool used before the pool existed.
SESSIONS_ENV = "SENTINEL_BRIDGE_SESSIONS"

# A script that reads nothing. What it costs is what asking costs under this transport, which is
# the floor every other number in the table sits on.
FLOOR_SCRIPT = "1"
FLOOR_NAME = "bridge floor"

# Where the document lives when nobody says otherwise, relative to the repository root.
DOC_PATH = "docs/measurements/latency.md"

# The shapes a host name, a Windows profile path or a home directory take: the same ones CI greps
# for in tracked files. A measurement that carries one is not written at all.
LEAKS = re.compile(r"[a-z0-9-]+\.ts\.net|C:\\Users\\[A-Za-z]|/home/[a-z][a-z0-9_-]*/")

# What a value from the machine may look like before it is allowed into the document: words,
# numbers and the punctuation an edition string uses. A serial, a path or an address cannot.
SHAPE_VALUE = re.compile(r"^[A-Za-z0-9 .()+/-]{1,60}$")

HEADINGS = {"session": "Session transport", "one-shot": "One-shot transport"}

TABLE_HEAD = "| Reading | Runs | Min (ms) | Median (ms) | p95 (ms) | Outcome |\n| --- | ---: | ---: | ---: | ---: | --- |"

PREAMBLE = """# Reading latency

What each reading costs on the machine it was taken on, from `system-sentinel bench`. Every number
is the envelope's own `took_ms` — what the caller waited for — over the runs the section names. A
reading that was not observed carries its outcome instead of a time, because a failure is not a
measurement. Min, median and p95 are nearest-rank over the observed runs: nothing is interpolated,
so a number never implies a sample that was not taken.

Regenerate it on the machine, one run per transport:

```
system-sentinel bench --transport session --out docs/measurements/latency.md
system-sentinel bench --transport one-shot --out docs/measurements/latency.md
```

The second run does not erase the first: a run rewrites its own transport's section and carries the
other one through. The transport a section names is the one that carried its questions: `bench`
sets the pool's size before the first question and reads back what the bridge did with it, so a
question that fell back to a launch is counted on the line rather than hidden in the numbers.

A run takes the whole catalog, the heavy readings included, so it is as slow as the slowest query
on the machine: `--readings a,b` narrows it when only some are in question, and `--runs N` buys a
tail worth reading, since a p95 over three runs is only the slowest of the three.

Nothing here names a machine, a person or a path, and `bench` refuses to write a document that
does."""


class Leak(ValueError):
    """The text would have carried a host, a user or a local path, so it was not written."""


@dataclass(frozen=True)
class Attempt:
    """One run of one reading: what came back, and how long it took when the machine answered."""

    outcome: str
    took_ms: int | None = None

    @classmethod
    def of(cls, reading: Reading) -> Attempt:
        return cls(reading.outcome, reading.took_ms if reading.observed else None)


@dataclass(frozen=True)
class Row:
    """One reading across its runs: the samples, and the three numbers derived from them.

    ``note`` is set only when the reading could not be taken at all — a bad parameter, a taker that
    raised — which is not an outcome the machine ever returned and is kept apart from the six.
    """

    reading: str
    attempts: tuple[Attempt, ...] = ()
    note: str | None = None

    @property
    def times(self) -> list[int]:
        """Only the runs the machine answered. The rest are outcomes, not measurements."""
        return sorted(a.took_ms for a in self.attempts if a.took_ms is not None)

    @property
    def observed(self) -> int:
        return len(self.times)

    @property
    def outcome(self) -> str:
        """Every outcome the runs came back with, first seen first, so a run that differed shows."""
        seen = list(dict.fromkeys(a.outcome for a in self.attempts))
        return ", ".join(seen) if seen else "not taken"

    @property
    def min_ms(self) -> int | None:
        return self.times[0] if self.times else None

    @property
    def median_ms(self) -> int | None:
        return int(round(statistics.median(self.times))) if self.times else None

    @property
    def p95_ms(self) -> int | None:
        return percentile(self.times, 0.95) if self.times else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reading": self.reading,
            "runs": len(self.attempts),
            "observed": self.observed,
            "outcome": self.outcome,
            "min_ms": self.min_ms,
            "median_ms": self.median_ms,
            "p95_ms": self.p95_ms,
            "samples": [{"outcome": a.outcome, "took_ms": a.took_ms} for a in self.attempts],
            "note": self.note,
        }


@dataclass(frozen=True)
class Report:
    """One run of the bench: the rows, the floor they sit on, and what the run was."""

    transport: str
    runs: int
    taken_on: str
    version: str
    rows: tuple[Row, ...]
    floor: Row | None = None
    machine: str | None = None
    pool: dict[str, Any] | None = None
    """What the bridge reported about its transport once the run was over: counts, never a verdict."""

    @property
    def observed(self) -> bool:
        """True when the machine answered something. Nothing observed is no measurement at all."""
        return any(row.observed for row in self.rows) or bool(self.floor and self.floor.observed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": "system-sentinel",
            "version": self.version,
            "taken_on": self.taken_on,
            "transport": self.transport,
            "runs": self.runs,
            "machine": self.machine,
            "pool": self.pool,
            "floor": self.floor.to_dict() if self.floor else None,
            "readings": [row.to_dict() for row in self.rows],
        }


def percentile(times: Sequence[int], q: float) -> int:
    """The nearest-rank percentile: the smallest measured value at or above the q-th rank.

    Nothing is interpolated. Over three runs the p95 is the slowest of the three, which is the
    truth about three runs; an interpolated number would describe samples nobody took.
    """
    if not times:
        raise ValueError("no observed runs to take a percentile of")
    ordered = sorted(times)
    rank = min(len(ordered), max(1, math.ceil(q * len(ordered))))
    return ordered[rank - 1]


def select(wanted: str | Iterable[str] | None = None) -> list[str]:
    """The readings to measure, in catalog order. Nothing named means every one of them.

    An unknown name is refused rather than skipped: a bench that quietly measured nothing would
    still print a table, and a table is read as evidence.
    """
    catalog = list(REGISTRY)
    if wanted is None:
        names: list[str] = []
    elif isinstance(wanted, str):
        names = [n.strip() for n in wanted.split(",") if n.strip()]
    else:
        names = [str(n).strip() for n in wanted if str(n).strip()]
    if not names:
        return catalog
    unknown = sorted(set(names) - set(catalog))
    if unknown:
        raise ValueError(f"unknown reading(s) {unknown}; the catalog holds {catalog}")
    return [name for name in catalog if name in set(names)]


def apply_transport(transport: str) -> None:
    """Point the bridge at the transport this run measures, in both places it is written.

    The pool takes its size from :data:`SESSIONS_ENV` when :mod:`sentinel.bridge` is imported,
    which is long before a flag is parsed: setting the variable alone would leave the bench
    measuring whichever transport was already configured and labelling it with the other. The size
    the pool actually reads is ``bridge.POOL_SIZE``, so both are set, and any pool already running
    is ended — a pool of four cannot answer a one-shot run's questions.

    ``one-shot`` is zero sessions: a process per question. ``session`` keeps a pool, and an
    operator's own positive setting stands, so a run can measure a pool of one against a pool of
    four.
    """
    if transport not in TRANSPORTS:
        raise ValueError(f"transport must be one of {list(TRANSPORTS)}")
    size = 0 if transport == "one-shot" else _session_size()
    os.environ[SESSIONS_ENV] = str(size)
    bridge_module.shutdown_sessions()
    bridge_module.POOL_SIZE = size


def _session_size() -> int:
    current = os.environ.get(SESSIONS_ENV, "").strip()
    return int(current) if current.isdigit() and int(current) > 0 else bridge_module.DEFAULT_POOL_SIZE


async def measure(bridge: Bridge, *, names: Sequence[str], runs: int = DEFAULT_RUNS, transport: str = "session") -> Report:
    """Take each named reading ``runs`` times and return what they cost.

    The transport is applied before the first question, so the pool the bridge starts is the pool
    being measured. One discarded question opens the run: the first question through a session pays
    for starting it, and charging that to whichever reading happened to be first would make one row
    a lie. The floor is then measured for itself, because "a reading costs a fraction of what it
    cost" is a claim about the cost of asking as much as about any one query.

    What the bridge did with the transport is read back when the questions are over, so the run
    reports the transport that carried them rather than the one it asked for.
    """
    if runs < 1:
        raise ValueError("a bench takes at least one run")
    apply_transport(transport)

    _ask_floor(bridge)
    floor = Row(FLOOR_NAME, tuple(_ask_floor(bridge) for _ in range(runs)))

    rows: list[Row] = []
    machine: str | None = None
    for name in names:
        attempts: list[Attempt] = []
        note: str | None = None
        for _ in range(runs):
            try:
                reading = await _take(name, bridge)
            except Exception as exc:  # noqa: BLE001 - one reading that cannot be taken must not end the bench
                note = f"could not be taken: {exc}"
                break
            attempts.append(Attempt.of(reading))
            if name == "system" and reading.observed:
                machine = machine_shape(reading) or machine
        rows.append(Row(name, tuple(attempts), note=note))

    return Report(
        transport=transport,
        runs=runs,
        taken_on=datetime.now(UTC).date().isoformat(),
        version=__version__,
        rows=tuple(rows),
        floor=floor,
        machine=machine,
        pool=bridge_module.sessions_report(bridge),
    )


async def _take(name: str, bridge: Bridge) -> Reading:
    """One reading for the bench, given what a required parameter needs.

    ``record`` frames the log on a moment, so it is given now — the same rule a capture uses, and
    the same query shape the dashboard runs when a reader jumps to the newest record.
    """
    params = {"before": _stamp()} if name == "record" else {}
    return await take(name, bridge, params)


def _ask_floor(bridge: Bridge) -> Attempt:
    result = bridge.run(FLOOR_SCRIPT)
    return Attempt(result.outcome, result.took_ms if result.observed else None)


def machine_shape(reading: Reading | None) -> str | None:
    """The machine in general terms, from the ``system`` snapshot and nothing else.

    A latency table is only readable beside the machine that produced it, and Windows' own edition,
    build and architecture say enough: a different machine is a different table. Each value has to
    match a shape a name, a serial or a path cannot take before it is written down, so a field that
    ever starts carrying an identifier drops out of the line instead of into the document.
    """
    if reading is None:
        return None
    snapshot = reading.section("snapshot")
    data = snapshot.data if snapshot is not None and isinstance(snapshot.data, dict) else None
    if not data:
        return None
    caption = _shape_value(data.get("os_caption"))
    build = _shape_value(data.get("os_build"))
    architecture = _shape_value(data.get("architecture"))
    parts = [part for part in (caption, f"build {build}" if build else None, architecture) if part]
    return ", ".join(parts) or None


def _shape_value(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not SHAPE_VALUE.match(text) or LEAKS.search(text):
        return None
    return text


def check_clean(text: str) -> None:
    """Refuse text that names a host, a user or a local path.

    The repository is public and this document is written from what the machine said, so the rule
    lives here as a mechanism: the same shapes CI greps for, checked before anything is written or
    printed rather than after it is committed.
    """
    found = sorted({match.group(0) for match in LEAKS.finditer(text)})
    if found:
        raise Leak(f"the measurement would have named a host, a user or a local path: {found}")


def table(rows: Sequence[Row]) -> str:
    """The rows as one Markdown table. A number that was never observed is an em dash, not a zero."""
    lines = [TABLE_HEAD]
    for row in rows:
        lines.append(f"| {row.reading} | {len(row.attempts)} | {_cell(row.min_ms)} | {_cell(row.median_ms)} | {_cell(row.p95_ms)} | {row.outcome} |")
    return "\n".join(lines)


def _cell(value: int | None) -> str:
    return "—" if value is None else str(value)


def section(report: Report) -> str:
    """One transport's section: what the run was, the floor, and the table."""
    facts = [
        f"- Taken **{report.taken_on}**, {report.runs} run(s) per reading, system-sentinel {report.version}.",
        f"- Machine: {report.machine}." if report.machine else "- Machine: not stated; the `system` reading did not answer.",
        f"- {transport_line(report)}",
        f"- {_floor_line(report.floor)}",
    ]
    return "\n".join([f"## {HEADINGS[report.transport]}", "", *facts, "", table(report.rows)])


def transport_line(report: Report) -> str:
    """What carried the questions, from the bridge's own counts rather than from the flag.

    A question that fell back to a launch is the one thing that would make a session run read like
    something it was not, so it is on the line whether it happened or not.
    """
    pool = report.pool or {}
    carried = pool.get("transport") or report.transport
    disagrees = "" if carried == report.transport else f" — the bridge reported `{carried}`"
    if carried == "one-shot":
        return f"Transport: **one-shot**, a process per question{disagrees}."
    return (
        f"Transport: **session**, a pool of {pool.get('size')}; {pool.get('answered', 0)} question(s) answered through sessions, "
        f"{pool.get('fell_back', 0)} fell back to a launch{disagrees}."
    )


def _floor_line(floor: Row | None) -> str:
    if floor is None or not floor.times:
        return f"Bridge floor (a script that reads nothing): not observed ({floor.outcome if floor else 'not taken'})."
    return f"Bridge floor (a script that reads nothing): min {floor.min_ms} ms, median {floor.median_ms} ms, p95 {floor.p95_ms} ms over {len(floor.attempts)} run(s)."


def document(report: Report | None = None, previous: str = "") -> str:
    """The measurement document: a fixed preamble and one section per transport.

    A run rewrites its own transport's section and carries the other one through untouched, so
    measuring the session transport does not erase the one-shot numbers it exists to be compared
    with. The preamble is always regenerated from here, so the command it names cannot drift from
    the command that exists.
    """
    sections = {transport: _placeholder(transport) for transport in TRANSPORTS}
    sections.update(_parse(previous))
    if report is not None:
        sections[report.transport] = section(report)
    return "\n\n".join([PREAMBLE, *(sections[transport] for transport in TRANSPORTS)]).strip() + "\n"


def _placeholder(transport: str) -> str:
    return "\n".join(
        [
            f"## {HEADINGS[transport]}",
            "",
            f"_Not measured yet. Run the command above with `--transport {transport}` on the machine._",
            "",
            TABLE_HEAD,
            "| _not measured_ | — | — | — | — | — |",
        ]
    )


def _parse(previous: str) -> dict[str, str]:
    """The transport sections already in a document, so a run keeps the one it did not measure."""
    found: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in previous.splitlines():
        if line.startswith("## "):
            if current is not None:
                found[current] = "\n".join(lines).strip()
            current = _transport_of(line[3:].strip())
            lines = [line]
            continue
        if current is not None:
            lines.append(line)
    if current is not None:
        found[current] = "\n".join(lines).strip()
    return found


def _transport_of(heading: str) -> str | None:
    return next((transport for transport, title in HEADINGS.items() if title.lower() == heading.lower()), None)


def write(report: Report, path: Path) -> str:
    """Merge this run into the document at ``path`` and write it. Returns what was written."""
    previous = path.read_text(encoding="utf-8") if path.is_file() else ""
    text = document(report, previous)
    check_clean(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def as_json(report: Report) -> str:
    """The run for a machine: the samples as they came back, and the numbers derived from them."""
    text = json.dumps(report.to_dict(), ensure_ascii=False, indent=1)
    check_clean(text)
    return text


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
