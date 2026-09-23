"""Windows' own second opinion: ``reliability``.

The Reliability Analysis Component keeps a record of this machine that nothing else here keeps:
events related to reliability (including informational events), and Windows' stability index,
one value for every hour. It is worth having beside the tool's own readings precisely because
it is somebody else's arithmetic — it can disagree, and a disagreement is a lead. A fall in
the index points at a day to inspect; it does not name the event that caused it.

Both classes answer without elevation, and either can hold nothing — a machine with no history,
a runner that was built this morning — so an empty result is a finding, not a failure. The script
collects and converts the times; the day-by-day rollup is in Python beside the rule that names it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from ..bridge import Bridge
from ..reading import Param, Reading, Section, Spec, from_object, register

MAX_DAYS = 366
RECORD_CAP = 500

# A record holds its insertion strings, and the payload holds both lists: one level deeper than
# the bridge's default serialization.
DEEP = 8

RELIABILITY_SCRIPT_TEMPLATE = r"""
$warnings = @()
$until = Get-Date
$since = $until.AddDays(-{days})

# Each class in its own try: one of them answering and the other not is still an observed reading,
# as long as the one that did not is a warning rather than a silence.
# Newest first and bounded: the only other unbounded raw section would be this one, and an agent
# taking the reading pays for every Message string in it. The bound says so when it bites.
$records = @()
$held = $null
$records_outcome = 'failed'
$records_error = $null
try {
    $all = @(Get-CimInstance Win32_ReliabilityRecords -ErrorAction Stop |
        Where-Object { $_.TimeGenerated -and $_.TimeGenerated -ge $since -and $_.TimeGenerated -le $until } |
        Sort-Object TimeGenerated -Descending)
    $held = $all.Count
    $records = @($all | Select-Object -First {cap} |
        ForEach-Object {
            [pscustomobject]@{
                SourceName       = $_.SourceName
                EventIdentifier  = $_.EventIdentifier
                TimeGenerated    = $_.TimeGenerated.ToUniversalTime().ToString('o')
                ProductName      = $_.ProductName
                Message          = $_.Message
                Logfile          = $_.Logfile
                RecordNumber     = $_.RecordNumber
                InsertionStrings = @($_.InsertionStrings)
                User             = $_.User
                ComputerName     = $_.ComputerName
            }
        })
    if ($held -gt {cap}) { $warnings += "the window holds $held reliability records; only the newest {cap} are returned" }
    $records_outcome = if ($records.Count) { 'ok' } else { 'empty' }
} catch {
    $records = @()
    $held = $null
    $records_outcome = if ($_.CategoryInfo.Category -eq 'PermissionDenied' -or $_.Exception -is [System.UnauthorizedAccessException]) { 'denied' } else { 'failed' }
    $records_error = $_.Exception.Message
}

# One row per hour, on UTC hour boundaries, for about the last thirty days however many were asked
# for: the window the class itself keeps is shorter than the window this reading can be given.
$stability = @()
$stability_outcome = 'failed'
$stability_error = $null
try {
    $stability = @(Get-CimInstance Win32_ReliabilityStabilityMetrics -ErrorAction Stop |
        Where-Object { $_.TimeGenerated -and $_.TimeGenerated -ge $since -and $_.TimeGenerated -le $until } |
        ForEach-Object {
            [pscustomobject]@{
                TimeGenerated        = $_.TimeGenerated.ToUniversalTime().ToString('o')
                StartMeasurementDate = $(if ($_.StartMeasurementDate) { $_.StartMeasurementDate.ToUniversalTime().ToString('o') } else { $null })
                EndMeasurementDate   = $(if ($_.EndMeasurementDate) { $_.EndMeasurementDate.ToUniversalTime().ToString('o') } else { $null })
                SystemStabilityIndex = $(if ($null -ne $_.SystemStabilityIndex) { [double]$_.SystemStabilityIndex } else { $null })
                RelID                = $_.RelID
            }
        })
    $stability_outcome = if ($stability.Count) { 'ok' } else { 'empty' }
} catch {
    $stability = @()
    $stability_outcome = if ($_.CategoryInfo.Category -eq 'PermissionDenied' -or $_.Exception -is [System.UnauthorizedAccessException]) { 'denied' } else { 'failed' }
    $stability_error = $_.Exception.Message
}

