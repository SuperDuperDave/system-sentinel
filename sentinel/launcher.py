"""The launcher: double-click, and the tool is running.

One process on the person's side of the boundary. It starts the API on this machine if nothing is
serving yet, opens the dashboard in the browser already signed in, and sits in the tray as the mark.
The CLI, the API and the MCP address stay underneath, unchanged, for the expert and for an agent.

The person never sees the token. Signing the browser in is a one-time code (:func:`sentinel.auth.mint_code`)
spent once at ``GET /api/session/open``, which only this machine may call; the token itself stays in
the data directory where an agent reads it on purpose. Nothing in the tray ever displays it. Signing
another device in is the same door: the tray opens the dashboard on its sign-in link, which asks the
machine for the address it publishes on a private network and draws a fresh code as a QR code.

The executable also puts itself where it belongs. One file is the whole tool, so the file a person
downloads is also the installer and the updater: started from anywhere but the data directory, it
copies itself in, starts the copy and steps aside; started while an older copy is serving, it asks
that one to quit and takes its place. :func:`plan` is that decision, and nothing else makes it.

The tray is the optional ``[launcher]`` extra (pystray and Pillow); the server itself stays
dependency-free. Without pystray the launcher does the same work and says how to quit. A windowed
executable has no console to explain itself in, so what a person has to know — the port is held, no
browser answered, a newer version is already running — arrives in a native message box.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import logging.handlers
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__
from .auth import load_or_create_token, mint_code
from .paths import data_dir

try:  # the tray and the mark are the optional extra
    from PIL import Image, ImageColor, ImageDraw, ImageFilter
except ImportError:  # pragma: no cover - exercised by not installing the extra
    Image = ImageColor = ImageDraw = ImageFilter = None  # type: ignore[assignment]

try:
    import pystray
except ImportError:  # pragma: no cover - exercised by not installing the extra
    pystray = None  # type: ignore[assignment]

LOG = logging.getLogger("sentinel.launcher")

DEFAULT_PORT = 8000
#: How long a start waits for the server to answer before it says it could not start. Generous
#: against the few seconds a start takes on a clean machine, and short enough that the person in
#: front of a windowed program learns something went wrong while they are still watching.
READY_TIMEOUT = 15.0
BROWSER_TIMEOUT = 30.0
#: How long a copy being replaced is given to answer, stop and let go of the port.
QUIT_TIMEOUT = 20.0
STARTUP_LINK = "System Sentinel.lnk"
INSTALLED_NAME = "SystemSentinel.exe"
LOG_NAME = "launcher.log"
#: Where a person looks for a newer one. The tool opens this page and asks nothing of it itself:
#: looking for an update sends nothing about this machine anywhere.
RELEASES = "https://github.com/SuperDuperDave/system-sentinel/releases/latest"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

#: The trace, on a 64-unit grid, copied from ``dashboard/src/Mark.tsx`` so the tray and the
#: dashboard draw the same S. Values owned by the identity note.
S_PATH = "M45 19C42 12 22 11 21 20C20 29 44 30 44 41C44 50 23 53 18 45"
FIELD = "#0B1A16"
PHOSPHOR_CORE = "#C9FFE1"
PHOSPHOR = "#6FF0A8"
BEAM = (18, 45)

#: The one seam a test moves: a request in, a status and a body out.
Fetch = Callable[..., "tuple[int | None, str]"]


# --- where the server is ----------------------------------------------------------------------


def port() -> int:
    """The port to serve on: ``SENTINEL_PORT`` when set, 8000 otherwise."""
    try:
        return int(os.environ.get("SENTINEL_PORT", DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def base_url(listen_port: int) -> str:
    return f"http://127.0.0.1:{listen_port}"


def _ask(url: str, headers: dict, method: str = "GET") -> tuple[int | None, str]:
    """The status code and the body, or None and nothing when nobody answered."""
    request = urllib.request.Request(url, headers=headers, method=method, data=b"" if method == "POST" else None)
    try:
        # Short: everything here is on this machine, and a stranger holding the port answers
        # nothing at all, so a long timeout only makes a failure take longer to notice.
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except OSError:
        return None, ""


def serving_version(base: str, token: str, ask: Fetch = _ask) -> str | None:
    """The version of this machine's server at this address, or None when it is not ours.

    Only a reading request answered with this machine's token counts: nothing listening, a stranger
    on the port and another machine's token all read the same, so a launcher never assumes. The
    catalog carries the version, so one request answers both *is it there* and *which one is it* —
    which is what a copy that may be an update needs to know before it does anything.
    """
    status, body = ask(f"{base}/api/readings", {"Authorization": f"Bearer {token}"})
    if status != 200:
        return None
    try:
        return str(json.loads(body).get("version") or "")
    except ValueError:
        return ""


def already_serving(base: str, token: str, ask: Fetch = _ask) -> bool:
    """True when this machine's server is answering at this address."""
    return serving_version(base, token, ask) is not None


