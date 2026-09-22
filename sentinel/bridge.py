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

There are two ways a question reaches the machine and one place its answer is
read. A :class:`Pool` keeps a few live ``powershell.exe`` processes and feeds
each question to one of them on stdin; when no session can be started the
question is launched on its own, as every question used to be. Both transports
hand the same three things — what was written to stdout, what was written to
stderr, and whether the script raised — to :func:`classify`, so the six outcomes
are one rule rather than two. On the machine this was built for, on 2026-09-21,
a launch cost 231 ms before the script even ran and the same question through a
live session cost 9 ms; ``system-sentinel bench`` is what measures that here.
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import IO, Any, Literal

Outcome = Literal["ok", "empty", "failed", "unavailable", "denied", "timeout"]

OUTCOMES: tuple[Outcome, ...] = ("ok", "empty", "failed", "unavailable", "denied", "timeout")

DEFAULT_TIMEOUT = 60.0

_KNOWN_LOCATIONS = (
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
)

# WSL's interop layer failing to hand the process over; seen on this machine on 2026-09-20 as
# "<3>WSL (pid - ) ERROR: UtilAcceptVsock:271: accept4 failed 110". A restricted
# launch can fail earlier at UtilBindVsockAnyPort. Neither is a Windows error.
WSL_INTEROP = "UtilAcceptVsock"
WSL_INTEROP_ERRORS = (WSL_INTEROP, "UtilBindVsockAnyPort")
WSL_INTEROP_ATTEMPTS = 3

# Under WSL a launch takes a slot shared by every process that uses the bridge: a test suite, a
# server, an agent reading through the tool. The layer saturates across processes, not only inside
# one — on 2026-09-21 a dashboard loading three readings beside one running suite failed all three,
# and with two slots a single reading beside the suite still failed (friction F2); the only count
# the evidence supports is one launch at a time, machine-wide, which is how every suite that ran
# alone passed. The slot is a lock file, so nothing has to remember the rule. Native Windows never
# takes this path, and the retry in :meth:`Bridge.run` stays for whatever else launches at the same
# moment. Starting a session is a launch and takes the slot; asking a live session a question is
# not, and does not: a pool of four starts four processes once instead of twenty-two per capture.
WSL_LAUNCH_SLOTS = 1
_SLOT_POLL = 0.05

_DENIED_MARKERS = (
    "access is denied",
    "access denied",
    "unauthorized operation",
    "requires elevation",
    "permission denied",
    "administrator privileges",
)

# Redirect PowerShell's error stream into the child script's pipeline before it leaves the question.
# A formatted ErrorRecord can otherwise reach stderr after a session's closing mark. Enumerating the
# records here preserves handled errors (which never leave the child scope), keeps data beside a
# nonterminating error, and gives an error-only answer a nonzero status instead of a false `empty`.
# Direct console writes by a script still use stderr; those precede the mark on the same handle.
_COLLECT = (
    "$__raw = @(& {invocation} 2>&1); "
    "$__data = [System.Collections.Generic.List[object]]::new(); $__hadError = $false; "
    "foreach ($__value in $__raw) { "
    "if ($__value -is [System.Management.Automation.ErrorRecord]) { $__hadError = $true; [Console]::Error.WriteLine($__value.Exception.Message) } "
    "else { $__data.Add($__value) } }; "
    "if ($__data.Count -gt 0) { ConvertTo-Json -InputObject $__data.ToArray() -Depth {depth} -Compress -ErrorAction Stop }; "
    "if ($__hadError -and $__data.Count -eq 0) { $__c = 1 }"
)

# A one-shot process and a live session use the same collector and the same classifier.
_PRELUDE = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$ProgressPreference = 'SilentlyContinue'; $__c = 0; "
    "try { " + _COLLECT.replace("{invocation}", "{ {script} }") + " } "
    "catch { $__c = 1; [Console]::Error.WriteLine($_.Exception.Message) }; "
    "exit $__c"
)

# Keep argv independent of collector size: Windows' process command line is bounded,
# while dump inspection can exceed it after UTF-16/base64 expansion. The payload stays
# ASCII on stdin regardless of console input encoding; the existing prelude owns the
# collector, output encoding and exit status after the bootstrap decodes it.
_ONE_SHOT_BOOTSTRAP = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    "try { & ([scriptblock]::Create([Text.Encoding]::Unicode.GetString("
    "[Convert]::FromBase64String([Console]::In.ReadToEnd())))) } "
    "catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }"
)
_ONE_SHOT_COMMAND = base64.b64encode(_ONE_SHOT_BOOTSTRAP.encode("utf-16le")).decode("ascii")


