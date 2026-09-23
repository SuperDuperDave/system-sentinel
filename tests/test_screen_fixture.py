"""The sanitized screen fixture must speak the same collector envelope as the live app."""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sentinel import readings  # noqa: F401
from sentinel.reading import take


def test_screen_fixture_answers_current_record_and_nearby_source_contracts():
    path = Path(__file__).parents[1] / "docs" / "screens" / "fixtures" / "fixture-server.py"
    spec = importlib.util.spec_from_file_location("sentinel_screen_fixture", path)
    assert spec and spec.loader
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    bridge = fixture.FixtureBridge()
    moment = datetime.now(UTC) - timedelta(minutes=2899.9)
    def stamp(time: datetime) -> str:
        return time.isoformat().replace("+00:00", "Z")

    recent = asyncio.run(take("events", bridge, {"log": "System", "levels": [1, 2], "count": 50}))
    before = asyncio.run(take("record", bridge, {"before": stamp(moment), "count": 25}))
    faults = asyncio.run(take("faults", bridge, {
        "since": stamp(moment - timedelta(hours=1)), "before": stamp(moment + timedelta(hours=1)), "count": 100,
    }))
    reports = asyncio.run(take("whea_reports", bridge, {
        "before": stamp(moment + timedelta(hours=1)), "hours": 2, "bucket_seconds": 60,
    }))

    assert recent.outcome == "ok" and recent.section("records").data
    assert before.outcome == "ok" and before.section("coverage").data["reaches_before"] is True
    assert faults.outcome == "ok" and faults.section("decoded").data
    assert faults.section("coverage").data["complete"] is True
    assert all(row["Log"] == "Application" for row in faults.section("records").data)
    assert reports.outcome == "ok" and [row["record_id"] for row in reports.section("reports").data] == [75]
    assert reports.section("coverage").data["kernel_whea"]["complete"] is True
