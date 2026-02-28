# System Sentinel

**AI-augmented hardware diagnostics platform for Windows systems.**

System Sentinel captures deep hardware telemetry — WHEA errors, crash dumps, event logs, driver history, PCIe topology, power delivery, and memory health — then composes that data into structured context for AI-powered diagnostic analysis.

## What It Does

- **WHEA Storm Detection** — Identifies rapid bursts of hardware error corrections that signal imminent failure
- **Deep Hardware Diagnostics** — PCIe fabric analysis, memory channel mapping, power delivery assessment, CPU/GPU health
- **Hardware Topology Mapping** — Visualizes your system's physical component layout with per-component drill-down
- **Live Event Streaming** — Real-time system events via SSE, monitoring WHEA, crashes, and driver state changes
- **Crash Dump Forensics** — Collects and decodes Windows crash dumps with bugcheck code mapping
- **Context Composer** — Assembles diagnostic data into optimized context packages for AI analysis
- **Capture Pack Export** — One-click ZIP export of all diagnostic data for sharing or archival

## AI Diagnostic Modes

System Sentinel ships with 6 specialist prompt configurations, each tuned for a different diagnostic scenario:

| Mode | Purpose |
|------|---------|
| **Quantum Diagnostician** | Silicon-level physics analysis — voltage domains, cache hierarchy, thermal effects |
| **System Archaeologist** | Temporal pattern tracing — what changed, when, and what broke |
| **Preventative Oracle** | Pre-failure detection — find invisible degradation before it crashes |
| **Emergency Triage** | Crisis stabilization — minimal analysis, maximum action |
| **RMA Prosecutor** | Warranty claim evidence — build an ironclad hardware failure case |
| **Performance Alchemist** | Post-stability optimization — extract maximum capability from stable hardware |

These prompts are designed for use with Claude, ChatGPT, or any capable LLM. Feed the composed context + select a mode.

## Architecture

```
┌─────────────────────┐     ┌──────────────────────┐
│   Next.js Frontend  │────▶│   FastAPI Backend     │
│   (React + Zustand) │ API │   (Python + Win32)    │
│   Port 3000         │◀────│   Port 8000           │
└─────────────────────┘     └──────────┬───────────┘
                                       │
                            ┌──────────▼───────────┐
                            │   Windows System      │
                            │   - Event Log (WMI)   │
                            │   - WHEA Records      │
                            │   - Crash Dumps       │
                            │   - Hardware Topology  │
                            └──────────────────────┘
```

**Backend**: FastAPI serving REST + SSE endpoints. Collects data via PowerShell/WMI on Windows, with graceful fallbacks for development on other platforms.

**Frontend**: Next.js 16 + React 19 + Tailwind CSS 4. Dashboard with sidebar navigation, widget grid, deep diagnostics views, and the Context Composer.

## Quick Start

### Prerequisites

- Python 3.10+ (`python3` on Linux/WSL — install `python3-venv` if missing: `sudo apt install python3-venv`)
- Node.js 18+
- Windows 10/11 (for full hardware data collection; backend runs on Linux/WSL with mock data)

### Run Both Services

```bash
chmod +x start.sh
./start.sh
```

This creates a Python venv, installs dependencies, and launches both backend (port 8000) and frontend (port 3000).

### Run Individually

**Backend:**
```bash
cd backend
python3 -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

Then open [http://localhost:3000](http://localhost:3000).

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+, FastAPI, Uvicorn, Pydantic |
| Frontend | Next.js 16, React 19, TypeScript 5, Tailwind CSS 4, Zustand |
| Windows Integration | PyWin32, PowerShell, WMI |
| WHEA Decoding | DecodeWheaRecord (.NET tool) |

## Project Structure

```
system-sentinel/
├── backend/
│   ├── main.py                 # FastAPI application
│   ├── collectors/             # Data collection modules
│   ├── services/               # Core diagnostic services
│   │   ├── whea/               # WHEA error processing
│   │   └── domains/            # Hardware domain analyzers
│   │       └── hardware/       # Per-component analysis
│   └── tools/                  # External tools (DecodeWheaRecord)
├── frontend/
│   ├── app/                    # Next.js app directory
│   ├── components/             # React components
│   │   ├── views/              # Dashboard view pages
│   │   ├── widgets/            # Dashboard widgets
│   │   └── context/            # Context Composer
│   └── lib/                    # Utilities, stores, types
├── system_prompts.md           # AI diagnostic mode gallery
├── start.sh                    # Launch script
└── backend/start_server.ps1    # Windows PowerShell launcher
```

## Status

This project is under active development. The core diagnostic pipeline and UI are functional. Areas for improvement include:

- Test coverage
- Configuration management
- Documentation for individual diagnostic domains
- Docker containerization
- CI/CD pipeline

Contributions welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
