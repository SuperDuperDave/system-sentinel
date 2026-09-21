"""Hardware errors: ``whea`` (the WHEA-Logger records with their payload decoded beside them)
and ``storms`` (the records over a window in wall-clock buckets, by signature, with burst and
acceleration flags).

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
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..bridge import WSL_INTEROP_ERRORS, Bridge
from ..reading import Param, Reading, Section, Spec, from_bridge, register
from .events import record_projection, winevent
from .health import DECODER

PROVIDER = "Microsoft-Windows-WHEA-Logger"
LOG = "System"

# The decoder takes the record on the command line, which Windows caps near 32,000 characters.
MAX_HEX = 30_000
DECODE_TIMEOUT = 15.0
DECODE_BUDGET = 30.0
DECODE_WORKERS = 4
_INTEROP_ATTEMPTS = 3

DECODED_BASIS = "DecodeWheaRecord.exe over each record's RawData"

# A storm is bounded by the window, but the window is the caller's: cap what one reading pulls
# out of the log and say so when the cap bites, rather than serializing an unbounded log.
RECORD_CAP = 20_000
MAX_BUCKETS = 20_000
RECENT_BUCKETS = 10
BASELINE_BUCKETS = 240
BASELINE_FLOOR = 0.1  # a baseline of zero would make every recent event infinitely accelerated
RECENT_FLOOR = 0.5  # below this the ratio is noise, not a trend
SIGNATURE_VERSION = "v1"
SIGNATURE_ID_CHARS = 16


# ---------------------------------------------------------------- whea


def query_xml(since: str | None = None) -> str:
    """The provider's records in the System log, from a moment when one is given.

    Both readings ask the log's own index by XPath, on the provider and on the time. The
    same question put through ``-FilterHashtable @{LogName; ProviderName}`` answered thirty
    times slower on this machine when nothing matched, which is exactly the case the tool
    has to be quick about.
    """
    predicate = f"Provider[@Name='{PROVIDER}']"
    if since:
        predicate += f" and TimeCreated[@SystemTime&gt;='{since}']"
    return f"""$xml = @"
<QueryList><Query Id="0" Path="{LOG}"><Select Path="{LOG}">*[System[{predicate}]]</Select></Query></QueryList>
"@
"""


def whea_script(count: int) -> str:
    """The records in the shared record shape, plus the CPER payload: the first binary property."""
    return query_xml() + winevent(
        f"""Get-WinEvent -FilterXml $xml -MaxEvents {int(count)} -ErrorAction Stop |
    {record_projection(RAW_DATA)}"""
    )


# The CPER payload: the record's first binary property, as hex.
RAW_DATA = "RawData = $( $b = $_.Properties | Where-Object { $_.Value -is [byte[]] } | Select-Object -First 1; if ($b) { [System.BitConverter]::ToString($b.Value).Replace('-','') } else { $null } )"


def take_whea(bridge: Bridge, params: dict[str, Any]) -> Reading:
    started = time.perf_counter()
    script = whea_script(params["count"])
    reading = from_bridge("whea", params, script, bridge.run(script))
    if not reading.observed:
        return reading
    records = reading.section("records").data
    decoded, warnings = decode_all(records)
    reading.sections.append(Section("decoded", "derived", decoded, basis=DECODED_BASIS))
    reading.warnings.extend(warnings)
    reading.took_ms = _ms(started)
    return reading


def decode_all(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """One entry per record, in the records' order: the decoder's structure or why there is none."""
    if not records:
        return [], []
    if not os.path.exists(DECODER):
        return (
            [{"RecordId": r.get("RecordId"), "error": "the decoder is not present"} for r in records],
            ["the CPER decoder is not present: the records were read but not decoded"],
        )
    deadline = time.monotonic() + DECODE_BUDGET
    # One process per record; a few at a time, in the records' order. The decoder is a local
    # executable over a string, so nothing it does contends with the bridge.
    with ThreadPoolExecutor(max_workers=DECODE_WORKERS) as pool:
        return list(pool.map(lambda r: decode_record(r, deadline), records)), []


def decode_record(record: dict[str, Any], deadline: float) -> dict[str, Any]:
    record_id = record.get("RecordId")
    payload = re.sub(r"[^0-9A-Fa-f]", "", str(record.get("RawData") or ""))
    if not payload:
        return {"RecordId": record_id, "error": "the record carries no binary payload"}
    if len(payload) > MAX_HEX:
        return {"RecordId": record_id, "error": f"payload of {len(payload)} hex characters is longer than the decoder takes on the command line"}
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {"RecordId": record_id, "error": "the reading's decoding budget was spent before this record"}

    stdout, stderr, code, error = _run_decoder(payload, min(DECODE_TIMEOUT, remaining))
    if error:
        return {"RecordId": record_id, "error": error}
    if code != 0 or not stdout:
        return {"RecordId": record_id, "error": stderr or f"the decoder exited with code {code}"}
    try:
        return {"RecordId": record_id, "decoded": json.loads(stdout)}
    except json.JSONDecodeError:
        return {"RecordId": record_id, "error": f"the decoder's output was not JSON: {stdout.splitlines()[0][:200]}"}


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


@dataclass(frozen=True)
class Window:
    """The wall-clock window the buckets cover, aligned to bucket boundaries and ending now."""

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
    if bucket_seconds is None or bucket_seconds <= 0:
        raise ValueError("parameter 'bucket_seconds': must be at least one second")
    count = int(hours * 3600 // bucket_seconds)
    if count < 1:
        raise ValueError("parameter 'bucket_seconds': longer than the window it would divide")
    if count > MAX_BUCKETS:
        raise ValueError(f"parameter 'bucket_seconds': the window would take {count} buckets; ask for longer buckets or a shorter window")
    last = int((now if now is not None else time.time()) // bucket_seconds) * bucket_seconds
    return Window(start=last - (count - 1) * bucket_seconds, bucket_seconds=bucket_seconds, count=count)


def storms_script(window: Window) -> str:
    """The records from the window's start; the buckets need the time and the message, not the payload."""
    return query_xml(_stamp(window.start)) + winevent(
        f"""Get-WinEvent -FilterXml $xml -MaxEvents {RECORD_CAP} -ErrorAction Stop |
    Select-Object RecordId, Id, LevelDisplayName,
        @{{Name='TimeCreated'; Expression={{ $_.TimeCreated.ToUniversalTime().ToString('o') }}}},
        Message"""
    )