def wait_until_serving(base: str, token: str, timeout: float = READY_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if already_serving(base, token):
            return True
        time.sleep(0.2)
    return False


def ask_to_quit(base: str, token: str, ask: Fetch = _ask) -> bool:
    """Ask the server at this address to stop, with the token in hand. True when it accepted.

    The token travels because the route insists on it: a browser's cookie cannot switch the tool
    off, and only something that can read the data directory — this machine's launcher, an agent
    running here — can ask.
    """
    status, _ = ask(f"{base}/api/quit", {"Authorization": f"Bearer {token}"}, "POST")
    LOG.info("the copy already serving was asked to quit; it answered %s", status)
    return status == 202


def port_free(listen_port: int) -> bool:
    """True when nothing accepts a connection on this port.

    Waiting for the answers to stop is not enough: a process that has been asked to quit keeps the
    listening socket until it has finished going, and the copy taking its place cannot bind until
    then.
    """
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", listen_port)) != 0


def wait_until_free(listen_port: int, timeout: float = QUIT_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_free(listen_port):
            return True
        time.sleep(0.2)
    return port_free(listen_port)


class Server:
    """The one way this tool runs uvicorn, for the tray and for ``serve`` alike.

    :meth:`start` puts it on a background thread so the tray can own the main one; :meth:`run`
    keeps it on the thread it was called from, which is what a terminal came for. Either way there
    is one way it ends — :meth:`quit` — and the state carries that as its quit callback, so the
    tray's Quit, ``POST /api/quit`` and a newer copy arriving all stop the server the same way.
    """

    def __init__(self, host: str, listen_port: int, console: bool = False):
        import uvicorn

        from .app import State, create_app

        self.state = State()
        self.state.on_quit = self.quit
        # A windowed exe has no console: leaving logging alone keeps the launcher's own file the
        # story of the launch, and no access log grows for as long as the machine is switched on.
        # Someone who typed `serve` in a terminal came for exactly those two things, so they get
        # uvicorn's own configuration instead.
        config = uvicorn.Config(
            create_app(self.state),
            host=host,
            port=listen_port,
            log_level="info",
            log_config=uvicorn.config.LOGGING_CONFIG if console else None,
            access_log=console,
            ws="none",
            # The stream is server-sent events: a connection that by design never ends. A shutdown
            # that waits for every connection to close would therefore wait for as long as a
            # dashboard is open, and a newer copy waiting for the port would give up. A few
            # seconds is long enough for a reading in flight and short enough to be a quit.
            timeout_graceful_shutdown=3,
        )
        self._server = uvicorn.Server(config)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.run, name="sentinel-api", daemon=True)
        self._thread.start()

    def run(self) -> None:
        """Serve here, until something asks it to stop."""
        self._server.run()

    def quit(self) -> None:
        """Ask uvicorn to finish what it is answering and exit.

        It signals and returns rather than waiting, because one of its callers is a request being
        answered by this very server: waiting for the thread it is running on would be waiting for
        itself."""
        LOG.info("the server was asked to stop")
        self._server.should_exit = True

    def stop(self) -> None:
        """Ask it to stop and wait for the thread it was started on."""
        self.quit()
        if self._thread is not None:
            self._thread.join(timeout=10)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# --- where the program is ----------------------------------------------------------------------

#: The six things a start can decide to do. One is chosen once, in :func:`plan`.
SERVE = "serve"  # this is the installed copy, or a development run: serve here
OPEN = "open"  # the same tool is already serving: open the dashboard and step aside
NEWER = "newer"  # something newer is serving: never downgrade; open the dashboard and say so
REPLACE = "replace"  # something older is serving: ask it to quit, take its place, start it
INSTALL = "install"  # nothing is serving and home holds another copy or none: put this one there
START = "start"  # home already holds this copy: start it


@dataclass(frozen=True)
class Plan:
    """What a start does, and the sentence that explains it to a log or to a person."""

    do: str
    why: str


def frozen() -> bool:
    """Whether this is the built executable rather than an interpreter running the package."""
    return bool(getattr(sys, "frozen", False))


def installed_exe() -> Path:
    """Where the program lives: one file in the data directory, beside the token it protects."""
    return data_dir() / INSTALLED_NAME


def this_exe() -> Path | None:
    """The executable running this code, or None when it is an interpreter and not the tool."""
    return Path(sys.executable).resolve() if frozen() else None


def log_path() -> Path:
    return data_dir() / LOG_NAME


def file_version(path: Path) -> str | None:
    """The ``ProductVersion`` Windows keeps in a file's properties, or None when it cannot be read.

    The version of a copy that is not running has to be readable without starting it, and this is
    the fact Explorer shows on the Properties page: the resource the build stamps in. A file with
    no such resource — an older build, something else entirely — reads as None, and :func:`_parts`
    sorts None below every real version, so an unreadable copy is never mistaken for a newer one.
    """
    if sys.platform != "win32" or not path.exists():
        return None
    try:
        version = ctypes.WinDLL("version")
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        block = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, block):
            return None
        value, length = ctypes.c_void_p(), ctypes.c_uint()
        # The strings are filed under a language and code page, and the file says which.
        if not version.VerQueryValueW(block, "\\VarFileInfo\\Translation", ctypes.byref(value), ctypes.byref(length)) or not length.value:
            return None
        language, codepage = ctypes.cast(value, ctypes.POINTER(ctypes.c_ushort * 2)).contents
        key = f"\\StringFileInfo\\{language:04x}{codepage:04x}\\ProductVersion"
        if not version.VerQueryValueW(block, key, ctypes.byref(value), ctypes.byref(length)) or not length.value:
            return None
        return ctypes.wstring_at(value.value, length.value).strip("\x00").strip() or None
    except (OSError, AttributeError, ValueError):
        return None


