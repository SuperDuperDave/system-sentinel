"""Exercise the real dump collector against scoped synthetic filesystem answers.

The replacements run in one child scope in a disposable PowerShell process. They never
query files, change permissions, or leave functions installed in a pooled session.
"""

from __future__ import annotations

import ntpath

import pytest

import sentinel.bridge
from sentinel.readings import dumps
from tests.conftest import real_bridge_or_skip

pytestmark = pytest.mark.host


_SYNTHETIC_FILESYSTEM = r"""
function Get-SyntheticDumpLocation {
    param([string]$LiteralPath)
    foreach ($entry in @(
        @{ id = 'minidump'; leaf = 'Minidump' },
        @{ id = 'memory'; leaf = 'MEMORY.DMP' },
        @{ id = 'live_kernel'; leaf = 'LiveKernelReports' }
    )) {
        if ($LiteralPath -eq [System.IO.Path]::Combine($env:SystemRoot, $entry.leaf)) {
            return $entry.id
        }
    }
    throw "unexpected synthetic dump path: $LiteralPath"
}
function New-SyntheticDumpFile {
    param([string]$Path)
    [pscustomobject]@{
        Name = [System.IO.Path]::GetFileName($Path)
        FullName = $Path
        PSIsContainer = $false
        Length = 4096
        LastWriteTimeUtc = [datetime]::SpecifyKind([datetime]'2025-01-02T03:04:05', [DateTimeKind]::Utc)
    }
}
function Get-Item {
    [CmdletBinding()]
    param([string]$LiteralPath, [switch]$Force)
    $identity = Get-SyntheticDumpLocation $LiteralPath
    if (-not $Force) { throw 'unexpected synthetic root query without Force' }
    $mode = $inventoryModes[$identity]
    if ($mode -eq 'missing') {
        $failure = [System.Management.Automation.ErrorRecord]::new(
            [System.Management.Automation.ItemNotFoundException]::new("synthetic $identity missing"),
            'PathNotFound', [System.Management.Automation.ErrorCategory]::ObjectNotFound, $LiteralPath)
        $PSCmdlet.ThrowTerminatingError($failure)
    }
    if ($mode -eq 'lookup_failed') {
        # ObjectNotFound alone does not establish an absent file or directory.
        $failure = [System.Management.Automation.ErrorRecord]::new(
            [System.Exception]::new("synthetic $identity lookup failed"),
            'SyntheticLookupFailed', [System.Management.Automation.ErrorCategory]::ObjectNotFound, $LiteralPath)
        $PSCmdlet.ThrowTerminatingError($failure)
    }
    if ($mode -eq 'denied') {
        throw [System.UnauthorizedAccessException]::new("Access is denied: synthetic $identity root")
    }
    if ($mode -eq 'wrong_type') {
        return New-SyntheticDumpFile $LiteralPath
    }
    if ($identity -eq 'memory') {
        if ($mode -ne 'data') { throw 'unexpected synthetic memory-file mode' }
        return New-SyntheticDumpFile $LiteralPath
    }
    if ($mode -notin @('empty', 'partial_denied', 'partial_failed')) {
        throw 'unexpected synthetic directory mode'
    }
    [pscustomobject]@{
        Name = [System.IO.Path]::GetFileName($LiteralPath)
        FullName = $LiteralPath
        PSIsContainer = $true
    }
}
function Get-ChildItem {
    [CmdletBinding()]
    param([string]$LiteralPath, [switch]$File, [string]$Filter, [switch]$Recurse, [switch]$Force)
    $identity = Get-SyntheticDumpLocation $LiteralPath
    if ($identity -eq 'memory' -or -not $File -or -not $Force -or $Filter -ne '*.dmp' -or
        [bool]$Recurse -ne ($identity -eq 'live_kernel')) {
        throw 'unexpected synthetic directory query'
    }
    $mode = $inventoryModes[$identity]
    if ($mode -eq 'empty') { return }
    if ($mode -notin @('partial_denied', 'partial_failed')) {
        throw 'unexpected synthetic directory enumeration'
    }
    New-SyntheticDumpFile ([System.IO.Path]::Combine($LiteralPath, 'retained.dmp'))
    if ($mode -eq 'partial_denied') {
        $failure = [System.Management.Automation.ErrorRecord]::new(
            [System.UnauthorizedAccessException]::new("Access is denied: synthetic $identity child"),
            'SyntheticChildDenied', [System.Management.Automation.ErrorCategory]::PermissionDenied,
            [System.IO.Path]::Combine($LiteralPath, 'unreadable-child'))
        $PSCmdlet.WriteError($failure)
        return
    }
    throw "synthetic $identity enumeration failed after file"
}
"""


