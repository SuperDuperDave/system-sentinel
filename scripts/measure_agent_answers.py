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
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp import types

from sentinel.app import State
from sentinel.bridge import BridgeResult
from sentinel.mcp_server import Surface
from tests.conftest import LogBridge, identity_result

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "docs/screens/fixtures/fixture-server.py"


def fixture_bridge() -> Any:
    spec = importlib.util.spec_from_file_location("sentinel_size_fixture", FIXTURE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("the synthetic screen fixture could not be loaded")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    return fixture.FixtureBridge()


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


async def row(surface: Surface, name: str, params: dict[str, Any], label: str) -> None:
    answer = await surface.call_tool(None, types.CallToolRequestParams(name=name, arguments=params))
    body = answer.structured_content
    if not isinstance(body, dict) or body.get("outcome") not in ("ok", "empty"):
        raise RuntimeError(f"synthetic {label} did not produce an observed answer")
    text = answer.content[0].text
    if json.loads(text) != body:
        raise RuntimeError(f"synthetic {label} text differed from structured content")
    print(
        f"| {label} | {body['outcome']} | {body.get('count') if body.get('count') is not None else '—'} | {len(text.encode('utf-8')):,} | "
        f"{bytes_of(answer.model_dump(by_alias=True, exclude_none=True)):,} | "
        f"{bytes_of(body.get('sections')):,} | {bytes_of(body.get('method')):,} |"
    )
    if label.startswith("record: 2,000"):
        records = next(section["data"] for section in body["sections"] if section["name"] == "records")
        messages = sum(bytes_of(record.get("Message")) for record in records)
        properties = sum(bytes_of(record.get("Properties")) for record in records)
        print(f"Heavy raw-field breakdown: Message values {messages:,} bytes; Properties values {properties:,} bytes.")


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sentinel-agent-size-") as home:
        os.environ["SYSTEM_SENTINEL_HOME"] = home
        fixture = Surface(State(bridge=fixture_bridge(), token="synthetic-token"))
        now = datetime.now(UTC).isoformat()
        print("| Synthetic reading | Outcome | Count | MCP text bytes | MCP result bytes | Sections bytes | Method bytes |")
        print("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
        for name, params in (
            ("health", {}),
            ("events", {}),
            ("record", {"before": now}),
            ("faults", {}),
            ("storms", {}),
            ("whea", {}),
        ):
            await row(fixture, name, params, name)
        heavy = Surface(State(bridge=heavy_bridge(), token="synthetic-token"))
        await row(heavy, "record", {"before": "2026-09-21T00:00:00Z", "count": 2000}, "record: 2,000 generated rows")


if __name__ == "__main__":
    asyncio.run(main())