[pscustomobject]@{
    records     = $records
    stability   = $stability
    window_days = {days}
    warnings    = $warnings
    collection  = [pscustomobject]@{
        window_start = $since.ToUniversalTime().ToString('o')
        window_end = $until.ToUniversalTime().ToString('o')
        records = [pscustomobject]@{
            outcome = $records_outcome; available = $held; returned = $records.Count
            limit = {cap}; error = $records_error
        }
        stability = [pscustomobject]@{
            outcome = $stability_outcome
            available = $(if ($stability_outcome -in @('ok', 'empty')) { $stability.Count } else { $null })
            returned = $stability.Count; limit = $null; error = $stability_error
        }
    }
}
"""

DAYS_BASIS = (
    "One entry per UTC day the returned rows cover, oldest first: the stability index at the last "
    "hour of that day that reported one, the lowest index any of that day's hours reported, and "
    "that day's reliability records counted by their source and event identifier. These include "
    "informational events, such as successful updates. The window is the span "
    "the rows actually cover, not the days asked for; the sources are that whole window counted, "
    "the current index is the last one reported, and the lowest is the day whose lowest hour was "
    "lowest. A day's index fall points at a day to inspect; it does not establish which event "
    "caused it or diagnose the machine."
    " If the reliability-record source did not answer, daily records, event types and the window's "
    "source counts are null: the available stability index does not establish zero events."
)


def reliability_script(days: int) -> str:
    return RELIABILITY_SCRIPT_TEMPLATE.replace("{days}", str(int(days))).replace("{cap}", str(RECORD_CAP))


def reliability_days(records: list[dict[str, Any]], stability: list[dict[str, Any]], *, records_observed: bool = True) -> dict[str, Any]:
    """The window, day by day: where the index stood, how low it went, and what Windows counted."""
    hours: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in stability:
        day = _day(row.get("TimeGenerated"))
        if day:
            hours[day].append(row)

    counted: dict[str, Counter] = defaultdict(Counter)
    typed: dict[str, Counter] = defaultdict(Counter)
    for record in records:
        day = _day(record.get("TimeGenerated"))
        if day:
            source = str(record.get("SourceName") or "unnamed source")
            counted[day][source] += 1
            typed[day][(source, record.get("EventIdentifier"))] += 1

    days: list[dict[str, Any]] = []
    for day in sorted(set(hours) | set(counted)):
        ordered = sorted(hours.get(day, []), key=lambda r: str(r.get("TimeGenerated") or ""))
        indexes = [value for value in (_float(r.get("SystemStabilityIndex")) for r in ordered) if value is not None]
        days.append(
            {
                "day": day,
                "index_last": indexes[-1] if indexes else None,
                "index_min": min(indexes) if indexes else None,
                "records": dict(sorted(counted.get(day, Counter()).items(), key=lambda kv: (-kv[1], kv[0]))) if records_observed else None,
                "event_types": [
                    {"source": source, "event_id": event_id, "count": count}
                    for (source, event_id), count in sorted(
                        typed.get(day, Counter()).items(), key=lambda kv: (-kv[1], kv[0][0], str(kv[0][1]))
                    )
                ] if records_observed else None,
            }
        )

    stamps = sorted(str(r.get("TimeGenerated")) for r in list(records) + list(stability) if r.get("TimeGenerated"))
    sources = Counter(str(r.get("SourceName") or "unnamed source") for r in records)
    lowest = min((d for d in days if d["index_min"] is not None), key=lambda d: d["index_min"], default=None)
    return {
        "from": stamps[0] if stamps else None,
        "to": stamps[-1] if stamps else None,
        "days": days,
        "sources": dict(sorted(sources.items(), key=lambda kv: (-kv[1], kv[0]))) if records_observed else None,
        "index_now": next((d["index_last"] for d in reversed(days) if d["index_last"] is not None), None),
        "index_lowest": {"day": lowest["day"], "index": lowest["index_min"]} if lowest else None,
    }


def take_reliability(bridge: Bridge, params: dict[str, Any]) -> Reading:
    days = int(params["days"])
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"parameter 'days': must be between 1 and {MAX_DAYS}")

    script = reliability_script(days)
    result = bridge.run(script, depth=DEEP)
    coverage: dict[str, Any] = {}

    def build(payload: dict[str, Any]) -> list[Section]:
        collection = payload.get("collection")
        collection = collection if isinstance(collection, dict) else {}
        coverage.update({"window_start": collection.get("window_start"), "window_end": collection.get("window_end")})
        rows: dict[str, list[dict[str, Any]]] = {}
        for name in ("records", "stability"):
            source, rows[name] = _source(collection.get(name), payload.get(name))
            coverage[name] = source
        records, stability = rows["records"], rows["stability"]
        return [
            Section("records", "raw", records),
            Section("stability", "raw", stability),
            Section("days", "derived", reliability_days(records, stability, records_observed=coverage["records"]["outcome"] in ("ok", "empty")), basis=DAYS_BASIS),
            Section("collection", "raw", coverage),
        ]

    reading = from_object("reliability", params, script, result, build)
    if reading.observed:
        records = reading.section("records").data
        failures = [name for name in ("records", "stability") if coverage[name]["outcome"] not in ("ok", "empty")]
        for name in failures:
            reading.warnings.append(f"Reliability {name} did not answer: {coverage[name]['error']}")
        reading.count = len(records) if "records" not in failures else None
        if records or reading.section("stability").data:
            reading.outcome = "ok"  # Useful partial evidence stays available, with its source limits.
        elif failures:
            reading.outcome = "denied" if all(coverage[name]["outcome"] == "denied" for name in failures) else "failed"
            reading.count = None
            reading.error = {"kind": reading.outcome, "detail": "No reliability history could be established because " + " and ".join(failures) + " did not answer."}
        else:
            reading.outcome = "empty"  # Both sources answered and neither returned history.
    return reading


def _source(value: Any, rows: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """An empty array cannot certify that a source was read; require its completed result."""
    problem = "the collector did not return a valid source outcome"
    if isinstance(value, dict) and value.get("outcome") in ("failed", "denied"):
        return {**value, "available": None, "returned": 0, "error": value.get("error") or "the source did not answer"}, []
    if isinstance(value, dict) and value.get("outcome") in ("ok", "empty"):
        valid_rows = isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
        available, returned = value.get("available"), value.get("returned")
        limit = value.get("limit")
        valid_limit = "limit" in value and (limit is None or type(limit) is int and limit > 0)
        valid_counts = type(available) is int and type(returned) is int and available >= returned >= 0
        complete = valid_counts and valid_limit and returned == (available if limit is None else min(available, limit))
        if valid_rows and complete and returned == len(rows) and (value["outcome"] == "empty") == (returned == 0):
            return {**value, "error": None}, rows
        problem = "the collector's source outcome and returned rows disagree"
    return {"outcome": "failed", "available": None, "returned": 0, "limit": None, "error": problem}, []


def _day(stamp: Any) -> str | None:
    moment = _parse(stamp)
    return moment.date().isoformat() if moment else None


def _parse(stamp: Any) -> datetime | None:
    text = str(stamp or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC)


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


register(
    Spec(
        name="reliability",
        description=(
            "Windows' own record of this machine: reliability-related events, including informational "
            "events, its hourly stability index, and both rolled up by day — where the index stood "
            "at each day's end, how low it went, and what Windows returned by source and event ID. "
            "A fall is a pointer at a day, not a cause finding. An empty result is a finding: "
            "Windows kept no record here."
        ),
        classes=("raw", "derived"),
        take=take_reliability,
        params=(Param("days", "int", 30, f"How many days back; 1 to {MAX_DAYS}.", minimum=1, maximum=MAX_DAYS),),
        private=("User", "ComputerName", "user names inside Message"),
        heavy=True,
    )
)
