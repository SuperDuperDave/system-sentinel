"""Check exact log boundaries in disposable Windows PowerShell, before the row cap."""

from __future__ import annotations

import asyncio

import pytest

import sentinel.bridge
from sentinel.reading import take
from sentinel.readings.crash import faults_script
from sentinel.readings.event_coverage import exact_stamp
from sentinel.readings.events import events_script, record_script
from tests.conftest import FakeBridge, real_bridge_or_skip


def test_exact_stamp_keeps_the_seventh_digit_and_legacy_input_forms():
    assert exact_stamp("2026-09-22T02:00:00.1234567+02:00", "since", naive="local")[0] == "2026-09-22T00:00:00.1234567Z"
    assert exact_stamp("2026-09-22", "before", naive="local")[0].endswith(".0000000Z")
    assert exact_stamp("2026-09-22 00:00", "since", naive="local")[0].endswith(".0000000Z")
    assert exact_stamp("2026-09-22T02:00:00+02:00:00.123456", "since")[0] == "2026-09-21T23:59:59.8765440Z"
    for invalid in ("2026-09-22T00:00:00.12345678Z", "1599-01-01T00:00:00Z", "2026-09-22T000000.1234567Z",
                    "2026-09-22T02:00:00+02:00:00.1234567"):
        with pytest.raises(ValueError):
            exact_stamp(invalid, "since", naive="local")


def test_invalid_exact_bounds_are_refused_before_windows_runs():
    bridge = FakeBridge()
    for name, params in (("record", {"before": "2026-09-22T00:00:00.12345678Z"}),
                         ("events", {"since": "2026-09-22T00:00:00.12345678Z"}),
                         ("faults", {"before": "2026-09-22T00:00:00.12345678Z"})):
        with pytest.raises(ValueError):
            asyncio.run(take(name, bridge, params))
    assert bridge.scripts == []


