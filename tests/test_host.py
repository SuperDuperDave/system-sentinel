"""Against the real Windows host through powershell.exe. Skipped where there is no bridge.

These establish what this machine returned today, nothing more: the shape of the
envelope and that the outcome is one the machine can answer with.
"""

import asyncio
import base64
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path, PureWindowsPath

import pytest

import sentinel.bridge
import sentinel.readings.diagnostics as diagnostics
from sentinel import readings  # noqa: F401
from sentinel.bridge import OUTCOMES, Bridge, Session, sessions_report
from sentinel.reading import REGISTRY, Section, automatic_params, from_bridge, from_object, take
from sentinel.readings.diagnostics import MEMORY_SCRIPT, memory_derived, power_derived, power_script
from sentinel.readings.health import learn_identity
from tests.conftest import real_bridge_or_skip

pytestmark = pytest.mark.host


@pytest.mark.parametrize("transport", ("one-shot", "session"))
def test_bridge_error_stream_keeps_failure_distinct_from_empty(transport: str, monkeypatch: pytest.MonkeyPatch):
    """PowerShell formats errors asynchronously; a question must classify its own ErrorRecords.

    The next question proves that a session did not inherit the preceding question's error.
    """
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0 if transport == "one-shot" else 1)
    bridge = Bridge.locate()
    assert bridge.available

    error_only = bridge.run('Write-Error "bridge-probe-error"')
    assert error_only.outcome == "failed", error_only
    assert "bridge-probe-error" in (error_only.error or "")

    denied = bridge.run('Write-Error "Access is denied."')
    assert denied.outcome == "denied", denied

    with_data = bridge.run('Write-Error "bridge-probe-warning"; [pscustomobject]@{Id=7}')
    assert with_data.outcome == "ok", with_data
    assert with_data.items == [{"Id": 7}]
    assert any("bridge-probe-warning" in warning for warning in with_data.warnings)

    handled = bridge.run('try { throw "handled" } catch {}; [pscustomobject]@{Id=8}')
    assert handled.outcome == "ok", handled
    assert handled.items == [{"Id": 8}]
    assert handled.warnings == []

    assert bridge.run("$unused = 1").outcome == "empty"


def test_health_answers():
    bridge = real_bridge_or_skip()
    r = asyncio.run(take("health", bridge, {}))
    assert r.outcome == "ok", r.error
    data = r.section("bridge").data
    assert data["bridge"]["available"] and data["bridge"]["powershell"]
    assert data["decoder"]["present"] is True


def test_a_session_finishing_start_after_shutdown_does_not_serve_a_reading(monkeypatch):
    """Exercise the startup handover with a real child, independent of scheduling luck."""
    bridge = real_bridge_or_skip()
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 1)
    started = threading.Event()
    release = threading.Event()
    real_start = Session.start
    real_one_shot = Bridge._run_once
    late: list[Session] = []
    windows_pids: list[int] = []
    one_shot_calls: list[str] = []
    results = []
    errors: list[BaseException] = []
    script = "[pscustomobject]@{ Answer = 'after-shutdown' }"

    def held_start(cls, located, *, timeout):
        session = real_start(located, timeout=timeout)
        late.append(session)
        pid = session.ask("[pscustomobject]@{ Pid = $PID }", timeout=10, depth=2)
        assert pid.outcome == "ok" and len(pid.items) == 1, pid
        windows_pid = pid.items[0]["Pid"]
        assert type(windows_pid) is int and windows_pid > 0
        windows_pids.append(windows_pid)
        started.set()
        assert release.wait(20), "shutdown did not release the started session"
        return session

    def one_shot(self, question, *, timeout, depth):
        one_shot_calls.append(question)
        raise AssertionError("final shutdown must not start another PowerShell process")

    monkeypatch.setattr(Session, "start", classmethod(held_start))
    monkeypatch.setattr(Bridge, "_run_once", one_shot)

    def ask():
        try:
            results.append(bridge.run(script, timeout=30))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=ask, daemon=True)
    worker.start()
    try:
        assert started.wait(20), "the real session did not finish its startup probe"
        pool = sentinel.bridge._pool_for(bridge)
        assert pool is not None and pool._starting == 1
        sentinel.bridge.shutdown_sessions()
        assert pool._closed and pool.stats()["alive"] == 0
    finally:
        release.set()
        worker.join(40)
        for session in late:
            if session.alive:
                session.discard("test cleanup")
    assert not worker.is_alive() and not errors, errors
    assert len(late) == 1 and len(results) == 1
    assert results[0].outcome == "unavailable" and results[0].error == "the bridge is shutting down", results[0]
    assert one_shot_calls == []
    session = late[0]
    assert session.discarded == "shutdown" and session.answered == 1  # only the PID probe ran there
    assert session._proc.poll() is not None and all(not reader.is_alive() for reader in session._readers)
    stats = pool.stats()
    assert stats["alive"] == stats["idle"] == stats["answered"] == stats["start_failures"] == 0
    assert stats["fell_back"] == 1 and stats["discarded"] == {"shutdown": 1}

    # On WSL the Popen PID is a relay PID; ask Windows about the actual session PID as well.
    windows_pid = windows_pids[0]
    with monkeypatch.context() as once:
        once.setattr(Bridge, "_run_once", real_one_shot)
        sentinel.bridge.reset_sessions(0)  # explicit test reset for the independent PID check
        gone = bridge.run(
            f"[pscustomobject]@{{ Alive = [bool](Get-Process -Id {windows_pid} -ErrorAction SilentlyContinue) }}",
            timeout=20,
            depth=2,
        )
    assert gone.outcome == "ok" and gone.items == [{"Alive": False}], gone


