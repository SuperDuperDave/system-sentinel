"""Exercise application path guards with synthetic Windows answers and no real file query."""

from __future__ import annotations

import ntpath

import pytest

import sentinel.bridge
from sentinel.readings import dumps
from tests.conftest import real_bridge_or_skip
from tests.test_dump_inventory import APPLICATION_ROOT

pytestmark = pytest.mark.host

_BASE = ntpath.dirname(APPLICATION_ROOT)
_ANCESTORS = [
    "C:\\", r"C:\Users", r"C:\Users\example-user", r"C:\Users\example-user\AppData",
    _BASE, APPLICATION_ROOT,
]

_REPLACEMENTS = r"""
$trace = @{
    items = [System.Collections.Generic.List[string]]::new()
    drives = [System.Collections.Generic.List[string]]::new()
    walks = [System.Collections.Generic.List[string]]::new()
}
$ancestors = @('C:\', 'C:\Users', 'C:\Users\example-user', 'C:\Users\example-user\AppData',
    'C:\Users\example-user\AppData\Local', 'C:\Users\example-user\AppData\Local\CrashDumps')
function Get-SentinelLocalApplicationData { return $fixtureBase }
function Get-SentinelDumpDriveType {
    param([string]$Root)
    $trace.drives.Add($Root)
    return [IO.DriveType]$fixtureDrive
}
function Get-Item {
    [CmdletBinding()]
    param([string]$LiteralPath, [switch]$Force)
    foreach ($leaf in @('Minidump', 'MEMORY.DMP', 'LiveKernelReports')) {
        if ($LiteralPath -eq [IO.Path]::Combine($env:SystemRoot, $leaf)) {
            $failure = [System.Management.Automation.ErrorRecord]::new(
                [System.Management.Automation.ItemNotFoundException]::new('synthetic kernel root missing'),
                'PathNotFound', [System.Management.Automation.ErrorCategory]::ObjectNotFound, $LiteralPath)
            $PSCmdlet.ThrowTerminatingError($failure)
        }
    }
    $index = $trace.items.Count
    $trace.items.Add($LiteralPath)
    if (-not $Force -or $index -ge $ancestors.Count -or $LiteralPath -cne $ancestors[$index]) {
        throw 'unexpected application path probe or traversal order'
    }
    if ($fixtureMode -eq 'parent_denied' -and $index -eq $fixtureBlocked) {
        throw [UnauthorizedAccessException]::new('Access is denied: synthetic application ancestor')
    }
    if ($fixtureMode -eq 'missing' -and $index -eq ($ancestors.Count - 1)) {
        $failure = [System.Management.Automation.ErrorRecord]::new(
            [System.Management.Automation.ItemNotFoundException]::new('synthetic application root missing'),
            'PathNotFound', [System.Management.Automation.ErrorCategory]::ObjectNotFound, $LiteralPath)
        $PSCmdlet.ThrowTerminatingError($failure)
    }
    $attributes = [IO.FileAttributes]::Directory
    if ($fixtureMode -eq 'reparse' -and $index -eq $fixtureBlocked) {
        $attributes = $attributes -bor [IO.FileAttributes]::ReparsePoint
    }
    [pscustomobject]@{
        Name = [IO.Path]::GetFileName($LiteralPath); FullName = $LiteralPath
        PSIsContainer = $true; Attributes = $attributes
    }
}
function New-SyntheticApplicationDump {
    [pscustomobject]@{
        Name = 'retained.dmp'; FullName = [IO.Path]::Combine($ancestors[-1], 'retained.dmp')
        PSIsContainer = $false; Attributes = [IO.FileAttributes]::Archive; Length = 4096
        LastWriteTimeUtc = [datetime]::SpecifyKind([datetime]'2025-01-02T03:04:05', [DateTimeKind]::Utc)
    }
}
function Get-ChildItem {
    [CmdletBinding()]
    param([string]$LiteralPath, [switch]$File, [switch]$Force, [string]$Filter, [switch]$Recurse)
    $trace.walks.Add($LiteralPath)
    if ($LiteralPath -cne $ancestors[-1] -or -not $File -or -not $Force -or $Recurse -or $Filter -ne '*.dmp') {
        throw 'unexpected application directory query'
    }
    if ($fixtureMode -eq 'empty') { return }
    if ($fixtureMode -notin @('data', 'partial_link', 'partial_denied', 'partial_failed')) {
        throw 'unexpected application directory walk after rejected path'
    }
    New-SyntheticApplicationDump
    if ($fixtureMode -eq 'partial_link') {
        $link = [pscustomobject]@{
            Name = 'linked.dmp'; FullName = [IO.Path]::Combine($LiteralPath, 'linked.dmp')
            PSIsContainer = $false; Attributes = [IO.FileAttributes]::ReparsePoint
        }
        # A guard must reject the entry before dereferencing target metadata.
        $link | Add-Member -MemberType ScriptProperty -Name Length -Value { throw 'synthetic link target size was accessed' }
        $link | Add-Member -MemberType ScriptProperty -Name LastWriteTimeUtc -Value { throw 'synthetic link target time was accessed' }
        $link
    }
    if ($fixtureMode -eq 'partial_denied') {
        $failure = [System.Management.Automation.ErrorRecord]::new(
            [UnauthorizedAccessException]::new('Access is denied: synthetic application file'),
            'SyntheticApplicationDenied', [System.Management.Automation.ErrorCategory]::PermissionDenied, $LiteralPath)
        $PSCmdlet.WriteError($failure)
    }
    if ($fixtureMode -eq 'partial_failed') { throw 'synthetic application walk failed after file' }
}
"""


