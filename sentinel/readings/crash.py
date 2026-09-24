"""The stop: ``crash`` (the stops the machine did not plan) and ``faults`` (what went wrong while
it kept running).

"It froze at 02:14." The log does not announce a freeze; the next start does, and the facts that
name one stop are spread over four records in two logs and a file on disk. ``crash`` fetches them
in one launch and composes one stop per session: when the machine stopped as Windows estimated it,
when it started again, the bug check if one was written, the dump that belongs to it, and the last
System record before the next start. That record can be later than Windows' stop estimate. Given a
moment instead, it reports what the first start after that moment announced when retained System
records can establish it; otherwise it keeps returned stops and names the gap.

``faults`` is the other half of the same question: the programs that crashed or hung, and the
kernel's own live reports, which are failures the machine survived and so never become a stop.

The scripts collect; every derivation is here in Python beside the table that names it, so a test
can hold a rule to a record rather than to a machine. The field maps are this build's event
manifests as they were read on 2026-09-21. A record whose properties do not fit its map keeps its
raw form and carries the mismatch as an error: a property read under the wrong name is worse than
one not read at all, and a code the tables do not know keeps no name rather than a guess.
"""

from __future__ import annotations

import re
import struct
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from ..bridge import Bridge
from ..reading import Param, Reading, Section, Spec, from_object, register
from .dumps import DUMPS_SCRIPT, inventory, missing_file
from .event_coverage import LOG_METADATA_SCRIPT, exact_stamp, stamp_key
from .event_coverage import metadata as log_metadata
from .events import _utc_stamp, from_log_collector, log_records_script, record_projection, since_clause, window_clauses
from .fault_process import APPLICATION_ERROR, APPLICATION_ERROR_1000, APPLICATION_HANG, APPLICATION_HANG_1002, process_identity

# The providers, spelled once. The same event id means different things under different providers:
# 1001 is a bug check under WER-SystemErrorReporting and a report of any kind under Windows Error
# Reporting, so nothing here is keyed by an id alone.
KERNEL_GENERAL = "Microsoft-Windows-Kernel-General"
KERNEL_POWER = "Microsoft-Windows-Kernel-Power"
EVENTLOG = "EventLog"
WER_SYSTEM = "Microsoft-Windows-WER-SystemErrorReporting"
WER_REPORTING = "Windows Error Reporting"

MAX_STOPS = 20
MAX_FAULTS = 500
# A moment asks one question — what did the next start announce — so the window it needs is the
# session that start opens and a little of what follows, not a count of stops.
MOMENT_RECORDS = 48
# The payload nests one level deeper than a record list: the object, its arrays, each record and
# each record's Properties.
DEPTH = 8


# ---------------------------------------------------------------------------
# The field maps: this build's event manifests, read on 2026-09-21
# ---------------------------------------------------------------------------

KERNEL_POWER_41 = (
    "BugcheckCode",
    "BugcheckParameter1",
    "BugcheckParameter2",
    "BugcheckParameter3",
    "BugcheckParameter4",
    "SleepInProgress",
    "PowerButtonTimestamp",
    "BootAppStatus",
    "Checkpoint",
    "ConnectedStandbyInProgress",
    "SystemSleepTransitionsToOn",
    "CsEntryScenarioInstanceId",
    "BugcheckInfoFromEFI",
    "CheckpointStatus",
    "CsEntryScenarioInstanceIdV2",
    "LongPowerButtonPressDetected",
    "LidReliability",
    "InputSuppressionState",
    "PowerButtonSuppressionState",
    "LidState",
    "WHEABootErrorCount",
)

KERNEL_GENERAL_12 = ("MajorVersion", "MinorVersion", "BuildVersion", "QfeVersion", "ServiceVersion", "BootMode", "StartTime")

# WER-SystemErrorReporting 1001 is not in this machine's System log: the names are Microsoft's
# template, not an observation, which is why the decoding falls back to the message text.
WER_SYSTEM_1001 = ("param1", "param2", "param3")

WER_REPORT = (
    "Bucket",
    "BucketType",
    "EventName",
    "Response",
    "CabId",
    "P1",
    "P2",
    "P3",
    "P4",
    "P5",
    "P6",
    "P7",
    "P8",
    "P9",
    "P10",
    "AttachedFiles",
    "StorePath",
    "AnalysisSymbol",
    "Rechecking",
    "ReportId",
    "ReportStatus",
    "HashedBucket",
    "CabGuid",
)

# EventLog 6008 and Kernel-General 13 carry positional properties this build's manifest does not
# name; they are decoded by position, and 6008's stop time comes from its binary value instead.
KINDS: dict[tuple[str, int], tuple[str, tuple[str, ...]]] = {
    (KERNEL_GENERAL, 12): ("start", KERNEL_GENERAL_12),
    (KERNEL_GENERAL, 13): ("clean shutdown", ()),
    (KERNEL_POWER, 41): ("unexpected shutdown", KERNEL_POWER_41),
    (EVENTLOG, 6008): ("unexpected shutdown, logged at the next start", ()),
    (WER_SYSTEM, 1001): ("bug check", WER_SYSTEM_1001),
    (WER_REPORTING, 1001): ("bug check report", WER_REPORT),
    (APPLICATION_ERROR, 1000): ("application crash", APPLICATION_ERROR_1000),
    (APPLICATION_HANG, 1002): ("application hang", APPLICATION_HANG_1002),
}

# What a Windows Error Reporting 1001 is, from its own EventName. The names this reading does not
# read (APPCRASH, BEX, StoreAgent and the rest) keep the unnamed kind rather than a wrong one.
REPORT_KINDS = {"BlueScreen": "bug check report", "LiveKernelEvent": "live kernel event"}

BLUE_SCREEN = "BlueScreen"
LIVE_KERNEL = "LiveKernelEvent"


# ---------------------------------------------------------------------------
# The tables: Microsoft's names for what the machine wrote down
# ---------------------------------------------------------------------------

# From the Bug Check Code Reference. A wrong name sends someone after the wrong cause, so this
# holds only codes whose name is certain; a code that is not here keeps its number and no name.
BUGCHECKS: dict[int, str] = {
    0x1: "APC_INDEX_MISMATCH",
    0x7: "INVALID_SOFTWARE_INTERRUPT",
    0xA: "IRQL_NOT_LESS_OR_EQUAL",
    0x1A: "MEMORY_MANAGEMENT",
    0x1E: "KMODE_EXCEPTION_NOT_HANDLED",
    0x24: "NTFS_FILE_SYSTEM",
    0x3B: "SYSTEM_SERVICE_EXCEPTION",
    0x3D: "INTERRUPT_EXCEPTION_NOT_HANDLED",
    0x41: "MUST_SUCCEED_POOL_EMPTY",
    0x44: "MULTIPLE_IRP_COMPLETE_REQUESTS",
    0x4E: "PFN_LIST_CORRUPT",
    0x50: "PAGE_FAULT_IN_NONPAGED_AREA",
    0x7A: "KERNEL_DATA_INPAGE_ERROR",
    0x7E: "SYSTEM_THREAD_EXCEPTION_NOT_HANDLED",
    0x7F: "UNEXPECTED_KERNEL_MODE_TRAP",
    0x8E: "KERNEL_MODE_EXCEPTION_NOT_HANDLED",
    0x9C: "MACHINE_CHECK_EXCEPTION",
    0x9F: "DRIVER_POWER_STATE_FAILURE",
    0xA0: "INTERNAL_POWER_ERROR",
    0xAB: "SESSION_HAS_VALID_POOL_ON_EXIT",
    0xBE: "ATTEMPTED_WRITE_TO_READONLY_MEMORY",
    0xC1: "SPECIAL_POOL_DETECTED_MEMORY_CORRUPTION",
    0xC2: "BAD_POOL_CALLER",
    0xC4: "DRIVER_VERIFIER_DETECTED_VIOLATION",
    0xC5: "DRIVER_CORRUPTED_EXPOOL",
    0xC9: "DRIVER_VERIFIER_IOMANAGER_VIOLATION",
    0xCA: "PNP_DETECTED_FATAL_ERROR",
    0xD1: "DRIVER_IRQL_NOT_LESS_OR_EQUAL",
    0xD5: "DRIVER_PAGE_FAULT_IN_FREED_SPECIAL_POOL",
    0xD6: "DRIVER_PAGE_FAULT_BEYOND_END_OF_ALLOCATION",
    0xDE: "POOL_CORRUPTION_IN_FILE_AREA",
    0xE3: "RESOURCE_NOT_OWNED",
    0xEA: "THREAD_STUCK_IN_DEVICE_DRIVER",
    0xEF: "CRITICAL_PROCESS_DIED",
    0xF4: "CRITICAL_OBJECT_TERMINATION",
    0xF5: "FLTMGR_FILE_SYSTEM",
    0xF7: "DRIVER_OVERRAN_STACK_BUFFER",
    0xFC: "ATTEMPTED_EXECUTE_OF_NOEXECUTE_MEMORY",
    0xFE: "BUGCODE_USB_DRIVER",
    0x101: "CLOCK_WATCHDOG_TIMEOUT",
    0x109: "CRITICAL_STRUCTURE_CORRUPTION",
    0x116: "VIDEO_TDR_FAILURE",
    0x117: "VIDEO_TDR_TIMEOUT_DETECTED",
    0x119: "VIDEO_SCHEDULER_INTERNAL_ERROR",
    0x124: "WHEA_UNCORRECTABLE_ERROR",
    0x133: "DPC_WATCHDOG_VIOLATION",
    0x139: "KERNEL_SECURITY_CHECK_FAILURE",
    0x13A: "KERNEL_MODE_HEAP_CORRUPTION",
    0x141: "VIDEO_ENGINE_TIMEOUT_DETECTED",
    0x142: "VIDEO_TDR_APPLICATION_BLOCKED",
    0x144: "BUGCODE_USB3_DRIVER",
    0x14F: "PDC_WATCHDOG_TIMEOUT",
    0x154: "UNEXPECTED_STORE_EXCEPTION",
    0x161: "LIVE_SYSTEM_DUMP",
    0x1CA: "SYNTHETIC_WATCHDOG_TIMEOUT",
    0xC000021A: "WINLOGON_FATAL_ERROR",
    0xC0000221: "STATUS_IMAGE_CHECKSUM_MISMATCH",
}

