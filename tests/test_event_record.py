"""A log-local RecordId is useful only when a missing or reused row stays honest."""

import asyncio

import httpx
import pytest
from mcp import types

from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.mcp_server import Surface
from sentinel.reading import REGISTRY, take
from sentinel.readings.events import MAX_RECORD_ID, event_record_script
from tests.conftest import FakeBridge, identity_result, log_collector_result, real_bridge_or_skip

AT = "2026-09-22T00:00:00.1234567Z"
LATER = "2026-09-22T00:00:01.1234567Z"
ROW = {"RecordId": 77, "TimeCreated": AT, "MachineName": "TESTBOX", "Message": "C:\\Users\\tester", "Properties": []}


def read(result: BridgeResult, **params):
    return asyncio.run(take("event_record", FakeBridge(result), {"log": "System", "record_id": 77, **params}))


def payload(rows, *, oldest="2026-01-01T00:00:00.0000000Z"):
    return log_collector_result(rows, log="System", oldest=oldest, window_start=None, limit=1)


def test_exact_event_query_selects_one_log_local_id_and_probes_for_ambiguity():
    script = event_record_script("System", 77)
    assert "Path='System'" in script and "EventRecordID=77" in script
    assert "-MaxEvents 2" in script
    assert "TimeCreated[@" not in script
    with pytest.raises(ValueError):
        event_record_script("Security", 77)


def test_found_event_keeps_raw_row_and_confirms_original_time():
    reading = read(payload([ROW]), time_created=AT)
    assert reading.outcome == "ok" and reading.count == 1
    assert reading.section("records").data == [ROW]
    assert reading.section("collection").data["returned"] == 1
    assert reading.section("reference").data["status"] == "same"
    assert reading.section("reference").data["retention"] == "within_retained"
    assert reading.params["time_created"] == AT


def test_reused_id_does_not_present_the_new_row_as_the_referenced_one():
    newer = {**ROW, "TimeCreated": LATER}
    reading = read(payload([newer]), time_created=AT)
    assert reading.outcome == "empty" and reading.count == 0
    assert reading.section("records") is None
    assert reading.section("reference").data["status"] == "id_reused"
    assert reading.section("reference").data["found_time_created"] == LATER
    assert reading.warnings
    current = read(payload([newer]))
    assert current.outcome == "ok" and current.section("records").data == [newer]


@pytest.mark.parametrize("oldest,retention", [
    ("2026-09-23T00:00:00.0000000Z", "before_retained"),
    ("2026-01-01T00:00:00.0000000Z", "within_retained"),
])
def test_not_returned_keeps_retention_as_context_not_cause(oldest, retention):
    reading = read(payload([], oldest=oldest), time_created=AT)
    assert reading.outcome == "empty" and reading.count == 0
    assert reading.section("reference").data["status"] == "not_returned"
    assert reading.section("reference").data["retention"] == retention
    assert "cannot prove why" in reading.warnings[-1]
    without_time = read(payload([], oldest=oldest))
    assert without_time.outcome == "empty" and without_time.section("reference") is None
    assert "does not mean it never existed" in without_time.warnings[-1]


def test_unavailable_retention_is_unknown():
    result = payload([])
    result.items[0]["metadata"] = {"log": "System", "log_state": "failed", "oldest_state": "failed"}
    reading = read(result, time_created=AT)
    assert reading.outcome == "empty"
    assert reading.section("reference").data["retention"] == "unknown"


def test_ambiguous_stopped_and_wrong_id_queries_never_claim_absence():
    duplicate = payload([ROW, {**ROW, "TimeCreated": LATER}])
    reading = read(duplicate, time_created=AT)
    assert reading.outcome == "failed" and reading.count is None
    assert reading.section("records") is None and "more than one" in reading.error["detail"]

    stopped = payload([ROW])
    stopped.items[0].update(stopped={"kind": "denied", "detail": "synthetic stop"}, truncated=None)
    reading = read(stopped, time_created=AT)
    assert reading.outcome == "denied" and reading.count is None
    assert reading.section("records") is None and "uniqueness" in reading.error["detail"]

    wrong = payload([{**ROW, "RecordId": 78}])
    reading = read(wrong, time_created=AT)
    assert reading.outcome == "failed" and reading.section("records") is None
    assert "different RecordId" in reading.error["detail"]

    unreadable_time = payload([{**ROW, "TimeCreated": "not a time"}])
    reading = read(unreadable_time, time_created=AT)
    assert reading.outcome == "failed" and reading.section("records") is None
    assert "could not be compared" in reading.error["detail"]