def test_final_shutdown_ends_a_running_one_shot_on_the_windows_host(monkeypatch):
    bridge = real_bridge_or_skip()
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    location = bridge.run("[pscustomobject]@{ Temp = $env:TEMP }", timeout=15)
    assert location.outcome == "ok" and location.items and location.items[0]["Temp"], location
    marker = PureWindowsPath(location.items[0]["Temp"]) / f"system-sentinel-child-{uuid.uuid4().hex}.pid"
    if sys.platform == "win32":
        local_marker = Path(marker)
    else:
        parts = marker.parts
        if not parts[0][1:3] == ":\\":
            pytest.skip("the Windows temp directory is not on a mounted drive")
        local_marker = Path("/mnt") / parts[0][0].lower()
        for part in parts[1:]:
            local_marker /= part
    literal = str(marker).replace("'", "''")
    script = f"Set-Content -LiteralPath '{literal}' -Value $PID -NoNewline -Encoding Ascii; Start-Sleep -Seconds 60; [pscustomobject]@{{ Done = $true }}"
    results, errors = [], []
    windows_pid = None

    def ask():
        try:
            results.append(bridge.run(script, timeout=65))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=ask, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 15
        while not local_marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert local_marker.exists(), "the synthetic one-shot child did not record its PID"
        windows_pid = int(local_marker.read_text(encoding="ascii").strip())
        sentinel.bridge.shutdown_sessions()
        worker.join(5)
        assert not worker.is_alive() and errors == [], errors
        assert len(results) == 1 and results[0].outcome == "unavailable", results
        assert results[0].error == "the bridge is shutting down"
        sentinel.bridge.reset_sessions(0)
        gone = bridge.run(f"[pscustomobject]@{{ Alive = [bool](Get-Process -Id {windows_pid} -ErrorAction SilentlyContinue) }}", timeout=15)
        assert gone.outcome == "ok" and gone.items == [{"Alive": False}], gone
    finally:
        sentinel.bridge.shutdown_sessions()
        worker.join(5)
        if windows_pid is not None:
            sentinel.bridge.reset_sessions(0)
            bridge.run(f"Stop-Process -Id {windows_pid} -Force -ErrorAction SilentlyContinue; [pscustomobject]@{{ Checked = $true }}", timeout=15)
        local_marker.unlink(missing_ok=True)


