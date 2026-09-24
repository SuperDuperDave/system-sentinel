"""Measure MCP answer bytes over synthetic evidence, without reading a machine.

Run from a source checkout with test dependencies: PYTHONPATH=. .venv/bin/python scripts/measure_agent_answers.py
The fixture and generated rows are public synthetic data. The reported wire bytes include both
MCP text and structured content; a particular client may consume one, both or neither.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp import types

from sentinel import __version__
from sentinel.app import State
from sentinel.bridge import BridgeResult
from sentinel.mcp_server import Surface
from sentinel.readings.crash import MAX_STOPS, record_cap
from sentinel.readings.diagnostics import SIGNAL_INPUTS
from tests.conftest import LogBridge, identity_result
from tests.test_crash import collection_for
from tests.test_crash import payload as crash_payload
from tests.test_diagnostics import payload_bridge
from tests.test_system import HARDWARE

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "docs/screens/fixtures/fixture-server.py"
CRASH_FIXTURE_STOPS = 5
AGENT_PATHS = (
    ("Broad or unclear", ("health", "signals")),
    ("Unexpected restart", ("health", "crash", "record")),
    ("Hardware errors", ("health", "whea", "storms")),
    ("Program crashed or hung", ("health", "faults")),
    ("Former full tour", ("health", "crash", "events", "record", "faults", "storms", "whea", "signals")),
)


@dataclass(frozen=True)
class Row:
    label: str
    outcome: str
    count: int | None
    text_bytes: int
    result_bytes: int
    sections_bytes: int
    method_bytes: int
    messages_bytes: int = 0
    properties_bytes: int = 0
    source_questions: int = 0

    def markdown(self) -> str:
        count = f"{self.count:,}" if self.count is not None else "—"
        return (
            f"| {self.label} | {self.outcome} | {count} | {self.text_bytes:,} | "
            f"{self.result_bytes:,} | {self.sections_bytes:,} | {self.method_bytes:,} |"
        )


def fixture_bridge() -> Any:
    spec = importlib.util.spec_from_file_location("sentinel_size_fixture", FIXTURE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("the synthetic screen fixture could not be loaded")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    return fixture.FixtureBridge()


class AgentSizeBridge:
    """One synthetic source for the recommended agent path, including every Signals input.

    The published screen fixture intentionally covers only its screens. Reuse the separate
    Crash and Diagnostics test evidence here instead of making that screenshot source silently
    claim it can collect readings it never pictured.
    """

    exe = "synthetic"
    available = True

    def __init__(self) -> None:
        self.screen = fixture_bridge()
        self.diagnostics = payload_bridge()
        self.questions: list[str] = []

    def run(self, script: str, *, timeout: float = 60, depth: int = 6) -> BridgeResult:
        self.questions.append(script[:80])
        if "dump_inventory = $dump_inventory" in script:
            match = re.search(r"\$system = @\(Get-WinEvent -FilterXml \(\[xml\]\$q\) -MaxEvents (\d+)", script)
            if match is None:
                raise RuntimeError("synthetic Crash could not identify its System query bound")
            limit = int(match.group(1))
            requested = [count for count in range(1, MAX_STOPS + 1) if record_cap(count, None) == limit]
            system_query = script[match.start():].splitlines()[0]
            if len(requested) != 1 or "-Oldest" in system_query:
                raise RuntimeError("synthetic Crash supports only an unanchored, count-bounded read")
            count = requested[0]
            body = crash_payload()
            body["collection"] = collection_for(body, count)
            if any(len(body[source]) > body["collection"][source]["limit"] for source in ("system", "reports")):
                raise RuntimeError("synthetic Crash fixture exceeds the query bound it claims")
            return BridgeResult("ok", items=[body], took_ms=12)
        if "Get-CimInstance Win32_VideoController" in script:
            return BridgeResult("ok", items=[HARDWARE], took_ms=12)
        if any(marker in script for marker in ("pnputil", "Win32_PhysicalMemory", "CM_PROB_NONE", "Win32_ReliabilityRecords")):
            return self.diagnostics.run(script, timeout=timeout, depth=depth)
        return self.screen.run(script, timeout=timeout, depth=depth)


def heavy_bridge() -> LogBridge:
    rows = [
        {
            "RecordId": number,
            "Id": 41,
            "ProviderName": "Synthetic-Provider",
            "MachineName": "WORKSTATION",
            "TimeCreated": "2026-09-20T12:00:00.0000000Z",
            "Message": "Synthetic detail. " + "x" * 512,
            "Properties": ["AB" * 512],
        }
        for number in range(1, 2002)
    ]
    return LogBridge(
        result=BridgeResult("ok", items=rows, took_ms=1),
        by_marker={"$env:COMPUTERNAME": identity_result("WORKSTATION", "person")},
    )


def bytes_of(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":")).encode("utf-8"))


def path_totals(rows: list[Row]) -> list[tuple[str, int, int, int]]:
    """Client-facing bytes and synthetic bridge questions for question-directed entry paths."""
    measured = {row.label: row for row in rows}
    return [
        (label, len(names), sum(measured[name].text_bytes for name in names),
         sum(measured[name].source_questions for name in names))
        for label, names in AGENT_PATHS
    ]


async def row(surface: Surface, name: str, params: dict[str, Any], label: str) -> Row:
    answer = await surface.call_tool(None, types.CallToolRequestParams(name=name, arguments=params))
    body = answer.structured_content
    if not isinstance(body, dict) or body.get("outcome") not in ("ok", "empty"):
        raise RuntimeError(f"synthetic {label} did not produce an observed answer")
    if name == "crash":
        wanted = params.get("count", 5)
        collection = next((section.get("data") for section in body.get("sections", []) if section.get("name") == "collection"), None)
        if body.get("count") != min(wanted, CRASH_FIXTURE_STOPS) or not isinstance(collection, dict) or any(
            not isinstance(collection.get(source), dict) or collection[source].get("outcome") not in ("ok", "empty")
            for source in ("system", "reports")
        ):
            raise RuntimeError(f"synthetic Crash count or source reach did not match count={wanted}")
    if name == "signals":
        sources = body.get("method", {}).get("readings", [])
        expected = [source for source, _ in SIGNAL_INPUTS]
        if [source.get("name") for source in sources] != expected:
            raise RuntimeError("synthetic Signals omitted or reordered an input")
        gaps = [source["name"] for source in sources if source.get("outcome") not in ("ok", "empty")]
        basis = next((section.get("basis", "") for section in body.get("sections", []) if section.get("name") == "signals"), "")
        scopes = dict(SIGNAL_INPUTS)
        if gaps or any(source.get("params") != scopes[source["name"]] for source in sources) or "Not observed" in basis:
            raise RuntimeError(f"synthetic Signals input gap or wrong Crash scope: {gaps}")
    text = answer.content[0].text
    if json.loads(text) != body:
        raise RuntimeError(f"synthetic {label} text differed from structured content")
    messages = properties = 0
    if label.startswith("record: 2,000"):
        records = next(section["data"] for section in body["sections"] if section["name"] == "records")
        messages = sum(bytes_of(record.get("Message")) for record in records)
        properties = sum(bytes_of(record.get("Properties")) for record in records)
    return Row(label, body["outcome"], body.get("count"), len(text.encode("utf-8")),
               bytes_of(answer.model_dump(by_alias=True, exclude_none=True)), bytes_of(body.get("sections")),
               bytes_of(body.get("method")), messages, properties)


async def measure(home: Path, *, heavy: bool = True) -> list[Row]:
    previous_home = os.environ.get("SYSTEM_SENTINEL_HOME")
    os.environ["SYSTEM_SENTINEL_HOME"] = str(home)
    try:
        bridge = AgentSizeBridge()
        state = State(bridge=bridge, token="synthetic-token")
        state.learn()  # The app does this at startup; path counts below cover reading work after startup.
        fixture = Surface(state)
        now = datetime.now(UTC).isoformat()
        rows = []
        for name, params in (
            ("health", {}),
            ("crash", {}),
            ("events", {}),
            ("record", {"before": now}),
            ("faults", {}),
            ("storms", {}),
            ("whea", {}),
            ("signals", {}),
        ):
            before = len(bridge.questions)
            answer = await row(fixture, name, params, name)
            rows.append(replace(answer, source_questions=len(bridge.questions) - before))
        if heavy:
            large = Surface(State(bridge=heavy_bridge(), token="synthetic-token"))
            rows.append(await row(large, "record", {"before": "2026-09-21T00:00:00Z", "count": 2000}, "record: 2,000 generated rows"))
        return rows
    finally:
        if previous_home is None:
            os.environ.pop("SYSTEM_SENTINEL_HOME", None)
        else:
            os.environ["SYSTEM_SENTINEL_HOME"] = previous_home


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sentinel-agent-size-") as home:
        rows = await measure(Path(home))
    print(f"Measured with source version {__version__} on {datetime.now(UTC).date()}:")
    print("| Synthetic reading | Outcome | Count | MCP text bytes | MCP result bytes | Sections bytes | Method bytes |")
    print("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for item in rows:
        print(item.markdown())
        if item.messages_bytes:
            print(f"Heavy raw-field breakdown: Message values {item.messages_bytes:,} bytes; Properties values {item.properties_bytes:,} bytes.")
    print("\n| Agent path | Readings | MCP text bytes | Synthetic bridge questions |")
    print("| --- | ---: | ---: | ---: |")
    for label, readings, text_bytes, questions in path_totals(rows):
        print(f"| {label} | {readings} | {text_bytes:,} | {questions} |")


if __name__ == "__main__":
    asyncio.run(main())