def take_storms(bridge: Bridge, params: dict[str, Any]) -> Reading:
    started = time.perf_counter()
    if params["burst_threshold"] is None or params["burst_threshold"] < 1:
        raise ValueError("parameter 'burst_threshold': must be at least one record")
    if params["accel_threshold"] is None or params["accel_threshold"] <= 0:
        raise ValueError("parameter 'accel_threshold': must be greater than zero")
    window = window_for(params["hours"], params["bucket_seconds"])
    script = storms_script(window)
    result = bridge.run(script)

    reading = Reading(
        reading="storms",
        params=params,
        outcome=result.outcome,
        method={"kind": "powershell", "query": script},
        took_ms=result.took_ms,
        warnings=list(result.warnings),
    )
    if not reading.observed:
        reading.error = {"kind": result.outcome, "detail": result.error or ""}
        return reading

    records = result.items if result.outcome == "ok" else []
    if len(records) >= RECORD_CAP:
        reading.warnings.append(f"the window holds at least {RECORD_CAP} records; the buckets cover only the most recent {RECORD_CAP}")
    reading.count = len(records)
    reading.sections = compose(records, window, params)
    reading.took_ms = _ms(started)
    return reading


def compose(records: list[dict[str, Any]], window: Window, params: dict[str, Any]) -> list[Section]:
    """The three sections: what was counted, what it was, and what the rule makes of it."""
    totals = [0] * window.count
    per_bucket: list[dict[str, int]] = [{} for _ in range(window.count)]
    signatures: dict[str, _Signature] = {}
    unplaced = 0

    for record in records:
        moment = _moment(record.get("TimeCreated"))
        idx = window.index(moment) if moment is not None else None
        if idx is None:
            unplaced += 1
            continue
        sig = _accumulate(signatures, record)
        totals[idx] += 1
        per_bucket[idx][sig.id] = per_bucket[idx].get(sig.id, 0) + 1

    # Every bucket is counted, but a day of idle minutes does not need a day of objects:
    # ``totals`` is the shape of the window, one count per bucket, oldest first; ``active``
    # names the buckets that hold records and what was in them.
    buckets = {
        "from": _stamp(window.start),
        "to": _stamp(window.end),
        "bucket_seconds": window.bucket_seconds,
        "bucket_count": window.count,
        "total": sum(totals),
        "unplaced": unplaced,
        "totals": totals,
        "active": [
            {"index": i, "start": _stamp(window.start + i * window.bucket_seconds), "total": totals[i], "signatures": per_bucket[i]}
            for i in range(window.count)
            if totals[i]
        ],
    }
    ranked = sorted(signatures.values(), key=lambda s: (s.count, s.last_seen), reverse=True)
    sections = [
        Section(
            "buckets",
            "derived",
            buckets,
            basis=(
                f"the WHEA-Logger records in the window counted into wall-clock buckets of {window.bucket_seconds} seconds, idle buckets included: "
                "'totals' holds one count per bucket from 'from' onwards, 'active' the buckets that hold records, "
                "'unplaced' the records whose timestamp could not be read"
            ),
        ),
        Section(
            "signatures",
            "derived",
            [s.to_dict() for s in ranked],
            basis="SHA-256 over the error type, bank, APIC id, MCI status, PCI vendor and device ids and the normalized message text; the first characters of the digest identify the signature",
        ),
        Section("status", "inferred", status(totals, per_bucket, params, unplaced), basis=_status_basis(params)),
    ]
    return sections


