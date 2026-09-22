# System Sentinel API

The one boundary. The desktop dashboard, a phone and a local agent are three clients of this interface; nothing reads the machine except through it. This document is written for an agent to read without the source. The machine-readable form is the OpenAPI document the server publishes at `/api/openapi.json`; the interactive form is `/api/docs`.

## Reaching it

The server runs on the Windows machine it reads and listens on `http://127.0.0.1:8000` unless told otherwise. Every route under `/api/` and the MCP endpoint at `/mcp` require the access token. The token is created on first start and kept in the data directory:

| Platform | Data directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\SystemSentinel\` |
| Other (development from WSL) | `~/.system-sentinel/` |

The file `token` in that directory holds it. Send it as `Authorization: Bearer <token>`. The dashboard exchanges it once for a session cookie whose value is derived from the token, never the token itself, so a browser or a phone holds something that opens the dashboard and nothing else; agents send the header. A request without a valid token gets `401` with `{"error": "unauthorized"}` and nothing else.

`system-sentinel token` prints it. `system-sentinel check` tells you whether the bridge to Windows works before you ask for anything.

The launcher signs a browser in without showing anyone the token: `GET /api/session/open?code=<one-time code>` accepts a code signed with the token, once, before the code runs out, and only for a connection made on this machine (a loopback client, or a connection accepted on the loopback listener, which is how a private-network proxy such as Tailscale serve reaches the tool). It sets the session cookie and redirects to the dashboard, or to the dashboard's sign-in link for another device with `&to=link`, the one value that lands anywhere but the dashboard itself. Agents have no use for it; they send the header.

`POST /api/session/link` is how a person already signed in hands the dashboard to a second device. It asks this machine, through the bridge, which https address Tailscale publishes for the port the request arrived on, and answers with that address, a link carrying a fresh one-time code, when that code expires, and a QR code of the link as SVG. The code lasts five minutes and is spent by the first device that follows it; the token is never in the answer. `outcome` keeps three things apart: `ok` when there is an address, `empty` when the machine answered and publishes nothing for this port (`installed` says whether Tailscale is there at all, `port` is the port it would have to publish, and `detail` names the command that would), and `unavailable` or `failed` when it could not be asked at all. `url`, `expires_at` and `qr` are null unless `ok`. It needs the session cookie or the bearer header like every other route and nothing more, because whoever is signed in may sign in another device. Agents have no use for this one either.

`POST /api/quit` stops the tool. It is the one route the session cookie does not open: it takes the bearer token, and a request carrying only the cookie is refused with `401` and a body saying the token is required, so a browser on a phone cannot shut down the machine's tool. It is also refused unless the connection came from this machine, on the same terms as `/api/session/open`. It answers `202 {"quitting": true}` and the process exits gracefully within a few seconds, under the tray launcher and under `serve`, though not under `serve --reload`, where the reloader owns the process. It is not an MCP tool: stopping the tool is not a reading of the machine, and an agent that means to stop it has the route.

### Claude Code

```
claude mcp add --transport http system-sentinel http://127.0.0.1:8000/mcp --header "Authorization: Bearer $(system-sentinel token)"
```

### Codex

```
codex mcp add system-sentinel --url http://127.0.0.1:8000/mcp --bearer-token-env-var SYSTEM_SENTINEL_TOKEN
```

Codex takes a bearer token from the environment rather than from a header, so `SYSTEM_SENTINEL_TOKEN` must hold the token in the environment Codex starts in.

### Other MCP clients

Streamable HTTP at `http://127.0.0.1:8000/mcp` with the same header. Every reading below is one tool, named as the reading is named, except that a dot becomes an underscore because MCP tool names allow only letters, digits, underscore and hyphen: `hardware.cpu` is the tool `hardware_cpu`. The stack, the composer and the captures are tools too: `stack_list`, `stack_add`, `stack_update`, `stack_remove`, `stack_clear`, `stack_prompt`, `compose`, `prompts_list`, `capture_create`, `capture_list`. Every tool is annotated with what it does to the machine, so a client can stop confirming the readings and keep confirming `stack_remove` and `stack_clear`; every reading answers with typed structured content beside its text, against one shared schema for the envelope. The six presets are also MCP prompts, and the catalog and the composed handoff are resources (`sentinel://catalog`, `sentinel://handoff`). Asking for `unredacted` requires a `reason`, which is carried into the answer's warnings.

Clients that support `subscriptions/listen` can subscribe to `sentinel://handoff`. A stack change from the dashboard or an MCP tool sends a resource-updated notification; read the resource again for the new handoff. The listen request is an event stream; ordinary MCP calls still return one JSON response.

### Without MCP

`curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/readings/events` works from any shell. Prefer this over writing your own PowerShell: the reading carries provenance, the outcome and the redaction, and it is the same evidence the person sees.

### Day two

