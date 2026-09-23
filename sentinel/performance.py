"""Aggregate performance samples kept locally because Windows does not retain their history.

The files contain timestamps and numbers only. One elected writer samples whether anyone has a
dashboard open or not; a history reading later asks the same local files what preceded a stop.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, BinaryIO

from .bridge import Bridge, BridgeResult
from .paths import data_dir
from .reading import Param, Reading, Section, Spec, register

LOG = logging.getLogger("sentinel.performance")

INTERVAL_SECONDS = 60
MIN_INTERVAL = 60
MAX_INTERVAL = 600
KEEP_DAYS = 30
MAX_HISTORY_HOURS = 48
MAX_DAY_BYTES = 512_000
TRIM_DAY_BYTES = 400_000

# Every output property is numeric. An exception message stays in the reading's warnings and is
# never put into a sample file. Each class can fail independently, leaving null rather than zero.
SAMPLE_SCRIPT = r"""
$warnings = @()
$cpu = $null
$memory = $null
$disk = $null
try { $cpu = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'" -ErrorAction Stop }
catch { $warnings += "Processor performance counters did not answer: $($_.Exception.Message)" }
try { $memory = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory -ErrorAction Stop }
catch { $warnings += "Memory performance counters did not answer: $($_.Exception.Message)" }
try { $disk = Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk -Filter "Name='_Total'" -ErrorAction Stop }
catch { $warnings += "Disk performance counters did not answer: $($_.Exception.Message)" }
[pscustomobject]@{
    at = [DateTime]::UtcNow.ToString('o')
    cpu_percent = $(if ($cpu) { $cpu.PercentProcessorTime } else { $null })
    memory_available_mb = $(if ($memory) { $memory.AvailableMBytes } else { $null })
    committed_bytes = $(if ($memory) { $memory.CommittedBytes } else { $null })
    commit_limit_bytes = $(if ($memory) { $memory.CommitLimit } else { $null })
    pages_per_sec = $(if ($memory) { $memory.PagesPerSec } else { $null })
    disk_queue = $(if ($disk) { $disk.AvgDiskQueueLength } else { $null })
    disk_read_bytes_per_sec = $(if ($disk) { $disk.DiskReadBytesPerSec } else { $null })
    disk_write_bytes_per_sec = $(if ($disk) { $disk.DiskWriteBytesPerSec } else { $null })
    warnings = $warnings
}
"""

METRICS = (
    "cpu_percent",
    "memory_available_mb",
    "committed_bytes",
    "commit_limit_bytes",
    "pages_per_sec",
    "disk_queue",
    "disk_read_bytes_per_sec",
    "disk_write_bytes_per_sec",
)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at.astimezone(UTC) if at.tzinfo else None


def _stamp(at: datetime) -> str:
    return at.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def normalize(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Keep only a UTC time and finite, plausible numeric metrics; never persist extra fields."""
    at = _parse_time(payload.get("at"))
    if at is None:
        return None
    row: dict[str, Any] = {"at": _stamp(at)}
    cadence = payload.get("cadence_seconds")
    row["cadence_seconds"] = cadence if isinstance(cadence, int) and not isinstance(cadence, bool) and MIN_INTERVAL <= cadence <= MAX_INTERVAL else None
    for key in METRICS:
        value = payload.get(key)
        try:
            number = float(value) if value is not None and not isinstance(value, bool) else None
        except (TypeError, ValueError):
            number = None
        if number is None or not math.isfinite(number) or number < 0 or (key == "cpu_percent" and number > 100):
            row[key] = None
        else:
            row[key] = int(number) if number.is_integer() else round(number, 3)
    return row if any(row[key] is not None for key in METRICS) else None


def sample(bridge: Bridge) -> tuple[BridgeResult, dict[str, Any] | None, list[str]]:
    result = bridge.run(SAMPLE_SCRIPT, timeout=20)
    payload = result.items[0] if result.observed and result.items and isinstance(result.items[0], dict) else {}
    warnings = list(result.warnings) + [str(w) for w in (payload.get("warnings") or [])]
    return result, normalize(payload), warnings


def take_load(bridge: Bridge, params: dict[str, Any]) -> Reading:
    result, row, warnings = sample(bridge)
    outcome = result.outcome if not result.observed else "ok" if row else "empty"
    reading = Reading("load", params, outcome, {"kind": "powershell", "query": SAMPLE_SCRIPT.strip()}, took_ms=result.took_ms, warnings=warnings)
    if row:
        reading.sections = [Section("snapshot", "raw", row)]
        reading.count = 1
    elif result.observed:
        reading.sections = [Section("snapshot", "raw", None)]
        reading.count = 0
    else:
        reading.error = {"kind": result.outcome, "detail": result.error or ""}
    return reading


def _native_lock(handle: BinaryIO, acquire: bool, blocking: bool = False) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking and acquire else msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle, (fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)) if acquire else fcntl.LOCK_UN)