@pytest.mark.parametrize("transport", ("one-shot", "session"))
def test_object_collectors_require_one_answer_from_the_real_transport(transport, monkeypatch):
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0 if transport == "one-shot" else 1)
    bridge = real_bridge_or_skip()
    cases = [
        ("[pscustomobject]@{ devices = @() }", "ok"),
        ("$nothing = 1", "failed"),
        ("[pscustomobject]@{ devices = @() }; [pscustomobject]@{ devices = @('synthetic problem') }", "failed"),
        ("'stray output'; [pscustomobject]@{ devices = @() }", "failed"),
    ]
    for script, expected in cases:
        result = bridge.run("& { " + script + " }")
        built = []

        def build(payload, built=built):
            built.append(payload)
            return [Section("devices", "raw", payload["devices"])]

        composed = from_object("synthetic", {}, script, result, build)
        direct = from_bridge("synthetic", {}, script, result, shape="object")
        assert composed.outcome == direct.outcome == expected, (result, composed.error, direct.error)
        if expected == "ok":
            assert built == [{"devices": []}]
        else:
            assert built == [] and composed.sections == direct.sections == []
            assert composed.count is None and direct.count is None


def test_identity_is_learned_and_never_empty():
    bridge = real_bridge_or_skip()
    identity, facts = learn_identity(bridge)
    assert facts["outcome"] == "ok"
    assert identity.host and identity.user


def test_events_from_the_system_log():
    bridge = real_bridge_or_skip()
    r = asyncio.run(take("events", bridge, {"count": 5}))
    assert r.outcome in ("ok", "empty"), r.error
    if r.outcome == "ok":
        rec = r.section("records").data[0]
        assert {"RecordId", "Id", "LevelDisplayName", "ProviderName", "TimeCreated", "Message"} <= set(rec)
        assert rec["TimeCreated"].endswith("Z")
        assert rec["LevelDisplayName"] in ("Critical", "Error")


def test_record_before_a_moment_and_before_the_log_began():
    bridge = real_bridge_or_skip()
    latest = asyncio.run(take("events", bridge, {"count": 1, "levels": [1, 2, 3, 4]}))
    if latest.outcome != "ok":
        pytest.skip("no records to anchor on")
    moment = latest.section("records").data[0]["TimeCreated"]
    r = asyncio.run(take("record", bridge, {"before": moment, "count": 10}))
    assert r.outcome in ("ok", "empty"), r.error
    if r.outcome == "ok":
        stamps = [x["TimeCreated"] for x in r.section("records").data]
        assert stamps == sorted(stamps)  # oldest first
        assert all(s < moment for s in stamps)
    ancient = asyncio.run(take("record", bridge, {"before": "2000-01-01T00:00:00Z", "count": 5}))
    assert ancient.outcome == "empty", ancient.error  # a clean no-match is a finding, not a failure


def test_a_log_that_does_not_exist_is_a_failure_not_an_empty_result():
    bridge = real_bridge_or_skip()
    from sentinel.readings.events import events_script

    result = bridge.run(events_script("NoSuchLogHere", [1, 2], 5))
    assert result.outcome == "failed", (result.outcome, result.error)
    assert result.error


def test_events_since_boot_holds_to_the_machines_own_idea_of_its_start():
    bridge = real_bridge_or_skip()
    snapshot = asyncio.run(take("system", bridge, {}))
    assert snapshot.outcome == "ok", snapshot.error
    boot = _moment(snapshot.section("snapshot").data["boot_time"])

    r = asyncio.run(take("events", bridge, {"count": 200, "levels": [1, 2, 3, 4], "since": "boot"}))
    assert r.outcome in ("ok", "empty"), r.error
    if r.outcome == "ok":
        # A minute of slack: the script asks the machine for its boot time a moment after this test did.
        assert all(_moment(rec["TimeCreated"]) >= boot - timedelta(minutes=1) for rec in r.section("records").data)


def test_events_since_a_moment_returns_nothing_earlier():
    bridge = real_bridge_or_skip()
    recent = asyncio.run(take("events", bridge, {"count": 20, "levels": [1, 2, 3, 4]}))
    if recent.outcome != "ok":
        pytest.skip("no records to anchor a moment on")
    moment = min(rec["TimeCreated"] for rec in recent.section("records").data)

    r = asyncio.run(take("events", bridge, {"count": 50, "levels": [1, 2, 3, 4], "since": moment}))
    assert r.outcome in ("ok", "empty"), r.error
    if r.outcome == "ok":
        # The XPath stamp is milliseconds; a record inside the same millisecond is not earlier.
        assert all(_moment(rec["TimeCreated"]) >= _moment(moment) - timedelta(milliseconds=1) for rec in r.section("records").data)


