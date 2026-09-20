"""Hardware errors: the payload decoded beside each record, and the storm rules over wall-clock buckets.

This machine holds no WHEA-Logger records, so ``empty`` is the correct observation here and the
host tests assert only that. Every non-empty path — bucketing across idle minutes, the signature
rule, a burst, an acceleration — runs against the fixture through the fake bridge, and the decoder
paths run against a faked process except the one host test that invokes the real executable.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from sentinel import readings  # noqa: F401
from sentinel.bridge import BridgeResult
from sentinel.reading import take
from sentinel.readings import whea
from tests.conftest import FakeBridge, real_bridge_or_skip

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
    moment = datetime.fromtimestamp(epoch, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond:06d}0Z"


def storms(records: list[dict[str, Any]], outcome: str = "ok", **params: Any):
    bridge = FakeBridge(BridgeResult(outcome, items=records, took_ms=9))
    return asyncio.run(take("storms", bridge, params))


# ---------------------------------------------------------------- whea


def test_the_query_asks_the_provider_for_the_payload_and_treats_a_no_match_as_empty():
    script = whea.whea_script(12)
    assert "Microsoft-Windows-WHEA-Logger" in script and 'Path="System"' in script
    assert "-MaxEvents 12" in script and "-ErrorAction Stop" in script
    assert "RawData" in script and "byte[]" in script
    assert "NoMatchingEventsFound" in script


def test_each_record_carries_its_decoded_structure_or_the_reason_there_is_none(monkeypatch):
    records = load(groups={"burst"})[:2]
    monkeypatch.setattr(whea, "DECODER", str(FIXTURE))  # present, but never actually run
    answers = [
        (json.dumps({"SectionCount": 1, "ErrorSeverity": "Corrected"}), "", 0, None),
        ("", "Hexadecimal string is not a valid CPER record", 1, None),
    ]
    monkeypatch.setattr(whea, "_run_decoder", lambda payload, timeout: answers.pop(0))

    reading = asyncio.run(take("whea", FakeBridge(BridgeResult("ok", items=records, took_ms=11)), {"count": 2}))
    assert reading.outcome == "ok" and reading.count == 2
    records_section, decoded = reading.sections
    assert (records_section.name, records_section.cls) == ("records", "raw")
    assert "RawData" in records_section.data[0]
    assert (decoded.name, decoded.cls, decoded.basis) == ("decoded", "derived", whea.DECODED_BASIS)
    assert decoded.data[0] == {"RecordId": records[0]["RecordId"], "decoded": {"SectionCount": 1, "ErrorSeverity": "Corrected"}}
    assert decoded.data[1]["RecordId"] == records[1]["RecordId"]
    assert decoded.data[1]["error"] == "Hexadecimal string is not a valid CPER record"


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


def test_a_missing_decoder_is_a_warning_on_the_reading_not_a_failure(monkeypatch):
    records = load(groups={"burst"})[:2]
    monkeypatch.setattr(whea, "DECODER", str(FIXTURE.parent / "no-decoder-here.exe"))
    reading = asyncio.run(take("whea", FakeBridge(BridgeResult("ok", items=records)), {"count": 2}))
    assert reading.outcome == "ok"
    assert [e["error"] for e in reading.section("decoded").data] == ["the decoder is not present"] * 2
    assert reading.warnings == ["the CPER decoder is not present: the records were read but not decoded"]


def test_an_empty_log_still_carries_both_sections():
    reading = asyncio.run(take("whea", FakeBridge(BridgeResult("empty", took_ms=4)), {}))
    assert reading.outcome == "empty" and reading.count == 0
    assert reading.section("records").data == [] and reading.section("decoded").data == []


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
    [{"hours": 0}, {"bucket_seconds": 0}, {"bucket_seconds": 1}, {"burst_threshold": 0}, {"accel_threshold": 0}],
)
def test_a_window_that_cannot_be_counted_is_refused_before_the_machine_is_asked(params):
    with pytest.raises(ValueError):
        storms([], **params)


def test_the_query_bounds_the_window_by_the_logs_own_index():
    window = whea.window_for(24, 60, now=1_758_000_123.75)
    script = whea.storms_script(window)
    assert "Microsoft-Windows-WHEA-Logger" in script and whea._stamp(window.start) in script
    assert f"-MaxEvents {whea.RECORD_CAP}" in script and "NoMatchingEventsFound" in script
    assert "RawData" not in script  # the payload is whea's business, not the storm rule's


# ---------------------------------------------------------------- storms: buckets and signatures


def test_every_wall_clock_bucket_is_counted_including_the_idle_ones():
    reading = storms(load())
    assert reading.outcome == "ok" and reading.count == 40
    buckets = reading.section("buckets")
    assert buckets.cls == "derived" and buckets.basis
    assert len(buckets.data["totals"]) == buckets.data["bucket_count"] == 1440 and buckets.data["bucket_seconds"] == 60
    assert buckets.data["total"] == 40 == sum(buckets.data["totals"])
    assert len(buckets.data["active"]) == 29  # the other 1411 minutes are idle and counted as zero
    assert [b["total"] for b in buckets.data["active"]] == [t for t in buckets.data["totals"] if t]
    assert buckets.data["active"][-1]["start"] < buckets.data["to"]


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
    assert len(buckets["totals"]) == 1440 and buckets["total"] == 0 and buckets["active"] == []
    assert reading.section("signatures").data == []
    status = reading.section("status").data
    assert status["state"] == "quiet" and status["reason"] == "the window holds no WHEA-Logger records"


def test_a_failed_query_concludes_nothing_about_the_machine():
    reading = storms([], outcome="denied", **{})
    assert reading.outcome == "denied" and reading.sections == []
    assert reading.error["kind"] == "denied"


def test_the_envelope_serializes_with_its_classes_and_its_basis():
    body = storms(load()).to_dict()
    assert [(s["name"], s["class"]) for s in body["sections"]] == [("buckets", "derived"), ("signatures", "derived"), ("status", "inferred")]
    assert all(s.get("basis") for s in body["sections"])
    assert body["method"]["kind"] == "powershell" and "QueryList" in body["method"]["query"]


def test_the_catalog_carries_both_readings_with_what_redaction_removes():
    from sentinel.reading import REGISTRY

    assert REGISTRY["whea"].classes == ("raw", "derived") and REGISTRY["storms"].classes == ("derived", "inferred")
    assert REGISTRY["whea"].private and REGISTRY["storms"].private
    assert [p.name for p in REGISTRY["storms"].params] == ["hours", "bucket_seconds", "burst_threshold", "accel_threshold"]


# ---------------------------------------------------------------- on this machine


def _observed(reading):
    """Skip only WSL's interop failure: that is the environment refusing, not the machine answering."""
    if reading.outcome == "unavailable" and "WSL could not start" in str((reading.error or {}).get("detail", "")):
        pytest.skip("WSL's interop layer would not start powershell.exe; the environment, not the machine")
    assert reading.outcome in ("ok", "empty"), reading.error
    return reading


