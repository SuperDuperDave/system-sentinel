"""``health``: whether the bridge to Windows works, before anything is asked of it.

Also the one place the tool learns what the machine calls itself, so the
redaction can replace those names wherever they appear.
"""

from __future__ import annotations

import os
from typing import Any

from ..bridge import Bridge
from ..paths import data_dir
from ..reading import Reading, Section, Spec, register
from ..redact import Identity

IDENTITY_SCRIPT = "[pscustomobject]@{ host = $env:COMPUTERNAME; user = $env:USERNAME; ps = $PSVersionTable.PSVersion.ToString(); os = [System.Environment]::OSVersion.Version.ToString() }"

DECODER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "DecodeWheaRecord", "DecodeWheaRecord.exe")


def learn_identity(bridge: Bridge) -> tuple[Identity, dict[str, Any]]:
    """Ask the machine its names. Returns the identity and the facts learned (already safe to show)."""
    result = bridge.run(IDENTITY_SCRIPT, timeout=30)
    if result.outcome != "ok":
        return Identity(), {"outcome": result.outcome, "error": result.error}
    item = result.items[0]
    identity = Identity(host=item.get("host") or None, user=item.get("user") or None)
    return identity, {"outcome": "ok", "powershell": item.get("ps"), "windows": item.get("os"), "took_ms": result.took_ms}


def take_health(bridge: Bridge, params: dict[str, Any]) -> Reading:
    _, facts = learn_identity(bridge)
    data = {
        "bridge": {"available": bridge.available, "exe": bool(bridge.exe), **facts},
        "decoder": {"present": os.path.exists(DECODER)},
        "data_dir": {"present": data_dir().is_dir()},
    }
    outcome = facts["outcome"] if facts["outcome"] in ("ok", "empty", "failed", "unavailable", "denied", "timeout") else "failed"
    reading = Reading(reading="health", params={}, outcome=outcome, method={"kind": "powershell", "query": IDENTITY_SCRIPT}, took_ms=facts.get("took_ms", 0))
    reading.sections = [Section("bridge", "raw", data)]
    if outcome not in ("ok", "empty"):
        reading.error = {"kind": outcome, "detail": facts.get("error") or ""}
    return reading


register(
    Spec(
        name="health",
        description="Whether the bridge works: PowerShell found and answering, its version, the decoder present, the data directory writable. Take this first.",
        classes=("raw",),
        take=take_health,
    )
)
