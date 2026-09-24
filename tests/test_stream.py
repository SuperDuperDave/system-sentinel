"""The stream: the query's shape, the frames it produces, and that it ends when the client goes."""

import asyncio
import json
import re
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.redact import Identity, Redactor
from sentinel.stream import LOGS, PRESETS, Stream, poll_script, stream_report
from tests.conftest import FakeBridge, identity_result, real_bridge_or_skip

TOKEN = "test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

CURSORS = BridgeResult("ok", items=[{"log": "System", "record": 307403}, {"log": "Application", "record": 4908829}], took_ms=3)
RECORD = {
    "RecordId": 307404,
    "Id": 41,
    "LevelDisplayName": "Critical",
    "ProviderName": "Microsoft-Windows-Kernel-Power",
    "MachineName": "TESTBOX",
    "TimeCreated": "2026-09-20T18:04:11.204Z",
    "Message": "The system has rebooted without cleanly shutting down first.",
}
POLLED = BridgeResult("ok", items=[{"log": "System", "record": RECORD}], took_ms=9)


def stream_bridge(poll: BridgeResult = POLLED, cursors: BridgeResult = CURSORS) -> FakeBridge:
    return FakeBridge(result=poll, by_marker={"$env:COMPUTERNAME": identity_result("TESTBOX", "tester"), "Get-WinEvent -LogName $log": cursors})


