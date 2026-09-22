"""One dump inventory for listing files, matching stops and selecting bounded inspections.

Locations answer independently. A directory walk can return useful files and still fail to
read a child folder; keep those files and the collection gap together.
"""

from __future__ import annotations

import ntpath
from typing import Any

from ..bridge import Bridge, Outcome
from ..reading import Reading, Section, Spec, from_object, register

LOCATION_IDS = ("minidump", "memory", "live_kernel")
ERROR_LIMIT = 20

DUMPS_SCRIPT = r"""
$locations = @()
$specs = @(
    @{ id = 'minidump'; path = (Join-Path $env:SystemRoot 'Minidump'); recursive = $false; directory = $true },
    @{ id = 'memory'; path = (Join-Path $env:SystemRoot 'MEMORY.DMP'); recursive = $false; directory = $false },
    @{ id = 'live_kernel'; path = (Join-Path $env:SystemRoot 'LiveKernelReports'); recursive = $true; directory = $true }
)
foreach ($spec in $specs) {
    $files = New-Object 'System.Collections.Generic.List[object]'
    $errors = New-Object 'System.Collections.Generic.List[object]'
    $present = $null
    $item = $null
    try {
        $item = Get-Item -LiteralPath $spec.path -Force -ErrorAction Stop
        $present = $true
        if ([bool]$item.PSIsContainer -ne $spec.directory) { throw 'The dump location has an unexpected file type.' }
    } catch {
        if ($_.FullyQualifiedErrorId -like 'PathNotFound,*' -or $_.FullyQualifiedErrorId -eq 'PathNotFound') {
            $present = $false
        } else { $errors.Add($_) }
        $item = $null
    }
    if ($null -ne $item) {
        # Append each completed projection directly: an error later in enumeration must not
        # erase files that were already read. Nonterminating walk errors are retained separately.
        $walkErrors = @()
        try {
            . {
                if ($spec.directory) {
                    Get-ChildItem -LiteralPath $spec.path -File -Force -Filter '*.dmp' -Recurse:$spec.recursive -ErrorAction SilentlyContinue -ErrorVariable walkErrors
                } else { $item }
            } | ForEach-Object {
                try {
                    $files.Add([pscustomobject]@{
                        name = $_.Name; path = $_.FullName; bytes = $_.Length
                        modified = $_.LastWriteTimeUtc.ToString('o')
                    })
                } catch { $errors.Add($_) }
            }
        } catch {
            if ($walkErrors -notcontains $_) { $errors.Add($_) }
        }
        foreach ($failure in $walkErrors) { $errors.Add($failure) }
    }
    $details = @($errors | ForEach-Object {
        [pscustomobject]@{
            kind = $(if ($_.CategoryInfo.Category -eq 'PermissionDenied' -or $_.Exception -is [System.UnauthorizedAccessException]) { 'denied' } else { 'failed' })
            detail = $_.Exception.Message
        }
    })
    $outcome = if ($details.Count) {
        if (@($details | Where-Object { $_.kind -ne 'denied' }).Count) { 'failed' } else { 'denied' }
    } elseif ($files.Count) { 'ok' } else { 'empty' }
    $locations += [pscustomobject]@{
        id = $spec.id; path = $spec.path; recursive = $spec.recursive; present = $present
        outcome = $outcome; returned = $files.Count; files = @($files.ToArray())
        error_count = $details.Count; errors = @($details | Select-Object -First 20)
    }
}
[pscustomobject]@{ locations = $locations }
"""


