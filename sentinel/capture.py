"""Captures: readings that can be taken without a selection, written to one ZIP on disk.

A capture is what a person hands to someone who is not at the machine, or keeps for the day the
machine will not start. It holds one envelope per automatically selectable reading — the heavy ones included, so it takes
as long as the slowest query on this machine — the stack as it stands, the composed handoff, and
a manifest that lists exactly the members with each reading's outcome and size. A reading that
needs an exact event or file reference is listed as omitted in that manifest. A reading that was
attempted but could not answer is written with its outcome, never hidden.

The files are redacted like every other response unless the caller asked for ``unredacted`` by
name. Completed captures are never deleted: the directory is the person's record. An unfinished
pending file is not a capture and is removed after cancellation or once stale.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .bridge import OUTCOMES, Bridge
from .paths import captures_dir
from .reading import REGISTRY, Reading, ReadingCall, automatic_params, take
from .redact import Redactor
from .serialization import json_safe_integers
from .stack import Prompts, Stack, compose

NAME = re.compile(r"^capture-\d{8}T\d{6}Z(-\d+)?\.zip$")

READINGS_MEMBER = "readings/{name}.json"
STACK_MEMBER = "stack.json"
COMPOSED_MEMBER = "composed.md"
MANIFEST_MEMBER = "manifest.json"
MAX_LIST_MANIFEST_BYTES = 256 * 1024
STALE_PENDING_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class Capture:
    path: Path
    manifest: dict[str, Any]

    @property
    def name(self) -> str:
        return self.path.name


async def create(bridge: Bridge, stack: Stack, prompts: Prompts, redactor: Redactor | None = None, *, reason: str | None = None, reader: ReadingCall | None = None) -> Capture:
    """Take automatic readings, then make the complete ZIP visible in one filesystem step."""
    started = datetime.now(UTC)
    members: list[dict[str, Any]] = []
    removed: set[str] = set()
    omitted = [{"reading": name, "reason": "requires an exact selection"} for name, spec in REGISTRY.items() if spec.requires_selection]
    directory = captures_dir()
    _reap_stale_pending(directory)
    fd, temporary_name = tempfile.mkstemp(prefix=".capture-", suffix=".pending", dir=directory)
    os.close(fd)
    temporary = Path(temporary_name)

    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, spec in list(REGISTRY.items()):
                if spec.requires_selection:
                    continue
                reading = await _take(name, bridge, started, reader)
                body = reading.to_dict()
                if redactor is not None:
                    body, taken_out = redactor.redact(body)
                    body["redacted"] = taken_out
                    removed.update(taken_out)
                member = READINGS_MEMBER.format(name=name)
                members.append({"path": member, "reading": name, "outcome": body["outcome"], "took_ms": body["took_ms"], "bytes": _write(archive, member, json.dumps(json_safe_integers(body), ensure_ascii=False, indent=1))})

            state: dict[str, Any] = stack.state()
            if redactor is not None:
                state, taken_out = redactor.redact(state)
                removed.update(taken_out)
            members.append({"path": STACK_MEMBER, "items": len(state.get("items") or []), "bytes": _write(archive, STACK_MEMBER, json.dumps(json_safe_integers(state), ensure_ascii=False, indent=1))})

            composed = compose(stack, prompts, redactor)
            removed.update(composed["redacted"])
            members.append({"path": COMPOSED_MEMBER, "items": composed["items"], "bytes": _write(archive, COMPOSED_MEMBER, composed["text"])})

            manifest = {
                "tool": "system-sentinel",
                "version": __version__,
                "created_at": _stamp(started),
                "unredacted": redactor is None,
                "redacted": sorted(removed),
                "readings": len(REGISTRY) - len(omitted),
                "omitted": omitted,
                "members": members,
            }
            if reason and redactor is None:
                manifest["reason"] = reason
            _write(archive, MANIFEST_MEMBER, json.dumps(json_safe_integers(manifest), ensure_ascii=False, indent=1))

        # The ZIP central directory and manifest must be closed before a list or download can see it.
        # Publication refuses an existing name, including another capture from this same second.
        path = _publish(temporary, started)
        return Capture(path=path, manifest=manifest)
    finally:
        temporary.unlink(missing_ok=True)


async def _take(name: str, bridge: Bridge, at: datetime, reader: ReadingCall | None = None) -> Reading:
    """One reading for the capture. A reading that needs a moment is given the capture's own.

    Anything the catalog refuses becomes an envelope that says so, so one reading cannot end a capture.
    """
    params = automatic_params(name, at)
    try:
        return await reader(name, params) if reader else await take(name, bridge, params)
    except Exception as exc:  # noqa: BLE001 - one reading's failure must not end the capture
        return Reading(
            reading=name,
            params=params,
            outcome="failed",
            method={"kind": "none", "query": ""},
            error={"kind": "failed", "detail": f"the capture could not take this reading: {exc}"},
        )


def listing() -> list[dict[str, Any]]:
    """What is on disk, newest first, with only bounded facts from each capture's manifest."""
    out = []
    directory = captures_dir()
    _reap_stale_pending(directory)
    for path in directory.glob("capture-*.zip"):
        if NAME.match(path.name):
            try:
                stat = path.stat()
            except OSError:  # a capture removed between the directory scan and this entry
                continue
            out.append({
                "name": path.name,
                "bytes": stat.st_size,
                "created_at": _stamp(datetime.fromtimestamp(stat.st_mtime, tz=UTC)),
                "manifest": _manifest_summary(path),
            })
    return sorted(out, key=lambda c: c["name"], reverse=True)


