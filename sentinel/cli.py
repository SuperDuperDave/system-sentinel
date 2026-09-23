"""``system-sentinel``: serve, token, check, launch, bench.

``serve`` runs the API on 127.0.0.1:8000 and prints where it is and where the
token lives. ``launch`` is the same server for someone who did not open a
terminal: it opens the dashboard already signed in and sits in the tray.
``token`` prints the token for an agent to read. ``check`` takes the health
reading without starting the server and exits non-zero unless the bridge
answered, so a deploy prompt can prove the install before anyone opens a page.
``bench`` takes every automatically selectable reading against the real bridge and writes what each one
costs, so a claim about speed points at the command that produces it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import __version__
from .auth import load_or_create_token, token_path
from .bench import DEFAULT_RUNS, DOC_PATH, TRANSPORTS
from .bridge import Bridge
from .paths import data_dir
from .serialization import json_safe_integers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="system-sentinel", description="A stethoscope for a Windows computer.")
    parser.add_argument("--version", action="version", version=f"system-sentinel {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the API and the dashboard")
    serve.add_argument("--host", default="127.0.0.1", help="interface to listen on (default 127.0.0.1; use a tailnet address to reach it from a phone)")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true", help="development: restart on source change")

    sub.add_parser("launch", help="run the tray launcher: serve, open the dashboard signed in, sit in the tray")
    sub.add_parser("token", help="print the access token")
    sub.add_parser("check", help="take the health reading and exit 0 only if the bridge answered")
    sub.add_parser("where", help="print the data directory")
    updater = sub.add_parser("update", help="check the latest published release; --install downloads, verifies and starts it on Windows")
    updater.add_argument("--install", action="store_true", help="download, verify and start a newer release")

    bench = sub.add_parser("bench", help="take readings that need no exact selection and report what each one costs")
    bench.add_argument("--runs", type=int, default=DEFAULT_RUNS, help=f"how many times to take each reading (default {DEFAULT_RUNS})")
    bench.add_argument("--readings", default="", help="comma-separated reading names to narrow to (default: readings that need no exact selection)")
    bench.add_argument("--transport", choices=TRANSPORTS, default=TRANSPORTS[0], help=f"which bridge transport to measure (default {TRANSPORTS[0]})")
    bench.add_argument("--json", dest="as_json", action="store_true", help="print the run as JSON, samples included, for a machine")
    bench.add_argument("--out", default=None, help=f"write the result to this file instead of standard output (the document is {DOC_PATH})")

    args = parser.parse_args(argv)
    if args.command == "serve":
        return _serve(args.host, args.port, args.reload)
    if args.command == "launch":
        from .launcher import main as launch

        return launch()
    if args.command == "token":
        print(load_or_create_token())
        return 0
    if args.command == "where":
        print(data_dir())
        return 0
    if args.command == "check":
        return _check()
    if args.command == "update":
        return _update(args.install)
    if args.command == "bench":
        return _bench(args.runs, args.readings, args.transport, args.as_json, args.out)
    parser.print_help()
    return 2


def _serve(host: str, port: int, reload: bool) -> int:
    load_or_create_token()
    print(f"System Sentinel {__version__}")
    print(f"  dashboard  http://{host}:{port}/")
    print(f"  api        http://{host}:{port}/api/docs")
    print(f"  mcp        http://{host}:{port}/mcp")
    print(f"  token      {token_path()}")
    if host not in ("127.0.0.1", "localhost"):
        print("  listening beyond this machine: every /api and /mcp request still needs the token")
    if reload:
        print("  --reload owns its own lifetime: this one is stopped here, not through POST /api/quit")
    sys.stdout.flush()

    if reload:
        # The reloader needs the app by name so it can build it again after every change, and it
        # owns the process it restarts; there is nothing for a quit callback to stop.
        import uvicorn

        uvicorn.run("sentinel.app:create_app", factory=True, host=host, port=port, reload=True, log_level="info")
        return 0

    # The same server the launcher runs, on this thread: one way uvicorn is started, so quitting
    # means the same thing whether the tool was started from a terminal or by double-clicking it.
    from .launcher import Server

    Server(host, port, console=True).run()
    return 0


def _check() -> int:
    from . import readings  # noqa: F401
    from .reading import REGISTRY

    bridge = Bridge.locate()
    reading = REGISTRY["health"].take(bridge, {})
    print(json.dumps(json_safe_integers(reading.to_dict()), indent=1))
    return 0 if reading.observed else 1


def _update(install: bool) -> int:
    from . import launcher, update

    try:
        release = update.latest_release()
        current = launcher.file_version(launcher.installed_exe()) or __version__
        if update.version_parts(release.version) <= update.version_parts(current):
            print(f"System Sentinel {current} is current; latest published release: {release.version}.")
            return 0
        print(f"System Sentinel {release.version} is available; this computer has {current}. {release.page}")
        if install:
            launcher.start_verified_update(release)
            print("The verified file was started; its installer will replace the running copy and reopen the dashboard.")
        return 0
    except (update.UpdateError, OSError) as exc:
        print(f"Update failed; the installed copy was left in place: {exc}", file=sys.stderr)
        return 1


def _bench(runs: int, readings: str, transport: str, as_json: bool, out: str | None) -> int:
    """Measure the selection and emit it, as the document or as JSON.

    Exits non-zero when the machine was never observed: a bench that measured nothing has nothing
    to report, and a table of em dashes should not be mistaken for a result.
    """
    from . import bench
    from . import readings as _catalog  # noqa: F401 - importing the package fills the registry

    if runs < 1:
        print("--runs takes at least 1", file=sys.stderr)
        return 2
    try:
        names = bench.select(readings)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    report = asyncio.run(bench.measure(Bridge.locate(), names=names, runs=runs, transport=transport))
    try:
        if out is None:
            text = bench.as_json(report) if as_json else bench.section(report)
            bench.check_clean(text)
            _say(text)
        else:
            path = Path(out)
            if as_json:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(bench.as_json(report) + "\n", encoding="utf-8")
            else:
                bench.write(report, path)
            _say(f"{transport} transport, {len(report.rows)} reading(s) x {report.runs} run(s) -> {out}")
    except bench.Leak as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0 if report.observed else 1


def _say(text: str) -> None:
    """Print, on a console that may not be able to spell what the document says.

    The measurement is written as UTF-8 and reads as typography; a Windows console is still
    sometimes a legacy code page, and a table is not worth ending the command over.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        print(text.encode(encoding, "replace").decode(encoding))


if __name__ == "__main__":
    sys.exit(main())
