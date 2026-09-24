"""Measure two synthetic Signals leads, without a host or a running app.

Run from a source checkout: PYTHONPATH=. .venv/bin/python scripts/measure_signals_leads.py
The pressure fixture separates a provider's one-minute cluster from a 14-day returned sample.
The reset fixture fills the bounded reference list and includes unusable raw ids.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sentinel import __version__
from sentinel.reading import Reading, Section
from sentinel.readings.diagnostics import take_signals_sync


def stamp(at: datetime) -> str:
    return at.isoformat(timespec="microseconds").replace("+00:00", "Z")


def reading(name: str, params: dict, sections: list[Section]) -> Reading:
    return Reading(name, params, "ok", {"kind": "synthetic"}, sections=sections)


def main() -> None:
    start = datetime(2026, 9, 10, tzinfo=UTC)
    events = [
        {"ProviderName": f"Other {i % 8}", "TimeCreated": stamp(start + timedelta(hours=2.5 * i))}
        for i in range(140)
    ] + [
        {"ProviderName": "Example", "TimeCreated": stamp(start + timedelta(days=7, seconds=i))}
        for i in range(60)
    ]
    event_reading = reading("events", {"log": "System", "levels": [1, 2, 3, 4], "count": 200}, [
        Section("records", "raw", events),
        Section("collection", "raw", {"log": "System", "limit": 200, "returned": 200, "truncated": True}),
    ])
    at = "2026-09-24T12:00:00.1234567Z"
    reset = {"Id": 4101, "ProviderName": "Display", "TimeCreated": at}
    transitions = [
        *({**reset, "RecordId": record_id} for record_id in range(1, 11)),
        {**reset, "RecordId": 11}, {**reset, "RecordId": 11}, {**reset, "RecordId": "bad"},
        {"Id": 1, "RecordId": 30, "ProviderName": "Microsoft-Windows-Power-Troubleshooter", "TimeCreated": at},
    ]
    power = reading("power", {}, [
        Section("raw", "raw", {"transitions": transitions}),
        Section("derived", "derived", {"ledger": {"counts": {"display driver reset": 13, "wake": 1},
                                               "window": {"first": at, "last": at}, "records": 14,
                                               "limit": 120, "limit_reached": False}}),
    ])
    leads, _ = take_signals_sync({"events": event_reading, "power": power})
    selected = {lead["id"]: lead for lead in leads if lead["id"] in ("pressure:Example", "transition:display-reset-near-wake")}
    if len(selected) != 2:
        raise RuntimeError("the synthetic pressure and reset leads did not both fire")
    print(f"Source version: {__version__}")
    for ident in ("pressure:Example", "transition:display-reset-near-wake"):
        lead = selected[ident]
        encoded = json.dumps(lead, ensure_ascii=False, separators=(",", ":")).encode()
        refs = lead["evidence"].get("refs", [])
        print(f"{ident}: {len(encoded)} JSON bytes, {len(refs)} inline references")


if __name__ == "__main__":
    main()