# The NTSTATUS and language-runtime codes an application crash carries, in the words a person
# reads them by rather than the constant's spelling.
EXCEPTIONS: dict[int, str] = {
    0x80000003: "breakpoint",
    0xC0000005: "access violation",
    0xC0000008: "invalid handle",
    0xC0000094: "integer divide by zero",
    0xC0000096: "privileged instruction",
    0xC00000FD: "stack overflow",
    0xC0000135: "DLL not found",
    0xC000013A: "control-C exit",
    0xC0000142: "DLL initialization failed",
    0xC000001D: "illegal instruction",
    0xC0000374: "heap corruption",
    0xC0000409: "stack buffer overrun",
    0xE0434352: ".NET exception",
    0xE06D7363: "C++ exception",
}

DECODED_BASIS = (
    "the positional properties mapped from the Windows event manifests used by this build; the bug check "
    "code and the exception code named from Microsoft's references; the 6008 stop time read from the record's "
    "binary value as two SYSTEMTIME structures, local then UTC"
)

STOPS_BASIS = (
    "Sessions are bounded by Kernel-General 12; a stop is a session that holds a Kernel-Power 41 or a bug check "
    "report. The EventLog 6008 and the WER-SystemErrorReporting 1001 in the same session belong to that stop, as "
    "does a BlueScreen report whose time falls inside it; a report that falls in no fetched session is a stop of "
    "its own, with only what the report says, and a 41 before the first fetched start is a stop with no known "
    "start, unless the query's record bound is what cut the start off, in which case that session is left for a "
    "larger count. started_at is the start's StartTime, stopped_at is Windows' own estimate from the 6008's binary "
    "value, reported_at is when the report was filed, and last_record_before is the last System record before the "
    "returned start of a stop the 41 announced; without a returned start, no pre-start lookup is "
    "made because a record before the 41 may already be from the new boot. The dump is the file "
    "the 1001 names, else a .dmp the report attached, else "
    "the newest returned dump written between the stop and half an hour past the start or the report, because the file is "
    "written while the machine comes back; matched_by says which. Collection names each event-log query's "
    "outcome, bound and retained reach. Missing queries preserve surviving evidence; no_bugcheck_recorded is null when "
    "incomplete queries or missing Application retention cannot establish absence, and last_record_collection distinguishes a failed lookup "
    "from an observed empty one. Dump locations carry their own coverage; an unmatched reported path "
    "keeps its inventory outcome, without treating an unread location as an absent file. Each stop's "
    "dump_inventory_complete qualifies a missing or time-matched dump when any location was unreadable. "
    "A report-only stop is ordered by when Windows filed the report; the stop itself came earlier "
    "and may precede a requested moment."
)

CRASH_COVERAGE_BASIS = (
    "Each log's retained_from is its oldest observed record after the primary query. Reaches_moment "
    "requires an answered query and an enabled circular log with a retained record strictly before "
    "the query's UTC millisecond boundary, which may be less than a millisecond earlier than the "
    "supplied moment; a record at the boundary is insufficient. First_start.established means "
    "the oldest-first System query can identify the first returned start record as the first after "
    "the moment, or prove no start record was returned within an uncapped query. First_start.at "
    "is the record's TimeCreated; first_start.started_at is its reported StartTime and may precede "
    "the record by seconds. A metadata failure cannot "
    "erase returned records. Windows may have omitted events, and a report's filing time does not "
    "establish when its stop occurred."
)

FAULTS_BASIS = (
    "the positional properties mapped from the Windows event manifests used by this build; the exception "
    "code named from Microsoft's NTSTATUS reference; a live kernel event is one entry per report id, taken from "
    "that report's latest returned record, carrying the record ids returned for it. Application process facts "
    "require a supported provider GUID, event ID, version and property count. Start values are interpreted as "
    "FILETIME with the per-provider basis in process.source; each field retains its own validation status. "
    "Encoding resolution is not clock accuracy, and PID/start time does not certify a unique process identity. "
    "Time windows select Application records by filing time, not when the fault occurred. A report whose "
    "records straddle a window boundary is interpreted from the records the window query returned; "
    "any out-of-window records are flagged."
)

SUMMARY_BASIS = (
    "the decoded entries counted by kind; an application is its AppName, with the modules its crash records "
    "named and the first and last times those records carry; a live kernel event is counted by its code and "
    "bucket. Sorted by count, highest first."
)


# ---------------------------------------------------------------------------
# Reading one record
# ---------------------------------------------------------------------------


def named(properties: list[Any], names: tuple[str, ...]) -> dict[str, Any]:
    """The record's positional properties under the names the manifest gives them.

    A property the map does not reach keeps its position as its name, so a newer template that
    added a field at the end is still readable rather than silently renaming everything after it.
    """
    fields: dict[str, Any] = {name: properties[i] for i, name in enumerate(names) if i < len(properties)}
    fields.update({f"[{i}]": value for i, value in enumerate(properties) if i >= len(names)})
    return fields