def _manifest_summary(path: Path) -> dict[str, Any]:
    """Read one small manifest; never decompress a capture's reading members to list it."""
    try:
        with zipfile.ZipFile(path) as archive:
            manifests = [info for info in archive.infolist() if info.filename == MANIFEST_MEMBER]
            if not manifests:
                return {"status": "missing"}
            if len(manifests) != 1:
                return {"status": "unreadable"}
            if manifests[0].file_size > MAX_LIST_MANIFEST_BYTES:
                return {"status": "limit"}
            with archive.open(manifests[0]) as stream:
                raw = stream.read(MAX_LIST_MANIFEST_BYTES + 1)
            if len(raw) > MAX_LIST_MANIFEST_BYTES:
                return {"status": "limit"}
        manifest = json.loads(raw)
    except (OSError, ValueError, RuntimeError, NotImplementedError, zipfile.BadZipFile):
        return {"status": "unreadable"}

    if not isinstance(manifest, dict) or manifest.get("tool") != "system-sentinel":
        return {"status": "unreadable"}
    unredacted, readings, members, captured_at = (manifest.get(key) for key in ("unredacted", "readings", "members", "created_at"))
    if type(unredacted) is not bool or type(readings) is not int or readings < 0 or not isinstance(members, list) or not isinstance(captured_at, str):
        return {"status": "unreadable"}
    try:
        if datetime.fromisoformat(captured_at.replace("Z", "+00:00")).tzinfo is None:
            return {"status": "unreadable"}
    except ValueError:
        return {"status": "unreadable"}
    rows = [member for member in members if isinstance(member, dict) and "reading" in member]
    if len(rows) != readings or len({row.get("reading") for row in rows if isinstance(row.get("reading"), str)}) != readings:
        return {"status": "unreadable"}
    if any(row.get("outcome") not in OUTCOMES for row in rows):
        return {"status": "unreadable"}
    omitted = manifest.get("omitted", [])
    if not isinstance(omitted, list) or any(
        not isinstance(entry, dict) or not isinstance(entry.get("reading"), str) or not isinstance(entry.get("reason"), str)
        for entry in omitted
    ):
        return {"status": "unreadable"}
    omitted_names = [entry["reading"] for entry in omitted]
    if len(omitted_names) != len(set(omitted_names)) or set(omitted_names) & {row["reading"] for row in rows}:
        return {"status": "unreadable"}
    counts = Counter(row["outcome"] for row in rows)
    return {
        "status": "read",
        "captured_at": captured_at,
        "unredacted": unredacted,
        "readings": readings,
        "omitted": len(omitted_names),
        "outcomes": {outcome: counts[outcome] for outcome in OUTCOMES if counts[outcome]},
    }


def find(name: str) -> Path | None:
    """One capture by name. A name that is not a capture's is not a path: it never leaves the directory."""
    if not NAME.match(name):
        return None
    path = captures_dir() / name
    return path if path.is_file() else None


def _publish(temporary: Path, at: datetime) -> Path:
    base = temporary.parent / f"capture-{at.strftime('%Y%m%dT%H%M%SZ')}.zip"
    n = 1
    while True:
        candidate = base if n == 1 else base.with_name(f"{base.stem}-{n}.zip")
        try:
            # Windows rename refuses an existing destination. POSIX rename replaces it, so a
            # same-directory hard link supplies its atomic, no-overwrite publication there.
            if os.name == "nt":
                os.rename(temporary, candidate)
            else:
                os.link(temporary, candidate)
            return candidate
        except FileExistsError:
            n += 1


def _reap_stale_pending(directory: Path) -> None:
    """Remove abandoned scratch files without touching completed captures."""
    cutoff = time.time() - STALE_PENDING_SECONDS
    for path in directory.glob(".capture-*.pending"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            # A scanner may hold this file; a later pass can retry. The list stays usable.
            continue


def _write(archive: zipfile.ZipFile, member: str, text: str) -> int:
    data = text.encode("utf-8")
    archive.writestr(member, data)
    return len(data)


def _stamp(at: datetime) -> str:
    return at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
