"""Kernel-WHEA report times stay separate from hardware-error occurrence claims."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sentinel import readings  # noqa: F401
from sentinel.bridge import BridgeResult
from sentinel.reading import take
from sentinel.readings import whea, whea_reports
from tests.conftest import real_bridge_or_skip

NOW = datetime(2026, 9, 23, 6, 30, 45, tzinfo=UTC)


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond:06d}0Z"


def header(previous: bool = False, *, valid: bool = True) -> str:
    data = bytearray(128)
    data[:4] = b"CPER" if valid else b"NOPE"
    data[6:10] = b"\xff" * 4
    data[10:12] = (1).to_bytes(2, "little")
    data[12:16] = (1).to_bytes(4, "little")
    data[20:24] = (200).to_bytes(4, "little")
    data[104:108] = (2 if previous else 0).to_bytes(4, "little")
    return data.hex().upper()


def row(record_id: int, at: datetime | None, *, previous: bool = False, valid_header: bool = True) -> dict[str, Any]:
    return {"RecordId": record_id, "Id": 20, "ProviderName": whea.CHANNEL_PROVIDER, "LogName": whea.CHANNEL,
            "TimeCreated": stamp(at) if at else None, "HeaderHex": header(previous, valid=valid_header), "PayloadBytes": 200}


def reports(rows: list[dict[str, Any]], *, outcome: str | None = None, oldest: datetime | None = None,
            stopped: dict[str, str] | None = None, truncated: bool = False, hours: int = 1,
            bucket_seconds: int = 60, before: str = "", payload_hook: Callable[[dict[str, Any]], None] | None = None) -> Any:
    class Bridge:
        def run(self, script: str, *, depth: int = 6) -> BridgeResult:
            width = int(re.search(r"\$bucketTicks = \[long\](\d+)", script).group(1))
            count = int(re.search(r"\(\[long\]\((\d+) - 1\)", script).group(1))
            assigned = re.search(r"\$requestedUntil = \[datetimeoffset\]::Parse\('([^']+)'\)", script)
            requested_end = datetime.fromisoformat(assigned.group(1).replace("Z", "+00:00")) if assigned else NOW
            end = min(requested_end, NOW)
            last = int((end - timedelta(microseconds=1)).timestamp() // width) * width
            start = datetime.fromtimestamp(last - (count - 1) * width, UTC)
            source = {
                "log": whea.CHANNEL, "outcome": outcome or ("ok" if rows else "empty"), "error": "synthetic source failure" if outcome in ("failed", "denied") else None,
                "returned": len(rows), "limit": whea_reports.RECORD_CAP, "truncated": None if stopped else truncated,
                "stopped": stopped, "records": rows, "log_enabled": True, "log_mode": "Circular", "log_state": "ok",
                "log_error": None, "log_oldest": stamp(oldest or start - timedelta(seconds=1)),
                "oldest_state": "ok", "oldest_error": None,
            }
            payload = {"window_start": stamp(start), "window_end": stamp(end), "queried_at": stamp(NOW), "source": source}
            if payload_hook:
                payload_hook(payload)
            return BridgeResult("ok", items=[payload])

    return asyncio.run(take("whea_reports", Bridge(), {"hours": hours, "bucket_seconds": bucket_seconds, "before": before}))


def sections(reading: Any) -> dict[str, Any]:
    return {section.name: section.data for section in reading.sections}


def test_previous_session_reports_are_counted_by_report_time_without_error_rate_status():
    reading = reports([row(4, NOW - timedelta(minutes=2), previous=True), row(5, NOW - timedelta(minutes=1))])
    data = sections(reading)
    assert reading.outcome == "ok" and reading.count == 2
    assert data["coverage"]["kernel_whea"]["complete"] is True
    assert data["buckets"]["total"] == 2 and data["buckets"]["previous_session"] == 1
    assert data["buckets"]["header_unreadable"] == 0 and data["buckets"]["unknown_buckets"] == 0
    assert data["reports"][0]["header"] == {"severity": "fatal", "previous_session": True}
    assert data["reports"][1]["header"]["previous_session"] is False
    assert "status" not in data and "signatures" not in data
    assert "report times" in reading.section("buckets").basis


def test_empty_channel_is_only_quiet_when_retention_covers_window():
    complete = sections(reports([]))
    assert complete["buckets"]["total"] == 0
    assert complete["buckets"]["unknown_buckets"] == 0
    partial = sections(reports([], oldest=NOW - timedelta(minutes=20)))
    assert partial["coverage"]["kernel_whea"]["complete"] is False
    assert partial["buckets"]["unknown_buckets"] > 0
    assert any(value is None for value in partial["buckets"]["totals"])


def test_unreadable_header_does_not_erase_report_and_unplaced_time_prevents_quiet():
    reading = reports([row(6, NOW - timedelta(minutes=3), valid_header=False), row(7, None, previous=True)])
    data = sections(reading)
    assert reading.outcome == "ok" and reading.count == 2
    assert data["reports"][0]["header"] is None and data["reports"][0]["header_error"]
    assert data["buckets"]["header_unreadable"] == 1 and data["buckets"]["unplaced"] == 1
    assert data["buckets"]["previous_session"] == 1  # the unplaced report still carries its header fact
    assert data["buckets"]["unknown_buckets"] == data["buckets"]["bucket_count"]
    assert data["coverage"]["kernel_whea"]["complete"] is False
    assert any("PreviousError flags are unknown" in warning for warning in reading.warnings)


def test_failed_and_stopped_channel_keep_outcome_and_coverage_explicit():
    failed = reports([], outcome="denied")
    assert failed.outcome == "denied" and failed.count is None
    assert "buckets" not in sections(failed)
    stopped = reports([row(8, NOW - timedelta(minutes=1), previous=True)], stopped={"kind": "failed", "detail": "synthetic early stop"})
    data = sections(stopped)
    assert stopped.outcome == "ok" and data["collection"]["kernel_whea"]["stopped"]["kind"] == "failed"
    assert data["coverage"]["kernel_whea"]["complete"] is False
    assert data["buckets"]["previous_session"] == 1


def test_missing_header_projection_cannot_become_a_clean_channel_read():
    broken = row(9, NOW - timedelta(minutes=1))
    del broken["HeaderHex"]
    reading = reports([broken])
    assert reading.outcome == "failed" and reading.count is None
    assert "buckets" not in sections(reading)


def test_report_query_is_bounded_and_projects_only_the_fixed_header():
    script = whea_reports.reports_script(whea.window_for(1, 60, now=0))
    assert f"-MaxEvents {whea_reports.RECORD_CAP + 1}" in script
    assert "[Math]::Min(128, $bytes.Length)" in script
    assert "Message = $event.Message" not in script
    assert whea.CHANNEL in script and whea.CHANNEL_PROVIDER in script


def test_anchored_report_window_ends_before_a_bucket_boundary():
    before = "2026-09-23T06:00:00Z"
    reading = reports([row(10, datetime(2026, 9, 23, 5, 59, tzinfo=UTC), previous=True)], before=before)
    data = sections(reading)
    assert reading.outcome == "ok" and reading.count == 1
    assert data["collection"]["window_end"] == "2026-09-23T06:00:00.0000000Z"
    assert data["buckets"]["from"] == "2026-09-23T05:00:00.000Z"
    assert data["buckets"]["to"] == "2026-09-23T06:00:00.000Z"
    assert data["coverage"]["kernel_whea"]["complete"] is True
    assert "[datetimeoffset]::Parse('2026-09-23T06:00:00.000Z')" in whea_reports.reports_script(whea.window_for(1, 60, now=0), before)


def test_future_report_anchor_preserves_the_requested_gap():
    reading = reports([], before="2026-09-23T07:00:00+00:00")
    data = sections(reading)
    assert data["collection"]["window_end"] == stamp(NOW)
    assert data["coverage"]["kernel_whea"]["complete"] is False
    assert data["coverage"]["kernel_whea"]["covered_until"] == stamp(NOW)
    assert any("after the machine's query time" in warning for warning in reading.warnings)


def test_report_window_entirely_after_the_host_clock_fails_instead_of_shifting_to_unrelated_rows():
    reading = reports([], before="2026-09-23T08:00:00Z")
    assert reading.outcome == "failed" and reading.count is None
    assert "begins at or after" in reading.error["detail"]
    assert "reports" not in sections(reading)


@pytest.mark.parametrize("hook", [
    lambda payload: payload.pop("queried_at"),
    lambda payload: payload.update(window_end=stamp(NOW + timedelta(seconds=1))),
])
def test_report_collector_cannot_claim_a_window_beyond_its_query_clock(hook):
    reading = reports([], payload_hook=hook)
    assert reading.outcome == "failed" and reading.count is None
    assert "reports" not in sections(reading)


def test_anchor_boundary_rows_stay_out_of_the_counts_with_the_narrow_xpath_tolerance():
    end = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
    reading = reports([row(10, end), row(11, end + timedelta(microseconds=500))], before="2026-09-23T06:00:00Z")
    data = sections(reading)
    assert reading.outcome == "empty" and reading.count == 0
    assert data["collection"]["kernel_whea"]["row_issues"]["outside_window"] == 2
    assert data["coverage"]["kernel_whea"]["complete"] is True


def test_unaligned_report_anchor_discloses_its_actual_partial_bucket():
    reading = reports([], before="2026-09-23T06:12:37.500999Z")
    data = sections(reading)
    assert data["collection"]["window_start"] == "2026-09-23T05:13:00.0000000Z"
    assert data["collection"]["window_end"] == "2026-09-23T06:12:37.5000000Z"
    assert data["buckets"]["to"] == "2026-09-23T06:13:00.000Z"


def test_report_anchor_requires_an_explicit_time_zone():
    with pytest.raises(ValueError, match="parameter 'before'"):
        whea_reports.reports_script(whea.window_for(1, 60, now=0), "2026-09-23T06:00:00")


@pytest.mark.host
def test_kernel_report_query_answers_from_the_windows_channel():
    reading = asyncio.run(take("whea_reports", real_bridge_or_skip(), {"hours": 24, "bucket_seconds": 60}))
    if reading.outcome == "unavailable" and "WSL could not start" in str((reading.error or {}).get("detail", "")):
        pytest.skip("WSL's interop layer did not start PowerShell")
    assert reading.outcome in ("ok", "empty"), reading.error
    data = sections(reading)
    source = data["collection"]["kernel_whea"]
    buckets = data["buckets"]
    assert source["outcome"] in ("ok", "empty") and source["returned"] == reading.count
    assert len(buckets["totals"]) == buckets["bucket_count"] == 1440
    assert buckets["total"] + buckets["unplaced"] == reading.count
    assert buckets["previous_session"] + buckets["header_unreadable"] <= reading.count


@pytest.mark.host
@pytest.mark.parametrize("seconds", [0, 17])
def test_windows_report_query_can_end_at_an_old_bucket_boundary_or_inside_a_bucket(seconds: int):
    before = ((datetime.now(UTC) - timedelta(hours=1)).replace(second=0, microsecond=0) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
    reading = asyncio.run(take("whea_reports", real_bridge_or_skip(), {"hours": 2, "bucket_seconds": 60, "before": before}))
    if reading.outcome == "unavailable" and "WSL could not start" in str((reading.error or {}).get("detail", "")):
        pytest.skip("WSL's interop layer did not start PowerShell")
    assert reading.outcome in ("ok", "empty"), reading.error
    data = sections(reading)
    assert whea.stamp_key(data["collection"]["window_end"]) == whea.stamp_key(before)
    if seconds == 0:
        assert whea.stamp_key(data["buckets"]["to"]) == whea.stamp_key(before)
    else:
        assert whea.stamp_key(data["buckets"]["to"]) > whea.stamp_key(before)


@pytest.mark.host
def test_windows_report_projection_preserves_rows_before_an_interruption(monkeypatch: pytest.MonkeyPatch):
    import sentinel.bridge

    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)  # fake PowerShell function stays in this one call
    fake = r"""