def _lock(path: Path, timeout: float = 5.0) -> BinaryIO:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.parent.chmod(0o700)
    handle = path.open("a+b")
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    deadline = time.monotonic() + timeout
    while True:
        try:
            _native_lock(handle, True)
            return handle
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                raise TimeoutError(f"the local performance store is busy: {path.name}") from None
            time.sleep(0.05)


@contextmanager
def locked(path: Path, timeout: float = 5.0) -> Iterator[None]:
    handle = _lock(path, timeout)
    try:
        yield
    finally:
        _native_lock(handle, False)
        handle.close()


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"), ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class PerformanceStore:
    """Daily numeric JSONL, bounded on disk, with a cross-process mutation lock."""

    def __init__(self, home: Path | None = None):
        self.home = home or data_dir() / "performance"
        self.lock_path = self.home / "store.lock"

    def settings(self) -> dict[str, Any]:
        try:
            value = json.loads((self.home / "settings.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"enabled": True, "interval_seconds": INTERVAL_SECONDS, "config_error": False}
        except (OSError, ValueError):
            return {"enabled": False, "interval_seconds": INTERVAL_SECONDS, "config_error": True}
        if not isinstance(value, dict):
            return {"enabled": False, "interval_seconds": INTERVAL_SECONDS, "config_error": True}
        enabled, interval = value.get("enabled"), value.get("interval_seconds")
        if not isinstance(enabled, bool) or not isinstance(interval, int) or isinstance(interval, bool) or not MIN_INTERVAL <= interval <= MAX_INTERVAL:
            return {"enabled": False, "interval_seconds": INTERVAL_SECONDS, "config_error": True}
        return {"enabled": enabled, "interval_seconds": interval, "config_error": False}

    def configure(self, enabled: bool, interval_seconds: int) -> dict[str, Any]:
        if not isinstance(enabled, bool) or isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int) or not MIN_INTERVAL <= interval_seconds <= MAX_INTERVAL:
            raise ValueError(f"interval_seconds must be {MIN_INTERVAL}–{MAX_INTERVAL} and enabled must be a boolean")
        with locked(self.lock_path):
            _atomic_json(self.home / "settings.json", {"enabled": enabled, "interval_seconds": interval_seconds})
        return self.settings()

    def status(self) -> dict[str, Any]:
        try:
            value = json.loads((self.home / "status.json").read_text(encoding="utf-8"))
            outcome = value.get("outcome")
            if outcome not in {"ok", "partial", "empty", "failed", "unavailable", "denied", "timeout", "cleared"}:
                raise ValueError("unknown stored outcome")
            return {"at": value.get("at") if _parse_time(value.get("at")) else None, "outcome": outcome, "took_ms": value.get("took_ms") if isinstance(value.get("took_ms"), int) else None}
        except (OSError, ValueError, AttributeError):
            return {"at": None, "outcome": "not_started", "took_ms": None}

    def record_status(self, outcome: str, took_ms: int | None = None) -> None:
        with locked(self.lock_path):
            _atomic_json(self.home / "status.json", {"at": _stamp(datetime.now(UTC)), "outcome": outcome, "took_ms": took_ms})

    def append(self, row: dict[str, Any]) -> None:
        clean = normalize(row)
        if clean is None:
            raise ValueError("a stored sample needs a time and at least one observed metric")
        at = _parse_time(clean["at"])
        assert at is not None
        day = at.date()
        with locked(self.lock_path):
            path = self.home / f"{day.isoformat()}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(clean, separators=(",", ":"), ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            if path.stat().st_size > MAX_DAY_BYTES:
                self._trim_day(path)
            self._prune(datetime.now(UTC).date())

    def _trim_day(self, path: Path) -> None:
        lines = path.read_bytes().splitlines(keepends=True)
        kept: list[bytes] = []
        size = 0
        for line in reversed(lines):
            if size + len(line) > TRIM_DAY_BYTES:
                break
            kept.append(line)
            size += len(line)
        temporary = path.with_suffix(".tmp")
        with temporary.open("wb") as handle:
            handle.writelines(reversed(kept))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def _prune(self, today: date) -> None:
        oldest = today - timedelta(days=KEEP_DAYS - 1)
        for path in self.home.glob("????-??-??.jsonl"):
            try:
                day = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if day < oldest or day > today:
                path.unlink(missing_ok=True)

    def clear(self) -> int:
        with locked(self.lock_path):
            files = list(self.home.glob("????-??-??.jsonl"))
            for path in files:
                path.unlink(missing_ok=True)
            _atomic_json(self.home / "status.json", {"at": _stamp(datetime.now(UTC)), "outcome": "cleared", "took_ms": None})
        return len(files)

    def read(self, start: datetime, end: datetime) -> tuple[list[dict[str, Any]], int]:
        rows: list[dict[str, Any]] = []
        malformed = 0
        with locked(self.lock_path):
            self._prune(datetime.now(UTC).date())
            day = start.date()
            while day <= end.date():
                path = self.home / f"{day.isoformat()}.jsonl"
                if path.exists():
                    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                        try:
                            payload = json.loads(line)
                            row = normalize(payload) if isinstance(payload, dict) else None
                        except ValueError:
                            row = None
                        at = _parse_time(row["at"]) if row else None
                        if row is None or at is None:
                            malformed += 1
                        elif start <= at <= end:
                            rows.append(row)
                day += timedelta(days=1)
        return sorted(rows, key=lambda row: row["at"]), malformed


class PerformanceCollector:
    """The sole writer for one data home; the lock survives until this server's thread ends."""

    def __init__(self, bridge: Bridge, store: PerformanceStore):
        self.bridge = bridge
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._leader: BinaryIO | None = None

    def start(self) -> bool:
        if self._thread is not None:
            return True
        try:
            self._leader = _lock(self.store.home / "collector.lock", timeout=0)
        except (OSError, TimeoutError):
            return False  # another process owns this data home's sampler
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sentinel-performance", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=25)
        if self._thread is None or not self._thread.is_alive():
            self._thread = None
            if self._leader is not None:
                _native_lock(self._leader, False)
                self._leader.close()
                self._leader = None

    def _run(self) -> None:
        due = time.monotonic()
        was_enabled = False
        while not self._stop.is_set():
            settings = self.store.settings()
            if not settings["enabled"]:
                was_enabled = False
                self._stop.wait(1)
                continue
            if not was_enabled:
                due = time.monotonic()  # re-enabling observes now, not the missed interval
                was_enabled = True
            remaining = due - time.monotonic()
            if remaining > 0:
                self._stop.wait(min(1, remaining))
                continue
            try:
                result, row, warnings = sample(self.bridge)
                if self._stop.is_set():
                    break  # a cancelled observation is not the machine's last sample status
                if result.observed and row is not None:
                    row["cadence_seconds"] = settings["interval_seconds"]
                    self.store.append(row)
                    outcome = "partial" if warnings else "ok"
                else:
                    outcome = "empty" if result.observed else result.outcome
                self.store.record_status(outcome, result.took_ms)
            except (OSError, TimeoutError, ValueError):
                if self._stop.is_set():
                    break
                LOG.warning("performance collection could not record a sample")
                try:
                    self.store.record_status("failed")
                except (OSError, TimeoutError):
                    pass
            due = time.monotonic() + settings["interval_seconds"]


def history_shape(rows: list[dict[str, Any]], start: datetime, end: datetime) -> dict[str, Any]:
    stamps = [_parse_time(row["at"]) for row in rows]
    times = [stamp for stamp in stamps if stamp is not None]
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)]
    shape: dict[str, Any] = {
        "window_start": _stamp(start),
        "window_end": _stamp(end),
        "first": rows[0]["at"] if rows else None,
        "last": rows[-1]["at"] if rows else None,
        "largest_gap_seconds": max(gaps) if gaps else None,
        "metrics": {},
    }
    for key in METRICS:
        values = [row[key] for row in rows if row.get(key) is not None]
        shape["metrics"][key] = {"count": len(values), "min": min(values) if values else None, "median": median(values) if values else None, "max": max(values) if values else None}
    commit = [100 * row["committed_bytes"] / row["commit_limit_bytes"] for row in rows if row.get("committed_bytes") is not None and row.get("commit_limit_bytes") and row["commit_limit_bytes"] > 0]
    traffic = [row["disk_read_bytes_per_sec"] + row["disk_write_bytes_per_sec"] for row in rows if row.get("disk_read_bytes_per_sec") is not None and row.get("disk_write_bytes_per_sec") is not None]
    for key, values in (("commit_percent", commit), ("disk_bytes_per_sec", traffic)):
        shape["metrics"][key] = {"count": len(values), "min": min(values) if values else None, "median": median(values) if values else None, "max": max(values) if values else None}
    return shape


