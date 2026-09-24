"""Captures: readings that can be taken without a selection, written to one ZIP on disk.

A capture is what a person hands to someone who is not at the machine, or keeps for the day the
machine will not start. It holds one envelope per automatically selectable reading. Signals'
seven source envelopes become those readings' ZIP members; the other readings are taken in turn,
heavy ones included, so their costs add up. It also holds the stack as it stands, the composed handoff, and
a manifest that lists exactly the members with each reading's outcome and size. A reading that
needs an exact event or file reference is listed as omitted in that manifest. A reading that was
attempted but could not answer is written with its outcome, never hidden.

The files are redacted like every other response unless the caller asked for ``unredacted`` by
name. Completed captures are never deleted: the directory is the person's record. An unfinished
pending file is not a capture and is removed after cancellation or once stale.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio

from . import __version__
from .bridge import OUTCOMES, Bridge
from .paths import captures_dir
from .reading import REGISTRY, Reading, ReadingCall, automatic_params, take
from .readings.diagnostics import SIGNAL_INPUTS, compose_signals, gather_signal_inputs
from .redact import Redactor
from .serialization import json_safe_integers
from .stack import Prompts, Stack, StoreUnavailable, compose

NAME = re.compile(r"^capture-\d{8}T\d{6}Z(-\d+)?\.zip$")

READINGS_MEMBER = "readings/{name}.json"
STACK_MEMBER = "stack.json"
COMPOSED_MEMBER = "composed.md"
MANIFEST_MEMBER = "manifest.json"
MAX_LIST_MANIFEST_BYTES = 256 * 1024
STALE_PENDING_SECONDS = 24 * 60 * 60
logger = logging.getLogger(__name__)


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
    unavailable: list[dict[str, str]] = []
    omitted = [{"reading": name, "reason": "requires an exact selection"} for name, spec in REGISTRY.items() if spec.requires_selection]
    directory = captures_dir()
    _reap_stale_pending(directory)
    fd, temporary_name = tempfile.mkstemp(prefix=".capture-", suffix=".pending", dir=directory)
    os.close(fd)
    temporary = Path(temporary_name)
    # The request owns the pending ZIP during reading takes. Once the tail worker claims it,
    # cancellation must leave closing, publication and cleanup to that worker alone.
    owner = threading.Lock()
    try:
        archive = zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    try:
        signal_inputs: dict[str, Reading | None] = {}
        signal_reasons: dict[str, str] = {}
        signal_reading: Reading | None = None
        signal_gather_error: str | None = None
        signal_scopes = dict(SIGNAL_INPUTS)
        if "signals" in REGISTRY and all(name in REGISTRY and not REGISTRY[name].requires_selection for name in signal_scopes):
            signal_started = time.perf_counter()
            try:
                signal_inputs, signal_reasons = await gather_signal_inputs(bridge)
            except Exception as exc:  # noqa: BLE001 - preserve the rest of the capture if gathering itself fails
                signal_gather_error = f"Signals input gathering raised {type(exc).__name__}; which sources answered is unknown."
                signal_reading = _failed_envelope("signals", {}, signal_gather_error)
            else:
                try:
                    signal_reading = compose_signals(signal_inputs, signal_reasons, {})
                except Exception as exc:  # noqa: BLE001 - keep gathered sources without asking them again
                    signal_reading = _failed_envelope("signals", {}, f"Signals composition raised {type(exc).__name__}; its gathered source members remain available.")
            signal_reading.took_ms = int((time.perf_counter() - signal_started) * 1000)

        for name, spec in list(REGISTRY.items()):
            if spec.requires_selection:
                continue
            observed_by_signals = name in signal_inputs
            if name == "signals" and signal_reading is not None:
                reading = signal_reading
            elif observed_by_signals:
                reading = signal_inputs[name] or _failed_envelope(name, signal_scopes[name], signal_reasons.get(name, "Signals did not obtain this input"))
            elif name in signal_scopes and signal_gather_error is not None:
                # Gathering might have asked this source before failing. Never re-query it
                # silently or claim to know which of the seven attempts completed.
                reading = _failed_envelope(name, signal_scopes[name], "Signals input gathering stopped before this source's result could be retained; this capture did not retry it.")
            else:
                reading = await _take(name, bridge, started, reader)
            body = reading.to_dict()
            if redactor is not None:
                body, taken_out = redactor.redact(body)
                body["redacted"] = taken_out
                body["redaction_gaps"] = redactor.gaps()
                removed.update(taken_out)
            member = READINGS_MEMBER.format(name=name)
            entry = {"path": member, "reading": name, "outcome": body["outcome"], "took_ms": body["took_ms"], "bytes": _write(archive, member, json.dumps(json_safe_integers(body), ensure_ascii=False, indent=1))}
            if observed_by_signals:
                entry["observed_by"] = "signals"
                entry["params"] = body["params"]
            if name == "whea":
                entry["scope"] = "bounded newest-record preview; exact WHEA fields, full CPER bytes and decoded detail require whea_record and are not in this capture"
            elif name == "whea_reports":
                entry["scope"] = "compact Kernel-WHEA report-time timeline; per-report references require references=true in a new reading, bounded previews require whea_window, and exact fields require whea_record"
            members.append(entry)

        made = await anyio.to_thread.run_sync(
            _finish, owner, archive, temporary, started, members, removed, unavailable, omitted, stack, prompts, redactor, reason
        )
        if made is None:
            raise RuntimeError("capture tail was not claimed")  # unreachable unless ownership bookkeeping is broken
        return made
    finally:
        if owner.acquire(blocking=False):
            try:
                archive.close()
            except Exception:
                # This ZIP is being discarded; cleanup must not replace cancellation or a reading error.
                logger.exception("unfinished capture could not be closed")
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.exception("unfinished capture could not be removed")


def _finish(
    owner: threading.Lock, archive: zipfile.ZipFile, temporary: Path, started: datetime,
    members: list[dict[str, Any]], removed: set[str], unavailable: list[dict[str, str]],
    omitted: list[dict[str, str]], stack: Stack, prompts: Prompts, redactor: Redactor | None, reason: str | None,
) -> Capture | None:
    """One worker owns the Stack tail, ZIP close, atomic publication and cleanup."""
    if not owner.acquire(blocking=False):
        return None  # cancellation claimed and removed the pending ZIP before this worker began
    try:
        with archive:
            try:
                snapshot: dict[str, Any] = stack.state()
                archive_state = snapshot
                if redactor is not None:
                    archive_state, taken_out = redactor.redact(snapshot)
                    removed.update(taken_out)
                    archive_state["redaction_gaps"] = redactor.gaps()
                members.append({"path": STACK_MEMBER, "items": len(archive_state.get("items") or []), "bytes": _write(archive, STACK_MEMBER, json.dumps(json_safe_integers(archive_state), ensure_ascii=False, indent=1))})
            except StoreUnavailable:
                unavailable.append({"member": STACK_MEMBER, "reason": "saved Stack data unavailable"})

            if not unavailable:
                composed = compose(stack, prompts, redactor, stack_state=snapshot)
                removed.update(composed["redacted"])
                members.append({"path": COMPOSED_MEMBER, "items": composed["items"], "prompt": composed["prompt"], "bytes": _write(archive, COMPOSED_MEMBER, composed["text"])})
            else:
                unavailable.append({"member": COMPOSED_MEMBER, "reason": "saved Stack data unavailable"})

            manifest = {
                "tool": "system-sentinel",
                "version": __version__,
                "created_at": _stamp(started),
                "unredacted": redactor is None,
                "redacted": sorted(removed),
                "readings": len(REGISTRY) - len(omitted),
                "omitted": omitted,
                "unavailable": unavailable,
                "members": members,
            }
            if redactor is not None:
                manifest["redaction_gaps"] = redactor.gaps()
            if reason and redactor is None:
                manifest["reason"] = reason
            _write(archive, MANIFEST_MEMBER, json.dumps(json_safe_integers(manifest), ensure_ascii=False, indent=1))

        # The central directory and manifest must be closed before a list or download sees the ZIP.
        # Publication refuses an existing name, including another capture from this same second.
        return Capture(path=_publish(temporary, started), manifest=manifest)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # A published capture remains successful; an owner-only pending copy can be reaped later.
            logger.exception("capture pending copy could not be removed")


async def _take(name: str, bridge: Bridge, at: datetime, reader: ReadingCall | None = None) -> Reading:
    """One reading for the capture. A reading that needs a moment is given the capture's own.

    Anything the catalog refuses becomes an envelope that says so, so one reading cannot end a capture.
    """
    params = automatic_params(name, at)
    try:
        return await reader(name, params) if reader else await take(name, bridge, params)
    except Exception as exc:  # noqa: BLE001 - one reading's failure must not end the capture
        return _failed_envelope(name, params, f"the capture could not take this reading: {exc}")


def _failed_envelope(name: str, params: dict[str, Any], detail: str) -> Reading:
    """Save an attempted source failure without silently asking the machine again."""
    return Reading(reading=name, params=params, outcome="failed", method={"kind": "none", "query": ""},
                   error={"kind": "failed", "detail": detail})


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
    unavailable = manifest.get("unavailable", [])
    if not isinstance(unavailable, list) or any(
        not isinstance(entry, dict) or entry.get("member") not in (STACK_MEMBER, COMPOSED_MEMBER)
        or not isinstance(entry.get("reason"), str)
        for entry in unavailable
    ):
        return {"status": "unreadable"}
    unavailable_names = [entry["member"] for entry in unavailable]
    if len(unavailable_names) != len(set(unavailable_names)) or set(unavailable_names) & {entry.get("path") for entry in members if isinstance(entry, dict)}:
        return {"status": "unreadable"}
    gaps = manifest.get("redaction_gaps")
    if gaps is not None and (not isinstance(gaps, list) or any(type(gap) is not str or gap not in ("host", "user") for gap in gaps) or len(gaps) != len(set(gaps))):
        return {"status": "unreadable"}
    counts = Counter(row["outcome"] for row in rows)
    handoff = next((entry for entry in members if isinstance(entry, dict) and entry.get("path") == COMPOSED_MEMBER), None)
    prompt = handoff.get("prompt") if handoff else None
    prompt_state = prompt.get("state") if isinstance(prompt, dict) else None
    summary = {
        "status": "read",
        "captured_at": captured_at,
        "unredacted": unredacted,
        "readings": readings,
        "omitted": len(omitted_names),
        "unavailable": unavailable_names,
        "prompt_state": prompt_state if prompt_state in ("included", "off", "none", "missing", "unavailable") else None,
        "outcomes": {outcome: counts[outcome] for outcome in OUTCOMES if counts[outcome]},
    }
    if gaps is not None:
        summary["redaction_gaps"] = gaps
    return summary


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