class _RecordingBridge:
    def __init__(self, bridge):
        self.bridge = bridge
        self.result = None

    def run(self, script, **kwargs):
        self.result = self.bridge.run(script, **kwargs)
        return self.result


def _take(monkeypatch, mode="empty", *, base=_BASE, drive="Fixed", blocked=-1):
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    # Only fixed synthetic values are embedded. Every filesystem call is replaced;
    # the bridge's Windows SystemRoot is used only for string comparisons.
    quoted_base = base.replace("'", "''")
    settings = (
        f"$fixtureBase = '{quoted_base}'\n"
        f"$fixtureDrive = '{drive}'\n$fixtureMode = '{mode}'\n$fixtureBlocked = {blocked}\n"
    )
    collector = dumps.ALL_DUMPS_SCRIPT.removeprefix(dumps.APPLICATION_DUMP_GUARD_SCRIPT)
    script = (
        "& {\n" + dumps.APPLICATION_DUMP_GUARD_SCRIPT + settings + _REPLACEMENTS
        + "\n$fixtureResult = . {\n" + collector + "\n}\n"
        + "$fixtureResult | Add-Member -MemberType NoteProperty -Name synthetic_trace -Value "
        "([pscustomobject]@{ items = @($trace.items.ToArray()); drives = @($trace.drives.ToArray()); walks = @($trace.walks.ToArray()) })\n"
        "$fixtureResult\n}\n"
    )
    monkeypatch.setattr(dumps, "ALL_DUMPS_SCRIPT", script)
    bridge = _RecordingBridge(real_bridge_or_skip())
    reading = dumps.take_dumps(bridge, {})
    assert bridge.result.outcome == "ok", (bridge.result.error, bridge.result.warnings)
    assert len(bridge.result.items) == 1
    return reading, bridge.result.items[0]["synthetic_trace"]


def _application(reading):
    collection = reading.section("collection").data
    assert tuple(source["id"] for source in collection["locations"]) == dumps.ALL_LOCATION_IDS
    return next(source for source in collection["locations"] if source["id"] == "application")


