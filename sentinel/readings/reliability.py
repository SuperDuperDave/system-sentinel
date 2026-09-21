"""Windows' own second opinion: ``reliability``.

The Reliability Analysis Component keeps a record of this machine that nothing else here keeps:
the failures Windows itself counted, and the stability index it computes from them, one value for
every hour. It is worth having beside the tool's own readings precisely because it is somebody
else's arithmetic — it can disagree, and a disagreement is a lead. Windows lowers the index
because it counted a failure, so a fall points at the day it counted; it is not a measure of how
the machine is, and nothing here treats it as one.

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
$since = (Get-Date).AddDays(-{days})

# Each class in its own try: one of them answering and the other not is still an observed reading,
# as long as the one that did not is a warning rather than a silence.
# Newest first and bounded: the only other unbounded raw section would be this one, and an agent
# taking the reading pays for every Message string in it. The bound says so when it bites.
$records = @()
$held = 0
try {
    $all = @(Get-CimInstance Win32_ReliabilityRecords -ErrorAction Stop |
        Where-Object { $_.TimeGenerated -and $_.TimeGenerated -ge $since } |
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
} catch { $warnings += "Win32_ReliabilityRecords did not answer: $($_.Exception.Message)" }

# One row per hour, on UTC hour boundaries, for about the last thirty days however many were asked
# for: the window the class itself keeps is shorter than the window this reading can be given.
$stability = @()
try {
    $stability = @(Get-CimInstance Win32_ReliabilityStabilityMetrics -ErrorAction Stop |
        Where-Object { $_.TimeGenerated -and $_.TimeGenerated -ge $since } |
        ForEach-Object {
            [pscustomobject]@{
                TimeGenerated        = $_.TimeGenerated.ToUniversalTime().ToString('o')
                StartMeasurementDate = $(if ($_.StartMeasurementDate) { $_.StartMeasurementDate.ToUniversalTime().ToString('o') } else { $null })
                EndMeasurementDate   = $(if ($_.EndMeasurementDate) { $_.EndMeasurementDate.ToUniversalTime().ToString('o') } else { $null })
                SystemStabilityIndex = $(if ($null -ne $_.SystemStabilityIndex) { [double]$_.SystemStabilityIndex } else { $null })
                RelID                = $_.RelID
            }
        })
} catch { $warnings += "Win32_ReliabilityStabilityMetrics did not answer: $($_.Exception.Message)" }

[pscustomobject]@{
    records     = $records
    stability   = $stability
    window_days = {days}
    warnings    = $warnings
}
"""

DAYS_BASIS = (
    "One entry per UTC day the returned rows cover, oldest first: the stability index at the last "
    "hour of that day that reported one, the lowest index any of that day's hours reported, and "
    "that day's reliability records counted by the source that wrote them. The window is the span "
    "the rows actually cover, not the days asked for; the sources are that whole window counted, "
    "the current index is the last one reported, and the lowest is the day whose lowest hour was "
    "lowest. Windows lowers the index when it counts a failure, so a day's fall points at what "
    "Windows counted that day; it is not a second failure and not a measure of the machine's health."
)


def reliability_script(days: int) -> str:
    return RELIABILITY_SCRIPT_TEMPLATE.replace("{days}", str(int(days))).replace("{cap}", str(RECORD_CAP))


def reliability_days(records: list[dict[str, Any]], stability: list[dict[str, Any]]) -> dict[str, Any]:
    """The window, day by day: where the index stood, how low it went, and what Windows counted."""
    hours: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in stability:
        day = _day(row.get("TimeGenerated"))
        if day:
            hours[day].append(row)

    counted: dict[str, Counter] = defaultdict(Counter)
    for record in records:
        day = _day(record.get("TimeGenerated"))
        if day:
            counted[day][str(record.get("SourceName") or "unnamed source")] += 1

    days: list[dict[str, Any]] = []
    for day in sorted(set(hours) | set(counted)):
        ordered = sorted(hours.get(day, []), key=lambda r: str(r.get("TimeGenerated") or ""))
        indexes = [value for value in (_float(r.get("SystemStabilityIndex")) for r in ordered) if value is not None]
        days.append(
            {
                "day": day,
                "index_last": indexes[-1] if indexes else None,
                "index_min": min(indexes) if indexes else None,
                "records": dict(sorted(counted.get(day, Counter()).items(), key=lambda kv: (-kv[1], kv[0]))),
            }
        )

    stamps = sorted(str(r.get("TimeGenerated")) for r in list(records) + list(stability) if r.get("TimeGenerated"))
    sources = Counter(str(r.get("SourceName") or "unnamed source") for r in records)
    lowest = min((d for d in days if d["index_min"] is not None), key=lambda d: d["index_min"], default=None)
    return {
        "from": stamps[0] if stamps else None,
        "to": stamps[-1] if stamps else None,
        "days": days,
        "sources": dict(sorted(sources.items(), key=lambda kv: (-kv[1], kv[0]))),
        "index_now": next((d["index_last"] for d in reversed(days) if d["index_last"] is not None), None),
        "index_lowest": {"day": lowest["day"], "index": lowest["index_min"]} if lowest else None,
    }


def take_reliability(bridge: Bridge, params: dict[str, Any]) -> Reading:
    days = int(params["days"])
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"parameter 'days': must be between 1 and {MAX_DAYS}")

    script = reliability_script(days)
    result = bridge.run(script, depth=DEEP)

    def build(payload: dict[str, Any]) -> list[Section]:
        records = [r for r in (payload.get("records") or []) if isinstance(r, dict)]
        stability = [r for r in (payload.get("stability") or []) if isinstance(r, dict)]
        return [
            Section("records", "raw", records),
            Section("stability", "raw", stability),
            Section("days", "derived", reliability_days(records, stability), basis=DAYS_BASIS),
        ]

    reading = from_object("reliability", params, script, result, build)
    if reading.observed:
        records = reading.section("records").data
        reading.count = len(records)
        if not records and not reading.section("stability").data:
            reading.outcome = "empty"  # Windows keeps no record of this machine yet: a finding
    return reading


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
            "Windows' own record of this machine: the failures the Reliability Analysis Component "
            "counted, its hourly stability index, and both rolled up by day — where the index stood "
            "at each day's end, how low it went, and what Windows counted that day, by source. "
            "Windows lowers the index because it counted a failure, so a fall is a pointer at the "
            "day, not a diagnosis. An empty result is a finding: Windows kept no record here."
        ),
        classes=("raw", "derived"),
        take=take_reliability,
        params=(Param("days", "int", 30, f"How many days back; 1 to {MAX_DAYS}."),),
        private=("User", "ComputerName", "user names inside Message"),
        heavy=True,
    )
)
