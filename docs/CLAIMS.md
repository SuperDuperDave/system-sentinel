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
| By default no response carries a serial number, the computer name, a user name, a MAC address or a network address; profile paths read `C:\Users\<user>\…`; every response lists what was removed. Fields are redacted by name, so this does not depend on the machine's names having been learned | `sentinel/redact.py`; `tests/test_redact.py`, `tests/test_app.py::test_reading_arrives_redacted_by_default`; check: `redacted` on any reading |
| A caller receives the real values only by asking for `unredacted` by name | `sentinel/app.py` (`unredacted` query parameter), `sentinel/mcp_server.py` (`unredacted` argument); `tests/test_app.py::test_unredacted_by_name` |
| Every route that carries machine data and the MCP endpoint require the access token or the session cookie, whose value is derived from the token and is not the token | `sentinel/auth.py` (`TokenMiddleware`); check: `curl -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/readings` → `401` |
| The server binds to localhost unless told otherwise | `sentinel/cli.py` (`serve --host` default `127.0.0.1`) |
| Nothing leaves the machine unless a person sends it: the handoff is copied to the clipboard, captures are files on disk | `dashboard/src/Copy.tsx`, `sentinel/capture.py` (writes under the data directory; no network) |

## For agents

| Claim | Source or check |
| --- | --- |
| An agent registers the server once and every reading is a tool | `docs/API.md` ("Claude Code"); check: `claude mcp add --transport http system-sentinel http://127.0.0.1:8000/mcp --header "Authorization: Bearer $TOKEN"` then list tools |
| Twenty-seven MCP tools: the nineteen readings and eight stack tools | `sentinel/mcp_server.py`; check: `tools/list` over JSON-RPC at `/mcp` (see `tests/test_app.py::test_mcp_lists_the_catalog_and_calls_a_reading`) |
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
| Every view that takes a reading starts with the outcome line: what was asked, what came back, whether the machine was observed, the method on request (Stack and Agents read no reading; Diagnostics shows it once a reading is taken) | `dashboard/src/Outcome.tsx`; every file under `dashboard/src/views/` |
| Inspect in place: a record opens where it is, and the record before it opens under it | `dashboard/src/views/Record.tsx`, `dashboard/src/Sections.tsx` (`RowList`) |
| Heavy readings are taken only when asked | `dashboard/src/views/Machine.tsx`, `Diagnostics.tsx` ("Take the reading"); `useReading(..., enabled)` |
| Works at phone width over the same boundary | Verified on glass 2026-09-20 at 390x844 and 1440x900, every view, no horizontal overflow; `docs/screens/` holds the sanitized captures with the commit, sizes and the fixture pipeline that reproduces them |
| The identity is in the tool: the tokens, Azeret Mono, JetBrains Mono, DM Sans self-hosted under the OFL, the mark, the graticule in one zone, the phosphor as light only | `dashboard/src/identity.css`, `dashboard/src/Mark.tsx`, `dashboard/src/App.module.css`, `dashboard/public/fonts/`; owned by `docs/design/IDENTITY-DIRECTIONS-2026-09-20.md` |
| One lit word: `live` while the stream is connected | `dashboard/src/Live.tsx` |

## Running it