def _parts(version: str | None) -> tuple[int, ...]:
    """A version as numbers, so two can be compared. Nothing readable sorts below everything."""
    return tuple(int(number) for number in re.findall(r"\d+", version)) if version else ()


def same_bytes(one: Path | None, other: Path) -> bool:
    """Whether these are the same file byte for byte — the question behind *am I already here*."""
    if one is None or not one.exists() or not other.exists() or one.stat().st_size != other.stat().st_size:
        return False
    return _digest(one) == _digest(other)


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def plan(*, frozen: bool, at_home: bool, serving: str | None, installed_version: str | None, same: bool, mine: str = __version__) -> Plan:
    """What this start should do, from what it can see. The whole decision, in one place.

    Two questions in order. *Is this tool already serving, and which version?* — the same one means
    step aside, a newer one means never downgrade, an older one means take its place. Then, with
    nothing serving: *am I the installed copy?* — if so, serve; otherwise this is a download, and a
    download's job is to put itself in the data directory and hand over to the copy that lives
    there, unless what lives there is already these bytes or something newer.

    Pure on purpose: every branch can be asked for without an executable, a port or a machine.
    """
    if serving is not None:
        if at_home or not frozen or _parts(serving) == _parts(mine):
            return Plan(OPEN, f"System Sentinel {serving} is already running")
        if _parts(serving) > _parts(mine):
            return Plan(NEWER, f"System Sentinel {serving} is already running on this computer, which is newer than this copy ({mine}). Nothing was changed.")
        return Plan(REPLACE, f"System Sentinel {serving} is running and this copy is {mine}")
    if not frozen or at_home:
        return Plan(SERVE, f"System Sentinel {mine} is starting")
    if same:
        return Plan(START, "the installed copy is this one")
    if _parts(installed_version) > _parts(mine):
        return Plan(START, f"the installed copy is {installed_version}, which is newer than this one ({mine})")
    return Plan(INSTALL, f"installing System Sentinel {mine}")


