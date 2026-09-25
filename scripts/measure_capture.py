"""Measure a complete synthetic capture's source questions and ZIP size.

Run from the repository root with test dependencies: PYTHONPATH=. .venv/bin/python scripts/measure_capture.py
The ordinary fixture reuses public screen and reading tests. The saturated fixture generates
200 System events and 20 stops with deterministic, hard-to-compress raw fields. Neither reads
the host; each capture uses a temporary data home and is removed after measurement.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import tempfile
import zipfile
from dataclasses import replace
from typing import Any

from scripts.measure_agent_answers import AgentSizeBridge
from sentinel import __version__
from sentinel.capture import create, read_saved
from sentinel.reading import REGISTRY, Reading, Section, Spec
from sentinel.stack import Prompts, Stack


def saturated_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Fill the differing Events and Crash scopes without pretending to be host timing data."""
    events = []
    for number in range(200):
        rng = random.Random(number)
        events.append({
            "Log": "System", "RecordId": 1000 + number, "Id": 100 + number % 10,
            "Level": 1 + number % 4, "ProviderName": f"Synthetic-Provider-{number % 9}",
            "TimeCreated": f"2026-09-24T12:{number // 60:02d}:{number % 60:02d}.0000000Z",
            "Message": "".join(rng.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789", k=512)),
            "Properties": [rng.randbytes(512).hex()],
        })
    records, stops = [], []
    for number in range(20):
        rng = random.Random(10000 + number)
        at = f"2026-09-{24 - number:02d}T12:00:00.0000000Z"
        records.append({
            "Log": "System", "RecordId": 2000 + number, "TimeCreated": at,
            "Id": 41, "ProviderName": "Microsoft-Windows-Kernel-Power",
            "Message": "".join(rng.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789", k=512)),
            "Properties": [rng.randbytes(512).hex()],
        })
        stops.append({
            "started_at": at, "bugcheck": {"code": "0x133", "name": "DPC_WATCHDOG_VIOLATION"},
            "records": {"power_41": 2000 + number},
        })
    return events, records, stops


def measure(*, saturated: bool) -> dict[str, Any]:
    original: dict[str, Spec] = {}
    extra_calls = {"events": 0, "crash": 0}
    if saturated:
        events, records, stops = saturated_rows()
        original = {name: REGISTRY[name] for name in extra_calls}

        def take_events(_bridge: Any, params: dict[str, Any]) -> Reading:
            extra_calls["events"] += 1
            selected = [row for row in events if row["Level"] in params["levels"]][: params["count"]]
            return Reading("events", params, "ok", {"kind": "synthetic"},
                           sections=[Section("records", "raw", selected)], count=len(selected))

        def take_crash(_bridge: Any, params: dict[str, Any]) -> Reading:
            extra_calls["crash"] += 1
            count = params["count"]
            return Reading("crash", params, "ok", {"kind": "synthetic"}, sections=[
                Section("records", "raw", records[:count]), Section("stops", "derived", stops[:count]),
                Section("collection", "raw", {"system": {"outcome": "ok", "bound_reached": False},
                                              "reports": {"outcome": "ok", "bound_reached": False}}),
            ], count=min(count, len(stops)))

        REGISTRY["events"] = replace(REGISTRY["events"], take=take_events)
        REGISTRY["crash"] = replace(REGISTRY["crash"], take=take_crash)

    try:
        with tempfile.TemporaryDirectory(prefix="sentinel-capture-size-") as home:
            old_home = os.environ.get("SYSTEM_SENTINEL_HOME")
            os.environ["SYSTEM_SENTINEL_HOME"] = home
            try:
                bridge = AgentSizeBridge()
                result = asyncio.run(create(bridge, Stack(), Prompts()))
                with zipfile.ZipFile(result.path) as archive:
                    member_bytes = {info.filename: info.file_size for info in archive.infolist()}
                    params = {name: json.loads(archive.read(f"readings/{name}.json"))["params"] for name in extra_calls}
                answer_bytes = {name: len(json.dumps(read_saved(result.name, name), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                                for name in ("signals", "crash", "events")}
                return {
                    "fixture": "saturated" if saturated else "ordinary",
                    "version": __version__,
                    "source_questions": len(bridge.questions) + sum(extra_calls.values()),
                    "bridge_questions": len(bridge.questions),
                    "synthetic_source_calls": extra_calls,
                    "zip_bytes": result.path.stat().st_size,
                    "uncompressed_bytes": sum(member_bytes.values()),
                    "member_bytes": {name: member_bytes[f"readings/{name}.json"] for name in ("signals", "crash", "events")},
                    "read_answer_bytes": answer_bytes,
                    "saved_params": params,
                }
            finally:
                if old_home is None:
                    os.environ.pop("SYSTEM_SENTINEL_HOME", None)
                else:
                    os.environ["SYSTEM_SENTINEL_HOME"] = old_home
    finally:
        REGISTRY.update(original)


if __name__ == "__main__":
    print(json.dumps([measure(saturated=False), measure(saturated=True)], indent=2))
