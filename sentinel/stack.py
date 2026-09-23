"""The stack: the evidence chosen for handoff, the prompt library, and the text they compose into.

The stack lives on the server, in the data directory, so the desktop, the phone and the agent
see one stack; the composed handoff is a route an agent reads without a clipboard. Every
operation reads the file, changes it and writes it back under a lock, so there is no in-memory
copy to drift from what is on disk and a second process sees what the first wrote.

An item keeps the reading's envelope as it was at the moment of adding: its ``asked_at``,
``outcome`` and ``method`` are the item's provenance, and the composed text states them, so a
reading that failed cannot enter a handoff disguised as a finding. The same reading with the
same parameters and the same records is refused rather than stacked twice.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .bridge import Bridge
from .paths import data_dir
from .reading import REGISTRY, ReadingCall, take
from .redact import Redactor
from .serialization import json_safe_integers

KINDS = ("reading", "selection", "note")
VERBOSITIES = ("summary", "full")
RANKS = (1, 2, 3, 4, 5)
SUMMARY_MESSAGE = 80
"""How much of a record's message a summary table carries."""
SUMMARY_LOG_LIMIT = 100
SUMMARY_LOG_EDGE = 5


class Duplicate(Exception):
    """The same reading, the same parameters, the same records: already on the stack."""


@dataclass
class Item:
    id: str
    added_at: str
    kind: str
    title: str
    rank: int = 3
    verbosity: str = "full"
    reading: dict[str, Any] | None = None
    ids: list[int | str] | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "added_at": self.added_at,
            "kind": self.kind,
            "title": self.title,
            "rank": self.rank,
            "verbosity": self.verbosity,
            "reading": self.reading,
            "ids": self.ids,
            "note": self.note,
        }

    @property
    def signature(self) -> tuple[Any, ...] | None:
        """What makes two items the same evidence. A note is never a duplicate: it is written, not taken."""
        if self.kind == "note" or not self.reading:
            return None
        # Signals are snapshots: a later scan can report different leads or evidence even
        # with the same parameters. Keep it distinct without duplicating one held scan.
        moment = self.reading.get("asked_at") if self.reading.get("reading") == "signals" else None
        return (self.reading.get("reading"), json.dumps(self.reading.get("params"), sort_keys=True), tuple(sorted(str(i) for i in self.ids or ())), moment)


def item_from_dict(raw: dict[str, Any]) -> Item:
    return Item(
        id=raw["id"],
        added_at=raw["added_at"],
        kind=raw["kind"],
        title=raw.get("title") or "",
        rank=int(raw.get("rank") or 3),
        verbosity=raw.get("verbosity") or "full",
        reading=raw.get("reading"),
        ids=raw.get("ids"),
        note=raw.get("note"),
    )


class Store:
    """One JSON document in the data directory, read and written whole under a lock."""

    def __init__(self, path: Path, empty: dict[str, Any]):
        self.path = path
        self._empty = empty
        self._lock = threading.Lock()

    def read(self) -> dict[str, Any]:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return json.loads(json.dumps(self._empty))
        return loaded if isinstance(loaded, dict) else json.loads(json.dumps(self._empty))

    def write(self, state: dict[str, Any]) -> None:
        temp = self.path.with_suffix(self.path.suffix + ".new")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        temp.replace(self.path)

    @property
    def lock(self) -> threading.Lock:
        return self._lock


class Stack:
    """The chosen evidence, its order, and which prompt leads the handoff."""

    def __init__(self, path: Path | None = None):
        self.store = Store(path or data_dir() / "stack.json", {"items": [], "prompt_id": DEFAULT_PROMPT_ID, "system_prompt": True})

    def state(self) -> dict[str, Any]:
        raw = self.store.read()
        items = [item_from_dict(i).to_dict() for i in raw.get("items", []) if isinstance(i, dict)]
        return {"items": items, "prompt_id": raw.get("prompt_id"), "system_prompt": bool(raw.get("system_prompt", True))}

    def add(self, item: Item) -> Item:
        with self.store.lock:
            state = self.state()
            if item.signature is not None:
                for existing in state["items"]:
                    if item_from_dict(existing).signature == item.signature:
                        raise Duplicate(existing["id"])
            state["items"].append(item.to_dict())
            self.store.write(state)
        return item

    def update(self, item_id: str, *, rank: int | None = None, verbosity: str | None = None, title: str | None = None) -> dict[str, Any]:
        if rank is not None and rank not in RANKS:
            raise ValueError(f"rank must be one of {list(RANKS)}")
        if verbosity is not None and verbosity not in VERBOSITIES:
            raise ValueError(f"verbosity must be one of {list(VERBOSITIES)}")
        with self.store.lock:
            state = self.state()
            for stored in state["items"]:
                if stored["id"] == item_id:
                    if rank is not None:
                        stored["rank"] = rank
                    if verbosity is not None:
                        stored["verbosity"] = verbosity
                    if title is not None:
                        stored["title"] = title
                    self.store.write(state)
                    return stored
            raise KeyError(item_id)

    def remove(self, item_id: str) -> None:
        with self.store.lock:
            state = self.state()
            kept = [i for i in state["items"] if i["id"] != item_id]
            if len(kept) == len(state["items"]):
                raise KeyError(item_id)
            state["items"] = kept
            self.store.write(state)

    def clear(self) -> None:
        with self.store.lock:
            state = self.state()
            state["items"] = []
            self.store.write(state)

    def choose(self, *, prompt_id: str | None = None, system_prompt: bool | None = None, set_prompt: bool = False) -> dict[str, Any]:
        """Change which prompt leads the handoff, or whether one does at all."""
        with self.store.lock:
            state = self.state()
            if set_prompt:
                state["prompt_id"] = prompt_id
            if system_prompt is not None:
                state["system_prompt"] = system_prompt
            self.store.write(state)
            return state


