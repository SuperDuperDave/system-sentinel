"""Identify the installed Windows process before replacing its executable.

PyInstaller's one-file executable can leave a parent process alive after its child has
closed the listening socket. Keep the identities of that small process tree so an
update can wait for the file to be released, then stop only processes it actually saw.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger("sentinel.instance")

try:
    import psutil
except ImportError:  # the launcher extra is optional for source installations
    psutil = None


@dataclass(frozen=True)
class _Identity:
    pid: int
    created: float


def _same_exe(exe: str, home: Path) -> bool:
    return bool(exe) and os.path.normcase(os.path.realpath(exe)) == os.path.normcase(os.path.realpath(home))


def _snapshot(process: psutil.Process, home: Path) -> _Identity | None:
    """Read one process only while psutil still recognizes its original PID."""
    try:
        if not process.is_running():
            return None
        created = process.create_time()
        if not _same_exe(process.exe(), home) or not process.is_running():
            return None
        return _Identity(process.pid, created)
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        return None


def _state(identity: _Identity, home: Path) -> tuple[str, psutil.Process | None]:
    """Distinguish a departed PID from one whose identity cannot be proven."""
    if psutil is None:
        return "unknown", None
    try:
        process = psutil.Process(identity.pid)
        if process.create_time() != identity.created or not process.is_running():
            return "gone", None
        if process.status() == psutil.STATUS_ZOMBIE:
            return "gone", None
        if not _same_exe(process.exe(), home) or not process.is_running():
            return "unknown", None
        return "alive", process
    except psutil.NoSuchProcess:
        return "gone", None
    except (psutil.AccessDenied, OSError):
        return "unknown", None


@dataclass(frozen=True)
class InstalledInstance:
    """The listener and its same-executable family, fixed at capture time."""

    home: Path
    identities: tuple[_Identity, ...]

    def alive(self) -> bool:
        """True while any captured identity remains or its exit cannot be proved."""
        return any(_state(identity, self.home)[0] != "gone" for identity in self.identities)

    def _wait(self, seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, seconds)
        while self.alive():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.1, remaining))
        return True

    def stop(self, grace_seconds: float = 8.0, kill_seconds: float = 5.0) -> bool:
        """Wait for graceful exit, then stop verified children before their parent.

        A denied inspection leaves the identity unresolved and makes this return False;
        it never broadens the set of processes eligible for a forceful stop.
        """
        if self._wait(grace_seconds):
            return True
        deadline = time.monotonic() + max(0.0, kill_seconds)
        newest_first = sorted(self.identities, key=lambda member: (member.created, member.pid), reverse=True)
        if not newest_first:
            return True

        def kill_if_verified(identity: _Identity) -> None:
            state, process = _state(identity, self.home)
            if state != "alive" or process is None:
                return
            try:
                LOG.warning("the prior installed process %s did not exit after the quit request; stopping this verified identity", identity.pid)
                process.kill()  # psutil also checks PID reuse before signalling
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                LOG.exception("the verified prior process %s could not be stopped", identity.pid)

        for identity in newest_first[:-1]:
            kill_if_verified(identity)
        if len(newest_first) > 1 and self._wait(min(1.0, max(0.0, kill_seconds) / 2)):
            return True
        kill_if_verified(newest_first[-1])
        return self._wait(max(0.0, deadline - time.monotonic()))


def capture_installed_listener(home: Path, port: int) -> InstalledInstance | None:
    """Capture the sole TCP listener on 127.0.0.1:port if it runs ``home``.

    Include its contiguous same-executable parents and their same-executable children.
    Each member is recorded as PID plus creation time; later checks never discover new
    processes to stop. Missing psutil, socket ownership, or inspection fails closed.
    """
    if psutil is None:
        return None
    try:
        owners = {
            connection.pid
            for connection in psutil.net_connections(kind="tcp")
            if connection.status == psutil.CONN_LISTEN
            and connection.laddr
            and connection.laddr[0] == "127.0.0.1"
            and connection.laddr[1] == port
        }
        if len(owners) != 1 or None in owners:
            return None
        listener = psutil.Process(owners.pop())
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        return None

    home = home.resolve()
    first = _snapshot(listener, home)
    if first is None:
        return None
    found = {first}
    root = listener
    while True:
        try:
            parent = root.parent()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            break
        if parent is None:
            break
        identity = _snapshot(parent, home)
        if identity is None or identity in found:
            break
        found.add(identity)
        root = parent

    pending = [root]
    visited: set[_Identity] = set()
    while pending:
        current = pending.pop()
        current_identity = _snapshot(current, home)
        if current_identity is None or current_identity in visited:
            continue
        visited.add(current_identity)
        try:
            children = current.children()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
        for child in children:
            identity = _snapshot(child, home)
            if identity is not None:
                found.add(identity)
                if identity not in visited:
                    pending.append(child)
    return InstalledInstance(home, tuple(sorted(found, key=lambda member: (member.created, member.pid))))