def install(here: Path, home: Path, tries: int = 12) -> str | None:
    """Copy this program into the data directory. None when it is there, a sentence when it is not.

    A copy that has just been asked to quit can hold its own file for a moment after it has stopped
    answering, so this keeps trying for a few seconds before it calls the attempt a failure.
    """
    home.parent.mkdir(parents=True, exist_ok=True)
    trouble: OSError | None = None
    for attempt in range(tries):
        try:
            shutil.copy2(here, home)
            LOG.info("copied the program into %s", home.parent)
            return None
        except OSError as exc:
            trouble = exc
            time.sleep(1)
    return f"the program could not be copied into {home.parent}: {trouble}"


def start_installed(home: Path) -> str | None:
    """Start the installed copy and let go of it. None when it started, a sentence when it did not."""
    try:
        subprocess.Popen([str(home)], cwd=str(home.parent), creationflags=DETACHED | NO_WINDOW, close_fds=True)
    except OSError as exc:
        return f"the installed copy at {home} could not be started: {exc}"
    LOG.info("started the installed copy")
    return None


# --- saying so -----------------------------------------------------------------------------------


def message_box(text: str, title: str = "System Sentinel") -> bool:
    """Say something to the person in front of the machine, and wait until they have seen it.

    The executable is windowed: it has no console, and a start that failed silently looks exactly
    like a start that did nothing. ``user32`` through ctypes is no new dependency. Elsewhere — a
    terminal, a test — the line goes where lines go.
    """
    LOG.info(text.replace("\n", " "))
    if sys.platform != "win32":
        _say(text)
        return False
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x00010040)  # MB_ICONINFORMATION | MB_SETFOREGROUND
        return True
    except (OSError, AttributeError):
        return False


def ask_yes_no(text: str, title: str = "System Sentinel") -> bool:
    """A native Yes/No question. Anything but Yes is No, including a question that could not be
    put on screen at all: the only caller is destructive, and a machine that cannot ask must not
    assume consent. Which of the two it was goes in the log, so a removal that did nothing can be
    told apart from one that was declined."""
    if sys.platform != "win32":
        LOG.info("asked, and nothing here can answer: %s", text.replace("\n", " "))
        return False
    try:
        answer = ctypes.windll.user32.MessageBoxW(None, text, title, 0x00010034)  # MB_YESNO | MB_ICONWARNING | MB_SETFOREGROUND
    except (OSError, AttributeError) as exc:
        LOG.info("the question could not be asked: %s", exc)
        return False
    if answer == 0:
        LOG.info("the question could not be put on screen (user32 gave error %s)", ctypes.GetLastError())
        return False
    return answer == 6  # IDYES


# --- what the tray does -----------------------------------------------------------------------


def open_dashboard(base: str, token: str, to: str = "", wait: bool = False, answered: threading.Event | None = None) -> str:
    """Open the dashboard in the browser, already signed in, and return the link that was opened.

    ``to="link"`` lands on the dashboard's sign-in link for another device, so the tray's entry for
    it is this one door with a destination rather than a second way in. The browser is asked on its
    own thread: on Windows the asking is ShellExecute, which can sit behind a dialog for as long as
    nobody answers it (a machine with no handler for http shows one), and the tray must come up
    regardless. ``wait`` is for the launcher that has nothing else to do before it exits, and
    ``answered`` is set if a browser took the link, so such a launcher can say when none did.
    """
    url = f"{base}/api/session/open?code={mint_code(token)}"
    if to == "link":
        url += "&to=link"
    LOG.info("opening the dashboard%s with a one-time code", " on the sign-in link" if to == "link" else "")
    opener = _in_the_browser(url, base + "/", answered)
    if wait:
        opener.join(BROWSER_TIMEOUT)
    return url


def open_releases() -> str:
    """Open the page where a newer one would be. The tool asks nothing of it: whether there is an
    update is the browser's business, and nothing about this machine goes anywhere to find out."""
    LOG.info("opening the releases page")
    _in_the_browser(RELEASES, RELEASES)
    return RELEASES


