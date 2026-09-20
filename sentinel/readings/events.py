"""The log: ``events`` (records by level) and ``record`` (the log around a moment).

Both read the Windows event log with ``Get-WinEvent`` and return the records field
for field. ``record`` is the composer's most distinctive mechanism: the log does
not announce a freeze; the next start does, so the records *before* that start
are what the machine was doing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..bridge import Bridge
from ..reading import Param, Reading, Spec, from_bridge, register

LOGS = ("System", "Application")

# One projection for every log reading, so every client sees the same record shape.
_SELECT = """Select-Object RecordId, Id, LevelDisplayName, Level, ProviderName, MachineName, TaskDisplayName,
    @{Name='TimeCreated'; Expression={ $_.TimeCreated.ToUniversalTime().ToString('o') }},
    Message,
    @{Name='Properties'; Expression={ @($_.Properties | ForEach-Object { if ($_.Value -is [byte[]]) { [System.BitConverter]::ToString($_.Value).Replace('-','') } else { $_.Value } }) }}"""


def winevent(query: str) -> str:
    """Get-WinEvent reports a clean no-match as an error. Only that error is an empty result; every other one is a failure."""
    return f"""try {{ {query} }} catch {{ if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') {{ return }} else {{ throw }} }}"""


def events_script(log: str, levels: list[int], count: int) -> str:
    level_list = ",".join(str(int(l)) for l in levels)
    return winevent(
        f"""Get-WinEvent -FilterHashtable @{{LogName='{log}'; Level={level_list}}} -MaxEvents {int(count)} -ErrorAction Stop |
    {_SELECT}"""
    )


def record_script(log: str, before: str, count: int) -> str:
    # The filter is XPath on the log itself, so the cost is the log's index, not a scan.
    stamp = _utc_stamp(before)
    return f"""$xml = @"
<QueryList><Query Id="0" Path="{log}"><Select Path="{log}">*[System[TimeCreated[@SystemTime&lt;'{stamp}']]]</Select></Query></QueryList>
"@
""" + winevent(
        f"""Get-WinEvent -FilterXml $xml -MaxEvents {int(count)} -ErrorAction Stop |
    {_SELECT}"""
    )


def _utc_stamp(before: str) -> str:
    """Parse the caller's timestamp and render it as the UTC form the event log's XPath expects."""
    text = before.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    utc = parsed.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def take_events(bridge: Bridge, params: dict[str, Any]) -> Reading:
    script = events_script(params["log"], params["levels"], params["count"])
    result = bridge.run(script)
    return from_bridge("events", params, script, result)


def take_record(bridge: Bridge, params: dict[str, Any]) -> Reading:
    try:
        script = record_script(params["log"], params["before"], params["count"])
    except ValueError as exc:
        raise ValueError(f"parameter 'before': not an ISO timestamp ({exc})") from exc
    result = bridge.run(script)
    reading = from_bridge("record", params, script, result)
    records = reading.section("records")
    if records and isinstance(records.data, list):
        records.data.reverse()  # oldest first: the reader follows time forward into the moment
    return reading


register(
    Spec(
        name="events",
        description="Records from a Windows log by level: what the machine logged as critical, error, warning or information.",
        classes=("raw",),
        take=take_events,
        params=(
            Param("log", "str", "System", "Which log.", choices=LOGS),
            Param("levels", "list[int]", [1, 2], "Levels to include: 1 critical, 2 error, 3 warning, 4 information."),
            Param("count", "int", 50, "How many of the most recent records."),
        ),
        private=("MachineName", "user names inside Message", "profile paths inside Message"),
    )
)

register(
    Spec(
        name="record",
        description="The log around a moment: the records before a timestamp, oldest first, ending at the moment. Take it with the timestamp of a start (Kernel-Power 41, EventLog 6008) to see what the machine was doing before it froze.",
        classes=("raw",),
        take=take_record,
        params=(
            Param("before", "str", None, "ISO timestamp; the records strictly before it are returned."),
            Param("count", "int", 50, "How many records before the moment."),
            Param("log", "str", "System", "Which log.", choices=LOGS),
        ),
        private=("MachineName", "user names inside Message", "profile paths inside Message"),
    )
)