def decode(record: dict[str, Any]) -> dict[str, Any]:
    """One record named: its kind, its properties under this build's names, and the bug check, the
    stop time or the exception where the record carries one."""
    provider = str(record.get("ProviderName") or "")
    event_id = _number(record.get("Id"))
    raw_properties = record.get("Properties")
    properties = raw_properties if isinstance(raw_properties, list) else []
    kind, names = KINDS.get((provider, event_id if event_id is not None else -1), ("unnamed record", ()))

    entry: dict[str, Any] = {"RecordId": record.get("RecordId")}
    if "Log" in record:
        entry["Log"] = record.get("Log")
    fields = named(properties, names)
    if provider == WER_REPORTING:
        kind = REPORT_KINDS.get(str(fields.get("EventName") or ""), "report")
    entry["kind"] = kind
    entry["fields"] = fields
    if (provider, event_id) in ((APPLICATION_ERROR, 1000), (APPLICATION_HANG, 1002)):
        entry["process"] = process_identity(record)

    if not isinstance(raw_properties, list):
        entry["error"] = "the record does not carry a property array"
    elif len(properties) < len(names):
        # The map did not fit, so every name after the gap would be wrong. The mismatch is the
        # finding; what can still be read from the record — a message the machine wrote in words —
        # is read below, because a record that does not fit is exactly when the text is worth having.
        entry["error"] = f"the record carries {len(properties)} properties where this build's manifest names {len(names)}"

    if (provider, event_id) == (KERNEL_POWER, 41):
        bugcheck = bugcheck_from_41(fields)
        if bugcheck:
            entry["bugcheck"] = bugcheck
    elif (provider, event_id) == (EVENTLOG, 6008):
        stopped = stop_times(properties)
        if stopped is None:
            entry.setdefault("error", "the record's binary value did not hold two SYSTEMTIME structures")
        else:
            fields["stopped_at"], fields["stopped_at_local"] = stopped
    elif (provider, event_id) == (WER_SYSTEM, 1001):
        bugcheck = bugcheck_from_1001(fields, str(record.get("Message") or ""))
        if bugcheck:
            entry["bugcheck"] = bugcheck
    elif provider == WER_REPORTING:
        report = report_facts(fields)
        if kind == "live kernel event":
            entry["report"] = {
                "id": report["id"],
                "code": report["code"],
                "name": report["name"],
                "parameters": report["parameters"],
                "bucket": report["bucket"],
                "dump_path": report["dumps"][0] if report["dumps"] else None,
                "records": [record.get("RecordId")],
            }
        elif kind == "bug check report" and report["code"]:
            entry["bugcheck"] = {"code": report["code"], "name": report["name"], "parameters": report["parameters"], "bucket": report["bucket"]}
    elif (provider, event_id) == (APPLICATION_ERROR, 1000):
        exception = exception_of(fields.get("ExceptionCode"))
        if exception:
            entry["exception"] = exception

    return entry


def bugcheck_from_41(fields: dict[str, Any]) -> dict[str, Any] | None:
    """The 41's own bug check. Code 0 is not a bug check: it is the machine saying it wrote none,
    which the stop carries as ``no_bugcheck_recorded`` rather than as a code of zero."""
    code = _number(fields.get("BugcheckCode"))
    if not code:
        return None
    parameters = [_code(fields.get(f"BugcheckParameter{i}")) for i in (1, 2, 3, 4)]
    return {"code": _code(code), "name": BUGCHECKS.get(code), "parameters": [p for p in parameters if p], "bucket": None}


_PARAM1 = re.compile(r"(0x[0-9a-fA-F]+)\s*\((.*)\)")
_MESSAGE_CODE = re.compile(r"The bugcheck was: (0x[0-9a-fA-F]+) \((.*?)\)")
# The sentence ends in a full stop the path does not own, and the path holds one of its own in
# MEMORY.DMP: the extension is what ends it, not the next period.
_MESSAGE_DUMP = re.compile(r"A dump was saved in:\s*(.+?\.dmp)", re.IGNORECASE)
_MESSAGE_REPORT = re.compile(r"Report Id:\s*([0-9a-zA-Z-]+)")


def bugcheck_from_1001(fields: dict[str, Any], message: str) -> dict[str, Any] | None:
    """The System log's bug check record, from its properties and, where they do not parse, from
    the message text. The property names are Microsoft's template rather than an observation here,
    so the text is a second reading of the same record, never a second record."""
    text = str(fields.get("param1") or "")
    match = _PARAM1.search(text) or _MESSAGE_CODE.search(message)
    if not match:
        return None
    code = _number(match.group(1))
    if code is None:
        return None
    parameters = [_code(part) for part in match.group(2).split(",")]
    return {"code": _code(code), "name": BUGCHECKS.get(code), "parameters": [p for p in parameters if p], "bucket": None}


def dump_from_1001(fields: dict[str, Any], message: str) -> str | None:
    path = str(fields.get("param2") or "").strip()
    if path:
        return path
    found = _MESSAGE_DUMP.search(message)
    return found.group(1).strip() if found else None


def report_id_of(fields: dict[str, Any], message: str = "") -> str | None:
    value = str(fields.get("param3") or fields.get("ReportId") or "").strip()
    if value:
        return value
    found = _MESSAGE_REPORT.search(message)
    return found.group(1) if found else None


def report_facts(fields: dict[str, Any]) -> dict[str, Any]:
    """What a Windows Error Reporting 1001 says about the failure: the code and its parameters as
    bare hex in P1 to P5, the bucket WER named once it had analysed the dump, and the dumps it
    attached."""
    code = _number(fields.get("P1"), base=16)
    parameters = [_code(fields.get(f"P{i}"), base=16) for i in (2, 3, 4, 5)]
    return {
        "id": report_id_of(fields),
        "code": _code(code) if code is not None else None,
        "name": BUGCHECKS.get(code) if code is not None else None,
        "parameters": [p for p in parameters if p],
        "bucket": str(fields.get("Bucket")).strip() if fields.get("Bucket") else None,
        "dumps": attached_dumps(fields.get("AttachedFiles")),
    }


def attached_dumps(attached: Any) -> list[str]:
    """The .dmp files a report attached, newline-separated and \\?\\-prefixed as WER writes them.
    The minidump comes first: it is the one that belongs to this stop, where MEMORY.DMP is
    overwritten by whichever stop was last."""
    paths = [_plain_path(line) for line in str(attached or "").splitlines() if line.strip().lower().endswith(".dmp")]
    return sorted(paths, key=lambda p: _file_name(p).lower() == "memory.dmp")


def exception_of(value: Any) -> dict[str, Any] | None:
    code = _number(value, base=16)
    if code is None:
        return None
    return {"code": _code(code), "name": EXCEPTIONS.get(code)}


# ---------------------------------------------------------------------------
# The 6008's binary value: two SYSTEMTIME structures, local then UTC
# ---------------------------------------------------------------------------

SYSTEMTIME_SIZE = 16
LOCAL_AT = 0
UTC_AT = 16
BINARY_PROPERTY = 7


def stop_times(properties: list[Any]) -> tuple[str, str] | None:
    """When the machine stopped, as the 6008 records it: the UTC stamp and the machine's own local
    reading of the same moment. The locale text in the record's first two properties says the same
    thing in the machine's language, with directional marks in it; this is the value that parses."""
    blob = properties[BINARY_PROPERTY] if len(properties) > BINARY_PROPERTY else None
    utc = systemtime(blob, UTC_AT)
    local = systemtime(blob, LOCAL_AT)
    if utc is None or local is None:
        return None
    return _iso(utc.replace(tzinfo=UTC)), local.isoformat(timespec="milliseconds")


def systemtime(blob: Any, offset: int) -> datetime | None:
    """One Win32 SYSTEMTIME at ``offset`` bytes into the record's binary value: eight little-endian
    uint16, year, month, day of week, day, hour, minute, second, millisecond."""
    raw = _hex_bytes(blob)
    if raw is None or len(raw) < offset + SYSTEMTIME_SIZE:
        return None
    year, month, _weekday, day, hour, minute, second, milliseconds = struct.unpack_from("<8H", raw, offset)
    try:
        return datetime(year, month, day, hour, minute, second, milliseconds * 1000)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# The queries
# ---------------------------------------------------------------------------

# The System log's stop-related records. Each provider carries its own ids, because the same
# number means different things under different providers.
SYSTEM_SELECTORS: tuple[tuple[str, tuple[int, ...]], ...] = (
    (KERNEL_GENERAL, (12, 13)),
    (KERNEL_POWER, (41,)),
    (EVENTLOG, (6008,)),
    (WER_SYSTEM, (1001,)),
)

FAULT_SELECTORS: tuple[tuple[str, int, str | None], ...] = (
    (APPLICATION_ERROR, 1000, None),
    (APPLICATION_HANG, 1002, None),
    (WER_REPORTING, 1001, LIVE_KERNEL),
)


def _selector(log: str, provider: str, ids: tuple[int, ...], clause: str, event_name: str | None = None) -> str:
    """One <Select>: the provider, its ids, the window, and — for a report — the kind of report it
    is, which the log's own index answers out of the record's EventData."""
    match = f"Provider[@Name='{provider}'] and ({' or '.join(f'EventID={i}' for i in ids)}){clause}"
    data = f" and EventData[Data[@Name='EventName']='{event_name}']" if event_name else ""
    return f"<Select Path='{log}'>*[System[{match}]{data}]</Select>"


def system_query(clause: str) -> str:
    selects = "".join(_selector("System", provider, ids, clause) for provider, ids in SYSTEM_SELECTORS)
    return f"<QueryList><Query Id='0' Path='System'>{selects}</Query></QueryList>"