def _in_the_browser(url: str, safe: str, answered: threading.Event | None = None) -> threading.Thread:
    """Ask the browser on its own thread, so nothing here waits behind a dialog nobody answered."""
    opener = threading.Thread(target=_ask_browser, args=(url, safe, answered), name="sentinel-browser", daemon=True)
    opener.start()
    return opener


def _ask_browser(url: str, safe: str, answered: threading.Event | None = None) -> None:
    """The log says whether a browser was found, and only ever the address safe to write down:
    the dashboard's link carries a live code, so what goes in the log is where the dashboard is."""
    try:
        found = webbrowser.open(url)
    except OSError as exc:
        LOG.info("the browser could not be started: %s", exc)
        found = False
    if found:
        LOG.info("the browser was asked")
        if answered is not None:
            answered.set()
    else:
        LOG.info("no browser answered; it is at %s", safe)


def show_dashboard(base: str, token: str, note: str = "") -> int:
    """The end of a start with nothing left to do: open the dashboard and wait for the browser.

    A message box follows only when there is something the person would otherwise never learn —
    that a newer copy is already running, or that no browser answered at all and the address is
    theirs to open. Always zero: the tool is running either way; only this copy is finished.
    """
    answered = threading.Event()
    open_dashboard(base, token, wait=True, answered=answered)
    if not answered.is_set():
        message_box(f"{note}\n\nSystem Sentinel is running at {base}/, but no browser answered. Open that address yourself.\n\nWhat happened is in {log_path()}.".strip())
    elif note:
        message_box(note)
    return 0


def agent_line(base: str, token: str) -> str:
    """The one line that gives Claude Code this machine. Copied, never shown."""
    return f'claude mcp add --transport http system-sentinel {base}/mcp --header "Authorization: Bearer {token}"'


def copy_to_clipboard(text: str) -> str:
    """Put text on the Windows clipboard through ``clip.exe`` — no new dependency. Says what happened."""
    clip = shutil.which("clip.exe")
    if clip is None:
        return "the clipboard needs Windows: clip.exe was not found"
    try:
        subprocess.run([clip], input=text.encode("utf-8"), check=True, timeout=10, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not copy: {exc}"
    return "copied the address for agents"


def startup_dir() -> Path | None:
    """The user's Startup folder (``shell:startup``), or None where there is no such thing."""
    appdata = os.environ.get("APPDATA")
    if sys.platform != "win32" or not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def startup_shortcut() -> Path | None:
    directory = startup_dir()
    return None if directory is None else directory / STARTUP_LINK


def startup_enabled() -> bool:
    """On or off is the file's existence; there is no second place to disagree with it."""
    shortcut = startup_shortcut()
    return shortcut is not None and shortcut.exists()


def launch_command() -> tuple[str, str]:
    """What the shortcut runs: the installed copy when frozen, otherwise the windowless
    interpreter on ``-m sentinel launch`` — the same launcher either way.

    Always the installed copy, never whichever file happened to be double-clicked: a shortcut
    pointing at a download in the Downloads folder would break the day that folder is tidied, and
    the copy in the data directory is the one every update replaces.
    """
    if frozen():
        home = installed_exe()
        return (str(home) if home.exists() else sys.executable), ""
    windowless = Path(sys.executable).with_name("pythonw.exe")
    return str(windowless if windowless.exists() else sys.executable), "-m sentinel launch"


def set_startup(on: bool) -> str:
    """Add or remove the Startup shortcut, hidden, and say in one line what happened."""
    shortcut = startup_shortcut()
    if shortcut is None:
        return "Start with Windows is Windows-only; nothing changed"
    if not on:
        shortcut.unlink(missing_ok=True)
        LOG.info("start with Windows off")
        return "Start with Windows is off"
    target, arguments = launch_command()
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    script = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_literal(str(shortcut))});"
        f"$s.TargetPath = {_literal(target)}; $s.Arguments = {_literal(arguments)};"
        f"$s.WorkingDirectory = {_literal(str(Path(target).parent))}; $s.WindowStyle = 7;"
        f"$s.Description = 'System Sentinel'; $s.Save()"
    )
    try:
        done = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not write the Startup shortcut: {exc}"
    if done.returncode != 0 or not shortcut.exists():
        return f"could not write the Startup shortcut: {done.stderr.strip() or done.returncode}"
    LOG.info("start with Windows on, pointing at %s", target)
    return "Start with Windows is on"


