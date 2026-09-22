"""The local performance witness: numeric-only samples, bounded storage and one writer."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from sentinel.app import State, create_app
from sentinel.bridge import BridgeResult
from sentinel.performance import METRICS, PerformanceCollector, PerformanceStore, normalize, take_load
from sentinel.reading import take
from tests.conftest import FakeBridge, identity_result

TOKEN = "performance-test-token"


def moment() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def payload(**over):
    return {
        "at": moment(),
        "cpu_percent": 12,
        "memory_available_mb": 4096,
        "committed_bytes": 16_000_000,
        "commit_limit_bytes": 32_000_000,
        "pages_per_sec": 3,
        "disk_queue": 0,
        "disk_read_bytes_per_sec": 400,
        "disk_write_bytes_per_sec": 800,
        **over,
    }


def test_only_plausible_numeric_metrics_are_kept_and_partial_classes_remain_null():
    row = normalize(payload(cpu_percent=float("nan"), disk_queue=-1, process_path=r"C:\Users\someone\private.exe"))
    assert row is not None and set(row) == {"at", "cadence_seconds", *METRICS}
    assert row["cpu_percent"] is None and row["disk_queue"] is None
    assert row["memory_available_mb"] == 4096
    assert "process_path" not in json.dumps(row)
    assert normalize(payload(**{key: None for key in METRICS})) is None
    assert normalize(payload(at="not a time")) is None
    assert normalize(payload(cpu_percent=101))["cpu_percent"] is None


def test_load_keeps_an_observed_partial_answer_and_does_not_store_a_failed_zero():
    bridge = FakeBridge(result=BridgeResult("ok", items=[dict(payload(cpu_percent=None), warnings=["CPU counters did not answer"])], took_ms=9))
    reading = take_load(bridge, {})
    assert reading.outcome == "ok" and reading.section("snapshot").data["cpu_percent"] is None
    assert reading.section("snapshot").data["memory_available_mb"] == 4096
    assert reading.warnings == ["CPU counters did not answer"]
    failed = take_load(FakeBridge(result=BridgeResult("unavailable", error="bridge absent")), {})
    assert failed.outcome == "unavailable" and failed.sections == []
    empty = take_load(FakeBridge(result=BridgeResult("ok", items=[payload(**{key: None for key in METRICS})])), {})
    assert empty.outcome == "empty" and empty.count == 0


def test_store_survives_reopen_skips_broken_lines_and_clear_removes_the_history(tmp_path):
    home = tmp_path / "performance"
    store = PerformanceStore(home)
    store.append(payload(process_path="never stored"))
    if os.name != "nt":
        assert home.stat().st_mode & 0o077 == 0
    path = next(home.glob("*.jsonl"))
    assert "process_path" not in path.read_text(encoding="utf-8")
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"at":"cut off"\n')
    reopened = PerformanceStore(home)
    now = datetime.now(UTC)
    rows, malformed = reopened.read(now - timedelta(hours=1), now + timedelta(minutes=1))
    assert len(rows) == 1 and malformed == 1
    assert rows[0]["cpu_percent"] == 12
    assert reopened.clear() == 1
    assert reopened.read(now - timedelta(hours=1), now + timedelta(minutes=1)) == ([], 0)
    assert reopened.status()["outcome"] == "cleared"


def test_unreadable_history_is_not_reported_as_a_clean_empty_window(private_home, monkeypatch):
    def unreadable(self, start, end):
        raise OSError("disk refused the read")

    monkeypatch.setattr(PerformanceStore, "read", unreadable)
    reading = asyncio.run(take("performance_history", FakeBridge(), {"hours": 1}))
    assert reading.outcome == "unavailable"
    assert reading.error == {"kind": "local_store", "detail": "Could not read local performance history: OSError"}
    assert reading.sections == []


def test_retention_prunes_old_days_and_corrupt_settings_fail_closed(tmp_path):
    home = tmp_path / "performance"
    home.mkdir()
    old = (datetime.now(UTC) - timedelta(days=31)).date()
    stale = home / f"{old.isoformat()}.jsonl"
    stale.write_text(json.dumps(payload(at=f"{old.isoformat()}T12:00:00Z")) + "\n", encoding="utf-8")
    future = (datetime.now(UTC) + timedelta(days=31)).date()
    future_file = home / f"{future.isoformat()}.jsonl"
    future_file.write_text(json.dumps(payload(at=f"{future.isoformat()}T12:00:00Z")) + "\n", encoding="utf-8")
    store = PerformanceStore(home)
    now = datetime.now(UTC)
    store.read(now - timedelta(hours=1), now)
    assert not stale.exists() and not future_file.exists()
    assert store.settings()["enabled"] is True
    (home / "settings.json").write_text("{broken", encoding="utf-8")
    assert store.settings() == {"enabled": False, "interval_seconds": 60, "config_error": True}
    with pytest.raises(ValueError, match="interval_seconds"):
        store.configure(True, 1)
    assert store.configure(False, 120)["enabled"] is False


def test_history_is_a_local_envelope_with_exact_rows_and_no_health_verdict(private_home):
    store = PerformanceStore(private_home / "performance")
    first = payload(cpu_percent=10)
    second = payload(cpu_percent=40)
    store.append(first)
    store.append(second)
    reading = asyncio.run(take("performance_history", FakeBridge(), {"hours": 1}))
    assert reading.outcome == "ok" and reading.count == 2
    assert reading.method["kind"] == "local-jsonl"
    assert reading.section("samples").data[0]["cpu_percent"] == 10
    shape = reading.section("shape").data
    assert shape["metrics"]["cpu_percent"] == {"count": 2, "min": 10, "median": 25.0, "max": 40}
    assert shape["metrics"]["commit_percent"]["median"] == 50.0
    assert "health" not in shape
    with pytest.raises(ValueError, match="hours"):
        asyncio.run(take("performance_history", FakeBridge(), {"hours": 49}))
    with pytest.raises(ValueError, match="UTC offset"):
        asyncio.run(take("performance_history", FakeBridge(), {"end": "2026-09-22T12:00:00"}))


def test_one_writer_respects_the_switch_across_processes_and_releases_its_lock(tmp_path, monkeypatch):
    import sentinel.performance as performance

    monkeypatch.setattr(performance, "MIN_INTERVAL", 1)
    home = tmp_path / "performance"
    store = PerformanceStore(home)
    store.configure(False, 1)
    bridge = FakeBridge(result=BridgeResult("ok", items=[payload()], took_ms=7))
    first = PerformanceCollector(bridge, store)
    second = PerformanceCollector(bridge, PerformanceStore(home))
    try:
        assert first.start() is True
        assert second.start() is False
        contender = subprocess.run(
            [sys.executable, "-c", "import sys; from pathlib import Path; from sentinel.performance import _lock; p=Path(sys.argv[1]);\ntry: _lock(p, timeout=0)\nexcept TimeoutError: sys.exit(7)", str(home / "collector.lock")],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert contender.returncode == 7, contender.stderr
        time.sleep(0.15)
        assert bridge.scripts == []
        store.configure(True, 1)
        deadline = time.monotonic() + 3
        while len(bridge.scripts) < 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(bridge.scripts) == 1
        store.configure(False, 1)
        time.sleep(1.2)
        assert len(bridge.scripts) == 1
        assert store.status()["outcome"] == "ok"
    finally:
        first.stop()
        second.stop()
    assert second.start() is True
    second.stop()


def test_stopping_during_a_sample_does_not_save_a_false_bridge_failure(tmp_path, monkeypatch):
    import sentinel.performance as performance

    monkeypatch.setattr(performance, "MIN_INTERVAL", 1)
    store = PerformanceStore(tmp_path / "performance")
    store.configure(True, 1)
    entered, release = threading.Event(), threading.Event()

    class HeldBridge:
        def run(self, script, *, timeout):
            entered.set()
            assert release.wait(10)
            return BridgeResult("unavailable", error="the bridge is shutting down")

    collector = PerformanceCollector(HeldBridge(), store)
    assert collector.start()
    stopper = threading.Thread(target=collector.stop, daemon=True)
    try:
        assert entered.wait(10)
        stopper.start()
        assert collector._stop.wait(10)
    finally:
        release.set()
        if stopper.ident is not None:
            stopper.join(10)
        collector.stop()
    assert not stopper.is_alive()
    assert store.status()["outcome"] == "not_started"


def test_authenticated_routes_expose_stop_resume_and_clear_without_a_new_host_query(private_home):
    bridge = FakeBridge(by_marker={"$env:COMPUTERNAME": identity_result()})
    with TestClient(create_app(State(bridge=bridge, token=TOKEN), mcp=False)) as client:
        route = "/api/performance/collection"
        assert client.get(route).status_code == 401
        auth = {"Authorization": f"Bearer {TOKEN}"}
        assert client.get(route, headers=auth).json()["settings"]["enabled"] is True
        changed = client.put(route, headers=auth, json={"enabled": False, "interval_seconds": 120})
        assert changed.status_code == 200 and changed.json()["settings"]["interval_seconds"] == 120
        assert client.put(route, headers=auth, json={"enabled": True, "interval_seconds": 10}).status_code == 422
        store = PerformanceStore(private_home / "performance")
        store.append(payload())
        assert client.delete("/api/performance/history", headers=auth).json()["cleared_files"] == 1
        assert not list(store.home.glob("*.jsonl"))