def inventory(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """Validate each location independently; one broken answer cannot erase another's files."""
    rows = payload.get("locations") if isinstance(payload, dict) else None
    rows = rows if isinstance(rows, list) else []
    locations, files, warnings = [], [], []
    for identity in LOCATION_IDS:
        matches = [row for row in rows if isinstance(row, dict) and row.get("id") == identity]
        row = matches[0] if len(matches) == 1 else {}
        source, found = _location(identity, row)
        locations.append(source)
        files.extend(found)
        if source["outcome"] not in ("ok", "empty"):
            detail = source["errors"][0]["detail"]
            warnings.append(f"Dump location {identity} was not fully read: {detail}")
        if source["error_count"] > len(source["errors"]):
            warnings.append(f"Dump location {identity}: {source['error_count']} errors occurred; only the first {len(source['errors'])} are returned.")
    files.sort(key=lambda row: row["modified"], reverse=True)
    return files, {"locations": locations, "complete": all(row["outcome"] in ("ok", "empty") for row in locations)}, warnings


def _location(identity: str, row: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    files, errors = row.get("files"), row.get("errors")
    returned, error_count = row.get("returned"), row.get("error_count")
    valid_files = isinstance(files, list) and all(
        isinstance(file, dict) and all(isinstance(file.get(key), str) and file[key] for key in ("name", "path", "modified"))
        and type(file.get("bytes")) is int and file["bytes"] >= 0 for file in files
    )
    valid_errors = isinstance(errors, list) and all(
        isinstance(error, dict) and error.get("kind") in ("failed", "denied") and isinstance(error.get("detail"), str) for error in errors
    )
    valid_counts = valid_files and valid_errors and type(returned) is int and returned == len(files) and type(error_count) is int and error_count >= len(errors) and len(errors) == min(error_count, ERROR_LIMIT)
    outcome, present = row.get("outcome"), row.get("present")
    observed = outcome in ("ok", "empty")
    valid_state = valid_counts and (
        observed and type(present) is bool and error_count == 0 and (outcome == "empty") == (returned == 0) and (present or returned == 0)
        or outcome in ("failed", "denied") and present is not False and bool(errors)
        and (present is None or type(present) is bool) and (not returned or present is True)
        and (all(error["kind"] == "denied" for error in errors) if outcome == "denied" else error_count > len(errors) or any(error["kind"] == "failed" for error in errors))
    )
    if valid_counts and valid_state and isinstance(row.get("path"), str) and row["path"] and type(row.get("recursive")) is bool and row["recursive"] == (identity == "live_kernel"):
        return {key: row[key] for key in ("id", "path", "recursive", "present", "outcome", "returned", "error_count", "errors")}, files
    return {
        "id": identity, "path": row.get("path") if isinstance(row.get("path"), str) else None,
        "recursive": identity == "live_kernel", "present": None, "outcome": "failed", "returned": 0,
        "error_count": 1, "errors": [{"kind": "failed", "detail": "the collector did not return a consistent location result"}],
    }, []


def missing_file(path: str, collection: dict[str, Any]) -> tuple[Outcome, str]:
    """Classify a missing exact inventory match; this never authorizes opening a path."""
    selected = ntpath.normcase(ntpath.normpath(path))
    relevant = []
    for source in collection["locations"]:
        root = ntpath.normcase(ntpath.normpath(source["path"])) if source["path"] else None
        if root is None or (selected == root if source["id"] == "memory" else ntpath.dirname(selected) == root or source["recursive"] and selected.startswith(root + "\\")):
            relevant.append(source)
    failures = [source for source in relevant if source["outcome"] not in ("ok", "empty")]
    if failures:
        outcome: Outcome = "denied" if all(source["outcome"] == "denied" for source in failures) else "failed"
        return outcome, "The dump location could not be fully read; whether this file is present is unknown."
    return "empty", "No exact match was returned in the observed dump inventory."


def take_dumps(bridge: Bridge, params: dict[str, Any]) -> Reading:
    gathered: dict[str, Any] = {}

    def build(payload: dict[str, Any]) -> list[Section]:
        files, collection, warnings = inventory(payload)
        gathered.update(files=files, collection=collection, warnings=warnings)
        return [Section("files", "raw", files), Section("collection", "raw", collection)]

    reading = from_object("dumps", params, DUMPS_SCRIPT, bridge.run(DUMPS_SCRIPT, depth=8), build)
    if reading.observed:
        reading.warnings.extend(gathered["warnings"])
        reading.count = len(gathered["files"])
        if reading.count:
            reading.outcome = "ok"
        elif gathered["collection"]["complete"]:
            reading.outcome = "empty"
        else:
            failures = [source for source in gathered["collection"]["locations"] if source["outcome"] not in ("ok", "empty")]
            reading.outcome = "denied" if all(source["outcome"] == "denied" for source in failures) else "failed"
            reading.count = None
            reading.error = {"kind": reading.outcome, "detail": "No dump files could be established because some dump locations could not be read."}
    return reading


register(Spec(
    name="dumps", description="Crash dump files in Windows' dump locations, with each location's collection outcome and any gaps.",
    classes=("raw",), take=take_dumps, private=("dump paths",),
))