def _literal(value: str) -> str:
    """A PowerShell single-quoted string, so a path with a quote in it stays one argument."""
    return "'" + value.replace("'", "''") + "'"


# --- leaving ------------------------------------------------------------------------------------


def removal_question(home: Path) -> str:
    """What the person is agreeing to, named exactly, before anything is deleted."""
    return (
        "Remove System Sentinel from this computer?\n\n"
        f"This deletes {home} and everything in it:\n"
        "    the program\n"
        "    the access token\n"
        "    the stack and the prompts\n"
        "    every capture you have taken\n"
        "and turns off Start with Windows.\n\n"
        "This stays: the copy you downloaded, wherever you put it. An agent that was pointed at "
        "this machine forgets it with\n\n"
        "    claude mcp remove system-sentinel"
    )


#: The removal, as a batch file. ``{target}`` is the directory; it tries for about as many seconds
#: as it has turns, then deletes itself whether or not the directory went.
REMOVAL_SCRIPT = """@echo off
for /l %%i in (1,1,40) do (
  ping -n 2 127.0.0.1 >nul
  rd /s /q "{target}" 2>nul
  if not exist "{target}" goto gone
)
:gone
del "%~f0"
"""


def remove_after_exit(home: Path) -> str:
    """Hand the deletion of the data directory to something that outlives this process.

    The program is inside the directory it is being asked to delete, and Windows will not let a
    running program's file go, so the last act is to start a detached ``cmd.exe`` that waits, tries
    again for a bounded while, and stops either way. Nothing here waits for it.

    The commands are written to a batch file rather than passed on the command line, because a
    one-liner of this shape — quoted paths inside a quoted argument, with redirection — is where
    ``cmd /c`` quietly loses its own quotes and does nothing. The file lives outside the directory
    being removed and deletes itself last.
    """
    target = str(home).rstrip("\\")
    script = Path(tempfile.gettempdir()) / "system-sentinel-remove.cmd"
    try:
        # mbcs, not UTF-8: cmd reads a batch file in the console code page, and a path can carry
        # a name that is not ASCII.
        script.write_text(REMOVAL_SCRIPT.format(target=target), encoding="mbcs" if sys.platform == "win32" else "utf-8")
        subprocess.Popen(["cmd.exe", "/c", str(script)], cwd=os.environ.get("SystemRoot", "C:\\"), creationflags=DETACHED | NO_WINDOW, close_fds=True)
    except OSError as exc:
        return f"the files in {home} could not be removed: {exc}"
    LOG.info("the data directory will be removed once this process has gone")
    return f"removing {home}"


# --- the mark ------------------------------------------------------------------------------------


def _trace(scale: float, steps: int = 48) -> list[tuple[float, float]]:
    """:data:`S_PATH` sampled into a polyline: a move, then cubics in groups of six numbers."""
    numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", S_PATH)]
    start, rest = numbers[:2], numbers[2:]
    point = (start[0], start[1])
    points: list[tuple[float, float]] = []
    for i in range(0, len(rest) - 5, 6):
        a, b, end = rest[i : i + 2], rest[i + 2 : i + 4], rest[i + 4 : i + 6]
        for step in range(steps + 1):
            t = step / steps
            u = 1 - t
            points.append(
                (
                    scale * (u**3 * point[0] + 3 * u * u * t * a[0] + 3 * u * t * t * b[0] + t**3 * end[0]),
                    scale * (u**3 * point[1] + 3 * u * u * t * a[1] + 3 * u * t * t * b[1] + t**3 * end[1]),
                )
            )
        point = (end[0], end[1])
    return points


def _disc(centre: tuple[float, float], radius: float) -> tuple[float, float, float, float]:
    return (centre[0] - radius, centre[1] - radius, centre[0] + radius, centre[1] + radius)