def take_history(_bridge: Bridge, params: dict[str, Any]) -> Reading:
    hours = params["hours"]
    if not 1 <= hours <= MAX_HISTORY_HOURS:
        raise ValueError(f"hours must be between 1 and {MAX_HISTORY_HOURS}")
    end = _parse_time(params["end"]) if params["end"] else datetime.now(UTC)
    if end is None:
        raise ValueError("end must be an ISO time with a UTC offset")
    if end > datetime.now(UTC) + timedelta(minutes=1):
        raise ValueError("end is in the future")
    if end < datetime.now(UTC) - timedelta(days=KEEP_DAYS):
        raise ValueError(f"end is outside the {KEEP_DAYS}-day retention window")
    start = end - timedelta(hours=hours)
    store = PerformanceStore()
    method = {"kind": "local-jsonl", "source": "performance/YYYY-MM-DD.jsonl"}
    try:
        rows, malformed = store.read(start, end)
    except (OSError, TimeoutError) as exc:
        return Reading("performance_history", params, "unavailable", method, error={"kind": "local_store", "detail": f"Could not read local performance history: {type(exc).__name__}"})
    settings = store.settings()
    reading = Reading("performance_history", params, "ok" if rows else "empty", method, count=len(rows))
    reading.sections = [
        Section("samples", "raw", rows),
        Section("shape", "derived", history_shape(rows, start, end), basis="For each metric, min, median and max use only returned numeric samples. Commit percentage is committed bytes divided by the reported commit limit; disk throughput adds reported read and write bytes per second only when both answered. The largest gap is time between samples, not a claim about machine activity."),
        Section("collection", "raw", {"settings": settings, "last_attempt": store.status(), "retention_days": KEEP_DAYS}),
    ]
    if malformed:
        reading.warnings.append(f"{malformed} stored sample lines were malformed or incomplete and were skipped")
    return reading


register(Spec(name="load", description="One aggregate processor, memory and physical-disk performance snapshot from Windows; numeric fields only, with missing classes left null.", classes=("raw",), take=take_load, heavy=True))
register(Spec(name="performance_history", description="Local numeric performance samples retained across stops; query a window ending now or at an ISO moment, with per-metric shape and collection status.", classes=("raw", "derived"), take=take_history, params=(Param("hours", "int", 24, f"Hours to return, from 1 to {MAX_HISTORY_HOURS}", minimum=1, maximum=MAX_HISTORY_HOURS), Param("end", "str", "", "ISO time with offset to end the window; blank means now"))))
