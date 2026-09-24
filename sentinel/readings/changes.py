"""Recent installation and device-configuration evidence, with one result per Windows log.

These records are leads before an incident, not proof that a change caused it. A driver inventory's
authored date is not its installation date; this reading uses the event log's own timestamps.
Raw records contain a deliberately limited projection. Kernel-PnP's message and device instance
identifiers can contain serials, so neither is collected into this reading.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ..bridge import Bridge
from ..reading import Param, Reading, Section, Spec, from_object, register
from .event_coverage import LOG_METADATA_SCRIPT, WINDOW_COVERAGE_BASIS, window_coverage
from .event_coverage import metadata as _metadata
from .event_coverage import parse_stamp as _parse
from .event_coverage import stamp_key as _stamp_key
from .events import _utc_stamp

MAX_HOURS = 2160
MAX_COUNT = 500
DEPTH = 9
SOURCES = ("windows_update", "device_configuration", "msi")
SOURCE_EXPECTED = {
    "windows_update": ("System", "Microsoft-Windows-WindowsUpdateClient", {19, 20}),
    "device_configuration": ("Microsoft-Windows-Kernel-PnP/Configuration", "Microsoft-Windows-Kernel-PnP", {400}),
    "msi": ("Application", "MsiInstaller", {1033, 1034}),
}
SAFE_DATA_KEYS = {
    "windows_update": {"updateTitle", "updateGuid", "updateRevisionNumber", "serviceGuid", "errorCode"},
    "device_configuration": {"DriverName", "ClassGuid", "DriverDate", "DriverVersion", "DriverProvider", "DriverInbox", "DriverRank", "DeviceUpdated", "Status"},
    "msi": {f"[{index}]" for index in range(5)},
}
RAW_KEYS = {"Log", "RecordId", "Id", "ProviderName", "Version", "Level", "TimeCreated", "Data", "FieldCount", "OmittedFieldCount", "ProjectionError"}
_KB = re.compile(r"\bKB\d{4,8}\b", re.I)
_MSI_SUCCESS = {0, 1641, 3010}

# Windows Event Log evaluates both time bounds. Each source gets its own catch and one extra
# record, so a failed or busy log cannot be mistaken for an empty one and a reached cap is exact.
CHANGES_SCRIPT = r"""
{before_assignment}
# Windows Event Log's XPath compares SystemTime at millisecond precision. Keep the reported
# window and validation at that same precision so a fractional boundary cannot omit valid rows.
$until = $until.AddTicks(-($until.Ticks % 10000))
$since = $until.AddHours(-{hours})
$startIso = $since.ToString('o')
$endIso = $until.ToString('o')
$queriedAt = (Get-Date).ToUniversalTime().ToString('o')
$limit = {count}
$specs = @(
    [pscustomobject]@{{ name = 'windows_update'; log = 'System'; provider = 'Microsoft-Windows-WindowsUpdateClient'; ids = '(EventID=19 or EventID=20)' }},
    [pscustomobject]@{{ name = 'device_configuration'; log = 'Microsoft-Windows-Kernel-PnP/Configuration'; provider = 'Microsoft-Windows-Kernel-PnP'; ids = 'EventID=400' }},
    [pscustomobject]@{{ name = 'msi'; log = 'Application'; provider = 'MsiInstaller'; ids = '(EventID=1033 or EventID=1034)' }}
)
function FailureOutcome($errorRecord) {{
    if ($errorRecord.CategoryInfo.Category -eq [System.Management.Automation.ErrorCategory]::PermissionDenied -or
        $errorRecord.Exception -is [System.UnauthorizedAccessException]) {{ return 'denied' }}
    return 'failed'
}}
function Project-Change($event, [string]$source) {{
    $data = [ordered]@{{}}
    $projectionError = $null
    $props = @($event.Properties)
    try {{
        if ($source -eq 'msi') {{
            # Microsoft documents positions 0..4 for 1033/1034. Other versions stay undecoded.
            if ($event.Version -eq 0) {{
                for ($i = 0; $i -lt [Math]::Min(5, $props.Count); $i++) {{
                    $value = $props[$i].Value
                    $data["[$i]"] = if ($null -eq $value) {{ $null }} else {{ [string]$value }}
                }}
            }}
        }} else {{
            [xml]$xml = $event.ToXml()
            $allowed = switch ($source) {{
                'windows_update' {{ @('updateTitle', 'updateGuid', 'updateRevisionNumber', 'serviceGuid', 'errorCode') }}
                'device_configuration' {{ @('DriverName', 'ClassGuid', 'DriverDate', 'DriverVersion', 'DriverProvider', 'DriverInbox', 'DriverRank', 'DeviceUpdated', 'Status') }}
                default {{ @() }}
            }}
            foreach ($node in @($xml.Event.EventData.Data)) {{
                if ($node -isnot [System.Xml.XmlElement]) {{ continue }}
                $name = $node.GetAttribute('Name')
                if ($name -in $allowed) {{ $data[$name] = $node.InnerText }}
            }}
        }}
    }} catch {{ $data = [ordered]@{{}}; $projectionError = 'the event data could not be projected' }}
    [pscustomobject]@{{
        Log = $event.LogName; RecordId = $event.RecordId; Id = $event.Id
        ProviderName = $event.ProviderName; Version = $event.Version; Level = $event.Level
        TimeCreated = $event.TimeCreated.ToUniversalTime().ToString('o')
        Data = [pscustomobject]$data; FieldCount = $props.Count
        OmittedFieldCount = [Math]::Max(0, $props.Count - $data.Count)
        ProjectionError = $projectionError
    }}
}}
$sources = foreach ($spec in $specs) {{
    $log = $spec.log; $provider = $spec.provider; $ids = $spec.ids
    $query = @"
<QueryList><Query Id='0' Path='$log'><Select Path='$log'>*[System[Provider[@Name='$provider'] and ($ids) and TimeCreated[@SystemTime&gt;='$startIso' and @SystemTime&lt;'$endIso']]]</Select></Query></QueryList>
"@
    $records = @(); $outcome = 'failed'; $errorText = $null; $truncated = $null
    try {{
        $found = @(Get-WinEvent -FilterXml ([xml]$query) -MaxEvents ($limit + 1) -ErrorAction Stop)
        $truncated = $found.Count -gt $limit
        $records = @($found | Select-Object -First $limit | ForEach-Object {{ Project-Change $_ $spec.name }})
        $outcome = if ($records.Count) {{ 'ok' }} else {{ 'empty' }}
    }} catch {{
        if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') {{ $outcome = 'empty'; $truncated = $false }}
        else {{ $outcome = FailureOutcome $_; $errorText = $_.Exception.Message }}
    }}
    $meta = Read-LogMetadata $log
    [pscustomobject]@{{
        name = $spec.name; log = $log; outcome = $outcome; error = $errorText
        returned = $records.Count; limit = $limit; truncated = $truncated; records = $records
        log_enabled = $meta.log_enabled; log_mode = $meta.log_mode; log_state = $meta.log_state; log_error = $meta.log_error
        log_oldest = $meta.log_oldest; oldest_state = $meta.oldest_state; oldest_error = $meta.oldest_error
    }}
}}
[pscustomobject]@{{ window_start = $startIso; window_end = $endIso; queried_at = $queriedAt; sources = @($sources) }}
"""

CHANGES_BASIS = (
    "One entry per returned event, ordered oldest first. Windows Update 19 records an installation "
    "success and 20 a failed attempt. Kernel-PnP 400 records device configuration, not necessarily "
    "a driver update; its DeviceUpdated field is shown without inferring a cause. MSI 1033/1034 "
    "record installation/removal results, with success derived only from a documented status code. "
    "MSI status 1641 means restart initiated and 3010 means restart required. "
    "A nearby event is a lead to inspect, not proof that it caused a later failure."
)
def changes_script(before: str, hours: int, count: int) -> str:
    if not 1 <= hours <= MAX_HOURS:
        raise ValueError(f"parameter 'hours': must be between 1 and {MAX_HOURS}")
    if not 1 <= count <= MAX_COUNT:
        raise ValueError(f"parameter 'count': must be between 1 and {MAX_COUNT}")
    try:
        stamp = _utc_stamp(before) if before.strip() else None
    except ValueError as exc:
        raise ValueError(f"parameter 'before': not an ISO timestamp ({exc})") from exc
    assignment = f"$until = [datetimeoffset]::Parse('{stamp}').UtcDateTime" if stamp else "$until = (Get-Date).ToUniversalTime()"
    return LOG_METADATA_SCRIPT + CHANGES_SCRIPT.replace("{before_assignment}", assignment).replace("{hours}", str(hours)).replace("{count}", str(count)).replace("{{", "{").replace("}}", "}")


def take_changes(bridge: Bridge, params: dict[str, Any]) -> Reading:
    script = changes_script(str(params["before"]), int(params["hours"]), int(params["count"]))
    result = bridge.run(script, depth=DEPTH)
    collection: dict[str, Any] = {}
    coverage: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []

    def build(payload: dict[str, Any]) -> list[Section]:
        collection.update({"window_start": payload.get("window_start"), "window_end": payload.get("window_end"), "queried_at": payload.get("queried_at")})
        sources = payload.get("sources")
        grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in SOURCES}
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict):
                    name = source.get("name")
                    if isinstance(name, str) and name in grouped:
                        grouped[name].append(source)
        for name in SOURCES:
            source, records = _source(name, grouped[name][0] if len(grouped[name]) == 1 else None, int(params["count"]), collection["window_start"], collection["window_end"])
            collection[name] = source
            in_window = [row for row in records if _in_window(row, collection["window_start"], collection["window_end"])]
            coverage[name] = window_coverage(source, in_window, collection["window_start"], collection["window_end"], collection["queried_at"],
                                             end_is_query_time=not bool(str(params["before"]).strip()))
            rows.extend(records)
        rows.sort(key=lambda r: (str(r.get("TimeCreated") or ""), str(r.get("Log") or ""), int(r.get("RecordId") or 0)))
        decoded = [_change(row) for row in rows]
        kinds = Counter(str(item.get("kind") or "unknown") for item in decoded)
        return [
            Section("records", "raw", rows),
            Section("changes", "derived", decoded, basis=CHANGES_BASIS),
            Section("summary", "derived", {"by_kind": dict(sorted(kinds.items())), "returned": len(rows)}, basis="Counts only the returned records; source limits are in collection and retention reach is in coverage."),
            Section("collection", "raw", collection),
            Section("coverage", "derived", coverage, basis=WINDOW_COVERAGE_BASIS),
        ]

    reading = from_object("changes", params, script, result, build)
    if not reading.observed:
        return reading
    if str(params["before"]).strip():
        query_time, requested_end = _stamp_key(collection["queried_at"]), _stamp_key(collection["window_end"])
        if query_time is None:
            reading.warnings.append("the machine's query time was not reported; the requested window's upper reach is unknown")
        elif requested_end is not None and requested_end > query_time:
            reading.warnings.append("the requested end is after the machine's query time; records logged after the query time are outside the covered reach")
    failures = [name for name in SOURCES if collection[name]["outcome"] not in ("ok", "empty")]
    for name in SOURCES:
        source = collection[name]
        if name in failures:
            reading.warnings.append(f"{name} did not answer: {source['error']}")
        if source["truncated"]:
            reading.warnings.append(f"{name} reached its {source['limit']}-record limit; older matching records in the window were not returned")
        if source.get("row_issues", {}).get("outside_window"):
            reading.warnings.append(f"{name} returned {source['row_issues']['outside_window']} record times outside the requested window; the raw rows remain visible and completeness cannot be established")
        if coverage[name]["complete"] is False:
            reading.warnings.append(
                f"{name} log coverage could not be established" if coverage[name]["covered_from"] is None
                else f"{name} does not cover the whole requested window"
            )
    if rows:
        reading.outcome = "ok"
        reading.count = len(rows)
    elif failures:
        reading.outcome = "denied" if all(collection[name]["outcome"] == "denied" for name in failures) else "failed"
        reading.count = None
        reading.error = {"kind": reading.outcome, "detail": "No change history could be established because " + " and ".join(failures) + " did not answer."}
    else:
        reading.outcome = "empty"
        reading.count = 0
    return reading


def _source(name: str, value: Any, limit: int, start: Any, end: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    problem = "the collector did not return a valid source result"
    if isinstance(value, dict):
        outcome, raw_rows = value.get("outcome"), value.get("records")
        rows: list[dict[str, Any]] = raw_rows if isinstance(raw_rows, list) else []
        if outcome in ("failed", "denied"):
            return {**_metadata(value), "outcome": outcome, "returned": 0, "limit": limit, "truncated": None, "error": value.get("error") or "the source did not answer"}, []
        if outcome in ("ok", "empty"):
            valid = (
                isinstance(raw_rows, list)
                and all(_valid_row(name, row) for row in rows)
                and value.get("log") == SOURCE_EXPECTED[name][0]
                and type(value.get("returned")) is int and value["returned"] == len(rows)
                and type(value.get("limit")) is int and value["limit"] == limit and type(value.get("truncated")) is bool
                and len(rows) <= limit and (outcome == "empty") == (len(rows) == 0)
                and (not value["truncated"] or len(rows) == limit)
                and _parse(start) is not None and _parse(end) is not None
            )
            if valid:
                metadata = _metadata(value)
                outside = sum(not _in_window(row, start, end) for row in rows)
                return {
                    **metadata, "outcome": outcome, "returned": len(rows), "limit": limit,
                    "truncated": value["truncated"], "error": None, "row_issues": {"outside_window": outside},
                }, rows
            problem = "the source result or record projection failed validation"
    return {**_metadata({}), "log": SOURCE_EXPECTED[name][0], "outcome": "failed", "returned": 0, "limit": limit, "truncated": None, "error": problem, "log_state": "failed", "oldest_state": "failed"}, []


def _in_window(row: dict[str, Any], start: Any, end: Any) -> bool:
    at, first, until = _stamp_key(row.get("TimeCreated")), _stamp_key(start), _stamp_key(end)
    return at is not None and first is not None and until is not None and first <= at < until


def _valid_row(name: str, row: Any) -> bool:
    if not isinstance(row, dict) or not set(row) <= RAW_KEYS or not isinstance(row.get("Data"), dict):
        return False
    log, provider, ids = SOURCE_EXPECTED[name]
    if _stamp_key(row.get("TimeCreated")) is None:
        return False
    data = row["Data"]
    return (
        row.get("Log") == log and row.get("ProviderName") == provider
        and type(row.get("Id")) is int and row["Id"] in ids
        and type(row.get("RecordId")) is int and row["RecordId"] > 0
        and (row.get("Version") is None or type(row["Version"]) is int)
        and (row.get("Level") is None or type(row["Level"]) is int)
        and type(row.get("FieldCount")) is int and row["FieldCount"] >= 0
        and type(row.get("OmittedFieldCount")) is int and 0 <= row["OmittedFieldCount"] <= row["FieldCount"]
        and row["FieldCount"] >= len(data) and row["OmittedFieldCount"] == row["FieldCount"] - len(data)
        and (row.get("ProjectionError") is None or isinstance(row["ProjectionError"], str))
        and set(data) <= SAFE_DATA_KEYS[name]
        and all(isinstance(key, str) and (value is None or isinstance(value, str)) for key, value in data.items())
    )


def _change(record: dict[str, Any]) -> dict[str, Any]:
    source = _source_name(record)
    raw_data = record.get("Data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    entry: dict[str, Any] = {
        "at": record.get("TimeCreated"), "source": source, "ref": {"log": record.get("Log"), "record_id": record.get("RecordId")},
        "kind": "unmapped_event", "subject": None, "version": None, "publisher": None, "fields": data,
    }
    version = record.get("Version")
    event_id = record.get("Id")
    if record.get("ProjectionError"):
        entry["error"] = str(record["ProjectionError"])
    elif source == "windows_update" and version == 1 and event_id in (19, 20) and isinstance(data.get("updateTitle"), str):
        title = data["updateTitle"]
        kb = _KB.search(title)
        entry.update(kind="update_installed" if event_id == 19 else "update_failed", subject=title, kb=kb.group(0).upper() if kb else None, error_code=data.get("errorCode") if event_id == 20 else None)
    elif source == "device_configuration" and version == 1 and event_id == 400 and all(key in data for key in ("DriverName", "DriverVersion", "DriverProvider", "DeviceUpdated")):
        entry.update(kind="device_configured", subject=data.get("DriverName"), version=data.get("DriverVersion"), publisher=data.get("DriverProvider"), device_updated=_bool(data.get("DeviceUpdated")), status=data.get("Status"))
    elif source == "msi" and version == 0 and event_id in (1033, 1034) and all(f"[{i}]" in data for i in range(5)):
        status = _integer(data.get("[3]"))
        success = status in _MSI_SUCCESS if status is not None else None
        action = "install" if event_id == 1033 else "removal"
        entry.update(
            kind=f"msi_{action}_{'succeeded' if success else 'failed' if success is False else 'unknown'}",
            subject=data.get("[0]"), version=data.get("[1]"), publisher=data.get("[4]"), status=status, succeeded=success,
            restart="initiated" if status == 1641 else "required" if status == 3010 else None,
        )
    else:
        entry["error"] = "the event's provider, version or data did not match the supported layout"
    return entry


def _source_name(record: dict[str, Any]) -> str:
    provider = str(record.get("ProviderName") or "")
    return {
        "Microsoft-Windows-WindowsUpdateClient": "windows_update",
        "Microsoft-Windows-Kernel-PnP": "device_configuration",
        "MsiInstaller": "msi",
    }.get(provider, "unknown")


def _bool(value: Any) -> bool | None:
    if str(value).lower() in ("true", "1"):
        return True
    if str(value).lower() in ("false", "0"):
        return False
    return None


def _integer(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


register(
    Spec(
        name="changes",
        description="Recent Windows Update results, device configuration and MSI installation/removal results before a moment, with per-log coverage and safe raw event fields. These events are leads, not causes.",
        classes=("raw", "derived"),
        take=take_changes,
        params=(
            Param("before", "str", "", "ISO timestamp; events strictly before it, or now when empty."),
            Param("hours", "int", 168, "Hours before the moment, 1 to 2160.", minimum=1, maximum=MAX_HOURS),
            Param("count", "int", 100, "Most recent records per source, 1 to 500; collection reports truncation.", minimum=1, maximum=MAX_COUNT),
        ),
        private=("user names or profile paths within update titles and MSI product names",),
        heavy=True,
    )
)
