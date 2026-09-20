"""The real app (``sentinel.app.create_app``) with a bridge that answers every script the two
screenshot views can run from fixtures. Nothing in the repository changes: the fixture is
injected at the one seam the readings already go through, so what's on screen is the same code
that renders the real thing, over records that were never on this machine.

Answers:
  - the identity probe (``$env:COMPUTERNAME``): a placeholder host and user
  - WHEA (``whea``, ``storms``): tests/fixtures/whea-records.json, one record given a real,
    decodable CPER payload (the minimal construction from tests/test_whea.py)
  - the System log (``events``, ``record``): docs/screens/fixtures/system-log.json, filtered
    and paged the way Get-WinEvent would be
  - anything else: empty

Run: SYSTEM_SENTINEL_HOME=<scratch dir> ./.venv/bin/python fixture-server.py --port 8021
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

# Repository root: as committed this script sits at docs/screens/fixtures/, three below the
# root, so walking up finds it there. SENTINEL_REPO_ROOT overrides this, for a copy run from
# somewhere else (a scratch directory, while drafting) rather than its committed location.
def find_repo_root(start: Path) -> Path:
    override = os.environ.get("SENTINEL_REPO_ROOT")
    if override and (Path(override) / "sentinel" / "app.py").exists():
        return Path(override)
    for candidate in [start, *start.parents]:
        if (candidate / "sentinel" / "app.py").exists():
            return candidate
    raise SystemExit("could not find the repository root (looked for sentinel/app.py); set SENTINEL_REPO_ROOT")


REPO = find_repo_root(HERE)
sys.path.insert(0, str(REPO))

from sentinel.bridge import BridgeResult  # noqa: E402
from sentinel.app import State, create_app  # noqa: E402

WHEA_FIXTURE = REPO / "tests" / "fixtures" / "whea-records.json"
SYSTEM_LOG_FIXTURE = HERE / "system-log.json"

FIXTURE_HOST = "WORKSTATION"
FIXTURE_USER = "person"


def minimal_cper() -> str:
    """The smallest record the decoder accepts (from tests/test_whea.py's ``minimal_cper``):
    a 128-byte CPER header plus one 72-byte generic-processor-error section descriptor."""
    descriptor = (
        (200).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (0x0300).to_bytes(2, "little")
        + bytes(2)
        + (1).to_bytes(4, "little")
        + uuid.UUID("9876ccad-47b4-4bdb-b65e-16f193c4f3db").bytes_le
        + bytes(16)
        + (0).to_bytes(4, "little")
        + bytes(20)
    )
    header = (
        b"CPER"
        + (0x0100).to_bytes(2, "little")
        + (0xFFFFFFFF).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + bytes(8)
        + (128 + len(descriptor)).to_bytes(4, "little")
        + bytes(8)
        + bytes(16) * 4
        + bytes(8 + 4 + 8 + 12)
    )
    return (header + descriptor).hex().upper()


CPER_HEX = minimal_cper()


def _powershell_stamp(epoch: float) -> str:
    """PowerShell's 'o' format, which the readings parse: seven fractional digits."""
    moment = datetime.fromtimestamp(epoch, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond:06d}0Z"


def _parse_stamp(stamp: str) -> float:
    text = stamp.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).timestamp()


# ---------------------------------------------------------------- WHEA


def whea_records(now: float, count: int | None = None) -> list[dict[str, Any]]:
    """The committed WHEA fixture, materialized relative to ``now``, capped the way ``-MaxEvents``
    would cap it (most recent first). The most recent record is given a real, decodable CPER
    payload — rather than only the fixture's one payload-less record — so the decoded structure
    is guaranteed to be within whatever count the view asks for, exactly as ``take_whea``'s own
    decode budget expects a small handful of records, not the whole fixture, per take."""
    doc = json.loads(WHEA_FIXTURE.read_text(encoding="utf-8"))
    out = []
    for entry in doc["records"]:
        rec = {k: v for k, v in entry.items() if k not in ("group", "minutes_ago")}
        rec["TimeCreated"] = _powershell_stamp(now - entry["minutes_ago"] * 60)
        rec["MachineName"] = FIXTURE_HOST
        out.append(rec)
    out.sort(key=lambda r: r["TimeCreated"], reverse=True)
    if count is not None:
        out = out[:count]
    if out:
        out[0]["RawData"] = CPER_HEX  # the most recent record always decodes, whatever the cap
    return out