def _take(monkeypatch, modes):
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    # All interpolated values are fixed test modes, never caller input. Resolve roots
    # from the child's SystemRoot, so the test does not assume a Windows drive letter.
    assignments = "; ".join(f"{identity} = '{mode}'" for identity, mode in modes.items())
    script = (
        "& {\n$inventoryModes = @{ " + assignments + " }\n"
        + _SYNTHETIC_FILESYSTEM + "\n" + dumps.DUMPS_SCRIPT + "\n}\n"
    )
    # Deliberately exercise only the kernel collector here. Application coverage and
    # its filesystem guard run in test_application_dump_inventory_observation.py.
    monkeypatch.setattr(dumps, "ALL_DUMPS_SCRIPT", script)
    monkeypatch.setattr(dumps, "ALL_LOCATION_IDS", dumps.LOCATION_IDS)
    return dumps.take_dumps(real_bridge_or_skip(), {})


@pytest.mark.parametrize(("modes", "outcome"), [
    pytest.param(
        {"minidump": "missing", "memory": "missing", "live_kernel": "missing"}, "empty",
        id="all-roots-missing",
    ),
    pytest.param(
        {"minidump": "empty", "memory": "missing", "live_kernel": "empty"}, "empty",
        id="existing-folders-empty",
    ),
    pytest.param(
        {"minidump": "denied", "memory": "missing", "live_kernel": "empty"}, "denied",
        id="one-root-denied-without-files",
    ),
    pytest.param(
        {"minidump": "denied", "memory": "data", "live_kernel": "empty"}, "ok",
        id="memory-file-survives-other-root-denial",
    ),
    pytest.param(
        {"minidump": "empty", "memory": "missing", "live_kernel": "partial_denied"}, "ok",
        id="recursive-walk-retains-file-before-nonterminating-denial",
    ),
    pytest.param(
        {"minidump": "partial_failed", "memory": "missing", "live_kernel": "empty"}, "ok",
        id="walk-retains-file-before-terminating-failure",
    ),
    pytest.param(
        {"minidump": "wrong_type", "memory": "missing", "live_kernel": "empty"}, "failed",
        id="wrong-root-type-is-not-missing",
    ),
    pytest.param(
        {"minidump": "lookup_failed", "memory": "lookup_failed", "live_kernel": "lookup_failed"}, "failed",
        id="object-not-found-category-without-path-not-found",
    ),
])
def test_collector_preserves_dump_location_observations(monkeypatch, modes, outcome):
    reading = _take(monkeypatch, modes)
    assert reading.outcome == outcome, (reading.error, reading.warnings)
    assert reading.observed == (outcome in ("ok", "empty"))
    files = reading.section("files").data
    collection = reading.section("collection").data
    locations = {source["id"]: source for source in collection["locations"]}
    assert len(collection["locations"]) == 3
    assert set(locations) == {"minidump", "memory", "live_kernel"}

    expected = {
        "missing": ("empty", False, 0),
        "empty": ("empty", True, 0),
        "data": ("ok", True, 1),
        "denied": ("denied", None, 0),
        "partial_denied": ("denied", True, 1),
        "partial_failed": ("failed", True, 1),
        "wrong_type": ("failed", True, 0),
        "lookup_failed": ("failed", None, 0),
    }
    expected_files = []
    for identity, mode in modes.items():
        source = locations[identity]
        source_outcome, present, returned = expected[mode]
        assert source["outcome"] == source_outcome, source
        assert source["present"] is present
        assert source["returned"] == returned
        assert source["recursive"] is (identity == "live_kernel")
        assert isinstance(source["path"], str) and source["path"]
        assert "files" not in source
        assert source["error_count"] == len(source["errors"])
        if source_outcome in ("ok", "empty"):
            assert source["errors"] == []
        else:
            assert source["errors"]
            assert all(error["kind"] == source_outcome for error in source["errors"])
            marker = {
                "denied": f"synthetic {identity} root",
                "partial_denied": f"synthetic {identity} child",
                "partial_failed": f"synthetic {identity} enumeration failed after file",
                "wrong_type": "unexpected file type",
                "lookup_failed": f"synthetic {identity} lookup failed",
            }[mode]
            assert any(marker in error["detail"] for error in source["errors"]), source
        if returned:
            path = source["path"] if identity == "memory" else ntpath.join(source["path"], "retained.dmp")
            expected_files.append({
                "name": ntpath.basename(path), "path": path, "bytes": 4096,
                "modified": "2025-01-02T03:04:05.0000000Z",
                "source": identity,
            })

    # Equality checks file fields, source attribution and preservation of partial data.
    assert files == expected_files
    assert reading.count == (len(expected_files) if reading.observed else None)
    complete = all(expected[mode][0] in ("ok", "empty") for mode in modes.values())
    assert collection["complete"] is complete
    if not complete:
        assert reading.warnings
    if not reading.observed:
        assert reading.error and reading.error["kind"] == outcome
