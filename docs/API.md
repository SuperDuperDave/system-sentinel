# System Sentinel API

The one boundary. The desktop dashboard, a phone and a local agent are three clients of this interface; nothing reads the machine except through it. This document is written for an agent to read without the source. The machine-readable form is the OpenAPI document the server publishes at `/api/openapi.json`; the interactive form is `/api/docs`.

## Reaching it

The server runs on the Windows machine it reads and listens on `http://127.0.0.1:8000` unless told otherwise. Every route under `/api/` and the MCP endpoint at `/mcp` require the access token. The token is created on first start and kept in the data directory:

| Platform | Data directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\SystemSentinel\` |
| Other (development from WSL) | `~/.system-sentinel/` |

The file `token` in that directory holds it. Send it as `Authorization: Bearer <token>`. The dashboard exchanges it once for a session cookie; agents send the header. A request without a valid token gets `401` with `{"error": "unauthorized"}` and nothing else.

`system-sentinel token` prints it. `system-sentinel check` tells you whether the bridge to Windows works before you ask for anything.

### Claude Code

```
claude mcp add --transport http system-sentinel http://127.0.0.1:8000/mcp --header "Authorization: Bearer $(system-sentinel token)"
```

### Codex and other MCP clients

Streamable HTTP at `http://127.0.0.1:8000/mcp` with the same header. Every reading below is one tool, named as the reading is named; the stack and the composer are tools too.

### Without MCP

`curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/readings/events` works from any shell. Prefer this over writing your own PowerShell: the reading carries provenance, the outcome and the redaction, and it is the same evidence the person sees.

## The reading

Every reading is one query against the machine, returned in one envelope. The envelope is the contract: a collection failure is distinguishable from no findings, and what the machine said is distinguishable from what the tool concluded.

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
| `unavailable` | The bridge to Windows is not there: `powershell.exe` was not found or did not start, or the decoder is missing. Nothing about the machine can be concluded. |
| `denied` | Windows refused (access denied, or a log that needs elevation). |
| `timeout` | The query did not finish within its limit. |

Only `ok` and `empty` say anything about the machine. Treat the other four as "not observed".

**`sections[].class`** is one of:

| Class | Meaning |
| --- | --- |
| `raw` | What Windows returned, field for field, minus redaction. |
| `derived` | Computed from raw by a stated rule (a count, a bucket, a signature, a decoded structure). The section names its inputs and rule in `basis`. |
| `invariant` | A fact about the machine that does not change between readings (the fingerprint). |
| `inferred` | A lead: a pattern the tool noticed that a person or an agent should investigate. Never a diagnosis. `basis` names the rule. |

**`method`** is how the reading was taken: the kind of bridge and the query text, so the evidence can be reproduced by hand. A reading built from several queries lists them.

**`warnings`** are error records PowerShell emitted while still producing output: a sub-query that failed inside a reading that otherwise answered.

**`redacted`** lists what the default redaction removed from this response. By default a response carries no serial number, computer name, user account name or MAC address, and paths under a user profile read `C:\Users\<user>\...`. Pass `?unredacted=true` (or the `unredacted` argument in MCP) to receive them; do that only when the reader has a reason, such as a warranty claim. Message text is kept: it is the evidence. Device instance identifiers are kept: they are how PCIe endpoints are told apart.

## The catalog

`GET /api/readings` lists every reading with its description, classes, parameters and their defaults, what it may carry that redaction removes, and whether it is heavy. The live catalog is authoritative; this is the catalog as designed, with each reading's sections.

