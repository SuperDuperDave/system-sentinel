"""The log: ``events`` (records by level, and by window) and ``record`` (the log around a moment).

Both read the Windows event log with ``Get-WinEvent`` and return the records field
for field. ``record`` is the composer's most distinctive mechanism: the log does
not announce a freeze; the next start does, so the records *before* that start
are what the machine was doing.

Every form of both readings is one ``-FilterXml`` query, so the level and the window are answered
by the log's own index rather than by a scan; :func:`since_clause` is where a window is turned
into that index's vocabulary, for this reading and for the others that take one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..bridge import Bridge, BridgeResult
from ..reading import Param, Reading, Section, Spec, from_bridge, register
from .event_coverage import LOG_METADATA_SCRIPT, LOG_WINDOW_COVERAGE_BASIS, coverage, metadata, stamp_key

LOGS = ("System", "Application")
MAX_LOG_RECORDS = 2000  # per-request cap for the log and its progressively widened Record frame

# One projection for every log reading (events, record, whea, the stream), so every client sees the
# same record shape. It is built as a pscustomobject rather than Select-Object's calculated properties:
# Windows PowerShell 5.1 serializes a calculated property that holds an array as {"value": [...], "Count": n},
# and this way Properties is the array it is.
RECORD_FIELDS = """RecordId = $_.RecordId; Id = $_.Id; Level = $_.Level; LevelDisplayName = $_.LevelDisplayName;
        ProviderName = $_.ProviderName; ProviderId = if ($null -eq $_.ProviderId) { $null } else { $_.ProviderId.ToString('D') }; Version = $_.Version;
        MachineName = $_.MachineName; TaskDisplayName = $_.TaskDisplayName;
        TimeCreated = $_.TimeCreated.ToUniversalTime().ToString('o'); Message = $_.Message;
        Properties = @($_.Properties | ForEach-Object { if ($_.Value -is [byte[]]) { [System.BitConverter]::ToString($_.Value).Replace('-','') } else { $_.Value } })"""


def record_projection(extra: str = "") -> str:
    """The pipeline stage that turns an event into the shared record; ``extra`` adds fields after it."""
    fields = RECORD_FIELDS + (f";\n        {extra}" if extra else "")
    return f"ForEach-Object {{ [pscustomobject]@{{ {fields} }} }}"


RECORD_SELECT = record_projection()


def winevent(query: str) -> str:
    """Get-WinEvent reports a clean no-match as an error. Only that error is an empty result; every other one is a failure."""
    return f"""try {{ {query} }} catch {{ if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') {{ return }} else {{ throw }} }}"""


def query_list(log: str, body: str) -> str:
    """One FilterXml query over one log, around the select body the caller composed."""
    return f"<QueryList><Query Id='0' Path='{log}'><Select Path='{log}'>{body}</Select></Query></QueryList>"


def since_clause(since: str) -> tuple[str, str]:
    """A window as the log's own index answers it: what the script has to work out first, and the
    XPath clause that uses it.

    ``-FilterHashtable``'s StartTime does not honour a timestamp's Kind — the same instant as UTC
    and as local time returned different counts on this machine on 2026-09-21 — so a window is a
    ``TimeCreated`` clause against the index instead, and the clause carries its own conjunction so
    it can be appended to a selector that already matches a provider. ``boot`` is resolved on the
    machine, because only the machine knows when it started.
    """
    text = str(since or "").strip()
    if not text:
        return "", ""
    if text.lower() == "boot":
        return "$boot = (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime.ToUniversalTime()\n$since = $boot.AddTicks(-($boot.Ticks % 10000)).ToString('o')\n", " and TimeCreated[@SystemTime&gt;='$since']"
    try:
        return "", f" and TimeCreated[@SystemTime&gt;='{_utc_stamp(text)}']"
    except ValueError as exc:
        raise ValueError(f"not an ISO timestamp or the word 'boot' ({exc})") from exc


def events_query(log: str, levels: list[int], window: str) -> str:
    """The levels asked for and the window, as one select. No level asked for is every level, which
    is the only thing an empty list can honestly mean."""
    parts = [f"({' or '.join(f'Level={int(level)}' for level in levels)})"] if levels else []
    if window.strip():
        parts.append(window.strip().removeprefix("and ").strip())
    return query_list(log, f"*[System[{' and '.join(parts)}]]" if parts else "*")


def events_script(log: str, levels: list[int], count: int, since: str = "") -> str:
    prelude, window = since_clause(since)
    start = "$since" if since.strip().lower() == "boot" else (f"'{_utc_stamp(since)}'" if since.strip() else "$null")
    return log_records_script(log, events_query(log, levels, window), count, prelude=prelude, window_start=start)


def record_script(log: str, before: str, count: int) -> str:
    # The filter is XPath on the log itself, so the cost is the log's index, not a scan.
    body = f"*[System[TimeCreated[@SystemTime&lt;'{_utc_stamp(before)}']]]"
    return log_records_script(log, query_list(log, body), count, window_end=f"'{_utc_stamp(before)}'")


def log_records_script(
    log: str, query: str, count: int, *, prelude: str = "", window_start: str = "$null", window_end: str = "$null", projection: str = RECORD_SELECT,
) -> str:
    """One object with bounded matching rows and metadata observed after the query.

    A clean no-match must still return the object: a pipeline-level ``return`` would otherwise
    hide the log's retention boundary and turn an observed empty query into a missing response.
    """
    return LOG_METADATA_SCRIPT + prelude + f"""$xml = @"
{query}
"@
$queriedAt = (Get-Date).ToUniversalTime().ToString('o')
$found = [System.Collections.Generic.List[object]]::new()
$records = @(); $outcome = 'failed'; $errorText = $null; $truncated = $null; $stopped = $null
try {{
    Get-WinEvent -FilterXml ([xml]$xml) -MaxEvents {int(count) + 1} -ErrorAction Stop |
        & {{ process {{ [void]$found.Add($_) }} }}
    $truncated = $found.Count -gt {int(count)}
}} catch {{
    $kind = if ($_.CategoryInfo.Category -eq [System.Management.Automation.ErrorCategory]::PermissionDenied -or $_.Exception -is [System.UnauthorizedAccessException]) {{ 'denied' }} else {{ 'failed' }}
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*' -and $found.Count -eq 0) {{ $truncated = $false }}
    elseif ($found.Count) {{
        $detail = ([string]$_.Exception.Message -split '\r?\n')[0].Trim()
        if (-not $detail) {{ $detail = "the {log} query stopped early" }}
        if ($detail.Length -gt 300) {{
            $detail = $detail.Substring(0, 300)
            if ([char]::IsHighSurrogate($detail[299])) {{ $detail = $detail.Substring(0, 299) }}
        }}
        $stopped = [pscustomobject]@{{ kind = $kind; detail = $detail }}
    }} else {{
        $outcome = $kind; $errorText = [string]$_.Exception.Message
        if (-not $errorText) {{ $errorText = "the {log} query did not answer" }}
    }}
}}
if ($null -eq $errorText) {{
    try {{
        $records = @($found | Select-Object -First {int(count)} | {projection})
        if ($records.Count -ne [Math]::Min($found.Count, {int(count)})) {{ throw 'the event projection returned fewer records than the query' }}
        $outcome = if ($records.Count) {{ 'ok' }} else {{ 'empty' }}
    }} catch {{
        $records = @(); $outcome = 'failed'; $errorText = $_.Exception.Message; $truncated = $null; $stopped = $null
    }}
}}
$meta = Read-LogMetadata '{log}'
[pscustomobject]@{{
    log = '{log}'; outcome = $outcome; error = $errorText; returned = $records.Count; limit = {int(count)}
    truncated = $truncated; stopped = $stopped; records = @($records); metadata = $meta
    window_start = {window_start}; window_end = {window_end}; queried_at = $queriedAt
}}
"""


def _utc_stamp(before: str) -> str:
    """Parse the caller's timestamp and render it as the UTC form the event log's XPath expects."""
    text = before.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    utc = parsed.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def take_events(bridge: Bridge, params: dict[str, Any]) -> Reading:
    try:
        script = events_script(params["log"], params["levels"], params["count"], str(params.get("since") or ""))
    except ValueError as exc:
        raise ValueError(f"parameter 'since': {exc}") from exc
    result = bridge.run(script, depth=8)
    return from_log_collector("events", params, script, result, params["log"], params["count"], window=bool(params.get("since", "").strip()))


def take_record(bridge: Bridge, params: dict[str, Any]) -> Reading:
    try:
        script = record_script(params["log"], params["before"], params["count"])
    except ValueError as exc:
        raise ValueError(f"parameter 'before': not an ISO timestamp ({exc})") from exc
    result = bridge.run(script, depth=8)
    reading = from_log_collector("record", params, script, result, params["log"], params["count"], before=_utc_stamp(params["before"]))
    records = reading.section("records")
    if records and isinstance(records.data, list):
        records.data.reverse()  # oldest first: the reader follows time forward into the moment
    return reading


RECORD_COVERAGE_BASIS = (
    "A returned record before the requested moment establishes reach. Otherwise the oldest retained "
    "record, read after the matching query, must precede that moment; it can be later if the log "
    "wrapped or timestamps moved backward. This does not say all earlier "
    "records still exist; collection.truncated says whether the response stopped at its row limit."
)
RECENT_COVERAGE_BASIS = "The oldest retained record was read after the newest-record query. No start was requested, so window completeness does not apply."


def from_log_collector(
    name: str, params: dict[str, Any], script: str, result: BridgeResult, log: str, limit: int, *, window: bool = False, before: str | None = None,
) -> Reading:
    """Keep returned evidence even when the independent retention check cannot answer."""
    reading = from_bridge(name, params, script, result, shape="object")
    if not reading.observed:
        return reading
    body = reading.section("records")
    assert body is not None
    payload = body.data
    rows, stopped = payload.get("records"), payload.get("stopped")
    valid_stop = stopped is None or (
        isinstance(stopped, dict) and set(stopped) == {"kind", "detail"} and stopped["kind"] in ("failed", "denied")
        and isinstance(stopped["detail"], str) and 0 < len(stopped["detail"]) <= 300
    )
    source_outcome = payload.get("outcome")
    if source_outcome in ("failed", "denied") and payload.get("log") == log:
        reading.outcome = source_outcome
        reading.error = {"kind": source_outcome, "detail": str(payload.get("error") or "the event query did not answer")}
        reading.sections = []
        return reading
    valid = (
        payload.get("log") == log and source_outcome in ("ok", "empty")
        and isinstance(rows, list) and len(rows) <= limit and valid_stop
        and type(payload.get("limit")) is int and payload["limit"] == limit
        and type(payload.get("returned")) is int and payload["returned"] == len(rows)
        and (payload.get("truncated") is None if stopped is not None else type(payload.get("truncated")) is bool)
        and (payload.get("truncated") is not True or len(rows) == limit)
        and (source_outcome == "empty") == (len(rows) == 0)
        and (stopped is None or source_outcome == "ok")
        and payload.get("error") is None
        and all(isinstance(row, dict) and type(row.get("RecordId")) is int and row["RecordId"] > 0 for row in rows)
    )
    if not valid:
        reading.outcome = "failed"
        reading.error = {"kind": "failed", "detail": "The log collector returned an invalid query result or record array."}
        reading.sections = []
        return reading
    reading.sections = [Section("records", "raw", rows)]
    reading.count = len(rows)
    reading.outcome = source_outcome
    source: dict[str, Any] = {
        "log": log, "outcome": source_outcome, "limit": limit, "returned": len(rows), "truncated": payload["truncated"], "stopped": stopped,
        "window_start": payload.get("window_start"), "window_end": payload.get("window_end"), "queried_at": payload.get("queried_at"),
        "row_issues": {"unplaced": sum(stamp_key(row.get("TimeCreated")) is None for row in rows)},
    }
    meta = payload.get("metadata")
    valid_meta = meta if isinstance(meta, dict) and meta.get("log") == log else {}
    source.update(metadata(valid_meta))
    source["log"] = log
    reading.sections.append(Section("collection", "raw", source))
    if not valid_meta:
        reading.warnings.append("the log's retention metadata did not identify the requested source")
    if stopped is not None:
        reading.warnings.append(f"the event query stopped early after returning {len(rows)} records: {stopped['detail']}")
    if source["row_issues"]["unplaced"]:
        reading.warnings.append(f"{source['row_issues']['unplaced']} returned event times could not be read; time-based coverage may be incomplete")
    if window and source["truncated"] is True:
        reading.warnings.append(f"the event query reached its {limit}-record limit; older matching records in the requested window were not returned")
    if source.get("log_state") == "ok" and source.get("log_enabled") is False:
        reading.warnings.append("the Windows event log is disabled; absence of new records cannot be established")

    retained = source.get("log_oldest") if source.get("oldest_state") == "ok" and stamp_key(source.get("log_oldest")) else None
    if window:
        start, end = source["window_start"], source["queried_at"]
        reach = coverage(source, reading.section("records").data, start, end) if isinstance(start, str) and isinstance(end, str) else {"covered_from": None, "covered_from_inclusive": None, "complete": None}
        reading.sections.append(Section("coverage", "derived", {"log": log, "retained_from": retained, **reach}, basis=LOG_WINDOW_COVERAGE_BASIS))
        if stamp_key(start) and stamp_key(end) and stamp_key(start) >= stamp_key(end):
            reading.warnings.append("the requested start is at or after the machine's query time; window completeness cannot yet be established")
        elif retained and stamp_key(start) and stamp_key(retained) and stamp_key(retained) >= stamp_key(start):
            reading.warnings.append("the log's oldest retained record does not precede the requested window; earlier events may be unavailable")
        elif reach["covered_from"] is None:
            reading.warnings.append("the requested window's retention reach could not be established")
    elif before is not None:
        oldest = stamp_key(retained)
        boundary = stamp_key(before)
        reaches = True if rows else False if oldest and boundary and oldest >= boundary else (
            True if source.get("log_state") == "ok" and source.get("log_enabled") is True and source.get("log_mode") == "Circular" and oldest and boundary else None
        )
        reading.sections.append(Section("coverage", "derived", {"log": log, "retained_from": retained, "reaches_before": reaches}, basis=RECORD_COVERAGE_BASIS))
        if reaches is False:
            reading.warnings.append("the requested moment is at or before this log's oldest retained record; earlier records are unavailable")
        elif reaches is True and oldest and boundary and oldest >= boundary:
            reading.warnings.append("returned records precede the moment, but the oldest retained record read afterward is later; the log may have wrapped or timestamps moved backward")
        elif reaches is None:
            reading.warnings.append("the log's retention reach before the requested moment could not be established")
    else:
        reading.sections.append(Section("coverage", "derived", {
            "log": log, "retained_from": retained, "covered_from": None, "covered_from_inclusive": None, "complete": None,
        }, basis=RECENT_COVERAGE_BASIS))
    return reading


register(
    Spec(
        name="events",
        description=(
            "Records from a Windows log by level: what the machine logged as critical, error, warning or "
            "information. Give it a window and it answers from that moment, or from Windows' reported "
            "kernel-session start, instead of from the most recent records. Retention reach is shown separately."
        ),
        classes=("raw", "derived"),
        take=take_events,
        params=(
            Param("log", "str", "System", "Which log.", choices=LOGS),
            Param("levels", "list[int]", [1, 2], "Levels to include: 1 critical, 2 error, 3 warning, 4 information."),
            Param("count", "int", 50, "How many of the most recent records.", minimum=1, maximum=MAX_LOG_RECORDS),
            Param("since", "str", "", "ISO timestamp, or 'boot' for Windows' reported kernel-session start. Empty for the most recent records."),
        ),
        private=("MachineName", "user names inside Message", "profile paths inside Message", "CPER bytes in binary Properties"),
    )
)

register(
    Spec(
        name="record",
        description="The log around a moment: retained records before a timestamp, oldest first, ending at the moment. Coverage says whether the log still reaches before that moment; take it with a stop's started_at for context before restart.",
        classes=("raw", "derived"),
        take=take_record,
        params=(
            Param("before", "str", None, "ISO timestamp; the records strictly before it are returned."),
            Param("count", "int", 50, "How many records before the moment.", minimum=1, maximum=MAX_LOG_RECORDS),
            Param("log", "str", "System", "Which log.", choices=LOGS),
        ),
        private=("MachineName", "user names inside Message", "profile paths inside Message", "CPER bytes in binary Properties"),
    )
)
