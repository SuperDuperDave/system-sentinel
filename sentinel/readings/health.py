"""``health``: whether the bridge to Windows works, before anything is asked of it.

Also the one place the tool learns what the machine calls itself, so the
redaction can replace those names wherever they appear.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Any

from ..bridge import Bridge, questions_report, sessions_report
from ..paths import data_dir
from ..reading import Reading, Section, Spec, register
from ..redact import Identity

IDENTITY_SCRIPT = "[pscustomobject]@{ host = $env:COMPUTERNAME; user = $env:USERNAME; ps = $PSVersionTable.PSVersion.ToString(); os = [System.Environment]::OSVersion.Version.ToString() }"

DECODER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "DecodeWheaRecord", "DecodeWheaRecord.exe")
logger = logging.getLogger(__name__)
IDENTITY_ERROR = "Sentinel's identity question raised an internal error; see the local server log"
_IDENTITY_LOG_INTERVAL = 300.0
_identity_log_lock = threading.Lock()
_last_identity_log: float | None = None


def _log_identity_exception() -> None:
    """Keep a persistent failed Health check from rotating out older local diagnostics."""
    global _last_identity_log
    now = time.monotonic()
    with _identity_log_lock:
        if _last_identity_log is None or now - _last_identity_log >= _IDENTITY_LOG_INTERVAL:
            logger.exception("Health identity question failed")
            _last_identity_log = now


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
    # stream imports readings.events; import here to avoid a readings package cycle.
    from ..stream import stream_report

    try:
        _, facts = learn_identity(bridge)
    except Exception:
        _log_identity_exception()
        facts = {"outcome": "failed", "error": IDENTITY_ERROR}
    sessions = sessions_report(bridge)
    data = {
        "bridge": {"available": bridge.available, "exe": bool(bridge.exe), **facts, "sessions": sessions, "questions": questions_report()},
        "streams": stream_report(),
        "decoder": {"present": os.path.exists(DECODER)},
        "data_dir": {"present": data_dir().is_dir()},
    }
    outcome = facts["outcome"]
    reading = Reading(reading="health", params={}, outcome=outcome, method={"kind": "powershell", "query": IDENTITY_SCRIPT}, took_ms=facts.get("took_ms", 0))
    reading.sections = [Section("bridge", "raw", data)]
    # A question that falls back to a one-shot launch may or may not answer. The outcome of each
    # reading is the evidence; this warning only explains the transport and its possible cost.
    if sessions["fell_back"] or sessions["start_failures"]:
        reading.warnings.append("some questions went to one-shot launches; each reading's outcome says whether Windows answered")
    if outcome not in ("ok", "empty"):
        reading.error = {"kind": facts.get("cause") or outcome, "detail": facts.get("error") or ""}
    return reading


register(
    Spec(
        name="health",
        description="Whether the bridge works: PowerShell found and answering, its version, bridge and stream workload, live sessions, the decoder present, the data directory writable. Take this first.",
        classes=("raw",),
        take=take_health,
    )
)