def render_mark(size: int = 64):
    """The mark as an image: the field tile, the trace with its glow, the beam's dot at the end.

    Drawn four times over and reduced, because the trace is thin and the tray is sixteen pixels wide.
    """
    if Image is None:
        raise RuntimeError("the mark needs Pillow: pip install system-sentinel[launcher]")
    over = 4
    edge = size * over
    scale = edge / 64
    tile = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, edge - 1, edge - 1), radius=round(14 * scale), fill=FIELD)

    points = _trace(scale)
    beam = (BEAM[0] * scale, BEAM[1] * scale)
    halo = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    glow = ImageDraw.Draw(halo)
    glow.line(points, fill=ImageColor.getrgb(PHOSPHOR) + (140,), width=max(1, round(7 * scale)), joint="curve")
    glow.ellipse(_disc(beam, 4.2 * scale), fill=ImageColor.getrgb(PHOSPHOR) + (153,))
    tile.alpha_composite(halo.filter(ImageFilter.GaussianBlur(2.6 * scale)))

    trace = ImageDraw.Draw(tile)
    trace.line(points, fill=PHOSPHOR_CORE, width=max(1, round(2.4 * scale)), joint="curve")
    trace.ellipse(_disc(beam, 2.6 * scale), fill=PHOSPHOR_CORE)
    return tile.resize((size, size), Image.LANCZOS)


ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def write_icon(path: str | Path) -> Path:
    """The same mark as a Windows ``.ico``, for the exe. Called by the build, not at runtime."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    render_mark(256).save(destination, format="ICO", sizes=ICON_SIZES)
    return destination


# --- running -------------------------------------------------------------------------------------


def tray_menu(server: Server | None, base: str, token: str):
    """The tray's entries, none of them the token. Built apart from the icon so the labels can be
    checked without a notification area to show them in.

    Four things a person does often sit at the top. What they do once — look for a newer one, take
    the tool off this computer — sits behind the version, which is also the answer to *which one am
    I running*: one line doing two jobs, and neither of them in the way.
    """

    def open_item(_icon, _item) -> None:
        open_dashboard(base, token)

    def link_item(_icon, _item) -> None:
        open_dashboard(base, token, to="link")

    def copy_item(_icon, _item) -> None:
        LOG.info(copy_to_clipboard(agent_line(base, token)))

    def startup_item(_icon, _item) -> None:
        LOG.info(set_startup(not startup_enabled()))

    def updates_item(_icon, _item) -> None:
        open_releases()

    def remove_item(tray, _item) -> None:
        home = data_dir()
        if not ask_yes_no(removal_question(home)):
            LOG.info("nothing was removed")
            return
        LOG.info(set_startup(False))
        LOG.info(remove_after_exit(home))
        _quit(server, tray)

    def quit_item(tray, _item) -> None:
        _quit(server, tray)

    return pystray.Menu(
        pystray.MenuItem("Open dashboard", open_item, default=True),
        pystray.MenuItem("Sign in another device…", link_item),
        pystray.MenuItem("Copy address for agents", copy_item),
        pystray.MenuItem("Start with Windows", startup_item, checked=lambda _item: startup_enabled()),
        pystray.MenuItem(
            f"System Sentinel {__version__}",
            pystray.Menu(
                pystray.MenuItem("Check for updates…", updates_item),
                pystray.MenuItem("Remove from this computer…", remove_item),
            ),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_item),
    )


def _icon(server: Server, base: str, token: str):
    """The mark in the notification area, with :func:`tray_menu` behind it."""
    icon = pystray.Icon("system-sentinel", render_mark(64), "System Sentinel")
    icon.menu = tray_menu(server, base, token)
    return icon


def _quit(server: Server | None, tray=None) -> None:
    """The one way this process ends: the server stops answering, the tray lets the main thread go.

    The tray's Quit, ``POST /api/quit`` and a newer copy arriving all arrive here, so there is one
    shutdown to understand rather than one per door.
    """
    LOG.info("quitting")
    if server is not None:
        server.quit()
    if tray is not None:
        tray.stop()


def _log_to_file() -> None:
    """One log beside the token, so a double-click that went wrong can be read afterwards. It is
    local and stays local: the launcher writes the link it opened, never the token.

    It rotates, because this starts with Windows and would otherwise be a file that only grows."""
    root = logging.getLogger()
    if any(isinstance(handler, logging.FileHandler) for handler in root.handlers):
        return
    handler = logging.handlers.RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _say(message: str) -> None:
    """Into the log always, and onto the console when there is one: the exe is windowed."""
    LOG.info(message)
    if sys.stdout is not None:
        print(message)
        sys.stdout.flush()


def _hand_over(base: str, token: str, listen_port: int, here: Path, home: Path, copy: bool) -> int:
    """A download's whole job: put the program where it lives, start that copy, and step aside."""
    trouble = install(here, home) if copy else None
    trouble = trouble or start_installed(home)
    if trouble:
        message_box(f"System Sentinel could not be installed on this computer.\n\n{trouble}\n\nWhat happened is in {log_path()}.")
        return 1
    if not wait_until_serving(base, token):
        message_box(
            f"System Sentinel is in {home.parent}, but it did not start answering at {base}/.\n\n"
            f"Something else may be holding port {listen_port}, or a previous copy is still stopping.\n\n"
            f"What happened is in {log_path()}."
        )
        return 1
    _say(f"System Sentinel is running at {base}/.")
    return 0


