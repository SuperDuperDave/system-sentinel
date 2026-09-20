"""``system-sentinel``: serve, token, check, launch.

``serve`` runs the API on 127.0.0.1:8000 and prints where it is and where the
token lives. ``launch`` is the same server for someone who did not open a
terminal: it opens the dashboard already signed in and sits in the tray.
``token`` prints the token for an agent to read. ``check`` takes the health
reading without starting the server and exits non-zero unless the bridge
answered, so a deploy prompt can prove the install before anyone opens a page.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .auth import load_or_create_token, token_path
from .bridge import Bridge
from .paths import data_dir


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
    parser.print_help()
    return 2


def _serve(host: str, port: int, reload: bool) -> int:
    import uvicorn

    load_or_create_token()
    print(f"System Sentinel {__version__}")
    print(f"  dashboard  http://{host}:{port}/")
    print(f"  api        http://{host}:{port}/api/docs")
    print(f"  mcp        http://{host}:{port}/mcp")
    print(f"  token      {token_path()}")
    if host not in ("127.0.0.1", "localhost"):
        print("  listening beyond this machine: every /api and /mcp request still needs the token")
    sys.stdout.flush()
    uvicorn.run("sentinel.app:create_app", factory=True, host=host, port=port, reload=reload, log_level="info")
    return 0


def _check() -> int:
    from . import readings  # noqa: F401
    from .reading import REGISTRY

    bridge = Bridge.locate()
    reading = REGISTRY["health"].take(bridge, {})
    print(json.dumps(reading.to_dict(), indent=1))
    return 0 if reading.observed else 1


if __name__ == "__main__":
    sys.exit(main())