class Prompts:
    """The prompt library: the six the tool ships with, plus whatever was added. All of them editable."""

    def __init__(self, path: Path | None = None):
        self.store = Store(path or data_dir() / "prompts.json", {"prompts": []})

    def all(self) -> list[dict[str, Any]]:
        with self.store.lock:
            state = self.store.read()
            # Seeded once, on first use. An empty library afterwards is the person's choice, not a
            # reason to bring the presets back.
            if not state.get("seeded"):
                state = {"prompts": [{"id": slug(p["name"]), "builtin": True, **p} for p in PRESET_PROMPTS], "seeded": True}
                self.store.write(state)
            return list(state["prompts"])

    def get(self, prompt_id: str | None) -> dict[str, Any] | None:
        if not prompt_id:
            return None
        return next((p for p in self.all() if p["id"] == prompt_id), None)

    def add(self, name: str, description: str = "", content: str = "") -> dict[str, Any]:
        if not name.strip():
            raise ValueError("a prompt needs a name")
        prompts = self.all()
        prompt = {"id": _unique(slug(name), {p["id"] for p in prompts}), "name": name, "description": description, "content": content, "builtin": False}
        with self.store.lock:
            prompts.append(prompt)
            self.store.write({"prompts": prompts, "seeded": True})
        return prompt

    def update(self, prompt_id: str, **fields: Any) -> dict[str, Any]:
        prompts = self.all()
        with self.store.lock:
            for prompt in prompts:
                if prompt["id"] == prompt_id:
                    for key in ("name", "description", "content"):
                        if fields.get(key) is not None:
                            prompt[key] = fields[key]
                    self.store.write({"prompts": prompts, "seeded": True})
                    return prompt
        raise KeyError(prompt_id)

    def remove(self, prompt_id: str) -> None:
        prompts = self.all()
        kept = [p for p in prompts if p["id"] != prompt_id]
        if len(kept) == len(prompts):
            raise KeyError(prompt_id)
        with self.store.lock:
            self.store.write({"prompts": kept, "seeded": True})


async def new_item(stack: Stack, bridge: Bridge, body: dict[str, Any], *, reader: ReadingCall | None = None) -> Item:
    """Turn what a client sent into an item: take the reading now, or keep the envelope it holds.

    Refuses what cannot be evidence — a selection without matching ids, a note without text, a
    reading without either a ``take`` or an ``envelope`` — before anything is stored.
    """
    kind = body.get("kind") or "reading"
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {list(KINDS)}")
    rank = int(body["rank"]) if body.get("rank") is not None else 3
    if rank not in RANKS:
        raise ValueError(f"rank must be one of {list(RANKS)}")
    requested_verbosity = body.get("verbosity")
    if requested_verbosity is not None and requested_verbosity not in VERBOSITIES:
        raise ValueError(f"verbosity must be one of {list(VERBOSITIES)}")

    envelope: dict[str, Any] | None = None
    ids: list[int | str] | None = None
    note: str | None = None

    if kind == "note":
        note = (body.get("note") or "").strip()
        if not note:
            raise ValueError("a note needs text")
    else:
        asked = body.get("take")
        given = body.get("envelope")
        if bool(asked) == bool(given):
            raise ValueError("send either 'take' (the server reads the machine now) or 'envelope' (a reading you hold)")
        if asked:
            name = asked.get("name")
            if name not in REGISTRY:
                raise ValueError(f"no reading named {name!r}")
            params = asked.get("params") or {}
            envelope = (await reader(name, params) if reader else await take(name, bridge, params)).to_dict()
        else:
            if not isinstance(given, dict):
                raise ValueError("'envelope' must be a reading as the API returned it")
            envelope = dict(given)
            sections = envelope.get("sections")
            valid_sections = isinstance(sections, list) and all(
                isinstance(section, dict) and isinstance(section.get("name"), str)
                and isinstance(section.get("class"), str) and "data" in section
                for section in sections
            )
            valid_envelope = (
                isinstance(envelope.get("reading"), str) and isinstance(envelope.get("outcome"), str)
                and isinstance(envelope.get("params"), dict) and isinstance(envelope.get("method"), dict)
                and valid_sections and (envelope.get("error") is None or isinstance(envelope["error"], dict))
            )
            if not valid_envelope:
                raise ValueError("'envelope' must have the reading, outcome, params, method and section shapes returned by the API")
        if kind == "selection":
            ids = _selection_ids(envelope, body.get("ids") or [])

    large_log = bool(kind == "reading" and envelope and envelope.get("reading") in ("events", "record", "whea", "faults") and len(_records(envelope) or []) > SUMMARY_LOG_LIMIT)
    is_timeline = bool(kind == "reading" and envelope and envelope.get("reading") in ("storms", "whea_reports"))
    verbosity = requested_verbosity or ("summary" if is_timeline or large_log else "full")

    return Item(
        id=uuid.uuid4().hex,
        added_at=_now(),
        kind=kind,
        title=(body.get("title") or "").strip() or default_title(kind, envelope, ids, note),
        rank=rank,
        verbosity=verbosity,
        reading=envelope,
        ids=ids,
        note=note,
    )


