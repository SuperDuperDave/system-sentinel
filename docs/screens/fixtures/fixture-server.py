"""The real app (``sentinel.app.create_app``) with a bridge that answers the screenshot
views from fixtures. Nothing in the repository changes: the fixture is
injected at the one seam the readings already go through, so what's on screen is the same code
that renders the real thing, over records that were never on this machine.

Answers:
  - the identity probe (``$env:COMPUTERNAME``): a placeholder host and user
  - WHEA (``whea``, ``whea_record``, ``storms``, ``whea_reports``): tests/fixtures/whea-records.json for System rows, with each
    binary System record given a decodable CPER payload; three synthetic Kernel-WHEA channel rows
    exercise fatal previous-session and unavailable-header presentation
  - the System log (``events``, ``record``): docs/screens/fixtures/system-log.json, filtered
    and paged the way Get-WinEvent would be
  - nearby Application faults: tests/fixtures/fault-records.json, placed near the older restart
  - Memory and Power: synthetic source-outcome examples, optionally with selected source
    failures when ``SENTINEL_FIXTURE_DIAGNOSTIC_FAILURES=1`` is set
  - anything else: empty

Run: SYSTEM_SENTINEL_HOME=<scratch dir> ./.venv/bin/python fixture-server.py --port 8021
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

# Repository root: as committed this script sits at docs/screens/fixtures/, three below the
# root, so walking up finds it there. SENTINEL_REPO_ROOT overrides this, for a copy run from
# somewhere else (a scratch directory, while drafting) rather than its committed location.
def find_repo_root(start: Path) -> Path:
    override = os.environ.get("SENTINEL_REPO_ROOT")
    if override and (Path(override) / "sentinel" / "app.py").exists():
        return Path(override)
    for candidate in [start, *start.parents]:
        if (candidate / "sentinel" / "app.py").exists():
            return candidate
    raise SystemExit("could not find the repository root (looked for sentinel/app.py); set SENTINEL_REPO_ROOT")


REPO = find_repo_root(HERE)
sys.path.insert(0, str(REPO))

from sentinel.app import State, create_app  # noqa: E402
from sentinel.bridge import BridgeResult  # noqa: E402

WHEA_FIXTURE = REPO / "tests" / "fixtures" / "whea-records.json"
FAULT_FIXTURE = REPO / "tests" / "fixtures" / "fault-records.json"
SYSTEM_LOG_FIXTURE = HERE / "system-log.json"
WHEA_ANCHOR = time.time()  # exact re-reads must name the same synthetic event timestamp

FIXTURE_HOST = "WORKSTATION"
FIXTURE_USER = "person"


def minimal_cper() -> str:
    """The smallest record the decoder accepts (from tests/test_whea.py's ``minimal_cper``):
    a 128-byte CPER header plus one 72-byte generic-processor-error section descriptor."""
    descriptor = (
        (200).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (0x0300).to_bytes(2, "little")
        + bytes(2)
        + (1).to_bytes(4, "little")
        + uuid.UUID("9876ccad-47b4-4bdb-b65e-16f193c4f3db").bytes_le
        + bytes(16)
        + (0).to_bytes(4, "little")
        + bytes(20)
    )
    header = (
        b"CPER"
        + (0x0100).to_bytes(2, "little")
        + (0xFFFFFFFF).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + bytes(8)
        + (128 + len(descriptor)).to_bytes(4, "little")
        + bytes(8)
        + bytes(16) * 4
        + bytes(8 + 4 + 8 + 12)
    )
    return (header + descriptor).hex().upper()


CPER_HEX = minimal_cper()


def earlier_session_cper() -> str:
    """One synthetic System report exercises the earlier-session display path."""
    payload = bytearray.fromhex(CPER_HEX)
    payload[104:108] = (2).to_bytes(4, "little")
    return payload.hex().upper()


EARLIER_SESSION_CPER_HEX = earlier_session_cper()


def kernel_cper() -> str:
    """A safe synthetic fatal header; this source never invokes the external decoder."""
    payload = bytearray.fromhex(CPER_HEX)
    payload[12:16] = (1).to_bytes(4, "little")
    payload[96:104] = (77).to_bytes(8, "little")
    payload[104:108] = (2).to_bytes(4, "little")  # PreviousError
    return payload.hex().upper()


CHANNEL_CPER_HEX = kernel_cper()


def _powershell_stamp(epoch: float) -> str:
    """PowerShell's 'o' format, which the readings parse: seven fractional digits."""
    moment = datetime.fromtimestamp(epoch, UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond:06d}0Z"


def _parse_stamp(stamp: str) -> float:
    text = stamp.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).timestamp()


