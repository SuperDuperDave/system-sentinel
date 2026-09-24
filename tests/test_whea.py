"""Hardware errors: both logs read as independent sources, each record's CPER header read beside it,
WHEA-Logger payloads decoded, and the storm rules over wall-clock buckets.

This machine's System log holds no WHEA-Logger records, and its Kernel-WHEA channel holds records
the decoder has not been exercised on, so the host tests assert the contract and never hand a real
record to the decoder. Every non-empty path — both logs merged, duplicates, a source that fails or
stops, bucketing across idle minutes, the signature rule, a burst, an acceleration — runs against
synthetic records through a fake bridge, and the decoder paths run against a faked process except
the one host test that invokes the real executable on a record built here.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel import readings  # noqa: F401
from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.mcp_server import tools
from sentinel.reading import take
from sentinel.readings import whea
from tests.conftest import FakeBridge, identity_result, real_bridge_or_skip

FIXTURE = Path(__file__).parent / "fixtures" / "whea-records.json"


def load(groups: set[str] | None = None, now: float | None = None) -> list[dict[str, Any]]:
    """The fixture as the bridge would hand it over: newest first, timestamps against a reference moment."""
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    moment = time.time() if now is None else now
    records = []
    for entry in doc["records"]:
        if groups is not None and entry["group"] not in groups:
            continue
        record = {k: v for k, v in entry.items() if k not in ("group", "minutes_ago")}
        record["TimeCreated"] = _powershell_stamp(moment - entry["minutes_ago"] * 60)
        records.append(record)
    return sorted(records, key=lambda r: r["TimeCreated"], reverse=True)


def _powershell_stamp(epoch: float) -> str:
    """PowerShell's 'o' format: seven fractional digits, which the reading has to parse."""
    moment = datetime.fromtimestamp(epoch, UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond:06d}0Z"


