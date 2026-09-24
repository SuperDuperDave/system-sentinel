"""``health``: whether the bridge to Windows works, before anything is asked of it.

Also the one place the tool learns what the machine calls itself, so the
redaction can replace those names wherever they appear.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from ..bridge import Bridge, sessions_report
from ..paths import data_dir
from ..reading import Reading, Section, Spec, register
from ..redact import Identity

IDENTITY_SCRIPT = "[pscustomobject]@{ host = $env:COMPUTERNAME; user = $env:USERNAME; ps = $PSVersionTable.PSVersion.ToString(); os = [System.Environment]::OSVersion.Version.ToString() }"

DECODER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "DecodeWheaRecord", "DecodeWheaRecord.exe")


def learn_identity(bridge: Bridge) -> tuple[Identity, dict[str, Any]]:
    """Ask the machine its names. Returns the identity and the facts learned (already safe to show)."""
    result = bridge.run(IDENTITY_SCRIPT, timeout=30)
    if result.outcome != "ok":
        # The native Windows child inherits these names from this process. Keep the bridge
        # failure visible, while retaining enough identity to mask names in default responses.
        native = Identity(host=os.environ.get("COMPUTERNAME") or None, user=os.environ.get("USERNAME") or None) if sys.platform == "win32" and isinstance(bridge, Bridge) else Identity()
        return native, {"outcome": result.outcome, "error": result.error, **({"cause": result.cause} if result.cause else {})}
    item = result.items[0]
    identity = Identity(host=item.get("host") or None, user=item.get("user") or None)
    return identity, {"outcome": "ok", "powershell": item.get("ps"), "windows": item.get("os"), "took_ms": result.took_ms}


def take_health(bridge: Bridge, params: dict[str, Any]) -> Reading:
    _, facts = learn_identity(bridge)
    sessions = sessions_report(bridge)
    data = {
        "bridge": {"available": bridge.available, "exe": bool(bridge.exe), **facts, "sessions": sessions},
        "decoder": {"present": os.path.exists(DECODER)},
        "data_dir": {"present": data_dir().is_dir()},
    }
    outcome = facts["outcome"]
    reading = Reading(reading="health", params={}, outcome=outcome, method={"kind": "powershell", "query": IDENTITY_SCRIPT}, took_ms=facts.get("took_ms", 0))
    reading.sections = [Section("bridge", "raw", data)]
    # A question that had to be launched because no live session would start is not a failure — the
    # machine still answered — but it is the difference between a reading that costs milliseconds
    # and one that costs a fifth of a second, so it is said out loud rather than left in a count.
    if sessions["fell_back"] or sessions["start_failures"]:
        reading.warnings.append("a live session did not start, so some questions fell back to one-shot launches; the machine still answered")
    if outcome not in ("ok", "empty"):
        reading.error = {"kind": facts.get("cause") or outcome, "detail": facts.get("error") or ""}
    return reading


register(
    Spec(
        name="health",
        description="Whether the bridge works: PowerShell found and answering, its version, how questions are reaching the machine and how the live sessions are doing, the decoder present, the data directory writable. Take this first.",
        classes=("raw",),
        take=take_health,
    )
)
