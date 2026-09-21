# Changelog

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