@pytest.mark.host
def test_native_exact_filter_excludes_boundary_rows_before_the_count(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    fake = r"""
$boundary = [datetimeoffset]::Parse('2026-09-22T00:00:00.1234567Z', [cultureinfo]::InvariantCulture).UtcDateTime
$script:seen = 0
function Get-WinEvent {
    [CmdletBinding()]
    param([xml]$FilterXml, [string]$ListLog, [string]$LogName, [int]$MaxEvents, [switch]$Oldest)
    if ($ListLog) { [pscustomobject]@{ IsEnabled=$true; LogMode='Circular' }; return }
    if ($LogName) { [pscustomobject]@{ TimeCreated=$boundary.AddDays(-1) }; return }
    foreach ($at in @($boundary.AddTicks(1), $boundary, $boundary.AddTicks(-1), $boundary.AddTicks(-2), $boundary.AddDays(-1))) {
        $script:seen++
        [pscustomobject]@{
            RecordId=(100 - $script:seen); Id=1; Level=2; LevelDisplayName='Error'
            ProviderName='Application Error'; ProviderId=$null; Version=1; MachineName='SYNTHETIC'
            TaskDisplayName=$null; TimeCreated=$at; Message='Synthetic'; Properties=@(); LogName='Application'
        }
    }
}
"""
    bridge = real_bridge_or_skip()
    cases = (
        (record_script("System", "2026-09-22T00:00:00.1234567Z", 1), "2026-09-22T00:00:00.1234566Z", 4),
        (events_script("System", [], 1, "2026-09-22T00:00:00.1234567Z"), "2026-09-22T00:00:00.1234568Z", 2),
        (faults_script(1, "", "2026-09-22T00:00:00.1234567Z"), "2026-09-22T00:00:00.1234566Z", 4),
    )
    for culture in ("fi-FI", "th-TH"):
        locale = f"[Threading.Thread]::CurrentThread.CurrentCulture = [cultureinfo]::GetCultureInfo('{culture}')\n"
        for script, expected, examined in cases:
            result = bridge.run(locale + fake + script + "[pscustomobject]@{ seen=$script:seen; year=([datetime]::new(2026,9,22)).ToString('yyyy') }\n", depth=8)
            assert result.outcome == "ok" and len(result.items) == 2, result.error
            answer, control = result.items
            assert answer["outcome"] == "ok" and answer["returned"] == 1 and answer["truncated"] is True
            assert answer["records"][0]["TimeCreated"] == expected
            assert control["seen"] == examined and (culture != "th-TH" or control["year"] == "2569")


@pytest.mark.host
def test_native_oldest_fault_filter_excludes_preboundary_rows_before_the_cap(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    fake = r"""
$boundary = [datetimeoffset]::Parse('2026-09-22T00:00:00.1234567Z', [cultureinfo]::InvariantCulture).UtcDateTime
$script:seen = 0; $script:oldestAsked = $false
function Get-WinEvent {
    [CmdletBinding()]
    param([xml]$FilterXml, [string]$ListLog, [string]$LogName, [int]$MaxEvents, [switch]$Oldest)
    if ($ListLog) { [pscustomobject]@{ IsEnabled=$true; LogMode='Circular' }; return }
    if ($LogName) { [pscustomobject]@{ TimeCreated=$boundary.AddDays(-1) }; return }
    $script:oldestAsked = [bool]$Oldest
    foreach ($at in @($boundary.AddTicks(-2), $boundary.AddTicks(-1), $boundary, $boundary.AddTicks(1), $boundary.AddTicks(2))) {
        $script:seen++
        [pscustomobject]@{
            RecordId=$script:seen; Id=1000; Level=2; LevelDisplayName='Error'
            ProviderName='Application Error'; ProviderId=$null; Version=1; MachineName='SYNTHETIC'
            TaskDisplayName=$null; TimeCreated=$at; Message='Synthetic'; Properties=@(); LogName='Application'
        }
    }
}
"""
    bridge = real_bridge_or_skip()
    script = faults_script(1, "2026-09-22T00:00:00.1234567Z", "2026-09-22T00:00:01Z", "oldest")
    for culture in ("fi-FI", "th-TH"):
        locale = f"[Threading.Thread]::CurrentThread.CurrentCulture = [cultureinfo]::GetCultureInfo('{culture}')\n"
        result = bridge.run(locale + fake + script + "[pscustomobject]@{ seen=$script:seen; oldest=$script:oldestAsked }\n", depth=8)
        assert result.outcome == "ok" and len(result.items) == 2, result.error
        answer, control = result.items
        assert answer["outcome"] == "ok" and answer["returned"] == 1 and answer["truncated"] is True
        assert answer["records"][0]["TimeCreated"] == "2026-09-22T00:00:00.1234567Z"
        assert answer["probe_time"] == "2026-09-22T00:00:00.1234568Z"
        assert control == {"seen": 4, "oldest": True}


@pytest.mark.host
def test_native_system_moment_split_keeps_exact_boundary_only_on_the_after_side(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    fake = r"""
$boundary = [datetimeoffset]::Parse('2026-09-22T00:00:00.1234567Z', [cultureinfo]::InvariantCulture).UtcDateTime
function Get-WinEvent {
    [CmdletBinding()]
    param([xml]$FilterXml, [string]$ListLog, [string]$LogName, [int]$MaxEvents, [switch]$Oldest)
    if ($ListLog) { [pscustomobject]@{ IsEnabled=$true; LogMode='Circular' }; return }
    if ($LogName) { [pscustomobject]@{ TimeCreated=$boundary.AddDays(-1) }; return }
    $times = @($boundary.AddTicks(-1), $boundary, $boundary.AddTicks(1), $boundary.AddTicks(2))
    if (-not $Oldest) { [array]::Reverse($times) }
    foreach ($at in $times) {
        [pscustomobject]@{
            RecordId=($at.Ticks - $boundary.Ticks + 100); Id=1; Level=4; LevelDisplayName='Information'
            ProviderName='Synthetic'; ProviderId=$null; Version=1; MachineName='SYNTHETIC'
            TaskDisplayName=$null; TimeCreated=$at; Message='Synthetic'; Properties=@()
        }
    }
}
"""
    bridge = real_bridge_or_skip()
    moment = "2026-09-22T00:00:00.1234567Z"
    scripts = (
        (record_script("System", moment, 1), [99], False, False),
        (events_script("System", [], 1, moment, order="oldest"), [100], True, True),
    )
    for culture in ("fi-FI", "th-TH"):
        locale = f"[Threading.Thread]::CurrentThread.CurrentCulture = [cultureinfo]::GetCultureInfo('{culture}')\n"
        for script, ids, oldest, truncated in scripts:
            result = bridge.run(locale + fake + script, depth=8)
            assert result.outcome == "ok" and len(result.items) == 1, result.error
            answer = result.items[0]
            assert answer["outcome"] == "ok" and answer["truncated"] is truncated
            assert [row["RecordId"] for row in answer["records"]] == ids
            assert ("probe_time" in answer) is oldest


@pytest.mark.host
def test_native_exact_pipeline_keeps_absence_partial_work_and_failure_distinct(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    fake = r"""
$boundary = [datetimeoffset]::Parse('2026-09-22T00:00:00.1234567Z', [cultureinfo]::InvariantCulture).UtcDateTime
function New-Event([long]$id, [datetime]$at) {
    [pscustomobject]@{ RecordId=$id; Id=1; Level=2; ProviderName='synthetic'; ProviderId=$null
        TimeCreated=$at; Message='Synthetic'; Properties=@() }
}
function Get-WinEvent {
    [CmdletBinding()]
    param([xml]$FilterXml, [string]$ListLog, [string]$LogName, [int]$MaxEvents)
    if ($ListLog) { [pscustomobject]@{ IsEnabled=$true; LogMode='Circular' }; return }
    if ($LogName) { [pscustomobject]@{ TimeCreated=$boundary.AddDays(-1) }; return }
    if ($scenario -eq 'no_match') {
        $errorRecord = [System.Management.Automation.ErrorRecord]::new(
            [System.Exception]::new('Synthetic no match'), 'NoMatchingEventsFound',
            [System.Management.Automation.ErrorCategory]::ObjectNotFound, $null)
        $PSCmdlet.ThrowTerminatingError($errorRecord)
    }
    if ($scenario -eq 'boundary_only') { New-Event 3 $boundary; New-Event 2 $boundary.AddTicks(1); return }
    if ($scenario -eq 'partial') { New-Event 1 $boundary.AddTicks(-1) }
    else { New-Event 3 $boundary }
    throw 'Synthetic interrupted query'
}
"""
    bridge = real_bridge_or_skip()
    script = record_script("System", "2026-09-22T00:00:00.1234567Z", 2)
    for scenario, outcome, returned, truncated, stopped in (
        ("no_match", "empty", 0, False, False),
        ("boundary_only", "empty", 0, False, False),
        ("partial", "ok", 1, None, True),
        ("boundary_then_error", "failed", 0, None, False),
    ):
        result = bridge.run(f"$scenario = '{scenario}'\n" + fake + script, depth=8)
        assert result.outcome == "ok" and len(result.items) == 1, result.error
        answer = result.items[0]
        assert (answer["outcome"], answer["returned"], answer["truncated"], bool(answer["stopped"])) == (
            outcome, returned, truncated, stopped)