# ---------------------------------------------------------------- WHEA


def whea_records(now: float, count: int | None = None) -> list[dict[str, Any]]:
    """The committed WHEA fixture, materialized relative to ``now``, capped the way ``-MaxEvents``
    would cap it (most recent first). Every record with binary data is given the same small,
    decodable CPER payload so none of the fixture's short signature tokens reaches the real
    decoder. The record with no binary data remains absent, and the reading says so."""
    doc = json.loads(WHEA_FIXTURE.read_text(encoding="utf-8"))
    out = []
    for entry in doc["records"]:
        rec = {k: v for k, v in entry.items() if k not in ("group", "minutes_ago")}
        rec["Log"] = "System"
        rec["TimeCreated"] = _powershell_stamp(now - entry["minutes_ago"] * 60)
        rec["MachineName"] = FIXTURE_HOST
        out.append(rec)
    out.sort(key=lambda r: r["TimeCreated"], reverse=True)
    if count is not None:
        out = out[:count]
    # The committed fixture holds short tokens to exercise signature grouping. They are not CPER
    # records: feeding them to the real .NET decoder can cause an unhandled exception that
    # Windows logs as an application crash. Every binary payload served for a screenshot must be structurally
    # valid, even when only the newest one's detail is visible in the final image.
    for record in out:
        if record.get("RawData"):
            record["RawData"] = EARLIER_SESSION_CPER_HEX if record["RecordId"] == 9034 else CPER_HEX
    return out


def kernel_whea_records(now: float) -> list[dict[str, Any]]:
    common = {
        "Log": "Microsoft-Windows-Kernel-WHEA/Errors", "Id": 20, "Level": 4,
        "LevelDisplayName": "Information", "ProviderName": "Microsoft-Windows-Kernel-WHEA",
        "ProviderId": None, "Version": 0, "MachineName": FIXTURE_HOST,
        "TaskDisplayName": None, "Message": "WHEA Event", "Properties": [],
    }
    return [
        {**common, "RecordId": 77, "TimeCreated": _powershell_stamp(now - 2 * 60), "RawData": CHANNEL_CPER_HEX},
        {**common, "RecordId": 76, "TimeCreated": _powershell_stamp(now - 5 * 60), "RawData": "43504552"},
        {**common, "RecordId": 75, "TimeCreated": _powershell_stamp(now - 2893 * 60), "RawData": CHANNEL_CPER_HEX},
    ]


# ---------------------------------------------------------------- System log


def system_log_records(now: float) -> list[dict[str, Any]]:
    doc = json.loads(SYSTEM_LOG_FIXTURE.read_text(encoding="utf-8"))
    out = []
    for entry in doc["records"]:
        rec = {k: v for k, v in entry.items() if k != "minutes_ago"}
        moment = now - entry["minutes_ago"] * 60
        rec["TimeCreated"] = _powershell_stamp(moment)
        if entry["Id"] in (12, 6008):  # the only two templates with a moment of their own to fill in
            rec["Message"] = _fill_template(rec["Message"], moment)
            rec["Properties"] = [_fill_template(p, moment) if isinstance(p, str) else p for p in rec["Properties"]]
        out.append(rec)
    return sorted(out, key=lambda r: r["TimeCreated"], reverse=True)  # newest first, as Get-WinEvent returns


def fault_records(now: float) -> list[dict[str, Any]]:
    """A synthetic nearby report cluster for the older of the two System restart frames."""
    rows = json.loads(FAULT_FIXTURE.read_text(encoding="utf-8"))["records"]
    for index, row in enumerate(rows):
        row["TimeCreated"] = _powershell_stamp(now - (2890 + index * 3) * 60)
        row["MachineName"] = FIXTURE_HOST
    return rows


def _fill_template(text: str, moment: float) -> str:
    """The triad's two message templates carry their own moment as a parameter: 12's is its own
    boot instant, 6008's is the crash six minutes and change earlier — the Display 4101 moment."""
    boot = datetime.fromtimestamp(moment, UTC)
    shutdown = datetime.fromtimestamp(moment - 6.2 * 60, UTC)
    return text.format(
        boot_iso=boot.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
        shutdown_time=f"{shutdown.hour % 12 or 12}:{shutdown.minute:02d}:{shutdown.second:02d} {'AM' if shutdown.hour < 12 else 'PM'}",
        shutdown_date=f"{shutdown.month}/{shutdown.day}/{shutdown.year}",
    )


