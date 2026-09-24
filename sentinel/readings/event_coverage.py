"""Shared, bounded evidence about how far a Windows Event Log query can see.

The event query decides its own outcome. Metadata and retention checks can qualify a clean
absence, but failure of a supporting probe cannot erase records the event query returned.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

METADATA_KEYS = ("log", "log_enabled", "log_mode", "log_state", "log_error", "log_oldest", "oldest_state", "oldest_error")
_STRICT_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,7}))?(Z|[+-]\d{2}:\d{2})", re.ASCII)
_FRACTION = re.compile(r"[.,](\d+)$", re.ASCII)
_WINDOWS_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)


def exact_stamp(value: str, label: str, *, naive: str = "reject", strict: bool = False) -> tuple[str, int]:
    """Normalize a Windows Event Log bound without dropping its seventh (100 ns) digit.

    Non-strict callers keep Python's existing ISO input forms, including local times.
    Strict callers require the documented zoned WHEA-window form.
    """
    text = value.strip()
    match = _STRICT_STAMP.fullmatch(text) if strict else None
    if strict and match is None:
        raise ValueError(f"parameter {label!r}: expected an ISO timestamp with Z or an offset and at most seven fractional digits")
    fraction = match.group(2) if match else None
    if not strict:
        # Split the date from the time before looking for a fraction: ISO offsets may themselves
        # contain fractional seconds, and those must not become a seventh digit of event time.
        separator = next((i for i, char in enumerate(text) if i >= 7 and char in "Tt "), None)
        time_text = text[separator + 1:] if separator is not None else (text[10:] if text[4:5] == "-" else text[8:])
        offset_fraction = re.search(r"[+-]\d{2}(?::?\d{2}){0,2}[.,](\d+)$", time_text)
        if offset_fraction and len(offset_fraction.group(1)) > 6:
            raise ValueError(f"parameter {label!r}: sub-microsecond UTC offsets are not supported")
        time_text = re.split(r"[+-]|Z$", time_text, maxsplit=1)[0]
        found = _FRACTION.search(time_text)
        fraction = found.group(1) if found else None
        if fraction and len(fraction) > 7:
            raise ValueError(f"parameter {label!r}: at most seven fractional digits are supported")
        if fraction and len(fraction) == 7 and not re.fullmatch(r"\d{2}:\d{2}:\d{2}\.\d{7}", time_text):
            raise ValueError(f"parameter {label!r}: use an extended seconds timestamp for seven fractional digits")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            if naive == "reject":
                raise ValueError("a time zone is required")
            parsed = parsed.astimezone()
        utc = parsed.astimezone(UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError(f"parameter {label!r}: invalid timestamp ({exc})") from exc
    if utc < _WINDOWS_EPOCH:
        raise ValueError(f"parameter {label!r}: a Windows event-log time must be in 1601 or later")
    seventh = int((fraction or "0").ljust(7, "0")[6])
    canonical = f"{utc.year:04d}-" + utc.strftime("%m-%dT%H:%M:%S.%f") + str(seventh) + "Z"
    delta = utc.replace(tzinfo=None) - datetime(1, 1, 1)
    ticks = (delta.days * 86400 + delta.seconds) * 10_000_000 + delta.microseconds * 10 + seventh
    return canonical, ticks

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
    "record and the oldest validated in-window match returned when the query stopped early or reached its cap. Complete "
    "means this retained circular log was enabled, had a record before the requested start, and "
    "returned all matching records within the per-source limit without stopping early. An event whose "
    "projected time cannot be read also prevents complete coverage. A boundary at the oldest retained "
    "or oldest returned record is exclusive: earlier records can share its timestamp. The time "
    "reach assumes event timestamps have not moved backward across retained record order; it does "
    "not prove Windows emitted every event."
)
WINDOW_COVERAGE_BASIS = COVERAGE_BASIS + " Complete describes the whole requested window; covered_until is its observed exclusive end, which cannot pass the machine's query time."
LOG_WINDOW_COVERAGE_BASIS = WINDOW_COVERAGE_BASIS + " For events and faults, a requested start at or after the machine's query time, or an end after it, cannot establish a complete requested window."


def metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value.get(key) for key in METADATA_KEYS}


def coverage(source: dict[str, Any], rows: list[dict[str, Any]], start: str, end: str) -> dict[str, Any]:
    if source["outcome"] not in ("ok", "empty"):
        return {"covered_from": None, "covered_from_inclusive": None, "complete": None}
    covered = covered_from(source, rows, start, end)
    oldest_key, start_key = stamp_key(source.get("log_oldest")), stamp_key(start)
    inclusive = covered is not None and not stopped_early(source) and oldest_key is not None and start_key is not None and oldest_key < start_key
    issues = source.get("row_issues")
    unplaced = issues.get("unplaced", 0) if isinstance(issues, dict) else 0
    return {
        "covered_from": covered,
        "covered_from_inclusive": None if covered is None else inclusive,
        "complete": covered is not None and inclusive and stamp_key(covered) == stamp_key(start) and not unplaced,
    }


def window_coverage(
    source: dict[str, Any], rows: list[dict[str, Any]], start: str, end: str, queried_at: str | None, *, end_is_query_time: bool,
) -> dict[str, Any]:
    """Qualify a requested window against the machine's pre-query clock without claiming future time."""
    future_end = False
    if not end_is_query_time:
        requested, observed_at = stamp_key(end), stamp_key(queried_at)
        if requested is None or observed_at is None:
            return {"covered_from": None, "covered_from_inclusive": None, "covered_until": None, "complete": None}
        future_end = requested > observed_at
        observed_end = queried_at if future_end else end
        assert observed_end is not None
    else:
        observed_end = end
    reach = coverage(source, rows, start, observed_end)
    complete = reach["complete"]
    issues = source.get("row_issues")
    outside = issues.get("outside_window", 0) if isinstance(issues, dict) else 0
    if outside and complete is not None:
        complete = False
    if future_end and complete is not None:
        complete = False
    return {**reach, "covered_until": observed_end if reach["covered_from"] is not None else None, "complete": complete}


def covered_from(source: dict[str, Any], rows: list[dict[str, Any]], start: str, end: str) -> str | None:
    if source.get("log_state") != "ok" or source.get("log_enabled") is not True or source.get("log_mode") != "Circular" or source.get("oldest_state") != "ok":
        return None
    oldest_text = source.get("log_oldest")
    oldest = stamp_key(oldest_text)
    window_start, window_end = stamp_key(start), stamp_key(end)
    if not isinstance(oldest_text, str) or oldest is None or window_start is None or window_end is None or window_start >= window_end or oldest >= window_end:
        return None
    candidates: list[str] = [start, oldest_text]
    if stopped_early(source):
        returned = [row.get("TimeCreated") for row in rows]
        if not returned or any(not isinstance(moment, str) or stamp_key(moment) is None for moment in returned):
            return None
        candidates.append(min((moment for moment in returned if isinstance(moment, str)), key=known_stamp_key))
    return max(candidates, key=known_stamp_key)


def stopped_early(source: dict[str, Any]) -> bool:
    """A record cap and an interrupted query both leave the older tail unobserved."""
    return source.get("truncated") is True or source.get("stopped") is not None


def parse_stamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (ValueError, OverflowError, OSError):
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
