# Changelog

## [Unreleased] - 2026-09-22

### Added
- Continuous local performance history: one elected collector per data home keeps numeric-only aggregate processor, memory and disk samples across stops. The default interval is one minute, configurable to ten minutes; daily JSONL is capped and kept up to 30 days. A pause switch, clear action and last-attempt status are authenticated controls. `load` takes a fresh snapshot; `performance_history` gives agents exact rows and a derived window summary. The Performance view charts the returned samples with unconnected gaps, a keyboard-operable sample inspector, raw JSON and a window held before an investigation moment. A selected sample opens System records before its timestamp and remains selected on return; the held moment is shown with its time.
- A fresh `processes` reading names the returned process instances using Windows' CPU time, private memory and process I/O counters, with bounded raw rows and separately derived leaders. Performance opens with three visual rankings that lead to exact, searchable process rows; no process name enters the retained history.
- Crashes shows the last System record before restart, Windows' stop estimate and the next start as separate evidence points for each returned stop. A report-only stop leads with its filed time, bug check and matched dump while naming the missing System evidence. Selecting either kind opens its exact detail, record jump and stack action.
- Crashes now maps the faults returned in a reading by decoded kind, with application and live-kernel groups, kind filters that focus exact rows, the full derived summary and matching raw Application-log records inside each fault. The overview distinguishes raw log-record counts from decoded fault instances, since one live-kernel report can span several records.
- A grouped, left-aligned navigation rail with distinct line icons. On phones, the current view stays visible above a keyboard-operable disclosure of every view and the device sign-in action.
- A day-by-day visual history in Crashes: Windows' stability index and returned reliability events share a UTC timeline. Selecting a day shows exact event types, the reported index, and the raw records; the same reading can be stacked for an agent.
- Dashboard addresses now remember the selected view and an investigation moment. Refresh, copied links and browser back/forward return to the right screen; links to sections that load after a reading answer land at the section, and view changes focus the new title.
- Record opens with a time-density view and leading sources for the returned System log rows. A bar or source opens and focuses one exact row below; the chart names its sample boundary instead of implying it covers the whole log.
- Diagnostics shows a memory map after the person takes the reading: installed capacity, reported slot population and each returned module's configured speed against its rating, with links to the exact derived and Windows fields below.
- Diagnostics now opens the PCIe reading with its shared-upstream groups: a bar for each returned group, exact endpoint counts and expandable device branches, followed by the Windows endpoint and bridge records. Bar lengths count endpoints and make no claim about bandwidth or fault likelihood.
- On phones, Record keeps the selected level and window visible in one disclosure. Its full filter controls remain one tap away, letting the returned evidence appear sooner in the first viewport.

### Changed
- Record now leads with a plain-language question and a clearer sampled time-and-source panel, then opens a more legible exact row with its complete returned JSON. The tool's Field Instrument refinement increases the base reading sizes and keyboard focus visibility while keeping the same source and raw evidence paths.
- The source basis shown with decoded stops and faults now describes the event-manifest mapping without a fixed observation date in product copy.
- Crash descriptions now say precisely that the last System record is the last one before the next start. Its timestamp may be later than Windows' estimated stop and does not by itself establish cause.
- Network adapter inventory stays available when Windows cannot assemble IP configuration; affected `ip` fields are null with a reading warning instead of empty lists that suggest no addresses.
- The memory reading leaves installed capacity unknown if any module lacks a reported capacity, and leaves free slot count unknown when the array's total is missing, contradictory or unsupported by returned modules. Missing data no longer becomes zero capacity or zero free slots.
- Signals opens with a visual five-class lead map and input coverage, with each count linked to its evidence and no health score implied.
- Machine opens with a visual inventory of four selected components and gauges for processor load and free physical memory; each part leads to its detailed reading, with exact fields still available below. The hardware fingerprint is classified as derived, with its selection rule on the wire. The disk selected by Windows index 0 is labeled disk 0 and exposed as `disk0_model`, rather than being called the boot disk without evidence.
- The reliability reading now counts returned records by source **and event ID** in its derived daily rollup. The UI and API name informational records, including successful Windows updates, so event volume cannot masquerade as failure volume.
- The index-fall signal requires adjacent UTC days and carries the event types Windows returned. A missing day no longer assigns an unseen index change to the next returned day.
- WHEA decoding checks the CPER header, declared length and section bounds before starting the external decoder. Malformed records retain their raw payload and carry a per-record error. Identical payloads are decoded once per reading and still appear under each record's own ID. The synthetic screenshot server now serves structurally valid CPER data wherever it offers binary data, so reproducing screenshots does not feed short fixture tokens to the real decoder.

