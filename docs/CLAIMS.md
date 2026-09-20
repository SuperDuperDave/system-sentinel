# What the studio page may claim about System Sentinel

Every claim the page at mainthread.ai/work/system-sentinel/ could make about the tool after the overhaul, each with the source path that implements it or the command that checks it. Written 2026-09-20 for the studio's evidence note, which binds the page's words; the page is built in the studio, not here. "Check" commands run from the repository root on the Windows machine (or WSL beside it) with the server running and `TOKEN` set from `system-sentinel token`.

## The boundary

| Claim | Source or check |
| --- | --- |
| One API is the product's single boundary; the desktop dashboard, a phone and a local agent are three clients of it | `sentinel/app.py` (the routes), `dashboard/src/api.ts` (the dashboard is a client over relative paths), `sentinel/mcp_server.py` (the agent's projection of the same catalog) |
| Every reading returns one envelope whose outcome says whether the machine was observed: `ok`, `empty`, `failed`, `unavailable`, `denied`, `timeout` | `sentinel/bridge.py` (`OUTCOMES`), `sentinel/reading.py` (`Reading`); check: `curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/readings/events?count=1` and read `outcome` |
| A collection failure is distinguishable from no findings | `tests/test_bridge.py`, `tests/test_reading.py::test_empty_reading_is_distinguishable_from_failure`, `tests/test_host.py::test_record_before_a_moment_and_before_the_log_began` (a moment before the log began is `empty`; a log that does not exist is `failed`) |
| Raw, derived, invariant and inferred are typed on the wire; inferred sections carry their basis | `sentinel/reading.py` (`Section.cls`, `basis`); check: `.../api/readings/hardware` shows `fingerprint:invariant`, `config:raw`, `risks:inferred` |
| Every reading carries the query that produced it, so the evidence can be reproduced by hand | `method` in every envelope; check any reading's `method.query` |
| Nineteen readings | `GET /api/readings` (the catalog); `sentinel/readings/*.py` |
| The record before a moment: the log does not announce a freeze, the next start does | `sentinel/readings/events.py` (`record`), `dashboard/src/views/Record.tsx` ("The record before this"); check: `.../api/readings/record?before=<a Kernel-Power 41 TimeCreated>&count=25` |
| WHEA records with their binary payload decoded beside them | `sentinel/readings/whea.py` (`decode_all`, `sentinel/tools/DecodeWheaRecord`); `tests/test_whea.py` (the decoder run against a constructed CPER record and an invalid one) |
| Storms: 60-second buckets, a burst at 5 a minute, critical above twice that, acceleration at 2x a 240-bucket baseline over a 10-bucket window, grouped by signature, computed from the log on demand over wall-clock time including idle minutes | `sentinel/readings/whea.py` (`storms`, `status`, constants); `tests/test_whea.py` with `tests/fixtures/whea-records.json` |
| The PCIe fabric with the endpoints that share an upstream link | `sentinel/readings/diagnostics.py` (`pcie`, from `pnputil /enum-devices /relations`); check: `.../api/readings/pcie` → `groups:derived` |
| Five forensic signal classes, each a lead with its rule and the readings it drew on, never a diagnosis | `sentinel/readings/diagnostics.py` (`signals`), `dashboard/src/views/Signals.tsx`; check: `.../api/readings/signals` → `method.readings`, `sections[0].basis` |
| Event streaming over server-sent events, with a `bridge` event when a poll did not observe the machine | `sentinel/stream.py`; check: `curl -N -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/stream` |

## Privacy and the boundary

| Claim | Source or check |
| --- | --- |
| By default no response carries a serial number, the computer name, a user name or a MAC address; profile paths read `C:\Users\<user>\…`; every response lists what was removed | `sentinel/redact.py`; `tests/test_redact.py`, `tests/test_app.py::test_reading_arrives_redacted_by_default`; check: `redacted` on any reading |
| A caller receives the real values only by asking for `unredacted` by name | `sentinel/app.py` (`unredacted` query parameter), `sentinel/mcp_server.py` (`unredacted` argument); `tests/test_app.py::test_unredacted_by_name` |
| Every route that carries machine data and the MCP endpoint require the access token or the session cookie made from it | `sentinel/auth.py` (`TokenMiddleware`); check: `curl -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/readings` → `401` |
| The server binds to localhost unless told otherwise | `sentinel/cli.py` (`serve --host` default `127.0.0.1`) |
| Nothing leaves the machine unless a person sends it: the handoff is copied to the clipboard, captures are files on disk | `dashboard/src/Copy.tsx`, `sentinel/capture.py` (writes under the data directory; no network) |

## For agents

| Claim | Source or check |
| --- | --- |
| An agent registers the server once and every reading is a tool | `docs/API.md` ("Claude Code"); check: `claude mcp add --transport http system-sentinel http://127.0.0.1:8000/mcp --header "Authorization: Bearer $TOKEN"` then list tools |
| Twenty-five MCP tools: the nineteen readings and six stack tools | `sentinel/mcp_server.py`; check: `tools/list` over JSON-RPC at `/mcp` (see `tests/test_app.py::test_mcp_lists_the_catalog_and_calls_a_reading`) |
| The interface is documented for an agent to read without the source | `docs/API.md`; the live form at `/api/docs` and `/api/openapi.json` |
| An agent reads the same composed handoff a person copies, and needs no clipboard | `sentinel/stack.py` (`compose`), `GET /api/stack/composed`, the `compose` tool |
| A one-shot agent connected over HTTP MCP and read the machine through the tool | Observed 2026-09-20: `claude -p` with the server registered read `health` and `events` and reported their outcomes; reproduce with the command in `docs/API.md` |

