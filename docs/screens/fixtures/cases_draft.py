"""A draft of the case routes that interface direction C asks of the API, served only by the
screenshot fixture server. Nothing here is in ``sentinel/``: it is the shape of the ask, made
concrete enough to feel, over synthetic cases that cite the synthetic crash fixture.

The contract, in brief (docs/design/2026-09-26-directions/C/README.md has the reasoning):

- ``GET /api/cases`` lists cases; ``POST /api/cases`` opens one, optionally with first evidence.
- ``GET|PATCH /api/cases/{id}``: the case; a patch changes its title, notes or open state.
- ``POST /api/cases/{id}/evidence`` adds an exhibit directly. The same observation and selection
  twice is refused with 409, the stack's own rule.
- ``POST /api/cases/{id}/proposals`` is how anything proposes: a claim, its citations and the
  readings it says it read. A proposal never enters the evidence by itself.
- ``POST /api/cases/{id}/proposals/{pid}/accept`` and ``.../decline`` decide one.
- ``GET /api/cases/{id}/composed`` is the case exported as a handoff.

Every item records the *route* it arrived by: a bearer header is ``api``, the dashboard's session
cookie is ``dashboard``. That is a transport, not an author, and not a boundary: whoever holds the
token can also mint a cookie. It is what the server can honestly say until each client has its own
credential. State lives in memory and is lost when the fixture stops.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.routing import Mount

_lock = threading.Lock()
_cases: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _route(request: Request) -> str:
    return "api" if request.headers.get("authorization", "").lower().startswith("bearer ") else "dashboard"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


EXHIBIT_FIELDS = ("kind", "title", "reading", "params", "asked_at", "outcome", "classes", "ids", "moment", "facts", "note", "citations", "stop")


def _exhibit(case: dict[str, Any], body: dict[str, Any], route: str, at: str, accepted_from: str | None = None) -> dict[str, Any]:
    item = {key: body.get(key) for key in EXHIBIT_FIELDS}
    item["kind"] = item["kind"] or "reading"
    item["classes"] = item["classes"] or []
    item.update(id=_id("ex"), n=len(case["evidence"]) + 1, added_at=at, route=route, accepted_from=accepted_from)
    return item


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = ("kind", "reading", "params", "asked_at", "ids", "note")
    return all(a.get(k) == b.get(k) for k in keys)


def _log(case: dict[str, Any], at: str, route: str, what: str, ref: str | None = None) -> None:
    case["record"].append({"at": at, "route": route, "what": what, "ref": ref})
    case["updated_at"] = at


def _summary(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": case["id"], "title": case["title"], "state": case["state"], "opened_at": case["opened_at"],
        "opened_from": case["opened_from"], "updated_at": case["updated_at"], "closed_at": case["closed_at"],
        "evidence": len(case["evidence"]),
        "pending": [
            {"id": p["id"], "claim": p["claim"], "received_at": p["received_at"], "route": p["route"],
             "cites": len(p["cites"]), "read": len(p["read"]),
             "not_observed": sum(1 for r in p["read"] if r.get("outcome") not in ("ok", "empty"))}
            for p in case["proposals"] if p["state"] == "pending"
        ],
    }


def _missing(what: str) -> JSONResponse:
    return JSONResponse({"error": "not_found", "detail": f"no such {what}"}, status_code=404)


ROUTE_WORDS = {"dashboard": "in the dashboard", "api": "through the API"}


def compose(case: dict[str, Any]) -> str:
    lines = [f"# Case: {case['title']}", "", f"State: {case['state']}. Opened {case['opened_at']} from {case['opened_from'].get('label', 'a question')}."]
    if case["notes"]:
        lines += ["", "## The person's notes", "", case["notes"]]
    lines += ["", "## Evidence, in the order it happened", ""]
    placed = sorted(case["evidence"], key=lambda e: (e.get("moment") is None, e.get("moment") or ""))
    for e in placed:
        tag = f"Exhibit {e['n']} · {e.get('reading') or e['kind']} · {', '.join(e['classes']) or 'no class'} · taken {e.get('asked_at') or 'unknown'}"
        how = "proposed through the API, accepted in the dashboard" if e["accepted_from"] else f"added {ROUTE_WORDS[e['route']]}"
        lines.append(f"- {e['title']} ({tag}; {how})")
        for key, value in e.get("facts") or []:
            lines.append(f"  - {key}: {value}")
        if e.get("ids"):
            lines.append(f"  - Records: {', '.join(str(i) for i in e['ids'])}")
    declined = [p for p in case["proposals"] if p["state"] == "declined"]
    if declined:
        lines += ["", "## Proposals the person declined", ""]
        lines += [f"- \"{p['claim']}\" Declined: {p.get('decline_reason') or 'no reason given'}" for p in declined]
    lines += ["", "Every exhibit is evidence as it was read, not a diagnosis. Declined proposals are not evidence."]
    return "\n".join(lines)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/cases", tags=["cases (draft)"])

    @router.get("")
    def list_cases() -> dict[str, Any]:
        with _lock:
            ordered = sorted(_cases.values(), key=lambda c: (c["state"] != "open", c["updated_at"]), reverse=False)
            open_first = [c for c in ordered if c["state"] == "open"][::-1] + [c for c in ordered if c["state"] != "open"][::-1]
            return {"cases": [_summary(c) for c in open_first], "draft": True}

    @router.post("", status_code=201)
    async def open_case(request: Request) -> Any:
        body = await request.json()
        title = str(body.get("title") or "").strip()[:140]
        if not title:
            return JSONResponse({"error": "invalid", "detail": "a case needs a title"}, status_code=422)
        at, route = _now(), _route(request)
        case = {"id": _id("case"), "title": title, "state": "open", "opened_at": at, "updated_at": at, "closed_at": None,
                "closing_note": None, "opened_from": body.get("opened_from") or {"kind": "question", "label": "a question"},
                "route": route, "notes": "", "notes_updated_at": None, "evidence": [], "proposals": [], "record": []}
        _log(case, at, route, "Opened the case")
        for item in body.get("evidence") or []:
            exhibit = _exhibit(case, item, route, at)
            case["evidence"].append(exhibit)
            _log(case, at, route, f"Added exhibit {exhibit['n']}", exhibit["id"])
        with _lock:
            _cases[case["id"]] = case
        return case

    @router.get("/{case_id}")
    def get_case(case_id: str) -> Any:
        with _lock:
            return _cases.get(case_id) or _missing("case")

    @router.patch("/{case_id}")
    async def patch_case(case_id: str, request: Request) -> Any:
        body = await request.json()
        at, route = _now(), _route(request)
        with _lock:
            case = _cases.get(case_id)
            if not case:
                return _missing("case")
            if "title" in body and str(body["title"]).strip():
                case["title"] = str(body["title"]).strip()[:140]
                _log(case, at, route, "Renamed the case")
            if "notes" in body:
                case["notes"] = str(body["notes"])[:8000]
                case["notes_updated_at"] = at
                case["updated_at"] = at
            if body.get("state") in ("open", "closed") and body["state"] != case["state"]:
                case["state"] = body["state"]
                case["closed_at"] = at if body["state"] == "closed" else None
                case["closing_note"] = str(body.get("closing_note") or "")[:2000] or None if body["state"] == "closed" else None
                _log(case, at, route, "Closed the case" if body["state"] == "closed" else "Reopened the case")
            return case

    @router.post("/{case_id}/evidence", status_code=201)
    async def add_evidence(case_id: str, request: Request) -> Any:
        body = await request.json()
        at, route = _now(), _route(request)
        with _lock:
            case = _cases.get(case_id)
            if not case:
                return _missing("case")
            twin = next((e for e in case["evidence"] if _same(e, body)), None)
            if twin:
                return JSONResponse({"error": "duplicate", "id": twin["id"], "n": twin["n"]}, status_code=409)
            exhibit = _exhibit(case, body, route, at)
            case["evidence"].append(exhibit)
            _log(case, at, route, f"Added exhibit {exhibit['n']}", exhibit["id"])
            return case

    @router.post("/{case_id}/proposals", status_code=201)
    async def propose(case_id: str, request: Request) -> Any:
        body = await request.json()
        at, route = _now(), _route(request)
        claim = str(body.get("claim") or "").strip()
        if not claim or not body.get("cites"):
            return JSONResponse({"error": "invalid", "detail": "a proposal needs a claim and at least one citation"}, status_code=422)
        with _lock:
            case = _cases.get(case_id)
            if not case:
                return _missing("case")
            proposal = {"id": _id("pr"), "claim": claim[:2000], "cites": body["cites"], "read": body.get("read") or [],
                        "received_at": at, "route": route, "state": "pending", "decided_at": None, "decline_reason": None}
            case["proposals"].append(proposal)
            _log(case, at, route, "Received a proposal", proposal["id"])
            return proposal

    @router.post("/{case_id}/proposals/{proposal_id}/{decision}")
    async def decide(case_id: str, proposal_id: str, decision: str, request: Request) -> Any:
        if decision not in ("accept", "decline"):
            return _missing("decision")
        body = await request.json() if int(request.headers.get("content-length") or 0) else {}
        at, route = _now(), _route(request)
        with _lock:
            case = _cases.get(case_id)
            proposal = next((p for p in (case or {}).get("proposals", []) if p["id"] == proposal_id), None)
            if not case or not proposal:
                return _missing("proposal")
            if proposal["state"] != "pending":
                return JSONResponse({"error": "decided", "detail": f"this proposal was already {proposal['state']}"}, status_code=409)
            proposal["state"], proposal["decided_at"] = ("accepted" if decision == "accept" else "declined"), at
            if decision == "accept":
                exhibit = _exhibit(case, {"kind": "claim", "title": proposal["claim"], "classes": ["inferred"],
                                          "citations": proposal["cites"], "moment": body.get("moment") or _first_moment(proposal)},
                                   route, at, accepted_from=proposal["id"])
                case["evidence"].append(exhibit)
                _log(case, at, route, f"Accepted a proposal as exhibit {exhibit['n']}", exhibit["id"])
            else:
                proposal["decline_reason"] = str(body.get("reason") or "")[:2000] or None
                _log(case, at, route, "Declined a proposal", proposal["id"])
            return case

    @router.post("/_fixture/reseed")
    def reseed() -> dict[str, Any]:
        """Fixture only: put the synthetic cases back, so a capture run starts from the same state."""
        seed()
        return {"cases": len(_cases)}

    @router.get("/{case_id}/composed")
    def composed(case_id: str) -> Any:
        with _lock:
            case = _cases.get(case_id)
            return {"text": compose(case), "draft": True} if case else _missing("case")

    return router


def _first_moment(proposal: dict[str, Any]) -> str | None:
    moments = sorted(c["moment"] for c in proposal["cites"] if c.get("moment"))
    return moments[-1] if moments else None


def install(app: FastAPI) -> None:
    """Add the draft routes ahead of the dashboard's static mount, which would otherwise answer first."""
    app.include_router(build_router())
    mounts = [r for r in app.router.routes if isinstance(r, Mount) and r.path == ""]
    for mount in mounts:
        app.router.routes.remove(mount)
        app.router.routes.append(mount)
    seed()