def test_reliability_answers_or_windows_kept_no_record():
    bridge = real_bridge_or_skip()
    r = asyncio.run(take("reliability", bridge, {"days": 7}))
    assert r.outcome in ("ok", "empty"), r.error
    assert [s.name for s in r.sections] == ["records", "stability", "days", "collection"]
    collection = r.section("collection").data
    for name in ("records", "stability"):
        assert collection[name]["outcome"] in ("ok", "empty"), collection[name]
        assert collection[name]["returned"] == len(r.section(name).data)
    rollup = r.section("days").data
    assert set(rollup) >= {"from", "to", "days", "sources", "index_now", "index_lowest"}
    if r.outcome == "ok":
        assert r.section("records").data or r.section("stability").data


def test_memory_carries_windows_own_memory_test_and_how_far_back_it_looked():
    bridge = real_bridge_or_skip()
    r = asyncio.run(take("memory", bridge, {}))
    if r.outcome != "ok":
        pytest.skip("memory was not observed")
    derived = r.section("derived").data
    diagnostic = derived["memory_diagnostic"]
    arrays = r.section("raw").data["arrays"]
    assert r.section("collection").data["arrays"]["outcome"] in ("ok", "empty")
    assert isinstance(arrays, list)
    assert set(diagnostic) == {"outcome", "last_result", "log_begins"}
    assert diagnostic["last_result"] is None or {"Id", "TimeCreated", "Message"} <= set(diagnostic["last_result"])
    assert "ledger" not in derived  # hardware errors belong to whea, not memory


def test_memory_module_query_failure_does_not_become_zero_modules():
    bridge = real_bridge_or_skip()
    script = MEMORY_SCRIPT.replace("Win32_PhysicalMemory -ErrorAction Stop", "Win32_SystemSentinelMissingMemory -ErrorAction Stop")
    result = bridge.run(script, depth=8)
    assert result.outcome == "ok" and len(result.items) == 1
    payload = result.items[0]
    assert payload["sources"]["modules"]["outcome"] == "failed"
    assert memory_derived(payload)["slots_used"] is None
    assert any("Win32_PhysicalMemory did not answer" in warning for warning in payload["warnings"])


def test_memory_module_failure_survives_the_whole_reading_boundary(monkeypatch: pytest.MonkeyPatch):
    bridge = real_bridge_or_skip()
    script = MEMORY_SCRIPT.replace("Win32_PhysicalMemory -ErrorAction Stop", "Win32_SystemSentinelMissingMemory -ErrorAction Stop")
    monkeypatch.setattr(diagnostics, "MEMORY_SCRIPT", script)
    reading = asyncio.run(take("memory", bridge, {}))
    assert reading.outcome == "ok" and reading.count is None
    assert reading.section("collection").data["modules"]["outcome"] == "failed"
    assert reading.section("raw").data["modules"] is None
    assert reading.section("derived").data["slots_used"] is None
    assert any("Win32_PhysicalMemory did not answer" in warning for warning in reading.warnings)


def test_memory_diagnostic_query_failure_is_not_no_retained_result():
    bridge = real_bridge_or_skip()
    script = MEMORY_SCRIPT.replace("LogName='System'; ProviderName='Microsoft-Windows-MemoryDiagnostics-Results'", "LogName='SystemSentinelMissingLog'; ProviderName='Microsoft-Windows-MemoryDiagnostics-Results'")
    result = bridge.run(script, depth=8)
    assert result.outcome == "ok" and len(result.items) == 1
    payload = result.items[0]
    assert payload["sources"]["diagnostic"]["outcome"] == "failed"
    assert memory_derived(payload)["memory_diagnostic"]["outcome"] == "failed"
    assert any("memory diagnostic result did not read" in warning for warning in payload["warnings"])


def test_power_failed_battery_and_wake_queries_do_not_become_mains_or_a_device():
    bridge = real_bridge_or_skip()
    script = power_script().replace("Win32_Battery -ErrorAction Stop", "Win32_SystemSentinelMissingBattery -ErrorAction Stop")
    script = script.replace("powercfg.exe /devicequery wake_armed", "powercfg.exe /devicequery sentinel_missing_query")
    result = bridge.run(script, depth=8)
    assert result.outcome == "ok" and len(result.items) == 1
    payload = result.items[0]
    assert payload["sources"]["batteries"]["outcome"] == "failed"
    assert payload["sources"]["wake_armed"]["outcome"] == "failed"
    derived = power_derived(payload)
    assert derived["power_source"] is None and derived["wake_armed_count"] is None
    assert payload["wake_armed"] is None
    assert len(payload["warnings"]) >= 2