| Claim | Source or check |
| --- | --- |
| Runs on the machine it reads, Windows 10 and 11, no hosted version, no account | `README.md`, `sentinel/cli.py`; check: `grep -rn "urlopen\|requests\|httpx" sentinel/` finds one client, the launcher asking its own server at `http://127.0.0.1` |
| One process serves the API, the dashboard and the MCP endpoint on one origin | `sentinel/app.py` (`create_app`: routes, `/mcp`, static) |
| A test suite: unit tests through a fake bridge, host tests against the real event log | `tests/`; check: `./.venv/bin/python -m pytest` |
| `system-sentinel check` proves the bridge before anything is asked of it | `sentinel/cli.py` (`_check`) |
| Published as a GitHub Release: one executable and the list of its SHA-256, downloadable without an account; the download path (download, the checksum against the list, unblock, start, first answer, `health` and `events`) run against the published address on a clean Windows 11 image in Windows Sandbox on 2026-09-21 | https://github.com/SuperDuperDave/system-sentinel/releases/latest; `build/windows/sandbox/run.ps1 -SkipSource -Release <url>`, recorded in `docs/DEPLOY.md` ("What the prompts were tested on") |
| Installable by pasting one prompt to your agent, from the release or from source; the agent inspects what it will run before it installs | `docs/DEPLOY.md` (two prompts); the long prompt run end to end through a one-shot Claude Code agent on 2026-09-20 on this machine (prerequisites present), and step by step on a clean Windows 11 image in Windows Sandbox on 2026-09-21 by `build/windows/sandbox/`, both recorded in its "tested on" section; the short prompt's record is there too |
| Double-click and it runs on a machine with nothing installed: one file, the browser opens on the dashboard already signed in, the mark in the tray with Open dashboard, Sign in another device, Copy address for agents, Start with Windows, a submenu named for the version holding Check for updates and Remove from this computer, and Quit; the person never types or reads the token (Copy address for agents places the agent's registration line, token included, on the clipboard on purpose) | `sentinel/launcher.py`, `build/windows/build.ps1`; observed 2026-09-20 on the Windows side from a local disk (nineteen readings, the decoder inside the bundle, 401 without the token, a second launch opened the dashboard and exited, no listener after Quit) and 2026-09-21 on a clean Windows 11 image in Windows Sandbox with no Python (first answer within ten seconds of the start, `health` and `events` ok); the entries built on the Windows side by `sentinel.launcher.tray_menu` (six and the submenu's two, observed 2026-09-21 with the real pystray), checked by `tests/test_launcher.py` where pystray is present |
| The browser is let in by a one-time code signed with the token and spent once, only from this machine; the launcher's log never carries the code | `sentinel/auth.py` (`mint_code`, `code_valid`, `code_expiry`), `sentinel/app.py` (`GET /api/session/open`, `State.spend_code`); `tests/test_launcher.py` |
| A phone signs in by scanning a QR code from the dashboard or the tray: the machine is asked which address it publishes on the private network, the link carries a five-minute one-time code, and the token is never displayed | `sentinel/link.py`, `sentinel/app.py` (`POST /api/session/link`), `dashboard/src/Devices.tsx`, `sentinel/launcher.py` (Sign in another device); `tests/test_link.py`; observed 2026-09-21 through Tailscale serve on this machine: the route answered `ok` with the published address and the link opened the session once |
| The file installs itself: run from any path but the installed one it copies itself into the data directory and starts that copy, so it can be run from Downloads | `sentinel/launcher.py` (`plan`, `install`, `start_installed`); check on Windows: run a copy from a temporary folder with nothing serving, then `(Get-FileHash "$env:LOCALAPPDATA\SystemSentinel\SystemSentinel.exe").Hash` equals the copy's; observed 2026-09-21 on this machine on a spare port and in Windows Sandbox, recorded in `docs/DEPLOY.md` ("What the prompts were tested on") |
| A newer file double-clicked replaces the installed copy: it asks the running one to quit, takes its place and keeps the token, the stack, the prompts and the captures. It does not downgrade; an older file says a newer version is running and leaves it | `sentinel/launcher.py`; check: `(Invoke-RestMethod -Uri http://127.0.0.1:8000/api/readings -Headers @{ Authorization = "Bearer $TOKEN" }).version` before and after, with the token file unchanged; observed 2026-09-21 on this machine (1.0.2 over 1.0.1, and 1.0.1 refused over 1.0.2) and on the clean image (1.0.1 over the published 1.0.0, which has no quit route and is the one update done by hand) |
| The tool can be stopped over its own boundary, by the token and from this machine only: the session cookie a browser or a phone holds is refused | `sentinel/app.py` (`POST /api/quit`), `sentinel/auth.py`, `sentinel/launcher.py` and `sentinel/cli.py` (each sets the quit callback on `State`); check: the route with the bearer header answers `202 {"quitting": true}` and the listener is gone within a few seconds; the same call with only the cookie answers `401` |
| Which version is installed is in the file, the way Windows keeps it | `build/windows/build.ps1` (the generated version resource), `sentinel/__init__.py` (`__version__`); check: `(Get-Item SystemSentinel.exe).VersionInfo.ProductVersion` |
| Which version is answering is on the catalog, so a caller can tell the running process from the file on disk | `sentinel/app.py` (`GET /api/readings`), `dashboard/src/views/Agents.tsx`; check: `curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/readings` → `version` |
| The tool removes itself: a confirmation that names what goes and what stays, then Start with Windows off, quit, and the data directory deleted after the process has exited. Nothing of it is left outside that directory but the Startup shortcut it removes | `sentinel/launcher.py` (the confirmation and the detached removal); `docs/DEPLOY.md` ("Removing it") states the same scope by hand |
| Check for updates opens the releases page in the browser; the tool asks nothing of anything off this machine, here or anywhere | `sentinel/launcher.py` (the same threaded opener the dashboard uses); check: `grep -rn "urlopen\|requests\|httpx" sentinel/` finds one client, the launcher asking its own server at `http://127.0.0.1` |
| Every release from 1.0.1 is built by the repository's own workflow and carries a build provenance attestation, beside the SHA-256 list and the digest GitHub computes for each asset | `.github/workflows/`; check: `gh attestation verify SystemSentinel.exe -R SuperDuperDave/system-sentinel`, and `gh release view v1.0.0 --json assets --jq '.assets[].digest'` for the digest GitHub holds |
| Reaching it from a phone is a transport in front of the token boundary; Tailscale recommended, a tunnel documented | `docs/DEPLOY.md` ("Optional: reach it from your phone") |
| Reached from a phone over a private network with HTTPS, the API still refusing anything without the token | Observed 2026-09-20: the dashboard opened on a phone over cellular through Tailscale serve (tailnet only, Funnel off), signed in by a one-time code; `GET /api/readings` over the same path answered 401 without the token |

## Deliberate absences, unchanged

No live telemetry as a service, no prediction, no crash-dump decoding (the dump inventory lists files; the decoder reads WHEA records, a different artifact), no diagnosed machine, no accuracy, no saved time, no users. A hosted, paired dashboard is planned after the overhaul and is not claimed until it exists.

## The screens

| Claim | Source or check |
| --- | --- |
| The screens show the real dashboard rendering a synthetic record built from Windows' own providers, ids and message templates, so nothing from any real machine appears | `docs/screens/README.md`; reproduce with `docs/screens/fixtures/fixture-server.py` and `capture.cjs` |
| Captured at 1440x900 at 2x and 390x844 at 3x from a recorded commit | `docs/screens/README.md` (the table and the commit) |