| Reading | What it reads | Sections (class) | Parameters |
| --- | --- | --- | --- |
| `health` | Whether the bridge works: PowerShell found, its version, a trivial round trip, decoder present, data directory | `bridge` (raw) | |
| `events` | Records from a Windows log by level | `records` (raw) | `log` (System, Application), `levels` (1 critical, 2 error, 3 warning, 4 information; default 1,2), `count` (default 50) |
| `record` | The log around a moment: the records before a timestamp, oldest first | `records` (raw) | `before` (ISO timestamp), `count` (default 50), `log` (default System) |
| `whea` | WHEA-Logger records with their binary payload, each decoded beside it | `records` (raw, with `RawData` hex), `decoded` (derived: one entry per record, the decoder's structure or its error) | `count` (default 30) |
| `storms` | WHEA records over a window in wall-clock buckets, grouped by signature, with burst and acceleration flags | `buckets` (derived, includes empty minutes), `signatures` (derived), `status` (inferred: quiet, burst or accelerating, with the rates and the reason) | `hours` (default 24), `bucket_seconds` (60), `burst_threshold` (5), `accel_threshold` (2.0) |
| `dumps` | The crash-dump inventory under the Windows dump locations | `files` (raw: name, path, bytes, modified) | |
| `system` | The snapshot: OS, build, boot time, uptime, processor load, memory | `snapshot` (raw) | |
| `hardware` | The fingerprint and configuration | `fingerprint` (invariant), `config` (raw), `risks` (inferred: observations such as Secure Boot off, never advice) | |
| `hardware.cpu` | Processor and platform detail | `raw`, `derived` | |
| `hardware.gpu` | Display adapters and driver | `raw`, `derived` | |
| `hardware.board` | Board and firmware | `raw`, `derived` | |
| `hardware.storage` | Disks, volumes, SMART where exposed | `raw`, `derived` | |
| `hardware.network` | Adapters and connectivity | `raw`, `derived` | |
| `drivers` | Driver changes: the most recently dated signed drivers | `drivers` (raw) | `count` (default 30) |
| `pcie` | The PCIe fabric | `endpoints` (raw), `roots` (raw), `groups` (derived: endpoints sharing a root) | |
| `power` | Power configuration and transitions | `raw`, `derived` | |
| `memory` | Physical memory and stability signals | `raw`, `derived` | |
| `constraints` | Configured limits and their sources | `raw`, `derived` | |
| `signals` | Forensic signals across the readings and the recent log | `signals` (inferred: suppressions, gaps, pressure, transitions, mismatches; `basis` names the inputs) | |

`GET /api/readings/{name}` takes the reading. Parameters are query parameters. Heavy readings (`hardware.*`, `pcie`, `power`, `memory`, `signals`) take seconds; the dashboard loads them on demand.

Records from a log (`events`, `record`, `whea`, the stream) share one shape: `RecordId`, `Id`, `Level`, `LevelDisplayName`, `ProviderName`, `MachineName`, `TaskDisplayName`, `TimeCreated` (UTC, ISO), `Message`, `Properties` (the event's data, binary values as hex).

## The record around a moment

`record` is the composer's most distinctive mechanism and the one an agent should reach for first when a person says "it froze": the log does not announce a freeze; the next start does. Take `events` to find the start (Kernel-Power 41, EventLog 6008, Kernel-General 12), then `record` with `before` set to that start to see what the machine was doing.

## The stream

`GET /api/stream` is server-sent events, polled from the logs every few seconds with one query per poll.

| Event | Data |
| --- | --- |
| `record` | `{ "log": "System", "record": { ...the record shape... } }` for each new record matching the tool's presets: crash and power (Kernel-Power 41, EventLog 6008, WER 1001, volmgr 46, disk 161 and 162), WHEA (1, 17 to 20, 46, 47), storage (7, 11, 51, 55, 57, 129, 153), driver and service (219, 7000 to 7034, 10110, 10111), application crashes (1000 to 1002), TPM (1796, 1801) |
| `heartbeat` | `{ "at": "...", "cursors": { "System": 307379, "Application": 88120 } }` on every poll |
| `bridge` | `{ "outcome": "failed", "error": "..." }` when a poll did not observe the machine, with the reading vocabulary |

A silent stream is not a healthy machine; a stream with heartbeats and no `bridge` events is.

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
| `kind` | `reading` (a whole reading), `selection` (some records of a reading, chosen by `RecordId` in `ids`), `note` (text the person or agent wrote) |
| `rank` | 1 first to 5 last in the composed handoff; default 3 |
| `verbosity` | `summary` (a table of time, level, provider, ID and the first line of the message) or `full` (the records as JSON); default `full` |
| `reading` | The envelope, kept as it was at the moment of adding: its `asked_at`, `outcome` and `method` are the item's provenance |

| Route | Does |
| --- | --- |
| `GET /api/stack` | `{ "items": [...], "prompt_id": "...", "system_prompt": true }` |
| `PATCH /api/stack` | Change `prompt_id` or `system_prompt` |
| `POST /api/stack/items` | Add an item. Body: `kind`, optional `title`, `rank`, `verbosity`, `ids`, `note`, and either `take: { "name": "...", "params": {...} }` (the server takes the reading now) or `envelope: { ... }` (a reading the caller already holds, stored as given). Returns the item. Adding the same reading with the same parameters and the same `ids` twice is refused with `409`. |
| `PATCH /api/stack/items/{id}` | Change `rank`, `verbosity` or `title` |
| `DELETE /api/stack/items/{id}` | Remove one |
| `DELETE /api/stack` | Clear |
| `GET /api/stack/composed` | `{ "text": "...", "items": 4, "redacted": [...] }`: the handoff as Markdown, the chosen prompt first (when `system_prompt` is on), then the items by rank, each headed with its kind, its class, its provenance (reading, parameters, when, outcome, method kind) and rendered by its verbosity; redacted unless `unredacted=true` |
| `GET /api/prompts` | The prompt library: `{ "id", "name", "description", "content", "builtin" }` each; six presets to start |
| `POST /api/prompts`, `PATCH /api/prompts/{id}`, `DELETE /api/prompts/{id}` | Yours to add, edit and delete, presets included |

The composed text is what the dashboard copies to the clipboard. An agent reads the same text and needs no clipboard.

## Captures

`POST /api/captures` takes every reading in the catalog now, writes a ZIP into the data directory and returns it. Members: `readings/<name>.json` (one envelope each, heavy ones included), `stack.json`, `composed.md`, and `manifest.json`, which lists exactly the members with each reading's outcome and byte size, the tool's version, and the redaction applied. The ZIP is redacted unless `unredacted=true`. `GET /api/captures` lists what is on disk; `GET /api/captures/{name}` returns one. Nothing is sent anywhere.

## What is not here

No live telemetry as a service, no prediction, no crash-dump decoding, no diagnosis. A burst of corrected errors, a gap in the log or a correlated event is a lead; the reading belongs to whoever holds the evidence.