function Get-WinEvent {
    [CmdletBinding()]
    param([xml]$FilterXml, [string]$ListLog, [string]$LogName, [switch]$Oldest, [int]$MaxEvents)
    if ($ListLog) { [pscustomobject]@{ IsEnabled = $true; LogMode = 'Circular' }; return }
    if ($Oldest) { [pscustomobject]@{ TimeCreated = [datetime]::UtcNow.AddDays(-2) }; return }
    $bytes = New-Object byte[] 200
    [Array]::Copy([byte[]](0x43,0x50,0x45,0x52), $bytes, 4)
    for ($j = 6; $j -lt 10; $j++) { $bytes[$j] = 0xff }
    [Array]::Copy([BitConverter]::GetBytes([uint16]1), 0, $bytes, 10, 2)
    [Array]::Copy([BitConverter]::GetBytes([uint32]1), 0, $bytes, 12, 4)
    [Array]::Copy([BitConverter]::GetBytes([uint32]200), 0, $bytes, 20, 4)
    [Array]::Copy([BitConverter]::GetBytes([uint32]2), 0, $bytes, 104, 4)
    $base = [datetime]::UtcNow.AddSeconds(-1)
    for ($i = 0; $i -lt 3; $i++) {
        [pscustomobject]@{
            RecordId = [int64](3 - $i); Id = 20; ProviderName = 'Microsoft-Windows-Kernel-WHEA'
            LogName = 'Microsoft-Windows-Kernel-WHEA/Errors'; TimeCreated = $base.AddMinutes(-$i)
            Properties = @([pscustomobject]@{ Value = [uint32]3 }, [pscustomobject]@{ Value = $bytes })
        }
    }
    Write-Error 'synthetic interruption' -ErrorAction Stop
}
"""
    result = real_bridge_or_skip().run(fake + whea_reports.reports_script(whea.window_for(24, 60, now=0)))
    assert result.outcome == "ok" and len(result.items) == 1, result
    source = result.items[0]["source"]
    assert source["outcome"] == "ok" and source["returned"] == 3
    assert source["stopped"] == {"kind": "failed", "detail": "synthetic interruption"}
    assert all(row["PayloadBytes"] == 200 and len(row["HeaderHex"]) == 256 for row in source["records"])
    reading = whea_reports.take_reports(type("Canned", (), {"run": lambda self, script: result})(), {"hours": 24, "bucket_seconds": 60})
    assert reading.outcome == "ok" and reading.count == 3, (reading.error, sections(reading)["collection"])
    assert sections(reading)["buckets"]["previous_session"] == 3
    assert sections(reading)["coverage"]["kernel_whea"]["complete"] is False
