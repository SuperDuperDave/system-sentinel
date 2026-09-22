"""Exercise crash collection with scoped synthetic event queries in disposable PowerShell.

No real event-log or dump-inventory query runs. The replacement functions exist only inside
one invocation, and the process exits after that query without changing Windows state.
"""

from __future__ import annotations

import pytest

import sentinel.bridge
from sentinel.reading import Reading
from sentinel.readings import crash
from tests.conftest import real_bridge_or_skip

pytestmark = pytest.mark.host

MOMENT = "2020-01-02T03:00:00Z"

EVENT_HELPER = r"""
function New-SyntheticCrashEvent {
    param([long]$RecordId, [int]$Id, [string]$Provider, [string]$Log, [datetime]$At, [object[]]$Values)
    [pscustomobject]@{
        RecordId = $RecordId; Id = $Id; ProviderName = $Provider; LogName = $Log
        TimeCreated = $At; Level = 4; LevelDisplayName = 'Information'
        MachineName = 'SYNTHETIC'; TaskDisplayName = $null; Message = 'Synthetic crash evidence'
        Properties = @($Values | ForEach-Object { [pscustomobject]@{ Value = $_ } })
    }
}
"""

SYSTEM_STOP = r"""
New-SyntheticCrashEvent -RecordId 100 -Id 12 -Provider 'Microsoft-Windows-Kernel-General' -Log 'System' -At '2020-01-02T03:04:00Z' -Values @(10,0,0,0,0,0,'2020-01-02T03:04:00Z')
New-SyntheticCrashEvent -RecordId 101 -Id 41 -Provider 'Microsoft-Windows-Kernel-Power' -Log 'System' -At '2020-01-02T03:04:03Z' -Values @(0,0,0,0,0,$false)
"""

REPORT = r"""
New-SyntheticCrashEvent -RecordId 200 -Id 1001 -Provider 'Windows Error Reporting' -Log 'Application' -At '2020-01-02T03:05:00Z' -Values @('synthetic-bucket',0,'BlueScreen','Not available',0,'133','1','2','3','4')
"""

NO_MATCH = r"""
$exception = [System.Exception]::new('No events were found that match the specified selection criteria.')
$failure = [System.Management.Automation.ErrorRecord]::new($exception, 'NoMatchingEventsFound', [System.Management.Automation.ErrorCategory]::ObjectNotFound, $null)
$PSCmdlet.ThrowTerminatingError($failure)
"""


def _body(mode: str, source: str) -> str:
    if mode == "empty":
        return "return"
    if mode == "no_match":
        return NO_MATCH
    if mode == "denied":
        return f"throw [System.UnauthorizedAccessException]::new('Access is denied: synthetic crash {source}')"
    failure = f"throw 'synthetic crash {source} query failure'"
    if mode == "failed":
        return failure
    if source == "system" and mode == "duplicate_stop":
        return SYSTEM_STOP + "\nNew-SyntheticCrashEvent -RecordId 102 -Id 41 -Provider 'Microsoft-Windows-Kernel-Power' -Log 'System' -At '2020-01-02T03:04:04Z' -Values @(0,0,0,0,0,$false)"
    if source == "system" and mode in ("stop", "partial_failed"):
        return SYSTEM_STOP + (failure if mode == "partial_failed" else "")
    if source == "reports" and mode == "report":
        return REPORT
    raise ValueError(f"unexpected synthetic mode: {source}/{mode}")


def _take(monkeypatch: pytest.MonkeyPatch, system: str, reports: str, *, before: str = "no_match", moment: str = "", count: int = 1) -> Reading:
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    monkeypatch.setattr(crash, "DUMPS_SCRIPT", "return")
    # CmdletBinding supplies ErrorAction; no branch delegates to the real Get-WinEvent.
    # The XML text distinguishes the two primary queries from the per-anchor lookup.
    script = (
        "& {\n" + EVENT_HELPER + "\nfunction Get-WinEvent {\n"
        "[CmdletBinding()] param([xml]$FilterXml, [int]$MaxEvents, [switch]$Oldest)\n"
        "$query = $FilterXml.QueryList.Query\n"
        "$log = [string]$query.Path\n"
        "$text = [string]$query.InnerText\n"
        "if ($log -eq 'System' -and $text.Contains(\"Provider[@Name='Microsoft-Windows-Kernel-Power']\")) {\n"
        + _body(system, "system") + "\n}\n"
        "elseif ($log -eq 'Application' -and $text.Contains(\"EventData[Data[@Name='EventName']='BlueScreen']\")) {\n"
        + _body(reports, "reports") + "\n}\n"
        "elseif ($log -eq 'System' -and $MaxEvents -eq 1 -and $text.Contains(\"TimeCreated[@SystemTime<'\")) {\n"
        + _body(before, "before") + "\n}\n"
        "else { throw 'unexpected synthetic crash query' }\n}\n"
        + crash.CRASH_SCRIPT_TEMPLATE + "\n}\n"
    )
    monkeypatch.setattr(crash, "CRASH_SCRIPT_TEMPLATE", script)
    return crash.take_crash(real_bridge_or_skip(), {"count": count, "moment": moment})