def test_power_failed_transition_query_does_not_claim_a_quiet_ledger():
    bridge = real_bridge_or_skip()
    script = power_script().replace("Get-WinEvent -FilterXml ([xml]$query) -MaxEvents 121", "Get-WinEvent -LogName 'SystemSentinelMissingLog' -MaxEvents 121")
    result = bridge.run(script, depth=8)
    assert result.outcome == "ok" and len(result.items) == 1
    payload = result.items[0]
    assert payload["sources"]["transitions"]["outcome"] == "failed"
    assert payload["transitions"] is None
    assert power_derived(payload)["ledger"]["records"] is None
    assert any("transition ledger did not read" in warning for warning in payload["warnings"])


def test_power_transition_bound_marks_older_matches_unknown():
    bridge = real_bridge_or_skip()
    synthetic_events = r"""
function Get-WinEvent {
    param($FilterXml, $MaxEvents)
    1..121 | ForEach-Object {
        [pscustomobject]@{ RecordId = $_; Id = 6005; ProviderName = 'EventLog'; LevelDisplayName = 'Information'; TimeCreated = [datetime]::UtcNow; Message = 'synthetic transition' }
    }
}
"""
    result = bridge.run(synthetic_events + power_script(), depth=8)
    assert result.outcome == "ok" and len(result.items) == 1
    payload = result.items[0]
    assert payload["sources"]["transitions"] == {"outcome": "ok", "limit": 120, "limit_reached": True, "returned": 120}
    assert len(payload["transitions"]) == 120
    assert power_derived(payload)["ledger"]["limit_reached"] is True


def test_the_power_ledger_names_the_logs_own_start_and_stop_when_they_are_there():
    bridge = real_bridge_or_skip()
    r = asyncio.run(take("power", bridge, {}))
    if r.outcome != "ok":
        pytest.skip("power was not observed")
    if r.section("collection").data["transitions"]["outcome"] not in ("ok", "empty"):
        pytest.skip("transition records were not observed")
    records = r.section("raw").data["transitions"]
    counts = r.section("derived").data["ledger"]["counts"]
    for event_id, name in ((6005, "log started"), (6006, "log stopped")):
        if any(rec.get("Id") == event_id and rec.get("ProviderName") == "EventLog" for rec in records):
            assert counts.get(name), (event_id, counts)


def test_signals_draws_on_the_stops_and_on_windows_own_record():
    bridge = real_bridge_or_skip()
    r = asyncio.run(take("signals", bridge, {}))
    assert r.outcome in ("ok", "empty"), r.error
    inputs = {entry["name"]: entry["outcome"] for entry in r.method["readings"]}
    assert {"crash", "reliability"} <= set(inputs)
    assert inputs["crash"] in ("ok", "empty") and inputs["reliability"] in ("ok", "empty"), inputs


# --- the two transports, against the real machine -------------------------------------------------


@pytest.mark.parametrize("transport", ("one-shot", "session"))
def test_long_queries_keep_unicode_outcomes_and_timeout_recovery(transport, monkeypatch):
    """Collector size must not exceed Windows' command line, including fallback launches."""
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0 if transport == "one-shot" else 1)
    bridge = real_bridge_or_skip()
    padding = "# synthetic long collector " + "x" * 20000 + "\n"
    assert len(base64.b64encode(padding.encode("utf-16le"))) > 40000
    text = "synthetic café 雪 🧪"
    ok_script = f"[pscustomobject]@{{text='{text}'}}"

    success = bridge.run(padding + ok_script)
    assert success.outcome == "ok" and success.items == [{"text": text}], success
    assert success.returncode == 0
    empty = bridge.run(padding + "$unused = 1")
    assert empty.outcome == "empty" and empty.returncode == 0, empty

    failed = bridge.run(padding + f"throw 'long-query-failure {text}'")
    assert failed.outcome == "failed" and failed.returncode == 1, failed
    assert f"long-query-failure {text}" in failed.error
    denied = bridge.run(padding + f"throw [UnauthorizedAccessException]::new('Access is denied: {text}')")
    assert denied.outcome == "denied" and denied.returncode == 1, denied
    assert text in denied.error

    partial = bridge.run(padding + f"Write-Error 'long-query-warning {text}'; " + ok_script)
    assert partial.outcome == "ok" and partial.items == [{"text": text}], partial
    assert any(f"long-query-warning {text}" in warning for warning in partial.warnings)
    malformed = bridge.run(padding + "if (")
    assert malformed.outcome == "failed" and malformed.returncode == 1 and malformed.error, malformed

    timed_out = bridge.run(padding + "Start-Sleep -Seconds 5", timeout=0.5)
    assert timed_out.outcome == "timeout", timed_out
    recovered = bridge.run(padding + ok_script)
    assert recovered.outcome == "ok" and recovered.items == [{"text": text}], recovered
    if transport == "session":
        report = sessions_report(bridge)
        assert report["answered"] >= 7 and report["discarded"].get("timeout") == 1
        assert report["fell_back"] == 0