_LEVEL_RE = re.compile(r"Level=([\d,]+)")
_MAXEVENTS_RE = re.compile(r"-MaxEvents (\d+)")


def answer_log_records(script: str) -> BridgeResult:
    """The current shared collector envelope, with real filtering and one-extra-row cutoff."""
    now = time.time()
    application = "log = 'Application'" in script
    log = "Application" if application else "System"
    all_rows = fault_records(now) if application else system_log_records(now)
    counts = [int(value) for value in _MAXEVENTS_RE.findall(script)]
    exact_cap = re.search(r"Select-Object -First (\d+) \|\s*& \{ process \{ \[void\]\$found.Add", script)
    cap = int(exact_cap.group(1)) - 1 if exact_cap else max(counts) - 1
    start_match = re.search(r"window_start = ('[^']+'|\$null|\$since)", script)
    end_match = re.search(r"window_end = ('[^']+'|\$null)", script)
    start_text = start_match.group(1).strip("'") if start_match and start_match.group(1) != "$null" else None
    end_text = end_match.group(1).strip("'") if end_match and end_match.group(1) != "$null" else None
    if start_text == "$since":
        start_text = _powershell_stamp(now - 3300 * 60)
    levels = {int(value) for value in _LEVEL_RE.findall(script)}
    matching = [row for row in all_rows if
                (not levels or row["Level"] in levels)
                and (start_text is None or _parse_stamp(row["TimeCreated"]) >= _parse_stamp(start_text))
                and (end_text is None or _parse_stamp(row["TimeCreated"]) < _parse_stamp(end_text))]
    kept = matching[:cap]
    # The Application log also contains older non-fault events outside this selected fixture.
    oldest = _powershell_stamp(now - 5000 * 60) if application else (all_rows[-1]["TimeCreated"] if all_rows else None)
    return BridgeResult("ok", items=[{
        "log": log, "outcome": "ok" if kept else "empty", "error": None, "records": kept,
        "returned": len(kept), "limit": cap, "truncated": len(matching) > cap, "stopped": None,
        "window_start": start_text, "window_end": end_text, "queried_at": _powershell_stamp(now),
        "metadata": {"log": log, "log_enabled": True, "log_mode": "Circular", "log_state": "ok", "log_error": None,
                     "log_oldest": oldest, "oldest_state": "ok" if oldest else "empty", "oldest_error": None},
    }], took_ms=37)


def answer_whea(script: str) -> BridgeResult:
    """The two-source collector's object shape, including safe synthetic channel rows."""
    match = re.search(r"Read-WheaSource 'system' 'System' .*? (\d+)\)", script)
    assert match, "the WHEA fixture must follow the collector's explicit per-source limit"
    limit = int(match.group(1))
    now = WHEA_ANCHOR
    system = whea_records(now)
    returned = [_whea_preview(row) for row in system[:limit]]
    source = {
        "name": "system", "log": "System", "outcome": "ok" if returned else "empty", "error": None,
        "returned": len(returned), "limit": limit, "truncated": len(system) > limit, "stopped": None, "records": returned,
        "log_enabled": True, "log_mode": "Circular", "log_state": "ok", "log_error": None,
        "log_oldest": _powershell_stamp(now - 86400), "oldest_state": "ok", "oldest_error": None,
    }
    channel_rows = kernel_whea_records(now)
    channel_returned = [_whea_preview(row) for row in channel_rows[:limit]]
    channel = {
        "name": "kernel_whea", "log": "Microsoft-Windows-Kernel-WHEA/Errors", "outcome": "ok", "error": None,
        "returned": len(channel_returned), "limit": limit, "truncated": len(channel_rows) > limit, "stopped": None, "records": channel_returned,
        "log_enabled": True, "log_mode": "Circular", "log_state": "ok", "log_error": None,
        "log_oldest": channel_rows[-1]["TimeCreated"], "oldest_state": "ok", "oldest_error": None,
    }
    return BridgeResult("ok", items=[{"sources": [source, channel]}], took_ms=412)