## The stack and captures

| Claim | Source or check |
| --- | --- |
| The stack lives on the machine, so the desktop, the phone and the agent see one stack | `sentinel/stack.py` (`Store` in the data directory); `tests/test_stack.py` (persistence across two app instances) |
| Items carry rank, verbosity and the envelope they were added with, so a not-observed reading says so in the handoff | `sentinel/stack.py` (`Item`, `render`); `dashboard/src/views/Stack.tsx` |
| Six prompt presets by name, yours to add, edit and delete | `sentinel/stack.py` (`PRESET_PROMPTS`); `GET /api/prompts` |
| The same evidence is not stacked twice | `POST /api/stack/items` → `409`; `tests/test_stack.py` |
| A capture is every reading, the stack and the composed text in one ZIP with a manifest that lists exactly its members, each reading's outcome and the redaction applied | `sentinel/capture.py`; check: `curl -X POST -H "Authorization: Bearer $TOKEN" -o capture.zip http://127.0.0.1:8000/api/captures && unzip -l capture.zip` |

## The dashboard

| Claim | Source or check |
| --- | --- |
| Eight views: Record, Hardware errors, Crash dumps, Machine, Diagnostics, Signals, Stack, Agents | `dashboard/src/store.ts` (`VIEWS`) |
| Every view starts with the outcome line: what was asked, what came back, whether the machine was observed, the method on request | `dashboard/src/Outcome.tsx`; every file under `dashboard/src/views/` |
| Inspect in place: a record opens where it is, and the record before it opens under it | `dashboard/src/views/Record.tsx`, `dashboard/src/Sections.tsx` (`RowList`) |
| Heavy readings are taken only when asked | `dashboard/src/views/Machine.tsx`, `Diagnostics.tsx` ("Take the reading"); `useReading(..., enabled)` |
| Works at phone width over the same boundary | Verified on glass 2026-09-20 at 390x844 and 1440x900, every view, no horizontal overflow; `docs/screens/` holds the sanitized captures with the commit, sizes and the fixture pipeline that reproduces them |
| The identity is in the tool: the tokens, Azeret Mono, JetBrains Mono, DM Sans self-hosted under the OFL, the mark, the graticule in one zone, the phosphor as light only | `dashboard/src/identity.css`, `dashboard/src/Mark.tsx`, `dashboard/src/App.module.css`, `dashboard/public/fonts/`; owned by `docs/design/IDENTITY-DIRECTIONS-2026-09-20.md` |
| One lit word: `live` while the stream is connected | `dashboard/src/Live.tsx` |

## Running it

| Claim | Source or check |
| --- | --- |
| Runs on the machine it reads, Windows 10 and 11, no hosted version, no account | `README.md`, `sentinel/cli.py`; the tool has no network client |
| One process serves the API, the dashboard and the MCP endpoint on one origin | `sentinel/app.py` (`create_app`: routes, `/mcp`, static) |
| A test suite: unit tests through a fake bridge, host tests against the real event log | `tests/`; check: `./.venv/bin/python -m pytest` |
| `system-sentinel check` proves the bridge before anything is asked of it | `sentinel/cli.py` (`_check`) |
| Installable by pasting one prompt to your agent | `docs/DEPLOY.md`; its "tested on" section records the run |
| Double-click and it runs: one file, the browser opens on the dashboard already signed in, the mark in the tray with Open dashboard, Copy address for agents, Start with Windows and Quit; the person never sees the token | `sentinel/launcher.py`, `build/windows/build.ps1`; observed 2026-09-20 on the Windows side from a local disk: the dashboard answered within seconds, nineteen readings, the decoder present inside the bundle, 401 without the token, a second launch opened the dashboard and exited, no listener after Quit |
| The browser is let in by a one-time code signed with the token and spent once, only from this machine | `sentinel/auth.py` (`mint_code`, `code_valid`), `sentinel/app.py` (`GET /api/session/open`); `tests/test_launcher.py` |
| Reaching it from a phone is a transport in front of the token boundary; Tailscale recommended, a tunnel documented | `docs/DEPLOY.md` ("Optional: reach it from your phone"); `_sessions/PLANNING.md` ("The remote path") |

## Deliberate absences, unchanged

No live telemetry as a service, no prediction, no crash-dump decoding (the dump inventory lists files; the decoder reads WHEA records, a different artifact), no diagnosed machine, no accuracy, no saved time, no users, no release. A hosted, paired dashboard is planned after the overhaul and is not claimed until it exists.

## Pending this session

The deploy prompt's "tested on" record joins this list when the run is observed.

## The screens

| Claim | Source or check |
| --- | --- |
| The screens show the real dashboard rendering a synthetic record built from Windows' own providers, ids and message templates, so nothing from any real machine appears | `docs/screens/README.md`; reproduce with `docs/screens/fixtures/fixture-server.py` and `capture.cjs` |
| Captured at 1440x900 at 2x and 390x844 at 3x from a recorded commit | `docs/screens/README.md` (the table and the commit) |