@pytest.mark.parametrize(("mode", "outcome", "present"), [
    ("missing", "empty", False),
    ("empty", "empty", True),
    ("data", "ok", True),
])
def test_application_directory_is_a_direct_observed_source(monkeypatch, mode, outcome, present):
    reading, trace = _take(monkeypatch, mode)
    source = _application(reading)
    assert reading.outcome == outcome and reading.observed
    assert source["outcome"] == outcome and source["present"] is present and source["recursive"] is False
    assert source["error_count"] == 0 and source["errors"] == []
    assert reading.section("collection").data["complete"] is True
    assert trace == {"items": _ANCESTORS, "drives": ["C:\\"], "walks": [] if mode == "missing" else [APPLICATION_ROOT]}
    files = reading.section("files").data
    assert reading.count == source["returned"] == len(files) == (1 if mode == "data" else 0)
    if files:
        assert files == [{
            "name": "retained.dmp", "path": APPLICATION_ROOT + r"\retained.dmp", "bytes": 4096,
            "modified": "2025-01-02T03:04:05.0000000Z", "source": "application",
        }]


@pytest.mark.parametrize("base", [
    "", r"\\example-server\share\Local", r"\\?\C:\Users\example-user\AppData\Local",
    r"\\.\C:\Users\example-user\AppData\Local", r"relative\Local", r"C:relative\Local",
    r"C:\Users\example-user\..\Local", r"C:\Users\example-user\Local.",
    r"C:\Users\example-user\Local:stream", "C:/Users/example-user/Local",
])
def test_unsupported_root_never_reaches_drive_or_filesystem_probes(monkeypatch, base):
    reading, trace = _take(monkeypatch, base=base)
    source = _application(reading)
    assert reading.outcome == "failed" and not reading.observed and reading.count is None
    assert source["outcome"] == "failed" and source["present"] is None
    assert source["returned"] == 0 and source["errors"]
    assert trace == {"items": [], "drives": [], "walks": []}
    assert reading.section("files").data == []


@pytest.mark.parametrize("drive", ["Network", "Unknown", "NoRootDirectory", "Removable"])
def test_nonfixed_drive_is_rejected_before_traversal(monkeypatch, drive):
    reading, trace = _take(monkeypatch, drive=drive)
    source = _application(reading)
    assert reading.outcome == "failed" and source["present"] is None
    assert "fixed drive" in source["errors"][0]["detail"]
    assert trace == {"items": [], "drives": ["C:\\"], "walks": []}


@pytest.mark.parametrize("blocked", range(len(_ANCESTORS)))
def test_reparse_ancestor_or_root_stops_before_visiting_descendants(monkeypatch, blocked):
    reading, trace = _take(monkeypatch, "reparse", blocked=blocked)
    source = _application(reading)
    assert reading.outcome == "failed" and reading.count is None
    assert source["outcome"] == "failed" and source["present"] is None
    assert "reparse point" in source["errors"][0]["detail"]
    assert trace == {"items": _ANCESTORS[:blocked + 1], "drives": ["C:\\"], "walks": []}


def test_denied_ancestor_keeps_windows_refusal_and_unknown_destination(monkeypatch):
    reading, trace = _take(monkeypatch, "parent_denied", blocked=2)
    source = _application(reading)
    assert reading.outcome == "denied" and not reading.observed and reading.count is None
    assert source["outcome"] == "denied" and source["present"] is None
    assert "synthetic application ancestor" in source["errors"][0]["detail"]
    assert trace["items"] == _ANCESTORS[:3] and trace["walks"] == []


@pytest.mark.parametrize(("mode", "outcome", "marker"), [
    ("partial_link", "failed", "reparse point"),
    ("partial_denied", "denied", "synthetic application file"),
    ("partial_failed", "failed", "synthetic application walk failed after file"),
])
def test_application_walk_preserves_good_files_with_link_or_query_gaps(monkeypatch, mode, outcome, marker):
    reading, trace = _take(monkeypatch, mode)
    source = _application(reading)
    assert reading.outcome == "ok" and reading.count == 1 and reading.warnings
    assert source["outcome"] == outcome and source["present"] is True and source["returned"] == 1
    assert source["error_count"] == len(source["errors"]) > 0
    assert any(marker in error["detail"] for error in source["errors"])
    assert reading.section("collection").data["complete"] is False
    assert reading.section("files").data[0]["name"] == "retained.dmp"
    assert trace["walks"] == [APPLICATION_ROOT]
