"""Readings shared by every client, including selections that must survive redaction.

Dump references are keyed selectors for a current location, scoped to this service's lifetime.
They retain neither paths nor file contents. Resolving one requires a fresh inventory; the
bounded reader then validates its own fresh inventory again before opening the selected file.
"""

from __future__ import annotations

import hashlib
import hmac
import ntpath
import re
import secrets
import time
from dataclasses import replace
from typing import Any

from .bridge import Bridge
from .reading import REGISTRY, Reading, Section, take
from .readings.dump_header import validate_selection
from .readings.dumps import ALL_LOCATION_IDS

REFERENCE = re.compile(r"d1[0-9a-f]{82}\Z")
REFERENCE_BASIS = "References select the current file at an inventoried location, not saved contents. Refresh the dump inventory after Sentinel restarts."


class ReadingService:
    def __init__(self, bridge: Bridge):
        self.bridge = bridge
        self._key = secrets.token_bytes(32)
        self._instance = secrets.token_hex(8)

    def _reference(self, source: str, path: str) -> str:
        source_code = f"{ALL_LOCATION_IDS.index(source):02x}"
        # The private root and even a personally named file are never a guessable unkeyed
        # digest. Neither the key nor the digest computation reaches a PowerShell query.
        identity = source + "\0" + ntpath.normcase(path)
        digest = hmac.new(self._key, identity.encode("utf-8"), hashlib.sha256).hexdigest()
        return "d1" + self._instance + source_code + digest

    def _source(self, reference: str) -> str:
        if not REFERENCE.fullmatch(reference):
            raise ValueError("ref must be an inspection reference from the dump inventory")
        if reference[2:18] != self._instance:
            raise ValueError("This dump reference belongs to another Sentinel session. Refresh the dump inventory and select the file again.")
        index = int(reference[18:20], 16)
        if index >= len(ALL_LOCATION_IDS):
            raise ValueError("ref does not name a supported dump source")
        return ALL_LOCATION_IDS[index]

    async def take(self, name: str, raw_params: dict[str, Any] | None = None) -> Reading:
        """Measure through reference resolution and service-added evidence, not just the taker."""
        started = time.perf_counter()
        if name == "dump_header":
            params = REGISTRY[name].coerce(raw_params or {})
            validate_selection(params)
            if params["ref"]:
                reading = await self._inspect(params)
            else:
                reading = await take(name, self.bridge, raw_params)
        else:
            reading = await take(name, self.bridge, raw_params)
            if name == "dumps":
                files = reading.section("files")
                if files is not None:
                    targets = [{"file_index": index, "source": file["source"], "ref": self._reference(file["source"], file["path"])} for index, file in enumerate(files.data)]
                    reading.sections.append(Section("inspection_targets", "derived", targets, REFERENCE_BASIS))
        return replace(reading, took_ms=int((time.perf_counter() - started) * 1000))

    async def _inspect(self, params: dict[str, Any]) -> Reading:
        reference = params["ref"]
        source_id = self._source(reference)  # Refuse malformed/retired references before I/O.
        listing = await take("dumps", self.bridge)
        files, collection = listing.section("files"), listing.section("collection")
        match = next((file for file in files.data if file["source"] == source_id and hmac.compare_digest(self._reference(source_id, file["path"]), reference)), None) if files else None
        if match is None:
            reading = Reading("dump_header", params, listing.outcome, listing.method, took_ms=listing.took_ms, warnings=list(listing.warnings), error=listing.error)
            if collection is not None:
                reading.sections = [collection]
                source = next(row for row in collection.data["locations"] if row["id"] == source_id)
                if source["outcome"] in ("ok", "empty"):
                    reading.outcome, reading.count, reading.error = "empty", 0, None
                    reading.warnings.append("No file matching this reference was returned by its observed dump location. Refresh the dump inventory.")
                else:
                    reading.outcome = source["outcome"]
                    reading.error = {"kind": reading.outcome, "detail": "The selected dump location could not be fully read; whether this file is present is unknown."}
            return reading
        reading = await take("dump_header", self.bridge, {"path": match["path"]})
        reading.params = params
        reading.method = {"kind": "powershell", "queries": [listing.method["query"], reading.method["query"]]}
        reading.warnings = list(dict.fromkeys([*listing.warnings, *reading.warnings]))
        reading.sections.append(Section("selection", "derived", {"ref": reference, "source": source_id}, REFERENCE_BASIS))
        return reading