def _take_over(base: str, token: str, listen_port: int, here: Path, home: Path) -> int:
    """Replace an older copy that is serving: ask it to stop, wait for the port, then hand over."""
    if not ask_to_quit(base, token):
        message_box(
            f"A copy of System Sentinel is already running at {base}/ and would not stop, so it was not updated.\n\n"
            f"Quit it from the tray and start this one again.\n\nWhat happened is in {log_path()}."
        )
        return 1
    if not wait_until_free(listen_port):
        message_box(
            f"The copy of System Sentinel at {base}/ was asked to stop but port {listen_port} is still held, so it was not updated.\n\n"
            f"What happened is in {log_path()}."
        )
        return 1
    return _hand_over(base, token, listen_port, here, home, copy=True)


def _serve_here(base: str, token: str, listen_port: int) -> int:
    """Serve, open the dashboard signed in, and sit in the tray until something asks it to stop."""
    server = Server("127.0.0.1", listen_port)
    icon = _icon(server, base, token) if pystray is not None and Image is not None else None
    if icon is not None:
        # Set before the door opens: a quit arriving in the first moment must stop the tray too.
        server.state.on_quit = lambda: _quit(server, icon)

    server.start()
    if not wait_until_serving(base, token):
        message_box(
            f"System Sentinel could not start on {base}/.\n\n"
            f"Something else may be holding port {listen_port}, or a previous copy is still stopping.\n\n"
            f"What happened is in {log_path()}."
        )
        server.stop()
        return 1
    open_dashboard(base, token)

    if icon is None:
        _say(f"System Sentinel is serving at {base}/ — the tray needs the launcher extra (pip install 'system-sentinel[launcher]'). Ctrl+C here quits.")
        try:
            while server.alive:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    else:
        icon.run()
    server.stop()
    return 0


def main() -> int:
    """Decide once what this start is, then do that.

    Where this copy is and what is already answering on the port are the only two things the
    decision needs; :func:`plan` turns them into one of six. Everything that can go wrong from here
    is said out loud, because a windowed program that exits quietly looks exactly like one that
    never ran at all.
    """
    _log_to_file()
    listen_port = port()
    base = base_url(listen_port)
    token = load_or_create_token()
    here, home = this_exe(), installed_exe()
    at_home = here is not None and os.path.normcase(str(here)) == os.path.normcase(str(home.resolve()))
    away = here is not None and not at_home

    step = plan(
        frozen=here is not None,
        at_home=at_home,
        serving=serving_version(base, token),
        installed_version=file_version(home) if away else None,
        same=same_bytes(here, home) if away else False,
    )
    LOG.info("%s: %s", step.do, step.why)

    if step.do == OPEN:
        _say(f"System Sentinel is already running at {base}/; opening the dashboard.")
        return show_dashboard(base, token)
    if step.do == NEWER:
        return show_dashboard(base, token, step.why)
    if step.do == REPLACE:
        return _take_over(base, token, listen_port, here, home)  # type: ignore[arg-type]
    if step.do in (INSTALL, START):
        return _hand_over(base, token, listen_port, here, home, copy=step.do == INSTALL)  # type: ignore[arg-type]
    return _serve_here(base, token, listen_port)


if __name__ == "__main__":
    sys.exit(main())