def test_a_reading_through_a_live_session_matches_one_through_a_launch(monkeypatch):
    """The transport is not part of the contract: the same question asked both ways comes back the
    same. A raw script for the exact items, and a reading for what the catalog answers with."""
    bridge = real_bridge_or_skip()
    script = "[pscustomobject]@{ name = 'one'; n = 1 }, [pscustomobject]@{ name = 'two'; n = 2 }"

    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    launched = bridge.run(script)
    launched_snapshot = asyncio.run(take("system", bridge, {}))

    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 4)
    answered = bridge.run(script)
    session_snapshot = asyncio.run(take("system", bridge, {}))

    assert launched.outcome == answered.outcome == "ok", (launched.error, answered.error)
    assert launched.items == answered.items == [{"name": "one", "n": 1}, {"name": "two", "n": 2}]
    assert sessions_report(bridge)["answered"] >= 2  # the second pair really did go through a session

    assert launched_snapshot.outcome == session_snapshot.outcome == "ok"
    assert [s.name for s in launched_snapshot.sections] == [s.name for s in session_snapshot.sections]
    # The machine did not restart between the two takes, so this is the same fact twice.
    assert launched_snapshot.section("snapshot").data["boot_time"] == session_snapshot.section("snapshot").data["boot_time"]


def test_a_question_in_a_live_session_cannot_see_the_one_before_it(monkeypatch):
    """Each question runs in a child scope, which is the whole of the isolation a session needs:
    one process answering everything must not let one reading's variables reach the next."""
    bridge = real_bridge_or_skip()
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 1)  # one session, so both questions go to it

    first = bridge.run("$__sentinel_probe = 41; [pscustomobject]@{ set = $true }")
    assert first.outcome == "ok", first.error
    second = bridge.run("[pscustomobject]@{ leaked = ($null -ne $__sentinel_probe) }")
    assert second.outcome == "ok", second.error
    assert second.items == [{"leaked": False}]

    report = sessions_report(bridge)
    assert report["alive"] == 1 and report["answered"] == 2, report  # and it was one session that answered both


def test_the_pool_survives_the_automatic_catalog_taken_twice(monkeypatch):
    """Every automatically selectable reading twice through one pool, without lost sessions."""
    bridge = real_bridge_or_skip()
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 4)
    at = datetime.now(UTC)

    seen: dict[str, list[str]] = {}
    for _ in range(2):
        for name, spec in list(REGISTRY.items()):
            if spec.requires_selection:
                continue
            reading = asyncio.run(take(name, bridge, automatic_params(name, at)))
            assert reading.outcome in OUTCOMES, (name, reading.outcome)
            assert reading.outcome != "unavailable", (name, reading.error)  # the bridge itself never stopped answering
            seen.setdefault(name, []).append(reading.outcome)

    report = sessions_report(bridge)
    assert report["transport"] == "session"
    # performance_history reads the local store rather than crossing the bridge.
    automatic = {name for name, spec in REGISTRY.items() if not spec.requires_selection}
    assert report["answered"] >= 2 * (len(automatic) - 1)
    assert report["alive"] <= report["size"]
    assert report["discarded"].get("died", 0) == 0, report
    assert report["fell_back"] == 0 and report["start_failures"] == 0, f"pool: {report!r}"
    assert set(seen) == automatic


