"""Hardware errors: ``whea`` (the records from both logs Windows keeps them in, each with its CPER
header read locally and, for WHEA-Logger, its payload decoded beside it) and ``storms`` (the
System log's WHEA-Logger records over a window in wall-clock buckets, by signature, with burst
and acceleration flags).

Built in phase 2 against docs/API.md from the queries and rules in the old backend
(``backend/services/system_logs.py:get_whea_events``, ``backend/services/cper_decoder.py``,
``backend/services/whea/{parse,aggregate,storms}.py``). The decoder lives at
``sentinel/tools/DecodeWheaRecord/DecodeWheaRecord.exe``.

Two things the old pipeline needed are gone because their cause is gone. There is no
store, no ingest cycle and no watermark: the event log already retains the records, so
``storms`` queries the window and counts it, and the watermark that never advanced has
nothing left to get wrong. And the buckets are wall-clock buckets over the whole window,
idle minutes included, so a rate is events per minute of elapsed time rather than events
per minute that happened to have an event, which is what inflated the old rates.

The decoder is a subprocess, not a bridge call: it is a local executable over a hex string,
and it runs in the same worker thread as the reading. A record that the decoder cannot
read is an error on that record, never a failure of the reading: the records were observed
either way, and observing them is the evidence.

Windows keeps hardware errors in two places, and ``whea`` reads both as independent sources:
WHEA-Logger's records in the System log, which carry a human-readable message, and the
Kernel-WHEA provider's event 20 in its own ``Errors`` channel, which carries only the CPER
record. The channel's retention is its own: on the machine this was built on it still held
fatal records the System log had long rotated out, while ``whea`` answered ``empty``. Neither
source stands in for the other, so a source that did not answer is never read as an empty one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ..bridge import WSL_INTEROP_ERRORS, Bridge
from ..reading import Param, Reading, Section, Spec, from_object, register
from .event_coverage import COVERAGE_BASIS, LOG_METADATA_SCRIPT, known_stamp_key, stamp_key
from .event_coverage import coverage as log_coverage
from .event_coverage import metadata as log_metadata
from .events import RECORD_FIELDS
from .health import DECODER

PROVIDER = "Microsoft-Windows-WHEA-Logger"
MAX_WHEA_RECORDS = 500
MAX_EXACT_BINARY_BYTES = 1024 * 1024
MAX_RECORD_ID = (1 << 53) - 1  # exact across JSON number clients
LOG = "System"
CHANNEL = "Microsoft-Windows-Kernel-WHEA/Errors"
CHANNEL_PROVIDER = "Microsoft-Windows-Kernel-WHEA"
WHEA_DEPTH = 8  # the payload's sources hold records, which hold their Properties

# The decoder takes the record on the command line, which Windows caps near 32,000 characters.
MAX_HEX = 30_000
DECODE_TIMEOUT = 15.0
DECODE_BUDGET = 30.0
DECODE_WORKERS = 4
_INTEROP_ATTEMPTS = 3

DECODED_BASIS = "CPER header and section bounds checked locally, then DecodeWheaRecord.exe over each structurally bounded RawData"

# A storm is bounded by the window, but the window is the caller's: cap what one reading pulls
# out of the log and say so when the cap bites, rather than serializing an unbounded log.
RECORD_CAP = 20_000
MAX_BUCKETS = 20_000
MAX_HOURS = 43_800  # Five years bounds host DateTime arithmetic even with wide buckets.
RECENT_BUCKETS = 10
BASELINE_BUCKETS = 240
BASELINE_FLOOR = 0.1  # a baseline of zero would make every recent event infinitely accelerated
RECENT_FLOOR = 0.5  # below this the ratio is noise, not a trend
SIGNATURE_VERSION = "v1"
SIGNATURE_ID_CHARS = 16


# ---------------------------------------------------------------- whea


@dataclass(frozen=True)
class WheaSource:
    """One place Windows keeps hardware error records, and whether this tool decodes them yet."""

    name: str
    log: str
    provider: str
    event_ids: tuple[int, ...] | None  # None: every event the provider writes to this log
    decode: bool

    def select(self, record_id: int | None = None) -> str:
        ids = f" and ({' or '.join(f'EventID={i}' for i in self.event_ids)})" if self.event_ids else ""
        exact = f" and EventRecordID={record_id}" if record_id is not None else ""
        return f"*[System[Provider[@Name='{self.provider}']{ids}{exact}]]"


WHEA_SOURCES: tuple[WheaSource, ...] = (
    WheaSource("system", LOG, PROVIDER, None, decode=True),
    # TODO(F4): decode the channel's records once DecodeWheaRecord.exe has been exercised on
    # structurally valid AMD payloads in isolation; until then its crash would land in this
    # machine's own reliability record, so the header is read locally and the raw bytes kept.
    WheaSource("kernel_whea", CHANNEL, CHANNEL_PROVIDER, (20,), decode=False),
)

DEFERRED = (
    f"detail decoding is deferred for {CHANNEL} records until the decoder has been exercised on them "
    "in isolation; the CPER header identity and the raw payload are in this reading"
)

# The CPER payload: the record's first binary property, as hex.
RAW_DATA = "RawData = $( $b = $_.Properties | Where-Object { $_.Value -is [byte[]] } | Select-Object -First 1; if ($b) { [System.BitConverter]::ToString($b.Value).Replace('-','') } else { $null } )"

# One source, asked for one record more than the limit so a reached cap is exact. The events are
# kept as they arrive, so a query that stops part way keeps the newest records it did return; a
# clean no-match is empty, and every other error is a failure, never an empty log. Log metadata is
# read afterwards and only qualifies the answer: its own failure is reported beside the records.
WHEA_SOURCE_SCRIPT = r"""
function Read-WheaSource([string]$name, [string]$log, [string]$select, [int]$limit, [int]$maxBinaryBytes = 0) {
    $xml = "<QueryList><Query Id='0' Path='$log'><Select Path='$log'>$select</Select></Query></QueryList>"
    $found = [System.Collections.Generic.List[object]]::new()
    $records = @(); $outcome = 'failed'; $errorText = $null; $truncated = $null; $stopped = $null
    try {
        Get-WinEvent -FilterXml ([xml]$xml) -MaxEvents ($limit + 1) -ErrorAction Stop | & { process { [void]$found.Add($_) } }
        $truncated = $found.Count -gt $limit
    } catch {
        $kind = if ($_.CategoryInfo.Category -eq [System.Management.Automation.ErrorCategory]::PermissionDenied -or $_.Exception -is [System.UnauthorizedAccessException]) { 'denied' } else { 'failed' }
        if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*' -and $found.Count -eq 0) { $truncated = $false }
        elseif ($found.Count) {
            $detail = ([string]$_.Exception.Message -split '\r?\n')[0].Trim()
            if (-not $detail) { $detail = "the $log query stopped early" }
            if ($detail.Length -gt 300) {
                $detail = $detail.Substring(0, 300)
                if ([char]::IsHighSurrogate($detail[299])) { $detail = $detail.Substring(0, 299) }
            }
            $stopped = [pscustomobject]@{ kind = $kind; detail = $detail }
        } else { $outcome = $kind; $errorText = $_.Exception.Message }
    }
    if ($null -eq $errorText) {
        try {
            $records = @($found | Select-Object -First $limit | ForEach-Object {
                if ($maxBinaryBytes -gt 0) {
                    $binaryBytes = [long]0
                    foreach ($property in $_.Properties) {
                        if ($property.Value -is [byte[]]) { $binaryBytes += $property.Value.Length }
                        if ($binaryBytes -gt $maxBinaryBytes) { throw "the $log record's binary properties exceed the $maxBinaryBytes-byte exact-read limit" }
                    }
                }
                [pscustomobject]@{ {fields} }
            })
            if ($records.Count -ne [Math]::Min($found.Count, $limit)) { throw "the $log record projection returned fewer records than the query" }
            $outcome = if ($records.Count) { 'ok' } else { 'empty' }
        } catch {
            $records = @(); $outcome = 'failed'; $errorText = $_.Exception.Message; $truncated = $null; $stopped = $null
        }
    }
    $meta = Read-LogMetadata $log
    [pscustomobject]@{
        name = $name; log = $log; outcome = $outcome; error = $errorText
        returned = $records.Count; limit = $limit; truncated = $truncated; stopped = $stopped; records = $records
        log_enabled = $meta.log_enabled; log_mode = $meta.log_mode; log_state = $meta.log_state; log_error = $meta.log_error
        log_oldest = $meta.log_oldest; oldest_state = $meta.oldest_state; oldest_error = $meta.oldest_error
    }
}
"""

# The shared record shape, plus the log the record came from (a RecordId is only unique within its
# log) and the CPER payload.
WHEA_FIELDS = RECORD_FIELDS + ";\n        Log = $_.LogName;\n        " + RAW_DATA
WHEA_ROW_KEYS = {
    "RecordId", "Id", "Level", "LevelDisplayName", "ProviderName", "ProviderId", "Version", "MachineName",
    "TaskDisplayName", "TimeCreated", "Message", "Properties", "Log", "RawData",
}


def whea_script(count: int) -> str:
    """Both sources, each through the same collector, each asked for ``count + 1`` records.

    Each source is one ``-FilterXml`` query on the log's own index: the same question put through
    ``-FilterHashtable @{LogName; ProviderName}`` answered thirty times slower on this machine when
    nothing matched, which is exactly the case the tool has to be quick about.
    """
    calls = ", ".join(f"(Read-WheaSource '{s.name}' '{s.log}' \"{s.select()}\" {int(count)})" for s in WHEA_SOURCES)
    return LOG_METADATA_SCRIPT + WHEA_SOURCE_SCRIPT.replace("{fields}", WHEA_FIELDS) + f"[pscustomobject]@{{ sources = @({calls}) }}\n"


def whea_record_script(spec: WheaSource, record_id: int) -> str:
    """One log-local reference; probe for a second match rather than accepting ambiguity."""
    select = spec.select(record_id)
    return (LOG_METADATA_SCRIPT + WHEA_SOURCE_SCRIPT.replace("{fields}", WHEA_FIELDS)
            + f"[pscustomobject]@{{ source = Read-WheaSource '{spec.name}' '{spec.log}' \"{select}\" 1 {MAX_EXACT_BINARY_BYTES} }}\n")


COLLECTION_BASIS = "each source's own answer: outcome, the records it returned against its limit of count + 1 asked, whether it was truncated or stopped part way, and its log's metadata"
EXACT_COLLECTION_BASIS = "one selected log and EventRecordID, with its provider and event-ID filter; the collector asks for two matches to detect ambiguity, enforces a binary-byte bound before projection, and reports that log's outcome and retention metadata"
WHEA_COVERAGE_BASIS = (
    "records holds the newest `limit` records across both logs, newest first; ties are ordered by source "
    "(System first) and then by descending RecordId. complete means every record both logs still retain "
    "matching the query is in records; it is false when a source did not answer, and never a claim "
    "about the machine's lifetime. When a source was truncated, stopped part way or cut by the merge, "
    "cutoff is the time after which every answered source is fully shown: records at exactly that time "
    "may be missing. retained_from is the oldest record the log still holds, read after the query, so "
    "a log that wrapped meanwhile can reach less far; it is null when it could not be read or the log "
    "holds nothing, and absence before it says nothing about what happened then."
)
IDENTITY_BASIS = (
    "Read locally from each payload's 128-byte CPER header after the structural check: the record id "
    "(which Windows documents as unique only on the machine that created it), severity, section count, "
    "notification type, flags, and the eight header time bytes when their valid bit is set. The "
    "header_time object keeps integer and BCD calendar interpretations separately; its reading "
    "names one only when exactly one interpretation forms a calendar date, and both means they agree. "
    "That choice does not establish the creator's encoding or a time zone. The precise bit is the "
    "record's claim that the time correlates to the error event. previous_session is the header's "
    "PreviousError flag: the error occurred in an earlier session and was reported after a restart, "
    "so the Windows event's TimeCreated is that report, not "
    "the moment of the error. PlatformId, PartitionId and CreatorId are not reported here; the full "
    "payload stays in records."
)
GROUPS_BASIS = (
    "A pair of returned records, one from each log, whose CPER headers agree on a nonzero record id, "
    "all eight raw timestamp bytes (even when the timestamp is not valid as a time), severity, section count "
    "and notification type is grouped as likely the same error reported twice. "
    "Both rows stay in records; a group is a derived match on the headers, not a statement of cause. "
    "likely_errors counts each group once and every other returned record once. Records that share a "
    "record id but disagree on the rest are listed under conflicting and not grouped."
)
DEFERRED_BASIS = DECODED_BASIS + f"; {CHANNEL} records are not yet decoded (see each entry's error)"

_SEVERITY = {0: "recoverable", 1: "fatal", 2: "corrected", 3: "informational"}


def take_whea(bridge: Bridge, params: dict[str, Any]) -> Reading:
    started = time.perf_counter()
    count = params["count"]
    script = whea_script(count)
    result = bridge.run(script, depth=WHEA_DEPTH)
    sources: dict[str, dict[str, Any]] = {}
    kept: list[dict[str, Any]] = []

    def build(payload: dict[str, Any]) -> list[Section]:
        answered = payload.get("sources")
        by_name: dict[str, list[Any]] = {s.name: [] for s in WHEA_SOURCES}
        for value in answered if isinstance(answered, list) else []:
            if isinstance(value, dict) and value.get("name") in by_name:
                by_name[value["name"]].append(value)
        rows: list[dict[str, Any]] = []
        for spec in WHEA_SOURCES:
            source, returned = whea_source(spec, by_name[spec.name][0] if len(by_name[spec.name]) == 1 else None, count)
            sources[spec.name] = source
            rows.extend(returned)
        kept.extend(merge_newest(rows, count))
        identities = [record_identity(record) for record in kept]
        return [
            Section("records", "raw", kept),
            Section("collection", "raw", {"limit": count, "returned": len(kept), "truncated": len(rows) > count or any(s["truncated"] is True for s in sources.values()), "sources": sources}, basis=COLLECTION_BASIS),
            Section("coverage", "derived", whea_coverage(sources, rows, kept), basis=WHEA_COVERAGE_BASIS),
            Section("identity", "derived", identities, basis=IDENTITY_BASIS),
            Section("groups", "derived", group_identities(identities, kept), basis=GROUPS_BASIS),
        ]

    reading = from_object("whea", params, script, result, build)
    if not reading.observed:
        return reading
    failures = [spec for spec in WHEA_SOURCES if sources[spec.name]["outcome"] not in ("ok", "empty")]
    for spec in WHEA_SOURCES:
        source = sources[spec.name]
        if spec in failures:
            reading.warnings.append(f"{spec.log} did not answer: {source['error']}; its records are missing, so this is not a complete list")
        if isinstance(source.get("stopped"), dict):
            reading.warnings.append(f"{spec.log} stopped after {source['returned']} returned records: {source['stopped']['detail']}; its older records were not returned")
        if source.get("log_enabled") is False:
            reading.warnings.append(f"{spec.log} is disabled: Windows is not recording new events there, so its absence of records is not evidence")
        if spec not in failures and (source.get("log_state") != "ok" or source.get("oldest_state") not in ("ok", "empty")):
            reading.warnings.append(f"how far back {spec.log} reaches could not be read; its returned records stand, its retention is unknown")
    if kept:
        reading.outcome, reading.count = "ok", len(kept)
    elif failures:
        reading.outcome = "denied" if all(sources[s.name]["outcome"] == "denied" for s in failures) else "failed"
        reading.count = None
        reading.error = {"kind": reading.outcome, "detail": "No hardware error record was returned and " + " and ".join(s.log for s in failures) + " did not answer, so an empty list would not be an observation."}
        reading.sections = [section for section in reading.sections if section.name in ("collection", "coverage")]
        reading.took_ms = _ms(started)
        return reading
    else:
        reading.outcome, reading.count = "empty", 0
    decoded, warnings = decode_all(kept)
    reading.sections.append(Section("decoded", "derived", decoded, basis=DEFERRED_BASIS))
    reading.warnings.extend(warnings)
    reading.took_ms = _ms(started)
    return reading


def take_whea_record(bridge: Bridge, params: dict[str, Any]) -> Reading:
    """Read one exact log row even when the newest-record reading no longer reaches it."""
    started = time.perf_counter()
    spec = next(source for source in WHEA_SOURCES if source.name == params["source"])
    record_id = params["record_id"]
    script = whea_record_script(spec, record_id)
    result = bridge.run(script, depth=WHEA_DEPTH)
    collection: dict[str, Any] = {}
    records: list[dict[str, Any]] = []

    def build(payload: dict[str, Any]) -> list[Section]:
        source, rows = whea_source(spec, payload.get("source"), 1)
        if source["outcome"] == "ok" and source["truncated"]:
            source = {**source, "outcome": "failed", "error": "more than one matching event was returned for this log-local RecordId", "returned": 0}
            rows = []
        elif source["outcome"] == "ok" and source["stopped"] is not None:
            stopped = source["stopped"]
            source = {**source, "outcome": stopped["kind"], "error": f"the exact query stopped before uniqueness could be established: {stopped['detail']}", "returned": 0}
            rows = []
        elif source["outcome"] == "ok" and rows[0]["RecordId"] != record_id:
            source = {**source, "outcome": "failed", "error": "the exact query returned a different RecordId", "returned": 0}
            rows = []
        collection.update(source)
        records.extend(rows)
        sections = [
            Section("collection", "raw", {"record_id": record_id, "source": source}, basis=EXACT_COLLECTION_BASIS),
        ]
        if source["outcome"] in ("failed", "denied"):
            return sections
        return sections + [
            Section("records", "raw", rows),
            Section("identity", "derived", [record_identity(row) for row in rows], basis=IDENTITY_BASIS),
        ]

    reading = from_object("whea_record", params, script, result, build)
    if not reading.observed:
        return reading
    if collection["outcome"] in ("failed", "denied"):
        reading.outcome = collection["outcome"]
        reading.count = None
        reading.error = {"kind": reading.outcome, "detail": collection["error"]}
    elif records:
        reading.outcome, reading.count = "ok", 1
        decoded, warnings = decode_all(records)
        reading.sections.append(Section("decoded", "derived", decoded, basis=DEFERRED_BASIS))
        reading.warnings.extend(warnings)
    else:
        reading.outcome, reading.count = "empty", 0
        reading.warnings.append("no retained event in this log matches the requested RecordId, provider and event ID; this does not mean it never existed")
    if collection.get("log_enabled") is False:
        reading.warnings.append(f"{spec.log} is disabled: Windows is not recording new events there")
    if collection.get("log_state") != "ok" or collection.get("oldest_state") not in ("ok", "empty"):
        reading.warnings.append(f"how far back {spec.log} reaches could not be read; its query outcome still stands")
    reading.took_ms = _ms(started)
    return reading


def whea_source(spec: WheaSource, value: Any, limit: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One source's answer, validated whole before any of its records is counted."""
    base = {"log": spec.log, "provider": spec.provider, "event_ids": list(spec.event_ids) if spec.event_ids else None, "limit": limit}
    failed = {**log_metadata({}), **base, "outcome": "failed", "error": "the collector did not return a valid source result", "returned": 0, "truncated": None, "stopped": None}
    if not isinstance(value, dict) or value.get("log") != spec.log:
        return failed, []
    metadata = {**log_metadata(value), **base}
    outcome = value.get("outcome")
    if outcome in ("failed", "denied"):
        return {**metadata, "outcome": outcome, "error": value.get("error") or "the source did not answer", "returned": 0, "truncated": None, "stopped": None}, []
    rows = value.get("records")
    stopped = value.get("stopped")
    valid_stop = stopped is None or (
        isinstance(stopped, dict) and set(stopped) == {"kind", "detail"} and stopped["kind"] in ("failed", "denied")
        and isinstance(stopped["detail"], str) and 0 < len(stopped["detail"]) <= 300
    )
    valid = (
        outcome in ("ok", "empty") and isinstance(rows, list) and valid_stop and "truncated" in value
        and type(value.get("returned")) is int and value["returned"] == len(rows)
        and type(value.get("limit")) is int and value["limit"] == limit and len(rows) <= limit
        and (value.get("truncated") is None if stopped is not None else type(value.get("truncated")) is bool)
        and (value.get("truncated") is not True or len(rows) == limit)
        and (outcome == "empty") == (len(rows) == 0)
        and (stopped is None or outcome == "ok")
        and value.get("error") is None
        and all(valid_whea_row(spec, row) for row in rows)
    )
    if not valid:
        return {**failed, **metadata, "outcome": "failed", "error": "the source result or its records failed validation", "returned": 0, "truncated": None, "stopped": None}, []
    return {**metadata, "outcome": outcome, "error": None, "returned": len(rows), "truncated": value["truncated"], "stopped": stopped}, rows