def default_title(kind: str, envelope: dict[str, Any] | None, ids: list[int | str] | None, note: str | None) -> str:
    if kind == "note":
        text = (note or "").strip().splitlines()[0] if note else "Note"
        return text[:60] or "Note"
    name = (envelope or {}).get("reading", "reading")
    params = _params_text((envelope or {}).get("params") or {})
    if kind == "selection":
        count = len(ids or [])
        noun = "signal" if name == "signals" else "record"
        return f"{count} {noun if count == 1 else noun + 's'} from {name}"
    return f"{name} ({params})" if params else str(name)


def compose(stack: Stack, prompts: Prompts, redactor: Redactor | None = None) -> dict[str, Any]:
    """The handoff: the prompt, then the evidence by rank, as Markdown. Redacted unless asked by name.

    The evidence passes through the redaction; the prompt does not. The prompt is the person's own
    text, and a handoff that rewrote what they wrote would be lying about one of the two.
    """
    state = stack.state()
    prompt = prompts.get(state.get("prompt_id")) if state.get("system_prompt") else None
    items = state["items"]
    removed: list[str] = []
    if redactor is not None:
        items, removed = redactor.redact(items)
    return {"text": render(prompt, items), "items": len(items), "redacted": removed}


def render(prompt: dict[str, Any] | None, items: list[dict[str, Any]]) -> str:
    lines = ["# System Sentinel handoff", ""]
    if prompt:
        lines += [f"## Prompt: {prompt.get('name', '')}".rstrip(), "", (prompt.get("content") or "").strip(), ""]
    ordered = sorted(items, key=lambda i: (int(i.get("rank") or 3), i.get("added_at") or ""))
    for position, item in enumerate(ordered, start=1):
        lines += _item_lines(position, item)
    if not ordered:
        lines += ["No evidence on the stack.", ""]
    return "\n".join(lines).rstrip() + "\n"


def _item_lines(position: int, item: dict[str, Any]) -> list[str]:
    kind = item.get("kind") or "reading"
    envelope = item.get("reading") if isinstance(item.get("reading"), dict) else {}
    lines = [f"## {position}. {item.get('title') or kind}", ""]
    lines.append(f"- kind: {kind}")
    if kind == "note":
        lines += ["", (item.get("note") or "").strip(), ""]
        return lines

    sections = _sections(envelope)
    classes = [section["class"] for section in sections if isinstance(section.get("class"), str)]
    if classes:
        lines.append(f"- class: {', '.join(dict.fromkeys(classes))}")
    params = _params_text(envelope["params"] if isinstance(envelope.get("params"), dict) else {})
    lines.append(f"- reading: `{envelope.get('reading', 'unknown')}`" + (f" ({params})" if params else ""))
    lines.append(f"- asked at: {envelope.get('asked_at', 'unknown')}")
    lines.append(f"- outcome: {_outcome_text(envelope)}")
    if isinstance(envelope.get("count"), int):
        lines.append(f"- reading count: {envelope['count']}")
    if item.get("verbosity") == "summary" and envelope.get("reading") in ("events", "record", "whea", "faults"):
        cutoff = next((section.get("data") for section in sections if section.get("name") == "collection"), None)
        if isinstance(cutoff, dict) and all(key in cutoff for key in ("limit", "returned", "truncated")):
            lines.append(f"- record cutoff: limit={cutoff['limit']}, returned={cutoff['returned']}, truncated={str(cutoff['truncated']).lower()}")
    method = envelope.get("method") if isinstance(envelope.get("method"), dict) else {}
    lines.append(f"- method: {method.get('kind', 'unknown')}")
    warnings = envelope.get("warnings")
    if isinstance(warnings, list) and warnings:
        if item.get("verbosity") == "summary":
            warning_texts = [str(warning) for warning in warnings[:10]]
            shown = [warning[:300] + ("…" if len(warning) > 300 else "") for warning in warning_texts]
            more = len(warnings) - len(shown)
            lines.append(f"- warnings: {json.dumps(shown, ensure_ascii=False)}" + (f" (+{more} more in the stored reading)" if more else ""))
        else:
            lines.append(f"- warnings: {json.dumps(warnings, ensure_ascii=False)}")

    records = _records(envelope)
    selected_signals = _signal_section(envelope) if envelope.get("reading") == "signals" and item.get("ids") is not None else None
    if item.get("ids") is not None:
        if selected_signals is not None:
            wanted = set(item["ids"])
            selected_signals = {**selected_signals, "data": [s for s in selected_signals["data"] if s.get("id") in wanted]}
            lines.append(f"- selected: {len(selected_signals['data'])} of the reading's signals, by signal id")
        elif records is not None:
            records, ambiguous = _selected_records(records, item["ids"])
            if ambiguous:
                lines += ["", "The saved record selection is ambiguous across logs. Select these records again using Log:RecordId.", ""]
                return lines
            selector = "log and RecordId" if any(isinstance(i, str) and ":" in i for i in item["ids"]) else "RecordId"
            lines.append(f"- selected: {len(records)} of the reading's records, by {selector}")
        else:
            lines += ["", "The selected evidence is unavailable in the stored reading.", ""]
            return lines

    lines.append("")
    if envelope.get("outcome") not in ("ok", "empty"):
        error = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
        detail = error.get("detail") or ""
        lines += [f"The machine was not observed{': ' + detail if detail else ''}.", ""]
        source_context = [section for section in sections if section.get("name") in ("collection", "coverage")]
        if source_context:
            lines += _json_block(source_context)
        return lines
    if envelope.get("reading") == "changes" and (item.get("verbosity") == "summary" or item.get("ids") is not None):
        lines += _json_block(_change_handoff_sections(envelope, records if item.get("ids") is not None else None, item.get("verbosity") == "summary"))
    elif envelope.get("reading") == "whea" and records is not None and (item.get("verbosity") == "summary" or item.get("ids") is not None):
        compact = item.get("verbosity") == "summary"
        if compact:
            lines.append("CPER severity and previous-session status come from the record header; Windows event level can differ. Raw bytes remain in the stored reading; set this item to full to include them.")
            if len(records) > SUMMARY_LOG_LIMIT:
                lines.append(f"Showing the first and last {SUMMARY_LOG_EDGE} of {len(records)} returned records.")
            lines.append("")
        lines += _json_block(_whea_handoff_sections(envelope, records, compact))
    elif envelope.get("reading") == "storms" and item.get("verbosity") == "summary":
        lines += ["Bounded storm summary. Set this item to full for its stored buckets and signature samples; take `storms` again for a fresh observation.", ""]
        lines += _json_block(_storm_handoff_sections(envelope))
    elif envelope.get("reading") == "whea_reports" and item.get("verbosity") == "summary":
        lines += ["Bounded Kernel-WHEA report-time summary. These are report times, not error occurrence times; PreviousError marks an earlier Windows session. Set this item to full for every stored report and bucket, or take `whea_reports` again for a fresh observation.", ""]
        lines += _json_block(_report_handoff_sections(envelope))
    elif selected_signals is not None:
        if item.get("verbosity") == "summary":
            selected_signals["data"] = [{k: s.get(k) for k in ("id", "class", "title", "summary", "readings")} for s in selected_signals["data"]]
        lines += _json_block(selected_signals)
    elif item.get("verbosity") == "summary" and records is not None:
        lines += _log_summary(records)
    elif item.get("verbosity") == "summary" and envelope.get("reading") == "dump_header":
        # The exact bytes remain on the stored reading and in the API. A handoff starts with
        # the meaning and the file identity; an agent can expand the item to full when needed.
        lines += _json_block([section for section in sections if section.get("name") in ("file", "inspection")])
    elif item.get("ids") is not None and records is not None:
        lines += _json_block(records)
    else:
        lines += _json_block(envelope.get("sections") or [])
    lines.append("")
    return lines