def reports_query(clause: str) -> str:
    select = _selector("Application", WER_REPORTING, (1001,), clause, BLUE_SCREEN)
    return f"<QueryList><Query Id='0' Path='Application'>{select}</Query></QueryList>"


def faults_query(clause: str) -> str:
    selects = "".join(_selector("Application", provider, (event_id,), clause, name) for provider, event_id, name in FAULT_SELECTORS)
    return f"<QueryList><Query Id='0' Path='Application'>{selects}</Query></QueryList>"


CRASH_SCRIPT_TEMPLATE = r"""
{metadata_script}
$warnings = @()
function Get-CrashQueryFailure($failure) {
    if ($failure.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { return 'empty' }
    if ($failure.CategoryInfo.Category -eq 'PermissionDenied' -or $failure.Exception -is [System.UnauthorizedAccessException]) { return 'denied' }
    return 'failed'
}

$system = @()
$system_outcome = 'failed'
$system_error = $null
try {
    $q = @"
{system_query}
"@
    $system = @(Get-WinEvent -FilterXml ([xml]$q) -MaxEvents {system_max}{oldest} -ErrorAction Stop |
        {projection})
    $system_outcome = if ($system.Count) { 'ok' } else { 'empty' }
} catch {
    $system = @()
    $system_outcome = Get-CrashQueryFailure $_
    if ($system_outcome -ne 'empty') { $system_error = $_.Exception.Message }
}

# What Windows filed about the stop. The Application log keeps these months longer than the System
# log keeps the stop itself, so a report can outlive the session it belongs to.
$reports = @()
$reports_outcome = 'failed'
$reports_error = $null
try {
    $rq = @"
{reports_query}
"@
    $reports = @(Get-WinEvent -FilterXml ([xml]$rq) -MaxEvents {reports_max}{oldest} -ErrorAction Stop |
        {projection})
    $reports_outcome = if ($reports.Count) { 'ok' } else { 'empty' }
} catch {
    $reports = @()
    $reports_outcome = Get-CrashQueryFailure $_
    if ($reports_outcome -ne 'empty') { $reports_error = $_.Exception.Message }
}
$system_meta = Read-LogMetadata 'System'
$reports_meta = Read-LogMetadata 'Application'

$dump_inventory = $null
try { $dump_inventory = & { {dumps} } }
catch { $warnings += "The dump inventory did not read: $($_.Exception.Message)" }

# The last System record before each returned next start. A 41 without that start cannot anchor
# a pre-start lookup: the record before its announcement may already be from the new boot.
$before = @()
$before_collection = @()
$queried_anchors = @{}
$announced = @($system | Where-Object { $_.Id -eq 41 -and $_.ProviderName -eq '{kernel_power}' } | Sort-Object TimeCreated {anchor_sort} | Select-Object -First {anchors})
foreach ($stop in $announced) {
    $opened = @($system | Where-Object { $_.Id -eq 12 -and $_.ProviderName -eq '{kernel_general}' -and $_.TimeCreated -le $stop.TimeCreated } | Sort-Object TimeCreated -Descending | Select-Object -First 1)
    if ($opened.Count -eq 0) { continue }
    $at = $opened[0]
    $anchor = $at.RecordId
    if ($queried_anchors.ContainsKey($anchor)) { continue }
    $queried_anchors[$anchor] = $true
    $moment = $at.TimeCreated
    $previous = @()
    $previous_outcome = 'failed'
    $previous_error = $null
    try {
        $invariant = [Globalization.CultureInfo]::InvariantCulture
        $boundary = [datetimeoffset]::Parse($moment, $invariant)
        $boundaryTicks = $boundary.UtcTicks
        $xpathEnd = $boundary.UtcDateTime.AddMilliseconds(2).ToString('yyyy-MM-ddTHH:mm:ss.fffZ', $invariant)
        $bq = @"
<QueryList><Query Id='0' Path='System'><Select Path='System'>*[System[TimeCreated[@SystemTime&lt;'$xpathEnd']]]</Select></Query></QueryList>
"@
        $previous = @(Get-WinEvent -FilterXml ([xml]$bq) -ErrorAction Stop |
            Where-Object { $null -eq $_.TimeCreated -or $_.TimeCreated.ToUniversalTime().Ticks -lt $boundaryTicks } |
            Select-Object -First 1 |
            {before_projection})
        $previous_outcome = if ($previous.Count) { 'ok' } else { 'empty' }
    } catch {
        $previous = @()
        $previous_outcome = Get-CrashQueryFailure $_
        if ($previous_outcome -ne 'empty') { $previous_error = $_.Exception.Message }
    }
    $before += $previous
    $before_collection += [pscustomobject]@{
        anchor = $anchor; at = $moment; outcome = $previous_outcome
        returned = $previous.Count; error = $previous_error
    }
}

[pscustomobject]@{
    system   = $system
    reports  = $reports
    dump_inventory = $dump_inventory
    before   = $before
    warnings = $warnings
    collection = [pscustomobject]@{
        system = [pscustomobject]@{
            outcome = $system_outcome; returned = $system.Count; limit = {system_max}; error = $system_error
            bound_reached = $(if ($system_outcome -in @('ok', 'empty')) { $system.Count -eq {system_max} } else { $null })
            log = $system_meta.log; log_enabled = $system_meta.log_enabled; log_mode = $system_meta.log_mode
            log_state = $system_meta.log_state; log_error = $system_meta.log_error
            log_oldest = $system_meta.log_oldest; oldest_state = $system_meta.oldest_state; oldest_error = $system_meta.oldest_error
        }
        reports = [pscustomobject]@{
            outcome = $reports_outcome; returned = $reports.Count; limit = {reports_max}; error = $reports_error
            bound_reached = $(if ($reports_outcome -in @('ok', 'empty')) { $reports.Count -eq {reports_max} } else { $null })
            log = $reports_meta.log; log_enabled = $reports_meta.log_enabled; log_mode = $reports_meta.log_mode
            log_state = $reports_meta.log_state; log_error = $reports_meta.log_error
            log_oldest = $reports_meta.log_oldest; oldest_state = $reports_meta.oldest_state; oldest_error = $reports_meta.oldest_error
        }
        before = $before_collection
    }
}
"""


def crash_script(count: int, moment: str | None) -> str:
    """One launch: the System log's stop records, the reports, the dump inventory and the last
    record before each start. The dump inventory is the ``dumps`` reading's own query, embedded
    rather than written again, so both readings see one inventory. With a moment, both logs are
    read forward from it; the clause is the same one every windowed reading uses."""
    clause = since_clause(moment)[1] if moment else ""
    return (
        CRASH_SCRIPT_TEMPLATE.replace("{metadata_script}", LOG_METADATA_SCRIPT)
        .replace("{system_query}", system_query(clause))
        .replace("{reports_query}", reports_query(clause))
        .replace("{system_max}", str(record_cap(count, moment)))
        .replace("{reports_max}", str(3 * count + 6))
        .replace("{oldest}", " -Oldest" if moment else "")
        .replace("{anchor_sort}", "" if moment else "-Descending")
        .replace("{anchors}", str(count))
        .replace("{kernel_power}", KERNEL_POWER)
        .replace("{kernel_general}", KERNEL_GENERAL)
        .replace("{before_projection}", record_projection("Log = $_.LogName; Anchor = $anchor"))
        .replace("{projection}", record_projection("Log = $_.LogName"))
        .replace("{dumps}", DUMPS_SCRIPT.strip())
    )


def record_cap(count: int, moment: str | None) -> int:
    """How many System records one take pulls out of the log. A clean session costs records too, so
    a stop is worth about a dozen of them."""
    return MOMENT_RECORDS if moment else 12 * count + 24


def faults_script(count: int, since: str, before: str = "") -> str:
    prelude, clause, start, end, from_ticks, until_ticks = window_clauses(since, before)
    return log_records_script("Application", faults_query(clause), count, prelude=prelude, window_start=start, window_end=end,
                              projection=record_projection("Log = $_.LogName"), from_ticks=from_ticks, until_ticks=until_ticks)


# ---------------------------------------------------------------------------
# The rule: sessions, stops, and the dump that belongs to each
# ---------------------------------------------------------------------------


