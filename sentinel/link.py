"""A sign-in link for another device: how this machine is reached, and a code to get in with.

Three things have to be true before a phone can open this dashboard, and the tool can only
observe one of them: the machine must publish an address on a private network, the phone must
be on that network, and the browser must be signed in without anyone reading the token. This
module observes the first through the bridge, mints the second as a one-time code, and draws
the two together as a QR code so a camera carries them across.

What the machine publishes is a **reading**, not a setting: Tailscale is asked on the spot and
``Reach.outcome`` keeps "it does not publish this port" apart from "it could not be asked".
Only Tailscale is asked, because only a tailnet name is a stable fact about the machine: a
tunnel's address changes every restart and nothing here would know it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import segno

from .auth import LINK_TTL, mint_code
from .bridge import Bridge

Outcome = Literal["ok", "empty", "unavailable", "failed"]

#: Long enough for Tailscale's own start-up, short enough that a dashboard asking is not left hanging.
REACH_TIMEOUT = 20.0

QUIET_ZONE = 4

# The word "tailscale" is in the executable's name, which is also the marker a fake bridge routes on.
REACH_SCRIPT = r"""
$exe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
if (-not (Test-Path $exe)) {
    [pscustomobject]@{ installed = $false }
} else {
    [pscustomobject]@{ installed = $true; serve = ((& $exe serve status --json 2>&1) | Out-String) }
}
"""

NOT_INSTALLED = "Tailscale is not installed on this machine"
PUBLISHED = "Tailscale publishes this port to your private network"


@dataclass(frozen=True)
class Reach:
    """How this machine is reached from another device, or why that is not known.

    ``ok`` with an address is the only outcome a link can be made from. ``empty`` means the
    machine answered and publishes nothing for this port, and ``installed`` says whether that is
    because Tailscale is absent or because it was never told to; ``unavailable`` and ``failed``
    mean the question could not be put, which is not the same as a no, and leave ``installed``
    unknown.
    """

    outcome: Outcome
    installed: bool | None = None
    address: str | None = None
    via: str | None = None
    detail: str = ""


def reach(bridge: Bridge, port: int) -> Reach:
    """Ask the machine which https address it publishes for this port."""
    result = bridge.run(REACH_SCRIPT, timeout=REACH_TIMEOUT)
    if result.outcome != "ok" or not result.items:
        outcome: Outcome = "unavailable" if result.outcome == "unavailable" else "failed"
        return Reach(outcome, detail=result.error or f"the machine answered {result.outcome}")

    item = result.items[0]
    if not item.get("installed"):
        return Reach("empty", installed=False, detail=NOT_INSTALLED)

    address = _published(str(item.get("serve") or ""), port)
    if address is None:
        return Reach("empty", installed=True, detail=f"Tailscale is installed but does not publish this port; on this machine run: tailscale serve --bg {port}")
    return Reach("ok", installed=True, address=address, via="Tailscale", detail=PUBLISHED)


def _published(serve: str, port: int) -> str | None:
    """The address whose ``/`` handler proxies to this port, out of ``tailscale serve status --json``.

    Anything that is not that — no JSON at all ("No serve config"), no ``Web`` map, or a map that
    serves some other port — is no address, which the caller reads as ``empty``.
    """
    try:
        config = json.loads(serve)
    except json.JSONDecodeError:
        return None
    web = config.get("Web") if isinstance(config, dict) else None
    if not isinstance(web, dict):
        return None
    for host_port, entry in web.items():
        handlers = entry.get("Handlers") if isinstance(entry, dict) else None
        root = handlers.get("/") if isinstance(handlers, dict) else None
        proxy = root.get("Proxy") if isinstance(root, dict) else None
        if isinstance(proxy, str) and proxy.endswith(f":{port}"):
            return _address(str(host_port))
    return None


def _address(host_port: str) -> str:
    """``host:443`` is the https address itself; any other port stays on it."""
    host, _, published = host_port.rpartition(":")
    if not host:
        return f"https://{host_port}"
    return f"https://{host}" if published == "443" else f"https://{host}:{published}"


def qr_svg(text: str) -> str:
    """The text as an SVG QR code that takes its colour from the page.

    The path is drawn here rather than by segno's own writer, which refuses ``currentColor``:
    one run of dark modules becomes one subpath, so the tile is a single element a light
    surface can be put behind and a camera can read.
    """
    rows = list(segno.make(text, error="m").matrix_iter(scale=1, border=QUIET_ZONE))
    size = len(rows)
    runs: list[str] = []
    for y, row in enumerate(rows):
        x = 0
        while x < size:
            if not row[x]:
                x += 1
                continue
            end = x
            while end < size and row[end]:
                end += 1
            runs.append(f"M{x} {y}h{end - x}v1h-{end - x}z")
            x = end
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges"'
        f' role="img" aria-label="QR code of the sign-in link"><path fill="currentColor" d="{"".join(runs)}"/></svg>'
    )


def sign_in_link(token: str, address: str, ttl: float = LINK_TTL) -> tuple[str, str]:
    """The link another device opens, and when it runs out. The token is signed into the code, never in it."""
    url = f"{address}/api/session/open?code={mint_code(token, ttl=ttl)}"
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    return url, expires.isoformat(timespec="seconds").replace("+00:00", "Z")