def storms(records: list[dict[str, Any]], outcome: str = "ok", *, oldest: str | None = None, truncated: bool = False, stopped: dict[str, str] | None = None, raw_rows: bool = False, log_state: str = "ok", query_outcome: str | None = None, host_now: float | None = None, start_shift: int = 0, omit_queried_at: bool = False, **params: Any):
    class StormBridge:
        def run(self, script: str, *, depth: int = 6):
            if outcome not in ("ok", "empty"):
                return BridgeResult(outcome, error="the log is not there", took_ms=9)
            bucket_seconds = int(re.search(r"\$bucketTicks = \[long\](\d+)", script).group(1))
            count = int(re.search(r"\(\[long\]\((\d+) - 1\)", script).group(1))
            machine_now = host_now if host_now is not None else time.time()
            rounded_end = datetime.fromtimestamp(int(machine_now * 1000) / 1000, UTC)
            requested = re.search(r"\$requestedUntil = \[datetimeoffset\]::Parse\('([^']+)', \[Globalization\.CultureInfo\]::InvariantCulture\)", script)
            end = min(rounded_end, datetime.fromisoformat(requested.group(1).replace("Z", "+00:00"))) if requested else rounded_end
            last = int((end - timedelta(microseconds=1)).timestamp() // bucket_seconds) * bucket_seconds
            start = whea._stamp(last - (count - 1) * bucket_seconds + start_shift)
            before = datetime.fromisoformat(start.replace("Z", "+00:00")) - timedelta(seconds=1)
            rows = [
                {**(record if raw_rows else {key: record.get(key) for key in whea.STORM_ROW_KEYS if key in record}),
                 "ProviderName": whea.PROVIDER, "LogName": whea.LOG,
                 "HeaderHex": record.get("HeaderHex"), "PayloadBytes": record.get("PayloadBytes")}
                for record in records
            ]
            source = {
                "log": whea.LOG, "outcome": query_outcome or outcome, "error": "synthetic query failure" if query_outcome in ("failed", "denied") else None,
                "returned": len(rows), "limit": whea.RECORD_CAP,
                "truncated": None if stopped else truncated, "stopped": stopped, "records": rows, "log_enabled": True, "log_mode": "Circular", "log_state": log_state,
                "log_error": None, "log_oldest": oldest or before.isoformat().replace("+00:00", "Z"), "oldest_state": "ok", "oldest_error": None,
            }
            payload = {"window_start": start, "window_end": _powershell_stamp(end.timestamp()), "source": source}
            if not omit_queried_at:
                payload["queried_at"] = _powershell_stamp(rounded_end.timestamp())
            return BridgeResult("ok", items=[payload], took_ms=9)

    bridge = StormBridge()
    return asyncio.run(take("storms", bridge, params))


# ---------------------------------------------------------------- whea

SYSTEM_OLDEST = "2026-07-27T16:16:21.3813739Z"
CHANNEL_OLDEST = "2025-10-21T23:58:54.6468291Z"


def cper(record_id: int = 1, *, severity: int = 1, flags: int = 0x2, valid: int = 0x2, when: tuple[int, ...] = (34, 24, 22, 0, 24, 3, 26, 20)) -> str:
    """A structurally valid record with the header fields the identity reads set on purpose."""
    data = bytearray.fromhex(minimal_cper())
    data[12:16] = severity.to_bytes(4, "little")
    data[16:20] = valid.to_bytes(4, "little")
    data[24:32] = bytes(when)  # seconds, minutes, hours, precise, day, month, year, century
    data[80:96] = uuid.UUID("e8f56ffe-919c-4cc5-ba88-65abe14913bb").bytes_le
    data[96:104] = record_id.to_bytes(8, "little")
    data[104:108] = flags.to_bytes(4, "little")
    return data.hex().upper()


def row(log: str, record_id: int, when: str, *, raw: str | None = None, event_id: int | None = None, message: str | None = None) -> dict[str, Any]:
    system = log == whea.LOG
    return {
        "RecordId": record_id, "Id": event_id if event_id is not None else (18 if system else 20), "Level": 2 if system else 4,
        "LevelDisplayName": "Error" if system else "Information",
        "ProviderName": whea.PROVIDER if system else whea.CHANNEL_PROVIDER, "ProviderId": None, "Version": 0,
        "MachineName": "SENTINEL-FIXTURE", "TaskDisplayName": None, "TimeCreated": when,
        "Message": message if message is not None else ("A fatal hardware error has occurred." if system else "WHEA Event"),
        "Properties": [], "Log": log, "RawData": raw,
    }


def stamp(minutes_ago: float, now: float = 1_790_000_000.0) -> str:
    return _powershell_stamp(now - minutes_ago * 60)


def source(name: str, rows: list[dict[str, Any]], *, limit: int = 30, outcome: str | None = None, truncated: bool | None = False, stopped: dict[str, str] | None = None, error: str | None = None, oldest: str | None = None, oldest_state: str = "ok", log_state: str = "ok", enabled: bool = True) -> dict[str, Any]:
    log = whea.LOG if name == "system" else whea.CHANNEL
    return {
        "name": name, "log": log, "outcome": outcome or ("ok" if rows else "empty"), "error": error,
        "returned": len(rows), "limit": limit, "truncated": None if stopped else truncated, "stopped": stopped, "records": rows,
        "log_enabled": enabled, "log_mode": "Circular", "log_state": log_state, "log_error": None if log_state == "ok" else "synthetic metadata failure",
        "log_oldest": (oldest or (SYSTEM_OLDEST if name == "system" else CHANNEL_OLDEST)) if oldest_state == "ok" else None,
        "oldest_state": oldest_state, "oldest_error": None if oldest_state in ("ok", "empty") else "synthetic oldest failure",
    }


def failed(name: str, outcome: str = "failed", error: str = "There is not an event log on the localhost computer that matches it.") -> dict[str, Any]:
    return {**source(name, [], outcome=outcome, error=error, oldest_state="failed", log_state="failed"), "truncated": None}


def preview(source_result: dict[str, Any]) -> dict[str, Any]:
    """Project old raw fixtures as the list collector now projects them on Windows."""
    projected = []
    for record in source_result["records"]:
        raw = record.get("RawData")
        message = record.get("Message")
        projected.append({
            **{key: record.get(key) for key in whea.WHEA_PREVIEW_KEYS - {"HeaderHex", "PayloadBytes", "MessageChars"}},
            "HeaderHex": raw[:256] if isinstance(raw, str) else None,
            "PayloadBytes": len(raw) // 2 if isinstance(raw, str) else None,
            "MessageChars": whea._utf16_chars(message) if isinstance(message, str) else None,
        })
    return {**source_result, "records": projected}


def take_both(system: dict[str, Any] | None, channel: dict[str, Any] | None, count: int = 30):
    sources = [preview(value) for value in (system, channel) if value is not None]
    return asyncio.run(take("whea", FakeBridge(BridgeResult("ok", items=[{"sources": sources}], took_ms=11)), {"count": count}))


def sections(reading) -> dict[str, Any]:
    return {section.name: section.data for section in reading.sections}


@pytest.fixture
def no_decoder_launch(monkeypatch):
    """The decoder is present but must not run: any launch fails the test."""
    monkeypatch.setattr(whea, "DECODER", str(FIXTURE))
    monkeypatch.setattr(whea, "_run_decoder", lambda payload, timeout: pytest.fail("the external decoder was launched"))


@pytest.fixture
def canned_decoder(monkeypatch):
    """A System record with a payload goes to the decoder; this answers for it without a process."""
    monkeypatch.setattr(whea, "DECODER", str(FIXTURE))
    monkeypatch.setattr(whea, "_run_decoder", lambda payload, timeout: ("{}", "", 0, None))


def test_the_query_asks_both_logs_for_bounded_headers_and_treats_a_no_match_as_empty():
    script = whea.whea_script(12)
    assert "Provider[@Name='Microsoft-Windows-WHEA-Logger']" in script and "'system' 'System'" in script
    assert "'kernel_whea' 'Microsoft-Windows-Kernel-WHEA/Errors'" in script
    assert "Provider[@Name='Microsoft-Windows-Kernel-WHEA'] and (EventID=20)" in script
    assert script.count("Read-WheaSource '") == 2 and script.endswith("12)) }\n")
    assert "-MaxEvents ($limit + 1)" in script and "-ErrorAction Stop" in script
    assert "NoMatchingEventsFound" in script and "Read-LogMetadata $log" in script
    assert "Log = $event.LogName" in script and "HeaderHex" in script and "byte[]" in script
    assert "RawData =" not in script and "Properties =" not in script


def test_both_logs_merge_newest_first_to_the_exact_global_limit(no_decoder_launch):
    system = [row(whea.LOG, 900 + i, stamp(minutes)) for i, minutes in enumerate((1, 5, 9))]
    channel = [row(whea.CHANNEL, 40 - i, stamp(minutes), raw=cper(40 - i)) for i, minutes in enumerate((2, 3, 10))]
    reading = take_both(source("system", system, limit=4, truncated=False), source("kernel_whea", channel, limit=4, truncated=False), count=4)
    data = sections(reading)
    assert reading.outcome == "ok" and reading.count == 4
    assert [(r["Log"], r["RecordId"]) for r in data["records"]] == [(whea.LOG, 900), (whea.CHANNEL, 40), (whea.CHANNEL, 39), (whea.LOG, 901)]
    assert data["collection"]["limit"] == 4 and data["collection"]["returned"] == 4 and data["collection"]["truncated"] is True
    # Two records beyond the global limit were returned and cut; the list is whole only after the oldest shown.
    assert data["coverage"]["complete"] is False and data["coverage"]["cutoff"] == data["records"][-1]["TimeCreated"]
    assert [data["coverage"]["sources"][name]["shown"] for name in ("system", "kernel_whea")] == [2, 2]
    assert [(e["Log"], e["RecordId"]) for e in data["identity"]] == [(r["Log"], r["RecordId"]) for r in data["records"]]


def test_ties_fall_to_source_order_and_then_to_the_newer_record_in_its_log(no_decoder_launch):
    same = stamp(7)
    reading = take_both(
        source("system", [row(whea.LOG, 10, same), row(whea.LOG, 9, same)], limit=3),
        source("kernel_whea", [row(whea.CHANNEL, 12, same, raw=cper(1)), row(whea.CHANNEL, 11, same, raw=cper(2))], limit=3),
        count=3,
    )
    assert [(r["Log"], r["RecordId"]) for r in sections(reading)["records"]] == [(whea.LOG, 10), (whea.LOG, 9), (whea.CHANNEL, 12)]
    assert sections(reading)["coverage"]["cutoff"] == same  # records at exactly the cutoff may be missing


def test_a_record_id_shared_by_both_logs_stays_two_records(no_decoder_launch):
    from sentinel.stack import _selection_ids

    reading = take_both(source("system", [row(whea.LOG, 5, stamp(1))]), source("kernel_whea", [row(whea.CHANNEL, 5, stamp(2), raw=cper(77))]))
    data = sections(reading)
    assert reading.count == 2 and [(r["Log"], r["RecordId"]) for r in data["records"]] == [(whea.LOG, 5), (whea.CHANNEL, 5)]
    assert "decoded" not in data
    assert [(e["Log"], e["RecordId"]) for e in data["identity"]] == [(whea.LOG, 5), (whea.CHANNEL, 5)]
    envelope = reading.to_dict()
    with pytest.raises(ValueError, match="ambiguous across logs"):
        _selection_ids(envelope, [5])
    assert _selection_ids(envelope, [f"{whea.CHANNEL}:5"]) == [f"{whea.CHANNEL}:5"]


def test_the_preview_never_launches_the_decoder_or_carries_full_bytes(monkeypatch):
    system_payload = cper(3, severity=2, flags=0)
    launched: list[str] = []
    monkeypatch.setattr(whea, "DECODER", str(FIXTURE))
    monkeypatch.setattr(whea, "_run_decoder", lambda payload, timeout: (launched.append(payload) or json.dumps({"Header": {"Signature": "CPER"}}), "", 0, None))
    reading = take_both(
        source("system", [row(whea.LOG, 3, stamp(1), raw=system_payload)]),
        source("kernel_whea", [row(whea.CHANNEL, 1, stamp(2), raw=cper(9)), row(whea.CHANNEL, 2, stamp(3), raw=system_payload)]),
    )
    assert launched == []
    assert "decoded" not in sections(reading)
    assert all("RawData" not in record and "Properties" not in record for record in sections(reading)["records"])
    assert sections(reading)["records"][1]["HeaderHex"] == cper(9)[:256]
    assert reading.warnings == []


def test_channel_records_need_no_decoder_and_warn_of_none(monkeypatch):
    monkeypatch.setattr(whea, "DECODER", str(FIXTURE.parent / "no-decoder-here.exe"))
    reading = take_both(source("system", []), source("kernel_whea", [row(whea.CHANNEL, 1, stamp(1), raw=cper(1))]))
    assert reading.outcome == "ok" and reading.warnings == []
    assert "decoded" not in sections(reading)


def test_authenticated_preview_withholds_header_by_default_and_never_sends_full_bytes(no_decoder_launch):
    payload = cper(77)
    event = row(whea.CHANNEL, 7, stamp(1), raw=payload)
    event["Properties"] = [payload]
    bridge = FakeBridge(
        BridgeResult("ok", items=[{"sources": [preview(source("system", [])), preview(source("kernel_whea", [event]))]}]),
        by_marker={"$env:COMPUTERNAME": identity_result("SENTINEL-FIXTURE", "person")},
    )
    token = "synthetic-test-token-0123456789"
    with TestClient(create_app(State(bridge=bridge, token=token))) as client:
        headers = {"Authorization": f"Bearer {token}"}
        default = client.get("/api/readings/whea?count=30", headers=headers)
        exact = client.get("/api/readings/whea?count=30&unredacted=true", headers=headers)
    assert default.status_code == exact.status_code == 200
    redacted = default.json()
    original = exact.json()
    hidden = next(s["data"][0] for s in redacted["sections"] if s["name"] == "records")
    raw = next(s["data"][0] for s in original["sections"] if s["name"] == "records")
    assert "cper" in redacted["redacted"] and hidden["HeaderHex"].startswith("<cper bytes withheld")
    assert "RawData" not in hidden and "Properties" not in hidden and payload not in json.dumps(redacted)
    assert raw["HeaderHex"] == payload[:256] and "RawData" not in raw
    assert next(s["data"][0]["cper"]["severity"] for s in redacted["sections"] if s["name"] == "identity") == "fatal"


def exact_record(answer: dict[str, Any], *, record_id: int = 77, source_name: str = "kernel_whea"):
    bridge = FakeBridge(BridgeResult("ok", items=[{"source": answer}], took_ms=11))
    return asyncio.run(take("whea_record", bridge, {"source": source_name, "record_id": record_id}))


def test_exact_record_uses_log_and_record_id_and_preserves_the_raw_row(no_decoder_launch):
    payload = cper(77)
    event = row(whea.CHANNEL, 77, stamp(1), raw=payload)
    reading = exact_record(source("kernel_whea", [event], limit=1))
    assert reading.outcome == "ok" and reading.count == 1
    assert sections(reading)["records"] == [event]
    assert sections(reading)["identity"][0]["cper"]["previous_session"] is True
    assert sections(reading)["decoded"][0]["error"] == whea.DEFERRED
    assert sections(reading)["collection"]["source"]["outcome"] == "ok"
    script = whea.whea_record_script(whea.WHEA_SOURCES[1], 77)
    assert "EventRecordID=77" in script and "(EventID=20)" in script
    assert "-MaxEvents ($limit + 1)" in script and f"1 {whea.MAX_EXACT_BINARY_BYTES}" in script


@pytest.mark.parametrize("answer,expected", [
    (source("kernel_whea", [], limit=1), "empty"),
    (failed("kernel_whea"), "failed"),
    (failed("kernel_whea", outcome="denied"), "denied"),
    (source("kernel_whea", [row(whea.CHANNEL, 78, stamp(1))], limit=1), "failed"),
    (source("kernel_whea", [row(whea.CHANNEL, 77, stamp(1))], limit=1, truncated=True), "failed"),
    (source("kernel_whea", [row(whea.CHANNEL, 77, stamp(1))], limit=1, stopped={"kind": "failed", "detail": "stopped"}), "failed"),
    (source("kernel_whea", [row(whea.CHANNEL, 77, stamp(1))], limit=1, stopped={"kind": "denied", "detail": "denied"}), "denied"),
])
def test_exact_record_distinguishes_missing_from_untrustworthy(answer, expected):
    reading = exact_record(answer)
    assert reading.outcome == expected
    assert reading.count == (0 if expected == "empty" else None)
    if expected == "empty":
        assert sections(reading)["records"] == []
    else:
        assert "records" not in sections(reading) and "identity" not in sections(reading)
    assert sections(reading)["collection"]["source"]["outcome"] == expected
    if expected == "empty":
        assert "no retained event" in " ".join(reading.warnings)
    else:
        assert reading.error and reading.error["kind"] == expected


def test_exact_record_failure_reasons_are_distinct():
    mismatched = exact_record(source("kernel_whea", [row(whea.CHANNEL, 78, stamp(1))], limit=1))
    ambiguous = exact_record(source("kernel_whea", [row(whea.CHANNEL, 77, stamp(1))], limit=1, truncated=True))
    stopped = exact_record(source("kernel_whea", [row(whea.CHANNEL, 77, stamp(1))], limit=1, stopped={"kind": "denied", "detail": "access denied"}))
    assert "different RecordId" in mismatched.error["detail"]
    assert "more than one" in ambiguous.error["detail"]
    assert stopped.outcome == "denied" and "stopped" in stopped.error["detail"]


def test_exact_record_parameter_and_auth_boundaries(no_decoder_launch):
    payload = cper(77)
    event = row(whea.CHANNEL, 77, stamp(1), raw=payload)
    event["Properties"] = [payload]
    bridge = FakeBridge(
        BridgeResult("ok", items=[{"source": source("kernel_whea", [event], limit=1)}]),
        by_marker={"$env:COMPUTERNAME": identity_result("SENTINEL-FIXTURE", "person")},
    )
    token = "synthetic-test-token-0123456789"
    with TestClient(create_app(State(bridge=bridge, token=token))) as client:
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/readings/whea_record?record_id=77").status_code == 401
        for query in ("", "?record_id=77", "?source=kernel_whea", "?record_id=0&source=kernel_whea", "?record_id=not-a-number&source=kernel_whea", "?record_id=77&source=wrong"):
            assert client.get("/api/readings/whea_record" + query, headers=headers).status_code == 422
        default = client.get("/api/readings/whea_record?source=kernel_whea&record_id=77", headers=headers)
        exact = client.get("/api/readings/whea_record?source=kernel_whea&record_id=77&unredacted=true", headers=headers)
    assert default.status_code == exact.status_code == 200
    redacted = default.json()
    original = exact.json()
    assert payload not in json.dumps(redacted) and "cper" in redacted["redacted"]
    assert next(s["data"][0]["RawData"] for s in original["sections"] if s["name"] == "records") == payload


def test_exact_record_agent_tool_requires_an_exact_log_reference():
    tool = next(item for item in tools() if item.name == "whea_record")
    schema = tool.input_schema
    assert "record_id" in schema["required"]
    assert "source" in schema["required"]
    assert schema["properties"]["record_id"]["minimum"] == 1
    assert schema["properties"]["record_id"]["maximum"] == whea.MAX_RECORD_ID
    assert schema["properties"]["source"]["enum"] == ["system", "kernel_whea"]
    assert schema["if"]["properties"]["unredacted"]["const"] is True
    assert schema["then"]["required"] == ["reason"]


def test_the_header_identity_is_read_locally_and_names_what_it_can_justify(no_decoder_launch):
    reading = take_both(source("system", []), source("kernel_whea", [
        row(whea.CHANNEL, 3, stamp(1), raw=cper(0x01DC00000000ABCD)),
        row(whea.CHANNEL, 2, stamp(2), raw=cper(2, severity=2, flags=0, valid=0)),
        row(whea.CHANNEL, 1, stamp(3), raw="435045520000"),
    ]))
    first, second, broken = sections(reading)["identity"]
    assert first == {"Log": whea.CHANNEL, "RecordId": 3, "error": None, "cper": {
        "record_id": "0x01dc00000000abcd", "severity": "fatal", "section_count": 1,
        "notify_type": "e8f56ffe-919c-4cc5-ba88-65abe14913bb",
        "header_time": {"bytes": "2218160018031A14", "precise": False, "reserved_bits": False,
                        "as_integers": "2026-03-24T22:24:34", "as_bcd": None, "reading": "as_integers"},
        "flags": "0x00000002", "recovered": False, "previous_session": True, "simulated": False,
    }}
    assert second["cper"]["severity"] == "corrected" and second["cper"]["previous_session"] is False
    assert second["cper"]["header_time"] is None  # the valid bit is not set, so the time is not claimed
    assert broken["cper"] is None and broken["error"] == "the CPER header is shorter than 128 bytes"
    assert "PlatformId" not in json.dumps(sections(reading)["identity"])


@pytest.mark.parametrize(("clock", "integers", "bcd", "reading", "precise", "reserved"), [
    ((34, 24, 22, 0, 24, 3, 26, 20), "2026-03-24T22:24:34", None, "as_integers", False, False),
    ((0x34, 0x24, 0x22, 1, 0x24, 0x03, 0x26, 0x20), None, "2026-03-24T22:24:34", "as_bcd", True, False),
    ((0x22, 0x18, 0x16, 0, 0x18, 0x03, 0x19, 0x14), "2025-03-24T22:24:34", "1419-03-18T16:18:22", None, False, False),
    ((1, 2, 3, 0, 4, 5, 6, 7), "0706-05-04T03:02:01", "0706-05-04T03:02:01", "both", False, False),
    ((0x3C, 0x24, 0x22, 1, 0x24, 0x03, 0x26, 0x20), None, None, None, True, False),
    ((1, 2, 3, 0, 0x13, 0x13, 6, 7), None, None, None, False, False),
    ((34, 24, 22, 0, 24, 3, 126, 19), None, None, None, False, False),
    ((0, 0, 0, 0, 0x30, 0x02, 0x26, 0x20), None, None, None, False, False),
    ((34, 24, 22, 2, 24, 3, 26, 20), "2026-03-24T22:24:34", None, "as_integers", False, True),
    ((34, 24, 22, 3, 24, 3, 26, 20), "2026-03-24T22:24:34", None, "as_integers", True, True),
])
def test_cper_header_time_keeps_both_conditional_calendar_readings_and_header_flags(clock, integers, bcd, reading, precise, reserved):
    header, error = whea.cper_header(cper(1, when=clock))
    assert error is None and header is not None
    assert header["header_time"] == {
        "bytes": bytes(clock).hex().upper(), "precise": precise, "reserved_bits": reserved,
        "as_integers": integers, "as_bcd": bcd, "reading": reading,
    }


def test_a_record_without_a_payload_has_an_identity_error_not_a_guess(no_decoder_launch):
    reading = take_both(source("system", [row(whea.LOG, 8, stamp(1))]), source("kernel_whea", []))
    assert sections(reading)["identity"] == [{"Log": whea.LOG, "RecordId": 8, "cper": None, "error": "the record carries no binary payload"}]
    assert sections(reading)["groups"]["without_identity"] == 1


def test_one_error_reported_in_both_logs_is_grouped_and_both_rows_are_kept(canned_decoder):
    payload = cper(41)
    reading = take_both(
        source("system", [row(whea.LOG, 70001, stamp(1), raw=payload)]),
        source("kernel_whea", [row(whea.CHANNEL, 41, stamp(1.01), raw=payload), row(whea.CHANNEL, 40, stamp(9), raw=cper(40))]),
    )
    data = sections(reading)
    assert reading.count == 3 and len(data["records"]) == 3
    assert data["groups"] == {
        "returned": 3, "without_identity": 0,
        "likely_same_error": [[f"{whea.LOG}:70001", f"{whea.CHANNEL}:41"]],
        "likely_errors": 2, "conflicting": [],
    }


def test_a_shared_record_id_with_a_different_header_is_a_conflict_not_a_group(canned_decoder):
    reading = take_both(
        source("system", [row(whea.LOG, 70001, stamp(1), raw=cper(41, severity=2))]),
        source("kernel_whea", [row(whea.CHANNEL, 41, stamp(2), raw=cper(41, severity=1)), row(whea.CHANNEL, 40, stamp(3), raw=cper(41, severity=1, when=(1, 1, 1, 0, 1, 1, 26, 20)))]),
    )
    groups = sections(reading)["groups"]
    assert groups["likely_same_error"] == [] and groups["likely_errors"] == 3
    assert groups["conflicting"] == [[f"{whea.LOG}:70001", f"{whea.CHANNEL}:41", f"{whea.CHANNEL}:40"]]


def test_an_unset_header_time_or_zero_cper_id_cannot_merge_distinct_errors(canned_decoder):
    untimed_a = cper(77, valid=0, when=(1, 2, 3, 0, 4, 5, 26, 20))
    untimed_b = cper(77, valid=0, when=(9, 2, 3, 0, 4, 5, 26, 20))
    zero = cper(0, valid=0)
    reading = take_both(
        source("system", [row(whea.LOG, 101, stamp(1), raw=untimed_a), row(whea.LOG, 102, stamp(3), raw=zero)]),
        source("kernel_whea", [row(whea.CHANNEL, 201, stamp(2), raw=untimed_b), row(whea.CHANNEL, 202, stamp(4), raw=zero)]),
    )
    groups = sections(reading)["groups"]
    assert groups["likely_same_error"] == [] and groups["likely_errors"] == 4
    assert groups["conflicting"] == [[f"{whea.LOG}:101", f"{whea.CHANNEL}:201"]]


def test_same_log_repeats_are_kept_separate_until_a_cross_log_pair_is_unambiguous(canned_decoder):
    payload = cper(81)
    reading = take_both(
        source("system", [row(whea.LOG, 101, stamp(1), raw=payload), row(whea.LOG, 102, stamp(2), raw=payload)]),
        source("kernel_whea", [row(whea.CHANNEL, 201, stamp(3), raw=payload)]),
    )
    groups = sections(reading)["groups"]
    assert groups["likely_same_error"] == [] and groups["likely_errors"] == 3


@pytest.mark.parametrize(("system", "channel", "outcome"), [
    ("empty", "empty", "empty"),
    ("empty", "failed", "failed"),
    ("failed", "empty", "failed"),
    ("denied", "denied", "denied"),
    ("failed", "denied", "failed"),
    ("empty", "missing", "failed"),
])
def test_no_records_and_a_source_that_did_not_answer_is_not_an_empty_log(no_decoder_launch, system, channel, outcome):
    def make(name: str, kind: str):
        return None if kind == "missing" else source(name, []) if kind == "empty" else failed(name, kind)

    reading = take_both(make("system", system), make("kernel_whea", channel))
    assert reading.outcome == outcome
    if outcome == "empty":
        assert reading.count == 0 and sections(reading)["records"] == [] and "decoded" not in sections(reading)
        assert sections(reading)["coverage"]["complete"] is True
        return
    assert reading.count is None and not reading.observed
    assert [s.name for s in reading.sections] == ["collection", "coverage"]  # why, without an empty list to misread
    assert "did not answer" in reading.error["detail"] and reading.error["kind"] == outcome
    assert sections(reading)["coverage"]["complete"] is False
    if channel == "missing":
        assert sections(reading)["collection"]["sources"]["kernel_whea"]["error"] == "the collector did not return a valid source result"


@pytest.mark.parametrize("failing", ["system", "kernel_whea"])
def test_records_from_one_log_survive_the_other_log_failing(no_decoder_launch, failing):
    rows = {"system": [row(whea.LOG, 3, stamp(1))], "kernel_whea": [row(whea.CHANNEL, 1, stamp(2), raw=cper(1))]}
    answers = {name: failed(name) if name == failing else source(name, rows[name]) for name in rows}
    reading = take_both(answers["system"], answers["kernel_whea"])
    assert reading.outcome == "ok" and reading.count == 1
    failed_log = whea.LOG if failing == "system" else whea.CHANNEL
    assert any(w.startswith(f"{failed_log} did not answer") and "not a complete list" in w for w in reading.warnings)
    coverage = sections(reading)["coverage"]
    assert coverage["complete"] is False and coverage["cutoff"] is None
    assert coverage["sources"][failing]["answered"] is False and coverage["sources"][failing]["complete"] is None


def test_a_query_that_stopped_keeps_its_returned_records_and_bounds_the_list(no_decoder_launch):
    stopped = {"kind": "failed", "detail": "The event log file is corrupted."}
    kept = [row(whea.CHANNEL, 9, stamp(1), raw=cper(9)), row(whea.CHANNEL, 8, stamp(4), raw=cper(8))]
    reading = take_both(source("system", [row(whea.LOG, 70, stamp(30))]), source("kernel_whea", kept, stopped=stopped))
    data = sections(reading)
    assert reading.outcome == "ok" and reading.count == 3
    assert data["collection"]["sources"]["kernel_whea"]["stopped"] == stopped and data["collection"]["sources"]["kernel_whea"]["truncated"] is None
    assert data["coverage"]["complete"] is False and data["coverage"]["cutoff"] == kept[-1]["TimeCreated"]
    assert any("stopped after 2 returned records" in w for w in reading.warnings)


def test_a_source_truncated_at_its_limit_bounds_the_list_even_when_nothing_is_cut(no_decoder_launch):
    channel = [row(whea.CHANNEL, 10 - i, stamp(i + 1), raw=cper(10 - i)) for i in range(3)]
    reading = take_both(source("system", [], limit=3), source("kernel_whea", channel, limit=3, truncated=True), count=3)
    data = sections(reading)
    assert reading.count == 3 and data["collection"]["truncated"] is True
    assert data["coverage"]["sources"]["kernel_whea"]["complete"] is False and data["coverage"]["sources"]["system"]["complete"] is True
    assert data["coverage"]["cutoff"] == channel[-1]["TimeCreated"]


@pytest.mark.parametrize("damage", [
    "returned count", "limit", "truncated without a full page", "empty with rows", "ok without rows",
    "stopped with truncated", "stopped without detail", "error on an answer", "wrong log", "duplicate source",
    "row from another log", "row from another provider", "channel event id", "boolean record id",
    "unreadable time", "unexpected field", "raw payload leaked", "missing message length",
    "wrong header length", "shorter original message",
])
def test_a_source_that_breaks_its_contract_fails_closed(no_decoder_launch, damage):
    good = preview(source("kernel_whea", [row(whea.CHANNEL, 2, stamp(1), raw=cper(2)), row(whea.CHANNEL, 1, stamp(2), raw=cper(1))]))["records"]
    channel = source("kernel_whea", good)
    system = source("system", [])
    if damage == "returned count":
        channel["returned"] = 5
    elif damage == "limit":
        channel["limit"] = 31
    elif damage == "truncated without a full page":
        channel["truncated"] = True
    elif damage == "empty with rows":
        channel["outcome"] = "empty"
    elif damage == "ok without rows":
        channel.update(records=[], returned=0)
    elif damage == "stopped with truncated":
        channel["stopped"] = {"kind": "failed", "detail": "stopped"}
    elif damage == "stopped without detail":
        channel.update(stopped={"kind": "failed", "detail": ""}, truncated=None)
    elif damage == "error on an answer":
        channel["error"] = "something"
    elif damage == "wrong log":
        channel["log"] = whea.LOG
    elif damage == "row from another log":
        good[0]["Log"] = whea.LOG
    elif damage == "row from another provider":
        good[0]["ProviderName"] = whea.PROVIDER
    elif damage == "channel event id":
        good[0]["Id"] = 19
    elif damage == "boolean record id":
        good[0]["RecordId"] = True
    elif damage == "unreadable time":
        good[0]["TimeCreated"] = "yesterday"
    elif damage == "unexpected field":
        good[0]["Serial"] = "x"
    elif damage == "raw payload leaked":
        good[0]["RawData"] = cper(2)
    elif damage == "missing message length":
        good[0].pop("MessageChars")
    elif damage == "wrong header length":
        good[0]["HeaderHex"] = "43"
    elif damage == "shorter original message":
        good[0]["MessageChars"] = 1
    sources = [system, channel, channel] if damage == "duplicate source" else [system, channel]
    reading = asyncio.run(take("whea", FakeBridge(BridgeResult("ok", items=[{"sources": sources}])), {"count": 30}))
    assert reading.outcome == "failed" and reading.count is None
    assert sections(reading)["collection"]["sources"]["kernel_whea"]["outcome"] == "failed"
    assert sections(reading)["collection"]["sources"]["system"]["outcome"] == "empty"


def test_unknown_retention_is_reported_without_losing_the_records(no_decoder_launch):
    reading = take_both(
        source("system", [], oldest_state="failed", log_state="failed"),
        source("kernel_whea", [row(whea.CHANNEL, 1, stamp(1), raw=cper(1))], oldest_state="denied"),
    )
    data = sections(reading)
    assert reading.outcome == "ok" and reading.count == 1
    assert data["coverage"]["sources"]["system"] | {"log": None} == {"log": None, "answered": True, "complete": True, "shown": 0, "retained_from": None, "retention": "failed", "enabled": True}
    assert data["coverage"]["sources"]["kernel_whea"]["retained_from"] is None and data["coverage"]["sources"]["kernel_whea"]["retention"] == "denied"
    assert sum("could not be read; its returned records stand" in w for w in reading.warnings) == 2


def test_metadata_the_collector_did_not_return_is_unknown_retention_not_a_lost_record(no_decoder_launch):
    channel = source("kernel_whea", [row(whea.CHANNEL, 1, stamp(1), raw=cper(1))])
    for key in ("log_enabled", "log_mode", "log_state", "log_error", "log_oldest", "oldest_state", "oldest_error"):
        channel.pop(key)
    reading = take_both(source("system", []), channel)
    assert reading.outcome == "ok" and reading.count == 1
    assert sections(reading)["coverage"]["sources"]["kernel_whea"] | {"log": None} == {"log": None, "answered": True, "complete": True, "shown": 1, "retained_from": None, "retention": None, "enabled": None}
    assert reading.warnings == [f"how far back {whea.CHANNEL} reaches could not be read; its returned records stand, its retention is unknown"]


def test_a_stopped_log_beside_a_failed_log_keeps_its_records_and_says_both(no_decoder_launch):
    stopped = {"kind": "denied", "detail": "Access is denied."}
    reading = take_both(failed("system"), source("kernel_whea", [row(whea.CHANNEL, 4, stamp(1), raw=cper(4))], stopped=stopped))
    assert reading.outcome == "ok" and reading.count == 1
    assert [w.split(":")[0] for w in reading.warnings] == [f"{whea.LOG} did not answer", f"{whea.CHANNEL} stopped after 1 returned records"]
    assert sections(reading)["coverage"]["complete"] is False and sections(reading)["coverage"]["cutoff"] == sections(reading)["records"][0]["TimeCreated"]


def test_an_empty_log_has_no_reach_and_a_disabled_log_is_not_evidence(no_decoder_launch):
    reading = take_both(source("system", [], oldest_state="empty"), source("kernel_whea", [], enabled=False))
    data = sections(reading)
    assert reading.outcome == "empty"
    assert data["coverage"]["sources"]["system"]["retained_from"] is None and data["coverage"]["sources"]["system"]["retention"] == "empty"
    assert data["coverage"]["sources"]["kernel_whea"]["retained_from"] == CHANNEL_OLDEST
    assert reading.warnings == [f"{whea.CHANNEL} is disabled: Windows is not recording new events there, so its absence of records is not evidence"]


def test_exact_system_record_carries_its_decoded_structure_or_reason(monkeypatch):
    records = [{**r, "Log": whea.LOG} for r in load(groups={"burst"})[:2]]
    records[0]["RawData"] = minimal_cper()
    other = bytearray.fromhex(minimal_cper())
    other[12] = 1  # a second structurally valid payload, so each fake answer has its own input
    records[1]["RawData"] = other.hex()
    monkeypatch.setattr(whea, "DECODER", str(FIXTURE))  # present, but never actually run
    answers = {
        records[0]["RawData"].upper(): (json.dumps({"SectionCount": 1, "ErrorSeverity": "Corrected"}), "", 0, None),
        records[1]["RawData"].upper(): ("", "Hexadecimal string is not a valid CPER record", 1, None),
    }
    monkeypatch.setattr(whea, "_run_decoder", lambda payload, timeout: answers[payload])

    first = exact_record(source("system", [records[0]], limit=1), record_id=records[0]["RecordId"], source_name="system")
    second = exact_record(source("system", [records[1]], limit=1), record_id=records[1]["RecordId"], source_name="system")
    assert first.outcome == second.outcome == "ok"
    assert first.section("decoded").basis.startswith(whea.DECODED_BASIS)
    assert sections(first)["decoded"] == [{"Log": whea.LOG, "RecordId": records[0]["RecordId"], "decoded": {"SectionCount": 1, "ErrorSeverity": "Corrected"}}]
    assert sections(second)["decoded"][0]["error"] == "Hexadecimal string is not a valid CPER record"


def test_a_truncated_preview_does_not_decode_its_records(monkeypatch):
    records = [{**r, "Log": whea.LOG, "RawData": minimal_cper()} for r in load(groups={"burst"})[:2]]
    calls = []
    monkeypatch.setattr(whea, "DECODER", str(FIXTURE))
    monkeypatch.setattr(whea, "_run_decoder", lambda payload, timeout: (calls.append(payload) or '{}', "", 0, None))
    reading = take_both(source("system", records, limit=2, truncated=True), source("kernel_whea", [], limit=2), count=2)
    assert reading.count == 2 and sorted(r["RecordId"] for r in sections(reading)["records"]) == sorted(r["RecordId"] for r in records)
    assert sections(reading)["collection"]["truncated"] is True
    assert "decoded" not in sections(reading) and calls == []


def test_identical_cper_payloads_are_decoded_once_and_keep_their_own_record_ids(monkeypatch):
    calls = []
    monkeypatch.setattr(whea, "DECODER", str(FIXTURE))
    monkeypatch.setattr(whea, "_run_decoder", lambda payload, timeout: (calls.append(payload) or '{}', "", 0, None))
    payload = minimal_cper()
    decoded, warnings = whea.decode_all([{"Log": whea.LOG, "RecordId": 41, "RawData": payload}, {"Log": whea.LOG, "RecordId": 42, "RawData": payload}])
    assert not warnings and len(calls) == 1
    assert decoded == [{"Log": whea.LOG, "RecordId": 41, "decoded": {}}, {"Log": whea.LOG, "RecordId": 42, "decoded": {}}]


def test_a_record_without_a_payload_never_reaches_the_decoder(monkeypatch):
    monkeypatch.setattr(whea, "DECODER", str(FIXTURE))
    monkeypatch.setattr(whea, "_run_decoder", lambda payload, timeout: pytest.fail("the decoder was invoked without a payload"))
    entry = whea.decode_record({"RecordId": 5, "RawData": None}, deadline=time.monotonic() + 10)
    assert entry == {"RecordId": 5, "error": "the record carries no binary payload"}


def test_a_payload_longer_than_the_command_line_is_an_error_on_that_record_only(monkeypatch):
    monkeypatch.setattr(whea, "DECODER", str(FIXTURE))
    monkeypatch.setattr(whea, "_run_decoder", lambda payload, timeout: pytest.fail("the decoder was invoked with an oversized payload"))
    entry = whea.decode_record({"RecordId": 6, "RawData": "AB" * whea.MAX_HEX}, deadline=time.monotonic() + 10)
    assert entry["RecordId"] == 6 and "longer than the decoder takes" in entry["error"]


def test_a_spent_budget_stops_decoding_without_failing_the_reading(monkeypatch):
    monkeypatch.setattr(whea, "DECODER", str(FIXTURE))
    entry = whea.decode_record({"RecordId": 7, "RawData": "435045520000000A"}, deadline=time.monotonic() - 1)
    assert entry["RecordId"] == 7 and "budget" in entry["error"]


def test_a_missing_decoder_does_not_affect_the_preview(monkeypatch):
    records = [{**r, "Log": whea.LOG} for r in load(groups={"burst"})[:2]]
    monkeypatch.setattr(whea, "DECODER", str(FIXTURE.parent / "no-decoder-here.exe"))
    reading = take_both(source("system", records), source("kernel_whea", []))
    assert reading.outcome == "ok"
    assert "decoded" not in sections(reading)
    assert reading.warnings == []


def test_a_failed_query_decodes_nothing_and_says_what_failed():
    reading = asyncio.run(take("whea", FakeBridge(BridgeResult("failed", error="the log is not there")), {}))
    assert reading.outcome == "failed" and reading.sections == []
    assert reading.error == {"kind": "failed", "detail": "the log is not there"}


# ---------------------------------------------------------------- storms: the window


def test_the_window_is_aligned_to_bucket_boundaries_and_ends_with_the_current_bucket():
    window = whea.window_for(24, 60, now=1_758_000_123.75)
    assert window.count == 1440
    assert window.start % 60 == 0 and window.end - window.start == 1440 * 60
    assert window.index(window.start) == 0 and window.index(window.end - 1) == 1439
    assert window.index(window.start - 1) is None and window.index(window.end) is None


@pytest.mark.parametrize(
    "params",
    [{"hours": 0}, {"hours": whea.MAX_HOURS + 1}, {"bucket_seconds": 0}, {"bucket_seconds": 1}, {"burst_threshold": 0}, {"accel_threshold": 0}],
)
def test_a_window_that_cannot_be_counted_is_refused_before_the_machine_is_asked(params):
    with pytest.raises(ValueError):
        storms([], **params)


def test_the_query_bounds_the_window_by_the_logs_own_index():
    window = whea.window_for(24, 60, now=1_758_000_123.75)
    script = whea.storms_script(window)
    anchored = whea.storms_script(window, before="2026-09-22T00:00:00Z")
    assert "[datetimeoffset]::Parse('2026-09-22T00:00:00.000Z', [Globalization.CultureInfo]::InvariantCulture)" in anchored
    assert "Microsoft-Windows-WHEA-Logger" in script and "$currentBucketTicks" in script
    assert f"$bucketTicks = [long]{window.bucket_seconds}" in script and f"([long]({window.count} - 1)" in script
    assert f"-MaxEvents {whea.RECORD_CAP + 1}" in script and "NoMatchingEventsFound" in script
    assert "$queryUntilIso = $until.AddMilliseconds(1)" in script
    assert "@SystemTime&lt;'$queryUntilIso'" in script
    assert "Read-LogMetadata 'System'" in script
    assert "RawData" not in script  # the payload is whea's business, not the storm rule's


# ---------------------------------------------------------------- storms: buckets and signatures


def test_every_wall_clock_bucket_is_counted_including_the_idle_ones():
    reading = storms(load())
    assert reading.outcome == "ok" and reading.count == 40
    buckets = reading.section("buckets")
    assert buckets.cls == "derived" and buckets.basis
    assert len(buckets.data["totals"]) == buckets.data["bucket_count"] == 1440 and buckets.data["bucket_seconds"] == 60
    assert buckets.data["total"] == 40 == sum(buckets.data["totals"])
    returned = buckets.data["returned"]
    assert len(returned["index"]) == 29  # the other 1411 minutes are idle and counted as zero
    assert returned["count"] == [t for t in buckets.data["totals"] if t]
    assert returned["index"][-1] < buckets.data["bucket_count"]
    assert sum(buckets.data["severity"].values()) == reading.count
    pairs = buckets.data["signature_pairs"]
    signatures = reading.section("signatures").data
    assert sum(pairs["count"]) == buckets.data["total"]
    assert {signatures[index]["id"] for index in pairs["signature"]} == {row["id"] for row in signatures}
    for index, count in zip(pairs["index"], pairs["count"], strict=True):
        assert index in returned["index"] and 0 < count <= returned["count"][returned["index"].index(index)]


@pytest.mark.parametrize("many_signatures", [False, True])
def test_busy_storm_bucket_answer_is_smaller_even_when_signatures_are_distinct(many_signatures: bool):
    start = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())
    window = whea.Window(start, 60, 2000)
    header = cper(1)[:256]
    records = [{"RecordId": i + 1, "Id": 18, "TimeCreated": whea._stamp(start + i * 60 + 30),
                "Message": f"Memory Error Bank: {i if many_signatures else 5}", "HeaderHex": header,
                "PayloadBytes": 200, "LevelDisplayName": "Error"} for i in range(window.count)]
    sections = {section.name: section.data for section in whea.compose(
        records, window, {"burst_threshold": 5, "accel_threshold": 2.0},
        {"covered_from": whea._stamp(start - 60), "covered_from_inclusive": True},
    )}
    compact = {"buckets": sections["buckets"], "signatures": sections["signatures"]}
    legacy = json.loads(json.dumps(compact))
    buckets = legacy["buckets"]
    returned, pairs = buckets.pop("returned"), buckets.pop("signature_pairs")
    buckets.pop("severity")
    per_index: dict[int, dict[str, int]] = {}
    for index, signature_index, count in zip(pairs["index"], pairs["signature"], pairs["count"], strict=True):
        per_index.setdefault(index, {})[legacy["signatures"][signature_index]["id"]] = count
    buckets["active"] = [{"index": index, "start": whea._stamp(start + index * 60),
                           "total": count, "complete": buckets["totals"][index] is not None,
                           "previous_session": prior, "header_unreadable": missing,
                           "signatures": per_index[index]}
                          for index, count, prior, missing in zip(returned["index"], returned["count"],
                                                                  returned["previous_session"], returned["header_unreadable"], strict=True)]
    def encode(value: Any) -> int:
        return len(json.dumps(value, separators=(",", ":")))

    assert encode(compact) < encode(legacy) * (0.9 if many_signatures else 0.25)