def status(totals: list[int], per_bucket: list[dict[str, int]], params: dict[str, Any], unplaced: int = 0) -> dict[str, Any]:
    """The old detector's rules, over wall-clock buckets: a burst first, then an acceleration, else quiet."""
    burst_threshold = params["burst_threshold"]
    accel_threshold = params["accel_threshold"]

    recent = totals[-RECENT_BUCKETS:]
    recent_from = max(0, len(totals) - RECENT_BUCKETS)
    baseline = totals[max(0, recent_from - BASELINE_BUCKETS) : recent_from]

    peak = max(recent) if recent else 0
    recent_rate = _mean(recent)
    baseline_rate = _mean(baseline)
    acceleration = round(recent_rate / max(baseline_rate, BASELINE_FLOOR), 3)
    dominant = _dominant(per_bucket[recent_from:])

    if peak >= burst_threshold:
        state, severity = "burst", ("critical" if peak > burst_threshold * 2 else "warning")
        reason = f"{peak} records in one bucket of the recent window; the burst threshold is {burst_threshold}"
    elif acceleration >= accel_threshold and recent_rate > RECENT_FLOOR:
        state, severity = "accelerating", "warning"
        reason = f"the recent rate is {acceleration}x the baseline ({recent_rate} against {baseline_rate} per bucket); the acceleration threshold is {accel_threshold}"
    else:
        state, severity = "quiet", None
        reason = (
            "the window holds no WHEA-Logger records"
            if sum(totals) == 0 and not unplaced
            else f"no bucket reached the burst threshold of {burst_threshold} and the recent rate is not {accel_threshold}x the baseline"
        )

    return {
        "state": state,
        "severity": severity,
        "reason": reason,
        "peak_rate": float(peak),
        "recent_rate": recent_rate,
        "baseline_rate": baseline_rate,
        "acceleration": acceleration,
        "dominant": dominant,
        "recent_buckets": len(recent),
        "baseline_buckets": len(baseline),
    }


def _status_basis(params: dict[str, Any]) -> str:
    return (
        f"the last {RECENT_BUCKETS} buckets against the {BASELINE_BUCKETS} before them, averaged over every wall-clock bucket including idle ones: "
        f"burst when one recent bucket reaches {params['burst_threshold']} records (critical above twice that), otherwise accelerating when the recent "
        f"rate is at least {params['accel_threshold']}x the baseline and above the noise floor. A lead to investigate, not a diagnosis."
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


def _accumulate(signatures: dict[str, _Signature], record: dict[str, Any]) -> _Signature:
    sig = signature(record)
    held = signatures.setdefault(sig.id, sig)
    held.count += 1
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


def _moment(stamp: Any) -> float | None:
    """The record's own UTC timestamp as an epoch, or nothing when it cannot be read."""
    if not stamp:
        return None
    text = str(stamp).strip().replace("Z", "+00:00")
    for candidate in (text, re.sub(r"\.(\d{6})\d+", r".\1", text)):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    return None


def _stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _mean(values: list[int]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


register(
    Spec(
        name="whea",
        description="WHEA-Logger records with their binary payload, each decoded beside it: what the firmware told Windows about a hardware error, and what the CPER record inside it says.",
        classes=("raw", "derived"),
        take=take_whea,
        params=(Param("count", "int", 30, "How many of the most recent records."),),
        private=("MachineName", "user names inside Message", "serial and UUID fields inside the decoded structure"),
    )
)

register(
    Spec(
        name="storms",
        description="WHEA-Logger records over a window in wall-clock buckets, grouped by signature, with the burst and acceleration rules applied. Computed from the log on each take; nothing is stored between takes.",
        classes=("derived", "inferred"),
        take=take_storms,
        params=(
            Param("hours", "int", 24, "How far back the window reaches."),
            Param("bucket_seconds", "int", 60, "The width of one wall-clock bucket."),
            Param("burst_threshold", "int", 5, "Records in one bucket that count as a burst; twice this is critical."),
            Param("accel_threshold", "float", 2.0, "How many times the baseline rate the recent rate must reach to count as accelerating."),
        ),
        private=("user names and profile paths inside the signature samples' message text",),
    )
)
