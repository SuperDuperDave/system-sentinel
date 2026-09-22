"""Fresh process pressure is a bounded snapshot, not retained performance history."""

from __future__ import annotations

from sentinel.bridge import BridgeResult
from sentinel.readings.processes import MAX_PROCESSES, PROCESS_SCRIPT, clean_process, take_processes
from tests.conftest import FakeBridge


def row(pid: int, name: str, cpu: object, memory: object, io: object, **extra):
    return {
        "pid": pid,
        "name": name,
        "cpu_core_percent": cpu,
        "private_working_set_bytes": memory,
        "private_bytes": memory,
        "io_bytes_per_sec": io,
        "io_read_bytes_per_sec": io,
        "io_write_bytes_per_sec": 0,
        "handles": 8,
        "threads": 2,
        **extra,
    }


def answer(processes, **extra):
    return BridgeResult("ok", items=[{"at": "2026-09-22T05:00:00Z", "logical_processors": 16, "total_processes": len(processes), "processes": processes, "warnings": [], **extra}], took_ms=412)


def test_process_rows_keep_exact_counters_and_derived_leaders_without_paths():
    payload = [
        row(12, "editor", 120, 4000, 200, path=r"C:\Users\someone\private.exe"),
        row(13, "editor#1", 15, 9000, 800),
        row(14, "browser", 40, 6000, 50),
    ]
    reading = take_processes(FakeBridge(result=answer(payload)), {})
    assert reading.outcome == "ok" and reading.count == 3 and reading.took_ms == 412
    assert reading.section("snapshot").data == {"at": "2026-09-22T05:00:00Z", "logical_processors": 16, "total_processes": 3, "returned_processes": 3, "omitted_processes": 0}
    rows = reading.section("processes").data
    assert rows[0]["cpu_core_percent"] == 120
    assert "path" not in rows[0]
    top = reading.section("leaders").data
    assert top["cpu"][0] == {"pid": 12, "name": "editor", "value": 120, "logical_capacity_percent": 7.5}
    assert top["memory"][0]["pid"] == 13
    assert top["io"][0]["pid"] == 13
    assert "not disk throughput" in reading.section("leaders").basis
    assert "Win32_PerfFormattedData_PerfProc_Process" in reading.method["query"]


def test_invalid_counter_stays_unknown_and_invalid_pid_is_skipped():
    reading = take_processes(FakeBridge(result=answer([
        row(12, "  one\n two  ", float("nan"), -20, "bad"),
        row(0, "Idle", 95, 0, 0),
    ], total_processes=2)), {})
    assert reading.outcome == "ok" and reading.count == 1
    assert reading.section("processes").data[0]["name"] == "one two"
    assert reading.section("processes").data[0]["cpu_core_percent"] is None
    assert reading.section("processes").data[0]["private_working_set_bytes"] is None
    assert reading.section("processes").data[0]["io_bytes_per_sec"] is None
    assert "skipped" in reading.warnings[0]
    assert reading.section("snapshot").data["omitted_processes"] == 1
    assert clean_process({"pid": "not a pid", "name": "x"}) is None


def test_zero_processes_is_empty_but_failure_and_unreadable_rows_are_not():
    empty = take_processes(FakeBridge(result=answer([])), {})
    assert empty.outcome == "empty" and empty.count == 0
    assert empty.section("processes").data == []
    denied = take_processes(FakeBridge(result=BridgeResult("denied", error="access refused")), {})
    assert denied.outcome == "denied" and denied.sections == []
    invalid = take_processes(FakeBridge(result=answer([{"pid": None}], total_processes=1)), {})
    assert invalid.outcome == "failed" and invalid.error["kind"] == "invalid_rows"
    assert invalid.sections == []


def test_process_script_bounds_the_answer_and_preserves_three_leader_dimensions():
    assert f"$all.Count -gt {MAX_PROCESSES}" in PROCESS_SCRIPT
    assert "PercentProcessorTime', 'WorkingSetPrivate', 'IODataBytesPersec" in PROCESS_SCRIPT
    assert "ExecutablePath" not in PROCESS_SCRIPT and "CommandLine" not in PROCESS_SCRIPT
