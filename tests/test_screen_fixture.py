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
    after = asyncio.run(take("events", bridge, {"log": "System", "levels": [], "since": stamp(moment), "order": "oldest", "count": 25}))
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
    assert after.outcome == "ok" and after.count == 25
    assert after.section("collection").data["order"] == "oldest" and after.section("coverage").data["covered_until"] == after.section("collection").data["probe_time"]
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


def test_screen_fixture_keeps_faults_on_both_sides_of_a_crowded_restart(monkeypatch):
    path = Path(__file__).parents[1] / "docs" / "screens" / "fixtures" / "fixture-server.py"
    spec = importlib.util.spec_from_file_location("sentinel_screen_fixture_fault_burst", path)
    assert spec and spec.loader
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    monkeypatch.setenv("SENTINEL_FIXTURE_CROWDED_FAULTS", "1")
    moment = fixture._powershell_stamp(datetime.now(UTC).timestamp() - 2900 * 60)
    center = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    common = {"count": 50}
    before = asyncio.run(take("faults", fixture.FixtureBridge(), {
        **common, "since": fixture._powershell_stamp(center.timestamp() - 3600),
        "before": moment, "order": "newest",
    }))
    after = asyncio.run(take("faults", fixture.FixtureBridge(), {
        **common, "since": moment, "before": fixture._powershell_stamp(center.timestamp() + 3600),
        "order": "oldest",
    }))
    before_ids = [row["RecordId"] for row in before.section("records").data]
    after_ids = [row["RecordId"] for row in after.section("records").data]
    assert {5000, 5001, 5002} <= set(before_ids)
    assert after_ids == list(range(6000, 6050))
    assert after.section("collection").data["truncated"] is True
    assert after.section("coverage").data["covered_until"] == after.section("collection").data["probe_time"]


def test_screen_fixture_places_seventh_digit_faults_on_only_one_side(monkeypatch):
    path = Path(__file__).parents[1] / "docs" / "screens" / "fixtures" / "fixture-server.py"
    spec = importlib.util.spec_from_file_location("sentinel_screen_fixture_exact_fault", path)
    assert spec and spec.loader
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    base = (datetime.now(UTC) - timedelta(minutes=2900)).strftime("%Y-%m-%dT%H:%M:%S")
    moment = f"{base}.1234567Z"
    earlier = f"{base}.1234566Z"
    center = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    template = next(row for row in fixture.fault_records(datetime.now(UTC).timestamp()) if row["Id"] == 1000)
    monkeypatch.setattr(fixture, "fault_records", lambda now: [
        {**template, "RecordId": 9000, "TimeCreated": earlier},
        {**template, "RecordId": 9001, "TimeCreated": moment},
    ])
    before = asyncio.run(take("faults", fixture.FixtureBridge(), {
        "since": fixture._powershell_stamp((center - timedelta(hours=1)).timestamp()), "before": moment,
        "order": "newest", "count": 1,
    }))
    after = asyncio.run(take("faults", fixture.FixtureBridge(), {
        "since": moment, "before": fixture._powershell_stamp((center + timedelta(hours=1)).timestamp()),
        "order": "oldest", "count": 1,
    }))
    assert [row["RecordId"] for row in before.section("records").data] == [9000]
    assert [row["RecordId"] for row in after.section("records").data] == [9001]


def test_screen_fixture_keeps_a_report_with_its_two_returned_records(monkeypatch):
    path = Path(__file__).parents[1] / "docs" / "screens" / "fixtures" / "fixture-server.py"
    spec = importlib.util.spec_from_file_location("sentinel_screen_fixture_grouped_fault", path)
    assert spec and spec.loader
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    monkeypatch.setenv("SENTINEL_FIXTURE_GROUPED_FAULTS", "1")
    moment = datetime.now(UTC).timestamp() - 2900 * 60
    reading = asyncio.run(take("faults", fixture.FixtureBridge(), {
        "since": fixture._powershell_stamp(moment), "before": fixture._powershell_stamp(moment + 3600),
        "order": "oldest", "count": 50,
    }))
    report = next(entry for entry in reading.section("decoded").data if entry.get("report"))
    assert report["RecordId"] == 7001
    assert report["report"]["records"] == [7000, 7001]
    assert [row["RecordId"] for row in reading.section("records").data][:2] == [7000, 7100]


def test_screen_fixture_composes_the_synthetic_stops_through_the_crash_reading():
    path = Path(__file__).parents[1] / "docs" / "screens" / "fixtures" / "fixture-server.py"
    spec = importlib.util.spec_from_file_location("sentinel_screen_fixture", path)
    assert spec and spec.loader
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    bridge = fixture.FixtureBridge()

    newest = asyncio.run(take("crash", bridge, {"count": 5}))
    assert newest.outcome == "ok" and not newest.warnings
    assert len(newest.section("stops").data) == 5
    assert newest.section("coverage").data

    forward = asyncio.run(take("crash", bridge, {"count": 5, "moment": "2026-09-09T09:00:00Z"}))
    assert forward.outcome == "ok"
    assert all(stop["started_at"] >= "2026-09-09T09:00:00Z" for stop in forward.section("stops").data if stop["started_at"])


def test_screen_fixture_answers_the_machine_and_process_readings_with_synthetic_parts():
    path = Path(__file__).parents[1] / "docs" / "screens" / "fixtures" / "fixture-server.py"
    spec = importlib.util.spec_from_file_location("sentinel_screen_fixture", path)
    assert spec and spec.loader
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    bridge = fixture.FixtureBridge()
    for name in ("system", "hardware", "hardware.cpu", "hardware.gpu", "hardware.board", "hardware.storage", "hardware.network", "processes"):
        assert asyncio.run(take(name, bridge, {})).outcome == "ok", name
