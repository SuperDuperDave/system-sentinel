"""Shared, bounded evidence about how far a Windows Event Log query can see.

The event query decides its own outcome. Metadata and retention checks can qualify a clean
absence, but failure of a supporting probe cannot erase records the event query returned.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

METADATA_KEYS = ("log", "log_enabled", "log_mode", "log_state", "log_error", "log_oldest", "oldest_state", "oldest_error")

# Call after the matching query. A circular log may wrap while that query runs; the oldest record
# observed afterwards is a conservative bound. Keep the projection narrow: LogFilePath is private.
LOG_METADATA_SCRIPT = r"""
function Read-LogMetadata([string]$log) {
    $enabled = $null; $mode = $null; $logState = 'failed'; $logError = $null
    try {
        $info = Get-WinEvent -ListLog $log -ErrorAction Stop
        $enabled = [bool]$info.IsEnabled; $mode = [string]$info.LogMode; $logState = 'ok'
    } catch {
        $logState = if ($_.CategoryInfo.Category -eq [System.Management.Automation.ErrorCategory]::PermissionDenied -or $_.Exception -is [System.UnauthorizedAccessException]) { 'denied' } else { 'failed' }
        $logError = $_.Exception.Message
    }
    $oldest = $null; $oldestState = 'failed'; $oldestError = $null
    try {
        $first = Get-WinEvent -LogName $log -Oldest -MaxEvents 1 -ErrorAction Stop
        $oldest = $first.TimeCreated.ToUniversalTime().ToString('o'); $oldestState = 'ok'
    } catch {
        if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { $oldestState = 'empty' }
        else {
            $oldestState = if ($_.CategoryInfo.Category -eq [System.Management.Automation.ErrorCategory]::PermissionDenied -or $_.Exception -is [System.UnauthorizedAccessException]) { 'denied' } else { 'failed' }
            $oldestError = $_.Exception.Message
        }
    }
    [pscustomobject]@{
        log = $log; log_enabled = $enabled; log_mode = $mode; log_state = $logState; log_error = $logError
        log_oldest = $oldest; oldest_state = $oldestState; oldest_error = $oldestError
    }
}
"""

COVERAGE_BASIS = (
    "For each observed source, compare the requested UTC start with the log's oldest retained "
    "record and the oldest matching record returned when the response was truncated. Complete "
    "means this retained circular log was enabled, had a record before the requested start and "
    "returned all matching records within the per-source limit. A boundary at the oldest retained "
    "or oldest returned record is exclusive: earlier records can share its timestamp. The time "
    "reach assumes event timestamps have not moved backward across retained record order; it does "
    "not prove Windows emitted every event."
)


def metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value.get(key) for key in METADATA_KEYS}


def coverage(source: dict[str, Any], rows: list[dict[str, Any]], start: str, end: str) -> dict[str, Any]:
    if source["outcome"] not in ("ok", "empty"):
        return {"covered_from": None, "covered_from_inclusive": None, "complete": None}
    covered = covered_from(source, rows, start, end)
    oldest_key, start_key = stamp_key(source.get("log_oldest")), stamp_key(start)
    inclusive = covered is not None and not source["truncated"] and oldest_key is not None and start_key is not None and oldest_key < start_key
    return {
        "covered_from": covered,
        "covered_from_inclusive": None if covered is None else inclusive,
        "complete": covered is not None and inclusive and stamp_key(covered) == stamp_key(start),
    }


def covered_from(source: dict[str, Any], rows: list[dict[str, Any]], start: str, end: str) -> str | None:
    if source.get("log_state") != "ok" or source.get("log_enabled") is not True or source.get("log_mode") != "Circular" or source.get("oldest_state") != "ok":
        return None
    oldest_text = source.get("log_oldest")
    oldest = stamp_key(oldest_text)
    window_start, window_end = stamp_key(start), stamp_key(end)
    if not isinstance(oldest_text, str) or oldest is None or window_start is None or window_end is None or oldest >= window_end:
        return None
    candidates: list[str] = [start, oldest_text]
    if source.get("truncated"):
        returned = [row.get("TimeCreated") for row in rows]
        if not returned or any(not isinstance(moment, str) or stamp_key(moment) is None for moment in returned):
            return None
        candidates.append(min((moment for moment in returned if isinstance(moment, str)), key=known_stamp_key))
    return max(candidates, key=known_stamp_key)


def parse_stamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except ValueError:
        return None


def stamp_key(value: Any) -> tuple[datetime, int] | None:
    """Compare Windows' seven-digit UTC fractions without rounding a boundary backward."""
    parsed = parse_stamp(value)
    if parsed is None:
        return None
    fraction = re.search(r"\.(\d+)(?:Z|[+-]\d{2}:\d{2})$", value)
    seventh = int((fraction.group(1) + "0000000")[:7]) % 10 if fraction else 0
    return parsed, seventh


def known_stamp_key(value: str) -> tuple[datetime, int]:
    key = stamp_key(value)
    assert key is not None
    return key
