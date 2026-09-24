"""The stack: the evidence chosen for handoff, the prompt library, and the text they compose into.

The stack lives on the server, in the data directory, so the desktop, the phone and the agent
see one stack; the composed handoff is a route an agent reads without a clipboard. Every
operation reads the file, changes it and writes it back under a lock, so there is no in-memory
copy to drift from what is on disk and a second process sees what the first wrote.

An item keeps the reading's envelope as it was at the moment of adding: its ``asked_at``,
``outcome`` and ``method`` are the item's provenance, and the composed text states them, so a
reading that failed cannot enter a handoff disguised as a finding. The same observation and
selection are refused rather than stacked twice; a later reading is a new observation.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .bridge import Bridge
from .paths import data_dir
from .performance import locked
from .reading import REGISTRY, ReadingCall, take
from .redact import Redactor
from .serialization import json_safe_integers

KINDS = ("reading", "selection", "note")
VERBOSITIES = ("summary", "full")
RANKS = (1, 2, 3, 4, 5)
SUMMARY_MESSAGE = 80
SUMMARY_WARNING_TEXT = 200
"""How much of a record's message a summary table carries."""
SUMMARY_LOG_LIMIT = 100
SUMMARY_LOG_EDGE = 5
SUMMARY_CONTEXT_TEXT = 250
SUMMARY_FAULT_GROUPS = 10
SUMMARY_FAULT_MODULES = 5
SUMMARY_CRASH_ISSUES = 10
SUMMARY_CRASH_STOPS = 20  # Today's crash.MAX_STOPS; excess saved stops get an explicit omitted count.
LOG_READINGS = ("events", "record")
STOP_REF_LOGS = {"start": "System", "power_41": "System", "eventlog_6008": "System", "wer_1001": "System"}


class Duplicate(Exception):
    """This observation and selection are already on the stack."""

    def __init__(self, item_id: str, asked_at: str | None):
        super().__init__(item_id)
        self.asked_at = asked_at


class StoreUnavailable(Exception):
    """Saved Stack or prompt data could not be read or changed without risking its contents."""


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
        """One observation and selection; notes are written, so never duplicates."""
        if self.kind == "note" or not self.reading:
            return None
        return (
            self.reading.get("reading"),
            json.dumps(self.reading.get("params"), sort_keys=True),
            _observation_instant(self.reading.get("asked_at")),
            _canonical_ids(self.reading, self.ids),
        )


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


def index_entry(item: dict[str, Any]) -> dict[str, Any]:
    """Describe a stored item without copying its evidence into routine answers."""
    reading = item.get("reading")
    provenance = None
    if item.get("kind") != "note":
        fields = reading if isinstance(reading, dict) else {}
        provenance = {
            "reading": fields.get("reading") if isinstance(fields.get("reading"), str) else None,
            "params": fields.get("params") if isinstance(fields.get("params"), dict) else None,
            "asked_at": fields.get("asked_at") if isinstance(fields.get("asked_at"), str) else None,
            "outcome": fields.get("outcome") if isinstance(fields.get("outcome"), str) else None,
            "count": fields.get("count") if type(fields.get("count")) is int and fields["count"] >= 0 else None,
        }
    return {key: item.get(key) for key in ("id", "added_at", "kind", "title", "rank", "verbosity", "ids", "note")} | {"provenance": provenance}


def index_state(state: dict[str, Any]) -> dict[str, Any]:
    """Keep prompt choice and item provenance; full stored readings remain in Stack.state."""
    return {"items": [index_entry(item) for item in state["items"]],
            "prompt_id": state["prompt_id"], "system_prompt": state["system_prompt"]}


class Store:
    """One JSON document, atomically replaced under a cross-process mutation lock."""

    def __init__(self, path: Path, empty: dict[str, Any]):
        self.path = path
        self._empty = empty
        self._lock = threading.Lock()

    def read(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if not self.path.parent.is_dir():
                raise StoreUnavailable(f"{self.path.name} directory is unavailable; no new file was written") from None
            return json.loads(json.dumps(self._empty))
        except (OSError, UnicodeError) as exc:
            raise StoreUnavailable(f"{self.path.name} could not be read; its file was left intact") from exc
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StoreUnavailable(f"{self.path.name} is not valid JSON; its file was left intact") from exc
        if not isinstance(loaded, dict):
            raise StoreUnavailable(f"{self.path.name} has an invalid shape; its file was left intact")
        return loaded

    def write(self, state: dict[str, Any]) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=1)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
        except OSError as exc:
            raise StoreUnavailable(f"{self.path.name} could not be written; inspect it before retrying") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # A closed process can leave an owner-only scratch file; the saved file is authoritative.

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            if not self.path.parent.is_dir():
                raise StoreUnavailable(f"{self.path.name} directory is unavailable; no new file was written")
            try:
                with locked(self.path.with_name(self.path.name + ".lock")):
                    yield
            except StoreUnavailable:
                raise
            except (OSError, TimeoutError) as exc:
                raise StoreUnavailable(f"{self.path.name} lock is unavailable; inspect the saved file before retrying") from exc