def sessions(system: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The fetched records split into the sessions they were written in, oldest first. A session
    begins at a Kernel-General 12; records before the first one belong to a session whose start is
    beyond the log's retention."""
    ordered = sorted(system, key=lambda r: _sort_key(r.get("TimeCreated")))
    out: list[dict[str, Any]] = []
    current: dict[str, Any] = {"start": None, "records": []}
    for record in ordered:
        if _is(record, KERNEL_GENERAL, 12):
            if current["start"] is not None or current["records"]:
                out.append(current)
            current = {"start": record, "records": []}
        current["records"].append(record)
    if current["start"] is not None or current["records"]:
        out.append(current)
    for session in out:
        start = session["start"]
        session["begins_at"] = _parse(start.get("TimeCreated") if start else session["records"][0].get("TimeCreated"))
    return out


def report_groups(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One report per ReportId. WER writes the same report two or three times as it analyses the
    dump; the latest record is the one that carries the bucket, and every record id is kept so the
    evidence can be stacked."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in reports:
        fields = named(list(record.get("Properties") or []), WER_REPORT)
        key = str(report_id_of(fields, str(record.get("Message") or "")) or f"record {record.get('RecordId')}")
        groups.setdefault(key, []).append(record)
    out = []
    for key, records in groups.items():
        records.sort(key=lambda r: _sort_key(r.get("TimeCreated")))
        latest = records[-1]
        out.append(
            {
                "id": key,
                "at": _parse(records[0].get("TimeCreated")),
                "record_ids": [r.get("RecordId") for r in records],
                "facts": report_facts(named(list(latest.get("Properties") or []), WER_REPORT)),
            }
        )
    out.sort(key=lambda g: (g["at"] or datetime.min.replace(tzinfo=UTC)))
    return out


def compose(payload: dict[str, Any], count: int, moment: str | None) -> dict[str, Any]:
    """The records as fetched, each one decoded, and the stops the rule composes from them."""
    boundary = _utc_stamp(moment) if moment else None
    coverage = payload.get("collection")
    coverage = coverage if isinstance(coverage, dict) else {}
    collection: dict[str, Any] = {}
    collection["system"], system = _query_result(coverage.get("system"), payload.get("system"), record_cap(count, moment))
    collection["reports"], reports = _query_result(coverage.get("reports"), payload.get("reports"), 3 * count + 6)
    for name, expected in (("system", "System"), ("reports", "Application")):
        supplied = coverage.get(name)
        source = supplied if isinstance(supplied, dict) and supplied.get("log") == expected else {}
        collection[name].update(log_metadata(source))
        collection[name]["log"] = expected
        if not source:
            problem = "the log metadata did not identify the expected source"
            collection[name].update(log_state="failed", oldest_state="failed", log_error=problem, oldest_error=problem)
    dumps, collection["dumps"], dump_warnings = inventory(payload.get("dump_inventory"))
    before: dict[Any, dict[str, Any]] = {}
    before_collection: dict[Any, dict[str, Any]] = {}
    before_rows = payload.get("before")
    before_rows = before_rows if isinstance(before_rows, list) else []
    attempts = coverage.get("before")
    for attempt in attempts if isinstance(attempts, list) else []:
        if not isinstance(attempt, dict) or type(attempt.get("anchor")) is not int:
            continue
        anchor = attempt["anchor"]
        rows = [r for r in before_rows if isinstance(r, dict) and r.get("Anchor") == anchor]
        source, rows = _query_result(attempt, rows)
        if anchor in before_collection:
            source, rows = {"outcome": "failed", "returned": 0, "error": "the collector returned duplicate lookup outcomes for this anchor"}, []
        before.pop(anchor, None)
        source.update(anchor=anchor, at=attempt.get("at"))
        row_at = stamp_key(rows[0].get("TimeCreated")) if rows else None
        anchor_at = stamp_key(attempt.get("at"))
        if rows and (row_at is None or anchor_at is None or row_at >= anchor_at):
            source, rows = {"outcome": "failed", "returned": 0, "error": "the record before this start was not strictly earlier",
                            "anchor": anchor, "at": attempt.get("at")}, []
        before_collection[anchor] = source
        if rows:
            before[anchor] = rows[0]
    collection["before"] = list(before_collection.values())
    records = system + reports
    warnings = [f"{label} did not answer: {collection[name]['error']}" for name, label in (("system", "System stop records"), ("reports", "Application bug check reports")) if not _observed(collection[name])]
    warnings.extend(dump_warnings)
    warnings.extend(f"The record before start {source['anchor']} did not answer: {source['error']}" for source in collection["before"] if not _observed(source))

    found = sessions(system)
    groups = report_groups(reports)

    # The query is bounded, newest first, so when the bound bit the oldest fetched records are a
    # session cut by the bound, not by the log's retention: its start was simply not fetched. Such
    # a session is left out rather than reported as a stop with no known start, and a report older
    # than everything fetched is left out rather than reported as a stop the log lost; both are one
    # larger count away, and the warning below says when the bound bit.
    capped = collection["system"].get("bound_reached") is True
    if capped and not moment and found and found[0]["start"] is None:
        found = found[1:]

    # A report belongs to the session its time falls in; one that falls before everything the
    # System log still holds is a stop of its own, with only what the report says.
    unplaced = list(groups)
    system_end = max((_sort_key(r.get("TimeCreated")) for r in system), default=None)
    for index, session in enumerate(found):
        nxt = found[index + 1]["begins_at"] if index + 1 < len(found) else None
        # An oldest-first query that reached its cap cannot establish an open-ended final
        # session. A later report may belong to a start that this query never reached.
        open_end = not (moment and capped and nxt is None)
        session["reports"] = [g for g in unplaced if _within(g["at"], session["begins_at"], nxt) and (open_end or g["at"] <= system_end)]
        unplaced = [g for g in unplaced if g not in session["reports"]]
    if capped and not moment:
        unplaced = []
    complete = all(_observed(collection[name]) and not collection[name]["bound_reached"] for name in ("system", "reports"))
    in_session = [
        dict(_stop(session, dumps, before, before_collection,
                   complete and _reaches(collection["reports"], _iso(session["begins_at"])) is True), _session=index)
        for index, session in enumerate(found) if _is_stop(session)
    ]
    orphans = [_orphan_stop(group, dumps) for group in unplaced]
    if moment and capped and orphans:
        warnings.append("Some bug check reports fall outside the returned System window; their session association is unknown.")

    moment_coverage: dict[str, Any] = {
        name: {
            "retained_from": collection[name].get("log_oldest") if collection[name].get("oldest_state") == "ok" and stamp_key(collection[name].get("log_oldest")) else None,
            "reaches_moment": _reaches(collection[name], boundary) if moment else None,
        }
        for name in ("system", "reports")
    }
    moment_coverage["first_start"] = None
    if moment:
        stops, moment_warnings, moment_coverage["first_start"] = _from_moment(found, in_session, orphans, count, moment, boundary, collection)
        warnings.extend(moment_warnings)
        if moment_coverage["reports"]["reaches_moment"] is False:
            warnings.append("Application bug check reports begin after the requested moment; earlier reports may be outside retention.")
        elif moment_coverage["reports"]["reaches_moment"] is None and _observed(collection["reports"]):
            warnings.append("Application bug check report retention could not be established for the requested moment.")
    else:
        stops = sorted(in_session + orphans, key=lambda s: _sort_key(s["_at"]), reverse=True)[:count]
    if capped and len(stops) < count:
        warnings.append(f"the query's record bound was reached after {len(stops)} stops; ask for fewer, or take `events` over the window")
    if collection["reports"].get("bound_reached"):
        warnings.append("the bug check report bound was reached; older or later reports may be outside this reading")

    for stop in stops:
        stop["dump_inventory_complete"] = collection["dumps"]["complete"]
        dump = stop["dump"]
        if dump and dump["bytes"] is None:
            if any(_path_key(file["path"]) == _path_key(dump["path"]) for file in dumps):
                outcome, detail = "ok", "A file with this path is inventoried, but was not matched to this stop."
            else:
                outcome, detail = missing_file(dump["path"], collection["dumps"])
            dump["inventory"] = {"outcome": outcome, "detail": detail}
        stop.pop("_at", None)
        stop.pop("_session", None)
    return {"records": records, "decoded": [decode(r) for r in records], "stops": stops, "warnings": warnings,
            "collection": collection, "coverage": moment_coverage}


def _observed(source: dict[str, Any]) -> bool:
    return source["outcome"] in ("ok", "empty")


def _reaches(source: dict[str, Any], at: str | None) -> bool | None:
    """A strict retained boundary proves reach only for an observed, enabled circular log."""
    oldest, target = stamp_key(source.get("log_oldest")), stamp_key(at)
    if not _observed(source) or source.get("oldest_state") != "ok" or oldest is None or target is None:
        return None
    if oldest >= target:
        return False
    if source.get("log_state") == "ok" and source.get("log_enabled") is True and source.get("log_mode") == "Circular":
        return True
    return None


def _query_result(value: Any, rows: Any, limit: int | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Accept rows only with a matching completed query result; a cap does not reveal a total."""
    source: dict[str, Any] = {"outcome": "failed", "returned": 0, "error": "the collector did not return a valid query outcome"}
    if limit is not None:
        source.update(limit=limit, bound_reached=None)
    if not isinstance(value, dict):
        return source, []
    if value.get("outcome") in ("failed", "denied"):
        source.update(outcome=value["outcome"], error=value.get("error") or "the query did not answer")
        return source, []
    if value.get("outcome") in ("ok", "empty"):
        returned = value.get("returned")
        valid_rows = isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
        valid_count = type(returned) is int and 0 <= returned <= (limit or 1)
        valid_bound = limit is None or value.get("limit") == limit and type(value.get("bound_reached")) is bool and value["bound_reached"] == (returned == limit)
        if valid_rows and valid_count and valid_bound and returned == len(rows) and (value["outcome"] == "empty") == (returned == 0):
            source.update(outcome=value["outcome"], returned=returned, error=None)
            if limit is not None:
                source["bound_reached"] = returned == limit
            return source, rows
        source["error"] = "the collector's query outcome and returned rows disagree"
    return source, []


def _is_stop(session: dict[str, Any]) -> bool:
    return _find(session["records"], KERNEL_POWER, 41) is not None or bool(session.get("reports"))


def _start_time(session: dict[str, Any]) -> str | None:
    start = session.get("start")
    return _iso(_field(start, KERNEL_GENERAL_12, "StartTime") or start.get("TimeCreated")) if start else None


def _from_moment(found: list[dict[str, Any]], stops: list[dict[str, Any]], orphans: list[dict[str, Any]], count: int, moment: str, boundary: str | None, collection: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Only a retained System window can name the first start after a requested moment."""
    opened = next((index for index, session in enumerate(found) if session["start"] is not None), None)
    reach = _reaches(collection["system"], boundary)
    first = reach is True and (opened is not None or not collection["system"]["bound_reached"])
    start = found[opened] if opened is not None else None
    first_start = {"at": _iso(start["begins_at"]) if start else None,
                   "started_at": _start_time(start) if start else None,
                   "record_id": start["start"].get("RecordId") if start else None, "established": first}
    if not first:
        available = sorted(stops + orphans, key=lambda stop: _sort_key(stop["_at"]))[:count]
        if reach is False:
            warning = f"System log retention begins after {moment}; the first start cannot be established, and returned stops retain only their available evidence"
        else:
            warning = f"the first start after {moment} could not be established from the returned System records; returned stops retain only their available evidence"
        return available, [warning], first_start
    if opened is None:
        return [], [f"no start follows {moment}; nothing after it announced a stop"], first_start
    available = sorted(
        [stop for stop in stops if stop["_session"] >= opened]
        + [stop for stop in orphans if _sort_key(stop["_at"]) >= found[opened]["begins_at"]],
        key=lambda stop: _sort_key(stop["_at"]),
    )[:count]
    if not _is_stop(found[opened]):
        started = first_start["at"]
        if any(not _observed(collection[name]) or collection[name]["bound_reached"] for name in ("system", "reports")) or _reaches(collection["reports"], first_start["started_at"] or started) is not True:
            return available, [f"the first start after {moment}, at {started}, could not be classified because the stop queries or Application retention are incomplete; later returned stops remain available"], first_start
        return [], [f"the first start after {moment}, at {started}, announced no unplanned stop: no Kernel-Power 41 and no bug check report in that session"], first_start
    return available, [], first_start


def _stop(session: dict[str, Any], dumps: list[dict[str, Any]], before: dict[Any, dict[str, Any]], before_collection: dict[Any, dict[str, Any]], complete: bool) -> dict[str, Any]:
    records = session["records"]
    start = session["start"]
    power = _find(records, KERNEL_POWER, 41)
    shutdown = _find(records, EVENTLOG, 6008)
    wer = _find(records, WER_SYSTEM, 1001)
    groups = session.get("reports") or []
    latest = groups[-1] if groups else None

    started_at = _start_time(session)
    announced_at = _iso((power or {}).get("TimeCreated")) if power else None
    stopped = stop_times(list((shutdown or {}).get("Properties") or [])) if shutdown else None
    stopped_at = stopped[0] if stopped else None

    bugcheck = _bugcheck(power, wer, latest)
    last_record = before.get(start.get("RecordId")) if start else None
    at = started_at or announced_at
    reported_at = _iso(groups[0]["at"]) if groups else None
    no_bugcheck = bool(power) and not bugcheck and _number(_field(power, KERNEL_POWER_41, "BugcheckCode")) == 0

    return {
        "_at": at or stopped_at or reported_at,
        "started_at": started_at,
        "announced_at": announced_at,
        "stopped_at": stopped_at,
        "reported_at": reported_at,
        "down_seconds": _seconds(stopped_at, started_at),
        "bugcheck": bugcheck,
        "no_bugcheck_recorded": None if no_bugcheck and not complete else no_bugcheck,
        "power": _power_facts(power),
        "dump": _dump(wer, latest, dumps, stopped_at, started_at, reported_at),
        "last_record_before": _last_record(last_record),
        "last_record_collection": before_collection.get(start.get("RecordId") if start else None, {
            "outcome": "not_returned" if start else "not_requested", "returned": 0,
            "error": "No lookup result was returned for this stop." if start else "The next start was not returned, so no pre-start record was requested.",
        }),
        "quiet_seconds": _seconds((last_record or {}).get("TimeCreated"), at) if last_record else None,
        "records": {
            "start": (start or {}).get("RecordId"),
            "power_41": (power or {}).get("RecordId"),
            "eventlog_6008": (shutdown or {}).get("RecordId"),
            "wer_1001": (wer or {}).get("RecordId"),
            "report": [record_id for group in groups for record_id in group["record_ids"]],
        },
    }


def _orphan_stop(group: dict[str, Any], dumps: list[dict[str, Any]]) -> dict[str, Any]:
    """A report without a returned session; query failure and retention are different reasons."""
    facts = group["facts"]
    return {
        "_at": _iso(group["at"]),
        "started_at": None,
        "announced_at": None,
        "stopped_at": None,
        "reported_at": _iso(group["at"]),
        "down_seconds": None,
        "bugcheck": _bugcheck(None, None, group),
        "no_bugcheck_recorded": False,
        "power": None,
        "dump": _dump_from_report(facts, dumps, _iso(group["at"])),
        "last_record_before": None,
        "last_record_collection": {"outcome": "not_requested", "returned": 0, "error": None},
        "quiet_seconds": None,
        "records": {"start": None, "power_41": None, "eventlog_6008": None, "wer_1001": None, "report": group["record_ids"]},
    }


def _bugcheck(power: dict[str, Any] | None, wer: dict[str, Any] | None, group: dict[str, Any] | None) -> dict[str, Any] | None:
    """The code the machine wrote down, from the record closest to the stop itself: the 41 when it
    carries one, else the bug check record, else the report. ``source`` names which record it came
    from. The bucket only ever comes from the report, because only WER names the failing module."""
    bucket = ((group or {}).get("facts") or {}).get("bucket")
    for source, candidate in (
        ("Kernel-Power 41", bugcheck_from_41(named(list((power or {}).get("Properties") or []), KERNEL_POWER_41)) if power else None),
        ("WER-SystemErrorReporting 1001", bugcheck_from_1001(named(list((wer or {}).get("Properties") or []), WER_SYSTEM_1001), str((wer or {}).get("Message") or "")) if wer else None),
        ("BlueScreen report", _report_bugcheck(group)),
    ):
        if candidate:
            return {**candidate, "source": source, "bucket": bucket or candidate.get("bucket")}
    return None


def _report_bugcheck(group: dict[str, Any] | None) -> dict[str, Any] | None:
    facts = (group or {}).get("facts") or {}
    if not facts.get("code"):
        return None
    return {"code": facts["code"], "name": facts["name"], "parameters": facts["parameters"], "bucket": facts["bucket"]}


def _power_facts(power: dict[str, Any] | None) -> dict[str, Any] | None:
    if not power:
        return None
    fields = named(list(power.get("Properties") or []), KERNEL_POWER_41)
    return {
        "sleep_in_progress": fields.get("SleepInProgress"),
        "power_button_timestamp": fields.get("PowerButtonTimestamp"),
        "whea_boot_error_count": fields.get("WHEABootErrorCount"),
        "boot_app_status": fields.get("BootAppStatus"),
        "checkpoint": fields.get("Checkpoint"),
    }


def _dump(wer: dict[str, Any] | None, group: dict[str, Any] | None, dumps: list[dict[str, Any]], stopped_at: str | None, started_at: str | None, reported_at: str | None) -> dict[str, Any] | None:
    """The file this stop wrote, by the strongest claim available: the bug check record names it,
    else the report attached it, else it is the dump written while the machine came back."""
    if wer:
        path = dump_from_1001(named(list(wer.get("Properties") or []), WER_SYSTEM_1001), str(wer.get("Message") or ""))
        if path:
            return _dump_entry(path, dumps, "1001")
    from_report = _dump_from_report((group or {}).get("facts") or {}, dumps, reported_at or started_at)
    if from_report:
        return from_report
    return _dump_by_time(dumps, stopped_at, started_at, reported_at)


# A dump is not on disk when the machine stops: the kernel writes it into the pagefile, and the
# file appears while the machine comes back, minutes after the start, just before the report is
# filed — on this machine the April minidump's modified time is five seconds before its report.
# So the window a dump can belong to a stop in runs from the stop to a margin past the start, or
# past the report when there is one. MEMORY.DMP is one file every later crash overwrites, so the
# copy on disk belongs to a stop only when it was written in the day before that window closes.
WHOLE_DUMP = "memory.dmp"
WHOLE_DUMP_WINDOW = timedelta(hours=24)
DUMP_WRITE_MARGIN = timedelta(minutes=30)


def _dump_from_report(facts: dict[str, Any], dumps: list[dict[str, Any]], at: Any = None) -> dict[str, Any] | None:
    """The file the report attached, if the disk still holds it and, for the whole memory dump,
    if the one on disk can still be this stop's. Otherwise the path the report named, on its own:
    the report is the evidence that the file existed, and the disk no longer having it is a
    different fact from no dump at all."""
    paths = facts.get("dumps") or []
    if not paths:
        return None
    index = {_path_key(f.get("path")): f for f in dumps}
    end = _parse(at) + DUMP_WRITE_MARGIN if _parse(at) else None
    for path in paths:
        held = index.get(_path_key(path))
        if held is None:
            continue
        if _file_name(path).lower() == WHOLE_DUMP:
            written = _parse(held.get("modified"))
            if end is None or written is None or not (end - WHOLE_DUMP_WINDOW <= written <= end):
                continue
        return _dump_entry(path, dumps, "report")
    named_only = _plain_path(paths[0])
    return {"name": _file_name(named_only), "path": named_only, "bytes": None, "modified": None, "matched_by": "report"}


def _dump_entry(path: str, dumps: list[dict[str, Any]], matched_by: str) -> dict[str, Any]:
    """A matched file or the reported path alone; collection coverage qualifies an unmatched path."""
    found = {_path_key(f.get("path")): f for f in dumps}.get(_path_key(path))
    if found:
        return {"name": found.get("name"), "path": found.get("path"), "bytes": found.get("bytes"), "modified": found.get("modified"), "matched_by": matched_by}
    plain = _plain_path(path)
    return {"name": _file_name(plain), "path": plain, "bytes": None, "modified": None, "matched_by": matched_by}


def _dump_by_time(dumps: list[dict[str, Any]], stopped_at: str | None, started_at: str | None, reported_at: str | None) -> dict[str, Any] | None:
    close = _parse(reported_at) or _parse(started_at)
    if close is None:
        return None
    end = close + DUMP_WRITE_MARGIN
    begin = (_parse(stopped_at) - timedelta(minutes=1)) if _parse(stopped_at) else _parse(started_at) - timedelta(hours=1) if _parse(started_at) else close - WHOLE_DUMP_WINDOW
    candidates = [(m, f) for f in dumps if (m := _parse(f.get("modified"))) and begin <= m <= end]
    if not candidates:
        return None
    newest = max(candidates, key=lambda pair: pair[0])[1]
    return {"name": newest.get("name"), "path": newest.get("path"), "bytes": newest.get("bytes"), "modified": newest.get("modified"), "matched_by": "time"}


def _last_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    return {
        "RecordId": record.get("RecordId"),
        "TimeCreated": record.get("TimeCreated"),
        "ProviderName": record.get("ProviderName"),
        "Id": record.get("Id"),
        "LevelDisplayName": record.get("LevelDisplayName"),
        "Message": _first_line(record.get("Message")),
    }


# ---------------------------------------------------------------------------
# faults
# ---------------------------------------------------------------------------


def decode_faults(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per record, except a live kernel event, which is one entry per report: WER writes
    the same report more than once, and three entries for one GPU timeout would read as three."""
    entries = [(record, decode(record)) for record in records]
    latest: dict[str, dict[str, Any]] = {}
    ids: dict[str, list[Any]] = {}
    for record, entry in entries:
        if not entry.get("report"):
            continue
        key = _report_key(record, entry)
        ids.setdefault(key, []).append(record.get("RecordId"))
        held = latest.get(key)
        if held is None or _sort_key(record.get("TimeCreated")) >= _sort_key(held["at"]):
            latest[key] = {"at": record.get("TimeCreated"), "RecordId": record.get("RecordId")}

    out: list[dict[str, Any]] = []
    for record, entry in entries:
        if not entry.get("report"):
            out.append(entry)
            continue
        key = _report_key(record, entry)
        if latest[key]["RecordId"] != record.get("RecordId"):
            continue
        entry["report"]["records"] = ids[key]
        out.append(entry)
    return out


def _report_key(record: dict[str, Any], entry: dict[str, Any]) -> str:
    """A report without an id is its own report, not everyone else's."""
    return str(entry["report"]["id"] or f"record {record.get('RecordId')}")


def faults_summary(records: list[dict[str, Any]], decoded: list[dict[str, Any]]) -> dict[str, Any]:
    times = {r.get("RecordId"): r.get("TimeCreated") for r in records}
    applications: dict[str, dict[str, Any]] = {}
    live: dict[tuple[str | None, str | None], dict[str, Any]] = {}

    for entry in decoded:
        at = times.get(entry["RecordId"])
        fields = entry.get("fields") or {}
        if entry["kind"] in ("application crash", "application hang"):
            name = str(fields.get("AppName") or fields.get("ExeFileName") or "unnamed")
            app = applications.setdefault(name, {"name": name, "count": 0, "first": at, "last": at, "modules": []})
            app["count"] += 1
            app["first"] = _earlier(app["first"], at)
            app["last"] = _later(app["last"], at)
            module = str(fields.get("ModuleName") or "").strip()
            if module and module not in app["modules"]:
                app["modules"].append(module)
        elif entry["kind"] == "live kernel event":
            report = entry["report"]
            key = (report["code"], report["bucket"])
            seen = live.setdefault(key, {"code": report["code"], "name": report.get("name"), "bucket": report["bucket"], "count": 0, "last": at})
            seen["count"] += 1
            seen["last"] = _later(seen["last"], at)

    for app in applications.values():
        app["modules"].sort()
    return {
        "by_kind": dict(sorted(Counter(entry["kind"] for entry in decoded).items(), key=lambda kv: (-kv[1], kv[0]))),
        "applications": sorted(applications.values(), key=lambda a: (-a["count"], a["name"])),
        "live_kernel": sorted(live.values(), key=lambda e: (-e["count"], str(e["code"]))),
    }


# ---------------------------------------------------------------------------
# The takers
# ---------------------------------------------------------------------------


def take_crash(bridge: Bridge, params: dict[str, Any]) -> Reading:
    count = int(params["count"])
    if not 1 <= count <= MAX_STOPS:
        raise ValueError(f"parameter 'count': must be between 1 and {MAX_STOPS}")
    moment = str(params.get("moment") or "").strip()
    if moment:
        try:
            _utc_stamp(moment)  # a moment is a timestamp; the word "boot" is a window, not a moment
        except ValueError as exc:
            raise ValueError(f"parameter 'moment': not an ISO timestamp ({exc})") from exc

    script = crash_script(count, moment or None)
    result = bridge.run(script, depth=DEPTH)
    composed: dict[str, Any] = {}

    def build(payload: dict[str, Any]) -> list[Section]:
        composed.update(compose(payload, count, moment or None))
        return [
            Section("records", "raw", composed["records"]),
            Section("decoded", "derived", composed["decoded"], basis=DECODED_BASIS),
            Section("stops", "derived", composed["stops"], basis=STOPS_BASIS),
            Section("collection", "raw", composed["collection"]),
            Section("coverage", "derived", composed["coverage"], basis=CRASH_COVERAGE_BASIS),
        ]

    reading = from_object("crash", params, script, result, build)
    # The record before each start is fetched inside the same script, from the starts it has just
    # read, so the whole reading is one process however many stops it names.
    reading.method["launches"] = 1
    if reading.observed:
        stops = composed.get("stops") or []
        reading.count = len(stops)
        reading.warnings.extend(composed.get("warnings") or [])
        failures = [composed["collection"][name] for name in ("system", "reports") if not _observed(composed["collection"][name])]
        if stops:
            reading.outcome = "ok"
        elif failures:
            reading.outcome = "denied" if all(source["outcome"] == "denied" for source in failures) else "failed"
            reading.count = None
            reading.error = {"kind": reading.outcome, "detail": "No stop could be established because a primary event-log query did not answer."}
        else:
            reading.outcome = "empty"  # Both primary sources answered within the stated bounds.
    return reading


def take_faults(bridge: Bridge, params: dict[str, Any]) -> Reading:
    count = int(params["count"])
    if not 1 <= count <= MAX_FAULTS:
        raise ValueError(f"parameter 'count': must be between 1 and {MAX_FAULTS}")
    script = faults_script(count, str(params.get("since") or ""), str(params.get("before") or ""))

    has_since = bool(str(params.get("since") or "").strip())
    before = str(params.get("before") or "").strip()
    reading = from_log_collector("faults", params, script, bridge.run(script, depth=8), "Application", count, window=has_since,
                                 before=exact_stamp(before, "before")[0] if before and not has_since else None)
    if not reading.observed:
        return reading
    record_section = reading.section("records")
    assert record_section is not None
    records = record_section.data
    decoded = decode_faults(records)
    reading.sections.append(Section("decoded", "derived", decoded, basis=FAULTS_BASIS))
    reading.sections.append(Section("summary", "derived", faults_summary(records, decoded), basis=SUMMARY_BASIS))
    return reading


# ---------------------------------------------------------------------------
# Arithmetic and small readings
# ---------------------------------------------------------------------------


def _is(record: dict[str, Any], provider: str, event_id: int) -> bool:
    return str(record.get("ProviderName") or "") == provider and _number(record.get("Id")) == event_id


def _find(records: list[dict[str, Any]], provider: str, event_id: int) -> dict[str, Any] | None:
    return next((r for r in records if _is(r, provider, event_id)), None)


def _field(record: dict[str, Any] | None, names: tuple[str, ...], name: str) -> Any:
    if not record:
        return None
    return named(list(record.get("Properties") or []), names).get(name)


def _within(at: datetime | None, begins: datetime | None, ends: datetime | None) -> bool:
    if at is None or begins is None:
        return False
    return at >= begins and (ends is None or at < ends)


def _number(value: Any, base: int = 10) -> int | None:
    """A code or a parameter as an integer, from a number or from the bare hex a report writes."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    if text.lower().startswith("0x"):
        text, base = text[2:], 16
    try:
        return int(text, base)
    except ValueError:
        return None


def _code(value: Any, base: int = 10) -> str | None:
    """Lower-case hex without leading zeros. A parameter that arrived as a signed 64-bit number is
    the same address written the other way round, so it is read back as unsigned."""
    number = _number(value, base)
    if number is None:
        return None
    return f"0x{number & 0xFFFFFFFFFFFFFFFF:x}" if number < 0 else f"0x{number:x}"


def _hex_bytes(value: Any) -> bytes | None:
    text = re.sub(r"[^0-9A-Fa-f]", "", str(value or ""))
    if not text or len(text) % 2:
        return None
    try:
        return bytes.fromhex(text)
    except ValueError:
        return None


_JSON_DATE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d{4})?\)/$")


def _parse(stamp: Any) -> datetime | None:
    """A moment from an ISO stamp, or from the ``/Date(milliseconds)/`` form ConvertTo-Json gives a
    DateTime property: Kernel-General 12's ``StartTime`` arrives that way, and a start whose time
    could not be read would leave every stop without one."""
    text = str(stamp or "").strip()
    if not text:
        return None
    json_date = _JSON_DATE.match(text)
    if json_date:
        return datetime.fromtimestamp(int(json_date.group(1)) / 1000, UTC)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _iso(value: Any) -> str | None:
    moment = value if isinstance(value, datetime) else _parse(value)
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _seconds(start: Any, end: Any) -> int | None:
    first, last = _parse(start), _parse(end)
    if first is None or last is None:
        return None
    return int((last - first).total_seconds())


def _sort_key(stamp: Any) -> datetime:
    return _parse(stamp) or datetime.min.replace(tzinfo=UTC)


def _earlier(held: Any, other: Any) -> Any:
    if held is None or other is None:
        return held if other is None else other
    return other if _sort_key(other) < _sort_key(held) else held


def _later(held: Any, other: Any) -> Any:
    if held is None or other is None:
        return held if other is None else other
    return other if _sort_key(other) > _sort_key(held) else held


def _plain_path(path: Any) -> str:
    return re.sub(r"^\\\\\?\\", "", str(path or "").strip())


def _path_key(path: Any) -> str:
    return _plain_path(path).replace("/", "\\").lower()


def _file_name(path: Any) -> str:
    return _plain_path(path).replace("/", "\\").rsplit("\\", 1)[-1]


def _first_line(message: Any) -> str | None:
    text = str(message or "").strip()
    return text.splitlines()[0].strip() if text else None


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------

register(
    Spec(
        name="crash",
        description=(
            "The stops this machine did not plan, newest first, each one named: when it stopped as Windows "
            "estimated it, when it started again, the bug check if one was recorded, the dump that belongs to it, "
            "and the last System record before the next start, which may be later than the stop estimate. "
            "Give it a moment and it reports what the first start at or after that moment announced "
            "when System retention establishes that start. Otherwise it keeps returned stop and report "
            "evidence with a warning: the log does not announce a freeze, the next start does."
        ),
        classes=("raw", "derived"),
        take=take_crash,
        params=(
            Param("count", "int", 5, f"How many stops, newest first; 1 to {MAX_STOPS}.", minimum=1, maximum=MAX_STOPS),
            Param("moment", "str", "", "ISO timestamp of a freeze someone remembers; the first start at or after it is reported when retention establishes it. Empty for the most recent stops."),
        ),
        private=("MachineName", "AttachedFiles", "dump paths", "user names inside Message", "profile paths inside Message"),
    )
)

register(
    Spec(
        name="faults",
        description=(
            "Application-log reports of programs that crashed or hung and Windows Error Reporting entries for "
            "live kernel events, by filing time. Returned records name the application, module and exception "
            "where supported; process ID and creation time carry per-field validity. Retention reach refers "
            "only to the Application log, not every live kernel event that occurred."
        ),
        classes=("raw", "derived"),
        take=take_faults,
        params=(
            Param("count", "int", 30, f"How many of the most recent records; 1 to {MAX_FAULTS}.", minimum=1, maximum=MAX_FAULTS),
            Param("since", "str", "", "Inclusive ISO timestamp, preserving up to seven fractional digits, or 'boot' for Windows' reported kernel-session start. Empty for the most recent records."),
            Param("before", "str", "", "Exclusive filing-time end with Z or an offset, preserving up to seven fractional digits. Pair with since for an anchored window; empty uses the query time."),
        ),
        private=("AppPath", "ModulePath", "ExeFileName", "AttachedFiles", "StorePath", "user names inside Message"),
    )
)
