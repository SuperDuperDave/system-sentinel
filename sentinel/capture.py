"""Captures: every reading taken now, written to one ZIP on disk, and nothing sent anywhere.

A capture is what a person hands to someone who is not at the machine, or keeps for the day the
machine will not start. It holds one envelope per reading — the heavy ones included, so it takes
as long as the slowest query on this machine — the stack as it stands, the composed handoff, and
a manifest that lists exactly the members with each reading's outcome and size. A reading that
could not be taken is written with its outcome, never omitted: a capture says what was not
observed as plainly as what was.

The files are redacted like every other response unless the caller asked for ``unredacted`` by
name. Nothing is ever deleted: the directory is the person's record.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .bridge import Bridge
from .paths import captures_dir
from .reading import REGISTRY, Reading, take
from .redact import Redactor
from .stack import Prompts, Stack, compose

NAME = re.compile(r"^capture-\d{8}T\d{6}Z(-\d+)?\.zip$")

READINGS_MEMBER = "readings/{name}.json"
STACK_MEMBER = "stack.json"
COMPOSED_MEMBER = "composed.md"
MANIFEST_MEMBER = "manifest.json"


@dataclass(frozen=True)
class Capture:
    path: Path
    manifest: dict[str, Any]

    @property
    def name(self) -> str:
        return self.path.name


async def create(bridge: Bridge, stack: Stack, prompts: Prompts, redactor: Redactor | None = None, *, reason: str | None = None) -> Capture:
    """Take every reading in the catalog now and write the ZIP. Returns where it landed and its manifest."""
    started = datetime.now(UTC)
    path = _free_path(started)
    members: list[dict[str, Any]] = []
    removed: set[str] = set()

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in list(REGISTRY):
            reading = await _take(name, bridge, started)
            body = reading.to_dict()
            if redactor is not None:
                body, taken_out = redactor.redact(body)
                body["redacted"] = taken_out
                removed.update(taken_out)
            member = READINGS_MEMBER.format(name=name)
            members.append({"path": member, "reading": name, "outcome": body["outcome"], "took_ms": body["took_ms"], "bytes": _write(archive, member, json.dumps(body, ensure_ascii=False, indent=1))})

        state: dict[str, Any] = stack.state()
        if redactor is not None:
            state, taken_out = redactor.redact(state)
            removed.update(taken_out)
        members.append({"path": STACK_MEMBER, "items": len(state.get("items") or []), "bytes": _write(archive, STACK_MEMBER, json.dumps(state, ensure_ascii=False, indent=1))})

        composed = compose(stack, prompts, redactor)
        removed.update(composed["redacted"])
        members.append({"path": COMPOSED_MEMBER, "items": composed["items"], "bytes": _write(archive, COMPOSED_MEMBER, composed["text"])})

        manifest = {
            "tool": "system-sentinel",
            "version": __version__,
            "created_at": _stamp(started),
            "unredacted": redactor is None,
            "redacted": sorted(removed),
            "readings": len(REGISTRY),
            "members": members,
        }
        if reason and redactor is None:
            manifest["reason"] = reason
        _write(archive, MANIFEST_MEMBER, json.dumps(manifest, ensure_ascii=False, indent=1))

    return Capture(path=path, manifest=manifest)


async def _take(name: str, bridge: Bridge, at: datetime) -> Reading:
    """One reading for the capture. A reading that needs a moment is given the capture's own.

    Anything the catalog refuses becomes an envelope that says so, so one reading cannot end a capture.
    """
    params = {"before": _stamp(at)} if name == "record" else {}
    try:
        return await take(name, bridge, params)
    except Exception as exc:  # noqa: BLE001 - one reading's failure must not end the capture
        return Reading(
            reading=name,
            params=params,
            outcome="failed",
            method={"kind": "none", "query": ""},
            error={"kind": "failed", "detail": f"the capture could not take this reading: {exc}"},
        )


def listing() -> list[dict[str, Any]]:
    """What is on disk, newest first."""
    out = []
    for path in captures_dir().glob("capture-*.zip"):
        if NAME.match(path.name):
            stat = path.stat()
            out.append({"name": path.name, "bytes": stat.st_size, "created_at": _stamp(datetime.fromtimestamp(stat.st_mtime, tz=UTC))})
    return sorted(out, key=lambda c: c["name"], reverse=True)


def find(name: str) -> Path | None:
    """One capture by name. A name that is not a capture's is not a path: it never leaves the directory."""
    if not NAME.match(name):
        return None
    path = captures_dir() / name
    return path if path.is_file() else None


def _free_path(at: datetime) -> Path:
    base = captures_dir() / f"capture-{at.strftime('%Y%m%dT%H%M%SZ')}.zip"
    if not base.exists():
        return base
    n = 2
    while (candidate := base.with_name(base.name.replace(".zip", f"-{n}.zip"))).exists():
        n += 1
    return candidate


def _write(archive: zipfile.ZipFile, member: str, text: str) -> int:
    data = text.encode("utf-8")
    archive.writestr(member, data)
    return len(data)


def _stamp(at: datetime) -> str:
    return at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