class Stack:
    """The chosen evidence, its order, and which prompt leads the handoff."""

    def __init__(self, path: Path | None = None):
        self.store = Store(path or data_dir() / "stack.json", {"items": [], "prompt_id": DEFAULT_PROMPT_ID, "system_prompt": True})

    def state(self) -> dict[str, Any]:
        with self.store.transaction():
            return self._state_locked()

    def item(self, item_id: str) -> dict[str, Any]:
        """An exact saved item, without rewriting the Stack or changing its handoff."""
        with self.store.transaction():
            for item in self._state_locked()["items"]:
                if item["id"] == item_id:
                    return item
        raise KeyError(item_id)

    def _state_locked(self) -> dict[str, Any]:
        """Read and validate while the caller owns the Stack mutation lock."""
        raw = self.store.read()
        saved = raw.get("items")
        if (
            not isinstance(saved, list)
            or not isinstance(raw.get("system_prompt", True), bool)
            or raw.get("prompt_id") is not None and not isinstance(raw["prompt_id"], str)
        ):
            raise StoreUnavailable("stack.json has an invalid shape; the file was left intact")
        try:
            items = [item_from_dict(i).to_dict() for i in saved]
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreUnavailable("stack.json contains a malformed item; the file was left intact") from exc
        if any(
            not isinstance(item["id"], str) or not isinstance(item["added_at"], str)
            or item["kind"] not in KINDS
            or not isinstance(item["title"], str)
            or item["reading"] is not None and not isinstance(item["reading"], dict)
            or item["ids"] is not None and not isinstance(item["ids"], list)
            or item["note"] is not None and not isinstance(item["note"], str)
            for item in items
        ):
            raise StoreUnavailable("stack.json contains a malformed item; the file was left intact")
        return {"items": items, "prompt_id": raw.get("prompt_id"), "system_prompt": bool(raw.get("system_prompt", True))}

    def add(self, item: Item) -> Item:
        with self.store.transaction():
            state = self._state_locked()
            if item.signature is not None:
                for existing in state["items"]:
                    if item_from_dict(existing).signature == item.signature:
                        raise Duplicate(existing["id"], existing.get("reading", {}).get("asked_at"))
            state["items"].append(item.to_dict())
            self.store.write(state)
        return item

    def update(self, item_id: str, *, rank: int | None = None, verbosity: str | None = None, title: str | None = None) -> dict[str, Any]:
        if rank is not None and (type(rank) is not int or rank not in RANKS):
            raise ValueError(f"rank must be one of {list(RANKS)}")
        if verbosity is not None and verbosity not in VERBOSITIES:
            raise ValueError(f"verbosity must be one of {list(VERBOSITIES)}")
        if title is not None and not isinstance(title, str):
            raise ValueError("title must be text")
        with self.store.transaction():
            state = self._state_locked()
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
        with self.store.transaction():
            state = self._state_locked()
            kept = [i for i in state["items"] if i["id"] != item_id]
            if len(kept) == len(state["items"]):
                raise KeyError(item_id)
            state["items"] = kept
            self.store.write(state)

    def clear(self) -> None:
        with self.store.transaction():
            state = self._state_locked()
            state["items"] = []
            self.store.write(state)

    def choose(self, *, prompt_id: str | None = None, system_prompt: bool | None = None, set_prompt: bool = False) -> dict[str, Any]:
        """Change which prompt leads the handoff, or whether one does at all."""
        if set_prompt and prompt_id is not None and not isinstance(prompt_id, str):
            raise ValueError("prompt_id must be text or null")
        if system_prompt is not None and type(system_prompt) is not bool:
            raise ValueError("system_prompt must be true or false")
        with self.store.transaction():
            state = self._state_locked()
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
        with self.store.transaction():
            return self._all_locked()

    def _all_locked(self) -> list[dict[str, Any]]:
        """Read or seed the library while the caller owns its mutation lock."""
        state = self.store.read()
        if not self.store.path.exists():
            state = {"prompts": [{"id": slug(p["name"]), "builtin": True, **p} for p in PRESET_PROMPTS], "seeded": True}
            self.store.write(state)
        prompts = state.get("prompts")
        if not isinstance(prompts, list) or any(
            not isinstance(p, dict) or not isinstance(p.get("id"), str)
            or not isinstance(p.get("name"), str) or not isinstance(p.get("content"), str)
            for p in prompts
        ):
            raise StoreUnavailable("prompts.json has an invalid shape; the file was left intact")
        return prompts

    def get(self, prompt_id: str | None) -> dict[str, Any] | None:
        if not prompt_id:
            return None
        return next((p for p in self.all() if p["id"] == prompt_id), None)

    def add(self, name: str, description: str = "", content: str = "") -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("a prompt needs a name")
        if not isinstance(description, str) or not isinstance(content, str):
            raise ValueError("prompt description and content must be text")
        with self.store.transaction():
            prompts = self._all_locked()
            prompt = {"id": _unique(slug(name), {p["id"] for p in prompts}), "name": name, "description": description, "content": content, "builtin": False}
            prompts.append(prompt)
            self.store.write({"prompts": prompts, "seeded": True})
        return prompt

    def update(self, prompt_id: str, **fields: Any) -> dict[str, Any]:
        if any(value is not None and not isinstance(value, str) for key, value in fields.items() if key in ("name", "description", "content")):
            raise ValueError("prompt fields must be text")
        with self.store.transaction():
            prompts = self._all_locked()
            for prompt in prompts:
                if prompt["id"] == prompt_id:
                    for key in ("name", "description", "content"):
                        if fields.get(key) is not None:
                            prompt[key] = fields[key]
                    self.store.write({"prompts": prompts, "seeded": True})
                    return prompt
        raise KeyError(prompt_id)

    def remove(self, prompt_id: str) -> None:
        with self.store.transaction():
            prompts = self._all_locked()
            kept = [p for p in prompts if p["id"] != prompt_id]
            if len(kept) == len(prompts):
                raise KeyError(prompt_id)
            self.store.write({"prompts": kept, "seeded": True})


