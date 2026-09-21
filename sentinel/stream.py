"""The stream: the machine's log as it happens, over server-sent events.

One query per poll covers both logs. Each preset contributes one selector — its
providers, its event ids, and ``EventRecordID`` above that log's cursor — so the
query returns exactly what is new and the log's own index does the work. The
selectors stay separate on purpose: the Windows event log accepts a bounded
number of expressions in one selector, and the presets' ids in a single flat
selector exceed it, which is why the query has to be shaped per preset and not
per log. The cursors start at the logs' current latest records, so opening the
stream replays no history.

Each poll runs in a worker thread: the bridge blocks on a process and the server
must keep answering. A poll that did not observe the machine emits a ``bridge``
event in the reading's vocabulary, because a silent stream is not a healthy
machine; a stream with heartbeats and no ``bridge`` events is.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .bridge import Bridge, BridgeResult

# One projection for every log reading: a record that arrives on the stream and a record read
# by `events` are the same shape, field for field.
from .readings.events import RECORD_SELECT, winevent
from .redact import Redactor

POLL_SECONDS = 5.0
MAX_PER_POLL = 200
"""Oldest first above the cursor: a burst is emitted in order over several polls, never skipped."""


@dataclass(frozen=True)
class Preset:
    """What the stream watches for. The providers and ids are the tool's, carried over unchanged."""

    id: str
    log: str
    providers: tuple[str, ...]
    ids: tuple[int, ...]


PRESETS: tuple[Preset, ...] = (
    Preset("crash_power", "System", ("Microsoft-Windows-Kernel-Power", "EventLog", "Microsoft-Windows-WER-SystemErrorReporting", "volmgr"), (41, 6008, 1001, 46, 161, 162)),
    Preset("whea_hardware", "System", ("Microsoft-Windows-WHEA-Logger",), (1, 17, 18, 19, 20, 46, 47)),
    Preset("storage_io", "System", ("Disk", "Ntfs", "storahci", "stornvme", "storport", "iaStorA", "iaStorAC"), (7, 11, 51, 55, 57, 129, 153)),
    Preset("driver_service", "System", ("Microsoft-Windows-Kernel-PnP", "Microsoft-Windows-DriverFrameworks-UserMode", "Service Control Manager"), (219, 10110, 10111, 7000, 7001, 7009, 7011, 7026, 7031, 7034)),
    Preset("app_crashes", "Application", ("Application Error", "Windows Error Reporting"), (1000, 1001, 1002)),
    Preset("tpm_secureboot", "System", ("Microsoft-Windows-TPM-WMI",), (1796, 1801)),
)

LOGS: tuple[str, ...] = ("System", "Application")

CURSOR_SCRIPT = f"""foreach ($log in {','.join(f"'{log}'" for log in LOGS)}) {{
    $latest = Get-WinEvent -LogName $log -MaxEvents 1 -ErrorAction SilentlyContinue
    [pscustomobject]@{{ log = $log; record = $(if ($latest) {{ [int64]$latest.RecordId }} else {{ $null }}) }}
}}"""


def selector(preset: Preset, cursor: int) -> str:
    providers = " or ".join(f"Provider[@Name='{p}']" for p in preset.providers)
    ids = " or ".join(f"EventID={i}" for i in preset.ids)
    return f"""<Select Path="{preset.log}">*[System[({providers}) and ({ids}) and EventRecordID &gt; {cursor}]]</Select>"""


def poll_script(cursors: Mapping[str, int], limit: int = MAX_PER_POLL) -> str:
    """The one query: every preset's selector, grouped by log, above each log's cursor.

    The records are sorted and bounded before the projection, so a storm costs one poll's worth
    of message formatting rather than the whole backlog's.
    """
    queries = "".join(
        f"""<Query Id="{n}" Path="{log}">{''.join(selector(p, int(cursors.get(log, 0))) for p in PRESETS if p.log == log)}</Query>"""
        for n, log in enumerate(LOGS)
    )
    return f"""$xml = @"
<QueryList>{queries}</QueryList>
"@
""" + winevent(
        f"""Get-WinEvent -FilterXml $xml -ErrorAction Stop |
    Sort-Object RecordId | Select-Object -First {int(limit)} |
    ForEach-Object {{ [pscustomobject]@{{ log = $_.LogName; record = ($_ | {RECORD_SELECT}) }} }}"""
    )


class Stream:
    """The poll loop behind ``GET /api/stream``. One instance per connected client."""

    def __init__(self, bridge: Bridge, redactor: Redactor | None = None, *, interval: float = POLL_SECONDS, limit: int = MAX_PER_POLL):
        self.bridge = bridge
        self.redactor = redactor
        self.interval = interval
        self.limit = limit
        self.cursors: dict[str, int] = {}

    @property
    def ready(self) -> bool:
        """Every log has said where it is. Until then there is nothing to be new against."""
        return len(self.cursors) == len(LOGS)

    def start_cursors(self) -> BridgeResult:
        """Ask each log for its latest record, so the stream begins at now and replays nothing."""
        result = self.bridge.run(CURSOR_SCRIPT, timeout=30)
        for item in result.items:
            log, record = item.get("log"), item.get("record")
            if log in LOGS and isinstance(record, int):
                self.cursors[log] = record
        return result

    def poll(self) -> tuple[BridgeResult, list[dict[str, Any]]]:
        """One round trip. Returns what the bridge said and the new records as stream payloads."""
        result = self.bridge.run(poll_script(self.cursors, self.limit))
        events: list[dict[str, Any]] = []
        for item in result.items:
            record = item.get("record")
            log = item.get("log")
            rid = record.get("RecordId") if isinstance(record, dict) else None
            if not isinstance(rid, int) or log not in LOGS:
                continue  # not a record of a watched log: nothing to emit and nothing to advance
            self.cursors[log] = max(self.cursors.get(log, 0), rid)
            payload: dict[str, Any] = {"log": log, "record": record}
            if self.redactor is not None:
                payload = self.redactor.attach(payload)
            events.append(payload)
        return result, events

    async def events(self, disconnected: Callable[[], Awaitable[bool]] | None = None) -> AsyncIterator[str]:
        """The frames, forever: ``record`` for each new record, ``bridge`` when a poll did not
        observe the machine, ``heartbeat`` on every poll. Ends cleanly when the client goes away."""
        try:
            while True:
                if disconnected is not None and await disconnected():
                    return
                if self.ready:
                    result, records = await asyncio.to_thread(self.poll)
                    for record in records:
                        yield frame("record", record)
                else:
                    result = await asyncio.to_thread(self.start_cursors)
                    if result.observed and not self.ready:
                        yield frame("bridge", {"outcome": "failed", "error": "the logs did not report where they are; the stream has nothing to be new against"})
                if not result.observed:
                    # The error text is PowerShell's own and can quote a path or a name: it leaves redacted too.
                    detail = {"outcome": result.outcome, "error": result.error or ""}
                    yield frame("bridge", self.redactor.attach(detail) if self.redactor is not None else detail)
                yield frame("heartbeat", {"at": _now(), "cursors": dict(self.cursors)})
                # While the machine is not answering, every poll costs the bridge's full retries: ask less often.
                await asyncio.sleep(self.interval if result.observed else self.interval * 4)
        except (asyncio.CancelledError, GeneratorExit):
            return


def frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
