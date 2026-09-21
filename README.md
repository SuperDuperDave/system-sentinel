# System Sentinel

**A stethoscope for your computer.** A Windows machine keeps a record of itself: the event log, the hardware error log, the crash dumps, what it is made of and how it is configured. System Sentinel gathers that record, shows it to you in one place, lets you choose what matters, and hands it on to the AI conversation of your choice. It is built for two users at once: the person at the dashboard, and the agent running on the same machine.

Every read of the machine passes through one boundary, the API. The dashboard, a phone and a local agent (Claude Code, Codex and the like) are three clients of that one interface. An agent troubleshooting the machine calls the tool instead of writing and running its own scripts.

## What it reads

| Reading | What it is |
| --- | --- |
| `events`, `record` | Records from the System and Application logs by level, and the records *before* a moment: the log does not announce a freeze; the next start does |
| `whea`, `storms` | Hardware-error records with their binary payload decoded beside them, and the same records over a window in wall-clock buckets, grouped by signature, with burst and acceleration flags |
| `dumps` | The crash-dump inventory |
| `system`, `hardware`, `hardware.cpu`, `.gpu`, `.board`, `.storage`, `.network`, `drivers` | The snapshot, the fingerprint and configuration, one subsystem at a time, and driver changes |
| `pcie`, `power`, `memory`, `constraints` | The PCIe fabric, power configuration and transitions, physical memory, devices present and not working |
| `signals` | Leads across the readings and the recent log: suppressions, gaps, pressure, transitions, mismatches |

Every reading comes back in one envelope. Its **outcome** says whether the machine was observed (`ok`, `empty`) or not (`failed`, `unavailable`, `denied`, `timeout`), so a collection failure is never mistaken for a clean machine. Its **sections** keep what Windows said (`raw`) apart from what the tool computed (`derived`), what does not change (`invariant`) and what is only a lead (`inferred`). Its **method** is the query, so the evidence can be reproduced by hand. By default nothing in a response carries a serial number, the computer name, a user name or a MAC address; a caller asks for those by name.

The **stack** is the evidence you or your agent chose to hand on. It lives on the machine, so the desktop, the phone and the agent see one stack. It composes into one text, led by a prompt from a library you can edit; the dashboard copies it to the clipboard, and an agent reads the same text from a route. A **capture** is every reading, the stack and the composed text in one ZIP on disk, with a manifest that says exactly what is in it. Nothing leaves the machine unless a person sends it.

## Installing it

Download `SystemSentinel.exe` from [the latest release](https://github.com/SuperDuperDave/system-sentinel/releases/latest) and double-click it, wherever your browser put it. It installs itself: the file copies itself into `%LOCALAPPDATA%\SystemSentinel\` and starts from there. It is the whole tool in one file: the server starts on `http://127.0.0.1:8000/`, the browser opens on the dashboard already signed in, and the mark sits in the system tray with *Open dashboard*, *Sign in another device*, *Copy address for agents*, *Start with Windows*, a submenu named for the version it is running, and *Quit*. Nothing else needs to be installed. [docs/DEPLOY.md](docs/DEPLOY.md) has the details, the two prompts you can paste to your agent instead (one installs the release and updates it, one installs from source), and the optional steps for running at logon and reaching the machine from your phone.

**Updating it.** Download the newer file and double-click it: it replaces the installed copy, asking the running one to quit first. Your token, stack, prompts and captures live in the data directory and are untouched. *Check for updates…* in the tray's version submenu opens the releases page in your browser — the tool asks nothing of anything off this machine.

**Removing it.** *Remove from this computer…* in the same submenu names what goes (the program, the token, the stack, the prompts, the captures, the Startup entry) and what stays (the file you downloaded; an agent's registration, undone by `claude mcp remove system-sentinel`), then does it.

Which version you have is in the file's *Properties*, in the tray's submenu, in the dashboard's Agents view, and as `version` on `GET /api/readings`.

From source, on the Windows machine, with Python 3.11 or newer and Node 22 or newer:

```
git clone https://github.com/SuperDuperDave/system-sentinel.git
cd system-sentinel
python -m venv .venv
.venv\Scripts\python -m pip install -e .
cd dashboard && npm ci && npm run build && cd ..
.venv\Scripts\system-sentinel check
.venv\Scripts\system-sentinel serve
```

`check` proves the bridge to Windows before anything is asked of it. `serve` runs the API and the dashboard; the dashboard asks once for the access token the tool created on first start, which `system-sentinel token` prints. `system-sentinel launch` is the tray launcher from source, with the `[launcher]` extra installed.

From WSL on the same machine the tool runs the same way, reading Windows through `powershell.exe`; that is how it is developed.

## For agents

Register it once and every reading is a tool:

```
claude mcp add --transport http system-sentinel http://127.0.0.1:8000/mcp --header "Authorization: Bearer $(system-sentinel token)"
```

Any MCP client reaches the same address over streamable HTTP with the same header; any shell reaches the same evidence with `curl`. [docs/API.md](docs/API.md) is the interface, written for an agent to read without the source; the live reference is `/api/docs`.

## The source

`sentinel/` is the Python package: the bridge to Windows, the reading envelope and catalog, the readings, the redaction policy, the token boundary, the stack, the stream, captures, the MCP projection and the CLI. `dashboard/` is the Vite and React dashboard, which builds into `sentinel/static` and is served from the same origin. `tests/` runs anywhere through a fake bridge and, where there is one, against the real machine. `docs/design/` owns the identity. `_sessions/` is how the work is worked.

The identity, the copy and the figure on [mainthread.ai](https://mainthread.ai/work/system-sentinel/) are the reference presentation; the page states only what this source implements.

## What it does not do

No live telemetry as a service, no prediction, no crash-dump decoding, no diagnosis. A burst of corrected errors, a gap in the log or a correlated event is a lead to investigate; the reading belongs to the person or the agent holding the evidence.

## License

[MIT](LICENSE). The fonts are under the SIL Open Font License, with their license texts beside them in `dashboard/public/fonts/`.