def _assert_primary(reading: Reading, name: str, mode: str, *, moment: str = "") -> None:
    source = reading.section("collection").data[name]
    expected = "ok" if mode in ("stop", "report") else "empty" if mode == "no_match" else "failed" if mode == "partial_failed" else mode
    returned = 2 if mode == "stop" else 1 if mode == "report" else 0
    assert source["outcome"] == expected, source
    assert source["returned"] == returned
    assert source["limit"] == (crash.record_cap(1, moment or None) if name == "system" else 9)
    assert source["bound_reached"] is (False if expected in ("ok", "empty") else None)
    log = "System" if name == "system" else "Application"
    assert len([row for row in reading.section("records").data if row["Log"] == log]) == returned
    if expected in ("failed", "denied"):
        assert f"synthetic crash {name}" in source["error"]
    else:
        assert source["error"] is None


@pytest.mark.parametrize(("system", "reports", "outcome"), [
    ("failed", "failed", "failed"),
    ("denied", "denied", "denied"),
    ("no_match", "no_match", "empty"),
    ("empty", "empty", "empty"),
    ("empty", "failed", "failed"),
    ("partial_failed", "empty", "failed"),
])
def test_primary_queries_distinguish_absence_from_failure(monkeypatch, system, reports, outcome):
    reading = _take(monkeypatch, system, reports)
    assert reading.outcome == outcome, (reading.error, reading.warnings)
    assert reading.observed is (outcome == "empty")
    assert reading.count == (0 if outcome == "empty" else None)
    assert reading.section("records").data == []
    assert reading.section("stops").data == []
    assert reading.section("collection").data["before"] == []
    _assert_primary(reading, "system", system)
    _assert_primary(reading, "reports", reports)
    if outcome == "empty":
        assert reading.error is None and reading.warnings == []
    else:
        assert reading.error is not None and reading.error["kind"] == outcome
        assert reading.warnings


def test_system_stop_survives_failed_reports_without_claiming_no_bugcheck(monkeypatch):
    reading = _take(monkeypatch, "stop", "failed")
    assert reading.outcome == "ok" and reading.count == 1, (reading.error, reading.warnings)
    assert reading.error is None and reading.warnings
    _assert_primary(reading, "system", "stop")
    _assert_primary(reading, "reports", "failed")
    stop = reading.section("stops").data[0]
    assert stop["records"]["start"] == 100 and stop["records"]["power_41"] == 101
    assert stop["no_bugcheck_recorded"] is not True
    before = reading.section("collection").data["before"]
    assert len(before) == 1 and before[0]["outcome"] == "empty"
    assert before[0]["returned"] == 0 and before[0]["error"] is None


@pytest.mark.parametrize("moment", ["", MOMENT], ids=["count", "moment"])
def test_report_only_stop_survives_failed_system_query(monkeypatch, moment):
    reading = _take(monkeypatch, "failed", "report", moment=moment)
    assert reading.outcome == "ok" and reading.count == 1, (reading.error, reading.warnings)
    assert reading.error is None and reading.warnings
    _assert_primary(reading, "system", "failed", moment=moment)
    _assert_primary(reading, "reports", "report", moment=moment)
    stop = reading.section("stops").data[0]
    assert stop["records"]["report"] == [200]
    assert stop["started_at"] is None and stop["announced_at"] is None
    assert stop["reported_at"] == "2020-01-02T03:05:00.000Z"
    assert stop["bugcheck"]["code"] == "0x133"
    assert reading.section("collection").data["before"] == []
    assert not any("no start follows" in warning for warning in reading.warnings)


def test_auxiliary_denial_preserves_stop_and_identifies_the_unread_anchor(monkeypatch):
    reading = _take(monkeypatch, "stop", "empty", before="denied")
    assert reading.outcome == "ok" and reading.count == 1, (reading.error, reading.warnings)
    assert reading.error is None and reading.warnings
    _assert_primary(reading, "system", "stop")
    _assert_primary(reading, "reports", "empty")
    stop = reading.section("stops").data[0]
    assert stop["records"]["power_41"] == 101
    assert stop["last_record_before"] is None and stop["quiet_seconds"] is None
    before = reading.section("collection").data["before"]
    assert len(before) == 1
    assert before[0]["anchor"] == 100 and before[0]["outcome"] == "denied"
    start = next(row for row in reading.section("records").data if row["RecordId"] == 100)
    assert before[0]["at"] == start["TimeCreated"]
    assert before[0]["returned"] == 0 and "synthetic crash before" in before[0]["error"]


def test_repeated_power_records_query_their_shared_start_only_once(monkeypatch):
    reading = _take(monkeypatch, "duplicate_stop", "empty", count=2)
    assert reading.outcome == "ok" and reading.count == 1
    attempts = reading.section("collection").data["before"]
    assert len(attempts) == 1 and attempts[0]["anchor"] == 100
    assert reading.section("stops").data[0]["last_record_collection"]["outcome"] == "empty"