| Question | Answer |
| --- | --- |
| What is running? | `version` on `GET /api/readings`: the version of the process answering right now. |
| What is installed? | `(Get-Item "$env:LOCALAPPDATA\SystemSentinel\SystemSentinel.exe").VersionInfo.ProductVersion`. It differs from the version above only while an older copy is still holding the port. |
| How do I update it? | Run the short prompt in [DEPLOY.md](DEPLOY.md) again; it is one prompt for install and update. The file it downloads installs itself and asks an older running copy to quit. Nothing in the tool looks for a release on its own, here or anywhere. |
| How do I stop it? | `POST /api/quit` with the bearer header, or *Quit* in the tray. |
| How do I remove it? | *Remove from this computer…* in the tray, or by hand: *Start with Windows* off, quit, delete the data directory, `claude mcp remove system-sentinel`. [DEPLOY.md](DEPLOY.md) ("Removing it") has the commands and says what stays. |

### Exact integers across clients

Evidence uses JSON numbers for integers from `-9007199254740991` through `9007199254740991`. Integers outside that range travel as **canonical decimal strings**, for example `"134100000000000001"`, so JavaScript clients preserve every digit. This applies consistently to API responses, MCP text and structured answers, SSE frames, the CLI health reading, JSON evidence in composed handoffs and newly created capture JSON members. Booleans, floating-point measurements and existing strings keep their types; this convention does not make a floating-point measurement exact.

A large `RecordId` and every derived reference to it use the same string form. Keep it as an identifier or parse it with an arbitrary-precision integer type; do not pass it through JavaScript `Number`. Stack selections accept these decimal-string record IDs when unique within the reading; a reading spanning logs also accepts `Log:RecordId` (for example `System:42`) to identify one exact record. Machine collection, derivation, stream cursor comparisons and existing Python persistence retain exact integers. Redaction runs before this output conversion, including for sensitive numeric fields.

Existing stack evidence is converted when served or exported, without rewriting its saved file. Older capture ZIPs remain as originally written. Values already rounded by another client cannot be reconstructed; take a fresh reading when the exact value is needed. The current representation preserves exact values across clients but does not preserve a distinct JSON numeric type outside the safe range.

## The reading

Every reading is one request for evidence, returned in one envelope. Most ask Windows through the bridge; `performance_history` reads samples stored locally by the app. The envelope is the contract: a collection failure is distinguishable from no findings, and raw evidence is distinguishable from what the tool computed.

```json
{
  "reading": "events",
  "params": { "log": "System", "levels": [1, 2], "count": 50 },
  "asked_at": "2026-09-20T18:04:11.204Z",
  "took_ms": 812,
  "outcome": "ok",
  "method": { "kind": "powershell", "query": "Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2} -MaxEvents 50" },
  "count": 50,
  "sections": [
    { "name": "records", "class": "raw", "data": [ ... ] }
  ],
  "error": null,
  "warnings": [],
  "redacted": ["host", "user"]
}
```

**`outcome`** is one of:

| Outcome | Meaning |
| --- | --- |
| `ok` | The query ran and returned at least one record. |
| `empty` | The query ran cleanly and matched nothing. This is a finding: the log holds no such records. |
| `failed` | The query ran and Windows or PowerShell reported an error. `error` says what. |
| `unavailable` | The bridge to Windows is not there: `powershell.exe` was not found or did not start. Nothing about the machine can be concluded. |
| `denied` | Windows refused (access denied, or a log that needs elevation). |
| `timeout` | The query did not finish within its limit. |

Only `ok` and `empty` say the requested source was observed. For `performance_history`, `empty` means the local store held no samples in that window; it says nothing about what the machine was doing then. Treat the other four as "not observed".

A collector that returns one structured object must actually return exactly one object. Missing, scalar or multiple-object output yields `failed`, a null count and no evidence sections; the adapter never chooses the first of several answers or substitutes an empty object. An object containing legitimately empty collections remains valid, and the reading determines its outcome from those collections. List collectors can still return observed empty lists or multiple rows.

**`sections[].class`** is one of:

| Class | Meaning |
| --- | --- |
| `raw` | Fields returned by the reading's source, usually Windows, sometimes projected and bounded by the reading; `performance_history` returns exact local sample rows. Default redaction applies. |
| `derived` | Computed from raw by a stated rule (a count, a bucket, a signature, a decoded structure). The section names its inputs and rule in `basis`. |
| `invariant` | A fact established as stable across readings; do not use for a current inventory that can change. |
| `inferred` | A lead: a pattern the tool noticed that a person or an agent should investigate. Never a diagnosis. `basis` names the rule. |

**`method`** is how the reading was taken: a bridge and query text, or a local source for stored history. A reading built from several queries lists them.

**`warnings`** name partial work inside an otherwise observed reading: a Windows sub-query that failed, truncated returned rows, or a malformed stored sample that was skipped.

**`redacted`** lists what the default redaction removed from this response. By default a response carries no serial number, computer name, user account name, MAC address or network address (IP, gateway, DNS), and paths under a user profile read `C:\Users\<user>\...`. Fields are redacted by name wherever they appear, and the machine's own names are replaced inside text once learned, so the default holds even when the names could not be learned. Pass `?unredacted=true` (or the `unredacted` argument in MCP) to receive them; do that only when the reader has a reason, such as a warranty claim. Message text is kept where the reading collects it. Device instance identifiers are kept in the existing hardware readings to tell PCIe endpoints apart; `changes` excludes them and full event messages from its Kernel-PnP projection because those values can contain a serial.

## The catalog

