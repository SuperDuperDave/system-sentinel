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
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
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
    moment = datetime.fromtimestamp(epoch, UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond:06d}0Z"


def storms(records: list[dict[str, Any]], outcome: str = "ok", *, oldest: str | None = None, truncated: bool = False, stopped: dict[str, str] | None = None, raw_rows: bool = False, log_state: str = "ok", query_outcome: str | None = None, host_now: float | None = None, start_shift: int = 0, **params: Any):
    class StormBridge:
        def run(self, script: str, *, depth: int = 6):
            if outcome not in ("ok", "empty"):
                return BridgeResult(outcome, error="the log is not there", took_ms=9)
            bucket_seconds = int(re.search(r"\$bucketTicks = \[long\](\d+)", script).group(1))
            count = int(re.search(r"\(\[long\]\((\d+) - 1\)", script).group(1))
            machine_now = host_now if host_now is not None else time.time()
            last = int(machine_now // bucket_seconds) * bucket_seconds
            start = whea._stamp(last - (count - 1) * bucket_seconds + start_shift)
            before = datetime.fromisoformat(start.replace("Z", "+00:00")) - timedelta(seconds=1)
            rows = [
                {**(record if raw_rows else {key: record.get(key) for key in whea.STORM_ROW_KEYS if key in record}), "ProviderName": whea.PROVIDER, "LogName": whea.LOG}
                for record in records
            ]
            source = {
                "log": whea.LOG, "outcome": query_outcome or outcome, "error": "synthetic query failure" if query_outcome in ("failed", "denied") else None,
                "returned": len(rows), "limit": whea.RECORD_CAP,
                "truncated": None if stopped else truncated, "stopped": stopped, "records": rows, "log_enabled": True, "log_mode": "Circular", "log_state": log_state,
                "log_error": None, "log_oldest": oldest or before.isoformat().replace("+00:00", "Z"), "oldest_state": "ok", "oldest_error": None,
            }
            return BridgeResult("ok", items=[{"window_start": start, "window_end": _powershell_stamp(int(machine_now * 1000) / 1000), "source": source}], took_ms=9)

    bridge = StormBridge()
    return asyncio.run(take("storms", bridge, params))


# ---------------------------------------------------------------- whea


def test_the_query_asks_the_provider_for_the_payload_and_treats_a_no_match_as_empty():
    script = whea.whea_script(12)
    assert "Microsoft-Windows-WHEA-Logger" in script and 'Path="System"' in script
    assert "-MaxEvents 13" in script and "-ErrorAction Stop" in script
    assert "RawData" in script and "byte[]" in script
    assert "NoMatchingEventsFound" in script


def test_each_record_carries_its_decoded_structure_or_the_reason_there_is_none(monkeypatch):
    records = load(groups={"burst"})[:2]
    records[0]["RawData"] = minimal_cper()
    other = bytearray.fromhex(minimal_cper())
    other[12] = 1  # a second structurally valid payload, so each fake answer has its own input
    records[1]["RawData"] = other.hex()
    monkeypatch.setattr(whea, "DECODER", str(FIXTURE))  # present, but never actually run
    answers = [
        (json.dumps({"SectionCount": 1, "ErrorSeverity": "Corrected"}), "", 0, None),
        ("", "Hexadecimal string is not a valid CPER record", 1, None),
    ]
    monkeypatch.setattr(whea, "_run_decoder", lambda payload, timeout: answers.pop(0))

    reading = asyncio.run(take("whea", FakeBridge(BridgeResult("ok", items=records, took_ms=11)), {"count": 2}))
    assert reading.outcome == "ok" and reading.count == 2
    records_section, collection, decoded = reading.sections
    assert collection.name == "collection" and collection.data == {"limit": 2, "returned": 2, "truncated": False}
    assert (records_section.name, records_section.cls) == ("records", "raw")
    assert "RawData" in records_section.data[0]
    assert (decoded.name, decoded.cls, decoded.basis) == ("decoded", "derived", whea.DECODED_BASIS)
    assert decoded.data[0] == {"RecordId": records[0]["RecordId"], "decoded": {"SectionCount": 1, "ErrorSeverity": "Corrected"}}
    assert decoded.data[1]["RecordId"] == records[1]["RecordId"]
    assert decoded.data[1]["error"] == "Hexadecimal string is not a valid CPER record"


def test_whea_cutoff_excludes_the_probe_record_from_decoding(monkeypatch):
    records = load(groups={"burst"})[:3]
    records[0]["RawData"] = minimal_cper()
    records[1]["RawData"] = minimal_cper()
    records[2]["RawData"] = minimal_cper()
    calls = []
    monkeypatch.setattr(whea, "_run_decoder", lambda payload, timeout: (calls.append(payload) or '{}', "", 0, None))
    reading = asyncio.run(take("whea", FakeBridge(BridgeResult("ok", items=records)), {"count": 2}))
    assert reading.count == 2 and [r["RecordId"] for r in reading.section("records").data] == [r["RecordId"] for r in records[:2]]
    assert reading.section("collection").data == {"limit": 2, "returned": 2, "truncated": True}
    assert len(reading.section("decoded").data) == 2 and len(calls) == 1  # repeated payload decoded once


def test_identical_cper_payloads_are_decoded_once_and_keep_their_own_record_ids(monkeypatch):
    calls = []
    monkeypatch.setattr(whea, "_run_decoder", lambda payload, timeout: (calls.append(payload) or '{}', "", 0, None))
    payload = minimal_cper()
    decoded, warnings = whea.decode_all([{"RecordId": 41, "RawData": payload}, {"RecordId": 42, "RawData": payload}])
    assert not warnings and len(calls) == 1
    assert decoded == [{"RecordId": 41, "decoded": {}}, {"RecordId": 42, "decoded": {}}]


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
    [{"hours": 0}, {"hours": whea.MAX_HOURS + 1}, {"bucket_seconds": 0}, {"bucket_seconds": 1}, {"burst_threshold": 0}, {"accel_threshold": 0}],
)
def test_a_window_that_cannot_be_counted_is_refused_before_the_machine_is_asked(params):
    with pytest.raises(ValueError):
        storms([], **params)