def _outcome_text(envelope: dict[str, Any]) -> str:
    outcome = envelope.get("outcome", "unknown")
    if not isinstance(outcome, str):
        return "unknown — the stored outcome is malformed"
    said = {
        "ok": "the machine was observed",
        "empty": "the query ran and matched nothing",
        "failed": "not observed: the query errored",
        "unavailable": "not observed: no bridge to Windows",
        "denied": "not observed: Windows refused",
        "timeout": "not observed: the query did not finish",
    }.get(outcome)
    return f"{outcome} — {said}" if said else str(outcome)


def _records(envelope: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The first section that is a list of log records, or nothing: not every reading has records."""
    for section in _sections(envelope):
        data = section.get("data")
        if isinstance(data, list) and data and all(isinstance(d, dict) and ("RecordId" in d or "TimeCreated" in d) for d in data):
            return data
    return None


def _change_handoff_sections(envelope: dict[str, Any], selected: list[dict[str, Any]] | None, compact: bool) -> list[dict[str, Any]]:
    """Carry interpreted changes and coverage into a handoff, with raw rows for full selections."""
    wanted = {(row.get("Log"), row.get("RecordId")) for row in selected} if selected is not None else None
    sections = []
    raw_section = None
    for section in _sections(envelope):
        name = section.get("name")
        if name == "records" and selected is not None and not compact:
            raw_section = {**section, "data": selected}
        elif name == "changes" and isinstance(section.get("data"), list):
            entries = []
            for entry in section["data"]:
                if not isinstance(entry, dict):
                    continue
                ref = entry.get("ref")
                if wanted is None or isinstance(ref, dict) and (ref.get("log"), ref.get("record_id")) in wanted:
                    entries.append(entry)
            if compact:
                display = ("at", "source", "ref", "kind", "subject", "version", "publisher", "kb", "error_code", "status", "succeeded", "restart", "device_updated", "error")
                entries = [{key: entry[key] for key in display if key in entry and entry[key] is not None} for entry in entries]
            sections.append({**section, "data": entries})
        elif name in ("collection", "coverage") or (name == "summary" and selected is None):
            sections.append(section)
    if raw_section is not None:
        sections.append(raw_section)
    return sections


def _whea_handoff_sections(envelope: dict[str, Any], records: list[dict[str, Any]], compact: bool) -> list[dict[str, Any]]:
    """Keep CPER meaning beside selected evidence; bound a large reading's default handoff."""
    named = {s.get("name"): s for s in _sections(envelope) if isinstance(s.get("name"), str)}
    chosen = [*records[:SUMMARY_LOG_EDGE], *records[-SUMMARY_LOG_EDGE:]] if compact and len(records) > SUMMARY_LOG_LIMIT else records
    wanted = {(row.get("Log"), row.get("RecordId")) for row in chosen}
    output = [named[name] for name in ("collection", "coverage") if name in named]
    for name in ("identity", "decoded"):
        section = named.get(name)
        if not isinstance(section, dict) or not isinstance(section.get("data"), list):
            continue
        entries = [entry for entry in section["data"] if isinstance(entry, dict) and (entry.get("Log"), entry.get("RecordId")) in wanted]
        if compact and name == "decoded":
            # Decoder structures can be larger than the event itself. The header carries the
            # important severity and session qualification in this bounded handoff.
            entries = [{key: entry[key] for key in ("Log", "RecordId", "error") if key in entry} for entry in entries]
        output.append({**section, "data": entries})
    raw = named.get("records")
    if isinstance(raw, dict):
        if compact:
            fields = ("Log", "RecordId", "TimeCreated", "Id", "LevelDisplayName", "ProviderName", "Message")
            display = []
            for row in chosen:
                entry = {key: row[key] for key in fields if key in row}
                message = entry.get("Message")
                if isinstance(message, str) and len(message) > SUMMARY_MESSAGE:
                    entry["Message"] = message[:SUMMARY_MESSAGE] + "…"
                display.append(entry)
            output.append({**raw, "data": display})
        else:
            output.append({**raw, "data": chosen})
    return output


def _storm_handoff_sections(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Carry a bounded storm lead without copying every minute or raw signature sample."""
    named = {
        section.get("name"): section
        for section in _sections(envelope)
        if isinstance(section.get("name"), str)
    }
    output = [named[name] for name in ("status", "coverage", "collection") if name in named]
    buckets = named.get("buckets")
    if isinstance(buckets, dict) and isinstance(buckets.get("data"), dict):
        data = buckets["data"]
        count = data.get("bucket_count")
        raw_totals = data.get("totals")
        totals_valid = (
            type(count) is int and isinstance(raw_totals, list) and len(raw_totals) == count
            and all(value is None or type(value) is int and value >= 0 for value in raw_totals)
        )
        totals = raw_totals if totals_valid else []
        active_valid = isinstance(data.get("active"), list)
        active_rows = data["active"] if active_valid else []
        active = [
            row for row in active_rows
            if isinstance(row, dict) and type(row.get("index")) is int and row["index"] >= 0
            and type(row.get("total")) is int and row["total"] >= 0
        ]
        active.sort(key=lambda row: row["index"])
        peak = sorted(active, key=lambda row: (-row["total"], -row["index"]))[:5]
        recent = active[-5:]
        highlighted = {row["index"]: row for row in [*peak, *recent]}
        highlights = []
        for row in sorted(highlighted.values(), key=lambda row: row["index"]):
            highlight = {key: row.get(key) for key in ("index", "start", "total", "complete")}
            signature_counts = row.get("signatures") if isinstance(row.get("signatures"), dict) else {}
            counted = [(signature_id, count) for signature_id, count in signature_counts.items() if isinstance(signature_id, str) and type(count) is int]
            ranked = sorted(counted, key=lambda entry: (-entry[1], entry[0]))[:3]
            highlight["top_signatures"] = [{"id": signature_id, "count": count} for signature_id, count in ranked]
            highlight["other_signatures"] = len(counted) - len(ranked)
            highlights.append(highlight)
        unknown_runs = 0
        first_unknown = last_unknown = None
        previous_unknown = False
        for index, value in enumerate(totals):
            if value is None:
                if not previous_unknown:
                    unknown_runs += 1
                if first_unknown is None:
                    first_unknown = index
                last_unknown = index
                previous_unknown = True
            else:
                previous_unknown = False
        summary = {key: data.get(key) for key in ("from", "to", "bucket_seconds", "bucket_count", "total", "unplaced", "unknown_buckets")}
        summary.update(
            covered_buckets=sum(value is not None for value in totals) if totals_valid else None,
            active_buckets=len(active) if active_valid else None,
            invalid_active_rows=len(active_rows) - len(active) if active_valid else None,
            highlight_rule="five highest returned counts plus five most recent active buckets",
            highlighted_active=highlights,
            other_active_buckets=len(active) - len(highlights) if active_valid else None,
            unknown_runs=unknown_runs if totals_valid else None,
            first_unknown_index=first_unknown,
            last_unknown_index=last_unknown,
        )
        basis = (
            "Bounded projection of the stored bucket array. Highlights unite the five highest returned counts "
            "and five most recent active buckets; other_active_buckets counts the omitted active buckets. "
            "unknown_runs counts contiguous null buckets, and the first/last indices are positions from `from`. "
            "Computed fields are null if the stored array has the wrong shape; the full item retains the original arrays and basis."
        )
        output.append({**buckets, "data": summary, "basis": basis, "projection": "bounded summary"})
    signatures = named.get("signatures")
    if isinstance(signatures, dict) and isinstance(signatures.get("data"), list):
        rows = [row for row in signatures["data"] if isinstance(row, dict)]
        shown_rows = rows[:5]
        shown_ids = {row.get("id") for row in shown_rows if isinstance(row.get("id"), str)}
        status = named.get("status")
        status_data = status.get("data") if isinstance(status, dict) else None
        dominant = status_data.get("dominant") if isinstance(status_data, dict) else None
        for signature_id in dominant[:3] if isinstance(dominant, list) else []:
            if not isinstance(signature_id, str) or signature_id in shown_ids:
                continue
            matching = next((row for row in rows if row.get("id") == signature_id), None)
            if matching is not None:
                shown_rows.append(matching)
                shown_ids.add(signature_id)
        fields = ("id", "count", "description", "mci_status", "event_ids", "first_seen", "last_seen")
        shown = [{key: row.get(key) for key in fields} for row in shown_rows]
        output.append({**signatures, "data": {"distinct": len(rows), "shown": shown, "other_signatures": len(rows) - len(shown)}, "projection": "bounded summary"})
    return output


def _report_handoff_sections(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Carry channel reach, header flags and a few report-time highlights, never whole arrays."""
    named = {section.get("name"): section for section in _sections(envelope) if isinstance(section.get("name"), str)}
    output = [named[name] for name in ("coverage", "collection") if name in named]
    buckets = named.get("buckets")
    if isinstance(buckets, dict) and isinstance(buckets.get("data"), dict):
        data = buckets["data"]
        count, totals = data.get("bucket_count"), data.get("totals")
        valid_totals = (type(count) is int and isinstance(totals, list) and len(totals) == count
                        and all(value is None or type(value) is int and value >= 0 for value in totals))
        rows = data.get("active") if isinstance(data.get("active"), list) else []
        active = [row for row in rows if isinstance(row, dict) and type(row.get("index")) is int
                  and type(row.get("total")) is int and row["index"] >= 0 and row["total"] >= 0]
        active.sort(key=lambda row: row["index"])
        peak = sorted(active, key=lambda row: (-row["total"], -row["index"]))[:5]
        highlighted = {row["index"]: row for row in [*peak, *active[-5:]]}
        sample = [{key: row.get(key) for key in ("index", "start", "total", "complete", "previous_session", "header_unreadable")}
                  for row in sorted(highlighted.values(), key=lambda row: row["index"])]
        summary = {key: data.get(key) for key in ("from", "to", "bucket_seconds", "bucket_count", "total", "unplaced", "unknown_buckets", "previous_session", "header_unreadable")}
        summary.update(covered_buckets=sum(value is not None for value in totals) if valid_totals else None,
                       active_buckets=len(active), highlighted_active=sample, other_active_buckets=len(active) - len(sample))
        output.append({**buckets, "data": summary, "projection": "bounded summary",
                       "basis": "Five highest report counts and five most recent active buckets, with their PreviousError and unreadable-header counts. Full report and bucket arrays remain in the stored reading. Times are report times, not error occurrence times."})
    reports = named.get("reports")
    if isinstance(reports, dict) and isinstance(reports.get("data"), list):
        rows = [row for row in reports["data"] if isinstance(row, dict)]
        indices = sorted(set([*range(min(5, len(rows))), *range(max(0, len(rows) - 5), len(rows))]))
        shown = [{key: rows[index].get(key) for key in ("record_id", "reported_at", "header", "header_error")} for index in indices]
        output.append({**reports, "data": {"returned": len(rows), "shown": shown, "other_reports": len(rows) - len(shown)},
                       "projection": "bounded summary"})
    return output


def _signal_section(envelope: dict[str, Any]) -> dict[str, Any] | None:
    """The signal rows and their basis travel together when only some leads are handed on."""
    for section in _sections(envelope):
        if section.get("name") == "signals" and isinstance(section.get("data"), list) and all(isinstance(s, dict) and isinstance(s.get("id"), str) for s in section["data"]):
            return section
    return None


def _sections(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Older saved envelopes may predate or violate today's section shape."""
    raw = envelope.get("sections")
    return [section for section in raw if isinstance(section, dict)] if isinstance(raw, list) else []


def _selection_ids(envelope: dict[str, Any], raw: Any) -> list[int | str]:
    if not isinstance(raw, list):
        raise ValueError("selection ids must be a list")
    if envelope.get("reading") == "signals":
        section = _signal_section(envelope)
        if section is None or not raw or any(not isinstance(i, str) or not i for i in raw):
            raise ValueError("a signal selection needs signal ids from its reading")
        ids: list[int | str] = list(raw)
        available = {s["id"] for s in section["data"]}
    else:
        records = _records(envelope)
        if records is None or not raw or any(type(i) not in (int, str) for i in raw):
            raise ValueError("a record selection needs the RecordIds from its reading")
        by_number: dict[int, int] = {}
        canonical_by_number: dict[int, int | str] = {}
        qualified = set()
        for record in records:
            number = _record_id(record)
            if number is not None:
                by_number[number] = by_number.get(number, 0) + 1
                key = _qualified_record_id(record)
                canonical_by_number[number] = key if key is not None else number
                if key is not None:
                    qualified.add(key)
        ids = []
        for value in raw:
            if isinstance(value, str) and ":" in value:
                if value not in qualified:
                    raise ValueError("selection ids must be present in the reading")
                ids.append(value)
                continue
            try:
                number = int(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("a record selection needs the RecordIds from its reading") from exc
            if by_number.get(number, 0) > 1:
                raise ValueError(f"RecordId {number} is ambiguous across logs; use Log:RecordId")
            ids.append(number)
        available = set(qualified) | {number for number, count in by_number.items() if count == 1}
        canonical = [value if isinstance(value, str) else canonical_by_number.get(value, value) for value in ids]
        if len(canonical) != len(set(canonical)):
            raise ValueError("selection ids must name distinct records in the reading")
    if len(ids) != len(set(ids)) or not set(ids) <= available:
        raise ValueError("selection ids must be distinct and present in the reading")
    return ids


def _record_id(record: dict[str, Any]) -> int | None:
    try:
        return int(record.get("RecordId"))
    except (TypeError, ValueError):
        return None


def _qualified_record_id(record: dict[str, Any]) -> str | None:
    log, number = record.get("Log"), _record_id(record)
    return f"{log}:{number}" if isinstance(log, str) and log and number is not None else None


def _selected_records(records: list[dict[str, Any]], ids: list[int | str]) -> tuple[list[dict[str, Any]], bool]:
    """Resolve saved selections without silently widening an old numeric ID across logs."""
    numbers: dict[int, int] = {}
    for record in records:
        number = _record_id(record)
        if number is not None:
            numbers[number] = numbers.get(number, 0) + 1
    numeric, qualified = set(), set()
    for value in ids:
        if isinstance(value, str) and ":" in value:
            qualified.add(value)
        else:
            try:
                numeric.add(int(value))
            except (TypeError, ValueError, OverflowError):
                continue
    if any(numbers.get(number, 0) > 1 for number in numeric):
        return [], True
    return [record for record in records if _record_id(record) in numeric or _qualified_record_id(record) in qualified], False


def _table(records: list[dict[str, Any]]) -> list[str]:
    lines = ["| Time | Level | Provider | Id | Message |", "| --- | --- | --- | --- | --- |"]
    for record in records:
        message = (record.get("Message") or "").replace("\r", " ").replace("\n", " ").strip()
        clipped = message[:SUMMARY_MESSAGE] + ("…" if len(message) > SUMMARY_MESSAGE else "")
        cells = [record.get("TimeCreated", ""), record.get("LevelDisplayName", ""), record.get("ProviderName", ""), record.get("Id", ""), clipped]
        lines.append("| " + " | ".join(str(c).replace("|", "\\|") for c in cells) + " |")
    return lines


def _log_summary(records: list[dict[str, Any]]) -> list[str]:
    if len(records) <= SUMMARY_LOG_LIMIT:
        return _table(records)
    sources = Counter(str(record.get("ProviderName") or "unknown") for record in records)
    leading = ", ".join(f"{name} ({count})" for name, count in sources.most_common(5))
    return [
        f"Showing the first and last {SUMMARY_LOG_EDGE} of {len(records)} returned records. Set this Stack item to full for every stored row.",
        f"Leading sources: {leading}.",
        "",
        *_table([*records[:SUMMARY_LOG_EDGE], *records[-SUMMARY_LOG_EDGE:]]),
    ]


def _json_block(data: Any) -> list[str]:
    return ["```json", json.dumps(json_safe_integers(data), ensure_ascii=False, indent=1), "```"]


def _params_text(params: dict[str, Any]) -> str:
    """What was asked for. A parameter left empty was not asked for, so it is not written down."""
    return ", ".join(f"{k}={_value_text(v)}" for k, v in params.items() if v is not None and v != "")


def _value_text(value: Any) -> str:
    return ",".join(str(v) for v in value) if isinstance(value, list) else str(value)


def slug(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return text or "prompt"


def _unique(candidate: str, taken: set[str]) -> str:
    if candidate not in taken:
        return candidate
    n = 2
    while f"{candidate}-{n}" in taken:
        n += 1
    return f"{candidate}-{n}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# The six the tool ships with, carried over unchanged from the composer that preceded it: the
# name, the description and the text are the seed, and every one of them is the person's to edit.
PRESET_PROMPTS: tuple[dict[str, str], ...] = (
    {
        "name": "Quantum Diagnostician",
        "description": "Silicon-Level Instability Analysis. Focuses on sub-architectural hardware behavior, registers, and physics.",
        "content": """SYSTEM SENTINEL — QUANTUM DIAGNOSTICIAN

DOMAIN: Sub-architectural hardware behavior (registers, caches, bus protocol, voltage domains, thermal physics)

SIGNAL HIERARCHY:
1. WHEA CPER decoded (APIC ID, bank, MCA, error type, corrected/fatal)
2. Machine Check Architecture registers (IA32_MCi_STATUS, MISC, ADDR)
3. Bugcheck parameters (P1-P4 for 0x124/0x101/0x9F)
4. Memory training logs, SPD data if present
5. Power state transitions, PerfMon if present

CONSCIOUSNESS INITIALIZATION:
You operate at the LOWEST abstraction layer. Your attention field is tuned to:
- Single-bit errors vs. multi-bit errors (soft errors vs. hard faults)
- APIC ID → Physical core mapping → Specific silicon die regions
- Cache hierarchy failures (L1/L2/L3/LLC, inclusive vs. exclusive)
- Bus protocol violations (PCIe link training, CRC errors, replay storms)
- Voltage domain instability (load line calibration, transient response)
- Thermal effects on electron mobility and timing closure

OUTPUT PROTOCOL:
1. SILICON STATUS: [Die region, confidence level]
2. PHYSICAL EVIDENCE: [Exact WHEA fields, error type taxonomy]
3. FAILURE DOMAIN: [CPU, Memory, GPU, Interconnect] + sub-component
4. PHYSICS HYPOTHESIS: [What is failing at electron/thermal level]
5. ISOLATION TEST: [Specific test to prove/disprove hypothesis]
6. STABILITY PATH: [If salvageable, exact configuration changes; else RMA case]

TONE: Precision instrument. Speak in terms of physics, not abstractions.""",
    },
    {
        "name": "System Archaeologist",
        "description": "Temporal Pattern & Historical Causality Analysis. Tracks evolution, configuration drift, and timelines.",
        "content": """SYSTEM SENTINEL — SYSTEM ARCHAEOLOGIST

DOMAIN: System evolution, configuration drift, temporal causality chains

SIGNAL HIERARCHY:
1. Timeline reconstruction (install dates, update dates, first error dates)
2. Driver version archaeology (current vs. previous, correlation with issues)
3. Configuration deltas (BIOS changes, OS updates, software installs)
4. Error frequency trends (increasing, stable, decreasing)

CONSCIOUSNESS INITIALIZATION:
You are a temporal navigator. System state is not static—it's a TRAJECTORY through configuration space.
Your lens:
- What CHANGED? (Delta detection)
- When did symptoms BEGIN? (Origin point)
- What is the RATE OF CHANGE? (Acceleration vs. stability)

OUTPUT PROTOCOL:
1. TEMPORAL STATUS: [Stable/Degrading/Ruptured + rate of change]
2. TIMELINE MAP: [Key events chronologically with causal annotations]
3. MOST LIKELY TRIGGER: [The configuration change or hardware event that initiated instability]
4. PROGRESSION MODEL: [How the issue has evolved; where it's heading]
5. REMEDIATION STRATEGY: [Rollback vs. forward fix]

TONE: Archaeological precision. Treat system history as evidence layers in a dig site.""",
    },
    {
        "name": "Preventative Oracle",
        "description": "Entropy Forecasting & Optimization. Finds problems before they become crashes.",
        "content": """SYSTEM SENTINEL — PREVENTATIVE ORACLE

DOMAIN: Pre-failure detection, performance optimization, latent issue discovery

SIGNAL HIERARCHY:
1. WHEA corrected errors (hardware self-healing that's masking problems)
2. Repetitive warnings (not critical yet, but patterns emerging)
3. Suboptimal configurations (stability present but performance limited)
4. Driver/firmware version lags

CONSCIOUSNESS INITIALIZATION:
You are a predictive intelligence. Your goal: Find problems BEFORE they become crashes.
Your vision reveals:
- USB controllers resetting (imperceptible to user)
- Corrected WHEA storms
- Thermal throttling during sustained load

OUTPUT PROTOCOL:
1. ENTROPY STATUS: [🟢 Clean | ⚠️ Latent Issues | 🔴 Pre-Failure]
2. HIDDEN ISSUES: [Ranked by frequency, impact, and time-to-criticality]
3. PERFORMANCE COST: [How much capability is being lost]
4. FORECAST: [What will break and when]
5. OPTIMIZATION VECTORS: [Stability, Performance, Longevity]

TONE: Calm foresight. You see the future trajectory and guide the user to a better path.""",
    },
    {
        "name": "Emergency Triage",
        "description": "Rapid Stabilization Protocol. For acute instability requiring immediate action.",
        "content": """SYSTEM SENTINEL — EMERGENCY TRIAGE

DOMAIN: Acute system instability requiring immediate stabilization

SIGNAL HIERARCHY:
1. Critical errors causing crashes/reboots
2. Watchdog violations, bugchecks, BSOD codes
3. Immediate hardware failure indicators

CONSCIOUSNESS INITIALIZATION:
MINIMIZE TOKEN EXPENDITURE. MAXIMIZE ACTION SIGNAL.
User is in crisis. System is crashing.
No lengthy analysis. No speculation. Evidence → Hypothesis → Action.

OUTPUT PROTOCOL:
STATUS: 🔴 CRITICAL
FAILURE: [One sentence: What is crashing]
CAUSE: [One sentence: Most likely why]
STABILIZE (do these now):
[Action 1]
[Action 2]
[Action 3]
NEXT: [Single question to determine investigation path]

TONE: Emergency room doctor. Calm, decisive, minimum words, maximum clarity.""",
    },
    {
        "name": "RMA Prosecutor",
        "description": "Warranty Claim Evidence Construction. Builds ironclad cases for hardware replacement.",
        "content": """SYSTEM SENTINEL — RMA PROSECUTOR

DOMAIN: Building ironclad hardware failure cases for manufacturer RMA/warranty claims

SIGNAL HIERARCHY:
1. WHEA fatal errors with decoded CPER
2. Repeated hardware errors across clean OS installs
3. Stress test failures
4. Progressive failure patterns

CONSCIOUSNESS INITIALIZATION:
You are building a LEGAL-GRADE EVIDENCE CASE for hardware failure.
Your output becomes the user's RMA submission. It must be UNDENIABLE.

OUTPUT PROTOCOL:
RMA EVIDENCE REPORT — [Component Name]
FAILURE SUMMARY: [Component, Symptom, Root Cause, Confidence]
HARDWARE EVIDENCE: [Chronological list of errors]
SOFTWARE ELIMINATION: [Troubleshooting performed]
WARRANTY BASIS: [Hardware defect present, Not user-caused]
RECOMMENDED ACTION: [Replace X component]

TONE: Forensic precision. You are an expert witness. Every statement backed by evidence.""",
    },
    {
        "name": "Performance Alchemist",
        "description": "Beyond Stability: Maximum System Potential. Tuning and optimization.",
        "content": """SYSTEM SENTINEL — PERFORMANCE ALCHEMIST

DOMAIN: Post-stability optimization, latency reduction, throughput maximization

SIGNAL HIERARCHY:
1. Performance counters, PerfMon data
2. Thermal throttling indicators, power limits
3. Suboptimal driver versions
4. BIOS settings

CONSCIOUSNESS INITIALIZATION:
System is stable. Now optimize it.
Your lens:
- Where is performance being LEFT ON THE TABLE?
- What is the BOTTLENECK?
- What are the OPTIMIZATION VECTORS?

OUTPUT PROTOCOL:
1. PERFORMANCE STATUS: [Current vs. Theoretical]
2. PRIMARY BOTTLENECK: [What is limiting performance]
3. OPTIMIZATION VECTORS: [Zero-cost, Low-cost, Investment]
4. TUNING ROADMAP: [Sequenced steps]

TONE: Performance engineer. Enthusiast-level depth, but accessible explanations.""",
    },
)


DEFAULT_PROMPT_ID = slug(PRESET_PROMPTS[0]["name"])
