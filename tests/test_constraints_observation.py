"""Exercise the constraints collector's source outcomes through Windows PowerShell.

The replacement function exists only inside one invocation in a disposable process. It never
calls the real device query or changes Windows devices, services, permissions, or configuration.
"""

from __future__ import annotations

import pytest

import sentinel.bridge
from sentinel.reading import Reading
from sentinel.readings import diagnostics
from tests.conftest import real_bridge_or_skip

pytestmark = pytest.mark.host


def _take_with_device_query(monkeypatch: pytest.MonkeyPatch, body: str) -> Reading:
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    # The call operator creates a child scope; the process also exits after this single query.
    # CmdletBinding supplies the ErrorAction common parameter used by the actual collector.
    script = (
        "& {\n"
        "function Get-PnpDevice {\n"
        "    [CmdletBinding()] param([switch]$PresentOnly)\n"
        f"    {body}\n"
        "}\n"
        + diagnostics.CONSTRAINTS_SCRIPT
        + "\n}\n"
    )
    monkeypatch.setattr(diagnostics, "CONSTRAINTS_SCRIPT", script)
    return diagnostics.take_constraints(real_bridge_or_skip(), {})


@pytest.mark.parametrize(
    ("body", "outcome", "detail"),
    [
        ("throw 'constraints-test-query-failed'", "failed", "constraints-test-query-failed"),
        (
            "throw [System.UnauthorizedAccessException]::new('Access is denied: constraints-test-refused')",
            "denied",
            "constraints-test-refused",
        ),
    ],
)
def test_device_query_failure_is_not_an_observed_empty_list(monkeypatch, body, outcome, detail):
    reading = _take_with_device_query(monkeypatch, body)
    assert reading.outcome == outcome, (reading.outcome, reading.error, reading.warnings)
    assert not reading.observed
    assert reading.count is None
    assert reading.sections == []
    assert reading.error is not None and reading.error["kind"] == outcome
    assert detail in reading.error["detail"]


def test_successful_device_query_with_no_matches_is_observed_empty(monkeypatch):
    reading = _take_with_device_query(monkeypatch, "return")
    assert reading.outcome == "empty", (reading.outcome, reading.error, reading.warnings)
    assert reading.observed and reading.count == 0
    assert reading.error is None and reading.warnings == []
    assert reading.section("raw").data == []
    assert reading.section("derived").data["counts"] == {"disabled": 0, "not_working": 0}