def test_records_group_into_signatures_ranked_by_how_often_they_recur():
    signatures = storms(load()).section("signatures").data
    assert [s["count"] for s in signatures] == [24, 9, 6, 1]
    assert [s["error_type"] for s in signatures] == ["Memory Error", "PCIe Error", "Cache Error", "Unknown"]
    assert len({s["id"] for s in signatures}) == 4

    memory, pcie, _, unknown = signatures
    assert (memory["bank"], memory["apic_id"], memory["mci_status"]) == ("5", "0", "0xcc20008000010092")
    assert (pcie["vendor_id"], pcie["device_id"]) == ("1022", "1483")  # the old expression could not read the 0x prefix
    assert unknown["bank"] is None and unknown["apic_id"] == "6"
    assert memory["first_seen"] < memory["last_seen"] and memory["sample"]["Message"]
    assert all(s["key"].startswith(whea.SIGNATURE_VERSION + "|ET:") for s in signatures)


def test_storm_keeps_one_bounded_reference_per_returned_report():
    now = time.time()
    rows = load(now=now)[:2]
    rows.append({**rows[0], "RecordId": None, "TimeCreated": None})
    reading = storms(rows, host_now=now, oldest=_powershell_stamp(now - 2 * 86400), references=True)
    references = reading.section("reports").data
    buckets = reading.section("buckets").data
    signature_ids = {entry["id"] for entry in reading.section("signatures").data}
    assert len(references) == reading.count == buckets["total"] + buckets["unplaced"] == 3
    assert all(reference["signature_id"] in signature_ids for reference in references[:2])
    assert references[2]["record_id"] is None and references[2]["reported_at"] is None
    assert references[2]["signature_id"] is None
    assert all(reference["event_id"] == row["Id"] for reference, row in zip(references, rows, strict=True))
    assert all(set(reference) == {"record_id", "reported_at", "event_id", "signature_id", "header", "header_error"}
               for reference in references)
    assert "Message" not in json.dumps(references) and "HeaderHex" not in json.dumps(references)


