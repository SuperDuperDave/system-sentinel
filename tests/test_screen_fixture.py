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
        "before": stamp(moment + timedelta(hours=1)), "hours": 2, "bucket_seconds": 60, "references": True,
    }))
    report_window = asyncio.run(take("whea_window", bridge, {
        "source": "kernel_whea", "since": stamp(moment - timedelta(hours=1)),
        "before": stamp(moment + timedelta(hours=1)), "order": "newest", "count": 500,
    }))
    whea = asyncio.run(take("whea", bridge, {"count": 30}))
    storms = asyncio.run(take("storms", bridge, {
        "before": stamp(datetime.fromtimestamp(fixture.WHEA_ANCHOR - 1, UTC)), "hours": 24, "bucket_seconds": 60,
    }))

    assert recent.outcome == "ok" and recent.section("records").data
    assert before.outcome == "ok" and before.section("coverage").data["reaches_before"] is True
    assert faults.outcome == "ok" and faults.section("decoded").data
    assert faults.section("coverage").data["complete"] is True
    assert all(row["Log"] == "Application" for row in faults.section("records").data)
    assert reports.outcome == "ok" and [row["record_id"] for row in reports.section("reports").data] == [75]
    assert reports.section("coverage").data["kernel_whea"]["complete"] is True
    assert report_window.outcome == "ok" and [row["RecordId"] for row in report_window.section("records").data] == [75]
    assert report_window.section("coverage").data["complete"] is True
    window_collection = report_window.section("collection").data
    assert window_collection["observed_end"].endswith("+00:00") and window_collection["window_end"].endswith("Z")
    assert datetime.fromisoformat(window_collection["observed_end"]) == datetime.fromisoformat(window_collection["window_end"].replace("Z", "+00:00"))
    assert whea.outcome == "ok" and whea.section("decoded") is None
    selected = whea.section("records").data[0]
    assert "RawData" not in selected and "Properties" not in selected
    exact = asyncio.run(take("whea_record", bridge, {"source": "system" if selected["Log"] == "System" else "kernel_whea", "record_id": selected["RecordId"]}))
    assert exact.outcome == "ok" and exact.section("records").data[0]["TimeCreated"] == selected["TimeCreated"]
    assert storms.outcome == "ok" and storms.count > 0
    assert storms.section("buckets").data["total"] == storms.count
    assert storms.section("status") is None
    assert storms.section("coverage").data["system"]["covered_until"] == storms.section("collection").data["window_end"]


def test_screen_fixture_can_show_both_sides_when_later_reports_fill_the_cap(monkeypatch):
    path = Path(__file__).parents[1] / "docs" / "screens" / "fixtures" / "fixture-server.py"
    spec = importlib.util.spec_from_file_location("sentinel_screen_fixture_crowded", path)
    assert spec and spec.loader
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    moment = datetime.fromtimestamp(fixture.WHEA_ANCHOR - 90 * 60, UTC)
    base = fixture.kernel_whea_records(fixture.WHEA_ANCHOR)[0]
    def stamp(at: datetime) -> str:
        return at.isoformat(timespec="microseconds").replace("+00:00", "Z")

    earlier = [{**base, "RecordId": index, "TimeCreated": stamp(moment - timedelta(seconds=offset))}
               for index, offset in ((1, 2), (2, 1), (3, 0.1))]
    later = [{**base, "RecordId": 1000 + index, "TimeCreated": stamp(moment + timedelta(seconds=index / 10))}
             for index in range(600)]
    rows = sorted([*earlier, *later], key=lambda row: row["TimeCreated"], reverse=True)
    monkeypatch.setattr(fixture, "kernel_whea_records", lambda now: rows)
    common = {"source": "kernel_whea", "count": 250}
    before = asyncio.run(take("whea_window", fixture.FixtureBridge(), {
        **common, "since": stamp(moment - timedelta(hours=1)), "before": stamp(moment), "order": "newest",
    }))
    after = asyncio.run(take("whea_window", fixture.FixtureBridge(), {
        **common, "since": stamp(moment), "before": stamp(moment + timedelta(hours=1)), "order": "oldest",
    }))
    assert before.outcome == "ok" and {row["RecordId"] for row in before.section("records").data} == {1, 2, 3}
    assert before.section("coverage").data["complete"] is True
    assert after.outcome == "ok" and [row["RecordId"] for row in after.section("records").data] == list(range(1000, 1250))
    assert after.section("collection").data["truncated"] is True
    assert after.section("coverage").data["covered_until"] == after.section("collection").data["probe_time"]
