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

from datetime import datetime, timezone
from typing import Any

from ..bridge import Bridge
from ..reading import Param, Reading, Spec, from_bridge, register

LOGS = ("System", "Application")

# One projection for every log reading (events, record, whea, the stream), so every client sees the
# same record shape. It is built as a pscustomobject rather than Select-Object's calculated properties:
# Windows PowerShell 5.1 serializes a calculated property that holds an array as {"value": [...], "Count": n},
# and this way Properties is the array it is.
RECORD_FIELDS = """RecordId = $_.RecordId; Id = $_.Id; Level = $_.Level; LevelDisplayName = $_.LevelDisplayName;
        ProviderName = $_.ProviderName; MachineName = $_.MachineName; TaskDisplayName = $_.TaskDisplayName;
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
        return "$since = (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime.ToUniversalTime().ToString('o')\n", " and TimeCreated[@SystemTime&gt;='$since']"
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
    return prelude + f"""$xml = @"
{events_query(log, levels, window)}
"@
""" + winevent(
        f"""Get-WinEvent -FilterXml $xml -MaxEvents {int(count)} -ErrorAction Stop |
    {RECORD_SELECT}"""
    )


def record_script(log: str, before: str, count: int) -> str:
    # The filter is XPath on the log itself, so the cost is the log's index, not a scan.
    body = f"*[System[TimeCreated[@SystemTime&lt;'{_utc_stamp(before)}']]]"
    return f"""$xml = @"
{query_list(log, body)}
"@
""" + winevent(
        f"""Get-WinEvent -FilterXml $xml -MaxEvents {int(count)} -ErrorAction Stop |
    {RECORD_SELECT}"""
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
    try:
        script = events_script(params["log"], params["levels"], params["count"], str(params.get("since") or ""))
    except ValueError as exc:
        raise ValueError(f"parameter 'since': {exc}") from exc
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
        description=(
            "Records from a Windows log by level: what the machine logged as critical, error, warning or "
            "information. Give it a window and it answers from that moment, or from this session's start, "
            "instead of from the most recent records."
        ),
        classes=("raw",),
        take=take_events,
        params=(
            Param("log", "str", "System", "Which log.", choices=LOGS),
            Param("levels", "list[int]", [1, 2], "Levels to include: 1 critical, 2 error, 3 warning, 4 information."),
            Param("count", "int", 50, "How many of the most recent records."),
            Param("since", "str", "", "ISO timestamp, or the word 'boot' for this session only. Empty for the most recent records."),
        ),
        private=("MachineName", "user names inside Message", "profile paths inside Message"),
    )
)

register(
    Spec(
        name="record",
        description="The log around a moment: the records before a timestamp, oldest first, ending at the moment. Take it with a stop's started_at from the crash reading to see what the machine was doing before it froze.",
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