def test_aggregate_performance_snapshot_answers_with_numbers_on_windows():
    bridge = real_bridge_or_skip()
    reading = asyncio.run(take("load", bridge, {}))
    assert reading.outcome == "ok", (reading.outcome, reading.error, reading.warnings)
    snapshot = reading.section("snapshot").data
    assert any(snapshot[key] is not None for key in ("cpu_percent", "memory_available_mb", "committed_bytes"))
    assert isinstance(snapshot["at"], str)


def test_process_pressure_answers_with_bounded_numeric_rows_on_windows():
    bridge = real_bridge_or_skip()
    reading = asyncio.run(take("processes", bridge, {}))
    assert reading.outcome == "ok", (reading.outcome, reading.error, reading.warnings)
    snapshot = reading.section("snapshot").data
    rows = reading.section("processes").data
    assert 0 < len(rows) <= 2048
    assert snapshot["returned_processes"] == len(rows)
    assert any(row["private_working_set_bytes"] is not None for row in rows)


def test_change_history_keeps_log_coverage_and_private_device_ids_out_of_raw_records():
    from sentinel.readings.changes import _stamp_key

    bridge = real_bridge_or_skip()
    reading = asyncio.run(take("changes", bridge, {"hours": 168, "count": 20}))
    assert reading.outcome in OUTCOMES and reading.outcome != "unavailable", (reading.outcome, reading.error)
    collection = reading.section("collection")
    assert collection is not None, reading.error
    assert set(("windows_update", "device_configuration", "msi")) <= set(collection.data)
    assert all(collection.data[bound].endswith("0000Z") for bound in ("window_start", "window_end"))
    assert collection.data["windows_update"]["outcome"] in ("ok", "empty")
    assert collection.data["msi"]["outcome"] in ("ok", "empty")
    reach = reading.section("coverage").data
    for name in ("windows_update", "device_configuration", "msi"):
        source = collection.data[name]
        assert source["outcome"] in ("ok", "empty", "failed", "denied")
        assert source["returned"] <= source["limit"] == 20
        assert source["error"] != "the source result or record projection failed validation", name
        if source["outcome"] in ("ok", "empty"):
            oldest, start = _stamp_key(source["log_oldest"]), _stamp_key(collection.data["window_start"])
            expected_complete = (source["log_enabled"] is True and source["log_mode"] == "Circular" and source["oldest_state"] == "ok" and oldest is not None and start is not None and oldest <= start and not source["truncated"])
            assert reach[name]["complete"] is expected_complete
        else:
            assert reach[name]["complete"] is None
    records = reading.section("records").data
    assert all("Message" not in row and "Properties" not in row and "MachineName" not in row for row in records)
    assert all("DeviceInstanceId" not in row.get("Data", {}) and "ParentDeviceInstanceId" not in row.get("Data", {}) for row in records)
    assert all(change.get("kind") != "unmapped_event" for change in reading.section("changes").data)


def test_process_response_limit_keeps_synthetic_leaders_from_all_three_metrics():
    from sentinel.readings.processes import PROCESS_SCRIPT

    bridge = real_bridge_or_skip()
    mock = r'''
function Get-CimInstance {
 param([string]$ClassName)
 if ($ClassName -eq 'Win32_ComputerSystem') { return [pscustomobject]@{ NumberOfLogicalProcessors = 8 } }
 foreach ($n in 1..2052) {
   [pscustomobject]@{ IDProcess=$n; Name="synthetic"; PercentProcessorTime=$(if($n -eq 2052){800}else{0}); WorkingSetPrivate=$(if($n -eq 2051){999999}else{1}); PrivateBytes=1; IODataBytesPersec=$(if($n -eq 2050){999999}else{0}); IOReadBytesPersec=0; IOWriteBytesPersec=0; HandleCount=1; ThreadCount=1 }
 }
}
'''
    result = bridge.run(mock + PROCESS_SCRIPT, timeout=45)
    assert result.outcome == "ok", result.error
    payload = result.items[0]
    rows = payload["processes"]
    ids = {int(row["pid"]) for row in rows}
    assert payload["total_processes"] == 2052 and len(ids) == 2048
    assert {2050, 2051, 2052} <= ids
    assert payload["warnings"]


def _moment(stamp: str) -> datetime:
    return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