@pytest.mark.parametrize("params", [
    {"log": "Security", "record_id": 77},
    {"log": "System", "record_id": 0},
    {"log": "System", "record_id": MAX_RECORD_ID + 1},
    {"log": "System", "record_id": 77, "time_created": "2026-09-22T00:00:00"},
    {"log": "System", "record_id": 77, "time_created": "2026-09-22T00:00:00.12345678Z"},
])
def test_invalid_selection_is_refused_before_the_bridge(params):
    bridge = FakeBridge()
    with pytest.raises(ValueError):
        asyncio.run(take("event_record", bridge, params))
    assert bridge.scripts == []


def test_exact_event_is_a_selected_catalog_reading():
    spec = REGISTRY["event_record"]
    assert spec.requires_selection and not spec.heavy
    assert [p.name for p in spec.params] == ["log", "record_id", "time_created"]


def test_authenticated_http_exact_event_applies_the_current_redaction_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    bridge = FakeBridge(by_marker={
        "$env:COMPUTERNAME": identity_result("TESTBOX", "tester"),
        "EventRecordID=77": payload([ROW]),
    })
    state = State(bridge=bridge, token="synthetic-token")
    state.learn()
    app = create_app(state, mcp=False)

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            url = "/api/readings/event_record?log=System&record_id=77&time_created=" + AT
            assert (await client.get(url)).status_code == 401
            redacted = await client.get(url, headers={"Authorization": "Bearer synthetic-token"})
            raw = await client.get(url + "&unredacted=true", headers={"Authorization": "Bearer synthetic-token"})
            invalid = await client.get("/api/readings/event_record?log=Security&record_id=77", headers={"Authorization": "Bearer synthetic-token"})
            assert redacted.status_code == 200 and raw.status_code == 200 and invalid.status_code == 422
            assert "TESTBOX" not in redacted.text and "tester" not in redacted.text
            assert "TESTBOX" in raw.text and "tester" in raw.text
            assert next(section for section in redacted.json()["sections"] if section["name"] == "reference")["data"]["status"] == "same"

    asyncio.run(check())


def test_mcp_exact_event_requires_a_reason_for_raw_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_SENTINEL_HOME", str(tmp_path))
    bridge = FakeBridge(by_marker={
        "$env:COMPUTERNAME": identity_result("TESTBOX", "tester"),
        "EventRecordID=77": payload([ROW]),
    })
    state = State(bridge=bridge, token="synthetic-token")
    state.learn()
    surface = Surface(state)
    selection = {"log": "System", "record_id": 77, "time_created": AT}

    async def check():
        denied = await surface.call_tool(None, types.CallToolRequestParams(name="event_record", arguments={**selection, "unredacted": True}))
        redacted = await surface.call_tool(None, types.CallToolRequestParams(name="event_record", arguments=selection))
        raw = await surface.call_tool(None, types.CallToolRequestParams(name="event_record", arguments={**selection, "unredacted": True, "reason": "inspect original event"}))
        assert denied.is_error and not redacted.is_error and not raw.is_error
        assert redacted.structured_content["reading"] == "event_record"
        assert redacted.structured_content["sections"][-1]["data"]["status"] == "same"
        assert "TESTBOX" not in str(redacted.structured_content)
        assert "TESTBOX" in str(raw.structured_content)

    asyncio.run(check())


@pytest.mark.host
def test_native_exact_event_query_projects_one_row_and_detects_a_duplicate():
    fake = r"""
function Get-WinEvent {
    [CmdletBinding()]
    param([xml]$FilterXml, [string]$ListLog, [string]$LogName, [int]$MaxEvents)
    if ($ListLog) { [pscustomobject]@{ IsEnabled=$true; LogMode='Circular' }; return }
    if ($LogName) { [pscustomobject]@{ TimeCreated=[datetime]::Parse('2026-01-01T00:00:00Z') }; return }
    if ($FilterXml.OuterXml -notlike '*EventRecordID=77*') { throw 'wrong exact query' }
    $at = [datetimeoffset]::Parse('2026-09-22T00:00:00.1234567Z', [cultureinfo]::InvariantCulture).UtcDateTime
    foreach ($id in 1..$answerCount) {
        [pscustomobject]@{ RecordId=77; Id=41; Level=2; LevelDisplayName='Error'
            ProviderName='Synthetic'; ProviderId=$null; Version=1; MachineName='TESTBOX'
            TaskDisplayName=$null; TimeCreated=$at; Message='Synthetic'; Properties=@() }
    }
}
"""
    bridge = real_bridge_or_skip()
    for count, truncated in ((1, False), (2, True)):
        result = bridge.run(f"$answerCount = {count}\n" + fake + event_record_script("System", 77), depth=8)
        assert result.outcome == "ok" and len(result.items) == 1, result.error
        answer = result.items[0]
        assert answer["outcome"] == "ok" and answer["returned"] == 1, answer
        assert answer["truncated"] is truncated and answer["records"][0]["RecordId"] == 77
