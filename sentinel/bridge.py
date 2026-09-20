"""The one path to Windows.

Every read of the machine runs a PowerShell script through ``powershell.exe`` and
comes back as a :class:`BridgeResult` whose ``outcome`` is part of the type. The
bridge cannot return "nothing": a script that ran and matched no records is
``empty``; a script that could not run is ``unavailable``; one that Windows
refused is ``denied``; one that errored is ``failed``; one that overran is
``timeout``. Only ``ok`` and ``empty`` say anything about the machine.

The script's pipeline output is collected into an array and serialized as JSON
by the bridge itself, so a single object and a list of one arrive the same way
and callers never see PowerShell's one-element quirk.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Literal

Outcome = Literal["ok", "empty", "failed", "unavailable", "denied", "timeout"]

OUTCOMES: tuple[Outcome, ...] = ("ok", "empty", "failed", "unavailable", "denied", "timeout")

DEFAULT_TIMEOUT = 60.0

_KNOWN_LOCATIONS = (
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
)

# WSL's interop layer failing to hand the process over; seen on this machine on 2026-09-20 as
# "<3>WSL (pid - ) ERROR: UtilAcceptVsock:271: accept4 failed 110". Not a Windows error.
WSL_INTEROP = "UtilAcceptVsock"
WSL_INTEROP_ATTEMPTS = 3

_DENIED_MARKERS = (
    "access is denied",
    "access denied",
    "unauthorized operation",
    "requires elevation",
    "permission denied",
    "administrator privileges",
)

# What the bridge wraps around every script. The script's pipeline output is
# captured into an array; nothing is written to stdout except one JSON document.
_PRELUDE = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$ProgressPreference = 'SilentlyContinue'; "
    "$__sentinel = @(& { {script} }); "
    "if ($__sentinel.Count -gt 0) { ConvertTo-Json -InputObject $__sentinel -Depth {depth} -Compress }"
)


@dataclass(frozen=True)
class BridgeResult:
    outcome: Outcome
    items: list[Any] = field(default_factory=list)
    took_ms: int = 0
    returncode: int | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def observed(self) -> bool:
        """True when the machine answered: the reading is evidence either way."""
        return self.outcome in ("ok", "empty")


@dataclass(frozen=True)
class Bridge:
    """A located ``powershell.exe``, or the fact that there is none."""

    exe: str | None
    cwd: str | None = None

    @classmethod
    def locate(cls) -> "Bridge":
        exe = shutil.which("powershell.exe")
        if exe is None:
            for candidate in _KNOWN_LOCATIONS:
                if os.path.exists(candidate):
                    exe = candidate
                    break
        return cls(exe=exe, cwd=_working_directory())

    @property
    def available(self) -> bool:
        return self.exe is not None

    def run(self, script: str, *, timeout: float = DEFAULT_TIMEOUT, depth: int = 6) -> BridgeResult:
        """Run one script. From WSL, the interop layer occasionally fails to hand the process over
        (``UtilAcceptVsock ... accept4 failed``); that is the environment, not the machine, so it is
        retried a bounded number of times with a short pause and otherwise reported as ``unavailable``.
        Native Windows never sees this path."""
        result = self._run_once(script, timeout=timeout, depth=depth)
        for attempt in range(1, WSL_INTEROP_ATTEMPTS):
            if not (result.outcome == "unavailable" and result.error and WSL_INTEROP in result.error):
                break
            time.sleep(0.5 * attempt)
            result = self._run_once(script, timeout=timeout, depth=depth)
        return result

    def _run_once(self, script: str, *, timeout: float, depth: int) -> BridgeResult:
        if self.exe is None:
            return BridgeResult("unavailable", error="powershell.exe was not found")

        full = _PRELUDE.replace("{script}", script).replace("{depth}", str(depth))
        encoded = base64.b64encode(full.encode("utf-16le")).decode("ascii")
        cmd = [self.exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]

        started = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                cwd=self.cwd,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return BridgeResult("timeout", took_ms=_ms(started), error=f"no answer within {timeout:g}s")
        except OSError as exc:
            return BridgeResult("unavailable", took_ms=_ms(started), error=f"powershell.exe did not start: {exc}")

        took = _ms(started)
        stdout = proc.stdout.decode("utf-8", errors="replace").strip()
        stderr = clean_stderr(proc.stderr.decode("utf-8", errors="replace"))

        if not stdout and WSL_INTEROP in stderr:
            return BridgeResult("unavailable", took_ms=took, returncode=proc.returncode, error=f"WSL could not start powershell.exe: {stderr}")

        if proc.returncode != 0 and not stdout:
            outcome: Outcome = "denied" if _looks_denied(stderr) else "failed"
            return BridgeResult(outcome, took_ms=took, returncode=proc.returncode, error=stderr or f"exit code {proc.returncode}")

        if not stdout:
            if _looks_denied(stderr):
                return BridgeResult("denied", took_ms=took, returncode=proc.returncode, error=stderr)
            if stderr and _looks_fatal(stderr):
                return BridgeResult("failed", took_ms=took, returncode=proc.returncode, error=stderr)
            return BridgeResult("empty", took_ms=took, returncode=proc.returncode, warnings=_lines(stderr))

        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            return BridgeResult("failed", took_ms=took, returncode=proc.returncode, error=f"output was not JSON: {exc}", warnings=_lines(stderr))

        items = parsed if isinstance(parsed, list) else [parsed]
        return BridgeResult("ok" if items else "empty", items=items, took_ms=took, returncode=proc.returncode, warnings=_lines(stderr))


def _working_directory() -> str | None:
    """Run from the system drive: powershell.exe started inside a WSL path warns and fails."""
    if sys.platform == "win32":
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/mnt/c" if os.path.isdir("/mnt/c") else None


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


_CLIXML_STRING = re.compile(r"<S S=\"(?:Error|Warning)\">(.*?)</S>", re.S)


def clean_stderr(text: str) -> str:
    """PowerShell wraps error records in CLIXML when its stderr is redirected. Unwrap them."""
    text = text.replace("\r", "")
    if "#< CLIXML" in text:
        parts = [_unescape(m) for m in _CLIXML_STRING.findall(text)]
        text = "\n".join(p for p in parts if p.strip())
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln.strip()).strip()


def _unescape(s: str) -> str:
    return (
        s.replace("_x000D_", "").replace("_x000A_", "\n").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")
    )


def _looks_denied(stderr: str) -> bool:
    low = stderr.lower()
    return any(marker in low for marker in _DENIED_MARKERS)


def _looks_fatal(stderr: str) -> bool:
    """An error record on stderr with nothing on stdout is a failure, not an empty result."""
    low = stderr.lower()
    return "exception" in low or "error" in low or "cannot" in low or "not recognized" in low


def _lines(stderr: str) -> list[str]:
    return [ln for ln in stderr.split("\n") if ln.strip()] if stderr else []