async def new_item(stack: Stack, bridge: Bridge, body: dict[str, Any], *, reader: ReadingCall | None = None) -> Item:
    """Turn what a client sent into an item: take the reading now, or keep the envelope it holds.

    Refuses what cannot be evidence — a selection without matching ids, a note without text, a
    reading without either a ``take`` or an ``envelope`` — before anything is stored.
    """
    kind = body.get("kind") or "reading"
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {list(KINDS)}")
    rank = body.get("rank") if body.get("rank") is not None else 3
    if type(rank) is not int:
        raise ValueError(f"rank must be one of {list(RANKS)}")
    if rank not in RANKS:
        raise ValueError(f"rank must be one of {list(RANKS)}")
    title = body.get("title")
    if title is not None and not isinstance(title, str):
        raise ValueError("title must be text")
    requested_verbosity = body.get("verbosity")
    if requested_verbosity is not None and requested_verbosity not in VERBOSITIES:
        raise ValueError(f"verbosity must be one of {list(VERBOSITIES)}")

    envelope: dict[str, Any] | None = None
    ids: list[int | str] | None = None
    note: str | None = None

    if kind == "note":
        supplied_note = body.get("note") or ""
        if not isinstance(supplied_note, str):
            raise ValueError("a note needs text")
        note = supplied_note.strip()
        if not note:
            raise ValueError("a note needs text")
    else:
        asked = body.get("take")
        given = body.get("envelope")
        if bool(asked) == bool(given):
            raise ValueError("send either 'take' (the server reads the machine now) or 'envelope' (a reading you hold)")
        if asked:
            if not isinstance(asked, dict):
                raise ValueError("'take' must name a reading and its parameters")
            name = asked.get("name")
            if not isinstance(name, str) or name not in REGISTRY:
                raise ValueError(f"no reading named {name!r}")
            params = asked.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError("'take' parameters must be an object")
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
                and _observation_instant(envelope.get("asked_at")) is not None
                and valid_sections and (envelope.get("error") is None or isinstance(envelope["error"], dict))
            )
            if not valid_envelope:
                raise ValueError("'envelope' must have the reading, asked_at with a timezone, outcome, params, method and section shapes returned by the API")
        if _observation_instant(envelope.get("asked_at")) is None:
            raise ValueError("a stacked reading needs an observed-at time with a timezone")
        if kind == "selection":
            ids = _selection_ids(envelope, body.get("ids") or [])

    large_log = bool(kind == "reading" and envelope and envelope.get("reading") in (*LOG_READINGS, "whea", "whea_window") and len(_records(envelope) or []) > SUMMARY_LOG_LIMIT)
    derived_summary = bool(kind == "reading" and envelope and envelope.get("reading") in ("storms", "whea_reports", "crash", "faults"))
    verbosity = requested_verbosity or ("summary" if derived_summary or large_log else "full")

    return Item(
        id=uuid.uuid4().hex,
        added_at=_now(),
        kind=kind,
        title=(title or "").strip() or default_title(kind, envelope, ids, note),
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
    if item.get("verbosity") == "summary" and envelope.get("reading") in (*LOG_READINGS, "whea", "whea_window", "faults"):
        cutoff = next((section.get("data") for section in sections if section.get("name") == "collection"), None)
        if isinstance(cutoff, dict) and all(key in cutoff for key in ("limit", "returned", "truncated")):
            truncated = cutoff["truncated"]
            state = str(truncated).lower() if isinstance(truncated, bool) else "unknown (query stopped early)" if cutoff.get("stopped") else "unknown"
            lines.append(f"- record cutoff: limit={cutoff['limit']}, returned={cutoff['returned']}, truncated={state}")
    method = envelope.get("method") if isinstance(envelope.get("method"), dict) else {}
    lines.append(f"- method: {method.get('kind', 'unknown')}")
    warnings = envelope.get("warnings")
    if isinstance(warnings, list) and warnings:
        if item.get("verbosity") == "summary":
            warning_texts = [str(warning) for warning in warnings[:10]]
            shown = [warning[:SUMMARY_WARNING_TEXT] + ("…" if len(warning) > SUMMARY_WARNING_TEXT else "") for warning in warning_texts]
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
            records, ambiguous = _selected_records(records, item["ids"], _known_log(envelope))
            if ambiguous:
                lines += ["", "The saved record selection is ambiguous across logs. Select these records again using Log:RecordId.", ""]
                return lines
            if not records:
                lines += ["", "The saved record selection resolves to no returned raw records in this reading. Select records again from a current reading.", ""]
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
    if envelope.get("reading") in ("crash", "faults") and (item.get("verbosity") == "summary" or item.get("ids") is not None):
        compact = item.get("verbosity") == "summary"
        if envelope.get("reading") == "crash":
            lines += _crash_handoff(envelope, records if item.get("ids") is not None else None, compact)
        else:
            lines += _fault_handoff(envelope, records if item.get("ids") is not None else None, compact)
    elif envelope.get("reading") == "changes" and (item.get("verbosity") == "summary" or item.get("ids") is not None):
        lines += _json_block(_change_handoff_sections(envelope, records if item.get("ids") is not None else None, item.get("verbosity") == "summary"))
    elif envelope.get("reading") in ("whea", "whea_window", "whea_record") and records is not None and (item.get("verbosity") == "summary" or item.get("ids") is not None):
        compact = item.get("verbosity") == "summary"
        if compact:
            lines.append("CPER severity and previous-session status come from the record header; Windows event level can differ. Set this item to full for its stored fields. Default redaction may withhold CPER bytes.")
            if envelope.get("reading") in ("whea", "whea_window") and records and isinstance(records[0], dict) and "RawData" not in records[0]:
                lines.append("This stored WHEA list is a bounded preview. Full verbosity expands only the stored preview; take whea_record with a selected source and RecordId for exact fields and decoded detail, then compare TimeCreated.")
            if len(records) > SUMMARY_LOG_LIMIT:
                lines.append(f"Showing the first and last {SUMMARY_LOG_EDGE} of {len(records)} returned records.")
            lines.append("")
        lines += _json_block(_whea_handoff_sections(envelope, records, compact))
    elif envelope.get("reading") in LOG_READINGS and records is not None and (item.get("verbosity") == "summary" or item.get("ids") is not None):
        lines += _log_handoff(envelope, records, selected=item.get("ids") is not None, compact=item.get("verbosity") == "summary")
    elif envelope.get("reading") == "storms" and item.get("verbosity") == "summary":
        lines += ["Bounded storm summary. Full shows stored buckets and signatures; `storms(references=true)` in a narrow window returns report references.", ""]
        params = envelope.get("params")
        if isinstance(params, dict) and isinstance(params.get("before"), str) and params["before"].strip():
            lines += ["Historical System WHEA-Logger filing-time window; `collection.window_end` is its actual exclusive end. No live burst, acceleration or quiet status is inferred by design. Check `buckets.previous_session` and `buckets.header_unreadable` when present; reports filed after restart may describe earlier errors.", ""]
        lines += _json_block(_storm_handoff_sections(envelope))
    elif envelope.get("reading") == "whea_reports" and item.get("verbosity") == "summary":
        lines += ["Bounded Kernel-WHEA report-time summary. These are report times, not error occurrence times; PreviousError marks an earlier Windows session. Set this item to full for stored buckets and any requested references; take `whea_window` for a selected interval or `whea_reports` with references=true for every returned reference.", ""]
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
        if item.get("verbosity") == "summary" and envelope.get("reading") not in LOG_READINGS:
            lines += ["At summary verbosity this observation carries its full stored sections.", ""]
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
                display = ("at", "source", "ref", "kind", "subject", "version", "publisher", "kb", "error_code", "status", "succeeded", "restart", "device_updated", "outside_window", "error")
                compact_entries = []
                for entry in entries:
                    shown = {key: entry[key] for key in display if key in entry and entry[key] is not None}
                    # False is the ordinary case; a true or unknown placement matters in a handoff.
                    if entry.get("outside_window") is False:
                        shown.pop("outside_window", None)
                    elif "outside_window" in entry and entry["outside_window"] is None:
                        shown["outside_window"] = None
                    compact_entries.append(shown)
                entries = compact_entries
            sections.append({**section, "data": entries})
        elif name in ("collection", "coverage") or (name == "summary" and selected is None):
            sections.append(section)
    if raw_section is not None:
        sections.append(raw_section)
    return sections


def _named_sections(envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {section["name"]: section for section in _sections(envelope) if isinstance(section.get("name"), str)}


def _clip_context(value: Any) -> Any:
    """Bound free text in a compact projection without changing the stored reading or nulls."""
    if isinstance(value, str):
        return value[:SUMMARY_CONTEXT_TEXT] + ("…" if len(value) > SUMMARY_CONTEXT_TEXT else "")
    if isinstance(value, list):
        return [_clip_context(item) for item in value]
    if isinstance(value, dict):
        return {key: _clip_context(item) for key, item in value.items()}
    return value


def _source_context(named: dict[str, dict[str, Any]], compact: bool) -> list[dict[str, Any]]:
    return [
        {**named[name], "data": _clip_context(named[name].get("data"))} if compact else named[name]
        for name in ("collection", "coverage") if name in named
    ]


def _known_log(envelope: dict[str, Any]) -> str | None:
    """A single-log reading can identify its rows even when older records omit Log."""
    if envelope.get("reading") not in LOG_READINGS:
        return None
    collection = _named_sections(envelope).get("collection", {}).get("data")
    log = collection.get("log") if isinstance(collection, dict) else None
    return log if log in ("System", "Application") else None


def _log_handoff(envelope: dict[str, Any], records: list[dict[str, Any]], *, selected: bool, compact: bool) -> list[str]:
    """Keep log reach beside bounded rows or an exact selected record."""
    named = _named_sections(envelope)
    lines: list[str] = []
    for name in ("collection", "coverage"):
        if name not in named:
            lines.append(f"This stored reading has no {name} section; its {'source outcome' if name == 'collection' else 'retention reach'} is unknown.")
    context = _source_context(named, compact)
    if selected and compact and context:
        lines += ["", *_json_block(context)]
    if compact:
        if selected:
            lines.append("")
        if envelope.get("reading") == "record":
            order = "oldest first; every row is strictly before the requested moment"
        else:
            collection = named.get("collection", {}).get("data")
            order = "oldest first" if isinstance(collection, dict) and collection.get("order") == "oldest" else "newest first"
        lines += [f"Returned rows are {order}.", "", *_log_summary(records, fallback_log=_known_log(envelope))]
        if not selected and context:
            lines += ["", *_json_block(context)]
    elif selected:
        raw = named.get("records")
        selected_rows = {**raw, "data": records, "projection": "selected raw records"} if raw else {"name": "records", "class": "raw", "data": records, "projection": "selected raw records"}
        lines += ["", *_json_block([*context, selected_rows])]
    return lines


def _source_log(row: dict[str, Any], reading: str) -> str | None:
    log = row.get("Log")
    if isinstance(log, str) and log:
        return log
    if reading == "faults":
        return "Application"  # The faults collector has only this source, including older saved rows.
    if reading == "crash":
        provider = row.get("ProviderName")
        if provider == "Windows Error Reporting":
            return "Application"  # The crash collector's BlueScreen report query.
        if isinstance(provider, str) and provider:
            return "System"  # All other records in the crash collector came from its System query.
    return None


def _row_refs(rows: list[dict[str, Any]], reading: str) -> tuple[set[tuple[str, int]], bool]:
    refs: set[tuple[str, int]] = set()
    unresolved = False
    for row in rows:
        number, log = _record_id(row), _source_log(row, reading)
        if number is None or log is None:
            unresolved = True
        else:
            refs.add((log, number))
    return refs, unresolved


def _crash_decoded_ref(entry: dict[str, Any], index: int, rows: list[dict[str, Any]], count: int) -> tuple[str, int] | None:
    number = _record_id(entry)
    log = entry.get("Log")
    if number is None:
        return None
    if isinstance(log, str) and log:
        return log, number
    # The crash decoder is one-for-one with raw rows. Older saved envelopes can lack `Log` in
    # both arrays; align them only when count and RecordId still agree.
    if len(rows) == count and index < len(rows) and _record_id(rows[index]) == number:
        inferred = _source_log(rows[index], "crash")
        return (inferred, number) if inferred is not None else None
    return None


def _stop_refs(stop: dict[str, Any]) -> set[tuple[str, int]]:
    records = stop.get("records")
    if not isinstance(records, dict):
        return set()
    refs = set()
    for key, log in STOP_REF_LOGS.items():
        number = _record_id({"RecordId": records.get(key)})
        if number is not None:
            refs.add((log, number))
    reports = records.get("report")
    if isinstance(reports, list):
        for value in reports:
            number = _record_id({"RecordId": value})
            if number is not None:
                refs.add(("Application", number))
    return refs


def _crash_handoff(envelope: dict[str, Any], selected: list[dict[str, Any]] | None, compact: bool) -> list[str]:
    """Carry composed stops and their source limits beside exact selected records."""
    named = _named_sections(envelope)
    source = named.get("stops")
    raw_stops = source.get("data") if source else None
    valid_stops = isinstance(raw_stops, list) and all(isinstance(stop, dict) for stop in raw_stops)
    stops = raw_stops if valid_stops else []
    wanted, unresolved = _row_refs(selected, "crash") if selected is not None else (None, False)
    matched = [stop for stop in stops if wanted is None or _stop_refs(stop) & wanted]
    lines = [
        "Composed stops, with their source coverage. Stopped at is Windows' stop estimate; started at is the next start, announced at is the Kernel-Power record, and reported at is when WER filed a report. A report time alone does not establish stop time."
        if selected is None else f"Composed stops referencing the selected records: {len(matched) if valid_stops else 'unknown'}. Their stop, start, announcement and report times are distinct. A report time alone does not establish stop time."
    ]
    if selected is not None and unresolved:
        lines.append("Some selected record sources could not be identified; their stop association is unknown.")
    if selected is not None and valid_stops and not matched and not unresolved:
        lines.append("No composed stop in the stored reading references these records.")
    lines.append("Set this item to full for every stored raw and decoded record." if compact and selected is None else "")
    lines.append("")

    sections: list[dict[str, Any]] = []
    if source is not None:
        if not valid_stops:
            lines.append("The stored composed stop section is malformed; its stop count is unknown.")
            sections.append({**source, "data": {"available": False, "reason": "invalid stored stop shape"}, "projection": "unavailable"})
        elif compact:
            shown = matched[:SUMMARY_CRASH_STOPS]
            sections.append({**source, "data": {"returned": len(matched), "shown": _clip_context(shown), "omitted": len(matched) - len(shown)}, "projection": "bounded summary"})
        else:
            sections.append({**source, "data": matched, "projection": "selected stops"})
    else:
        lines.append("The stored reading has no composed stop section.")

    decoded_section = named.get("decoded")
    decoded = decoded_section.get("data") if decoded_section else None
    raw_rows = named.get("records", {}).get("data")
    valid_raw_rows = isinstance(raw_rows, list) and all(isinstance(row, dict) for row in raw_rows)
    all_rows = raw_rows if valid_raw_rows else []
    if compact:
        if isinstance(decoded, list) and all(isinstance(entry, dict) for entry in decoded):
            issues = []
            unscoped = 0
            for index, entry in enumerate(decoded):
                if not entry.get("error"):
                    continue
                ref = _crash_decoded_ref(entry, index, all_rows, len(decoded))
                if wanted is not None and ref is None:
                    unscoped += 1
                    continue
                if wanted is not None and ref not in wanted:
                    continue
                issues.append({"Log": ref[0] if ref else entry.get("Log"), "RecordId": entry.get("RecordId"), "kind": entry.get("kind"), "error": entry["error"]})
            shown = issues[:SUMMARY_CRASH_ISSUES]
            complete = not unresolved and unscoped == 0 and valid_raw_rows and len(decoded) == len(all_rows)
            issue_data = {"available": complete, "returned": len(issues) if complete else None, "shown": _clip_context(shown), "omitted": len(issues) - len(shown) if complete else None, "unscoped_errors": unscoped}
        else:
            issue_data = {"available": False, "reason": "the stored decoded section is missing or malformed", "returned": None, "shown": None, "omitted": None}
        sections.append({"name": "decode_issues", "class": "derived", "basis": "Decoder errors in the stored crash reading, scoped to the selection when there is one; zero requires an available decoded section and identifiable selected sources.", "data": issue_data})
    sections += _source_context(named, compact)

    if selected is not None and not compact:
        if isinstance(decoded, list) and decoded_section is not None:
            matching = [entry for index, entry in enumerate(decoded) if isinstance(entry, dict) and _crash_decoded_ref(entry, index, all_rows, len(decoded)) in wanted]
            sections.append({**decoded_section, "data": matching, "projection": "selected decoded records"})
        raw = named.get("records")
        if raw is not None:
            sections.append({**raw, "data": selected, "projection": "selected raw records"})
    lines += _json_block(sections)
    if selected is not None and compact:
        lines += ["", "Selected raw records, at log record time:", "", *_log_summary(selected)]
    return lines


def _fault_display(entry: dict[str, Any], times: dict[tuple[str, int], Any]) -> dict[str, Any]:
    fields = entry.get("fields") if isinstance(entry.get("fields"), dict) else {}
    process = entry.get("process") if isinstance(entry.get("process"), dict) else None
    report = entry.get("report") if isinstance(entry.get("report"), dict) else None
    number = _record_id(entry)
    log = str(entry.get("Log") or "Application")
    return _clip_context({
        "ref": f"{log}:{number}" if number is not None else None,
        "recorded_at": times.get((log, number)) if number is not None else None,
        "kind": entry.get("kind"),
        "application": fields.get("AppName") or fields.get("ExeFileName"),
        "application_version": fields.get("AppVersion"),
        "module": fields.get("ModuleName"),
        "module_version": fields.get("ModuleVersion"),
        "faulting_offset": fields.get("FaultingOffset"),
        "hang_type": fields.get("HangType"),
        "exception": entry.get("exception"),
        "raw_exception_code": fields.get("ExceptionCode"),
        "process": {key: process.get(key) for key in ("id", "id_status", "id_reason", "created_at", "creation_status", "creation_reason", "warnings")} if process else None,
        "report": {key: report.get(key) for key in ("id", "code", "name", "parameters", "bucket", "dump_path", "records")} if report else None,
        "error": entry.get("error"),
    })


def _fault_summary(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"available": False, "reason": "the stored derived summary has an invalid shape"}
    applications = data.get("applications")
    apps_valid = isinstance(applications, list)
    apps = [app for app in applications if isinstance(app, dict)] if apps_valid else []
    bad_apps = len(applications) - len(apps) if apps_valid else None
    count_valid = apps_valid and all(type(app.get("count")) is int and app["count"] >= 0 for app in apps)
    shown_apps = []
    for app in apps[:SUMMARY_FAULT_GROUPS]:
        modules = app.get("modules")
        names = modules if isinstance(modules, list) else []
        shown_apps.append({**app, "modules": names[:SUMMARY_FAULT_MODULES], "other_modules": max(0, len(names) - SUMMARY_FAULT_MODULES)})
    live = data.get("live_kernel")
    reports_valid = isinstance(live, list)
    reports = [row for row in live if isinstance(row, dict)] if reports_valid else []
    bad_reports = len(live) - len(reports) if reports_valid else None
    return _clip_context({
        "available": isinstance(data.get("by_kind"), dict) and apps_valid and reports_valid and bad_apps == 0 and bad_reports == 0 and count_valid,
        "by_kind": data.get("by_kind") if isinstance(data.get("by_kind"), dict) else None,
        "applications": shown_apps if apps_valid else None,
        "other_applications": max(0, len(apps) - len(shown_apps)) if apps_valid else None,
        "other_application_entries": sum(app["count"] for app in apps[len(shown_apps):]) if count_valid else None,
        "invalid_application_rows": bad_apps,
        "live_kernel": reports[:SUMMARY_FAULT_GROUPS] if reports_valid else None,
        "other_live_kernel": max(0, len(reports) - SUMMARY_FAULT_GROUPS) if reports_valid else None,
        "invalid_live_kernel_rows": bad_reports,
    })


def _fault_handoff(envelope: dict[str, Any], selected: list[dict[str, Any]] | None, compact: bool) -> list[str]:
    """Carry interpreted fault kinds, process validity and source reach with the selected raw rows."""
    named = _named_sections(envelope)
    decoded_section = named.get("decoded")
    raw_decoded = decoded_section.get("data") if decoded_section else None
    valid_decoded = isinstance(raw_decoded, list) and all(isinstance(entry, dict) for entry in raw_decoded)
    decoded = raw_decoded if valid_decoded else []
    raw_rows = named.get("records", {}).get("data")
    rows = [row for row in raw_rows if isinstance(row, dict)] if isinstance(raw_rows, list) else []
    times = {(str(row.get("Log") or "Application"), number): row.get("TimeCreated") for row in rows if (number := _record_id(row)) is not None}
    wanted, unresolved = _row_refs(selected, "faults") if selected is not None else (None, False)

    def belongs(entry: dict[str, Any]) -> bool:
        if wanted is None:
            return True
        log, number = str(entry.get("Log") or "Application"), _record_id(entry)
        if number is not None and (log, number) in wanted:
            return True
        report = entry.get("report")
        references = report.get("records") if isinstance(report, dict) else None
        return isinstance(references, list) and any((log, _record_id({"RecordId": ref})) in wanted for ref in references)

    matching = [entry for entry in decoded if belongs(entry)]
    lines = [
        "Derived faults from returned Application records. Times are log record times; a live kernel report may be filed after its underlying event. One live kernel entry can represent several records."
        if selected is None else f"Derived fault entries referencing the selected records: {len(matching) if valid_decoded else 'unknown'}. Times are log record times, which may lag the underlying event."
    ]
    if selected is not None and unresolved:
        lines.append("Some selected record IDs could not be read; their fault association is unknown.")
    if selected is not None and valid_decoded and not matching and not unresolved:
        lines.append("No decoded fault entry in the stored reading references these records.")
    if compact and selected is None:
        lines.append("Set this item to full for every stored raw and decoded record.")
    lines.append("")

    sections: list[dict[str, Any]] = []
    if compact and selected is None:
        summary = named.get("summary")
        if summary is not None:
            sections.append({**summary, "data": _fault_summary(summary.get("data")), "projection": "bounded summary; counts describe decoded entries from returned records"})
        else:
            lines.append("The stored reading has no derived fault summary section; grouped counts are unknown.")
    if decoded_section is not None:
        if not valid_decoded:
            lines.append("The stored decoded fault section is malformed; its entry count is unknown.")
            sections.append({**decoded_section, "data": {"available": False, "reason": "invalid stored decoded shape"}, "projection": "unavailable"})
        elif compact:
            chosen = matching if len(matching) <= 2 * SUMMARY_LOG_EDGE else [*matching[:SUMMARY_LOG_EDGE], *matching[-SUMMARY_LOG_EDGE:]]
            sections.append({**decoded_section, "data": {"entries": len(matching), "shown": [_fault_display(entry, times) for entry in chosen], "omitted_entries": len(matching) - len(chosen)}, "projection": "bounded interpreted entries"})
        else:
            sections.append({**decoded_section, "data": matching, "projection": "selected decoded entries"})
    else:
        lines.append("The stored reading has no decoded fault section.")
    sections += _source_context(named, compact)
    if selected is not None and not compact:
        raw = named.get("records")
        if raw is not None:
            sections.append({**raw, "data": selected, "projection": "selected raw records"})
    lines += _json_block(sections)
    if selected is not None and compact:
        lines += ["", "Selected raw records, at log record time:", "", *_log_summary(selected)]
    return lines


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
            fields = ("Log", "RecordId", "TimeCreated", "Id", "Level", "LevelDisplayName", "ProviderName", "Message", "MessageChars", "PayloadBytes")
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


def _bucket_rows(data: dict[str, Any], signatures: list[Any] | None = None) -> tuple[list[dict[str, Any]], bool]:
    """Read both stored object buckets and current sparse columns for bounded handoffs."""
    old = data.get("active")
    if isinstance(old, list):
        return ([row for row in old if isinstance(row, dict) and type(row.get("index")) is int
                 and row["index"] >= 0 and type(row.get("total")) is int and row["total"] >= 0], True)
    columns = data.get("returned")
    totals = data.get("totals")
    bucket_count = data.get("bucket_count")
    if (not isinstance(columns, dict) or not isinstance(totals, list)
            or type(bucket_count) is not int or len(totals) != bucket_count
            or any(value is not None and (type(value) is not int or value < 0) for value in totals)):
        return [], False
    indices, counts = columns.get("index"), columns.get("count")
    previous, unreadable = columns.get("previous_session"), columns.get("header_unreadable")
    if not all(isinstance(array, list) for array in (indices, counts, previous, unreadable)):
        return [], False
    assert isinstance(indices, list) and isinstance(counts, list)
    assert isinstance(previous, list) and isinstance(unreadable, list)
    if not (len(indices) == len(counts) == len(previous) == len(unreadable)):
        return [], False
    start, seconds = data.get("from"), data.get("bucket_seconds")
    if not isinstance(start, str) or type(seconds) is not int or seconds < 1:
        return [], False
    try:
        origin = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if origin.utcoffset() != timedelta(0):
            return [], False
        rows: list[dict[str, Any]] = []
        for index, count, prior, missing in zip(indices, counts, previous, unreadable, strict=True):
            if (type(index) is not int or not 0 <= index < len(totals) or rows and index <= rows[-1]["index"]
                    or any(type(value) is not int or value < 0 for value in (count, prior, missing))
                    or count == 0 or prior + missing > count
                    or totals[index] is not None and totals[index] != count):
                return [], False
            rows.append({"index": index, "start": (origin + timedelta(seconds=index * seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                         "total": count, "complete": totals[index] is not None,
                         "previous_session": prior, "header_unreadable": missing})
    except (ValueError, OverflowError):
        return [], False
    pairs = data.get("signature_pairs")
    if signatures is not None and pairs is None:
        return [], False
    if pairs is not None:
        if not isinstance(pairs, dict) or signatures is None:
            return [], False
        pair_indices, pair_signatures, pair_counts = pairs.get("index"), pairs.get("signature"), pairs.get("count")
        if not all(isinstance(array, list) for array in (pair_indices, pair_signatures, pair_counts)):
            return [], False
        assert isinstance(pair_indices, list) and isinstance(pair_signatures, list) and isinstance(pair_counts, list)
        if not (len(pair_indices) == len(pair_signatures) == len(pair_counts)):
            return [], False
        by_index = {row["index"]: row for row in rows}
        pair_totals = {row["index"]: 0 for row in rows}
        for index, sig_index, count in zip(pair_indices, pair_signatures, pair_counts, strict=True):
            if (type(index) is not int or index not in by_index or type(sig_index) is not int
                    or not 0 <= sig_index < len(signatures) or type(count) is not int or count < 1
                    or not isinstance(signatures[sig_index], dict) or not isinstance(signatures[sig_index].get("id"), str)):
                return [], False
            signature_counts = by_index[index].setdefault("signatures", {})
            sig_id = signatures[sig_index]["id"]
            if sig_id in signature_counts:
                return [], False
            signature_counts[sig_id] = count
            pair_totals[index] += count
        if any(pair_totals[row["index"]] != row["total"] for row in rows):
            return [], False
    return rows, True


def _storm_handoff_sections(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Carry a bounded storm lead without copying every minute or raw signature sample."""
    named = {
        section.get("name"): section
        for section in _sections(envelope)
        if isinstance(section.get("name"), str)
    }
    references = named.get("reports")
    has_references = isinstance(references, dict) and isinstance(references.get("data"), list)
    output = [named[name] for name in ("status", "coverage", "collection") if name in named]
    if output and output[0].get("name") == "status":
        status_data = output[0].get("data")
        header_lead = ("The fixed-header not_marked_burst can be unknown; an unmarked report does not date an error. "
                       if isinstance(status_data, dict) and "not_marked_burst" in status_data else
                       "This saved reading predates the fixed-header lead. ")
        output[0] = {**output[0], "basis": (
            "Live System report traffic: a threshold bucket is a burst; acceleration and quiet need coverage. "
            "The current bucket ends at query time. Quiet does not clear Kernel-WHEA/Errors. "
            + header_lead +
            "Full rule is stored."), "projection": "bounded summary"}
    for index, section in enumerate(output):
        if section.get("name") == "coverage":
            output[index] = {**section, "basis": (
                "System-log reach: covered_from excludes older unknown time; covered_until is exclusive. "
                "Complete needs an answered uncapped query and pre-window history in an enabled circular log; future time is incomplete."),
                "projection": "bounded summary"}
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
        signature_data = named.get("signatures")
        signature_rows = signature_data.get("data") if isinstance(signature_data, dict) else None
        active, active_valid = _bucket_rows(data, signature_rows if isinstance(signature_rows, list) else None)
        active_rows = data.get("active") if isinstance(data.get("active"), list) else active
        active.sort(key=lambda row: row["index"])
        peak = sorted(active, key=lambda row: (-row["total"], -row["index"]))[:5]
        recent = active[-5:]
        highlighted = {row["index"]: row for row in [*peak, *recent]}
        highlights = []
        for row in sorted(highlighted.values(), key=lambda row: row["index"]):
            highlight = {key: row.get(key) for key in ("index", "start", "total", "complete", "previous_session", "header_unreadable")}
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
        summary = {key: data.get(key) for key in ("from", "to", "bucket_seconds", "bucket_count", "total", "unplaced", "unknown_buckets", "previous_session", "header_unreadable", "header_unreadable_reasons")}
        if "severity" in data:
            summary["severity"] = data["severity"]
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
            "Five highest and five newest returned buckets; other_active_buckets counts omissions. "
            "Null totals mark unknown coverage; malformed totals leave coverage summaries null. "
            + ("Header totals include unplaced reports. PreviousError does not date an error. "
               if type(data.get("previous_session")) is int else "Older saved reading: header counts unavailable. ")
            + "Full evidence is in the stored item."
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
        fields = ("id", "count", "previous_session", "header_unreadable", "description", "mci_status", "event_ids", "first_seen", "last_seen")
        shown = []
        for index, row in enumerate(shown_rows):
            entry = {key: row.get(key) for key in fields}
            sample = row.get("sample")
            if index < 3 and isinstance(sample, dict) and type(sample.get("RecordId")) is int and sample["RecordId"] > 0 and isinstance(sample.get("TimeCreated"), str):
                entry["sample_ref"] = {"record_id": sample["RecordId"], "time_created": sample["TimeCreated"],
                                       "previous_session": sample.get("previous_session")}
            shown.append(entry)
        output.append({**signatures, "data": {"distinct": len(rows), "shown": shown, "other_signatures": len(rows) - len(shown)},
                       "basis": "Counts cover placed reports; first/last are filing times, not error occurrence. Up to three sample_ref values identify System records for whea_record. Missing flags stay unknown.",
                       "projection": "bounded summary"})
    if has_references:
        source_rows = references["data"]
        rows = [row for row in source_rows if isinstance(row, dict)]
        output.append({**references, "data": {"returned": len(source_rows), "shown": [],
                                               "other_reports": len(rows),
                                               "invalid_rows": len(source_rows) - len(rows)},
                       "basis": "All per-report references are omitted here; signature sample_ref values above remain. Full references are in the stored item.",
                       "projection": "bounded summary"})
    elif isinstance(references, dict):
        output.append({"name": "reports", "class": "derived", "data": {"available": False, "reason": "saved report references could not be read"},
                       "basis": "A reports section exists in this saved reading, but its data is not a list. Signature samples above remain available.",
                       "projection": "bounded summary"})
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
        active, active_valid = _bucket_rows(data)
        active.sort(key=lambda row: row["index"])
        peak = sorted(active, key=lambda row: (-row["total"], -row["index"]))[:5]
        highlighted = {row["index"]: row for row in [*peak, *active[-5:]]}
        sample = [{key: row.get(key) for key in ("index", "start", "total", "complete", "previous_session", "header_unreadable")}
                  for row in sorted(highlighted.values(), key=lambda row: row["index"])]
        summary = {key: data.get(key) for key in ("from", "to", "bucket_seconds", "bucket_count", "total", "unplaced", "unknown_buckets", "previous_session", "header_unreadable")}
        if "severity" in data:
            summary["severity"] = data["severity"]
        summary.update(covered_buckets=sum(value is not None for value in totals) if valid_totals else None,
                       active_buckets=len(active) if active_valid else None, highlighted_active=sample,
                       other_active_buckets=len(active) - len(sample) if active_valid else None)
        output.append({**buckets, "data": summary, "projection": "bounded summary",
                       "basis": "Five highest report counts and five most recent active buckets, with their PreviousError and unreadable-header counts. Full bucket columns and any requested report references remain in the stored reading. Times are report times, not error occurrence times."})
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


def _observation_instant(value: Any) -> datetime | None:
    """Normalize the envelope's time, including equivalent UTC offsets."""
    if not isinstance(value, str) or not (value.endswith("Z") or re.search(r"[+-]\d{2}:\d{2}$", value)):
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return at.astimezone(UTC) if at.tzinfo is not None else None
    except ValueError:
        return None


def _canonical_ids(envelope: dict[str, Any], ids: list[int | str] | None) -> tuple[str, ...]:
    """Numeric and Log:RecordId forms of one selected row have the same identity."""
    if not ids:
        return ()
    logs: dict[int, set[str]] = {}
    fallback_log = _known_log(envelope)
    for row in _records(envelope) or []:
        number, log = _record_id(row), row.get("Log") or fallback_log
        if number is not None and isinstance(log, str) and log:
            logs.setdefault(number, set()).add(log)
    canonical = []
    for value in ids:
        if isinstance(value, str) and ":" in value:
            canonical.append(value)
            continue
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            canonical.append(str(value))  # An older malformed selection must not break the stack.
            continue
        matching = logs.get(number, set())
        canonical.append(f"{next(iter(matching))}:{number}" if len(matching) == 1 else str(number))
    return tuple(sorted(canonical))


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
        fallback_log = _known_log(envelope)
        for record in records:
            number = _record_id(record)
            if number is not None:
                by_number[number] = by_number.get(number, 0) + 1
                key = _qualified_record_id(record, fallback_log)
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


def _qualified_record_id(record: dict[str, Any], fallback_log: str | None = None) -> str | None:
    log, number = record.get("Log") or fallback_log, _record_id(record)
    return f"{log}:{number}" if isinstance(log, str) and log and number is not None else None


def _selected_records(records: list[dict[str, Any]], ids: list[int | str], fallback_log: str | None = None) -> tuple[list[dict[str, Any]], bool]:
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
    return [record for record in records if _record_id(record) in numeric or _qualified_record_id(record, fallback_log) in qualified], False


def _table(records: list[dict[str, Any]], fallback_log: str | None = None) -> list[str]:
    lines = ["| Time | Level | Provider | Id | Record | Message |", "| --- | --- | --- | --- | --- | --- |"]
    for record in records:
        message = (record.get("Message") or "").replace("\r", " ").replace("\n", " ").strip()
        clipped = message[:SUMMARY_MESSAGE] + ("…" if len(message) > SUMMARY_MESSAGE else "")
        reference = _qualified_record_id(record, fallback_log)
        if reference is None:
            number = _record_id(record)
            reference = str(number) if number is not None else ""
        cells = [record.get("TimeCreated", ""), record.get("LevelDisplayName", ""), record.get("ProviderName", ""), record.get("Id", ""), reference, clipped]
        lines.append("| " + " | ".join(str(c).replace("|", "\\|") for c in cells) + " |")
    return lines


def _log_summary(records: list[dict[str, Any]], *, fallback_log: str | None = None) -> list[str]:
    if len(records) <= SUMMARY_LOG_LIMIT:
        return _table(records, fallback_log)
    sources = Counter(str(record.get("ProviderName") or "unknown") for record in records)
    leading = ", ".join(f"{name} ({count})" for name, count in sources.most_common(5))
    return [
        f"Showing the first and last {SUMMARY_LOG_EDGE} of {len(records)} returned records. Set this Stack item to full for every stored row.",
        f"Leading sources: {leading}.",
        "",
        *_table([*records[:SUMMARY_LOG_EDGE], *records[-SUMMARY_LOG_EDGE:]], fallback_log),
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
