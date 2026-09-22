"""Run reliability's real collector script against scoped, synthetic Windows source outcomes."""

from __future__ import annotations

import pytest

import sentinel.bridge
from sentinel.readings import reliability
from tests.conftest import real_bridge_or_skip

pytestmark = pytest.mark.host


def _body(mode: str, source: str) -> str:
    if mode == "failed":
        return "throw 'synthetic reliability source failure'"
    if mode == "denied":
        return "throw [System.UnauthorizedAccessException]::new('Access is denied: synthetic reliability source')"
    if mode == "empty":
        return "return"
    if source == "stability":
        return "[pscustomobject]@{ TimeGenerated=(Get-Date).AddHours(-1); SystemStabilityIndex=6.2 }"
    count = 501 if mode == "limited" else 1
    return (
        f"1..{count} | ForEach-Object {{ [pscustomobject]@{{ "
        "TimeGenerated=(Get-Date).AddHours(-1); SourceName='Synthetic'; EventIdentifier=19; Message='Synthetic event' } }"
    )


@pytest.mark.parametrize(("records", "stability", "outcome"), [
    ("failed", "failed", "failed"),
    ("denied", "denied", "denied"),
    ("empty", "failed", "failed"),
    ("empty", "empty", "empty"),
    ("failed", "data", "ok"),
    ("data", "failed", "ok"),
    ("limited", "data", "ok"),
])
def test_collector_preserves_which_source_answered(monkeypatch, records, stability, outcome):
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    # The query replacement lives in one child scope in a disposable process; no real CIM
    # query, permission change or persistent function is involved.
    script = (
        "& {\nfunction Get-CimInstance {\n"
        "[CmdletBinding()] param([string]$ClassName)\n"
        "if ($ClassName -eq 'Win32_ReliabilityRecords') { " + _body(records, "records") + " }\n"
        "elseif ($ClassName -eq 'Win32_ReliabilityStabilityMetrics') { " + _body(stability, "stability") + " }\n"
        "else { throw 'unexpected synthetic source' }\n}\n"
        + reliability.RELIABILITY_SCRIPT_TEMPLATE + "\n}\n"
    )
    monkeypatch.setattr(reliability, "RELIABILITY_SCRIPT_TEMPLATE", script)
    reading = reliability.take_reliability(real_bridge_or_skip(), {"days": 7})
    assert reading.outcome == outcome, (reading.error, reading.warnings)
    collection = reading.section("collection").data
    assert collection["window_start"] < collection["window_end"]
    for name, mode in (("records", records), ("stability", stability)):
        expected = "ok" if mode in ("data", "limited") else mode
        source = collection[name]
        assert source["outcome"] == expected, source
        assert source["returned"] == len(reading.section(name).data)
        if mode in ("failed", "denied"):
            assert source["available"] is None and source["error"]
    if records == "limited":
        assert collection["records"]["available"] == 501
        assert collection["records"]["returned"] == reliability.RECORD_CAP
        assert reading.count == reliability.RECORD_CAP and reading.warnings
    if records == "failed" and stability == "data":
        assert reading.count is None
        assert reading.section("days").data["days"][0]["records"] is None
