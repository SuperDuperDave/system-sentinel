"""Exact, bounded WHEA report windows keep source outcome and time reach separate."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.reading import REGISTRY, take
from sentinel.readings import whea  # register the reading
from tests.conftest import FakeBridge, identity_result, real_bridge_or_skip
from tests.test_whea import cper, preview, row, source

START = "2026-09-22T00:00:00.0000000Z"
END = "2026-09-23T00:00:00.0000000Z"
QUERY = "2026-09-24T00:00:00.0000000Z"


def moment(hour: int, *, fraction: str = "0000000") -> str:
    return f"2026-09-22T{hour:02d}:00:00.{fraction}Z"


def canned(
    name: str, times: list[str], *, count: int = 2, order: str = "newest", probe: str | None = None,
    stopped: dict[str, str] | None = None, oldest: str | None = None, query: str = QUERY,
    observed_end: str = END, outcome: str | None = None, error: str | None = None,
):
    spec = next(item for item in whea.WHEA_SOURCES if item.name == name)
    records = [row(spec.log, 100 + i, stamp) for i, stamp in enumerate(times)]
    answer = preview(source(name, records, limit=count, truncated=probe is not None, stopped=stopped,
                            oldest=oldest, outcome=outcome, error=error))
    answer["probe_time"] = probe
    payload = {"source": answer, "window_start": START, "window_end": END,
               "observed_end": observed_end, "queried_at": query}
    bridge = FakeBridge(BridgeResult("ok", items=[payload], took_ms=7))
    reading = asyncio.run(take("whea_window", bridge, {"source": name, "since": START, "before": END,
                                                      "order": order, "count": count}))
    return reading, bridge


def section(reading, name: str) -> Any:
    found = reading.section(name)
    assert found is not None
    return found.data


def test_exact_parser_preserves_the_seventh_digit_and_offset():
    assert whea.exact_window_stamp("2026-09-22T02:00:00.1234567+02:00", "since") == "2026-09-22T00:00:00.1234567Z"
    assert whea.exact_window_stamp("2026-09-22T00:00:00Z", "since") == "2026-09-22T00:00:00.0000000Z"
    assert whea.exact_window_stamp("2026-09-22T00:00:00.123Z", "since") == "2026-09-22T00:00:00.1230000Z"
    assert whea.exact_window_stamp("2026-09-22T00:00:00.123456Z", "since") == "2026-09-22T00:00:00.1234560Z"


@pytest.mark.parametrize("value", ["2026-09-22T00:00:00", "2026-09-22T00:00:00.12345678Z",
                                   "0001-01-01T00:00:00Z", "not-a-time"])
def test_exact_parser_refuses_ambiguous_or_overprecise_times(value: str):
    with pytest.raises(ValueError, match="parameter 'since'"):
        whea.exact_window_stamp(value, "since")


def test_catalog_requires_a_source_and_two_distinct_exact_bounds():
    spec = REGISTRY["whea_window"]
    assert spec.requires_selection and spec.heavy
    for missing in ({"since": START, "before": END}, {"source": "system", "before": END},
                    {"source": "system", "since": START}):
        with pytest.raises(ValueError, match="required"):
            spec.coerce(missing)
    assert spec.coerce({"source": "system", "since": START, "before": END})["order"] == "newest"
    for wrong in ({"order": "forward"}, {"count": 501}, {"source": "Application"}):
        with pytest.raises(ValueError):
            spec.coerce({"source": "system", "since": START, "before": END, **wrong})
    with pytest.raises(ValueError, match="after 'since'"):
        asyncio.run(take("whea_window", FakeBridge(), {"source": "system", "since": moment(1, fraction="1234567"),
                                                         "before": moment(1, fraction="1234567")}))


@pytest.mark.parametrize("since,before", [
    ("2026-09-22T00:00:00.12345678Z", END),
    (START, START),
    ("2026-09-22T00:00:00", END),
])
def test_invalid_exact_bounds_return_422_before_any_whea_query(since: str, before: str):
    bridge = FakeBridge()
    with TestClient(create_app(State(bridge=bridge, token="test-token-0123456789"))) as client:
        bridge.scripts.clear()
        response = client.get("/api/readings/whea_window", headers={"Authorization": "Bearer test-token-0123456789"},
                              params={"source": "system", "since": since, "before": before})
        assert response.status_code == 422
        assert bridge.scripts == []


def test_window_script_caps_after_exact_tick_filter_and_probes_in_both_directions():
    spec = whea.WHEA_SOURCES[1]
    newest = whea.whea_window_script(spec, START, END, 2, "newest")
    oldest = whea.whea_window_script(spec, START, END, 2, "oldest")
    for script in (newest, oldest):
        assert "-windowed -fromTicks $from.UtcTicks -untilTicks $until.UtcTicks" in script
        assert "Select-Object -First ($limit + 1)" in script
        assert "probe_time =" in script
        assert "AddMilliseconds(2)" in script
        assert "EventID=20" in script and "TimeCreated[@SystemTime&gt;='$xpathStart'" in script
        assert "RawData =" not in script and "Properties =" not in script
    assert " $select 2 -oldest -windowed" not in newest
    assert " $select 2 -oldest -windowed" in oldest
    assert "-MaxEvents ($limit + 1)" in whea.whea_script(2)


def test_newest_and_oldest_caps_report_opposite_exclusive_reach():
    newest, _ = canned("kernel_whea", [moment(3), moment(2)], probe=moment(1))
    oldest, _ = canned("kernel_whea", [moment(1), moment(2)], probe=moment(3), order="oldest")
    assert newest.outcome == oldest.outcome == "ok"
    assert section(newest, "coverage")["covered_from"] == moment(1)
    assert section(newest, "coverage")["covered_from_inclusive"] is False
    assert section(newest, "coverage")["covered_until"] == END
    assert section(oldest, "coverage")["covered_from"] == START
    assert section(oldest, "coverage")["covered_from_inclusive"] is True
    assert section(oldest, "coverage")["covered_until"] == moment(3)
    assert section(newest, "coverage")["complete"] is False
    assert section(oldest, "coverage")["complete"] is False
    assert [(r["RecordId"], r["TimeCreated"]) for r in section(newest, "records")] == [(101, moment(2)), (100, moment(3))]
    assert len(section(newest, "identity")) == 2
    assert newest.section("groups") is None and newest.section("decoded") is None


def test_probe_at_a_shared_time_keeps_returned_rows_but_excludes_that_reach_boundary():
    reading, _ = canned("system", [moment(1), moment(2)], probe=moment(2), order="oldest")
    assert reading.outcome == "ok" and reading.count == 2
    assert section(reading, "coverage")["covered_until"] == moment(2)
    assert len(section(reading, "records")) == 2


def test_uncapped_circular_retention_before_start_establishes_only_retained_completeness():
    reading, _ = canned("kernel_whea", [moment(2), moment(1)], order="newest")
    assert reading.outcome == "ok"
    assert section(reading, "coverage")["complete"] is True
    assert section(reading, "coverage")["covered_from"] == START
    assert section(reading, "coverage")["covered_from_inclusive"] is True


def test_retention_inside_the_window_is_exclusive_even_for_an_uncapped_query():
    reading, _ = canned("kernel_whea", [moment(2)], oldest=moment(1))
    reach = section(reading, "coverage")
    assert reach["covered_from"] == moment(1) and reach["covered_from_inclusive"] is False
    assert reach["complete"] is False


def test_a_window_before_all_retained_records_is_empty_without_retained_reach():
    reading, _ = canned("kernel_whea", [], oldest="2026-09-24T00:00:00.0000000Z")
    reach = section(reading, "coverage")
    assert reading.outcome == "empty" and reach["complete"] is False
    assert reach["covered_from"] is None and reach["covered_until"] is None
    assert any("oldest retained record" in warning for warning in reading.warnings)


@pytest.mark.parametrize("order,times,bound", [
    ("newest", [moment(3), moment(2)], moment(2)),
    ("oldest", [moment(1), moment(2)], moment(2)),
])
def test_stopped_queries_keep_partial_rows_and_bound_the_requested_side(order: str, times: list[str], bound: str):
    reading, _ = canned("kernel_whea", times, order=order, stopped={"kind": "failed", "detail": "partial read"})
    assert reading.outcome == "ok" and reading.count == 2
    reach = section(reading, "coverage")
    assert reach["complete"] is False
    assert reach["covered_from" if order == "newest" else "covered_until"] == bound


def test_a_future_window_is_empty_with_no_claim_of_reach():
    future = "2026-09-21T23:00:00.0000000Z"
    reading, _ = canned("kernel_whea", [], query=future, observed_end=future)
    reach = section(reading, "coverage")
    assert reading.outcome == "empty" and reading.count == 0
    assert reach["complete"] is False and reach["covered_from"] is None and reach["covered_until"] is None
    assert any("cannot establish a quiet window" in warning for warning in reading.warnings)


def test_future_end_caps_the_observed_reach_and_cannot_be_complete():
    query = moment(4)
    reading, _ = canned("kernel_whea", [moment(2)], query=query, observed_end=query)
    reach = section(reading, "coverage")
    assert reading.outcome == "ok" and reach["covered_until"] == query and reach["complete"] is False


def test_a_host_echo_that_changes_the_observed_end_fails_without_sections():
    reading, _ = canned("kernel_whea", [moment(2)], observed_end=moment(4))
    assert reading.outcome == "failed" and reading.sections == []


def test_oldest_first_clock_inversion_among_rows_or_probe_cannot_claim_time_reach():
    for times, probe in (([moment(2), moment(1)], None), ([moment(1), moment(2)], moment(1))):
        reading, _ = canned("kernel_whea", times, order="oldest", probe=probe)
        assert reading.outcome == "ok" and section(reading, "coverage")["covered_until"] is None
        assert section(reading, "coverage")["returned_time_ordered"] is False


def test_denied_after_some_rows_keeps_the_rows_and_says_the_remainder_is_unknown():
    reading, _ = canned("kernel_whea", [moment(1)], order="oldest",
                        stopped={"kind": "denied", "detail": "access ended"})
    assert reading.outcome == "ok" and reading.count == 1
    assert section(reading, "coverage")["complete"] is False
    assert any("stopped after some records" in warning for warning in reading.warnings)


@pytest.mark.parametrize("damage", ["row", "probe", "order", "clock"])
def test_broken_time_evidence_fails_closed_or_drops_contiguous_reach(damage: str):
    times = [moment(2), moment(1)]
    probe = None
    query = QUERY
    if damage == "row":
        times[0] = END
    elif damage == "probe":
        probe = END
    elif damage == "order":
        times.reverse()
    else:
        query = "invalid"
    reading, _ = canned("kernel_whea", times, probe=probe, query=query)
    if damage == "order":
        assert reading.outcome == "ok" and section(reading, "coverage")["covered_from"] is None
        assert any("moved against log record order" in warning for warning in reading.warnings)
    else:
        assert reading.outcome == "failed" and reading.sections == []


def test_a_source_failure_is_never_an_empty_whea_window():
    reading, _ = canned("system", [], outcome="denied", error="access denied")
    assert reading.outcome == "denied" and reading.count is None and reading.sections == []


def test_authenticated_window_withholds_cper_header_until_unredacted_is_requested():
    payload = cper(77)
    answer = preview(source("kernel_whea", [row(whea.CHANNEL, 77, moment(1), raw=payload)], limit=50))
    answer["probe_time"] = None
    bridge = FakeBridge(
        BridgeResult("ok", items=[{"source": answer, "window_start": START, "window_end": END,
                                   "observed_end": END, "queried_at": QUERY}], took_ms=7),
        by_marker={"$env:COMPUTERNAME": identity_result("SENTINEL-FIXTURE", "person")},
    )
    token = "synthetic-test-token-0123456789"
    params = {"source": "kernel_whea", "since": START, "before": END}
    with TestClient(create_app(State(bridge=bridge, token=token))) as client:
        headers = {"Authorization": f"Bearer {token}"}
        default = client.get("/api/readings/whea_window", headers=headers, params=params)
        exact = client.get("/api/readings/whea_window", headers=headers, params={**params, "unredacted": "true"})
    assert default.status_code == exact.status_code == 200
    hidden = next(s["data"][0] for s in default.json()["sections"] if s["name"] == "records")
    raw = next(s["data"][0] for s in exact.json()["sections"] if s["name"] == "records")
    assert "cper" in default.json()["redacted"] and hidden["HeaderHex"].startswith("<cper bytes withheld")
    assert payload not in default.text and "RawData" not in hidden and "Properties" not in hidden
    assert raw["HeaderHex"] == payload[:256] and "RawData" not in raw


@pytest.mark.host
def test_native_tick_filter_excludes_both_edges_before_the_directional_cap(monkeypatch: pytest.MonkeyPatch):
    import sentinel.bridge

    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)  # synthetic functions must not persist in live sessions
    fake = r"""
