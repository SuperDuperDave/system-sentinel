"""Fresh process pressure from Windows' formatted counters; process identity is never stored."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from ..bridge import Bridge
from ..reading import Reading, Section, Spec, register

MAX_PROCESSES = 2048
LEADERS = 5

PROCESS_SCRIPT = rf"""
$all = @(Get-CimInstance Win32_PerfFormattedData_PerfProc_Process -ErrorAction Stop | Where-Object {{ $_.IDProcess -gt 0 }})
$warnings = @()
$logical = $null
try {{ $logical = (Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).NumberOfLogicalProcessors }}
catch {{ $warnings += "Logical processor count did not answer: $($_.Exception.Message)" }}

# On an unusually busy machine, keep leaders in all three dimensions before filling by PID.
# The response says how many rows were omitted; rankings are only over returned rows.
$chosen = $all
if ($all.Count -gt {MAX_PROCESSES}) {{
    $seen = [System.Collections.Generic.HashSet[int]]::new()
    $selected = [System.Collections.Generic.List[object]]::new()
    foreach ($metric in @('PercentProcessorTime', 'WorkingSetPrivate', 'IODataBytesPersec')) {{
        foreach ($row in @($all | Sort-Object -Property $metric -Descending | Select-Object -First 256)) {{
            if ($seen.Add([int]$row.IDProcess)) {{ $selected.Add($row) }}
        }}
    }}
    foreach ($row in @($all | Sort-Object IDProcess)) {{
        if ($selected.Count -ge {MAX_PROCESSES}) {{ break }}
        if ($seen.Add([int]$row.IDProcess)) {{ $selected.Add($row) }}
    }}
    $chosen = @($selected | Sort-Object IDProcess)
    $warnings += "Windows returned $($all.Count) process instances; {MAX_PROCESSES} are shown, with leaders preserved in processor, private memory and I/O use"
}}

$processes = @($chosen | ForEach-Object {{
    [pscustomobject]@{{
        pid                       = $_.IDProcess
        name                      = $_.Name
        cpu_core_percent          = $_.PercentProcessorTime
        private_working_set_bytes = $_.WorkingSetPrivate
        private_bytes             = $_.PrivateBytes
        io_bytes_per_sec          = $_.IODataBytesPersec
        io_read_bytes_per_sec     = $_.IOReadBytesPersec
        io_write_bytes_per_sec    = $_.IOWriteBytesPersec
        handles                   = $_.HandleCount
        threads                   = $_.ThreadCount
    }}
}})
[pscustomobject]@{{
    at = [DateTime]::UtcNow.ToString('o')
    logical_processors = $logical
    total_processes = $all.Count
    processes = $processes
    warnings = $warnings
}}
"""

NUMBERS = (
    "cpu_core_percent",
    "private_working_set_bytes",
    "private_bytes",
    "io_bytes_per_sec",
    "io_read_bytes_per_sec",
    "io_write_bytes_per_sec",
    "handles",
    "threads",
)

LEADER_BASIS = (
    "Each list ranks only returned process instances by Windows' formatted counter at this one "
    "snapshot, with PID breaking ties. Processor time sums a process's threads on a one-processor "
    "scale and may exceed 100%; division by the reported logical processor count is a separate "
    "estimate of logical capacity, not a substitute for Windows' counter. Private working set is "
    "resident private memory. Process I/O includes activity beyond disk and is not disk throughput. "
    "An omitted process on a truncated machine cannot be ranked here."
)


def _number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0 or number > 2**53:
        return None
    return int(number) if number.is_integer() else round(number, 3)


def _name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())[:256]
    return name or None


def clean_process(value: Any) -> dict[str, Any] | None:
    """Keep the PID, instance name and declared counters; never let an extra CIM field through."""
    if not isinstance(value, dict):
        return None
    pid = _number(value.get("pid"))
    if not isinstance(pid, int) or pid <= 0 or pid > 2**32 - 1:
        return None
    return {"pid": pid, "name": _name(value.get("name")), **{key: _number(value.get(key)) for key in NUMBERS}}


def leaders(rows: list[dict[str, Any]], logical: int | None) -> dict[str, Any]:
    def ranked(metric: str) -> list[dict[str, Any]]:
        ordered = sorted((row for row in rows if row[metric] is not None), key=lambda row: (-row[metric], row["pid"]))[:LEADERS]
        return [
            {
                "pid": row["pid"],
                "name": row["name"],
                "value": row[metric],
                **({"logical_capacity_percent": round(row[metric] / logical, 2) if logical else None} if metric == "cpu_core_percent" else {}),
            }
            for row in ordered
        ]

    return {"cpu": ranked("cpu_core_percent"), "memory": ranked("private_working_set_bytes"), "io": ranked("io_bytes_per_sec")}


def take_processes(bridge: Bridge, params: dict[str, Any]) -> Reading:
    result = bridge.run(PROCESS_SCRIPT, timeout=20)
    reading = Reading("processes", params, result.outcome, {"kind": "powershell", "query": PROCESS_SCRIPT.strip()}, took_ms=result.took_ms, warnings=list(result.warnings))
    if not result.observed:
        reading.error = {"kind": result.error_kind, "detail": result.error or ""}
        return reading
    payload = result.items[0] if result.items and isinstance(result.items[0], dict) else None
    if payload is None:
        reading.outcome = "failed"
        reading.error = {"kind": "invalid_payload", "detail": "Windows' process query did not return a process snapshot"}
        return reading
    rows = [row for value in (payload.get("processes") or []) if (row := clean_process(value)) is not None]
    invalid = len(payload.get("processes") or []) - len(rows)
    if invalid:
        reading.warnings.append(f"{invalid} process rows could not be read and were skipped")
    reading.warnings.extend(str(w) for w in (payload.get("warnings") or []))
    at = payload.get("at")
    if not isinstance(at, str):
        at = None
    else:
        try:
            stamp = datetime.fromisoformat(at.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                at = None
        except ValueError:
            at = None
    logical = _number(payload.get("logical_processors"))
    logical = logical if isinstance(logical, int) and 0 < logical <= 4096 else None
    total = _number(payload.get("total_processes"))
    total = total if isinstance(total, int) and total >= len(rows) else len(rows)
    if total and not rows:
        reading.outcome = "failed"
        reading.error = {"kind": "invalid_rows", "detail": "Windows reported processes but no process row could be read"}
        return reading
    reading.outcome = "ok" if rows else "empty"
    reading.count = len(rows)
    reading.sections = [
        Section("snapshot", "raw", {"at": at, "logical_processors": logical, "total_processes": total, "returned_processes": len(rows), "omitted_processes": max(0, total - len(rows))}),
        Section("processes", "raw", rows),
        Section("leaders", "derived", leaders(rows, logical), basis=LEADER_BASIS),
    ]
    return reading


register(Spec(name="processes", description="Fresh per-process CPU time, private memory and process I/O from Windows, with exact rows and separately ranked leaders. Process identity is never stored in performance history.", classes=("raw", "derived"), take=take_processes, heavy=True))