def _whea_preview(row: dict[str, Any]) -> dict[str, Any]:
    """Mirror the list's bounded fields; exact fixture rows remain for whea_record."""
    raw = row.get("RawData")
    message = row.get("Message")
    return {
        "RecordId": row["RecordId"], "Id": row["Id"], "Level": row["Level"],
        "ProviderName": row["ProviderName"], "TimeCreated": row["TimeCreated"],
        "Message": message[:1024] if isinstance(message, str) else None,
        "MessageChars": len(message.encode("utf-16-le")) // 2 if isinstance(message, str) else None,
        "Log": row["Log"], "HeaderHex": raw[:256] if isinstance(raw, str) else None,
        "PayloadBytes": len(raw) // 2 if isinstance(raw, str) else None,
    }


def answer_whea_record(script: str) -> BridgeResult:
    """One exact synthetic source row, as the new on-demand reading asks Windows for it."""
    match = re.search(r"Read-WheaSource '(system|kernel_whea)' '[^']+' \"[^\"]*EventRecordID=(\d+)[^\"]*\" 1 \d+", script)
    assert match, "the exact WHEA fixture must carry a source and EventRecordID"
    name, record_id = match.group(1), int(match.group(2))
    rows = whea_records(WHEA_ANCHOR) if name == "system" else kernel_whea_records(WHEA_ANCHOR)
    selected = [row for row in rows if row["RecordId"] == record_id]
    source = {
        "name": name, "log": "System" if name == "system" else "Microsoft-Windows-Kernel-WHEA/Errors",
        "outcome": "ok" if selected else "empty", "error": None,
        "returned": len(selected), "limit": 1, "truncated": False, "stopped": None, "records": selected,
        "log_enabled": True, "log_mode": "Circular", "log_state": "ok", "log_error": None,
        "log_oldest": rows[-1]["TimeCreated"] if rows else None,
        "oldest_state": "ok" if rows else "empty", "oldest_error": None,
    }
    return BridgeResult("ok", items=[{"source": source}], took_ms=22)


def answer_whea_window(script: str) -> BridgeResult:
    """A selected, count-capped preview from the same retained synthetic channel rows."""
    name = re.search(r"source = Read-WheaSource '(system|kernel_whea)'", script).group(1)
    since = re.search(r"\$from = \[datetimeoffset\]::Parse\('([^']+)'", script).group(1)
    before = re.search(r"\$requestedUntil = \[datetimeoffset\]::Parse\('([^']+)'", script).group(1)
    request = re.search(r"source = Read-WheaSource '[^']+' '[^']+' \$select (\d+)( -oldest)? -windowed", script)
    assert request, "the WHEA window fixture needs an explicit count and direction"
    cap, oldest_first = int(request.group(1)), bool(request.group(2))
    queried = _powershell_stamp(time.time())
    observed = min(_parse_stamp(before), _parse_stamp(queried))
    rows = whea_records(WHEA_ANCHOR) if name == "system" else kernel_whea_records(WHEA_ANCHOR)
    matching = [row for row in rows if _parse_stamp(since) <= _parse_stamp(row["TimeCreated"]) < observed]
    matching.sort(key=lambda row: _parse_stamp(row["TimeCreated"]), reverse=not oldest_first)
    selected = [_whea_preview(row) for row in matching[:cap]]
    log = "System" if name == "system" else "Microsoft-Windows-Kernel-WHEA/Errors"
    source = {
        "name": name, "log": log, "outcome": "ok" if selected else "empty", "error": None,
        "returned": len(selected), "limit": cap, "truncated": len(matching) > cap, "stopped": None,
        "records": selected, "probe_time": matching[cap]["TimeCreated"] if len(matching) > cap else None,
        "log_enabled": True, "log_mode": "Circular", "log_state": "ok", "log_error": None,
        "log_oldest": _powershell_stamp(_parse_stamp(since) - 86400), "oldest_state": "ok", "oldest_error": None,
    }
    # These are DateTimeOffset.ToString('o') echoes, unlike event TimeCreated's UTC Z stamp.
    def as_offset(stamp: str) -> str:
        return stamp.replace("Z", "+00:00")
    return BridgeResult("ok", items=[{"queried_at": as_offset(queried), "window_start": as_offset(since),
                                       "window_end": as_offset(before),
                                       "observed_end": as_offset(_powershell_stamp(observed)), "source": source}], took_ms=24)