$start = [datetimeoffset]::Parse('2026-09-22T00:00:00.0000000Z', [cultureinfo]::InvariantCulture).UtcDateTime
$end = [datetimeoffset]::Parse('2026-09-23T00:00:00.0000000Z', [cultureinfo]::InvariantCulture).UtcDateTime
$script:rows = @(
    [pscustomobject]@{ RecordId=1; TimeCreated=$start.AddTicks(-1) },
    [pscustomobject]@{ RecordId=2; TimeCreated=$start },
    [pscustomobject]@{ RecordId=3; TimeCreated=$end.AddTicks(-1) },
    [pscustomobject]@{ RecordId=4; TimeCreated=$end }
)
function Get-WinEvent {
    [CmdletBinding()]
    param([xml]$FilterXml, [string]$ListLog, [string]$LogName, [switch]$Oldest, [int]$MaxEvents)
    if ($ListLog) { [pscustomobject]@{ IsEnabled=$true; LogMode='Circular' }; return }
    if ($LogName) { [pscustomobject]@{ TimeCreated=$start.AddDays(-1) }; return }
    $script:seenXPath = $FilterXml.OuterXml
    $ordered = if ($Oldest) { $script:rows } else { @($script:rows[3], $script:rows[2], $script:rows[1], $script:rows[0]) }
    foreach ($event in $ordered) {
        [pscustomobject]@{
            RecordId=$event.RecordId; TimeCreated=$event.TimeCreated; Id=20; Level=4
            ProviderName='Microsoft-Windows-Kernel-WHEA'; LogName='Microsoft-Windows-Kernel-WHEA/Errors'
            Message='Synthetic event'; Properties=@()
        }
    }
}
"""
    spec = whea.WHEA_SOURCES[1]
    bridge = real_bridge_or_skip()
    for culture, order, kept, probe in (("fi-FI", "newest", 3, 2), ("th-TH", "oldest", 2, 3)):
        locale = f"[Threading.Thread]::CurrentThread.CurrentCulture = [cultureinfo]::GetCultureInfo('{culture}')\n"
        script = locale + fake + whea.whea_window_script(spec, START, END, 1, order)
        script += "[pscustomobject]@{ xpath = $script:seenXPath; culture = [cultureinfo]::CurrentCulture.Name; calendar_year = ([datetime]::new(2026,9,22)).ToString('yyyy'); separator = [cultureinfo]::CurrentCulture.DateTimeFormat.TimeSeparator }\n"
        result = bridge.run(script, timeout=60, depth=8)
        assert result.outcome == "ok" and len(result.items) == 2, result.error
        answer = result.items[0]["source"]
        assert answer["outcome"] == "ok" and answer["returned"] == 1 and answer["truncated"] is True
        assert answer["records"][0]["RecordId"] == kept
        assert whea.stamp_key(answer["probe_time"]) == whea.stamp_key(START if probe == 2 else "2026-09-22T23:59:59.9999999Z")
        control = result.items[1]
        assert control["culture"] == culture and isinstance(control["separator"], str)
        if culture == "th-TH":
            assert control["calendar_year"] == "2569"  # the hostile calendar was active
        assert "2026-09-22T00:00:00.000Z" in control["xpath"]
        assert "2026-09-23T00:00:00.002Z" in control["xpath"]
