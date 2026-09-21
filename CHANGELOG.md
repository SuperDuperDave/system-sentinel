# Changelog

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