def test_storm_references_are_opt_in_and_unreadable_times_are_null():
    now = time.time()
    row = {**load(now=now)[0], "TimeCreated": "unreadable-time"}
    default = storms([row], host_now=now)
    detailed = storms([row], host_now=now, references=True)
    assert default.section("reports") is None and default.count == 1
    assert detailed.section("reports").data[0]["reported_at"] is None
    assert detailed.section("reports").data[0]["signature_id"] is None
    assert detailed.section("buckets").data["unplaced"] == 1


def test_the_signature_is_the_error_not_the_instance():
    one = whea.signature({"Message": "Cache Hierarchy Error\nProcessor APIC ID: 4\nBank: 1\nMCI Status: 0xbea0000001000108\nAddress: 0xffff8001"})
    same = whea.signature({"Message": "Cache Hierarchy Error\nProcessor APIC ID: 4\nBank: 1\nMCI Status: 0xbea0000001000108\nAddress: 0xdead0002"})
    other = whea.signature({"Message": "Cache Hierarchy Error\nProcessor APIC ID: 7\nBank: 1\nMCI Status: 0xbea0000001000108\nAddress: 0xffff8001"})
    assert one.id == same.id != other.id
    assert len(one.id) == whea.SIGNATURE_ID_CHARS
    assert whea.normalize("value 0x1234 {01234567-89ab-cdef-0123-456789abcdef}  spaced") == "value <HEX> <GUID> spaced"


