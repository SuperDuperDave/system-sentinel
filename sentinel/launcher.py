"""The launcher: double-click, and the tool is running.

One process on the person's side of the boundary. It starts the API on this machine if nothing is
serving yet, opens the dashboard in the browser already signed in, and sits in the tray as the mark.
The CLI, the API and the MCP address stay underneath, unchanged, for the expert and for an agent.

The person never sees the token. Signing the browser in is a one-time code (:func:`sentinel.auth.mint_code`)
spent once at ``GET /api/session/open``, which only this machine may call; the token itself stays in
the data directory where an agent reads it on purpose. Nothing in the tray ever displays it. Signing
another device in is the same door: the tray opens the dashboard on its sign-in link, which asks the
machine for the address it publishes on a private network and draws a fresh code as a QR code.

The tray is the optional ``[launcher]`` extra (pystray and Pillow); the server itself stays
dependency-free. Without pystray the launcher does the same work and says how to quit.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable

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
READY_TIMEOUT = 20.0
BROWSER_TIMEOUT = 30.0
STARTUP_LINK = "System Sentinel.lnk"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: The trace, on a 64-unit grid, copied from ``dashboard/src/Mark.tsx`` so the tray and the
#: dashboard draw the same S. Values owned by the identity note.
S_PATH = "M45 19C42 12 22 11 21 20C20 29 44 30 44 41C44 50 23 53 18 45"
FIELD = "#0B1A16"
PHOSPHOR_CORE = "#C9FFE1"
PHOSPHOR = "#6FF0A8"
BEAM = (18, 45)

Status = Callable[[str, dict], "int | None"]


# --- where the server is ----------------------------------------------------------------------


def port() -> int:
    """The port to serve on: ``SENTINEL_PORT`` when set, 8000 otherwise."""
    try:
        return int(os.environ.get("SENTINEL_PORT", DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def base_url(listen_port: int) -> str:
    return f"http://127.0.0.1:{listen_port}"


def _status(url: str, headers: dict) -> int | None:
    """The status code, or None when nothing answered."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return None


def already_serving(base: str, token: str, get: Status = _status) -> bool:
    """True when this address answers a reading request with this machine's token.

    Anything else — nothing listening, a stranger on the port, another machine's token — is not our
    server, so the launcher starts its own rather than assume.
    """
    return get(f"{base}/api/readings", {"Authorization": f"Bearer {token}"}) == 200


def wait_until_serving(base: str, token: str, timeout: float = READY_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if already_serving(base, token):
            return True
        time.sleep(0.2)
    return False


class Server:
    """uvicorn on a background thread, so the tray can own the main one."""

    def __init__(self, host: str, listen_port: int):
        import uvicorn

        from .app import State, create_app

        self.state = State()
        # log_config=None leaves the launcher's own file handler in place (a windowed exe has no
        # console to log to); access_log=False keeps that file the story of the launch rather than
        # a request log that grows for as long as the machine is switched on.
        config = uvicorn.Config(create_app(self.state), host=host, port=listen_port, log_level="info", log_config=None, access_log=False, ws="none")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="sentinel-api", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()


# --- what the tray does -----------------------------------------------------------------------


def open_dashboard(base: str, token: str, to: str = "", wait: bool = False) -> str:
    """Open the dashboard in the browser, already signed in, and return the link that was opened.

    ``to="link"`` lands on the dashboard's sign-in link for another device, so the tray's entry for
    it is this one door with a destination rather than a second way in. The browser is asked on its
    own thread: on Windows the asking is ShellExecute, which can sit behind a dialog for as long as
    nobody answers it (a machine with no handler for http shows one), and the tray must come up
    regardless. ``wait`` is for the launcher that has nothing else to do before it exits.
    """
    url = f"{base}/api/session/open?code={mint_code(token)}"
    if to == "link":
        url += "&to=link"
    LOG.info("opening the dashboard%s with a one-time code", " on the sign-in link" if to == "link" else "")
    opener = threading.Thread(target=_ask_browser, args=(url,), name="sentinel-browser", daemon=True)
    opener.start()
    if wait:
        opener.join(BROWSER_TIMEOUT)
    return url


def _ask_browser(url: str) -> None:
    """The log says whether a browser was found, never the link: the link carries a live code."""
    home = url.split("/api/", 1)[0] + "/"
    try:
        found = webbrowser.open(url)
    except OSError as exc:
        LOG.info("the browser could not be started: %s", exc)
        found = False
    if found:
        LOG.info("the browser was asked")
    else:
        LOG.info("no browser answered; the dashboard is at %s", home)


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
    """What the shortcut runs: the exe itself when frozen, otherwise the windowless interpreter
    on ``-m sentinel launch`` — the same launcher either way."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
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
    """The five entries, none of them the token. Built apart from the icon so the labels can be
    checked without a notification area to show them in."""

    def open_item(_icon, _item) -> None:
        open_dashboard(base, token)

    def link_item(_icon, _item) -> None:
        open_dashboard(base, token, to="link")

    def copy_item(_icon, _item) -> None:
        LOG.info(copy_to_clipboard(agent_line(base, token)))

    def startup_item(_icon, _item) -> None:
        LOG.info(set_startup(not startup_enabled()))

    def quit_item(tray, _item) -> None:
        LOG.info("quitting")
        if server is not None:
            server.stop()
        tray.stop()

    return pystray.Menu(
        pystray.MenuItem("Open dashboard", open_item, default=True),
        pystray.MenuItem("Sign in another device…", link_item),
        pystray.MenuItem("Copy address for agents", copy_item),
        pystray.MenuItem("Start with Windows", startup_item, checked=lambda _item: startup_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_item),
    )


def _tray(server: Server, base: str, token: str) -> None:
    """The mark in the notification area, with :func:`tray_menu` behind it."""
    icon = pystray.Icon("system-sentinel", render_mark(64), "System Sentinel")
    icon.menu = tray_menu(server, base, token)
    icon.run()


def _log_to_file() -> None:
    """One log beside the token, so a double-click that went wrong can be read afterwards. It is
    local and stays local: the launcher writes the link it opened, never the token."""
    root = logging.getLogger()
    if any(isinstance(handler, logging.FileHandler) for handler in root.handlers):
        return
    handler = logging.FileHandler(data_dir() / "launcher.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _say(message: str) -> None:
    """Into the log always, and onto the console when there is one: the exe is windowed."""
    LOG.info(message)
    if sys.stdout is not None:
        print(message)
        sys.stdout.flush()


def main() -> int:
    """Serve if nothing is serving, open the dashboard signed in, then sit in the tray until Quit."""
    _log_to_file()
    listen_port = port()
    base = base_url(listen_port)
    token = load_or_create_token()

    if already_serving(base, token):
        _say(f"System Sentinel is already running at {base}/; opening the dashboard.")
        open_dashboard(base, token, wait=True)
        return 0

    server = Server("127.0.0.1", listen_port)
    server.start()
    if not wait_until_serving(base, token):
        _say(f"System Sentinel could not start on {base} — something else may be holding the port.")
        server.stop()
        return 1
    open_dashboard(base, token)

    if pystray is None or Image is None:
        _say(f"System Sentinel is serving at {base}/ — the tray needs the launcher extra (pip install 'system-sentinel[launcher]'). Ctrl+C here quits.")
        try:
            while server.alive:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        server.stop()
        return 0

    _tray(server, base, token)
    server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