# ---------------------------------------------------------------- System log


def system_log_records(now: float) -> list[dict[str, Any]]:
    doc = json.loads(SYSTEM_LOG_FIXTURE.read_text(encoding="utf-8"))
    out = []
    for entry in doc["records"]:
        rec = {k: v for k, v in entry.items() if k != "minutes_ago"}
        moment = now - entry["minutes_ago"] * 60
        rec["TimeCreated"] = _powershell_stamp(moment)
        if entry["Id"] in (12, 6008):  # the only two templates with a moment of their own to fill in
            rec["Message"] = _fill_template(rec["Message"], moment)
            rec["Properties"] = [_fill_template(p, moment) if isinstance(p, str) else p for p in rec["Properties"]]
        out.append(rec)
    return sorted(out, key=lambda r: r["TimeCreated"], reverse=True)  # newest first, as Get-WinEvent returns


def _fill_template(text: str, moment: float) -> str:
    """The triad's two message templates carry their own moment as a parameter: 12's is its own
    boot instant, 6008's is the crash six minutes and change earlier — the Display 4101 moment."""
    boot = datetime.fromtimestamp(moment, timezone.utc)
    shutdown = datetime.fromtimestamp(moment - 6.2 * 60, timezone.utc)
    return text.format(
        boot_iso=boot.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
        shutdown_time=shutdown.strftime("%-I:%M:%S %p"),
        shutdown_date=shutdown.strftime("%-m/%-d/%Y"),
    )


_LEVEL_RE = re.compile(r"Level=([\d,]+)")
_MAXEVENTS_RE = re.compile(r"-MaxEvents (\d+)")
_BEFORE_RE = re.compile(r"SystemTime&lt;'([^']+)'")


def answer_events(script: str) -> BridgeResult:
    levels = {int(v) for v in _LEVEL_RE.search(script).group(1).split(",")}
    count = int(_MAXEVENTS_RE.search(script).group(1))
    records = [r for r in system_log_records(time.time()) if r["Level"] in levels][:count]
    return BridgeResult("ok" if records else "empty", items=records, took_ms=41)


def answer_record(script: str) -> BridgeResult:
    before = _parse_stamp(_BEFORE_RE.search(script).group(1))
    count = int(_MAXEVENTS_RE.search(script).group(1))
    records = [r for r in system_log_records(time.time()) if _parse_stamp(r["TimeCreated"]) < before][:count]
    return BridgeResult("ok" if records else "empty", items=records, took_ms=37)


class FixtureBridge:
    exe = "fixture"
    available = True

    def run(self, script: str, *, timeout: float = 60, depth: int = 6) -> BridgeResult:
        if "$env:COMPUTERNAME" in script:
            return BridgeResult("ok", items=[{"host": FIXTURE_HOST, "user": FIXTURE_USER, "ps": "5.1", "os": "10.0"}], took_ms=3)
        if "foreach ($log in" in script:
            # The stream's cursor probe: neither view under capture watches the live stream, but
            # it starts on every page and would otherwise show "failed" forever. Anchoring both
            # logs to a real high-water mark makes it read as caught up, not broken.
            top = max((r["RecordId"] for r in system_log_records(time.time())), default=0)
            return BridgeResult("ok", items=[{"log": "System", "record": top}, {"log": "Application", "record": 1}], took_ms=6)
        if "WHEA-Logger" in script:
            cap = _MAXEVENTS_RE.search(script)
            return BridgeResult("ok", items=whea_records(time.time(), int(cap.group(1)) if cap else None), took_ms=412)
        if "FilterHashtable" in script:
            return answer_events(script)
        if "SystemTime&lt;" in script:
            return answer_record(script)
        # every other reading (hardware.*, dumps, system, pcie, ...) answers empty: the two
        # screenshot views never take them, and nothing here should render as if it were data.
        return BridgeResult("empty", items=[], took_ms=5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8021)
    args = parser.parse_args()

    state = State(bridge=FixtureBridge())
    app = create_app(state, mcp=False)

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