def frames(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        name, _, data = block.partition("\n")
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


async def take_frames(stream: Stream, polls: int) -> list[tuple[str, dict]]:
    """Drive the generator for a fixed number of polls, as a client that then goes away."""
    seen = 0

    async def disconnected() -> bool:
        nonlocal seen
        seen += 1
        return seen > polls

    return frames("".join([frame async for frame in stream.events(disconnected)]))


def test_the_query_carries_every_preset_and_stays_inside_the_logs_limit():
    script = poll_script({"System": 307403, "Application": 4908829})
    for preset in PRESETS:
        for provider in preset.providers:
            assert f"Provider[@Name='{provider}']" in script
        for event_id in preset.ids:
            assert f"EventID={event_id}" in script
    assert script.count("<Select ") == len(PRESETS)
    assert all(f'<Query Id="{n}" Path="{log}">' in script for n, log in enumerate(LOGS))
    assert "EventRecordID &gt; 307403" in script and "EventRecordID &gt; 4908829" in script
    # The Windows event log refuses a selector with too many expressions: the ids of all the
    # presets in one flat selector are past the limit, which is why each preset gets its own.
    for selector in re.findall(r"<Select [^>]*>(.*?)</Select>", script):
        assert selector.count(" or ") + selector.count(" and ") + 1 <= 20


def test_the_first_frame_is_a_heartbeat_that_says_where_the_logs_are():
    stream = Stream(stream_bridge(), None, interval=0)
    got = asyncio.run(take_frames(stream, polls=1))
    assert got[0][0] == "heartbeat"
    assert got[0][1]["cursors"] == {"System": 307403, "Application": 4908829}
    assert got[0][1]["at"].endswith("Z")


def test_new_records_arrive_and_move_the_cursor():
    before = stream_report()
    stream = Stream(stream_bridge(), None, interval=0)
    got = asyncio.run(take_frames(stream, polls=2))
    kinds = [name for name, _ in got]
    assert kinds == ["heartbeat", "record", "heartbeat"]
    log, record = got[1][1]["log"], got[1][1]["record"]
    assert log == "System" and record["RecordId"] == 307404 and record["Message"].startswith("The system has rebooted")
    assert got[2][1]["cursors"]["System"] == 307404
    assert stream.cursors["Application"] == 4908829  # untouched: nothing new in that log
    after = stream_report()
    assert after["asked"] == before["asked"] + 2  # cursor and poll, both attributable to a stream
    assert after["records_returned"] == before["records_returned"] + 1
    assert after["connected"] == before["connected"]
    assert after["connected_max"] >= 1


def test_stream_counts_raw_returned_items_before_validation_and_redaction():
    before = stream_report()
    result = BridgeResult("ok", items=[{"log": "System", "record": RECORD}, {"malformed": True}], took_ms=1)
    stream = Stream(stream_bridge(poll=result), None, limit=2)
    stream.cursors = {"System": 307403, "Application": 4908829}
    _, records = stream.poll()
    assert len(records) == 1
    after = stream_report()
    assert after["asked"] == before["asked"] + 1
    assert after["records_returned"] == before["records_returned"] + 2
    assert after["polls_at_limit"] == before["polls_at_limit"] + 1


def test_records_are_redacted_like_every_other_response():
    stream = Stream(stream_bridge(), Redactor(Identity(host="TESTBOX", user="tester")), interval=0)
    got = asyncio.run(take_frames(stream, polls=2))
    assert got[1][1]["record"]["MachineName"] == "<host>"
    assert got[1][1]["redacted"] == ["host"]


def test_a_connected_stream_uses_identity_learned_after_it_connected():
    record = {**RECORD, "Message": "TESTBOX signed in tester"}
    bridge = stream_bridge(poll=BridgeResult("ok", items=[{"log": "System", "record": record}], took_ms=9))
    bridge.by_marker["$env:COMPUTERNAME"] = BridgeResult("unavailable", error="bridge unavailable")
    state = State(bridge=bridge, token=TOKEN)
    state.learn()  # Startup could not learn names.
    assert state.identity.host is None
    stream = Stream(bridge, lambda: state.redactor, interval=0)
    assert stream.start_cursors().observed
    bridge.by_marker["$env:COMPUTERNAME"] = identity_result("TESTBOX", "tester")
    state._learned_at = time.time() - 61  # The connected stream outlived the retry interval.
    result, records = stream.poll()
    assert result.observed and len(records) == 1
    assert state.identity.host == "TESTBOX"
    assert records[0]["record"]["Message"] == "<host> signed in <user>"
    assert records[0]["record"]["MachineName"] == "<host>"
    assert records[0]["redacted"] == ["host", "user"]


def test_a_connected_stream_redacts_bridge_errors_with_the_current_identity():
    current = {"policy": Redactor(Identity())}
    bridge = stream_bridge(poll=BridgeResult("failed", error="TESTBOX refused tester", took_ms=4))
    resolved_on: list[int] = []

    def current_policy() -> Redactor:
        resolved_on.append(threading.get_ident())
        return current["policy"]

    stream = Stream(bridge, current_policy, interval=0)
    assert stream.start_cursors().observed
    current["policy"] = Redactor(Identity(host="TESTBOX", user="tester"))
    loop_thread = threading.get_ident()
    got = asyncio.run(take_frames(stream, polls=1))
    assert got[0] == ("bridge", {"outcome": "failed", "error": "<host> refused <user>", "redacted": ["host", "user"]})
    assert resolved_on and all(thread != loop_thread for thread in resolved_on)


def test_a_poll_that_did_not_observe_the_machine_says_so():
    bridge = stream_bridge(poll=BridgeResult("failed", error="There is not an event log that matches 'Nope'.", took_ms=4))
    got = asyncio.run(take_frames(Stream(bridge, None, interval=0), polls=2))
    assert [name for name, _ in got] == ["heartbeat", "bridge", "heartbeat"]
    assert got[1][1] == {"outcome": "failed", "error": "There is not an event log that matches 'Nope'."}


def test_a_bridge_that_is_not_there_is_reported_before_any_cursor_exists():
    bridge = stream_bridge(cursors=BridgeResult("unavailable", error="powershell.exe was not found"))
    got = asyncio.run(take_frames(Stream(bridge, None, interval=0), polls=1))
    assert got[0] == ("bridge", {"outcome": "unavailable", "error": "powershell.exe was not found"})
    assert got[1][1]["cursors"] == {}


def test_logs_that_do_not_say_where_they_are_are_not_a_silent_stream():
    bridge = stream_bridge(cursors=BridgeResult("ok", items=[{"log": "System", "record": 1}], took_ms=2))
    got = asyncio.run(take_frames(Stream(bridge, None, interval=0), polls=1))
    assert got[0][0] == "bridge" and got[0][1]["outcome"] == "failed"


def serve(app) -> tuple[uvicorn.Server, threading.Thread, int]:
    """A real server: the TestClient cannot close an endless response, and a client going away is the point."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 60  # startup learns the machine's names through the bridge
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "the server did not start"
    return server, thread, port


def read_until_heartbeat(port: int, seconds: float) -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []
    block: list[str] = []
    deadline = time.monotonic() + seconds
    with httpx.stream("GET", f"http://127.0.0.1:{port}/api/stream", headers=AUTH, timeout=seconds) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line:
                block.append(line)
                continue
            if block:
                seen.append(frames("\n".join(block))[0])
                block = []
            if any(name == "heartbeat" for name, _ in seen) or time.monotonic() > deadline:
                break
    return seen


def test_the_route_streams_and_the_server_lets_go_when_the_client_does():
    server, thread, port = serve(create_app(State(bridge=stream_bridge(), token=TOKEN), mcp=False))
    try:
        assert httpx.get(f"http://127.0.0.1:{port}/api/stream", timeout=5).status_code == 401
        connected_before = stream_report()["connected"]
        seen = read_until_heartbeat(port, seconds=10)
        assert [name for name, _ in seen][0] == "heartbeat"
        assert seen[0][1]["cursors"] == {"System": 307403, "Application": 4908829}
        deadline = time.monotonic() + 3
        while stream_report()["connected"] != connected_before and time.monotonic() < deadline:
            time.sleep(0.02)
        assert stream_report()["connected"] == connected_before
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    # A generator still running would hold the connection open and the shutdown would not finish.
    assert not thread.is_alive()


def test_the_route_updates_redaction_on_an_already_connected_stream():
    record = {**RECORD, "Message": "TESTBOX signed in tester"}
    bridge = stream_bridge(poll=BridgeResult("ok", items=[{"log": "System", "record": record}], took_ms=9))
    bridge.by_marker["$env:COMPUTERNAME"] = BridgeResult("unavailable", error="identity unavailable")
    state = State(bridge=bridge, token=TOKEN)
    server, thread, port = serve(create_app(state, mcp=False))
    try:
        with httpx.stream("GET", f"http://127.0.0.1:{port}/api/stream", headers=AUTH, timeout=12) as response:
            assert response.status_code == 200
            block: list[str] = []
            learned = False
            received = False
            for line in response.iter_lines():
                if line:
                    block.append(line)
                    continue
                if not block:
                    continue
                name, data = frames("\n".join(block))[0]
                block = []
                if name == "heartbeat" and not learned:
                    bridge.by_marker["$env:COMPUTERNAME"] = identity_result("TESTBOX", "tester")
                    state._learned_at = time.time() - 61
                    learned = True
                if name == "record":
                    assert data["record"]["Message"] == "<host> signed in <user>"
                    received = True
                    break
            assert learned and received
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert not thread.is_alive()


@pytest.mark.host
def test_the_stream_on_this_machine():
    bridge = real_bridge_or_skip()
    server, thread, port = serve(create_app(State(bridge=bridge, token=TOKEN), mcp=False))
    try:
        started = time.perf_counter()
        seen = read_until_heartbeat(port, seconds=30)
        took = int((time.perf_counter() - started) * 1000)
        names = [name for name, _ in seen]
        assert "heartbeat" in names, seen
        heartbeat = next(data for name, data in seen if name == "heartbeat")
        assert set(heartbeat["cursors"]) == set(LOGS)
        assert heartbeat["cursors"]["System"] > 0, "this machine's System log has records"
        assert "bridge" not in names, seen
        print(f"\nfirst heartbeat in {took} ms; cursors {heartbeat['cursors']}")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
    assert not thread.is_alive()


@pytest.mark.host
def test_a_poll_of_this_machine_returns_records_when_asked_from_far_enough_back():
    """The presets do match this machine's log: wound the cursors back and the poll answers."""
    stream = Stream(real_bridge_or_skip(), None, interval=0)
    assert stream.start_cursors().observed and stream.ready
    stream.cursors = {log: max(0, cursor - 20000) for log, cursor in stream.cursors.items()}
    result, records = stream.poll()
    assert result.outcome in ("ok", "empty"), result.error
    assert len(records) <= stream.limit
    for payload in records:
        assert payload["log"] in LOGS
        assert isinstance(payload["record"]["RecordId"], int)
        assert set(payload["record"]) >= {"Id", "LevelDisplayName", "ProviderName", "TimeCreated", "Message", "Properties"}
    print(f"\npoll took {result.took_ms} ms, {len(records)} records, outcome {result.outcome}")
