"""An updater may stop only the installed process tree it identified before quit."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from sentinel import instance


class NoSuchProcess(Exception):
    pass


class AccessDenied(Exception):
    pass


@dataclass
class Node:
    pid: int
    created: float
    exe: str
    parent_pid: int | None = None
    running: bool = True
    deny_exe: bool = False
    deny_kill: bool = False
    kills: int = 0


class FakeProcess:
    def __init__(self, system: FakePsutil, pid: int):
        self.system = system
        self.pid = pid

    @property
    def node(self) -> Node:
        node = self.system.nodes.get(self.pid)
        if node is None:
            raise NoSuchProcess(self.pid)
        return node

    def is_running(self) -> bool:
        return self.node.running

    def create_time(self) -> float:
        return self.node.created

    def exe(self) -> str:
        if self.node.deny_exe:
            raise AccessDenied(self.pid)
        return self.node.exe

    def status(self) -> str:
        return "running"

    def parent(self) -> FakeProcess | None:
        parent_pid = self.node.parent_pid
        return self.system.Process(parent_pid) if parent_pid is not None else None

    def children(self) -> list[FakeProcess]:
        return [self.system.Process(node.pid) for node in self.system.nodes.values() if node.parent_pid == self.pid and node.running]

    def kill(self) -> None:
        if self.node.deny_kill:
            raise AccessDenied(self.pid)
        self.node.kills += 1
        self.system.kill_order.append(self.pid)
        self.node.running = False


class FakePsutil:
    AccessDenied = AccessDenied
    NoSuchProcess = NoSuchProcess
    CONN_LISTEN = "LISTEN"
    STATUS_ZOMBIE = "zombie"

    def __init__(self, nodes: list[Node], connections: list[tuple[str, int, int]]):
        self.nodes = {node.pid: node for node in nodes}
        self.connections = connections
        self.kill_order: list[int] = []

    def Process(self, pid: int) -> FakeProcess:  # noqa: N802 - psutil API
        if pid not in self.nodes:
            raise NoSuchProcess(pid)
        return FakeProcess(self, pid)

    def net_connections(self, *, kind: str) -> list[SimpleNamespace]:
        assert kind == "tcp"
        return [SimpleNamespace(pid=pid, laddr=(host, port), status=self.CONN_LISTEN) for host, port, pid in self.connections]


def test_capture_listener_parent_chain_and_same_exe_descendants(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    home = tmp_path / "SystemSentinel.exe"
    other = tmp_path / "other.exe"
    nodes = [
        Node(10, 1, str(other)),
        Node(11, 2, str(home), 10),
        Node(12, 3, str(home), 11),  # listening PyInstaller child
        Node(13, 4, str(home), 12),
        Node(14, 5, str(home), 11),
        Node(15, 6, str(other), 11),
        Node(16, 7, str(home), 15),  # not a contiguous same-exe descendant
    ]
    system = FakePsutil(nodes, [("127.0.0.1", 8000, 12)])
    monkeypatch.setattr(instance, "psutil", system)

    captured = instance.capture_installed_listener(home, 8000)

    assert captured is not None
    assert {identity.pid for identity in captured.identities} == {11, 12, 13, 14}
    assert captured.alive()
    assert captured.stop(grace_seconds=0, kill_seconds=0)
    assert {node.pid for node in nodes if node.kills} == {11, 12, 13, 14}
    assert system.kill_order == [14, 13, 12, 11]
    assert not captured.alive()


@pytest.mark.parametrize("connections", [[], [("0.0.0.0", 8000, 12)], [("127.0.0.1", 8001, 12)], [("127.0.0.1", 8000, 12), ("127.0.0.1", 8000, 13)]])
def test_capture_needs_one_exact_local_listener(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, connections: list[tuple[str, int, int]]):
    home = tmp_path / "SystemSentinel.exe"
    system = FakePsutil([Node(12, 1, str(home)), Node(13, 2, str(home))], connections)
    monkeypatch.setattr(instance, "psutil", system)
    assert instance.capture_installed_listener(home, 8000) is None


def test_capture_rejects_wrong_executable_or_unavailable_psutil(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    home = tmp_path / "SystemSentinel.exe"
    system = FakePsutil([Node(12, 1, str(tmp_path / "elsewhere.exe"))], [("127.0.0.1", 8000, 12)])
    monkeypatch.setattr(instance, "psutil", system)
    assert instance.capture_installed_listener(home, 8000) is None
    monkeypatch.setattr(instance, "psutil", None)
    assert instance.capture_installed_listener(home, 8000) is None


def test_reused_pid_is_never_killed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    home = tmp_path / "SystemSentinel.exe"
    node = Node(12, 1, str(home))
    monkeypatch.setattr(instance, "psutil", FakePsutil([node], [("127.0.0.1", 8000, 12)]))
    captured = instance.capture_installed_listener(home, 8000)
    assert captured is not None

    node.created = 2  # a new process reused the PID after capture
    assert not captured.alive()
    assert captured.stop(grace_seconds=0, kill_seconds=0)
    assert node.kills == 0


def test_denied_recheck_does_not_kill_or_claim_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    home = tmp_path / "SystemSentinel.exe"
    node = Node(12, 1, str(home))
    monkeypatch.setattr(instance, "psutil", FakePsutil([node], [("127.0.0.1", 8000, 12)]))
    captured = instance.capture_installed_listener(home, 8000)
    assert captured is not None

    node.deny_exe = True
    assert captured.alive()
    assert not captured.stop(grace_seconds=0, kill_seconds=0)
    assert node.kills == 0


def test_changed_executable_is_not_killed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    home = tmp_path / "SystemSentinel.exe"
    node = Node(12, 1, str(home))
    monkeypatch.setattr(instance, "psutil", FakePsutil([node], [("127.0.0.1", 8000, 12)]))
    captured = instance.capture_installed_listener(home, 8000)
    assert captured is not None

    node.exe = str(tmp_path / "other.exe")
    assert not captured.stop(grace_seconds=0, kill_seconds=0)
    assert node.kills == 0


def test_failed_kill_reports_process_still_alive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    home = tmp_path / "SystemSentinel.exe"
    node = Node(12, 1, str(home), deny_kill=True)
    monkeypatch.setattr(instance, "psutil", FakePsutil([node], [("127.0.0.1", 8000, 12)]))
    captured = instance.capture_installed_listener(home, 8000)
    assert captured is not None

    assert not captured.stop(grace_seconds=0, kill_seconds=0)
    assert captured.alive()
    assert node.kills == 0


def test_pyinstaller_parent_exits_after_child_is_killed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    home = tmp_path / "SystemSentinel.exe"
    parent = Node(11, 1, str(home))
    child = Node(12, 2, str(home), 11)
    system = FakePsutil([parent, child], [("127.0.0.1", 8000, 12)])
    monkeypatch.setattr(instance, "psutil", system)
    captured = instance.capture_installed_listener(home, 8000)
    assert captured is not None

    original_kill = FakeProcess.kill

    def kill_and_reap_parent(process: FakeProcess) -> None:
        original_kill(process)
        if process.pid == child.pid:
            parent.running = False

    monkeypatch.setattr(FakeProcess, "kill", kill_and_reap_parent)
    assert captured.stop(grace_seconds=0, kill_seconds=1)
    assert system.kill_order == [12]


def test_graceful_exit_avoids_kill(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    home = tmp_path / "SystemSentinel.exe"
    node = Node(12, 1, str(home))
    monkeypatch.setattr(instance, "psutil", FakePsutil([node], [("127.0.0.1", 8000, 12)]))
    captured = instance.capture_installed_listener(home, 8000)
    assert captured is not None

    monkeypatch.setattr(instance.time, "sleep", lambda _seconds: setattr(node, "running", False))
    assert captured.stop(grace_seconds=1, kill_seconds=0)
    assert node.kills == 0