class SlotTimeout(Exception):
    """Nothing came free within the question's own timeout — a launch slot another process is
    holding, or a session another question is using — so the question is reported as
    ``unavailable`` rather than waited for without end."""


class BridgeStopping(Exception):
    """Final process shutdown has begun; no new PowerShell child may start."""


class SessionStartFailed(Exception):
    """A live session could not be started, or would not answer its first question. The question
    goes to the one-shot transport; never lose a reading because a session would not start."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


class SessionLost(Exception):
    """A live session stopped answering in the middle of a question. The process is gone, so the
    question is asked again through another transport rather than answered from a dead pipe."""


@contextmanager
def _launch_slot(timeout: float) -> Iterator[None]:
    """Hold a cross-process launch slot for the duration, where launches go through WSL's interop
    layer. A slot is an exclusive lock on a file in the temporary directory; a launch tries each
    slot and waits a moment when all are taken, for at most the launch's own timeout. A lock file
    that cannot be opened (another user's, a read-only directory) is no coordination at all, so
    the launch goes ahead without one: the retry in :meth:`Bridge.run` still stands."""
    if sys.platform == "win32":
        yield
        return
    import fcntl

    directory = os.environ.get("TMPDIR", "/tmp")
    handles = []
    try:
        for i in range(WSL_LAUNCH_SLOTS):
            handles.append(open(os.path.join(directory, f"system-sentinel-launch-{i}.lock"), "w"))
    except OSError:
        for handle in handles:
            handle.close()
        yield
        return
    held = None
    deadline = time.monotonic() + timeout
    try:
        while held is None:
            with _POOLS_LOCK:
                if _SESSIONS_ENDED:
                    raise BridgeStopping("the bridge is shutting down")
            for handle in handles:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                held = handle
                break
            else:
                if time.monotonic() >= deadline:
                    raise SlotTimeout(f"no launch slot came free within {timeout:g}s: another process is holding the bridge")
                time.sleep(_SLOT_POLL)
        yield
    finally:
        if held is not None:
            fcntl.flock(held, fcntl.LOCK_UN)
        for handle in handles:
            handle.close()


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


def classify(stdout: str, stderr: str, returncode: int | None, took_ms: int) -> BridgeResult:
    """Turn what a script wrote into one of the six outcomes. The only place that decision is made.

    A launch and a live session produce the same three facts — what went to stdout, what went to
    stderr, and whether the script raised — so both hand them here rather than each deciding what
    they mean. That is what keeps the contract identical across transports: there is one rule, and
    a test of an outcome tests it whichever way the question was asked.

    The order matters and is the order of the evidence: WSL failing to hand a process over is the
    environment rather than the machine; a script that raised and said nothing is a failure, or a
    refusal where Windows said so; silence with nothing alarming on stderr is a finding, not an
    error; and anything on stdout has to be the JSON document the frame asked for.
    """
    out = _clean_stdout(stdout)
    err = clean_stderr(stderr)

    if not out and _wsl_interop_error(err):
        return BridgeResult("unavailable", took_ms=took_ms, returncode=returncode, error=f"WSL could not start powershell.exe: {err}")

    if returncode is not None and returncode != 0 and not out:
        outcome: Outcome = "denied" if _looks_denied(err) else "failed"
        return BridgeResult(outcome, took_ms=took_ms, returncode=returncode, error=err or f"exit code {returncode}")

    if not out:
        if _looks_denied(err):
            return BridgeResult("denied", took_ms=took_ms, returncode=returncode, error=err)
        if err and _looks_fatal(err):
            return BridgeResult("failed", took_ms=took_ms, returncode=returncode, error=err)
        return BridgeResult("empty", took_ms=took_ms, returncode=returncode, warnings=_lines(err))

    try:
        parsed = json.loads(out)
    except json.JSONDecodeError as exc:
        return BridgeResult("failed", took_ms=took_ms, returncode=returncode, error=f"output was not JSON: {exc}", warnings=_lines(err))

    items = parsed if isinstance(parsed, list) else [parsed]
    return BridgeResult("ok" if items else "empty", items=items, took_ms=took_ms, returncode=returncode, warnings=_lines(err))


@dataclass(frozen=True)
class Bridge:
    """A located ``powershell.exe``, or the fact that there is none."""

    exe: str | None
    cwd: str | None = None

    @classmethod
    def locate(cls) -> Bridge:
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
        """Ask the machine one question and get an outcome back.

        The question goes to a live session when the pool can give it one and to a process of its
        own when it cannot — same script, same classifier, same six outcomes, so nothing above this
        seam can tell which it was. From WSL the interop layer occasionally fails to hand a process
        over (``UtilAcceptVsock ... accept4 failed``); that is the environment, not the machine, so
        it is retried a bounded number of times with a short pause and otherwise reported as
        ``unavailable``. The retry sits above the transport because a session is started by a launch
        like any other. Native Windows never sees this path.
        """
        if self.exe is None:
            return BridgeResult("unavailable", error="powershell.exe was not found")
        result = self._answer(script, timeout=timeout, depth=depth)
        for attempt in range(1, WSL_INTEROP_ATTEMPTS):
            if not (result.outcome == "unavailable" and result.error and _wsl_interop_error(result.error)):
                break
            time.sleep(0.5 * attempt)
            result = self._answer(script, timeout=timeout, depth=depth)
        return result

    def _answer(self, script: str, *, timeout: float, depth: int) -> BridgeResult:
        """One attempt, through whichever transport can take it: a live session for preference, a
        launch of its own when the pool is switched off or no session would start. Terminal
        shutdown refuses a new launch."""
        pool = _pool_for(self)
        if pool is not None:
            answered = pool.ask(script, timeout=timeout, depth=depth)
            if answered is not None:
                return answered
        with _POOLS_LOCK:
            if _SESSIONS_ENDED:
                return BridgeResult("unavailable", error="the bridge is shutting down")
        try:
            with _launch_slot(timeout):
                with _POOLS_LOCK:
                    if _SESSIONS_ENDED:
                        return BridgeResult("unavailable", error="the bridge is shutting down")
                return self._run_once(script, timeout=timeout, depth=depth)
        except BridgeStopping:
            return BridgeResult("unavailable", error="the bridge is shutting down")
        except SlotTimeout as exc:
            return BridgeResult("unavailable", error=str(exc))

    def _run_once(self, script: str, *, timeout: float, depth: int) -> BridgeResult:
        """The one-shot transport: one script, one process, one answer."""
        if self.exe is None:
            return BridgeResult("unavailable", error="powershell.exe was not found")

        full = _PRELUDE.replace("{script}", script).replace("{depth}", str(depth))
        encoded = base64.b64encode(full.encode("utf-16le"))
        cmd = [self.exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", _ONE_SHOT_COMMAND]

        started = time.perf_counter()
        try:
            proc = _spawn_child(cmd, self.cwd)
        except BridgeStopping:
            return BridgeResult("unavailable", took_ms=_ms(started), error="the bridge is shutting down")
        except OSError as exc:
            return BridgeResult("unavailable", took_ms=_ms(started), error=f"powershell.exe did not start: {exc}")

        timed_out = False
        try:
            try:
                stdout, stderr = proc.communicate(input=encoded, timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _end_child(proc)
                stdout = stderr = b""
            except BaseException:
                _end_child(proc)
                raise
        finally:
            with _POOLS_LOCK:
                interrupted = _CHILDREN.get(proc, False)
                if proc.poll() is not None:
                    _CHILDREN.pop(proc, None)
        if interrupted:
            return BridgeResult("unavailable", took_ms=_ms(started), error="the bridge is shutting down")
        if timed_out:
            return BridgeResult("timeout", took_ms=_ms(started), error=f"no answer within {timeout:g}s")
        return classify(
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            proc.returncode,
            _ms(started),
        )


# --- the session transport ---------------------------------------------------------------------

#: How many live sessions this process keeps, from ``SENTINEL_BRIDGE_SESSIONS``. Four was measured
#: on this machine on 2026-09-21: four sessions cost 612 ms to start and then answered a hundred
#: questions in 615 ms together, less than three of the launches they replace. ``0`` switches the
#: sessions off and every question is a launch again, which is the fallback in configuration as
#: well as in code.
DEFAULT_POOL_SIZE = 4

#: A session is retired after this many questions. Two hundred were measured through one session
#: with no drift — first fifty median 19 ms, last fifty 18 ms, 90 MB resident — so the bound sits
#: at what was observed rather than past it.
SESSION_QUESTIONS = 200

#: How long a new session has to answer its first question before it is judged not to have started.
SESSION_START_TIMEOUT = 20.0

#: How long the pool waits before trying to start a session again after one would not start. Until
#: then questions go through the one-shot transport rather than paying a failed launch each time.
START_RETRY_SECONDS = 30.0

#: Set once when a session starts, instead of in front of every script the way a launch has to.
_SESSION_PRELUDE = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'"

#: What a question looks like on a session's stdin: one line, whatever the script looks like.
#:
#: The script travels base64-encoded UTF-16, the encoding ``-EncodedCommand`` already takes, so a
#: script with newlines and comments in it stays one line here and the line itself stays ASCII
#: whatever the console's input encoding is. ``&`` runs it in a child scope, which is where the
#: isolation between questions comes from: on 2026-09-21 a variable set by one question was not
#: visible to the next, and the frame's own variables were not visible to either.
#:
# The shared collector folds PowerShell ErrorRecords into this question before the marker is
# written, so PowerShell's asynchronous error formatting cannot assign them to the next question.
# The ``finally`` is what makes the frame closeable: however the script fails — a terminating
# error, a cmdlet error, no output at all — the mark is written and the question is answered. The
#: mark is fresh for every question, so output that happens to look like a mark cannot close a
#: frame early and a late line from a question that timed out is recognisable as stale. The catch
# writes the message to stderr rather than carrying it on the mark, so the classifier sees exactly
# what a launch would see, and ``$__c`` is the returncode it is given: 1 when the script raised or
# only produced errors, 0 otherwise. ``$Error`` is deliberately not consulted — it holds errors the
# script caught and handled, and reporting those would turn every no-match into a failure.
#:
#: The ``finally`` closes stderr with the same mark before it closes stdout with it, so a question
#: knows where its own stderr ends instead of pausing after every answer to find out: everything
#: the script wrote on that stream was written before the mark, and the mark is already on the wire
#: when the answer arrives.
_FRAME = (
    "$__c = 0; try { " + _COLLECT.replace("{invocation}", "([scriptblock]::Create([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{payload}'))))") + " } "
    "catch { $__c = 1; [Console]::Error.WriteLine($_.Exception.Message) } "
    'finally { [Console]::Error.WriteLine("{mark}"); "{mark}`t$__c" }'
)


def _frame(script: str, depth: int, mark: str) -> str:
    payload = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return _FRAME.replace("{payload}", payload).replace("{depth}", str(depth)).replace("{mark}", mark)


def _reader(
    name: str,
    pipe: IO[bytes] | None,
    into: queue.Queue[str | None],
    observe: Callable[[str], None] | None = None,
) -> threading.Thread:
    """Drain one pipe into a queue, line by line, until it ends.

    Both pipes get one of these because a full stderr buffer stops the process writing stdout, and
    a session that cannot write its mark never answers. ``None`` on the queue is the pipe's end.
    """

    def drain() -> None:
        try:
            if pipe is not None:
                for raw in pipe:
                    # The BOM belongs to the console encoding the prelude set, not to the answer.
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n").lstrip("\ufeff")
                    if observe is not None:
                        observe(line)
                    into.put(line)
        except (OSError, ValueError):
            pass  # the pipe was closed under us: the session is going
        finally:
            into.put(None)

    thread = threading.Thread(target=drain, name=name, daemon=True)
    thread.start()
    return thread


class Session:
    """One live ``powershell.exe``, answering question after question on stdin.

    A session is discarded, never reused, when a frame does not close within its timeout (the
    script is still running in there and nothing else can be asked down that pipe), when the
    process has died, and when it has answered :data:`SESSION_QUESTIONS` questions. Discarding
    kills the process, because a session that is not answering questions is a process nobody owns.
    """

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self._proc = proc
        self._out: queue.Queue[str | None] = queue.Queue()
        self._err: queue.Queue[str | None] = queue.Queue()
        self._startup_interop = threading.Event()  # a category, never the stderr text
        self._watching_startup = True
        self._readers = (
            _reader(f"sentinel-session-{proc.pid}-out", proc.stdout, self._out),
            _reader(f"sentinel-session-{proc.pid}-err", proc.stderr, self._err, self._observe_startup_stderr),
        )
        self.started_at = time.monotonic()
        self.answered = 0
        self.discarded: str | None = None

    @classmethod
    def start(cls, bridge: Bridge, *, timeout: float) -> Session:
        """Start one, and prove it answers before anyone's question is bound to it.

        The launch slot is held across the whole of it — the process, the prelude and the first
        question — because starting a session is a launch through WSL's interop layer like any
        other, and because a session that is half up is not one. A session that will not answer
        raises, and the question that wanted it goes to the one-shot transport.
        """
        if bridge.exe is None:
            raise SessionStartFailed("missing_executable", "powershell.exe was not found")
        cmd = [bridge.exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"]
        try:
            with _launch_slot(timeout):
                proc = _spawn_child(cmd, bridge.cwd)
                session: Session | None = None
                try:
                    session = cls(proc)
                    session._write(_SESSION_PRELUDE)
                    probe = session.ask("", timeout=min(timeout, SESSION_START_TIMEOUT), depth=1)
                except SessionLost as exc:
                    assert session is not None
                    session.discard("start")
                    if _child_was_interrupted(proc):
                        reason = "shutdown"
                    else:
                        reason = "wsl_interop" if session._startup_interop.is_set() else "probe_lost"
                    raise SessionStartFailed(reason, f"the session ended before it answered: {exc}") from exc
                except BaseException:
                    if session is None:
                        _end_child(proc)
                    else:
                        session.discard("start")
                    raise
                if not probe.observed:
                    session.discard("start")
                    interop = session._startup_interop.is_set() or bool(probe.error and _wsl_interop_error(probe.error))
                    if _child_was_interrupted(proc):
                        reason = "shutdown"
                    else:
                        reason = "wsl_interop" if interop else f"probe_{probe.outcome}"
                    raise SessionStartFailed(reason, f"the session did not answer its first question: {probe.outcome}")
                session.answered = 0  # the probe is nobody's question
                session._watching_startup = False
                return session
        except SlotTimeout as exc:
            raise SessionStartFailed("launch_slot_timeout", str(exc)) from exc
        except BridgeStopping as exc:
            raise SessionStartFailed("shutdown", str(exc)) from exc
        except OSError as exc:
            raise SessionStartFailed("process_start_error", f"powershell.exe did not start: {exc}") from exc

    @property
    def alive(self) -> bool:
        return self.discarded is None and self._proc.poll() is None

    @property
    def age(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def pid(self) -> int:
        return self._proc.pid

    def _observe_startup_stderr(self, line: str) -> None:
        # The probe has no preceding question. Record only the known launch-failure signature,
        # before a queue drain or the other reader's EOF can make that evidence disappear.
        if self._watching_startup and _wsl_interop_error(line):
            self._startup_interop.set()

    def ask(self, script: str, *, timeout: float, depth: int) -> BridgeResult:
        """Ask this session one question. Raises :class:`SessionLost` if the process went away."""
        self._drop_stale_stderr()
        mark = uuid.uuid4().hex
        started = time.perf_counter()
        self._write(_frame(script, depth, mark))

        deadline = time.monotonic() + timeout
        lines: list[str] = []
        code: int | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # The frame never closed: the script is still running in there. Nothing else can be
                # asked down this pipe, so the session goes with the question.
                self.discard("timeout")
                return BridgeResult("timeout", took_ms=_ms(started), error=f"no answer within {timeout:g}s")
            try:
                line = self._out.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                self.discard("died")
                raise SessionLost("the session ended in the middle of a question")
            if line.startswith(mark):
                code = _mark_code(line, mark)
                break
            lines.append(line)

        stderr = self._stderr_for_this_question(mark, deadline)
        if stderr is None:
            # A stdout mark alone is not an answer: the error stream may still hold the reason
            # this question failed. Never turn an incomplete frame into an observed empty result.
            self.discard("stderr")
            return BridgeResult("unavailable", took_ms=_ms(started), error="the session's error stream did not close this answer")

        self.answered += 1
        return classify("\n".join(lines), stderr, code, _ms(started))

    def discard(self, why: str, *, grace: float = 0.0) -> None:
        """End this session. Idempotent, and the first reason given is the one kept.

        ``grace`` lets a session that is only being put away end by itself: closing stdin is how
        ``powershell.exe -Command -`` is told there is nothing more to read. A session that is
        still running a script is killed, because asking it politely would be waiting for the
        script that overran.
        """
        if self.discarded is not None:
            return
        self.discarded = why
        proc = self._proc
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        if grace > 0:
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        if proc.poll() is not None:
            # A reaped child's pipes normally reach EOF. Let the readers observe their buffered
            # tail before closing the streams, with one bound shared by both reader threads.
            drain_by = time.monotonic() + 0.5
            for reader in self._readers:
                reader.join(timeout=max(0.0, drain_by - time.monotonic()))
        for pipe in (proc.stdout, proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except OSError:
                pass
        for reader in self._readers:
            reader.join(timeout=1)

    def _write(self, line: str) -> None:
        stdin = self._proc.stdin
        if stdin is None:
            raise SessionLost("the session has no input")
        try:
            stdin.write(line.encode("utf-8") + b"\n")
            stdin.flush()
        except (OSError, ValueError) as exc:
            self.discard("died")
            raise SessionLost(f"the session stopped listening: {exc}") from exc

    def _stderr_for_this_question(self, mark: str, deadline: float) -> str | None:
        """The lines stderr carried while this question was being answered, up to its own mark.

        Questions in a session are serialized, so a line that arrives between sending a question
        and seeing its answer belongs to it. Which lines those are is not left to timing: the
        frame writes the mark to stderr before it writes it to stdout, so by the time the answer
        has been read the end of its stderr is already on the way. The question's own deadline
        bounds the wait. If the mark never arrives, the answer is incomplete and the session
        cannot be reused.
        """
        parts: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                line = self._err.get(timeout=remaining)
            except queue.Empty:
                return None
            if line is None:
                return None
            if line.startswith(mark):
                return "\n".join(parts)
            parts.append(line)

    def _drop_stale_stderr(self) -> None:
        """A line after the previous frame's mark cannot belong to the next question. Drop it
        rather than attach an old error to a new answer."""
        while True:
            try:
                self._err.get_nowait()
            except queue.Empty:
                return


class Pool:
    """A few live sessions, lent out one question at a time.

    Sessions are started lazily, up to :attr:`size`, and the pool is the concurrency cap: a
    question waits for a session rather than starting a process of its own, which is the same
    bound the old per-process launch semaphore expressed and one mechanism instead of two. A
    session that comes back discarded is simply not kept; the next question that needs one starts
    a fresh session in its place.
    """

    def __init__(self, bridge: Bridge, size: int) -> None:
        self.bridge = bridge
        self.size = max(1, size)
        self._lock = threading.Condition()
        self._sessions: list[Session] = []  # every live session, idle or out with a question
        self._idle: list[Session] = []
        self._starting = 0
        self._closed = False
        self._cooldown_until = 0.0
        self.answered = 0
        self.fell_back = 0
        self.start_failures = 0
        self.last_start_failure: str | None = None
        self.discarded: dict[str, int] = {}

    def ask(self, script: str, *, timeout: float, depth: int) -> BridgeResult | None:
        """Answer one question from a live session, or return ``None`` to say it could not: no
        session would start, and the question belongs to the one-shot transport."""
        try:
            session = self._checkout(timeout)
        except SlotTimeout as exc:
            return BridgeResult("unavailable", error=str(exc))
        if session is None:
            with self._lock:
                self.fell_back += 1
            return None
        try:
            result = session.ask(script, timeout=timeout, depth=depth)
        except SessionLost:
            self._release(session)
            with self._lock:
                self.fell_back += 1
            return None
        except BaseException:
            self._release(session)
            raise
        self._release(session)
        with self._lock:
            self.answered += 1
        return result

    def shutdown(self) -> None:
        """Stop every session this pool holds, including one that is out with a question: at the
        end of a process that is exactly the session that would be left behind. Each is given a
        moment to end by itself first. A question that loses its session can fall back to a launch
        during a pool reset, but terminal shutdown refuses that launch.
        Startup runs outside the lock; if it finishes after closure, checkout disposes of that
        session before lending it. Shutdown does not wait for an in-progress startup probe.
        """
        with self._lock:
            self._closed = True
            going = list(self._sessions)
            self._sessions.clear()
            self._idle.clear()
            self._count("shutdown", len(going))
            self._lock.notify_all()
        for session in going:
            session.discard("shutdown", grace=1.0)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            oldest = max((session.age for session in self._sessions), default=None)
            return {
                "alive": len(self._sessions),
                "idle": len(self._idle),
                "answered": self.answered,
                "discarded": dict(self.discarded),
                "oldest_seconds": oldest,
                "fell_back": self.fell_back,
                "start_failures": self.start_failures,
                "last_start_failure": self.last_start_failure,
            }

    # --- the lending itself ---

    @property
    def _live(self) -> int:
        """Sessions this pool is answerable for, counting one that is on its way up. Read under
        the lock: it is what the pool's size bounds."""
        return len(self._sessions) + self._starting

    def _checkout(self, timeout: float) -> Session | None:
        deadline = time.monotonic() + timeout
        dead: list[tuple[Session, str]] = []
        chosen: Session | None = None
        start_new = False
        try:
            with self._lock:
                while True:
                    while self._idle and chosen is None:
                        session = self._idle.pop()
                        if not session.alive:
                            dead.append((session, "died"))
                            self._retire(session, "died")
                        elif session.answered >= SESSION_QUESTIONS:
                            dead.append((session, "recycled"))
                            self._retire(session, "recycled")
                        else:
                            chosen = session
                    if chosen is not None or self._closed:
                        break
                    if self._live < self.size and time.monotonic() >= self._cooldown_until:
                        self._starting += 1
                        start_new = True
                        break
                    if self._live == 0:
                        break  # nothing is alive and starting one is on cooldown: the launch path
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SlotTimeout(f"no bridge session came free within {timeout:g}s")
                    self._lock.wait(remaining)
        finally:
            # Ending a process is not done under the lock: no other question waits on it.
            for session, why in dead:
                session.discard(why)
        if chosen is not None:
            return chosen
        if not start_new:
            return None
        try:
            started = Session.start(self.bridge, timeout=timeout)
        except SessionStartFailed as exc:
            with self._lock:
                self._starting -= 1
                if exc.reason != "shutdown" and not self._closed:
                    self.start_failures += 1
                    self.last_start_failure = exc.reason
                    self._cooldown_until = time.monotonic() + START_RETRY_SECONDS
                self._lock.notify()
            return None
        with self._lock:
            self._starting -= 1
            if not self._closed:
                self._sessions.append(started)
                return started
            self._count("shutdown", 1)
            self._lock.notify_all()
        # Shutdown could not collect a session still owned by startup. Retire it here, outside
        # the lock, before the pending question falls back to its own one-shot process.
        started.discard("shutdown")
        return None

    def _release(self, session: Session) -> None:
        """Take a session back: onto the idle list if it is still good for another question, out of
        the pool's count if it is not."""
        retire: str | None = None
        with self._lock:
            if session.discarded is not None:
                retire = session.discarded
            elif not session.alive:
                retire = "died"
            elif session.answered >= SESSION_QUESTIONS:
                retire = "recycled"
            elif self._closed:
                retire = "shutdown"
            else:
                self._idle.append(session)
            if retire is not None:
                self._retire(session, retire)
            self._lock.notify()
        if retire is not None:
            session.discard(retire)

    def _retire(self, session: Session, why: str) -> None:
        """One fewer session, and why. Accounting only, under the lock: ending the process itself
        is done outside it, so nothing else waits for a kill. A session :meth:`shutdown` has
        already taken is counted once, there, not again when its question hands it back."""
        if session in self._sessions:
            self._sessions.remove(session)
            self._count(why, 1)
        self._lock.notify()

    def _count(self, why: str, n: int) -> None:
        if n:
            self.discarded[why] = self.discarded.get(why, 0) + n


def _pool_size() -> int:
    """How many sessions to keep, from the environment. Anything that is not a number is the
    default; zero, or less, is the one-shot transport and nothing else."""
    raw = os.environ.get("SENTINEL_BRIDGE_SESSIONS")
    if raw is None:
        return DEFAULT_POOL_SIZE
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_POOL_SIZE


POOL_SIZE = _pool_size()

_POOLS: dict[Bridge, Pool] = {}
_POOLS_LOCK = threading.Lock()
_SESSIONS_ENDED = False
_POPEN = subprocess.Popen
# Weak keys retain an interruption marker until the owning thread is done with its process.
_CHILDREN: weakref.WeakKeyDictionary[subprocess.Popen[bytes], bool] = weakref.WeakKeyDictionary()


def _spawn_child(cmd: list[str], cwd: str | None) -> subprocess.Popen[bytes]:
    """Start and record one bridge child atomically with final shutdown."""
    with _POOLS_LOCK:
        if _SESSIONS_ENDED:
            raise BridgeStopping("the bridge is shutting down")
        proc: subprocess.Popen[bytes] = _POPEN(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        _CHILDREN[proc] = False
        return proc


def _child_was_interrupted(proc: subprocess.Popen[bytes]) -> bool:
    with _POOLS_LOCK:
        return _CHILDREN.get(proc, False)


def _end_child(proc: subprocess.Popen[bytes]) -> None:
    """Stop a child whose owning question failed, with a bounded pipe drain."""
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.communicate(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except OSError:
                pass


def _pool_for(bridge: Bridge) -> Pool | None:
    """The pool for this ``powershell.exe``, made the first time a question needs it. Equal bridges
    share one pool, so a process that locates the bridge twice does not keep twice the sessions."""
    if bridge.exe is None:
        return None
    with _POOLS_LOCK:
        if _SESSIONS_ENDED or POOL_SIZE <= 0:
            return None
        pool = _POOLS.get(bridge)
        if pool is None:
            pool = Pool(bridge, POOL_SIZE)
            _POOLS[bridge] = pool
        return pool


def shutdown_sessions() -> None:
    """End every live session this process started and refuse new questions afterward.

    Registered on ``atexit``, called from the server's lifespan shutdown and from the launcher's
    serverless quit path. Benchmark reconfiguration and test setup use :func:`reset_sessions`
    to start a fresh pool explicitly.
    """
    global _SESSIONS_ENDED
    with _POOLS_LOCK:
        _SESSIONS_ENDED = True
        pools = list(_POOLS.values())
        _POOLS.clear()
        running = [proc for proc in _CHILDREN if proc.poll() is None]
        for proc in running:
            _CHILDREN[proc] = True
    # Close pools first so their session shutdowns are counted before killed readers return.
    for pool in pools:
        pool.shutdown()
    # A session still proving startup and a one-shot already answering are not in a pool's list.
    # Their owning threads drain the pipes; shutdown only needs to stop the child promptly.
    for proc in running:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass


def reset_sessions(size: int | None = None) -> None:
    """End current pools, then allow new ones for an explicit transport reset."""
    global _SESSIONS_ENDED, POOL_SIZE
    if size is not None and size < 0:
        raise ValueError("session pool size cannot be negative")
    with _POOLS_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
        # An explicit reset is rare and synchronous. Keep the registry closed until old children
        # are gone, so a concurrent final shutdown cannot be undone halfway through this reset.
        for pool in pools:
            pool.shutdown()
        if size is not None:
            POOL_SIZE = size
        _SESSIONS_ENDED = False


atexit.register(shutdown_sessions)


def sessions_report(bridge: Bridge | None = None) -> dict[str, Any]:
    """What the transport is doing, in counts: which one questions are going through, how many
    sessions are alive and how old the oldest is, how many questions they have answered, how many
    sessions were discarded and for which reason, and how many questions fell back to a launch.

    Counts, never a verdict. The last session-start failure is a bounded reason, with no path or
    raw stderr. A fallback is a number here and a warning on the reading that reports it; the
    reading of what that means belongs to whoever is holding the evidence.
    """
    with _POOLS_LOCK:
        pools = [pool for key, pool in _POOLS.items() if bridge is None or key == bridge]
        ended = _SESSIONS_ENDED
        active_size = 0 if ended else POOL_SIZE
    report: dict[str, Any] = {
        "transport": "stopped" if ended else "session" if active_size > 0 else "one-shot",
        "size": active_size,
        "alive": 0,
        "idle": 0,
        "answered": 0,
        "discarded": {},
        "oldest_seconds": None,
        "fell_back": 0,
        "start_failures": 0,
        "last_start_failure": None,
    }
    oldest: float | None = None
    for pool in pools:
        stats = pool.stats()
        for key in ("alive", "idle", "answered", "fell_back", "start_failures"):
            report[key] += stats[key]
        if stats["last_start_failure"] is not None:
            report["last_start_failure"] = stats["last_start_failure"]
        for why, count in stats["discarded"].items():
            report["discarded"][why] = report["discarded"].get(why, 0) + count
        if stats["oldest_seconds"] is not None:
            oldest = stats["oldest_seconds"] if oldest is None else max(oldest, stats["oldest_seconds"])
    report["oldest_seconds"] = round(oldest, 1) if oldest is not None else None
    return report


def _working_directory() -> str | None:
    """Run from the system drive: powershell.exe started inside a WSL path warns and fails."""
    if sys.platform == "win32":
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/mnt/c" if os.path.isdir("/mnt/c") else None


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _mark_code(line: str, mark: str) -> int | None:
    """The ``$__c`` the frame's ``finally`` wrote beside the mark: 1 when the script raised."""
    tail = line[len(mark) :].strip()
    try:
        return int(tail)
    except ValueError:
        return None


_CLIXML_STRING = re.compile(r"<S S=\"(?:Error|Warning)\">(.*?)</S>", re.S)


def _clean_stdout(text: str) -> str:
    return text.lstrip("\ufeff").strip()


def clean_stderr(text: str) -> str:
    """PowerShell wraps error records in CLIXML when its stderr is redirected. Unwrap them."""
    text = text.replace("\r", "")
    if "#< CLIXML" in text:
        parts = [_unescape(m) for m in _CLIXML_STRING.findall(text)]
        text = "\n".join(p for p in parts if p.strip())
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln.strip()).strip()


def _wsl_interop_error(text: str) -> bool:
    return any(marker in text for marker in WSL_INTEROP_ERRORS)


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