# The synthetic cases. They cite the synthetic crash fixture (tests/fixtures/crash-records.json)
# and the signals the fixture composes from it; nothing here was read from a machine.

STOP_SEP12 = {"started_at": "2026-09-12T06:14:58.000Z", "stopped_at": "2026-09-12T06:11:02.000Z", "records": {"start": 1300, "power_41": 1301, "eventlog_6008": 1302}}
STOP_SEP05 = {"started_at": "2026-09-05T18:29:58.500Z", "stopped_at": "2026-09-05T18:12:44.113Z", "records": {"start": 1000, "power_41": 1001, "eventlog_6008": 1002}}
CRASH_READ = {"reading": "crash", "params": {"count": 5}, "asked_at": "2026-09-24T21:40:12Z", "outcome": "ok"}


def seed() -> None:
    with _lock:
        _cases.clear()
    froze = {
        "id": "case_sep12freeze", "title": "It froze and restarted on Sep 12", "state": "open",
        "opened_at": "2026-09-24T21:14:00Z", "updated_at": "2026-09-25T08:02:00Z", "closed_at": None, "closing_note": None,
        "opened_from": {"kind": "stop", "label": "the stop on Sep 12", "stop": STOP_SEP12},
        "route": "dashboard",
        "notes": "Screen froze mid-game and the machine restarted by itself about four minutes later. "
                 "I think this happened once before this month. Check whether it is the display driver before reinstalling anything.",
        "notes_updated_at": "2026-09-24T21:20:00Z", "evidence": [], "proposals": [], "record": [],
    }
    froze["evidence"] = [
        {"id": "ex_stop12", "n": 1, "kind": "selection", "title": "The stop on Sep 12: DPC_WATCHDOG_VIOLATION (0x133)",
         "reading": "crash", "params": {"count": 5}, "asked_at": "2026-09-24T21:13:48Z", "outcome": "ok", "classes": ["derived"],
         "ids": [1300, 1301, 1302], "moment": "2026-09-12T06:11:02.000Z",
         "facts": [["Windows stop estimate", "2026-09-12T06:11:02.000Z"], ["Next start", "2026-09-12T06:14:58.000Z"], ["Down for", "3 min 56 s"],
                   ["Bug check", "0x133 · DPC_WATCHDOG_VIOLATION"], ["Dump", "091226-12345-01.dmp · matched by time"]],
         "stop": STOP_SEP12, "note": None, "citations": None, "added_at": "2026-09-24T21:14:00Z", "route": "dashboard", "accepted_from": None},
        {"id": "ex_rec1295", "n": 2, "kind": "selection", "title": "Display 4101: the display driver stopped responding and recovered",
         "reading": "crash", "params": {"count": 5}, "asked_at": "2026-09-24T21:13:48Z", "outcome": "ok", "classes": ["raw"],
         "ids": [1295], "moment": "2026-09-12T06:10:45.000Z",
         "facts": [["Record", "System 1295 · Display · event 4101 · Warning"], ["Written", "2026-09-12T06:10:45.000Z"],
                   ["Message", "Display driver nvlddmkm stopped responding and has successfully recovered."],
                   ["Placed", "Last System record before the restart, 17 s before Windows' stop estimate"]],
         "note": None, "citations": None, "added_at": "2026-09-24T21:16:30Z", "route": "dashboard", "accepted_from": None},
    ]
    froze["proposals"] = [
        {"id": "pr_display_cause", "claim": "The display driver caused both freezes.",
         "cites": [{"label": "Display 4101 before the Sep 12 stop", "reading": "crash", "params": {"count": 5}, "ids": [1295], "moment": "2026-09-12T06:10:45.000Z"}],
         "read": [dict(CRASH_READ, asked_at="2026-09-24T22:05:10Z")],
         "received_at": "2026-09-24T22:05:31Z", "route": "api", "state": "declined", "decided_at": "2026-09-25T07:58:00Z",
         "decline_reason": "Not established. It cites one stop; the Sep 5 stop's last record is a storage reset, not the display."},
        {"id": "pr_same_bugcheck", "claim": "The stop on Sep 5 carries the same bug check, 0x133 DPC_WATCHDOG_VIOLATION. "
                                             "A shared code is a lead: whether the two stops share a cause is for their dumps and the record before each start to say.",
         "cites": [
             {"label": "The stop on Sep 5 · Kernel-Power 41", "reading": "crash", "params": {"count": 5}, "ids": [1001], "moment": "2026-09-05T18:12:44.113Z", "stop": STOP_SEP05},
             {"label": "The stop on Sep 12 · Kernel-Power 41", "reading": "crash", "params": {"count": 5}, "ids": [1301], "moment": "2026-09-12T06:11:02.000Z", "stop": STOP_SEP12},
             {"label": "Lead: 2 stops share bug check 0x133", "reading": "signals", "params": {}, "signal": "transition:repeated-stop:0x133"},
         ],
         "read": [dict(CRASH_READ, asked_at="2026-09-25T07:41:02Z"),
                  {"reading": "signals", "params": {}, "asked_at": "2026-09-25T07:41:05Z", "outcome": "ok",
                   "inputs": {"hardware": "ok", "power": "ok", "events": "ok", "crash": "ok", "pcie": "failed", "constraints": "failed", "reliability": "failed"}}],
         "received_at": "2026-09-25T07:41:20Z", "route": "api", "state": "pending", "decided_at": None, "decline_reason": None},
        {"id": "pr_storage_before", "claim": "The last System record before the Sep 5 restart is storahci 129, a reset issued to a storage device, "
                                              "4 s before Windows' stop estimate. It is a record near the stop, not a cause.",
         "cites": [{"label": "storahci 129 · System 995", "reading": "crash", "params": {"count": 5}, "ids": [995], "moment": "2026-09-05T18:12:40.000Z"}],
         "read": [dict(CRASH_READ, asked_at="2026-09-25T07:41:02Z")],
         "received_at": "2026-09-25T08:02:00Z", "route": "api", "state": "pending", "decided_at": None, "decline_reason": None},
    ]
    froze["record"] = [
        {"at": "2026-09-24T21:14:00Z", "route": "dashboard", "what": "Opened the case from the stop on Sep 12", "ref": None},
        {"at": "2026-09-24T21:14:00Z", "route": "dashboard", "what": "Added exhibit 1", "ref": "ex_stop12"},
        {"at": "2026-09-24T21:16:30Z", "route": "dashboard", "what": "Added exhibit 2", "ref": "ex_rec1295"},
        {"at": "2026-09-24T22:05:31Z", "route": "api", "what": "Received a proposal", "ref": "pr_display_cause"},
        {"at": "2026-09-25T07:41:20Z", "route": "api", "what": "Received a proposal", "ref": "pr_same_bugcheck"},
        {"at": "2026-09-25T07:58:00Z", "route": "dashboard", "what": "Declined a proposal", "ref": "pr_display_cause"},
        {"at": "2026-09-25T08:02:00Z", "route": "api", "what": "Received a proposal", "ref": "pr_storage_before"},
    ]
    slow = {
        "id": "case_slowupdate", "title": "Slow to wake after the September update", "state": "closed",
        "opened_at": "2026-09-16T19:02:00Z", "updated_at": "2026-09-20T10:15:00Z", "closed_at": "2026-09-20T10:15:00Z",
        "closing_note": "Went away after a full restart. Nothing in the record pointed anywhere.",
        "opened_from": {"kind": "question", "label": "a question"}, "route": "dashboard",
        "notes": "Took a long time to wake from sleep for two days after the update.", "notes_updated_at": "2026-09-16T19:03:00Z",
        "evidence": [], "proposals": [], "record": [
            {"at": "2026-09-16T19:02:00Z", "route": "dashboard", "what": "Opened the case", "ref": None},
            {"at": "2026-09-20T10:15:00Z", "route": "dashboard", "what": "Closed the case", "ref": None}],
    }
    with _lock:
        _cases[froze["id"]] = froze
        _cases[slow["id"]] = slow