def test_the_query_bounds_the_window_by_the_logs_own_index():
    window = whea.window_for(24, 60, now=1_758_000_123.75)
    script = whea.storms_script(window)
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


def test_a_single_bucket_at_its_exact_start_is_an_empty_interval_not_a_collector_failure():
    instant = whea.window_for(1, 3600, now=time.time()).end
    reading = storms([], outcome="empty", host_now=instant, hours=1, bucket_seconds=3600)
    assert reading.outcome == "empty" and reading.count == 0
    assert reading.section("buckets").data["bucket_count"] == 1
    assert reading.section("status").data["state"] == "unknown"  # no baseline for a trend


def test_a_misaligned_host_window_fails_with_its_own_reason():
    reading = storms([], outcome="empty", start_shift=1)
    assert reading.outcome == "failed" and reading.count is None
    assert "window bounds" in reading.error["detail"]
    assert reading.section("buckets") is None


def test_one_extra_record_makes_the_storm_cap_exact(monkeypatch):
    monkeypatch.setattr(whea, "RECORD_CAP", 3)
    script = whea.storms_script(whea.window_for(24, 60))
    assert "-MaxEvents 4" in script
    reading = storms(load(groups={"tail"})[:3], truncated=True)
    assert reading.outcome == "ok" and reading.count == 3
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
    assert [p.name for p in REGISTRY["storms"].params] == ["hours", "bucket_seconds", "burst_threshold", "accel_threshold"]


# ---------------------------------------------------------------- on this machine


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
    assert reading.section("status").data["state"] in ("quiet", "burst", "accelerating", "unknown")
    assert reading.section("coverage").data["system"]["complete"] in (True, False)
    assert reading.section("status").data["reason"]


@pytest.mark.host
def test_wider_storm_buckets_align_on_this_machine():
    reading = _observed(asyncio.run(take("storms", real_bridge_or_skip(), {"hours": 24, "bucket_seconds": 900})))
    buckets = reading.section("buckets").data
    assert buckets["bucket_seconds"] == 900 and buckets["bucket_count"] == 96
    assert whea.stamp_key(buckets["from"]) == whea.stamp_key(reading.section("collection").data["window_start"])


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