def answer_whea_reports(script: str) -> BridgeResult:
    """The bounded report-time projection, including a report near the older restart frame."""
    now = time.time()
    bucket_seconds = int(re.search(r"\$bucketTicks = \[long\](\d+)", script).group(1))
    count = int(re.search(r"\(\[long\]\((\d+) - 1\)", script).group(1))
    requested = re.search(r"\$requestedUntil = \[datetimeoffset\]::Parse\('([^']+)', \[Globalization\.CultureInfo\]::InvariantCulture\)", script)
    queried = int(now * 1000) / 1000
    until_time = min(_parse_stamp(requested.group(1)), queried) if requested else queried
    last = int((until_time - 0.000001) // bucket_seconds) * bucket_seconds
    start = _powershell_stamp(last - (count - 1) * bucket_seconds)
    end = _powershell_stamp(until_time)
    cap = max(int(value) for value in _MAXEVENTS_RE.findall(script)) - 1
    first, until = _parse_stamp(start), _parse_stamp(end)
    rows = []
    for record in kernel_whea_records(WHEA_ANCHOR):
        if not first <= _parse_stamp(record["TimeCreated"]) < until:
            continue
        raw = record["RawData"]
        rows.append({
            "RecordId": record["RecordId"], "Id": record["Id"], "ProviderName": record["ProviderName"],
            "LogName": record["Log"], "TimeCreated": record["TimeCreated"],
            "HeaderHex": raw[:256], "PayloadBytes": len(raw) // 2,
        })
    kept = rows[:cap]
    source = {
        "log": "Microsoft-Windows-Kernel-WHEA/Errors", "outcome": "ok" if kept else "empty", "error": None,
        "returned": len(kept), "limit": cap, "truncated": len(rows) > cap, "stopped": None, "records": kept,
        "log_enabled": True, "log_mode": "Circular", "log_state": "ok", "log_error": None,
        "log_oldest": _powershell_stamp(_parse_stamp(start) - 86400), "oldest_state": "ok", "oldest_error": None,
    }
    return BridgeResult("ok", items=[{"window_start": start, "window_end": end, "queried_at": _powershell_stamp(queried), "source": source}], took_ms=24)


class FixtureBridge:
    exe = "fixture"
    available = True

    def run(self, script: str, *, timeout: float = 60, depth: int = 6) -> BridgeResult:
        if "$env:COMPUTERNAME" in script:
            return BridgeResult("ok", items=[{"host": FIXTURE_HOST, "user": FIXTURE_USER, "ps": "5.1", "os": "10.0"}], took_ms=3)
        if "foreach ($log in" in script:
            # The stream's cursor probe: neither view under capture watches the live stream, but
            # it starts on every page and would otherwise show "failed" forever. Anchoring both
            # logs to a real high-water mark makes it read as caught up, not broken.
            top = max((r["RecordId"] for r in system_log_records(time.time())), default=0)
            return BridgeResult("ok", items=[{"log": "System", "record": top}, {"log": "Application", "record": 1}], took_ms=6)
        if "metadata = $meta" in script and "records = @($records)" in script:
            return answer_log_records(script)
        if "Read-WheaSource" in script and "sources = @(" in script:
            return answer_whea(script)
        if "Read-WheaSource" in script and "-windowed -fromTicks" in script:
            return answer_whea_window(script)
        if "Read-WheaSource" in script and "source = Read-WheaSource" in script:
            return answer_whea_record(script)
        if "window_start = $startIso" in script and "Provider[@Name='Microsoft-Windows-Kernel-WHEA']" in script:
            return answer_whea_reports(script)
        if "window_start = $startIso" in script and "WHEA-Logger" in script:
            # The storm collector returns one source object, not the record list used by whea.
            now = time.time()
            bucket_seconds = int(re.search(r"\$bucketTicks = \[long\](\d+)", script).group(1))
            count = int(re.search(r"\(\[long\]\((\d+) - 1\)", script).group(1))
            queried = int(now * 1000) / 1000
            requested = re.search(r"\$requestedUntil = \[datetimeoffset\]::Parse\('([^']+)', \[Globalization\.CultureInfo\]::InvariantCulture\)", script)
            until_time = min(_parse_stamp(requested.group(1)), queried) if requested else queried
            last = int((until_time - 0.000001) // bucket_seconds) * bucket_seconds
            start = _powershell_stamp(last - (count - 1) * bucket_seconds)
            end = _powershell_stamp(until_time)
            cap = max(int(value) for value in _MAXEVENTS_RE.findall(script)) - 1
            first, until = _parse_stamp(start), _parse_stamp(end)
            records = [
                {"RecordId": row["RecordId"], "Id": row["Id"], "ProviderName": "Microsoft-Windows-WHEA-Logger", "LogName": "System", "LevelDisplayName": row.get("LevelDisplayName"), "TimeCreated": row["TimeCreated"], "Message": row.get("Message"),
                 "HeaderHex": row["RawData"][:256] if row.get("RawData") else None,
                 "PayloadBytes": len(row["RawData"]) // 2 if row.get("RawData") else None}
                for row in whea_records(WHEA_ANCHOR) if first <= _parse_stamp(row["TimeCreated"]) < until
            ]
            source = {
                "log": "System", "outcome": "ok" if records else "empty", "error": None,
                "returned": min(len(records), cap), "limit": cap, "truncated": len(records) > cap, "records": records[:cap],
                "log_enabled": True, "log_mode": "Circular", "log_state": "ok", "log_error": None,
                "log_oldest": _powershell_stamp(_parse_stamp(start) - 86400), "oldest_state": "ok", "oldest_error": None,
            }
            return BridgeResult("ok", items=[{"window_start": start, "window_end": end, "queried_at": _powershell_stamp(queried), "source": source}], took_ms=412)
        if "Win32_PhysicalMemory -ErrorAction Stop" in script:
            failed = os.environ.get("SENTINEL_FIXTURE_DIAGNOSTIC_FAILURES") == "1"
            modules = None if failed else [{"BankLabel": "CHANNEL A", "DeviceLocator": "DIMM 1", "Manufacturer": "Example", "PartNumber": "EXAMPLE-16",
                "serial_number": "SYNTHETIC", "Capacity": 17179869184, "Speed": 3200, "ConfiguredClockSpeed": 3200,
                "ConfiguredVoltage": 1200, "TotalWidth": 64, "DataWidth": 64}]
            payload = {
                "modules": modules, "arrays": [{"MemoryDevices": 2, "MemoryErrorCorrection": 3, "Use": 3}],
                "diagnostic": None, "log_begins": "2026-09-01T00:00:00Z",
                "sources": {"modules": {"outcome": "failed" if failed else "ok"}, "arrays": {"outcome": "ok"},
                    "diagnostic": {"outcome": "empty"}, "log_begins": {"outcome": "ok"}},
                "warnings": ["The module inventory did not answer in this synthetic example."] if failed else [],
            }
            return BridgeResult("ok", items=[payload], took_ms=26)
        if "powercfg.exe /devicequery wake_armed" in script:
            failed = os.environ.get("SENTINEL_FIXTURE_DIAGNOSTIC_FAILURES") == "1"
            payload = {
                "sleep_states": ["The following sleep states are available on this system:", "Standby (S3)"],
                "aspm": {"ac_index": "0x00000002", "dc_index": "0x00000000"},
                "wake_armed": None if failed else [], "hiberboot_enabled": 1, "batteries": None if failed else [], "boot_time": "2026-09-01T00:00:00Z",
                "transitions": [],
                "sources": {"sleep_states": {"outcome": "ok"}, "aspm": {"outcome": "ok"},
                    "wake_armed": {"outcome": "failed" if failed else "empty"}, "hiberboot": {"outcome": "ok"},
                    "batteries": {"outcome": "failed" if failed else "empty"}, "boot_time": {"outcome": "ok"},
                    "transitions": {"outcome": "empty", "returned": 0, "limit": 120, "limit_reached": False}},
                "warnings": ["The battery and wake-device queries did not answer in this synthetic example."] if failed else [],
            }
            return BridgeResult("ok", items=[payload], took_ms=24)
        if "WHEA-Logger" in script:
            cap = _MAXEVENTS_RE.search(script)
            return BridgeResult("ok", items=whea_records(time.time(), int(cap.group(1)) if cap else None), took_ms=412)
        # Every other reading answers empty: the screenshot views never take them, and nothing
        # here should render as if it were machine data.
        return BridgeResult("empty", items=[], took_ms=5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8021)
    args = parser.parse_args()

    state = State(bridge=FixtureBridge())
    app = create_app(state, mcp=False)

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