def valid_whea_row(spec: WheaSource, row: Any) -> bool:
    if not isinstance(row, dict) or not set(row) <= WHEA_ROW_KEYS:
        return False
    return (
        row.get("Log") == spec.log and row.get("ProviderName") == spec.provider
        and type(row.get("RecordId")) is int and row["RecordId"] > 0
        and type(row.get("Id")) is int and (spec.event_ids is None or row["Id"] in spec.event_ids)
        and stamp_key(row.get("TimeCreated")) is not None
        and (row.get("RawData") is None or isinstance(row["RawData"], str))
        and (row.get("Message") is None or isinstance(row["Message"], str))
        and (row.get("Level") is None or type(row["Level"]) is int)
    )


def merge_newest(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """The newest ``limit`` across both logs. Each source returned its own newest ``limit``, so the
    merged prefix is exact; ties fall to source order and then to the newer RecordId in its log."""
    rank = {s.log: i for i, s in enumerate(WHEA_SOURCES)}
    ordered = sorted(rows, key=lambda r: (known_stamp_key(r["TimeCreated"]), -rank[r["Log"]], r["RecordId"]), reverse=True)
    return ordered[:limit]


def whea_coverage(sources: dict[str, dict[str, Any]], rows: list[dict[str, Any]], kept: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether the list is every retained match, and if not, from when it is."""
    shown = {(r["Log"], r["RecordId"]) for r in kept}
    oldest_kept = min((r["TimeCreated"] for r in kept), key=known_stamp_key) if kept else None
    per_source: dict[str, dict[str, Any]] = {}
    boundaries: list[str] = []
    for spec in WHEA_SOURCES:
        source = sources[spec.name]
        answered = source["outcome"] in ("ok", "empty")
        own = [r for r in rows if r["Log"] == spec.log]
        cut = any((r["Log"], r["RecordId"]) not in shown for r in own)
        bounded = source["truncated"] is True or source["stopped"] is not None
        if answered and bounded and own:
            boundaries.append(min((r["TimeCreated"] for r in own), key=known_stamp_key))
        if answered and cut and oldest_kept is not None:
            boundaries.append(oldest_kept)
        per_source[spec.name] = {
            "log": spec.log,
            "answered": answered,
            "complete": (not bounded and not cut) if answered else None,
            "shown": sum(1 for r in kept if r["Log"] == spec.log),
            "retained_from": source.get("log_oldest") if source.get("oldest_state") == "ok" and stamp_key(source.get("log_oldest")) is not None else None,
            "retention": source.get("oldest_state"),
            "enabled": source.get("log_enabled"),
        }
    return {
        "complete": all(s["complete"] is True for s in per_source.values()),
        "cutoff": max(boundaries, key=known_stamp_key) if boundaries else None,
        "sources": per_source,
    }


def cper_header(payload: Any) -> tuple[dict[str, Any] | None, str | None]:
    """The header facts a person or an agent can use to tell errors apart, or why there are none."""
    text = str(payload or "").strip()
    if not text:
        return None, "the record carries no binary payload"
    checked, reason = checked_cper(text)
    if reason:
        return None, reason
    data = bytes.fromhex(checked[:256])
    severity = int.from_bytes(data[12:16], "little")
    valid = int.from_bytes(data[16:20], "little")
    flags = int.from_bytes(data[104:108], "little")
    header_time = _cper_time(data[24:32]) if valid & 0x2 else None
    return {
        "record_id": f"0x{int.from_bytes(data[96:104], 'little'):016x}",
        "severity": _SEVERITY.get(severity, f"unknown ({severity})"),
        "section_count": int.from_bytes(data[10:12], "little"),
        "notify_type": str(uuid.UUID(bytes_le=data[80:96])),
        "header_time": header_time,
        "flags": f"0x{flags:08x}",
        "recovered": bool(flags & 0x1),
        "previous_session": bool(flags & 0x2),
        "simulated": bool(flags & 0x4),
    }, None


def _cper_time(raw: bytes) -> dict[str, Any]:
    """Keep both CPER calendar interpretations; the header does not identify its encoding."""
    def calendar(fields: list[int | None]) -> str | None:
        if any(value is None for value in fields):
            return None
        seconds, minutes, hours, day, month, year, century = [int(value) for value in fields if value is not None]
        if year > 99 or century > 99:
            return None
        try:
            return datetime(century * 100 + year, month, day, hours, minutes, seconds).isoformat()
        except ValueError:
            return None

    def bcd(byte: int) -> int | None:
        high, low = byte >> 4, byte & 0xF
        return high * 10 + low if high <= 9 and low <= 9 else None

    fields = [raw[index] for index in (0, 1, 2, 4, 5, 6, 7)]
    integers = calendar(fields)
    bcd_time = calendar([bcd(value) for value in fields])
    if integers and bcd_time:
        reading = "both" if integers == bcd_time else None
    elif integers:
        reading = "as_integers"
    elif bcd_time:
        reading = "as_bcd"
    else:
        reading = None
    return {
        "bytes": raw.hex().upper(),
        "precise": bool(raw[3] & 0x1),
        "reserved_bits": bool(raw[3] & 0xFE),
        "as_integers": integers,
        "as_bcd": bcd_time,
        "reading": reading,
    }


def record_identity(record: dict[str, Any]) -> dict[str, Any]:
    header, reason = cper_header(record.get("RawData"))
    entry: dict[str, Any] = {"Log": record.get("Log"), "RecordId": record.get("RecordId")}
    if header is None:
        return {**entry, "cper": None, "error": reason}
    return {**entry, "cper": header, "error": None}


def group_identities(identities: list[dict[str, Any]], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Likely repeats of one error by their headers, with every returned row kept as it came."""
    by_key: dict[tuple[Any, ...], list[tuple[str, str]]] = {}
    by_id: dict[str, set[tuple[Any, ...]]] = {}
    members: dict[str, list[str]] = {}
    for entry, record in zip(identities, records, strict=True):
        header = entry.get("cper")
        if not header:
            continue
        ref = f"{entry['Log']}:{entry['RecordId']}"
        # Invalid timestamp fields have no time meaning, but their bytes still distinguish two
        # records. A zero CPER id has no useful identity; never collapse such records by accident.
        raw_time = str(record.get("RawData") or "")[48:64].upper()
        key = (header["record_id"], raw_time, header["severity"], header["section_count"], header["notify_type"])
        by_key.setdefault(key, []).append((str(entry["Log"]), ref))
        by_id.setdefault(header["record_id"], set()).add(key)
        members.setdefault(header["record_id"], []).append(ref)
    groups = [[ref for _, ref in refs] for key, refs in by_key.items() if key[0] != "0x0000000000000000" and len(refs) == 2 and refs[0][0] != refs[1][0]]
    return {
        "returned": len(identities),
        "without_identity": sum(1 for entry in identities if not entry.get("cper")),
        "likely_same_error": groups,
        "likely_errors": len(identities) - sum(len(refs) - 1 for refs in groups),
        "conflicting": [members[record_id] for record_id, keys in by_id.items() if len(keys) > 1],
    }


def decode_all(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """One entry per record, in the records' order, named by its log and RecordId: the decoder's
    structure, or why there is none. Only the sources the decoder has been exercised on reach it."""
    if not records:
        return [], []
    decodes = {s.log for s in WHEA_SOURCES if s.decode}
    eligible = [r for r in records if r.get("Log") in decodes]
    answers: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    if eligible and not os.path.exists(DECODER):
        answers = {str(r.get("RawData") or ""): {"error": "the decoder is not present"} for r in eligible}
        warnings.append("the CPER decoder is not present: the records were read but not decoded")
    elif eligible:
        deadline = time.monotonic() + DECODE_BUDGET
        # The decoder is a pure function of RawData. Repeated records often carry identical binary
        # data, especially fixtures: one process per distinct payload is enough, then put each
        # answer back under its own record. This also bounds a bad payload to one attempted launch
        # per reading rather than one launch per occurrence.
        unique: dict[str, dict[str, Any]] = {}
        for record in eligible:
            unique.setdefault(str(record.get("RawData") or ""), record)
        with ThreadPoolExecutor(max_workers=DECODE_WORKERS) as pool:
            answers = dict(zip(unique, pool.map(lambda r: decode_record(r, deadline), unique.values()), strict=True))
    out = []
    for record in records:
        ref = {"Log": record.get("Log"), "RecordId": record.get("RecordId")}
        if record.get("Log") not in decodes:
            out.append({**ref, "error": DEFERRED})
        else:
            answer = {k: v for k, v in answers[str(record.get("RawData") or "")].items() if k != "RecordId"}
            out.append({**ref, **answer})
    return out, warnings


def decode_record(record: dict[str, Any], deadline: float) -> dict[str, Any]:
    record_id = record.get("RecordId")
    payload = str(record.get("RawData") or "").strip()
    if not payload:
        return {"RecordId": record_id, "error": "the record carries no binary payload"}
    if len(payload) > MAX_HEX:
        return {"RecordId": record_id, "error": f"payload of {len(payload)} hex characters is longer than the decoder takes on the command line"}
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {"RecordId": record_id, "error": "the reading's decoding budget was spent before this record"}

    checked, reason = checked_cper(payload)
    if reason:
        return {"RecordId": record_id, "error": reason}

    stdout, stderr, code, error = _run_decoder(checked, min(DECODE_TIMEOUT, remaining))
    if error:
        return {"RecordId": record_id, "error": error}
    if code != 0 or not stdout:
        return {"RecordId": record_id, "error": stderr or f"the decoder exited with code {code}"}
    try:
        return {"RecordId": record_id, "decoded": json.loads(stdout)}
    except json.JSONDecodeError:
        return {"RecordId": record_id, "error": f"the decoder's output was not JSON: {stdout.splitlines()[0][:200]}"}


def checked_cper(payload: str) -> tuple[str, str | None]:
    """Reject malformed CPER structure before the external decoder can crash on its input.

    The raw record stays in its section. We only check the fixed header and descriptor bounds,
    not the meaning of a section's data, so a passing check is permission to try the decoder,
    never a claim that the whole record is valid. Layout: Microsoft's WHEA_ERROR_RECORD header
    and SectionDescriptor structures (128 and 72 bytes respectively).
    """
    if len(payload) % 2 or not re.fullmatch(r"[0-9A-Fa-f]+", payload):
        return "", "the binary payload is not an even-length hexadecimal string"
    data = bytes.fromhex(payload)
    if len(data) < 128:
        return "", "the CPER header is shorter than 128 bytes"
    if data[:4] != b"CPER" or data[6:10] != b"\xff\xff\xff\xff":
        return "", "the CPER header signatures do not match"
    sections = int.from_bytes(data[10:12], "little")
    if sections == 0:
        return "", "the CPER header lists no sections"
    descriptors_end = 128 + 72 * sections
    declared = int.from_bytes(data[20:24], "little")
    if descriptors_end > declared or declared > len(data):
        return "", "the CPER length or section directory is outside the binary payload"
    for index in range(sections):
        base = 128 + 72 * index
        offset = int.from_bytes(data[base : base + 4], "little")
        length = int.from_bytes(data[base + 4 : base + 8], "little")
        if offset < descriptors_end or offset + length > declared:
            return "", f"CPER section {index + 1} points outside the declared record"
    return payload[: declared * 2].upper(), None


def _run_decoder(payload: str, timeout: float) -> tuple[str, str, int | None, str | None]:
    """Run the executable once, retrying only WSL's interop failure, as the bridge does.

    Returns stdout, stderr, the exit code, and a reason when the process itself did not run.
    """
    for attempt in range(_INTEROP_ATTEMPTS):
        try:
            proc = subprocess.run([DECODER, payload], capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return "", "", None, "the decoder did not answer in time"
        except OSError as exc:
            return "", "", None, f"the decoder did not start: {exc}"
        stdout = proc.stdout.decode("utf-8", errors="replace").strip()
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        if not stdout and any(marker in stderr for marker in WSL_INTEROP_ERRORS):
            if attempt + 1 < _INTEROP_ATTEMPTS:
                time.sleep(0.5 * (attempt + 1))
                continue
            return "", stderr, proc.returncode, "WSL could not start the decoder"
        return stdout, stderr, proc.returncode, None
    return "", "", None, "WSL could not start the decoder"


# ---------------------------------------------------------------- storms

STORM_COVERAGE_BASIS = (
    COVERAGE_BASIS + " Complete uses actual bucket bounds; covered_until is the observed exclusive end. "
    "Future requested ends are incomplete."
)


@dataclass(frozen=True)
class Window:
    """The wall-clock window the buckets cover, aligned to bucket boundaries."""

    start: int
    bucket_seconds: int
    count: int

    @property
    def end(self) -> int:
        return self.start + self.count * self.bucket_seconds

    def index(self, epoch: float) -> int | None:
        idx = int((epoch - self.start) // self.bucket_seconds)
        return idx if 0 <= idx < self.count else None


def window_for(hours: int, bucket_seconds: int, now: float | None = None) -> Window:
    if hours is None or hours <= 0:
        raise ValueError("parameter 'hours': must be at least one hour")
    if hours > MAX_HOURS:
        raise ValueError(f"parameter 'hours': must be at most {MAX_HOURS} hours")
    if bucket_seconds is None or bucket_seconds <= 0:
        raise ValueError("parameter 'bucket_seconds': must be at least one second")
    count = int(hours * 3600 // bucket_seconds)
    if count < 1:
        raise ValueError("parameter 'bucket_seconds': longer than the window it would divide")
    if count > MAX_BUCKETS:
        raise ValueError(f"parameter 'bucket_seconds': the window would take {count} buckets; ask for longer buckets or a shorter window")
    last = int((now if now is not None else time.time()) // bucket_seconds) * bucket_seconds
    return Window(start=last - (count - 1) * bucket_seconds, bucket_seconds=bucket_seconds, count=count)


def before_stamp(before: str) -> str:
    """A millisecond UTC end for a historical WHEA window, never an offset-free guess."""
    try:
        parsed = datetime.fromisoformat(before.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("a time zone is required")
        utc = parsed.astimezone(UTC)
        if utc <= datetime(1970, 1, 1, tzinfo=UTC):
            raise ValueError("must be after the Unix epoch")
        return utc.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"parameter 'before': not an ISO timestamp with Z or an offset ({exc})") from exc


def valid_fixed_header_projection(raw: Any, payload_bytes: Any) -> bool:
    """The bounded first-binary-property shape both WHEA window collectors promise."""
    return ((raw is None and payload_bytes is None)
            or (isinstance(raw, str) and type(payload_bytes) is int and payload_bytes >= 0
                and len(raw) == min(payload_bytes, 128) * 2 and re.fullmatch(r"[0-9A-Fa-f]*", raw) is not None))


def fixed_cper_header(raw: Any, payload_bytes: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Read only CPER's fixed 128-byte header; do not certify the section payload."""
    if not isinstance(raw, str) or len(raw) != 256 or type(payload_bytes) is not int or payload_bytes < 128:
        return None, "the fixed CPER header was not available"
    try:
        data = bytes.fromhex(raw)
    except ValueError:
        return None, "the CPER header bytes were invalid"
    if data[:4] != b"CPER" or data[6:10] != b"\xff\xff\xff\xff":
        return None, "the CPER header signatures do not match"
    sections = int.from_bytes(data[10:12], "little")
    declared = int.from_bytes(data[20:24], "little")
    if sections == 0 or 128 + 72 * sections > declared or declared > payload_bytes:
        return None, "the CPER header's section count or length is inconsistent"
    severity = int.from_bytes(data[12:16], "little")
    flags = int.from_bytes(data[104:108], "little")
    return {"severity": {0: "recoverable", 1: "fatal", 2: "corrected", 3: "informational"}.get(severity, f"unknown ({severity})"),
            "previous_session": bool(flags & 0x2)}, None


def fixed_header_issue(raw: Any, payload_bytes: Any) -> str | None:
    """Why one bounded projection cannot supply a fixed CPER header."""
    header, _ = fixed_cper_header(raw, payload_bytes)
    if header is not None:
        return None
    if raw is None:
        return "no_payload"
    if type(payload_bytes) is int and payload_bytes < 128:
        return "short_payload"
    return "invalid_header"


STORMS_SCRIPT_TEMPLATE = r"""
$queried = (Get-Date).ToUniversalTime()
$queried = $queried.AddTicks(-($queried.Ticks % 10000))
$requestedUntil = {before_assignment}
$until = if ($requestedUntil -gt $queried) { $queried } else { $requestedUntil }
$untilIso = $until.ToString('o')
$queryUntilIso = $until.AddMilliseconds(1).ToString('o')
$epoch = [datetime]::SpecifyKind([datetime]'1970-01-01T00:00:00', [System.DateTimeKind]::Utc)
$bucketTicks = [long]{bucket_seconds} * [long]10000000
$elapsedTicks = $until.AddTicks(-1).Ticks - $epoch.Ticks
$currentBucketTicks = $elapsedTicks - ($elapsedTicks % $bucketTicks)
$startIso = $epoch.AddTicks($currentBucketTicks - ([long]({count} - 1) * $bucketTicks)).ToString('o')
$xml = @"
<QueryList><Query Id='0' Path='System'><Select Path='System'>*[System[Provider[@Name='Microsoft-Windows-WHEA-Logger'] and TimeCreated[@SystemTime&gt;='$startIso' and @SystemTime&lt;'$queryUntilIso']]]</Select></Query></QueryList>
"@
$found = [System.Collections.Generic.List[object]]::new()
$records = @(); $outcome = 'failed'; $errorText = $null; $truncated = $null; $stopped = $null
try {
    Get-WinEvent -FilterXml ([xml]$xml) -MaxEvents {extra} -ErrorAction Stop | & { process { [void]$found.Add($_) } }
    $truncated = $found.Count -gt {cap}
} catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*' -and $found.Count -eq 0) { $truncated = $false }
    elseif ($found.Count) {
        $kind = if ($_.CategoryInfo.Category -eq [System.Management.Automation.ErrorCategory]::PermissionDenied -or $_.Exception -is [System.UnauthorizedAccessException]) { 'denied' } else { 'failed' }
        $detail = ([string]$_.Exception.Message -split '\r?\n')[0].Trim()
        if (-not $detail) { $detail = 'the System query stopped early' }
        if ($detail.Length -gt 300) {
            $detail = $detail.Substring(0, 300)
            if ([char]::IsHighSurrogate($detail[299])) { $detail = $detail.Substring(0, 299) }
        }
        $stopped = [pscustomobject]@{ kind = $kind; detail = $detail }
    } else {
        $outcome = if ($_.CategoryInfo.Category -eq [System.Management.Automation.ErrorCategory]::PermissionDenied -or $_.Exception -is [System.UnauthorizedAccessException]) { 'denied' } else { 'failed' }
        $errorText = $_.Exception.Message
    }
}
if ($null -eq $errorText) {
    try {
        $records = @($found | Select-Object -First {cap} -ErrorAction Stop | ForEach-Object {
            $event = $_
            $bytes = $null
            foreach ($property in $event.Properties) {
                if ($property.Value -is [byte[]]) { $bytes = $property.Value; break }
            }
            [pscustomobject]@{
                RecordId = $event.RecordId; Id = $event.Id; ProviderName = $event.ProviderName; LogName = $event.LogName
                LevelDisplayName = $event.LevelDisplayName
                TimeCreated = $(if ($null -ne $event.TimeCreated) { $event.TimeCreated.ToUniversalTime().ToString('o') } else { $null })
                Message = $event.Message
                HeaderHex = $(if ($null -ne $bytes) { [System.BitConverter]::ToString($bytes, 0, [Math]::Min(128, $bytes.Length)).Replace('-','') } else { $null })
                PayloadBytes = $(if ($null -ne $bytes) { $bytes.Length } else { $null })
            }
        })
        if ($records.Count -ne [Math]::Min($found.Count, {cap})) { throw 'the System event projection returned fewer records than the query' }
        $outcome = if ($records.Count) { 'ok' } else { 'empty' }
    } catch {
        $records = @(); $outcome = 'failed'; $errorText = $_.Exception.Message; $truncated = $null; $stopped = $null
    }
}
$meta = Read-LogMetadata 'System'
[pscustomobject]@{
    window_start = $startIso; window_end = $untilIso; queried_at = $queried.ToString('o')
    source = [pscustomobject]@{
        log = 'System'; outcome = $outcome; error = $errorText
        returned = $records.Count; limit = {cap}; truncated = $truncated; stopped = $stopped; records = $records
        log_enabled = $meta.log_enabled; log_mode = $meta.log_mode; log_state = $meta.log_state; log_error = $meta.log_error
        log_oldest = $meta.log_oldest; oldest_state = $meta.oldest_state; oldest_error = $meta.oldest_error
    }
}
"""


def storms_script(window: Window, before: str = "") -> str:
    """Ask the Windows clock for the observed end and bucket-aligned System window."""
    stamp = before_stamp(before) if before.strip() else None
    assignment = f"[datetimeoffset]::Parse('{stamp}').UtcDateTime" if stamp else "$queried"
    return (LOG_METADATA_SCRIPT + STORMS_SCRIPT_TEMPLATE.replace("{bucket_seconds}", str(window.bucket_seconds))
            .replace("{count}", str(window.count)).replace("{cap}", str(RECORD_CAP)).replace("{extra}", str(RECORD_CAP + 1))
            .replace("{before_assignment}", assignment))


def take_storms(bridge: Bridge, params: dict[str, Any]) -> Reading:
    started = time.perf_counter()
    if params["burst_threshold"] is None or params["burst_threshold"] < 1:
        raise ValueError("parameter 'burst_threshold': must be at least one record")
    if params["accel_threshold"] is None or params["accel_threshold"] <= 0:
        raise ValueError("parameter 'accel_threshold': must be greater than zero")
    # This validates the requested shape; the Windows collector supplies the actual clock.
    requested = window_for(params["hours"], params["bucket_seconds"], now=0)
    before = str(params.get("before") or "").strip()
    requested_end = before_stamp(before) if before else None
    requested_key = stamp_key(requested_end) if requested_end else None
    requested_start_key = (requested_key[0] - timedelta(hours=params["hours"]), requested_key[1]) if requested_key else None
    script = storms_script(requested, before)
    result = bridge.run(script)

    collection: dict[str, Any] = {}
    reach: dict[str, Any] = {}
    records: list[dict[str, Any]] = []

    def build(payload: dict[str, Any]) -> list[Section]:
        start_text, end_text = payload.get("window_start"), payload.get("window_end")
        host_window = _host_window(start_text, end_text, requested)
        queried_at = payload.get("queried_at")
        query_key, end_key = stamp_key(queried_at), stamp_key(end_text)
        problem = "the collector's window bounds or query time failed validation"
        if requested_start_key is not None and query_key is not None and requested_start_key >= query_key:
            problem = "the requested storm window begins at or after the machine's query time"
            host_window = None
        if query_key is None or end_key is None or end_key != min(query_key, requested_key or query_key):
            host_window = None
        source, returned = _storm_source(
            payload.get("source") if host_window else None, start_text, end_text,
            problem=problem if host_window is None else "the storm source result or record projection failed validation",
        )
        collection.update(window_start=start_text, window_end=end_text, queried_at=queried_at, system=source)
        records.extend(returned)
        reach.update(system=log_coverage(source, returned, start_text, end_text))
        future_end = requested_key is not None and query_key is not None and requested_key > query_key
        reach["system"]["covered_until"] = end_text if reach["system"]["covered_from"] is not None else None
        if future_end and reach["system"]["complete"] is not None:
            reach["system"]["complete"] = False
        sections = compose(returned, host_window, params, reach["system"], stopped=source.get("stopped"), anchored=bool(before)) if host_window and source["outcome"] in ("ok", "empty") else []
        return [
            *sections,
            Section("collection", "raw", collection),
            Section("coverage", "derived", reach, basis=STORM_COVERAGE_BASIS),
        ]

    reading = from_object("storms", params, script, result, build)
    if not reading.observed:
        return reading
    source = collection["system"]
    if source["outcome"] not in ("ok", "empty"):
        reading.outcome = source["outcome"]
        reading.count = None
        reading.error = {"kind": reading.outcome, "detail": source["error"] or "The System event query did not answer."}
    else:
        reading.outcome = "ok" if records else "empty"
        reading.count = len(records)
        query_key = stamp_key(collection.get("queried_at"))
        if requested_key is not None and query_key is not None and requested_key > query_key:
            reading.warnings.append("the requested end is after the machine's query time; the observed storm window ends there and its start moves earlier too")
        if source["truncated"]:
            reading.warnings.append(f"the System query reached its {RECORD_CAP}-record limit; older matching records were not returned")
        if isinstance(source.get("stopped"), dict):
            reading.warnings.append(f"the System query stopped after {source['returned']} returned records: {source['stopped']['detail']}")
        issues = source.get("row_issues") or {}
        if issues.get("unplaced"):
            reading.warnings.append(f"{issues['unplaced']} returned WHEA-Logger records had no readable time; no bucket can be called quiet")
        if issues.get("outside_window"):
            reading.warnings.append(f"{issues['outside_window']} returned records fell just outside the requested window and were not counted")
        unreadable_headers = reading.section("buckets").data["header_unreadable"] if reading.section("buckets") else 0
        if unreadable_headers:
            reading.warnings.append(f"{unreadable_headers} returned System reports have no readable fixed CPER header; their PreviousError flags are unknown")
        oldest = stamp_key(source.get("log_oldest"))
        start = stamp_key(collection.get("window_start"))
        end = stamp_key(collection.get("window_end"))
        if oldest is not None and end is not None and oldest >= end:
            reading.warnings.append("the System log's retained history begins at or after this storm window; no earlier absence can be established")
        elif reach["system"]["complete"] is False and reach["system"]["covered_from"] is None:
            reading.warnings.append("System log coverage could not be established")
        elif oldest is not None and start is not None and oldest >= start:
            reading.warnings.append("System log retention does not cover the whole requested storm window")
    reading.took_ms = _ms(started)
    return reading


STORM_ROW_KEYS = {"RecordId", "Id", "ProviderName", "LogName", "LevelDisplayName", "TimeCreated", "Message", "HeaderHex", "PayloadBytes"}


def _host_window(start: Any, end: Any, requested: Window) -> Window | None:
    """Validate the host's exact window before any returned row is counted."""
    end_key = stamp_key(end)
    if end_key is None:
        return None
    try:
        # The query end is exclusive. On an exact bucket boundary the last observed
        # bucket is the preceding one, not an unobserved bucket starting at the end.
        last = end_key[0] if end_key[1] else end_key[0] - timedelta(microseconds=1)
        current = int(last.timestamp() // requested.bucket_seconds) * requested.bucket_seconds
        aligned = Window(current - (requested.count - 1) * requested.bucket_seconds, requested.bucket_seconds, requested.count)
        aligned_start = stamp_key(_stamp(aligned.start))
    except (ValueError, OverflowError, OSError):
        return None
    if stamp_key(start) != aligned_start or aligned_start is None or aligned_start > end_key:
        return None
    return aligned


def _storm_source(value: Any, start: Any, end: Any, *, problem: str = "the storm source result or record projection failed validation") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fallback = {**log_metadata({}), "log": LOG, "outcome": "failed", "returned": 0, "limit": RECORD_CAP, "truncated": None, "stopped": None, "row_issues": {"unplaced": 0, "outside_window": 0}, "error": problem}
    first, until = stamp_key(start), stamp_key(end)
    if not isinstance(value, dict) or first is None or until is None or first > until:
        return fallback, []
    outcome = value.get("outcome")
    if outcome in ("failed", "denied"):
        return {**log_metadata(value), "log": LOG, "outcome": outcome, "returned": 0, "limit": RECORD_CAP, "truncated": None, "stopped": None, "row_issues": {"unplaced": 0, "outside_window": 0}, "error": value.get("error") or "the source did not answer"}, []
    rows = value.get("records")
    if outcome not in ("ok", "empty") or not isinstance(rows, list):
        return fallback, []
    stopped = value.get("stopped")
    valid_stop = stopped is None or (
        isinstance(stopped, dict) and set(stopped) == {"kind", "detail"}
        and stopped["kind"] in ("failed", "denied")
        and isinstance(stopped["detail"], str) and 0 < len(stopped["detail"]) <= 300
    )
    valid = (
        value.get("log") == LOG
        and type(value.get("returned")) is int and value["returned"] == len(rows)
        and type(value.get("limit")) is int and value["limit"] == RECORD_CAP
        and valid_stop and "truncated" in value
        and (value.get("truncated") is None if stopped is not None else type(value.get("truncated")) is bool)
        and len(rows) <= RECORD_CAP
        and (value.get("truncated") is not True or len(rows) == RECORD_CAP)
        and (outcome == "empty") == (len(rows) == 0)
        and (stopped is None or outcome == "ok" and bool(rows))
        and value.get("error") is None
    )
    if not valid:
        return fallback, []
    kept: list[dict[str, Any]] = []
    issues = {"unplaced": 0, "outside_window": 0}
    for row in rows:
        location = _storm_row_location(row, first, until)
        if location is None:
            return fallback, []
        if location == "outside_window":
            issues[location] += 1
        else:
            kept.append(row)
            if location == "unplaced":
                issues[location] += 1
    if stopped is not None and not kept:
        return {**log_metadata(value), "log": LOG, "outcome": stopped["kind"], "returned": len(rows), "limit": RECORD_CAP, "truncated": None, "stopped": stopped, "row_issues": issues, "error": "the System query stopped before any in-window record could be counted"}, []
    return {**log_metadata(value), "outcome": outcome, "returned": len(rows), "limit": RECORD_CAP, "truncated": value["truncated"], "stopped": stopped, "row_issues": issues, "error": None}, kept


def _storm_row_location(row: Any, first: tuple[datetime, int], until: tuple[datetime, int]) -> str | None:
    if not isinstance(row, dict) or not set(row) <= STORM_ROW_KEYS or not {"HeaderHex", "PayloadBytes"} <= set(row):
        return None
    record_id = row.get("RecordId")
    if not (
        row.get("LogName") == LOG and row.get("ProviderName") == PROVIDER
        and (record_id is None or type(record_id) is int and record_id > 0)
        and type(row.get("Id")) is int
        and (row.get("LevelDisplayName") is None or isinstance(row["LevelDisplayName"], str))
        and (row.get("Message") is None or isinstance(row["Message"], str))
        and valid_fixed_header_projection(row.get("HeaderHex"), row.get("PayloadBytes"))
    ):
        return None
    stamp = row.get("TimeCreated")
    if stamp is not None and not isinstance(stamp, str):
        return None
    at = stamp_key(stamp)
    if at is None:
        return "unplaced"
    if first <= at < until:
        return "inside"
    # Event Log's XPath clock may round at a millisecond boundary. Accept only that
    # narrow disagreement, and do not store or count the out-of-window projection.
    delta = ((first[0] - at[0]) if at < first else (at[0] - until[0])).total_seconds()
    delta += (first[1] - at[1] if at < first else at[1] - until[1]) / 10_000_000
    return "outside_window" if 0 <= delta <= 0.001 else None


def compose(records: list[dict[str, Any]], window: Window, params: dict[str, Any], reach: dict[str, Any], *, stopped: Any = None, anchored: bool = False) -> list[Section]:
    """Returned events remain visible; a bucket is zero only when retention covers all of it."""
    returned_totals = [0] * window.count
    previous = [0] * window.count
    unreadable = [0] * window.count
    not_marked = [0] * window.count
    previous_total = 0
    unreadable_total = 0
    unreadable_reasons = {"no_payload": 0, "short_payload": 0, "invalid_header": 0}
    per_bucket: list[dict[str, int]] = [{} for _ in range(window.count)]
    signatures: dict[str, _Signature] = {}
    unplaced = 0

    for record in records:
        header, _ = fixed_cper_header(record.get("HeaderHex"), record.get("PayloadBytes"))
        header_issue = fixed_header_issue(record.get("HeaderHex"), record.get("PayloadBytes")) if header is None else None
        previous_session = header["previous_session"] if header is not None else None
        if previous_session is True:
            previous_total += 1
        elif header_issue is not None:
            unreadable_total += 1
            unreadable_reasons[header_issue] += 1
        at = stamp_key(record.get("TimeCreated"))
        idx = window.index(at[0].timestamp()) if at is not None else None
        if idx is None:
            unplaced += 1
            continue
        sig = _accumulate(signatures, record, previous_session)
        returned_totals[idx] += 1
        if previous_session is True:
            previous[idx] += 1
        elif header_issue is not None:
            unreadable[idx] += 1
        else:
            not_marked[idx] += 1
        per_bucket[idx][sig.id] = per_bucket[idx].get(sig.id, 0) + 1

    cutoff = stamp_key(reach.get("covered_from"))
    inclusive = reach.get("covered_from_inclusive") is True
    totals: list[int | None] = []
    for index, count in enumerate(returned_totals):
        start = stamp_key(_stamp(window.start + index * window.bucket_seconds))
        covered = cutoff is not None and start is not None and (start > cutoff or inclusive and start == cutoff)
        totals.append(count if covered and not unplaced else None)

    buckets = {
        "from": _stamp(window.start),
        "to": _stamp(window.end),
        "bucket_seconds": window.bucket_seconds,
        "bucket_count": window.count,
        "total": sum(returned_totals),
        "unplaced": unplaced,
        "previous_session": previous_total,
        "header_unreadable": unreadable_total,
        "header_unreadable_reasons": unreadable_reasons,
        "totals": totals,
        "unknown_buckets": sum(value is None for value in totals),
        "active": [
            {"index": i, "start": _stamp(window.start + i * window.bucket_seconds), "total": returned_totals[i], "complete": totals[i] is not None,
             "previous_session": previous[i], "header_unreadable": unreadable[i], "signatures": per_bucket[i]}
            for i in range(window.count)
            if returned_totals[i]
        ],
    }
    ranked = sorted(signatures.values(), key=lambda s: (s.count, s.last_seen), reverse=True)
    sections = [
        Section(
            "buckets",
            "derived",
            buckets,
            basis=(
                f"placed in-window WHEA-Logger records counted into wall-clock buckets of {window.bucket_seconds} seconds: "
                "'totals' has null where retained history, an early stop, the record cap or an event with unreadable time cannot establish a whole bucket, "
                "zero only for an observed quiet bucket, and a count otherwise. 'active' keeps returned "
                "records even in an incomplete bucket; the final bucket is observed only through collection.window_end. "
                "PreviousError and unreadable-header totals include returned rows without readable filing time; "
                "active buckets and signatures require placed rows. Header bytes are used for derivation and omitted from the reading. "
                "An anchored window has no inferred burst, acceleration or quiet status; its thresholds are not applied."
            ),
        ),
        Section(
            "signatures",
            "derived",
            [s.to_dict() for s in ranked],
            basis="Counts, PreviousError flags and first/last System-log filing times describe placed in-window returned records only, not hardware occurrence times. An unreadable fixed header leaves PreviousError unknown. SHA-256 over the error type, bank, APIC id, MCI status, PCI vendor and device ids and the normalized message text; the first characters of the digest identify the signature",
        ),
    ]
    if not anchored:
        sections.append(Section("status", "inferred", status(totals, returned_totals, per_bucket, params, unplaced,
                                                            previous=previous, unreadable=unreadable, not_marked=not_marked,
                                                            reach=reach, stopped=stopped), basis=_status_basis(params)))
    return sections


def status(totals: list[int | None], returned_totals: list[int], per_bucket: list[dict[str, int]], params: dict[str, Any], unplaced: int = 0, *, previous: list[int], unreadable: list[int], not_marked: list[int], reach: dict[str, Any], stopped: Any = None) -> dict[str, Any]:
    """A returned burst is evidence; acceleration and quiet require their whole source window."""
    burst_threshold = params["burst_threshold"]
    accel_threshold = params["accel_threshold"]

    recent = totals[-RECENT_BUCKETS:]
    recent_from = max(0, len(totals) - RECENT_BUCKETS)
    baseline = totals[max(0, recent_from - BASELINE_BUCKETS) : recent_from]

    recent_known = bool(recent) and all(value is not None for value in recent)
    baseline_known = bool(baseline) and all(value is not None for value in baseline)
    whole_window = all(value is not None for value in totals) and not unplaced
    observed_peak = max(returned_totals[-RECENT_BUCKETS:], default=0)
    recent_previous, recent_unreadable, recent_not_marked = previous[-RECENT_BUCKETS:], unreadable[-RECENT_BUCKETS:], not_marked[-RECENT_BUCKETS:]
    not_marked_at_least = max(recent_not_marked, default=0)
    not_marked_at_most = (max((known + unknown for known, unknown in zip(recent_not_marked, recent_unreadable, strict=True)), default=0)
                          if recent_known and not unplaced else None)
    not_marked_burst = (True if not_marked_at_least >= burst_threshold else
                        False if not_marked_at_most is not None and not_marked_at_most < burst_threshold else None)
    peak = max(recent) if recent_known else None
    recent_rate = _mean([value for value in recent if value is not None]) if recent_known else None
    baseline_rate = _mean([value for value in baseline if value is not None]) if baseline_known else None
    acceleration = round(recent_rate / max(baseline_rate, BASELINE_FLOOR), 3) if recent_rate is not None and baseline_rate is not None else None
    dominant = _dominant(per_bucket[recent_from:])

    if observed_peak >= burst_threshold:
        state, severity = "burst", ("critical" if observed_peak > burst_threshold * 2 else "warning")
        reason = f"at least {observed_peak} returned records in one recent bucket; the burst threshold is {burst_threshold}" if not recent_known else f"{observed_peak} records in one bucket of the recent window; the burst threshold is {burst_threshold}"
    elif acceleration is not None and acceleration >= accel_threshold and recent_rate is not None and recent_rate > RECENT_FLOOR:
        state, severity = "accelerating", "warning"
        reason = f"the recent rate is {acceleration}x the baseline ({recent_rate} against {baseline_rate} per bucket); the acceleration threshold is {accel_threshold}"
    elif not whole_window or not baseline_known:
        state, severity = "unknown", None
        if stopped is not None:
            reason = "The System event query stopped before the requested window was fully read."
        elif unplaced:
            reason = "Some returned records could not be placed in time buckets."
        elif reach.get("covered_from") is None:
            reason = "Log coverage could not be established for the requested window."
        elif not whole_window:
            reason = "The requested window is not fully covered by retained log history and returned records."
        else:
            reason = "There are too few buckets to establish a baseline."
    else:
        state, severity = "quiet", None
        reason = (
            "the window holds no WHEA-Logger records"
            if sum(value for value in totals if value is not None) == 0
            else f"no bucket reached the burst threshold of {burst_threshold} and the recent rate is not {accel_threshold}x the baseline"
        )

    return {
        "state": state,
        "severity": severity,
        "reason": reason,
        "peak_rate": float(peak) if peak is not None else None,
        "observed_peak": observed_peak,
        "recent_rate": recent_rate,
        "baseline_rate": baseline_rate,
        "acceleration": acceleration,
        "dominant": dominant,
        "recent_buckets": len(recent),
        "baseline_buckets": len(baseline),
        "recent_observed": sum(value is not None for value in recent),
        "baseline_observed": sum(value is not None for value in baseline),
        "recent_composition": {"previous_session": sum(recent_previous), "not_marked": sum(recent_not_marked),
                               "header_unreadable": sum(recent_unreadable)},
        "not_marked_peak": {"at_least": not_marked_at_least, "at_most": not_marked_at_most},
        "not_marked_burst": not_marked_burst,
    }


def _status_basis(params: dict[str, Any]) -> str:
    return (
        "Live System filing-time report-traffic lead; quiet does not clear Kernel-WHEA/Errors. "
        f"Burst: at least {params['burst_threshold']} reports in one of the last {RECENT_BUCKETS} buckets; critical above twice that. "
        f"Acceleration: at least {params['accel_threshold']}x the prior {BASELINE_BUCKETS}-bucket average, with covered windows and a noise floor. "
        "Quiet needs whole-window coverage; the current bucket ends at query time. "
        "recent_composition separates readable PreviousError flags from unreadable headers. "
        "not_marked_burst is true from returned reports, false only if complete recent buckets stay below threshold even with unreadable headers, otherwise null. "
        "A clear flag does not date an error. Lead, not diagnosis."
    )


def _dominant(per_bucket: list[dict[str, int]], top: int = 3) -> list[str]:
    totals: dict[str, int] = {}
    for bucket in per_bucket:
        for sig, count in bucket.items():
            totals[sig] = totals.get(sig, 0) + count
    return [sig for sig, _ in sorted(totals.items(), key=lambda pair: -pair[1])[:top]]


# ---------------------------------------------------------------- signatures

# The message is the only field the record shape carries that names the failing part. The
# extraction and the signature are the old rule; the patterns accept the 0x prefix Windows
# writes into the PCI ids, which the old expression could not match.
_ERROR_TYPES = (
    ("PCIe Error", ("PCI", "Root Port")),
    ("Memory Error", ("Memory", "ECC")),
    ("Cache Error", ("Cache",)),
    ("Bus Error", ("Bus", "Interconnect")),
    ("TLB Error", ("Translation Lookaside Buffer", "TLB")),
)
_BANK = re.compile(r"Bank(?:\s*Number)?:\s*(\d+)", re.I)
_APIC = re.compile(r"APIC\s*ID:\s*(\d+)", re.I)
_MCI = re.compile(r"MCi?[\s_]*Status:\s*(?:0x)?([0-9a-fA-F]+)", re.I)
_PCI = re.compile(r"Vendor\s*ID:Device\s*ID:\s*(?:0x)?([0-9a-fA-F]+):(?:0x)?([0-9a-fA-F]+)", re.I)
_HEX = re.compile(r"0x[0-9a-fA-F]+")
_GUID = re.compile(r"\{[0-9a-fA-F-]{36}\}")


@dataclass
class _Signature:
    id: str
    key: str
    description: str
    error_type: str
    bank: str | None
    apic_id: str | None
    mci_status: str | None
    vendor_id: str | None
    device_id: str | None
    first_seen: str = ""
    last_seen: str = ""
    count: int = 0
    previous_session: int = 0
    header_unreadable: int = 0
    event_ids: set[int] = field(default_factory=set)
    sample: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "description": self.description,
            "error_type": self.error_type,
            "bank": self.bank,
            "apic_id": self.apic_id,
            "mci_status": self.mci_status,
            "vendor_id": self.vendor_id,
            "device_id": self.device_id,
            "count": self.count,
            "previous_session": self.previous_session,
            "header_unreadable": self.header_unreadable,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "event_ids": sorted(self.event_ids),
            "sample": self.sample,
        }


def signature(record: dict[str, Any]) -> _Signature:
    """One signature from one record: the stable hardware features first, the normalized message last."""
    message = str(record.get("Message") or "")
    bank = _first(_BANK, message)
    apic_id = _first(_APIC, message)
    mci = _first(_MCI, message)
    mci_status = f"0x{mci.lower()}" if mci else None
    pci = _PCI.search(message)
    vendor_id = pci.group(1).lower() if pci else None
    device_id = pci.group(2).lower() if pci else None
    error_type = "PCIe Error" if pci else _error_type(message)

    parts = [SIGNATURE_VERSION, f"ET:{error_type}"]
    if bank:
        parts.append(f"BK:{bank}")
    if apic_id:
        parts.append(f"APIC:{apic_id}")
    if mci_status:
        parts.append(f"MCI:{mci_status}")
    if vendor_id and device_id:
        parts.append(f"PCI:{vendor_id}:{device_id}")
    parts.append(f"MSG:{normalize(message)}")

    key = "|".join(parts)
    description = error_type
    if bank:
        description += f" (bank {bank})"
    if apic_id:
        description += f" (APIC {apic_id})"
    if vendor_id:
        description += f" [PCI {vendor_id}:{device_id}]"

    return _Signature(
        id=hashlib.sha256(key.encode("utf-8")).hexdigest()[:SIGNATURE_ID_CHARS],
        key=key,
        description=description,
        error_type=error_type,
        bank=bank,
        apic_id=apic_id,
        mci_status=mci_status,
        vendor_id=vendor_id,
        device_id=device_id,
    )


def normalize(message: str) -> str:
    """Strip what varies between two instances of the same error: addresses, GUIDs, spacing, the tail."""
    if not message:
        return ""
    text = _GUID.sub("<GUID>", _HEX.sub("<HEX>", message))
    return " ".join(text.split())[:100]


def _accumulate(signatures: dict[str, _Signature], record: dict[str, Any], previous_session: bool | None) -> _Signature:
    sig = signature(record)
    held = signatures.setdefault(sig.id, sig)
    held.count += 1
    if previous_session is True:
        held.previous_session += 1
    elif previous_session is None:
        held.header_unreadable += 1
    stamp = str(record.get("TimeCreated") or "")
    held.first_seen = min(held.first_seen or stamp, stamp)
    if stamp >= held.last_seen:
        held.last_seen = stamp
        held.sample = {
            "RecordId": record.get("RecordId"),
            "TimeCreated": record.get("TimeCreated"),
            "Id": record.get("Id"),
            "LevelDisplayName": record.get("LevelDisplayName"),
            "Message": record.get("Message"),
            "previous_session": previous_session,
        }
    if isinstance(record.get("Id"), int):
        held.event_ids.add(record["Id"])
    return held


def _error_type(message: str) -> str:
    for name, markers in _ERROR_TYPES:
        if any(marker in message for marker in markers):
            return name
    return "Unknown"


def _first(pattern: re.Pattern[str], message: str) -> str | None:
    match = pattern.search(message)
    return match.group(1) if match else None


# ---------------------------------------------------------------- shared


def _stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _mean(values: list[int]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


register(
    Spec(
        name="whea",
        description=(
            "Hardware error records from both places Windows keeps them: WHEA-Logger in the System log and "
            "the Kernel-WHEA CPER events in Microsoft-Windows-Kernel-WHEA/Errors, newest first across both, "
            "each with its log, its raw payload and its CPER header identity. Each source reports its own "
            "outcome and how far its log reaches back; a source that did not answer is never an empty log. "
            "Records that are likely one error reported in both logs are grouped, never merged. WHEA-Logger "
            "payloads are decoded beside them; channel records are not decoded yet."
        ),
        classes=("raw", "derived"),
        take=take_whea,
        params=(Param("count", "int", 30, "How many of the most recent records across both logs.", minimum=1, maximum=MAX_WHEA_RECORDS),),
        private=("MachineName", "user names inside Message", "CPER bytes in RawData and Properties", "serial and UUID fields inside the decoded structure"),
    )
)

register(
    Spec(
        name="whea_record",
        description=(
            "One exact hardware-error event by its required log source and RecordId, including its raw CPER payload "
            "when explicitly unredacted. Works for a retained report older than whea's newest 500 rows. "
            "Use source=system for Log=System or source=kernel_whea for Log=Microsoft-Windows-Kernel-WHEA/Errors; "
            "compare the returned TimeCreated with the original reference because log-local IDs can be reused. "
            "A missing row is empty; an interrupted, failed or ambiguous query cannot establish absence. "
            "This is a report reference, not a diagnosis or a claim about when a previous-session error occurred."
        ),
        classes=("raw", "derived"),
        take=take_whea_record,
        params=(
            Param("source", "str", None, "The log that owns this RecordId: system for System, kernel_whea for Microsoft-Windows-Kernel-WHEA/Errors.", choices=tuple(s.name for s in WHEA_SOURCES)),
            Param("record_id", "int", None, "EventRecordID in that log, as shown in whea or whea_reports.", minimum=1, maximum=MAX_RECORD_ID),
        ),
        private=("MachineName", "user names inside Message", "CPER bytes in RawData and Properties", "serial and UUID fields inside the decoded structure"),
        requires_selection=True,
    )
)

register(
    Spec(
        name="storms",
        description="WHEA-Logger reports in the System log over a wall-clock filing-time window, grouped by signature. Fixed CPER headers add PreviousError and unreadable counts when available; a clear flag does not date an error. A live window has report-traffic burst and acceleration status plus a separate true/false/unknown lead for reports not marked earlier-session; a historical before window keeps its buckets and signatures without live status. Computed from the log on each take; nothing is stored between takes. The separate Kernel-WHEA/Errors channel is not counted here.",
        classes=("raw", "derived", "inferred"),
        take=take_storms,
        params=(
            Param("hours", "int", 24, "Hours preceding before, or the query time when before is empty; the actual start is bucket-aligned.", minimum=1, maximum=MAX_HOURS),
            Param("bucket_seconds", "int", 60, "The width of one wall-clock bucket.", minimum=1),
            Param("burst_threshold", "int", 5, "Live window only: records in one bucket that count as a burst; twice this is critical.", minimum=1),
            Param("accel_threshold", "float", 2.0, "Live window only: how many times the baseline rate the recent rate must reach to count as accelerating."),
            Param("before", "str", "", "Exclusive historical end with Z or an offset; empty uses the query time and includes live status."),
        ),
        private=("user names and profile paths inside the signature samples' message text",),
    )
)
