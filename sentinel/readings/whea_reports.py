"""Kernel-WHEA reports over time, separate from System WHEA-Logger error-rate leads.

The event's TimeCreated is when Windows *reported* a CPER record. A CPER PreviousError flag says
the error condition occurred in an earlier Windows session, so this reading never calls a cluster
of these reports a burst of hardware errors. Its buckets describe report traffic only.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from ..bridge import Bridge
from ..reading import Param, Reading, Section, Spec, from_object, register
from .event_coverage import COVERAGE_BASIS, LOG_METADATA_SCRIPT, stamp_key
from .event_coverage import coverage as log_coverage
from .event_coverage import metadata as log_metadata
from .whea import (
    CHANNEL,
    CHANNEL_PROVIDER,
    MAX_HOURS,
    RECORD_CAP,
    Window,
    _host_window,
    _stamp,
    before_stamp,
    report_reference,
    valid_fixed_header_projection,
    window_for,
)

SCRIPT = r"""
$queried = (Get-Date).ToUniversalTime()
$queried = $queried.AddTicks(-($queried.Ticks % 10000))
$requestedUntil = {before_assignment}
$until = if ($requestedUntil -gt $queried) {{ $queried }} else {{ $requestedUntil }}
$untilIso = $until.ToString('o')
$queryUntilIso = $until.AddMilliseconds(1).ToString('o')
$epoch = [datetime]::SpecifyKind([datetime]'1970-01-01T00:00:00', [System.DateTimeKind]::Utc)
$bucketTicks = [long]{bucket_seconds} * [long]10000000
$elapsedTicks = $until.AddTicks(-1).Ticks - $epoch.Ticks
$currentBucketTicks = $elapsedTicks - ($elapsedTicks % $bucketTicks)
$startIso = $epoch.AddTicks($currentBucketTicks - ([long]({count} - 1) * $bucketTicks)).ToString('o')
$xml = @"
<QueryList><Query Id='0' Path='{channel}'><Select Path='{channel}'>*[System[Provider[@Name='{provider}'] and EventID=20 and TimeCreated[@SystemTime&gt;='$startIso' and @SystemTime&lt;'$queryUntilIso']]]</Select></Query></QueryList>
"@
$found = [System.Collections.Generic.List[object]]::new()
$records = @(); $outcome = 'failed'; $errorText = $null; $truncated = $null; $stopped = $null
try {{
    Get-WinEvent -FilterXml ([xml]$xml) -MaxEvents {extra} -ErrorAction Stop | & {{ process {{ [void]$found.Add($_) }} }}
    $truncated = $found.Count -gt {cap}
}} catch {{
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*' -and $found.Count -eq 0) {{ $truncated = $false }}
    elseif ($found.Count) {{
        $kind = if ($_.CategoryInfo.Category -eq [System.Management.Automation.ErrorCategory]::PermissionDenied -or $_.Exception -is [System.UnauthorizedAccessException]) {{ 'denied' }} else {{ 'failed' }}
        $detail = ([string]$_.Exception.Message -split '\r?\n')[0].Trim()
        if (-not $detail) {{ $detail = 'the Kernel-WHEA query stopped early' }}
        if ($detail.Length -gt 300) {{
            $detail = $detail.Substring(0, 300)
            if ([char]::IsHighSurrogate($detail[299])) {{ $detail = $detail.Substring(0, 299) }}
        }}
        $stopped = [pscustomobject]@{{ kind = $kind; detail = $detail }}
    }} else {{
        $outcome = if ($_.CategoryInfo.Category -eq [System.Management.Automation.ErrorCategory]::PermissionDenied -or $_.Exception -is [System.UnauthorizedAccessException]) {{ 'denied' }} else {{ 'failed' }}
        $errorText = $_.Exception.Message
    }}
}}
if ($null -eq $errorText) {{
    try {{
        $records = @($found | Select-Object -First {cap} | ForEach-Object {{
            $event = $_
            $bytes = $null
            foreach ($property in $event.Properties) {{
                if ($property.Value -is [byte[]]) {{ $bytes = $property.Value; break }}
            }}
            [pscustomobject]@{{
                RecordId = $event.RecordId; Id = $event.Id; ProviderName = $event.ProviderName; LogName = $event.LogName
                TimeCreated = $(if ($null -ne $event.TimeCreated) {{ $event.TimeCreated.ToUniversalTime().ToString('o') }} else {{ $null }})
                HeaderHex = $(if ($null -ne $bytes) {{ [System.BitConverter]::ToString($bytes, 0, [Math]::Min(128, $bytes.Length)).Replace('-','') }} else {{ $null }})
                PayloadBytes = $(if ($null -ne $bytes) {{ $bytes.Length }} else {{ $null }})
            }}
        }})
        if ($records.Count -ne [Math]::Min($found.Count, {cap})) {{ throw 'the Kernel-WHEA projection returned fewer records than the query' }}
        $outcome = if ($records.Count) {{ 'ok' }} else {{ 'empty' }}
    }} catch {{
        $records = @(); $outcome = 'failed'; $errorText = $_.Exception.Message; $truncated = $null; $stopped = $null
    }}
}}
$meta = Read-LogMetadata '{channel}'
[pscustomobject]@{{
    window_start = $startIso; window_end = $untilIso; queried_at = $queried.ToString('o')
    source = [pscustomobject]@{{
        log = '{channel}'; outcome = $outcome; error = $errorText
        returned = $records.Count; limit = {cap}; truncated = $truncated; stopped = $stopped; records = $records
        log_enabled = $meta.log_enabled; log_mode = $meta.log_mode; log_state = $meta.log_state; log_error = $meta.log_error
        log_oldest = $meta.log_oldest; oldest_state = $meta.oldest_state; oldest_error = $meta.oldest_error
    }}
}}
"""

ROW_KEYS = {"RecordId", "Id", "ProviderName", "LogName", "TimeCreated", "HeaderHex", "PayloadBytes"}
BASIS = (
    "Buckets count Windows Kernel-WHEA event-20 report times, not when the underlying hardware "
    "condition occurred. A CPER PreviousError flag identifies a condition from an earlier Windows "
    "session; a report after restart must not become a new-error-rate claim. Null bucket totals mean "
    "the channel's retention, query limit, early stop, or unreadable time cannot establish a quiet "
    "bucket. Header facts use only the fixed 128-byte CPER header and do not validate the entire "
    "record. Top-level PreviousError and unreadable-header counts include returned reports whose "
    "time could not be placed; active bucket counts require a readable time. The separate "
    "whea_record reading retrieves one retained event by this channel's log-local RecordId, "
    "with CPER bytes only on explicit unredacted request. Per-report references require references=true; "
    "whea_window returns bounded previews for a selected exact interval. Sparse returned columns align "
    "index, count, previous_session and header_unreadable for buckets with placed reports; derive a "
    "bucket start from from + index * bucket_seconds and completeness from totals[index] not being null. "
    "CPER-header severity totals count returned reports, including unplaced ones, not error occurrence times; unknown and unreadable stay distinct. The final bucket is observed only "
    "through collection.window_end, even when buckets.to reaches the next bucket boundary."
)
REPORT_COVERAGE_BASIS = (
    COVERAGE_BASIS + " Complete describes the actual bucket-aligned window_start through "
    "window_end, not an earlier start implied by hours. The start can fall up to one bucket "
    "after before minus hours. covered_until is the observed exclusive end and never passes "
    "queried_at; a requested end after queried_at keeps complete false."
)


def reports_script(window: Window, before: str = "") -> str:
    stamp = before_stamp(before) if before.strip() else None
    assignment = f"[datetimeoffset]::Parse('{stamp}').UtcDateTime" if stamp else "$queried"
    return LOG_METADATA_SCRIPT + SCRIPT.format(
        bucket_seconds=window.bucket_seconds, count=window.count, channel=CHANNEL,
        provider=CHANNEL_PROVIDER, cap=RECORD_CAP, extra=RECORD_CAP + 1,
        before_assignment=assignment,
    )


def take_reports(bridge: Bridge, params: dict[str, Any]) -> Reading:
    started = time.perf_counter()
    requested = window_for(params["hours"], params["bucket_seconds"], now=0)
    before = str(params.get("before") or "").strip()
    requested_end = before_stamp(before) if before else None
    requested_key = stamp_key(requested_end) if requested_end is not None else None
    requested_start_key = (requested_key[0] - timedelta(hours=params["hours"]), requested_key[1]) if requested_key else None
    script = reports_script(requested, before)
    result = bridge.run(script)
    source: dict[str, Any] = {}
    reach: dict[str, Any] = {}
    reports: list[dict[str, Any]] = []

    def build(payload: dict[str, Any]) -> list[Section]:
        start, end = payload.get("window_start"), payload.get("window_end")
        host_window = _host_window(start, end, requested)
        queried_at = payload.get("queried_at")
        query_key = stamp_key(queried_at)
        end_key = stamp_key(end)
        problem = "the report collector's window or query time failed validation"
        if requested_start_key is not None and query_key is not None and requested_start_key >= query_key:
            problem = "the requested report-time window begins at or after the machine's query time"
            host_window = None
        if query_key is None or end_key is None or end_key != min(query_key, requested_key or query_key):
            host_window = None
        source_data, rows = _source(payload.get("source") if host_window else None, start, end, problem=problem)
        source.update(source_data)
        reach.update(log_coverage(source, rows, start, end))
        future_end = requested_key is not None and query_key is not None and requested_key > query_key
        reach["covered_until"] = end if reach["covered_from"] is not None else None
        if future_end and reach["complete"] is not None:
            reach["complete"] = False
        if host_window and source["outcome"] in ("ok", "empty"):
            reports.extend(_reports(rows))
            buckets = _buckets(reports, host_window, reach)
            sections = ([Section("reports", "derived", reports, basis=BASIS)] if params.get("references") is True else [])
            sections.append(Section("buckets", "derived", buckets, basis=BASIS))
        else:
            sections = []
        return [*sections, Section("collection", "raw", {"window_start": start, "window_end": end, "queried_at": queried_at, "kernel_whea": source}),
                Section("coverage", "derived", {"kernel_whea": reach}, basis=REPORT_COVERAGE_BASIS)]

    reading = from_object("whea_reports", params, script, result, build)
    if not reading.observed:
        return reading
    if source["outcome"] not in ("ok", "empty"):
        reading.outcome, reading.count = source["outcome"], None
        reading.error = {"kind": reading.outcome, "detail": source["error"] or "The Kernel-WHEA query did not answer."}
    else:
        reading.outcome, reading.count = ("ok" if reports else "empty"), len(reports)
        collection_section = reading.section("collection")
        query_key = stamp_key(collection_section.data.get("queried_at")) if collection_section else None
        if requested_key is not None and query_key is not None and requested_key > query_key:
            reading.warnings.append("the requested end is after the machine's query time; the observed report window ends there and its start moves earlier too")
        if source["truncated"]:
            reading.warnings.append(f"the Kernel-WHEA query reached its {RECORD_CAP}-record limit; older reports were not returned")
        if isinstance(source.get("stopped"), dict):
            reading.warnings.append(f"the Kernel-WHEA query stopped after {source['returned']} reports: {source['stopped']['detail']}")
        if source["row_issues"]["unplaced"]:
            reading.warnings.append(f"{source['row_issues']['unplaced']} reports had no readable time; no bucket can be called quiet")
        if source["row_issues"]["outside_window"]:
            reading.warnings.append(f"{source['row_issues']['outside_window']} reports fell just outside the requested window and were not counted")
        unreadable = sum(report["header"] is None for report in reports)
        if unreadable:
            reading.warnings.append(f"{unreadable} returned reports have no readable fixed CPER header; their PreviousError flags are unknown")
        if source.get("log_enabled") is False:
            reading.warnings.append("The Kernel-WHEA channel is disabled; no new reports are being recorded there")
        if reach["complete"] is not True:
            reading.warnings.append("Kernel-WHEA report coverage is incomplete or could not be established for the requested window")
    reading.took_ms = int((time.perf_counter() - started) * 1000)
    return reading


def _source(value: Any, start: Any, end: Any, *, problem: str = "the report source or window failed validation") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fallback = {**log_metadata({}), "log": CHANNEL, "outcome": "failed", "error": problem,
                "returned": 0, "limit": RECORD_CAP, "truncated": None, "stopped": None,
                "row_issues": {"unplaced": 0, "outside_window": 0}}
    first, until = stamp_key(start), stamp_key(end)
    if not isinstance(value, dict) or first is None or until is None or first > until:
        return fallback, []
    outcome = value.get("outcome")
    if outcome in ("failed", "denied"):
        return {**fallback, **log_metadata(value), "outcome": outcome, "error": value.get("error") or "the source did not answer"}, []
    rows, stopped = value.get("records"), value.get("stopped")
    valid_stop = stopped is None or (isinstance(stopped, dict) and set(stopped) == {"kind", "detail"}
                                   and stopped["kind"] in ("failed", "denied") and isinstance(stopped["detail"], str)
                                   and 0 < len(stopped["detail"]) <= 300)
    valid = (outcome in ("ok", "empty") and isinstance(rows, list) and value.get("log") == CHANNEL
             and type(value.get("returned")) is int and value["returned"] == len(rows)
             and type(value.get("limit")) is int and value["limit"] == RECORD_CAP and len(rows) <= RECORD_CAP
             and valid_stop and "truncated" in value
             and (value.get("truncated") is None if stopped else type(value.get("truncated")) is bool)
             and (value.get("truncated") is not True or len(rows) == RECORD_CAP)
             and (outcome == "empty") == (len(rows) == 0)
             and (stopped is None or outcome == "ok" and bool(rows)) and value.get("error") is None)
    if not valid:
        return fallback, []
    kept: list[dict[str, Any]] = []
    issues = {"unplaced": 0, "outside_window": 0}
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_KEYS or row.get("LogName") != CHANNEL or row.get("ProviderName") != CHANNEL_PROVIDER or row.get("Id") != 20:
            return fallback, []
        record_id, stamp = row.get("RecordId"), row.get("TimeCreated")
        if type(record_id) is not int or record_id <= 0 or (stamp is not None and not isinstance(stamp, str)):
            return fallback, []
        header, length = row.get("HeaderHex"), row.get("PayloadBytes")
        if not valid_fixed_header_projection(header, length):
            return fallback, []
        at = stamp_key(stamp)
        if at is None:
            issues["unplaced"] += 1
            kept.append(row)
        elif first <= at < until:
            kept.append(row)
        else:
            delta = ((first[0] - at[0]) if at < first else (at[0] - until[0])).total_seconds()
            delta += (first[1] - at[1] if at < first else at[1] - until[1]) / 10_000_000
            if not 0 <= delta <= 0.001:
                return fallback, []
            issues["outside_window"] += 1
    if stopped and not kept:
        return {**fallback, **log_metadata(value), "outcome": stopped["kind"], "error": "the query stopped before any in-window report could be counted",
                "returned": len(rows), "stopped": stopped, "row_issues": issues}, []
    return {**log_metadata(value), "outcome": outcome, "returned": len(rows), "limit": RECORD_CAP,
            "truncated": value["truncated"], "stopped": stopped, "row_issues": issues, "error": None}, kept


def _reports(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [report_reference(row) for row in rows]


def _buckets(reports: list[dict[str, Any]], window: Window, reach: dict[str, Any]) -> dict[str, Any]:
    counts = [0] * window.count
    previous = [0] * window.count
    unknown_header = [0] * window.count
    severity = {"fatal": 0, "recoverable": 0, "corrected": 0, "informational": 0, "unknown": 0, "unreadable": 0}
    unplaced = 0
    for report in reports:
        header = report.get("header")
        level = header["severity"] if header is not None else "unreadable"
        severity[level if level in severity else "unknown"] += 1
        at = stamp_key(report.get("reported_at"))
        index = window.index(at[0].timestamp()) if at else None
        if index is None:
            unplaced += 1
            continue
        counts[index] += 1
        if report.get("header") is None:
            unknown_header[index] += 1
        elif report["header"]["previous_session"]:
            previous[index] += 1
    cutoff = stamp_key(reach.get("covered_from"))
    inclusive = reach.get("covered_from_inclusive") is True
    totals: list[int | None] = []
    for index, count in enumerate(counts):
        start = stamp_key(_stamp(window.start + index * window.bucket_seconds))
        covered = cutoff is not None and start is not None and (start > cutoff or inclusive and start == cutoff)
        totals.append(count if covered and not unplaced else None)
    active = [i for i, count in enumerate(counts) if count]
    return {"from": _stamp(window.start), "to": _stamp(window.end), "bucket_seconds": window.bucket_seconds,
            "bucket_count": window.count, "total": sum(counts), "unplaced": unplaced, "totals": totals,
            "unknown_buckets": sum(value is None for value in totals),
            "previous_session": sum(report.get("header") is not None and report["header"]["previous_session"] for report in reports),
            "header_unreadable": sum(report.get("header") is None for report in reports),
            "severity": severity,
            "returned": {"index": active, "count": [counts[i] for i in active],
                         "previous_session": [previous[i] for i in active],
                         "header_unreadable": [unknown_header[i] for i in active]}}


register(Spec(
    name="whea_reports",
    description="Kernel-WHEA event-20 reports in wall-clock buckets by report time, with independent channel coverage and fixed CPER header flags. PreviousError marks a condition from an earlier Windows session; no hardware-error burst or acceleration is inferred from report clustering.",
    classes=("raw", "derived"), take=take_reports,
    params=(Param("hours", "int", 24, "Hours preceding before, or the query time when before is empty; the actual start is bucket-aligned.", minimum=1, maximum=MAX_HOURS),
            Param("bucket_seconds", "int", 60, "The width of one report-time bucket.", minimum=1),
            Param("before", "str", "", "Exclusive report-time end with Z or an offset; empty uses the query time."),
            Param("references", "bool", False, "Include one bounded reference per returned report. Default false keeps broad timelines compact; use whea_window for a selected interval.")),
))