@pytest.mark.host
def test_whea_answers_on_this_machine():
    reading = _observed(asyncio.run(take("whea", real_bridge_or_skip(), {"count": 5})))
    assert reading.section("records") is not None and reading.section("decoded") is not None
    assert len(reading.section("decoded").data) == len(reading.section("records").data)
    if reading.outcome == "empty":
        assert reading.count == 0 and reading.section("records").data == []


@pytest.mark.host
def test_storms_answers_on_this_machine():
    reading = _observed(asyncio.run(take("storms", real_bridge_or_skip(), {"hours": 24})))
    buckets = reading.section("buckets").data
    assert len(buckets["totals"]) == buckets["bucket_count"] == 1440
    assert reading.section("status").data["state"] in ("quiet", "burst", "accelerating")
    assert reading.section("status").data["reason"]


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


def _ran(entry: dict[str, Any]) -> dict[str, Any]:
    """WSL failing to hand the process over is the environment, not an answer about the record."""
    if str(entry.get("error", "")).startswith("WSL could not start"):
        pytest.skip("WSL's interop layer would not start the decoder; the environment, not the machine")
    return entry


@pytest.mark.host
def test_the_decoder_is_present_and_reads_a_record_or_says_why_not():
    real_bridge_or_skip()
    assert os.path.exists(whea.DECODER), "the CPER decoder is missing from the package"
    deadline = time.monotonic() + whea.DECODE_BUDGET

    rejected = _ran(whea.decode_record({"RecordId": 1, "RawData": "00112233"}, deadline=deadline))
    assert "decoded" not in rejected and rejected["error"], rejected

    accepted = _ran(whea.decode_record({"RecordId": 2, "RawData": minimal_cper()}, deadline=deadline))
    assert accepted["RecordId"] == 2 and "error" not in accepted, accepted
    assert accepted["decoded"]["Header"]["Signature"] == "CPER"
    assert accepted["decoded"]["SectionDescriptor"][0]["SectionType"] == "Generic Processor Error"