## [1.1.0] - 2026-09-21

The moment, answered in one call and one click. "It froze at 02:14" now has a reading that names the stop, the bug check, the dump and the machine's last word before it, and a dashboard where every time that frames an investigation is a place you can go to.

### Added
- A person-triggered tray update flow: check GitHub's latest stable release, confirm before download, verify the executable with GitHub's asset digest and the release's SHA-256 list, then start it through the existing handoff. `system-sentinel update` checks from a terminal; `--install` installs on Windows. No background checks or machine readings leave the computer.
- `crash`: the stops the machine did not plan, newest first, each one composed out of the records of its session — when it stopped as Windows estimated it (read from the EventLog 6008 record's binary value, not from its locale text), when it started again, the bug check if one was written and the parameters it carried, the dump file on disk that belongs to it and how it was matched to it, and the last record the machine managed before it went, with how long it had been quiet. Give it a moment instead of a count and it reports what the first start at or after that moment announced, which is the only thing that can answer for a freeze; a start that announced nothing is an answer too, and the reading says which start it was. One launch, however many stops it names.
- `faults`: what went wrong while the machine kept running — the programs that crashed or hung and the kernel's own live reports, a GPU timeout or a watchdog, each with its application, its module and its exception named, and one entry per report however many records Windows wrote it across.
- `reliability`: Windows' own events related to reliability, including informational entries, and the stability index it computes every hour, rolled up by day — where the index stood at each day's end, how low it went, and what Windows returned that day, by source. It is somebody else's arithmetic, which is exactly why it is worth having beside the tool's own readings: it can disagree, and a disagreement is a lead.
- **Crashes**, the view, in place of Crash dumps: the stops, the programs and the kernel's live reports, and the dump files, each reading with its own outcome line and each row opening in place on the facts behind it. Opening a dump now takes `dump_header` on demand: its format and recorded bug check where readable, or an explicit Windows denial.
- `dump_header` opens only a selected file from the Windows dump inventory and exposes bounded structural bytes and their offsets. A recognized x64 kernel header yields its stop code and four parameters; an MDMP stream minidump yields its directory, exception and system metadata, and up to 128 module records. An exception address is located within a recorded module range where possible, without treating that module as a cause. Crashes puts the interpreted facts first, with an in-place raw readout and one action to stack the same reading for an agent. The rest of the dump is not validated.
- The moment as somewhere to go. A stop, a fault, a dump file, a hardware-error record, a time inside a signal's evidence, a lit stretch of the hardware-error trace: each carries one control that frames the log around that instant — the records before it, oldest first, ending there, widened twenty-five at a time, with the way back beside them. The frame is held while you look at something else and is still there when you come back.
- `since` on `events` and `faults`: an ISO moment, or the word `boot` for this session only, answered by the log's own index rather than by a scan. Record and Hardware errors both offer it as a window.
- Two more signals, each a lead with its rule: stops that share a bug check code, and stops that wrote none, as one signal per group; and the day Windows' own stability index fell furthest below where the day before left it.

- The bridge answers from a pool of live PowerShell sessions instead of starting a process for every question. A question used to pay for a shell to start before any work happened, and for a light reading that was most of the cost; now the shell is already running. `Bridge.run` is unchanged — the same six outcomes, the same envelope — because the rules that decide an outcome were lifted into one function that both transports hand the same three facts. A session that overruns its question or dies is discarded and replaced, a question no session can take is launched the old way rather than lost, and `health` says which transport carried your questions and how the pool is doing. `docs/measurements/latency.md` has both transports measured side by side on one machine.
- `system-sentinel bench`: the tool measures itself. Every reading, N times, min, median and p95 of the envelope's own `took_ms`, with a reading that was not observed carrying its outcome rather than a time, because a failure is not a measurement.
- The MCP surface is the whole protocol rather than a tool list. Every tool is annotated with what it does to the machine, so a client can stop confirming harmless readings and keep confirming the two that remove things. Every reading answers with typed structured content beside its text against one shared schema, so an agent branches on `outcome` as a field instead of parsing a string. The six presets are MCP prompts; the catalog and the composed handoff are resources; captures are tools. Asking for an unredacted view now requires a reason, and the reason is carried into the answer's warnings.
- The evidence a reviewer checks in a minute: dependencies pinned in a hashed lock and installed with `--require-hashes`, `pip-audit` and `npm audit`, `ruff` and strict `mypy` over the boundary files, `eslint` with accessibility rules for the dashboard, CodeQL, an SBOM beside each released executable, coverage printed, and a test that fails the day the counts written in our own prose go stale.

### Changed
- A live PowerShell session now waits for both output streams to close within the question's timeout. An incomplete error stream makes the reading unavailable and retires the session; it can no longer make a failed query appear empty. Health reports a bounded category for the last session-start failure, so a one-shot fallback has a reason without exposing raw stderr. The bridge also recognizes WSL's socket-bind launch failure as unavailable and retries it. Redaction covers numeric and structured values under sensitive field names, not only strings.
- `memory` gives up the bug check half of its ledger — the stops are `crash`'s to report now — and gains the result of Windows' own memory test beside the time the System log's oldest record carries, so no result says how far back that reaches instead of implying the test was never run.
- `power` counts the log's own start and stop (EventLog 6005 and 6006) among its transitions.
- The MCP instructions lead with the moment: what is up, then the last stops or the stop a moment announced, then the record before it.
- Launches of `powershell.exe` are bounded: a process caps how many it has in flight, and under WSL a slot shared across every process that uses the bridge serializes them machine-wide, a test suite and a live dashboard included (the numbers live where they are defined in `sentinel/bridge.py`). A reading made of several others queues behind the same bridge rather than racing them, which under WSL's interop layer was how a reading came back `unavailable` for reasons that had nothing to do with the machine; a slot held by another process for longer than the launch's own timeout is reported the same way rather than waited for without end.

## [1.0.1] - 2026-09-21

The file looks after itself: it installs, updates and removes itself, says which version it is, and says so out loud when it cannot start.

### Added
- The executable installs and updates itself. Started from anywhere but its installed path it is a download: it copies itself into `%LOCALAPPDATA%\SystemSentinel\` and starts that copy. When a server is already answering with this machine's token it reads the `version` that server reports: equal to its own it just opens the dashboard, older it asks to quit and takes its place, newer it leaves alone and says so in a message box, because a download should not quietly undo an update.
- `POST /api/quit`: the bearer token only — a request carrying just the session cookie is refused, so a browser on any device cannot stop the machine's tool — and only from this machine. It answers `202 {"quitting": true}` and the process exits gracefully within a few seconds, under the tray and under `serve`.
- The version in the file's own properties, where Windows keeps it: `(Get-Item SystemSentinel.exe).VersionInfo.ProductVersion`.
- The tray's version submenu, named for the version it is running: *Check for updates…*, which opens the releases page in the browser and asks the network for nothing itself, and *Remove from this computer…*, which names what goes and what stays, asks once, turns *Start with Windows* off, quits and deletes its home behind itself.
- Failure is visible. A native message box whenever the windowed executable cannot start — the port held, a previous instance still stopping — or a second instance's browser did not answer, naming what happened, the address and where `launcher.log` is. The log rotates now, two backups behind it.
- Continuous integration on the repository, and a release workflow that builds the executable and attaches a build provenance attestation: `gh attestation verify SystemSentinel.exe -R SuperDuperDave/system-sentinel` checks a release from this version on.

### Changed
- The install note's short prompt is one prompt for install and update: it downloads to a temporary path, checks the published SHA-256, compares it with the installed copy and stops there when they are the same file and it is already running, and proves the result by the version the server reports against the version in the file. The long prompt looks for a server on the port before starting a second one. New sections cover updating, removing, which version you have, and a table of what to do about what you see.
- `version` on `GET /api/readings` is documented, and the dashboard's Agents view shows it beside a link to the releases page.
- The dashboard's sign-in screen names both ways in: the tray, which signs a browser in, and `system-sentinel token` from source. It said only the second, which is not the path most people are on.

## [1.0.0] - 2026-09-20

The overhaul. One boundary for three clients; the identity in the tool; nothing that reads the machine outside the API.

### Added
- `sentinel/`, the Python package: a typed bridge to Windows whose outcome is one of `ok`, `empty`, `failed`, `unavailable`, `denied`, `timeout`; the reading envelope with `raw`, `derived`, `invariant` and `inferred` sections and the query that produced it; a catalog of nineteen readings projected to REST, OpenAPI and MCP from one table.
- The access token and the session cookie; every route under `/api/` and the MCP endpoint require one.
- Redaction at the boundary: serial numbers, platform identifiers, the computer name, user names, MAC addresses and profile paths, with what was removed listed on every response; `unredacted` by name.
- The stack on the server: items with rank and verbosity, the prompt library seeded with the six presets, the composed handoff as a route an agent reads; captures of every reading with a manifest that says what is in the ZIP.
- The stream: one query per poll across the presets, `record`, `heartbeat` and `bridge` events, a longer poll while the machine is not answering.
- `storms` computed from the log on demand over wall-clock buckets, idle minutes included; the PCIe fabric read from `pnputil` in a fraction of a second.
- `dashboard/`, rebuilt in Vite and React with CSS modules and the identity (the tokens, Azeret Mono, JetBrains Mono and DM Sans self-hosted, the mark, the graticule in one zone, the phosphor as light only), served from the API's origin; eight views, each starting with the outcome line; phone width first.
- `tests/`: a fake `powershell.exe`, a fake bridge, fixtures for what this machine cannot show, and host tests against the real log.
- `docs/API.md` for agents; `docs/DEPLOY.md`, the prompts a person pastes to their agent: one for the published release, one from source, both run on a clean machine by the harness in `build/windows/sandbox/`.
- The launcher: `SystemSentinel.exe`, one file built by `build/windows/build.ps1`, which starts the server, opens the browser signed in by a one-time code and sits in the tray with Open dashboard, Sign in another device, Copy address for agents, Start with Windows and Quit; the person never reads the token.
- A sign-in link for another device: `POST /api/session/link` asks Tailscale which address it publishes for the port, mints a five-minute one-time code and draws the link as a QR code; the dashboard and the tray both show it.

### Removed
- The FastAPI backend and Next.js dashboard of 0.2.0 (`backend/`, `frontend/`, `start.sh`, `system_prompts.md`), whose queries were kept as the material of the readings. With them: open CORS, the two-port start, the in-memory WHEA store and its watermark, the hard-coded PCIe fixture and driver sentence in the composer, and every path that returned an empty list on failure.

## [0.2.0] - 2025-01-15

### Added
- WHEA storm detection and visualization
- Deep diagnostics with hardware domain analysis (memory, PCIe, power, forensic signals)
- Hardware topology tree with component-level views (CPU, GPU, storage, network, motherboard)
- System constraint analysis
- Context Composer for AI-augmented diagnostics
- Prompt Gallery with 6 specialist diagnostic modes
- Live event streaming via SSE

### Changed
- Expanded backend service architecture with domain-specific analyzers
- Improved frontend dashboard with tabbed deep diagnostics views

## [0.1.0] - 2024-12-01

### Added
- Initial release
- Windows event log collection via PowerShell/WMI
- WHEA error record decoding
- Crash dump inventory
- Basic dashboard with system overview
- Capture pack export