`GET /api/readings` lists every reading with its description, classes, parameters and their defaults, what it may carry that redaction removes, and whether it is heavy, and beside the list a `version`: the tool's own, which is how a caller tells what is answering. The live catalog is authoritative; this is the catalog as designed, with each reading's sections.

| Reading | What it reads | Sections (class) | Parameters |
| --- | --- | --- | --- |
| `health` | Whether the bridge works: PowerShell found, its version, a trivial round trip, decoder present, data directory, and how questions are reaching the machine (the transport, sessions alive and idle, questions answered, sessions discarded by reason, the oldest session's age, questions that fell back to a launch, and the last session-start failure category) | `bridge` (raw) | |
| `events` | Records from a Windows log by level, and from a moment or this session's start | `records` (raw) | `log` (System, Application), `levels` (1 critical, 2 error, 3 warning, 4 information; default 1,2), `count` (default 50), `since` (an ISO timestamp or the word `boot`; empty for the most recent records) |
| `record` | The log around a moment: the records before a timestamp, oldest first | `records` (raw) | `before` (ISO timestamp, required: `422` without it), `count` (default 50), `log` (default System) |
| `crash` | The stops the machine did not plan, each composed from the records of its session: when it stopped as Windows estimated it, when it started again, the bug check if one was written, the dump that belongs to it, and the last System record before the next start | `records` (raw, from both logs, each carrying `Log`), `decoded` (derived: one entry per record, its named fields and the bug check where it carries one), `stops` (derived: one entry per stop, newest first, or forward from `moment`; `basis` states the rule that composed it, and `count` on the envelope is how many stops there were), `collection` (raw event-log query outcomes, bounds and supporting lookup results) | `count` (default 5, 1 to 20), `moment` (an ISO timestamp; the first start at or after it is reported instead) |
| `changes` | Windows Update installation results, device configuration and MSI installation or removal results before a moment; these are leads, not causes | `records` (raw safe field projection, oldest first), `changes` (derived event meanings with exact log and RecordId references), `summary` (derived counts of returned kinds), `collection` (raw per-source outcome, cap, truncation and log retention evidence), `coverage` (derived reach and completeness) | `before` (ISO timestamp, empty for now), `hours` (default 168, 1 to 2160), `count` (most recent per source, default 100, 1 to 500) |
| `faults` | What went wrong while the machine kept running: the programs that crashed or hung, and the kernel's own live reports (a GPU timeout, a watchdog) | `records` (raw, with `Log`), `decoded` (derived: the application, module, exception and supported process facts with per-field validity; a live kernel report is one entry however many records it was written across), `summary` (derived: by kind, by application, by live kernel report) | `count` (default 30, 1 to 500), `since` (an ISO timestamp or the word `boot`) |
| `whea` | WHEA-Logger records with their binary payload, each decoded beside it | `records` (raw, with `RawData` hex), `decoded` (derived: one entry per record, the decoder's structure or its error). The fixed CPER header and section bounds are checked before the external decoder runs. A malformed or unreadable record has a per-entry error; a missing decoder also adds a reading warning. The outcome stays the log's, because the records were observed either way | `count` (default 30) |
| `storms` | WHEA records over a window in wall-clock buckets, grouped by signature, with burst and acceleration flags | `buckets` (derived, includes empty minutes), `signatures` (derived), `status` (inferred: quiet, burst or accelerating, with the rates and the reason) | `hours` (default 24), `bucket_seconds` (60), `burst_threshold` (5), `accel_threshold` (2.0) |
| `dumps` | System dumps and default current-user application dumps | `files` (raw: name, path, bytes, modified, source), `collection` (raw: per-location outcomes and gaps), `inspection_targets` (derived selectors from the shared service) | |
| `dump_header` | A bounded structural read of one inventoried dump: its exact header bytes and, according to format, a kernel bug check or a stream minidump's directory and fixed exception and system metadata | `file` (raw metadata), `header` (raw bytes, values and offsets), `streams` (raw MDMP directory entries and sampled metadata bytes, when present), `inspection` (derived: format, bug check or exception, stream counts, and limits), `collection` (raw inventory coverage), `selection` (derived reference semantics when resolved) | Exactly one of `ref` (from `dumps.inspection_targets`) or `path` (exact unredacted path from inventory) |
| `system` | The snapshot: OS, build, boot time, uptime, processor load, memory | `snapshot` (raw) | |
| `hardware` | The selected current hardware inventory and configuration | `fingerprint` (derived: first processor and board, selected display adapter, BIOS, and disk index 0; `disk0_model` does not establish the Windows boot disk), `config` (raw), `risks` (inferred: observations such as Secure Boot off, never advice) | |
| `hardware.cpu` | Processor and platform detail | `raw`, `derived` | |
| `hardware.gpu` | Display adapters and driver | `raw`, `derived` | |
| `hardware.board` | Board and firmware | `raw`, `derived` | |
| `hardware.storage` | Disks, volumes, SMART where exposed | `raw`, `derived` | |
| `hardware.network` | Adapters and connectivity | `raw`, `derived`; if Windows cannot assemble IP configuration, the adapter inventory remains observed, a warning names the gap, and each adapter's `ip` is `null` rather than an empty address list | |
| `drivers` | Current signed driver inventory, sorted by the driver's authored date rather than installation time | `drivers` (raw) | `count` (default 30) |
| `pcie` | The PCIe fabric | `endpoints` (raw), `roots` (raw), `groups` (derived: endpoints sharing a root) | |
| `power` | Power configuration and transitions | `raw`, `derived` | |
| `memory` | Physical memory and stability signals | `raw`, `derived` | |
| `constraints` | Configured limits and their sources | `raw`, `derived` | |
| `reliability` | Windows' reliability related events, including informational entries such as successful updates, and its hourly stability index | `records` (raw), `stability` (raw), `days` (derived: per UTC day the last and lowest reported index, and returned records by source and event ID), `collection` (raw: queried UTC window and each source's outcome, available and returned counts, limit and error) | `days` (default 30, 1 to 366) |
| `load` | One fresh numeric-only aggregate processor, memory and physical-disk snapshot; an unavailable counter stays null | `snapshot` (raw) | |
| `performance_history` | Stored local samples, including gaps across sleep, shutdown or collection failure; it does not infer what happened inside a gap | `samples` (raw), `shape` (derived: per-metric min/median/peak and first/last sample), `collection` (raw settings and last attempt) | `hours` (default 24, 1 to 48), `end` (ISO timestamp with offset; blank for now) |
| `processes` | Fresh Windows formatted per-process counters, bounded to 2,048 instances; Idle and `_Total` are excluded | `snapshot` (raw time, logical processor count, total/returned/omitted counts), `processes` (raw PID, instance name, processor time, private working set, private bytes, process I/O, handles, threads), `leaders` (derived: top returned CPU, memory and I/O users) | |
| `signals` | Forensic signals across the readings and the recent log | `signals` (inferred: suppressions, gaps, pressure, transitions, mismatches; `basis` names the inputs) | |

`reliability.collection` identifies the inclusive query window as `window_start` and `window_end`, and reports `records` and `stability` independently. Each source has `outcome` (`ok`, `empty`, `denied` or `failed`), `available` (matching rows before the response limit, or null when unobserved), `returned`, `limit` and `error`. The envelope is `empty` only when both sources answered without history. If no history is available and either source failed, the envelope is unobserved; useful data from a surviving source remains `ok` with warnings. When the event source failed, the envelope's `count`, the daily `records` and `event_types`, and the aggregate `sources` are null. The stability index remains usable, and any index-fall signal preserves those unknown event counts. Counts describe returned rows; compare `available` with `returned` to see whether the event list was limited.

`changes` uses the interval `[before − hours, before)`, or the Windows host's current time when `before` is empty. The end is rounded down to the millisecond that Windows Event Log XPath compares, and `collection.window_start`/`window_end` report those actual bounds. Three service-filtered event-log queries answer independently: Windows Update 19 is an installation success and 20 a failed attempt; Kernel-PnP 400 is device configuration, which does **not** establish a driver update; MSI 1033/1034 are installation/removal results, whose status distinguishes success from failure; status 1641 means a restart was initiated and 3010 means one is required. MSI does not cover every installer or Store app. The raw `records` section retains each event's log, RecordId, timestamp, provider, ID, version and only fields the reading can project safely. `FieldCount` and `OmittedFieldCount` make its projection limit explicit. It omits messages and device instance identifiers from the Configuration log, because those can include serials. `changes` derives one labeled timeline entry per returned record and cites `{log, record_id}`; an unsupported layout keeps its projected raw row and an entry error. No event's proximity to a crash establishes a cause. [Microsoft's Windows Installer event reference](https://learn.microsoft.com/en-us/windows/win32/msi/event-logging) documents the MSI field positions; its [Windows Update troubleshooting example](https://learn.microsoft.com/en-us/troubleshoot/windows-server/installing-updates-features-roles/troubleshoot-windows-update-error-0x80070002) shows event 20 as a failed installation.

`changes.collection` gives `windows_update`, `device_configuration` and `msi` separate outcomes, returned counts, per-source limits and exact `truncated` flags. Each source also says whether its log was enabled, its mode and oldest retained record when readable. The derived `changes.coverage` section gives each source a `covered_from` boundary and `complete` (`true`, `false`, or null when unobserved). `covered_from` is the earliest supported boundary in the requested interval, accounting for retained history and any response truncation; it is null when log state or retention cannot support that bound. `covered_from_inclusive` is false after truncation, because other events may share the oldest returned timestamp; it is null when the boundary is unknown. `complete` requires an observed query, an enabled circular log retained from the requested start, and no returned-record truncation. This is a bound on the **retained log**, not proof that Windows emitted every relevant event. A source that failed is never an observed empty source. The envelope is `ok` with partial-source warnings when at least one record survived, `empty` only when all three queries answered with no records, and otherwise `failed` or `denied` with a null count. Even an `empty` reading can have a retention warning: inspect `coverage` before treating the whole requested interval as quiet.

`GET /api/readings/{name}` takes the reading. Parameters are query parameters; a name the reading does not take is refused with `422` rather than ignored, so a misspelled parameter cannot read the wrong evidence with a clean outcome. Heavy readings (`changes`, `dump_header`, `hardware.*`, `pcie`, `power`, `memory`, `reliability`, `signals`, `load`, `processes`) are loaded on demand; `changes` is currently available through the API and MCP without a dashboard view. `processes` runs on opening Performance and while it remains open; `load` runs only when the fresh-snapshot button is pressed.

The app samples `load` in the background by default at 60-second intervals whether a dashboard is open or not. `GET /api/performance/collection` reports `settings` (`enabled`, `interval_seconds`, `config_error`), `last_attempt` (`at`, `outcome`, `took_ms`) and `retention_days`. `PUT /api/performance/collection` accepts `{"enabled": false, "interval_seconds": 60}` to pause; set `enabled` true to resume and choose an interval from 60 to 600 seconds. `DELETE /api/performance/history` removes all retained sample day files and resets the last-attempt status without changing the enabled setting. All three routes require the same authentication as readings. Stored rows under the local data directory's `performance/` folder are JSONL with UTC timestamps and numeric fields only, bounded to 30 calendar days and 512 KB per day. A malformed line is skipped with a reading warning; failure to read the store gives `unavailable`, not an empty history. No sampling data is uploaded.

`processes` is requested when the Performance view opens and refreshes once per minute while it stays open. It is never part of the background JSONL collector. Its `cpu_core_percent` is Windows' Process counter on a one-processor scale: 100% is one logical processor, and a multithreaded process can exceed 100%. Where Windows returned a logical processor count, `leaders.cpu[].logical_capacity_percent` divides by that count as a separately labeled estimate. `private_working_set_bytes` is resident memory private to the process; `private_bytes` is a different committed counter. `io_bytes_per_sec` is process I/O, which can include activity beyond disks. The counter instance name can carry a `#` suffix; PID distinguishes simultaneous instances but can be reused after exit. The reading does not ask for executable paths or command lines. If the response limit is reached, the script preserves high users in all three ranking dimensions and reports omitted rows; rankings never claim to describe those omitted processes. Microsoft documents the [formatted WMI counters](https://learn.microsoft.com/en-us/windows/win32/wmisdk/accessing-wmi-preinstalled-performance-classes) and [multi-processor Process counter](https://learn.microsoft.com/en-us/windows/win32/perfctrs/collecting-performance-data).

`dumps` checks Minidump, MEMORY.DMP and LiveKernelReports under Windows' actual `SystemRoot`. Each `collection.locations` entry names its path, recursion, `present` (boolean or unknown), `outcome`, returned file count and bounded error details; `error_count` preserves the full error count when only the first 20 details are returned. `collection.complete` means all locations answered. A missing location is an observed empty result; a denied or failed directory walk keeps any files already returned. Useful files yield `ok` with warnings when coverage is incomplete. With no files, a collection failure yields `failed` or `denied` with a null count; only a complete empty inventory yields `empty`. This coverage also appears at `crash.collection.dumps`, without changing the outcome of its primary event-log queries. Each stop carries `dump_inventory_complete`, qualifying a missing match or the newest returned time candidate when some locations could not be read. An unmatched reported dump path carries `inventory.outcome` and `inventory.detail`: whether its location could be checked, or whether a current file exists but was not matched to that historical stop.

`dumps` also searches the default current Windows process user's `LocalApplicationData\CrashDumps` directory, directly and without recursion, as location `application`. File rows retain `name`, `path`, `bytes` and `modified`, with `source` naming the inventory location. This does not discover custom WER destinations, other accounts, service profiles or WER archives, and it does not enable dump recording. Windows documents the [default and configurable user-mode dump locations](https://learn.microsoft.com/en-us/windows/win32/wer/collecting-user-mode-dumps). Application files are excluded from `crash`'s kernel-only inventory and stop matching.

The application source resolves its folder without creating or verifying it first, checks for a canonical fixed-drive path, and checks ancestors and candidates for reparse points before following them. Unsupported or redirected locations are collection failures, not observed empty directories; ordinary files found beside rejected entries remain available with incomplete coverage. These pathname checks are not an adversarial filesystem sandbox: concurrent changes, DOS aliases and hard links are not excluded by the policy. The same guard runs again before opening an application file.

**Selecting a dump with privacy preserved.** The shared API/MCP service adds derived `inspection_targets` to `dumps`: each target has `file_index` (zero-based position in the returned raw `files` array), `source`, and an opaque `ref`. Use `dump_header(ref=...)` even when the displayed path or filename was redacted. References are keyed to the running service instance and reveal no path; no key enters a query, capture or response. They select the **current file at that location**, not saved bytes or an immutable version. They remain stable within the instance; after restart or an unknown reference, refresh `dumps` and select again. Historical stack/capture evidence remains readable after its reference expires.

Malformed references, another instance's reference, both selectors or neither selector are request errors. A valid reference is resolved against a fresh inventory; the header collector then checks its own fresh inventory before opening. Both queries and their combined bridge time are returned. A missing match under an observed source yields `empty`; an unreadable requested source yields `denied` or `failed`. Unrelated source failures stay warnings. A file may change or disappear between queries. Direct unredacted `path` calls remain supported; passing a redacted placeholder is refused with instructions to use a reference. Standalone collectors and benchmarks retain direct-path access; session references belong to the shared service used by API, MCP, stack reads and captures.

`dump_header` opens only a file already found in the Windows dump inventory. It reads the first 96 bytes and returns them in hex with the offsets of interpreted fields. `PAGE`/`DU64` yields the recorded 64-bit kernel bug check and parameters. `MDMP` yields its 32-byte header and up to 128 directory entries (1,536 bytes), plus bounded fixed exception and system metadata, a thread count and up to 128 module records with at most 512 UTF-16 bytes per module name. Every sampled fixed record and its file offset is in the raw sections; module names are returned as text so the normal path redaction applies. The derived `inspection` names the exception, system and any loaded module range containing the exception address. A range match locates code; it does not establish a cause. The Crashes detail shows the interpretation first, with the raw readout in place; a stacked dump starts with the interpretation and keeps the raw sections available when expanded to full. Directory entries that point outside the file, a changing file, a zero-byte file, a missing sample and a directory over the limit are reported explicitly. The raw `collection` preserves the inventory coverage on both successful and unsuccessful inspections. `empty` means no exact match was returned and the requested location was observed (or the path lies outside the inventory scope). If that location could not be fully read, the inspection returns `denied` or `failed` and file presence remains unknown. An unrelated location failure does not hide an observed missing match. Windows may also list a file but deny opening it. Inventory matching is case-insensitive and exact; path normalization is used only to classify coverage, never to authorize opening a file. A normal process may encounter denial under `C:\Windows\Minidump`. This structural read does **not** validate the whole file, unwind a stack, read module contents or memory streams, or identify a cause. No debugger runs and no symbols are fetched. Microsoft documents the [DUMP_HEADER64 fields](https://microsoft.github.io/windows-docs-rs/doc/windows/Win32/System/Diagnostics/Debug/struct.DUMP_HEADER64.html), [MINIDUMP_HEADER](https://learn.microsoft.com/en-us/windows/win32/api/minidumpapiset/ns-minidumpapiset-minidump_header), [stream directory](https://learn.microsoft.com/en-us/windows/win32/api/minidumpapiset/ns-minidumpapiset-minidump_directory), [module list](https://learn.microsoft.com/en-us/windows/win32/api/minidumpapiset/ns-minidumpapiset-minidump_module_list), and [DumpChk's fuller validation](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/dumpchk).

Records from a log (`events`, `record`, `whea`, `crash`, `faults`, the stream) share one shape: `RecordId`, `Id`, `Level`, `LevelDisplayName`, `ProviderName`, `ProviderId` (canonical GUID text or null), `Version` (integer or null), `MachineName`, `TaskDisplayName`, `TimeCreated` (UTC, ISO), `Message`, `Properties` (the event's data, binary values as hex). A reading that asks two logs in one query adds `Log` to it, so a record says which log it came from: `crash` and `faults` carry it, the readings that ask one log do not.

### Application process interpretation

Application Error 1000 and Application Hang 1002 decoded entries include `process`: nullable `id` and `created_at`, exact decimal `creation_filetime`, `creation_resolution_ns`, independent `id_status`/`creation_status` and reasons, `source`, and `warnings`. Status is `ok`, `absent`, `malformed` or `unsupported`. A bad start value cannot erase a valid PID, and a bad PID cannot erase a valid start. The original properties and existing named fields remain available.

Normalization requires the supported provider GUID, integer event ID and version, and a property array with the exact supported count:

| Provider | GUID | Event / version | Properties | PID / creation field |
| --- | --- | --- | --- | --- |
| Application Error | `a0e9b465-b939-57d7-b27d-95d8e925ff57` | 1000 / 0 | 15 | `ProcessId` / `ProcessCreationTime` |
| Application Hang | `c631c3dc-c676-59e4-2db3-5c0af00f9675` | 1002 / 0 | 10 | `ProcessId` / `StartTime` |

`source` retains the record's provider/event/version and, for a supported layout, each field's name and zero-based property index, plus the creation encoding and its interpretation basis. Missing historical metadata, another version or a shorter/appended layout gives unsupported process facts. This bounded mapping trusts the publisher's layout identity; it does not detect same-count reordering under an unchanged identity, authenticate the record or query a provider template on every reading.

Start values are interpreted using Windows [process creation FILETIME](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes): units of 100 nanoseconds since 1601 UTC. The supported Error message names the application start; Hang's message does not describe its `StartTime`, so its per-entry basis states the weaker support. `created_at` preserves seven fractional digits using integer arithmetic. `creation_resolution_ns: 100` describes the encoding, **not clock accuracy**. These are interpreted process facts, not proof that a file belongs to a particular fault; PID reuse and dump timestamp precision still matter.

Exact integers, canonical unsigned decimal strings and explicit `0x` hexadecimal strings are accepted. Booleans, floats, negative values, ambiguous bare hex and values outside their unsigned 32/64-bit range are refused. Null/empty values are absent. A zero PID/start is unsupported with an explicit reason; no undocumented sentinel meaning or 1601 creation date is asserted. Valid ticks outside the supported calendar range retain `creation_filetime` while the date remains unsupported. A start later than a valid event timestamp is kept with a warning, since clock changes or the source values can need investigation. Hang `TerminationTime` remains an uninterpreted raw value.

### Minidump process metadata

For `MDMP` stream 15, Sentinel reads only the 24-byte common prefix of [MINIDUMP_MISC_INFO](https://learn.microsoft.com/en-us/windows/win32/api/minidumpapiset/ns-minidumpapiset-minidump_misc_info), including larger versions of the structure. The full declared stream must fit within the file, and `SizeOfInfo` must be between 24 and the directory entry's declared size. Raw `streams.entries[].misc_info` retains the six integer fields, their file offsets and field-use flags beside the exact sample bytes. `inspection.process` is null if no valid record was read; otherwise it contains nullable `id`, `created_at` (UTC) and `creation_precision_seconds`. Flag `0x1` permits the process ID; flag `0x2` permits process creation time and CPU times. Unused bytes never become process facts. A valid creation time has one-second precision; its exact epoch-seconds value and the user/kernel CPU seconds remain in the raw metadata. These recorded values can support comparison with other evidence, but PID reuse and timestamp precision prevent treating the pair as an authenticated or unique process identity. No automatic fault association is performed.

For `MDMP`, the raw `streams.entries` list includes every directory entry even when its fixed metadata was not sampled. A later stream of a type already sampled has `sample_status: skipped_duplicate`; that planned limit is distinct from an unavailable or incomplete read. Crashes presents this list with each declared range and read status before the exact raw file readout.

## The moment

"It froze at 02:14." The log does not announce a freeze; the next start does, and the facts that name one stop are spread over four records in two logs and a file on disk.

`crash` is the call to reach for first. It fetches them in one launch and composes one stop per session: when the machine stopped as Windows estimated it (read from EventLog 6008's binary value, not from its locale text), when it started again, the bug check if one was written, the dump on disk that belongs to it and how it was matched, and the last System record before that start. The record may be later than the estimated stop time; it supplies context but does not by itself establish cause. Given `moment`, it reports what the first start at or after that moment announced. When both log queries are complete, a clean start there is an `empty` answer with a warning naming the start. If that start cannot be established or classified, a warning states the gap and any surviving stop or report evidence remains available.

The raw `crash.collection` section reports the primary `system` and `reports` queries independently: `outcome` (`ok`, `empty`, `denied`, `failed`), `returned`, `limit`, `bound_reached` and `error`. A reached bound means the result may be incomplete; no total matching count is claimed. For an unobserved query, `returned` is zero and `bound_reached` is null. An envelope with surviving stops is `ok` with warnings for failed sources. With no stops, either primary query failing makes the envelope unobserved with `count: null`; `empty` requires both queries to have answered, within their stated bounds. `collection.before` records each attempted supporting lookup by `anchor` RecordId and `at` timestamp, with its outcome, returned count and error. Those lookup failures preserve the primary stop evidence. This section covers the event-log queries; it does not certify dump-inventory completeness.

Each stop carries: `started_at` (the start that announced it), `announced_at` (the Kernel-Power 41), `stopped_at` (Windows' own estimate, from the 6008), `reported_at` (when Windows Error Reporting filed the report, including a report without a returned System session), `down_seconds`, `bugcheck` (`code`, `name` or null when the table does not know it, `parameters`, `source`: which record named it, `bucket`: the failing module when WER analysed the dump), `no_bugcheck_recorded` (true only for code 0 with no bug check in complete primary queries; null when query gaps or bounds prevent that absence finding), `power` (the 41's fields that say what kind of stop it was), `dump` (`name`, `path`, `bytes`, `modified`, `matched_by`: `1001`, `report` or `time`; a file not returned by inventory keeps its reported path and loses its size), `last_record_before` (the last System record before the next start, including the first line of its message) with `quiet_seconds` (time from that record to the next start), `last_record_collection` (the matching supporting query result, `outcome: not_requested` for a stop with no lookup anchor, or `not_returned` when an expected lookup result is missing), and `records`: the record ids the stop was composed from, for the stack. Missing System rows alone do not prove that retention erased a report's session.

Then `record` with `before` set to that stop's `started_at` returns System records before the next start, oldest first — the same mechanism the dashboard offers as *The record before this*. Inspect their timestamps against the stop estimate before drawing a conclusion. `faults` with `since` set to the stop covers what went wrong afterwards while the machine kept running; `whea` and `storms` cover the hardware errors around it; `signals` says what the tool noticed across all of them.

## The stream

`GET /api/stream` is server-sent events, polled from the logs every few seconds with one query per poll.

| Event | Data |
| --- | --- |
| `record` | `{ "log": "System", "record": { ...the record shape... } }` for each new record matching the tool's presets: crash and power (Kernel-Power 41, EventLog 6008, WER 1001, volmgr 46, disk 161 and 162), WHEA (1, 17 to 20, 46, 47), storage (7, 11, 51, 55, 57, 129, 153), driver and service (219, 7000 to 7034, 10110, 10111), application crashes (1000 to 1002), TPM (1796, 1801) |
| `heartbeat` | `{ "at": "...", "cursors": { "System": 307379, "Application": 88120 } }` on every poll |
| `bridge` | `{ "outcome": "failed", "error": "..." }` when a poll did not observe the machine, with the reading vocabulary |

A silent stream is not a healthy machine; a stream with heartbeats and no `bridge` events is. Records arrive redacted unless the stream was opened with `?unredacted=true`. While the machine is not answering, the poll backs off to every twenty seconds and says so with a `bridge` event each time.

## The stack

The stack is the evidence a person or an agent has chosen to hand on. It lives on the server so the desktop, the phone and the agent see one stack.

An item:

```json
{
  "id": "…",
  "added_at": "2026-09-20T18:10:02.000Z",
  "kind": "reading",
  "title": "Critical and error records, last 50",
  "rank": 3,
  "verbosity": "full",
  "reading": { …the envelope as it was read… },
  "ids": null,
  "note": null
}
```

| Field | Meaning |
| --- | --- |
| `kind` | `reading` (a whole reading), `selection` (some records chosen by `RecordId` or `Log:RecordId`, or signals chosen by string signal ID, in `ids`), `note` (text the person or agent wrote) |
| `rank` | 1 first to 5 last in the composed handoff; default 3 |
| `verbosity` | `summary` (a compact record table; for `changes`, interpreted entries with source outcomes and coverage; or signal identity, title, summary and source readings) or `full` (selected records or signals with evidence as JSON; a `changes` selection puts its interpreted entries before exact raw rows); default `full` |
| `reading` | The envelope, kept as it was at the moment of adding: its `asked_at`, `outcome` and `method` are the item's provenance |

| Route | Does |
| --- | --- |
| `GET /api/stack` | `{ "items": [...], "prompt_id": "...", "system_prompt": true }` |
| `PATCH /api/stack` | Change `prompt_id` or `system_prompt` |
| `POST /api/stack/items` | Add an item. Body: `kind`, optional `title`, `rank`, `verbosity`, `ids`, `note`, and either `take: { "name": "...", "params": {...} }` (the server takes the reading now) or `envelope: { ... }` (a reading the caller already holds, stored as given). A selection needs distinct IDs present in that reading: `RecordId` for a record unique within the reading, `Log:RecordId` when records from different logs share a number, or string `id` for a signal. Ambiguous numeric selections are refused; an older saved selection with that ambiguity renders a warning instead of including both logs' records. The composed signal selection keeps the signal section's basis and, at full verbosity, its rule evidence. Adding the same reading with the same parameters and the same `ids` twice is refused with `409`; a later `signals` observation, whole or selected, is a new snapshot. |
| `PATCH /api/stack/items/{id}` | Change `rank`, `verbosity` or `title` |
| `DELETE /api/stack/items/{id}` | Remove one |
| `DELETE /api/stack` | Clear |
| `GET /api/stack/composed` | `{ "text": "...", "items": 4, "redacted": [...] }`: the handoff as Markdown, the chosen prompt first (when `system_prompt` is on), then the items by rank, each headed with its kind, its class, its provenance (reading, parameters, when, outcome, method kind), the envelope's unit-neutral reading count when present, and rendered by its verbosity; redacted unless `unredacted=true` |
| `GET /api/prompts` | The prompt library: `{ "prompts": [ { "id", "name", "description", "content", "builtin" }, ... ] }`; six presets to start |
| `POST /api/prompts`, `PATCH /api/prompts/{id}`, `DELETE /api/prompts/{id}` | Yours to add, edit and delete, presets included; a deleted preset stays deleted |

The composed text is what the dashboard copies to the clipboard or downloads as a Markdown file. An agent reads the same text and needs no clipboard.

## Captures

`POST /api/captures` takes every reading in the catalog now, writes a ZIP into the data directory and returns it, with the file's name in the `X-Capture-Name` header. It is the slowest thing the tool does, on the order of a minute, because it is every reading including the heavy ones; give it a long timeout. Members: `readings/<name>.json` (one envelope each), `stack.json`, `composed.md`, and `manifest.json`, which lists exactly the members with each reading's outcome and byte size, the tool's version, and the redaction applied. The ZIP is redacted unless `unredacted=true`. When an agent takes an unredacted ZIP through `capture_create`, its required reason is also recorded in that ZIP's manifest. `GET /api/captures` lists each file's name, bytes and file modification time (`created_at`), plus a bounded summary of its manifest: `status=read` carries `captured_at`, `unredacted`, the reading count and counts by outcome; `missing`, `unreadable` or `limit` leave privacy and outcomes unknown. Listing reads only up to 256 KiB of each manifest, not the reading members, and does not validate the whole archive. `GET /api/captures/{name}` returns one. The tool never deletes a capture and nothing is sent anywhere.

## What is not here

No live telemetry as a service, no prediction, no full crash-dump analysis, no diagnosis. `dump_header` reads bounded structural metadata; it does not inspect the stack, module contents or memory streams, validate the whole file, or infer a culprit. A burst of corrected errors, a gap in the log or a correlated event is a lead; the reading belongs to whoever holds the evidence.

No temperature, voltage, fan speed or power-draw telemetry either, and that is a decision rather than a gap. What Windows exposes without help is the ACPI thermal zone (`MSAcpi_ThermalZoneTemperature`), which on a desktop board commonly reports a zone that is not the processor, or nothing at all; the sensors a person actually wants sit behind a Super I/O chip or the processor's own management interface, and reaching them means loading a vendor kernel driver — a different trust posture from reading a log Windows already wrote, and one this tool will not take on to report a number. Both of the diagnostic efforts this tool came out of reached for HWiNFO by hand when a temperature mattered, and that stays the answer: run the sensor tool beside this one, and hand its reading to the same conversation.