# ---------------------------------------------------------------- storms: the status rule


def test_a_minute_over_the_threshold_reads_as_a_burst():
    status = storms(load()).section("status")
    assert status.cls == "inferred" and status.basis
    assert status.data["state"] == "burst" and status.data["severity"] == "warning"
    assert status.data["peak_rate"] == 6.0 and "burst threshold is 5" in status.data["reason"]
    assert len(status.data["dominant"]) == 2  # only the recent window's signatures


def test_a_peak_over_twice_the_threshold_is_critical():
    status = storms(load(), burst_threshold=2).section("status").data
    assert status["state"] == "burst" and status["severity"] == "critical"


@pytest.mark.parametrize("kinds,composition,at_most,classified", [
    (["previous"] * 15, (15, 0, 0), 0, False),
    (["previous"] * 12 + ["not_marked"] * 3, (12, 3, 0), 3, False),
    (["previous"] * 10 + ["no_payload"] * 6, (10, 0, 6), 6, None),
    (["previous"] * 6 + ["not_marked"] * 6, (6, 6, 0), 6, True),
    (["no_payload"] * 6, (0, 0, 6), 6, None),
])
def test_report_traffic_and_not_marked_burst_remain_separate(kinds, composition, at_most, classified):
    host_now = datetime(2026, 9, 23, 18, 30, 30, tzinfo=UTC).timestamp()
    base = load(now=host_now)[0]
    rows = []
    for record_id, kind in enumerate(kinds, 1):
        payload = cper(record_id, flags=2 if kind == "previous" else 0) if kind != "no_payload" else None
        rows.append({**base, "RecordId": record_id, "TimeCreated": _powershell_stamp(host_now - 5),
                     "HeaderHex": payload[:256] if payload else None,
                     "PayloadBytes": len(payload) // 2 if payload else None})
    reading = storms(rows, host_now=host_now, oldest=_powershell_stamp(host_now - 2 * 86400))
    buckets = reading.section("buckets").data
    status = reading.section("status").data
    assert reading.outcome == "ok" and status["state"] == "burst"  # all reports filed in one bucket
    assert status["recent_composition"] == dict(zip(("previous_session", "not_marked", "header_unreadable"), composition, strict=True))
    assert status["not_marked_peak"] == {"at_least": composition[1], "at_most": at_most}
    assert status["not_marked_burst"] is classified
    assert buckets["previous_session"] == composition[0] and buckets["header_unreadable"] == composition[2]
    assert buckets["returned"]["previous_session"][-1] == composition[0]
    assert sum(signature["previous_session"] for signature in reading.section("signatures").data) == composition[0]
    assert all(signature["sample"]["previous_session"] is (True if kinds[-1] == "previous" else None if kinds[-1] == "no_payload" else False)
               for signature in reading.section("signatures").data)
    assert "43504552" not in json.dumps(reading.to_dict())  # no projected CPER bytes in an API reading


def test_storm_header_failures_keep_returned_reports_and_explain_unknown_flags():
    host_now = datetime(2026, 9, 23, 18, 30, 30, tzinfo=UTC).timestamp()
    base = load(now=host_now)[0]
    valid = cper(1, flags=2)
    bad = "00" + valid[2:]
    rows = [
        {**base, "RecordId": 1, "HeaderHex": valid[:256], "PayloadBytes": len(valid) // 2},
        {**base, "RecordId": 2, "HeaderHex": "43504552", "PayloadBytes": 4},
        {**base, "RecordId": 3, "HeaderHex": bad[:256], "PayloadBytes": len(bad) // 2},
        {**base, "RecordId": 4, "HeaderHex": None, "PayloadBytes": None, "TimeCreated": None},
    ]
    reading = storms(rows, host_now=host_now, oldest=_powershell_stamp(host_now - 2 * 86400))
    buckets = reading.section("buckets").data
    assert reading.count == 4 and buckets["total"] == 3 and buckets["unplaced"] == 1
    assert buckets["previous_session"] == 1 and buckets["header_unreadable"] == 3
    assert buckets["severity"]["unreadable"] == buckets["header_unreadable"]
    assert buckets["header_unreadable_reasons"] == {"no_payload": 1, "short_payload": 1, "invalid_header": 1}
    assert buckets["returned"]["previous_session"][-1] == 1 and buckets["returned"]["header_unreadable"][-1] == 2
    assert reading.section("status").data["not_marked_burst"] is None
    assert any("3 returned System reports have no readable fixed CPER header" in warning for warning in reading.warnings)
    assert "HeaderHex" not in json.dumps(reading.to_dict()["sections"])


def test_historical_storm_keeps_fixed_header_evidence_without_live_classification():
    host_now = datetime(2026, 9, 23, 18, 30, 30, tzinfo=UTC).timestamp()
    anchor = host_now - 3600
    base = load(now=anchor)[0]
    marked = cper(1, flags=2)
    row = {**base, "TimeCreated": _powershell_stamp(anchor - 30),
           "HeaderHex": marked[:256], "PayloadBytes": len(marked) // 2}
    reading = storms([row], host_now=host_now, before=_powershell_stamp(anchor),
                     oldest=_powershell_stamp(anchor - 2 * 86400), references=True)
    assert reading.section("status") is None
    assert reading.section("buckets").data["previous_session"] == 1
    assert reading.section("buckets").data["returned"]["previous_session"][-1] == 1
    assert reading.section("signatures").data[0]["sample"]["previous_session"] is True
    assert reading.section("reports").data[0]["header"]["previous_session"] is True


@pytest.mark.parametrize("returned,expected", [(1, None), (6, True)])
def test_incomplete_recent_retention_cannot_falsely_clear_not_marked_reports(returned, expected):
    host_now = datetime(2026, 9, 23, 18, 30, 30, tzinfo=UTC).timestamp()
    base = load(now=host_now)[0]
    payload = cper(flags=0)
    rows = [{**base, "RecordId": index + 1, "TimeCreated": _powershell_stamp(host_now - 5),
             "HeaderHex": payload[:256], "PayloadBytes": len(payload) // 2} for index in range(returned)]
    reading = storms(rows, host_now=host_now, oldest=_powershell_stamp(host_now - 5 * 60))
    status = reading.section("status").data
    assert status["not_marked_peak"] == {"at_least": returned, "at_most": None}
    assert status["not_marked_burst"] is expected


@pytest.mark.parametrize("projection", [
    {"HeaderHex": "00", "PayloadBytes": 200},
    {"HeaderHex": "GG", "PayloadBytes": 1},
    {"HeaderHex": "", "PayloadBytes": True},
    {"HeaderHex": None, "PayloadBytes": 0},
    {"PayloadBytes": 0},
    {"HeaderHex": ""},
])
def test_broken_storm_header_projection_fails_closed(projection):
    row = {**load()[0], **projection}
    if "HeaderHex" not in projection:
        row.pop("HeaderHex", None)
    if "PayloadBytes" not in projection:
        row.pop("PayloadBytes", None)
    reading = storms([row])
    assert reading.outcome == "failed" and reading.count is None
    assert reading.section("buckets") is None


def test_a_rising_rate_against_a_quiet_baseline_reads_as_accelerating():
    status = storms(load(groups={"baseline", "tail"})).section("status").data
    assert status["state"] == "accelerating" and status["severity"] == "warning"
    assert status["peak_rate"] == 3.0 and status["recent_rate"] == 0.9 and status["baseline_rate"] == 0.1
    assert status["acceleration"] == 9.0 and status["baseline_buckets"] == 240
    assert "x the baseline" in status["reason"]


def test_a_baseline_alone_is_quiet_and_says_which_rule_did_not_fire():
    status = storms(load(groups={"baseline"})).section("status").data
    assert status["state"] == "quiet" and status["severity"] is None
    assert status["peak_rate"] == 0.0 and status["recent_rate"] == 0.0 and status["dominant"] == []
    assert "no bucket reached the burst threshold" in status["reason"]


def test_a_rate_below_the_noise_floor_does_not_accelerate():
    status = storms(load(groups={"baseline"}) + load(groups={"tail"})[:4]).section("status").data
    assert status["recent_rate"] == 0.4 and status["acceleration"] >= 2.0
    assert status["state"] == "quiet"  # four records in ten minutes is noise, not a trend


def test_an_empty_window_is_a_finding_with_the_buckets_still_there():
    reading = storms([], outcome="empty")
    assert reading.outcome == "empty" and reading.count == 0
    buckets = reading.section("buckets").data
    assert len(buckets["totals"]) == 1440 and buckets["total"] == 0 and buckets["returned"]["index"] == []
    assert reading.section("signatures").data == []
    status = reading.section("status").data
    assert status["state"] == "quiet" and status["reason"] == "the window holds no WHEA-Logger records"


def test_retention_gap_is_unknown_buckets_and_cannot_read_as_quiet():
    oldest = _powershell_stamp(time.time() - 12 * 3600)
    reading = storms([], outcome="empty", oldest=oldest)
    assert reading.outcome == "empty" and reading.count == 0
    totals = reading.section("buckets").data["totals"]
    assert totals[0] is None and totals[-1] == 0
    assert reading.section("buckets").data["unknown_buckets"] > 0
    assert reading.section("coverage").data["system"]["complete"] is False
    assert reading.section("status").data["state"] == "unknown"
    assert "no WHEA-Logger records" not in reading.section("status").data["reason"]


def test_a_burst_of_returned_records_survives_a_retention_gap():
    reading = storms(load(), oldest=_powershell_stamp(time.time() - 12 * 3600))
    assert reading.section("coverage").data["system"]["complete"] is False
    assert reading.section("status").data["state"] == "burst"


@pytest.mark.parametrize("skew_seconds", [-3600, 3600])
def test_host_clock_aligns_the_query_and_buckets_even_when_python_time_differs(skew_seconds):
    host_now = time.time() + skew_seconds
    reading = storms(load(now=host_now), host_now=host_now)
    window = whea.window_for(24, 60, now=host_now)
    buckets = reading.section("buckets").data
    assert reading.outcome == "ok" and buckets["from"] == whea._stamp(window.start)
    assert buckets["total"] == reading.count == 40 and buckets["unplaced"] == 0
    assert reading.section("status").data["state"] == "burst"


def test_an_exact_end_boundary_uses_the_preceding_bucket_without_a_collector_failure():
    instant = whea.window_for(1, 3600, now=time.time()).end
    reading = storms([], outcome="empty", host_now=instant, hours=1, bucket_seconds=3600)
    assert reading.outcome == "empty" and reading.count == 0
    assert reading.section("buckets").data["bucket_count"] == 1
    assert reading.section("buckets").data["to"] == whea._stamp(instant)
    assert reading.section("buckets").data["from"] == whea._stamp(instant - 3600)
    assert reading.section("status").data["state"] == "unknown"  # no baseline for a trend


def test_historical_storm_window_keeps_evidence_without_a_live_status():
    query_time = datetime(2026, 9, 23, 18, 30, tzinfo=UTC).timestamp()
    anchor_time = query_time - 3 * 86400
    reading = storms(load(now=anchor_time), host_now=query_time, before=_powershell_stamp(anchor_time),
                     oldest=_powershell_stamp(anchor_time - 2 * 86400))
    data = sections(reading)
    assert reading.outcome == "ok" and reading.count == 40
    assert "status" not in data
    assert data["buckets"]["total"] == 40 and sum(signature["count"] for signature in data["signatures"]) == 40
    assert data["collection"]["window_end"] == _powershell_stamp(anchor_time)
    assert data["collection"]["queried_at"] == _powershell_stamp(query_time)
    assert data["coverage"]["system"]["covered_until"] == _powershell_stamp(anchor_time)
    assert data["coverage"]["system"]["complete"] is True


def test_historical_storm_end_on_bucket_boundary_uses_preceding_bucket():
    query_time = datetime(2026, 9, 23, 18, 30, tzinfo=UTC).timestamp()
    anchor_time = query_time - 3 * 86400 - 1800
    reading = storms([], outcome="empty", host_now=query_time, hours=1, bucket_seconds=3600,
                     before=_powershell_stamp(anchor_time), oldest=_powershell_stamp(anchor_time - 86400))
    assert reading.outcome == "empty"
    assert reading.section("buckets").data["from"] == whea._stamp(anchor_time - 3600)
    assert reading.section("buckets").data["to"] == whea._stamp(anchor_time)
    assert reading.section("status") is None


def test_historical_storm_partial_final_bucket_ends_at_the_anchor():
    query_time = datetime(2026, 9, 23, 18, 30, tzinfo=UTC).timestamp()
    anchor_time = query_time - 3 * 86400 + 30
    reading = storms([], outcome="empty", host_now=query_time, hours=1, bucket_seconds=60,
                     before=_powershell_stamp(anchor_time), oldest=_powershell_stamp(anchor_time - 86400))
    buckets = reading.section("buckets").data
    assert reading.outcome == "empty" and buckets["to"] == whea._stamp(anchor_time + 30)
    assert reading.section("collection").data["window_end"] == _powershell_stamp(anchor_time)
    assert reading.section("coverage").data["system"]["covered_until"] == _powershell_stamp(anchor_time)
    assert buckets["totals"][-1] == 0 and reading.section("status") is None


def test_historical_storm_excludes_rows_at_or_just_after_its_exclusive_end():
    query_time = datetime(2026, 9, 23, 18, 30, tzinfo=UTC).timestamp()
    anchor_time = query_time - 3 * 86400
    base = load(now=anchor_time)[0]
    rows = [{**base, "TimeCreated": _powershell_stamp(anchor_time + offset), "Message": "PRIVATE_OUTSIDE_MARKER"}
            for offset in (0, 0.0005)]
    reading = storms(rows, host_now=query_time, before=_powershell_stamp(anchor_time),
                     oldest=_powershell_stamp(anchor_time - 2 * 86400), references=True)
    assert reading.outcome == "empty" and reading.count == 0
    assert reading.section("collection").data["system"]["row_issues"]["outside_window"] == 2
    assert reading.section("buckets").data["total"] == 0
    assert reading.section("reports").data == []
    assert "PRIVATE_OUTSIDE_MARKER" not in json.dumps(reading.to_dict())


def test_future_storm_anchor_reports_only_observed_reach_and_incomplete_coverage():
    query_time = datetime(2026, 9, 23, 18, 30, tzinfo=UTC).timestamp()
    reading = storms([], outcome="empty", host_now=query_time, before=_powershell_stamp(query_time + 3600),
                     oldest=_powershell_stamp(query_time - 2 * 86400))
    assert reading.outcome == "empty" and reading.section("status") is None
    assert reading.section("collection").data["window_end"] == _powershell_stamp(query_time)
    reach = reading.section("coverage").data["system"]
    assert reach["covered_until"] == _powershell_stamp(query_time) and reach["complete"] is False
    assert any("requested end is after" in warning for warning in reading.warnings)


def test_storm_window_before_retained_history_has_one_clear_retention_warning():
    query_time = datetime(2026, 9, 23, 18, 30, tzinfo=UTC).timestamp()
    anchor_time = query_time - 3 * 86400
    reading = storms([], outcome="empty", host_now=query_time, before=_powershell_stamp(anchor_time),
                     oldest=_powershell_stamp(anchor_time + 3600))
    assert reading.outcome == "empty" and reading.section("status") is None
    assert all(total is None for total in reading.section("buckets").data["totals"])
    reach = reading.section("coverage").data["system"]
    assert reach["complete"] is False and reach["covered_from"] is None and reach["covered_until"] is None
    assert len(reading.warnings) == 1 and "retained history begins at or after" in reading.warnings[0]


def test_entirely_future_storm_window_fails_instead_of_looking_quiet():
    query_time = datetime(2026, 9, 23, 18, 30, tzinfo=UTC).timestamp()
    reading = storms([], outcome="empty", host_now=query_time, hours=1,
                     before=_powershell_stamp(query_time + 7200))
    assert reading.outcome == "failed" and "begins at or after" in reading.error["detail"]
    assert reading.section("buckets") is None


def test_storm_collector_requires_host_query_time():
    reading = storms([], outcome="empty", omit_queried_at=True)
    assert reading.outcome == "failed" and "query time" in reading.error["detail"]
    assert reading.section("buckets") is None


@pytest.mark.parametrize("before", ["2026-09-23T18:30:00", "not-a-time", "1969-12-31T23:00:00Z"])
def test_storm_anchor_requires_a_zoned_post_epoch_time(before):
    with pytest.raises(ValueError, match="parameter 'before'"):
        storms([], before=before)


def test_storm_http_boundary_rejects_an_unzoned_anchor_before_querying():
    bridge = FakeBridge(BridgeResult("failed", error="the bridge must not be asked"),
                        by_marker={"$env:COMPUTERNAME": identity_result("SENTINEL-FIXTURE", "person")})
    token = "synthetic-test-token-0123456789"
    with TestClient(create_app(State(bridge=bridge, token=token))) as client:
        response = client.get("/api/readings/storms", params={"before": "2026-09-23T18:30:00"},
                              headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422 and "parameter 'before'" in response.json()["detail"]


def test_a_misaligned_host_window_fails_with_its_own_reason():
    reading = storms([], outcome="empty", start_shift=1)
    assert reading.outcome == "failed" and reading.count is None
    assert "window bounds" in reading.error["detail"]
    assert reading.section("buckets") is None


def test_one_extra_record_makes_the_storm_cap_exact(monkeypatch):
    monkeypatch.setattr(whea, "RECORD_CAP", 3)
    script = whea.storms_script(whea.window_for(24, 60))
    assert "-MaxEvents 4" in script
    reading = storms(load(groups={"tail"})[:3], truncated=True, references=True)
    assert reading.outcome == "ok" and reading.count == 3
    assert len(reading.section("reports").data) == 3
    assert reading.section("collection").data["system"]["truncated"] is True
    assert reading.section("coverage").data["system"]["covered_from_inclusive"] is False
    assert reading.section("buckets").data["unknown_buckets"] > 0
    assert reading.section("status").data["state"] == "unknown"


def test_a_stopped_query_keeps_returned_burst_evidence_but_not_the_older_quiet_claim():
    stopped = {"kind": "failed", "detail": "synthetic event query interruption"}
    moment = time.time()
    rows = load(now=moment)
    reading = storms(rows, host_now=moment, stopped=stopped)
    source = reading.section("collection").data["system"]
    coverage = reading.section("coverage").data["system"]
    buckets = reading.section("buckets").data
    assert reading.outcome == "ok" and reading.count == 40
    assert source["stopped"] == stopped and source["truncated"] is None and source["error"] is None
    assert coverage["complete"] is False and coverage["covered_from_inclusive"] is False
    assert coverage["covered_from"] == min(row["TimeCreated"] for row in rows)
    assert buckets["totals"][0] is None and buckets["unknown_buckets"] > 0
    assert reading.section("status").data["state"] == "burst"
    assert any("stopped after 40" in warning for warning in reading.warnings)
    assert not any("retention" in warning for warning in reading.warnings)
    retained_gap = storms(rows, host_now=moment, stopped=stopped, oldest=_powershell_stamp(moment - 12 * 3600))
    assert any("retention" in warning for warning in retained_gap.warnings)
    assert any("stopped after" in warning for warning in retained_gap.warnings)


def test_a_stopped_query_detail_is_redacted_before_a_handoff():
    from sentinel.redact import redact
    from sentinel.stack import _item_lines

    reading = storms(load()[:1], stopped={"kind": "failed", "detail": r"C:\Users\SentinelPrivateName\Desktop\synthetic.log"})
    body, _ = redact(reading.to_dict())
    handoff = "\n".join(_item_lines(1, {"kind": "reading", "title": "Synthetic storm", "reading": body, "verbosity": "summary"}))
    assert "SentinelPrivateName" not in handoff and "<user>" in handoff
    assert '"stopped": {' in handoff and '"detail":' in handoff


def test_unreadable_event_time_counts_the_event_without_claiming_any_bucket_quiet():
    row = {**load()[0], "RecordId": None, "TimeCreated": "unreadable-time", "Message": "PRIVATE_UNPLACED_MARKER"}
    reading = storms([row])
    source = reading.section("collection").data["system"]
    buckets = reading.section("buckets").data
    assert reading.outcome == "ok" and reading.count == 1
    assert source["row_issues"]["unplaced"] == 1
    assert buckets["unplaced"] == 1 and buckets["total"] == 0
    assert all(value is None for value in buckets["totals"])
    assert reading.section("coverage").data["system"]["complete"] is False
    assert reading.section("status").data["state"] == "unknown"
    assert "PRIVATE_UNPLACED_MARKER" not in json.dumps(reading.to_dict())
    assert not any("retention" in warning for warning in reading.warnings)
    retained_gap = storms([row], oldest=_powershell_stamp(time.time() - 12 * 3600))
    assert any("retention" in warning for warning in retained_gap.warnings)

    no_zone = storms([{**row, "TimeCreated": "2026-09-22T10:00:00.1234567"}])
    assert no_zone.section("collection").data["system"]["row_issues"]["unplaced"] == 1
    assert no_zone.section("buckets").data["unplaced"] == 1
    assert all(value is None for value in no_zone.section("buckets").data["totals"])
    assert no_zone.section("status").data["state"] == "unknown"


def test_a_placed_burst_survives_an_untimed_event_without_claiming_acceleration():
    rows = load()
    rows.append({**rows[0], "RecordId": None, "TimeCreated": None, "Message": "PRIVATE_UNTIMED_MARKER"})
    reading = storms(rows)
    buckets = reading.section("buckets").data
    status = reading.section("status").data
    assert reading.count == 41 and buckets["unplaced"] == 1 and buckets["total"] == 40
    assert all(value is None for value in buckets["totals"])
    assert status["state"] == "burst" and status["acceleration"] is None
    assert "PRIVATE_UNTIMED_MARKER" not in json.dumps(reading.to_dict())


def test_query_precision_drops_only_near_boundary_rows_without_retaining_their_content():
    now = time.time()
    start = whea.window_for(24, 60, now=now).start
    row = {**load(now=now)[0], "TimeCreated": _powershell_stamp(start - 0.0005), "Message": "PRIVATE_OUTSIDE_MARKER"}
    reading = storms([row], host_now=now)
    assert reading.outcome == "empty" and reading.count == 0
    source = reading.section("collection").data["system"]
    assert source["outcome"] == "ok" and source["row_issues"]["outside_window"] == 1
    assert reading.section("coverage").data["system"]["complete"] is True
    assert "PRIVATE_OUTSIDE_MARKER" not in json.dumps(reading.to_dict())
    far = storms([{**row, "TimeCreated": _powershell_stamp(start - 300)}], host_now=now)
    assert far.outcome == "failed" and far.section("buckets") is None
    stopped = storms([row], host_now=now, stopped={"kind": "denied", "detail": "synthetic stop"})
    assert stopped.outcome == "denied" and stopped.count is None
    stopped_source = stopped.section("collection").data["system"]
    assert stopped_source["row_issues"]["outside_window"] == 1 and stopped_source["stopped"]["kind"] == "denied"
    assert "PRIVATE_OUTSIDE_MARKER" not in json.dumps(stopped.to_dict())


def test_a_stopped_query_with_no_countable_records_and_a_bad_row_contract_fail_closed():
    stopped = {"kind": "failed", "detail": "synthetic stop"}
    assert storms([], outcome="empty", stopped=stopped).outcome == "failed"
    invalid = storms([{**load()[0], "Id": "17"}])
    assert invalid.outcome == "failed" and invalid.section("buckets") is None
    projected = {key: value for key, value in load()[0].items() if key in whea.STORM_ROW_KEYS}
    unexpected = storms([{**projected, "SensitiveUnexpectedField": "PRIVATE_MARKER"}], raw_rows=True)
    assert unexpected.outcome == "failed" and "PRIVATE_MARKER" not in json.dumps(unexpected.to_dict())

    now = time.time()
    first = _powershell_stamp(whea.window_for(24, 60, now=now).start)
    end = _powershell_stamp(now)
    valid_row = {key: value for key, value in load(now=now)[0].items() if key in whea.STORM_ROW_KEYS}
    valid_row.update(LogName=whea.LOG, ProviderName=whea.PROVIDER)
    source = {
        "log": whea.LOG, "outcome": "ok", "error": None, "returned": 1, "limit": whea.RECORD_CAP,
        "records": [valid_row], "stopped": stopped,
    }
    normalized, kept = whea._storm_source(source, first, end)
    assert normalized["outcome"] == "failed" and kept == []  # a missing truncated field is a broken collector shape


def test_extreme_projected_timestamps_fail_closed_without_an_exception():
    from sentinel.readings.event_coverage import stamp_key

    assert stamp_key("0001-01-01T00:00:00+01:00") is None
    requested = whea.window_for(24, 60, now=0)
    assert whea._host_window("0001-01-01T00:00:00Z", "0001-01-01T00:00:00+01:00", requested) is None


def test_a_failed_supporting_probe_keeps_a_clean_query_but_unknown_reach():
    reading = storms([], outcome="empty", log_state="failed")
    assert reading.outcome == "empty" and reading.count == 0
    assert reading.section("coverage").data["system"]["complete"] is False
    assert reading.section("status").data["state"] == "unknown"
    assert "could not be established" in reading.section("status").data["reason"]
    assert any("could not be established" in warning for warning in reading.warnings)
    denied = storms([], query_outcome="denied")
    assert denied.outcome == "denied" and denied.count is None
    assert denied.section("buckets") is None
    assert denied.section("collection").data["system"]["outcome"] == "denied"
    assert denied.section("coverage").data["system"]["complete"] is None
    from sentinel.stack import _item_lines

    handoff = "\n".join(_item_lines(1, {"kind": "reading", "title": "Hardware errors", "reading": denied.to_dict(), "verbosity": "summary"}))
    assert '"name": "collection"' in handoff and '"name": "coverage"' in handoff
    assert '"outcome": "denied"' in handoff


def test_a_failed_query_concludes_nothing_about_the_machine():
    reading = storms([], outcome="denied", **{})
    assert reading.outcome == "denied" and reading.sections == []
    assert reading.error["kind"] == "denied"


def test_the_envelope_serializes_with_its_classes_and_its_basis():
    body = storms(load()).to_dict()
    assert [(s["name"], s["class"]) for s in body["sections"]] == [("buckets", "derived"), ("signatures", "derived"), ("status", "inferred"), ("collection", "raw"), ("coverage", "derived")]
    assert all(s.get("basis") for s in body["sections"] if s["class"] != "raw")
    assert body["method"]["kind"] == "powershell" and "QueryList" in body["method"]["query"]


def test_the_catalog_carries_both_readings_with_what_redaction_removes():
    from sentinel.reading import REGISTRY

    assert REGISTRY["whea"].classes == ("raw", "derived") and REGISTRY["storms"].classes == ("raw", "derived", "inferred")
    assert REGISTRY["whea"].private and REGISTRY["storms"].private
    assert [p.name for p in REGISTRY["storms"].params] == ["hours", "bucket_seconds", "burst_threshold", "accel_threshold", "references", "before"]


# ---------------------------------------------------------------- on this machine


@pytest.mark.host
def test_native_storm_projection_bounds_fixed_headers(monkeypatch: pytest.MonkeyPatch):
    import sentinel.bridge

    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)  # keep the synthetic function out of live sessions
    bridge = real_bridge_or_skip()
    fake = r"""
$hex = 'PAYLOAD_HEX'
$payload = [byte[]]::new($hex.Length / 2)
for ($j = 0; $j -lt $payload.Length; $j++) { $payload[$j] = [Convert]::ToByte($hex.Substring($j * 2, 2), 16) }
function Get-WinEvent {
    [CmdletBinding()]
    param([xml]$FilterXml, [string]$ListLog, [string]$LogName, [switch]$Oldest, [int]$MaxEvents)
    if ($ListLog) { [pscustomobject]@{ IsEnabled = $true; LogMode = 'Circular' }; return }
    if ($Oldest) { [pscustomobject]@{ TimeCreated = [datetime]::UtcNow.AddDays(-2) }; return }
    for ($i = 0; $i -lt 4; $i++) {
        $bytes = if ($i -eq 0) { ,$payload } elseif ($i -eq 1) { ,([byte[]]@(1, 2, 3, 4)) } elseif ($i -eq 2) { ,([byte[]]@()) } else { $null }
        $properties = if ($null -ne $bytes) { @([pscustomobject]@{ Value = $bytes }) } else { @() }
        [pscustomobject]@{
            RecordId = $i + 1; Id = 17; ProviderName = 'Microsoft-Windows-WHEA-Logger'; LogName = 'System'
            LevelDisplayName = 'Warning'; TimeCreated = [datetime]::UtcNow.AddMinutes(-1)
            Message = 'Synthetic hardware event'; Properties = $properties
        }
    }
}
""".replace("PAYLOAD_HEX", cper(1, flags=2))
    result = bridge.run(fake + whea.storms_script(whea.window_for(24, 60, now=0)))
    assert result.outcome == "ok" and len(result.items) == 1, result
    source = result.items[0]["source"]
    assert source["outcome"] == "ok" and source["returned"] == 4
    rows = source["records"]
    assert [(len(row["HeaderHex"]) if row["HeaderHex"] is not None else None, row["PayloadBytes"]) for row in rows] == [
        (256, len(cper()) // 2), (8, 4), (0, 0), (None, None),
    ]
    assert whea.fixed_cper_header(rows[0]["HeaderHex"], rows[0]["PayloadBytes"])[0]["previous_session"] is True
    canned = type("CannedBridge", (), {"run": lambda self, script: result})()
    reading = asyncio.run(take("storms", canned, {"hours": 24, "references": True}))
    references = reading.section("reports").data
    assert reading.outcome == "ok" and len(references) == 4
    assert references[0]["header"]["previous_session"] is True
    assert sum(reference["header"] is None for reference in references) == 3
    assert "HeaderHex" not in json.dumps(reading.to_dict()["sections"])


@pytest.mark.host
def test_powershell_keeps_the_prefix_when_a_storm_query_stops(monkeypatch: pytest.MonkeyPatch):
    import sentinel.bridge

    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)  # the synthetic function must not remain in a live session
    bridge = real_bridge_or_skip()
    fake = r"""
function Get-WinEvent {
    [CmdletBinding()]
    param([xml]$FilterXml, [string]$ListLog, [string]$LogName, [switch]$Oldest, [int]$MaxEvents)
    if ($ListLog) { [pscustomobject]@{ IsEnabled = $true; LogMode = 'Circular' }; return }
    if ($Oldest) { [pscustomobject]@{ TimeCreated = [datetime]::UtcNow.AddDays(-2) }; return }
    for ($i = 0; $i -lt 3; $i++) {
        [pscustomobject]@{
            RecordId = $i + 1; Id = 17; ProviderName = 'Microsoft-Windows-WHEA-Logger'; LogName = 'System'
            LevelDisplayName = 'Warning'; TimeCreated = [datetime]::UtcNow.AddMinutes(-1).AddSeconds(-$i)
            Message = 'Synthetic hardware event'
        }
    }
    Write-Error 'synthetic interruption' -ErrorAction Stop
}
"""
    script = fake + whea.storms_script(whea.window_for(24, 60, now=0))
    result = bridge.run(script)
    assert result.outcome == "ok" and len(result.items) == 1, result
    source = result.items[0]["source"]
    assert source["outcome"] == "ok" and source["returned"] == 3 and len(source["records"]) == 3
    assert source["truncated"] is None and source["stopped"] == {"kind": "failed", "detail": "synthetic interruption"}

    clean_query = fake.replace("Write-Error 'synthetic interruption' -ErrorAction Stop", "")
    failed_projection = clean_query + "\nfunction Select-Object { throw 'synthetic projection failure' }\n"
    projected = bridge.run(failed_projection + whea.storms_script(whea.window_for(24, 60, now=0)))
    assert projected.outcome == "ok" and len(projected.items) == 1, projected
    source = projected.items[0]["source"]
    assert source["outcome"] == "failed" and source["returned"] == 0 and source["error"]


def _observed(reading):
    """Skip only WSL's interop failure: that is the environment refusing, not the machine answering."""
    if reading.outcome == "unavailable" and "WSL could not start" in str((reading.error or {}).get("detail", "")):
        pytest.skip("WSL's interop layer would not start powershell.exe; the environment, not the machine")
    assert reading.outcome in ("ok", "empty"), reading.error
    return reading


@pytest.mark.host
def test_whea_answers_from_both_logs_on_this_machine():
    reading = _observed(asyncio.run(take("whea", real_bridge_or_skip(), {"count": 5})))
    sources = reading.section("collection").data["sources"]
    assert set(sources) == {"system", "kernel_whea"}
    assert all(sources[name]["outcome"] in ("ok", "empty") for name in sources), {name: s["outcome"] for name, s in sources.items()}
    assert reading.section("decoded") is None
    assert len(reading.section("records").data) == len(reading.section("identity").data)
    assert all("RawData" not in row and "Properties" not in row and set(row) == whea.WHEA_PREVIEW_KEYS for row in reading.section("records").data)
    assert all(row["Log"] in (whea.LOG, whea.CHANNEL) for row in reading.section("records").data)
    if reading.outcome == "empty":
        assert reading.count == 0 and reading.section("coverage").data["complete"] is True


@pytest.mark.host
def test_exact_whea_record_answers_from_its_own_log_on_this_machine(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(whea, "_run_decoder", lambda payload, timeout: ("{}", "", 0, None))
    bridge = real_bridge_or_skip()
    recent = _observed(asyncio.run(take("whea", bridge, {"count": 5})))
    rows = recent.section("records").data
    if not rows:
        pytest.skip("this machine currently retains no WHEA record to select")
    selected = rows[0]
    source_name = next(spec.name for spec in whea.WHEA_SOURCES if spec.log == selected["Log"])
    exact = _observed(asyncio.run(take("whea_record", bridge, {"source": source_name, "record_id": selected["RecordId"]})))
    assert exact.count == 1 and exact.section("records").data[0]["RecordId"] == selected["RecordId"]
    exact_row = exact.section("records").data[0]
    assert exact_row["TimeCreated"] == selected["TimeCreated"]
    assert exact_row["RawData"][:256] == selected["HeaderHex"]
    assert len(exact_row["RawData"]) // 2 == selected["PayloadBytes"]
    assert exact.section("collection").data["source"]["outcome"] == "ok"


@pytest.mark.host
def test_exact_whea_record_refuses_binary_over_its_bound_before_projection(monkeypatch: pytest.MonkeyPatch):
    import sentinel.bridge

    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    bridge = real_bridge_or_skip()
    fake = r"""
function Get-WinEvent {
    [CmdletBinding()]
    param([xml]$FilterXml, [string]$ListLog, [string]$LogName, [switch]$Oldest, [int]$MaxEvents)
    if ($ListLog) { [pscustomobject]@{ IsEnabled = $true; LogMode = 'Circular' }; return }
    if ($Oldest) { [pscustomobject]@{ TimeCreated = [datetime]::UtcNow.AddDays(-2) }; return }
    [pscustomobject]@{
        RecordId = [int64]77; Id = 20; Level = 4; LevelDisplayName = 'Information'; Version = 0; ProviderId = $null
        ProviderName = 'Microsoft-Windows-Kernel-WHEA'; LogName = 'Microsoft-Windows-Kernel-WHEA/Errors'
        MachineName = 'SYNTHETIC'; TaskDisplayName = $null; TimeCreated = [datetime]::UtcNow
        Message = 'Synthetic WHEA Event'; Properties = @([pscustomobject]@{ Value = [byte[]](0x43,0x50,0x45,0x52,0,0,0,0,0) })
    }
}
"""
    script = whea.whea_record_script(whea.WHEA_SOURCES[1], 77).replace(f"1 {whea.MAX_EXACT_BINARY_BYTES} }}", "1 8 }")
    result = bridge.run(fake + script, depth=whea.WHEA_DEPTH)
    assert result.outcome == "ok" and len(result.items) == 1, result
    source_result = result.items[0]["source"]
    assert source_result["outcome"] == "failed" and source_result["records"] == []
    assert "8-byte exact-read limit" in source_result["error"]


@pytest.mark.host
def test_native_whea_preview_bounds_large_binary_message_and_property_list(monkeypatch: pytest.MonkeyPatch):
    import sentinel.bridge

    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    bridge = real_bridge_or_skip()
    fake = r"""
function Get-WinEvent {
    [CmdletBinding()]
    param([xml]$FilterXml, [string]$ListLog, [string]$LogName, [switch]$Oldest, [int]$MaxEvents)
    if ($ListLog) { [pscustomobject]@{ IsEnabled = $true; LogMode = 'Circular' }; return }
    if ($Oldest) { [pscustomobject]@{ TimeCreated = [datetime]::UtcNow.AddDays(-2) }; return }
    $log = [string]$FilterXml.QueryList.Query.Path
    if ($log -ne 'System') { Write-Error 'synthetic empty' -ErrorId 'NoMatchingEventsFound' -ErrorAction Stop }
    $bytes = [byte[]]::new(20480)
    $bytes[0] = 0x43; $bytes[1] = 0x50; $bytes[2] = 0x45; $bytes[3] = 0x52
    $properties = @([pscustomobject]@{ Value = $bytes })
    for ($i = 0; $i -lt 79; $i++) { $properties += [pscustomobject]@{ Value = ('extra' * 1000) } }
    [pscustomobject]@{
        RecordId = [int64]77; Id = 18; Level = 2; ProviderName = 'Microsoft-Windows-WHEA-Logger'
        LogName = 'System'; TimeCreated = [datetime]::UtcNow
        Message = ('a' * 1023) + [char]::ConvertFromUtf32(0x1F642) + ('z' * 19000)
        Properties = $properties
    }
}
"""
    result = bridge.run(fake + whea.whea_script(5), depth=whea.WHEA_DEPTH)
    assert result.outcome == "ok" and len(result.items) == 1, result
    system, channel = result.items[0]["sources"]
    assert channel["outcome"] == "empty" and system["outcome"] == "ok"
    entry = system["records"][0]
    assert set(entry) == whea.WHEA_PREVIEW_KEYS
    assert entry["MessageChars"] == 20025 and entry["Message"] == "a" * 1023
    assert entry["PayloadBytes"] == 20480 and len(entry["HeaderHex"]) == 256
    assert "RawData" not in entry and "Properties" not in entry
    assert whea.valid_whea_preview_row(whea.WHEA_SOURCES[0], entry)
    assert len(json.dumps(result.items[0])) < 8000


@pytest.mark.host
def test_powershell_keeps_the_returned_records_when_a_whea_source_stops(monkeypatch: pytest.MonkeyPatch):
    import sentinel.bridge

    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)  # the synthetic function must not remain in a live session
    bridge = real_bridge_or_skip()
    fake = r"""
function Get-WinEvent {
    [CmdletBinding()]
    param([xml]$FilterXml, [string]$ListLog, [string]$LogName, [switch]$Oldest, [int]$MaxEvents)
    if ($ListLog) { [pscustomobject]@{ IsEnabled = $true; LogMode = 'Circular' }; return }
    if ($Oldest) { [pscustomobject]@{ TimeCreated = [datetime]::UtcNow.AddDays(-2) }; return }
    $log = $FilterXml.QueryList.Query.Path
    if ($log -eq 'System') { Write-Error 'synthetic empty' -ErrorId 'NoMatchingEventsFound' -ErrorAction Stop }
    for ($i = 0; $i -lt 3; $i++) {
        [pscustomobject]@{
            RecordId = [int64](3 - $i); Id = 20; Level = 4; LevelDisplayName = 'Information'; Version = 0; ProviderId = $null
            ProviderName = 'Microsoft-Windows-Kernel-WHEA'; LogName = $log; MachineName = 'SYNTHETIC'; TaskDisplayName = $null
            TimeCreated = [datetime]::UtcNow.AddMinutes(-$i); Message = 'WHEA Event'
            Properties = @([pscustomobject]@{ Value = [uint32]3 }, [pscustomobject]@{ Value = [byte[]](0x43, 0x50, 0x45) })
        }
    }
    Write-Error 'synthetic interruption' -ErrorAction Stop
}
"""
    result = bridge.run(fake + whea.whea_script(5), depth=whea.WHEA_DEPTH)
    assert result.outcome == "ok" and len(result.items) == 1, result
    system, channel = result.items[0]["sources"]
    assert system["outcome"] == "empty" and system["truncated"] is False and system["records"] == []
    assert channel["outcome"] == "ok" and channel["returned"] == 3 and channel["truncated"] is None
    assert channel["stopped"] == {"kind": "failed", "detail": "synthetic interruption"}
    assert [r["Log"] for r in channel["records"]] == [whea.CHANNEL] * 3 and channel["records"][0]["HeaderHex"] == "435045"
    reading = whea.take_whea(type("Canned", (), {"run": lambda self, script, depth=6: result})(), {"count": 5})
    assert reading.outcome == "ok" and reading.count == 3 and any("stopped after 3 returned records" in w for w in reading.warnings)


@pytest.mark.host
def test_storms_answers_on_this_machine():
    reading = _observed(asyncio.run(take("storms", real_bridge_or_skip(), {"hours": 24})))
    buckets = reading.section("buckets").data
    assert len(buckets["totals"]) == buckets["bucket_count"] == 1440
    assert reading.section("status").data["state"] in ("quiet", "burst", "accelerating", "unknown")
    assert reading.section("coverage").data["system"]["complete"] in (True, False)
    assert reading.section("status").data["reason"]


@pytest.mark.host
def test_wider_storm_buckets_align_on_this_machine():
    reading = _observed(asyncio.run(take("storms", real_bridge_or_skip(), {"hours": 24, "bucket_seconds": 900})))
    buckets = reading.section("buckets").data
    assert buckets["bucket_seconds"] == 900 and buckets["bucket_count"] == 96
    assert whea.stamp_key(buckets["from"]) == whea.stamp_key(reading.section("collection").data["window_start"])


@pytest.mark.host
def test_historical_storm_query_answers_on_this_machine_without_live_status():
    before = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    reading = _observed(asyncio.run(take("storms", real_bridge_or_skip(), {"hours": 1, "before": before})))
    collection = reading.section("collection").data
    assert whea.stamp_key(collection["window_end"]) == whea.stamp_key(whea.before_stamp(before))
    assert whea.stamp_key(collection["queried_at"]) > whea.stamp_key(collection["window_end"])
    assert reading.section("buckets") is not None and reading.section("status") is None
    assert reading.section("coverage").data["system"]["covered_until"] in (collection["window_end"], None)


def minimal_cper() -> str:
    """The smallest record the decoder accepts: the 128-byte CPER header and one 72-byte section descriptor.

    A machine with no hardware errors has no record to decode, so the decoder's success path is
    exercised against a record built here rather than one this machine produced.
    """
    descriptor = (
        (200).to_bytes(4, "little")  # the section begins where the descriptor ends
        + (0).to_bytes(4, "little")  # and carries nothing
        + (0x0300).to_bytes(2, "little")  # revision
        + bytes(2)  # validation bits, reserved
        + (1).to_bytes(4, "little")  # flags: the primary section
        + uuid.UUID("9876ccad-47b4-4bdb-b65e-16f193c4f3db").bytes_le  # generic processor error
        + bytes(16)  # FRU id
        + (0).to_bytes(4, "little")  # severity: recoverable
        + bytes(20)  # FRU text
    )
    header = (
        b"CPER"
        + (0x0100).to_bytes(2, "little")  # revision
        + (0xFFFFFFFF).to_bytes(4, "little")  # signature end
        + (1).to_bytes(2, "little")  # one section
        + bytes(8)  # severity, validation bits
        + (128 + len(descriptor)).to_bytes(4, "little")  # record length
        + bytes(8)  # timestamp
        + bytes(16) * 4  # platform, partition, creator and notification ids
        + bytes(8 + 4 + 8 + 12)  # record id, flags, persistence info, reserved
    )
    return (header + descriptor).hex().upper()


@pytest.mark.parametrize("damage", [
    "not hex", "odd hex", "short", "signature", "signature end", "no sections",
    "directory past record", "declared past payload", "section before data", "section past record",
])
def test_malformed_cper_never_starts_the_external_decoder(monkeypatch, damage: str):
    monkeypatch.setattr(whea, "_run_decoder", lambda payload, timeout: pytest.fail("malformed CPER reached the decoder"))
    data = bytearray.fromhex(minimal_cper())
    if damage == "not hex":
        payload = "not hexadecimal"
    elif damage == "odd hex":
        payload = minimal_cper()[:-1]
    elif damage == "short":
        payload = minimal_cper()[:16]
    else:
        if damage == "signature":
            data[0] = 0
        elif damage == "signature end":
            data[6] = 0
        elif damage == "no sections":
            data[10:12] = (0).to_bytes(2, "little")
        elif damage == "directory past record":
            data[10:12] = (2).to_bytes(2, "little")
        elif damage == "declared past payload":
            data[20:24] = (9999).to_bytes(4, "little")
        elif damage == "section before data":
            data[128:132] = (100).to_bytes(4, "little")
        elif damage == "section past record":
            data[132:136] = (1).to_bytes(4, "little")
        payload = data.hex()
    entry = whea.decode_record({"RecordId": 17, "RawData": payload}, deadline=time.monotonic() + 10)
    assert entry["RecordId"] == 17 and "error" in entry


def test_the_screenshot_fixture_never_feeds_a_truncated_cper_to_the_real_decoder():
    from runpy import run_path

    fixture = run_path(str(Path(__file__).parents[1] / "docs/screens/fixtures/fixture-server.py"))
    records = fixture["whea_records"](time.time(), count=30)
    assert records
    assert all(whea.checked_cper(record["RawData"])[1] is None for record in records if record.get("RawData"))
    assert next(record for record in records if record["RecordId"] == 9034)["RawData"][208:216] == "02000000"
    channel = fixture["kernel_whea_records"](time.time())
    assert len(channel) == 3 and all(record["Log"] == whea.CHANNEL for record in channel)
    assert whea.cper_header(channel[0]["RawData"])[0]["previous_session"] is True
    assert whea.cper_header(channel[0]["RawData"])[0]["severity"] == "fatal"
    assert whea.cper_header(channel[1]["RawData"])[1] is not None
    exact = fixture["answer_whea_record"](whea.whea_record_script(whea.WHEA_SOURCES[1], channel[0]["RecordId"]))
    assert exact.items[0]["source"]["records"][0]["RawData"] == channel[0]["RawData"]


def test_the_screenshot_fixture_routes_memory_and_power_to_their_own_sources(monkeypatch):
    from runpy import run_path

    from sentinel.readings.diagnostics import MEMORY_SCRIPT, memory_derived, power_derived, power_script

    fixture = run_path(str(Path(__file__).parents[1] / "docs/screens/fixtures/fixture-server.py"))
    bridge = fixture["FixtureBridge"]()
    memory = bridge.run(MEMORY_SCRIPT, depth=8).items[0]
    power = bridge.run(power_script(), depth=8).items[0]
    assert memory["sources"]["modules"]["outcome"] == "ok"
    assert memory_derived(memory)["slots_used"] == 1
    assert power["sources"]["batteries"]["outcome"] == "empty"
    assert power_derived(power)["power_source"] == "mains (no battery reported)"

    monkeypatch.setenv("SENTINEL_FIXTURE_DIAGNOSTIC_FAILURES", "1")
    failed_memory = bridge.run(MEMORY_SCRIPT, depth=8).items[0]
    failed_power = bridge.run(power_script(), depth=8).items[0]
    assert failed_memory["sources"]["modules"]["outcome"] == "failed"
    assert memory_derived(failed_memory)["slots_used"] is None
    assert failed_power["sources"]["batteries"]["outcome"] == "failed"
    assert power_derived(failed_power)["power_source"] is None


def _ran(entry: dict[str, Any]) -> dict[str, Any]:
    """WSL failing to hand the process over is the environment, not an answer about the record."""
    if str(entry.get("error", "")).startswith("WSL could not start"):
        pytest.skip("WSL's interop layer would not start the decoder; the environment, not the machine")
    return entry


@pytest.mark.host
def test_the_decoder_is_present_and_reads_a_record():
    real_bridge_or_skip()
    assert os.path.exists(whea.DECODER), "the CPER decoder is missing from the package"
    deadline = time.monotonic() + whea.DECODE_BUDGET

    # The record it cannot read is not fed to it here: the decoder dies on one with an unhandled
    # exception, and every such death is an Application Error record and a mark against Windows'
    # reliability index on the machine being tested. The unit suite holds that path with a fake.
    accepted = _ran(whea.decode_record({"RecordId": 2, "RawData": minimal_cper()}, deadline=deadline))
    assert accepted["RecordId"] == 2 and "error" not in accepted, accepted
    assert accepted["decoded"]["Header"]["Signature"] == "CPER"
    assert accepted["decoded"]["SectionDescriptor"][0]["SectionType"] == "Generic Processor Error"
