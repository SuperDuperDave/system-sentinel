"""The bridge's outcome is part of the type: every way powershell.exe can answer maps to one outcome."""

from sentinel.bridge import Bridge, BridgeResult, clean_stderr


def test_ok_list(bridge: Bridge):
    r = bridge.run("# fake: ok-list\nGet-WinEvent")
    assert r.outcome == "ok"
    assert r.observed
    assert [i["Id"] for i in r.items] == [41, 6008]
    assert r.returncode == 0
    assert r.error is None
    assert r.took_ms >= 0


def test_single_object_becomes_a_list_of_one(bridge: Bridge):
    r = bridge.run("# fake: ok-object")
    assert r.outcome == "ok"
    assert r.items == [{"CPU": "x"}]


def test_empty_is_a_finding_not_a_failure(bridge: Bridge):
    r = bridge.run("# fake: empty")
    assert r.outcome == "empty"
    assert r.observed
    assert r.items == []
    assert r.error is None


def test_failed_carries_the_error(bridge: Bridge):
    r = bridge.run("# fake: failed")
    assert r.outcome == "failed"
    assert not r.observed
    assert "event log" in r.error
    assert r.returncode == 1


def test_denied_is_recognized(bridge: Bridge):
    assert bridge.run("# fake: denied").outcome == "denied"
    assert bridge.run("# fake: denied-quiet").outcome == "denied"


def test_clixml_stderr_is_unwrapped(bridge: Bridge):
    r = bridge.run("# fake: clixml")
    assert r.outcome == "failed"
    assert "No events were found" in r.error
    assert "CLIXML" not in r.error
    assert "_x000D_" not in r.error


def test_warnings_survive_an_ok_result(bridge: Bridge):
    r = bridge.run("# fake: warn")
    assert r.outcome == "ok"
    assert r.items == [{"Id": 1}]
    assert any("Invalid class" in w for w in r.warnings)


def test_non_json_output_is_a_failure(bridge: Bridge):
    r = bridge.run("# fake: notjson")
    assert r.outcome == "failed"
    assert "not JSON" in r.error


def test_timeout(bridge: Bridge):
    r = bridge.run("# fake: sleep", timeout=0.5)
    assert r.outcome == "timeout"
    assert not r.observed


def test_unavailable_when_there_is_no_powershell():
    r = Bridge(exe=None).run("anything")
    assert r.outcome == "unavailable"
    assert not r.observed
    r2 = Bridge(exe="/nonexistent/powershell.exe").run("anything")
    assert r2.outcome == "unavailable"


def test_wsl_interop_failure_is_unavailable_not_failed(bridge: Bridge):
    r = bridge.run("# fake: wsl-interop")
    assert r.outcome == "unavailable"
    assert "WSL could not start powershell.exe" in r.error


def test_wsl_interop_transient_is_retried_once(bridge: Bridge):
    r = bridge.run("# fake: wsl-interop-once")
    assert r.outcome == "ok" and r.items == [{"Id": 7}]


def test_clean_stderr_plain_text():
    assert clean_stderr("line one\r\n\r\nline two\r\n") == "line one\nline two"


def test_result_is_immutable():
    r = BridgeResult("ok", items=[1])
    try:
        r.outcome = "failed"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("BridgeResult must be frozen")
