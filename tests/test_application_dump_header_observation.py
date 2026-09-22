"""A fresh application-file guard rejects redirects introduced after inventory.

All filesystem answers are synthetic. A tripwire replaces the final file-open operation,
so even a failed guard regression cannot open a real file while this test runs.
"""

from __future__ import annotations

import ntpath

import pytest

import sentinel.bridge
from sentinel.readings.dump_header import dump_header_script
from sentinel.readings.dumps import APPLICATION_DUMP_GUARD_SCRIPT
from tests.conftest import real_bridge_or_skip
from tests.test_dump_inventory import APPLICATION_ROOT

pytestmark = pytest.mark.host

_PATH = APPLICATION_ROOT + r"\changed.dmp"
_CHAIN = [
    "C:\\", r"C:\Users", r"C:\Users\example-user", r"C:\Users\example-user\AppData",
    ntpath.dirname(APPLICATION_ROOT), APPLICATION_ROOT, _PATH,
]

_TRACE_AND_FILESYSTEM = r"""
$trace = @{
    round = 0; next = 0; opens = 0
    probes = [System.Collections.Generic.List[object]]::new()
    walks = [System.Collections.Generic.List[string]]::new()
    drives = [System.Collections.Generic.List[string]]::new()
}
$chain = @('C:\', 'C:\Users', 'C:\Users\example-user', 'C:\Users\example-user\AppData',
    'C:\Users\example-user\AppData\Local', 'C:\Users\example-user\AppData\Local\CrashDumps',
    'C:\Users\example-user\AppData\Local\CrashDumps\changed.dmp')
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
    if ($LiteralPath -ceq $chain[0]) {
        $trace.round++
        $trace.next = 0
    }
    $trace.probes.Add([pscustomobject]@{ round = $trace.round; path = $LiteralPath })
    $index = $trace.next
    if (-not $Force -or $trace.round -notin @(1, 2) -or $index -ge $chain.Count -or $LiteralPath -cne $chain[$index]) {
        throw 'unexpected synthetic header traversal'
    }
    $trace.next++
    $isFile = $index -eq ($chain.Count - 1)
    $attributes = if ($isFile) { [IO.FileAttributes]::Archive } else { [IO.FileAttributes]::Directory }
    if ($trace.round -eq 2 -and $index -eq $fixtureBlocked) {
        $attributes = $attributes -bor [IO.FileAttributes]::ReparsePoint
    }
    [pscustomobject]@{
        Name = [IO.Path]::GetFileName($LiteralPath); FullName = $LiteralPath
        PSIsContainer = -not $isFile; Attributes = $attributes
    }
}
function Get-ChildItem {
    [CmdletBinding()]
    param([string]$LiteralPath, [switch]$File, [switch]$Force, [string]$Filter, [switch]$Recurse)
    $trace.walks.Add($LiteralPath)
    if ($trace.round -ne 1 -or $trace.next -ne ($chain.Count - 1) -or $LiteralPath -cne $chain[-2] -or
        -not $File -or -not $Force -or $Recurse -or $Filter -ne '*.dmp') {
        throw 'unexpected synthetic header inventory walk'
    }
    [pscustomobject]@{
        Name = 'changed.dmp'; FullName = $chain[-1]; PSIsContainer = $false
        Attributes = [IO.FileAttributes]::Archive; Length = 4096
        LastWriteTimeUtc = [datetime]::SpecifyKind([datetime]'2025-01-02T03:04:05', [DateTimeKind]::Utc)
    }
}
function Open-SyntheticDumpFile {
    $trace.opens++
    throw 'synthetic file-open tripwire reached'
}
"""

_ENVIRONMENT_OVERRIDES = r"""
function Get-SentinelLocalApplicationData { return 'C:\Users\example-user\AppData\Local' }
function Get-SentinelDumpDriveType {
    param([string]$Root)
    $trace.drives.Add($Root)
    return [IO.DriveType]::Fixed
}
"""


@pytest.mark.parametrize("blocked", [
    pytest.param(3, id="ancestor-changes-after-inventory"),
    pytest.param(6, id="file-changes-after-inventory"),
])
def test_fresh_header_guard_rejects_reparse_changes_before_open(monkeypatch, blocked):
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    query = dump_header_script(_PATH)
    guard = APPLICATION_DUMP_GUARD_SCRIPT.strip()
    # Both helper definitions must be overridden, without resetting shared trace state.
    # The collector and guard bodies themselves remain the production code.
    assert query.count(guard) == 2
    query = query.replace(guard, guard + "\n" + _ENVIRONMENT_OVERRIDES)
    opening = "$stream = [IO.File]::Open($file.path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)"
    assert query.count(opening) == 1 and query.count("[IO.File]::Open") == 1
    query = query.replace(opening, "$stream = Open-SyntheticDumpFile")
    script = (
        "& {\n" + _TRACE_AND_FILESYSTEM + f"\n$fixtureBlocked = {blocked}\n"
        + "$headerResult = & {\n" + query + "\n}\n"
        + "[pscustomobject]@{ header = $headerResult; trace = [pscustomobject]@{ "
        "rounds = $trace.round; opens = $trace.opens; probes = @($trace.probes.ToArray()); "
        "walks = @($trace.walks.ToArray()); drives = @($trace.drives.ToArray()) } }\n}\n"
    )
    result = real_bridge_or_skip().run(script, depth=12)
    assert result.outcome == "ok", (result.error, result.warnings)
    assert len(result.items) == 1
    header, trace = result.items[0]["header"], result.items[0]["trace"]
    assert header["status"] == "failed" and "prefix" not in header
    application = next(source for source in header["inventory"]["locations"] if source["id"] == "application")
    assert application["outcome"] == "ok" and application["present"] is True
    assert application["returned"] == 1 and application["files"][0]["path"] == _PATH
    assert application["errors"] == []
    assert trace["rounds"] == 2 and trace["opens"] == 0
    assert trace["walks"] == [APPLICATION_ROOT] and trace["drives"] == ["C:\\", "C:\\"]
    assert trace["probes"] == (
        [{"round": 1, "path": path} for path in _CHAIN[:-1]]
        + [{"round": 2, "path": path} for path in _CHAIN[:blocked + 1]]
    )
