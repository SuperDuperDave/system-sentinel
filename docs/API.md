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
| `inferred` | A lead: a pattern the tool noticed that a person or an agent should investigate. Never a diagnosis. |

**`method`** is how the reading was taken: the kind of bridge and the query text, so the evidence can be reproduced by hand.

**`redacted`** lists what the default redaction removed from this response. By default a response carries no serial number, computer name, user account name or MAC address, and paths under a user profile read `C:\Users\<user>\...`. Pass `?unredacted=true` (or the `unredacted` argument in MCP) to receive them; do that only when the reader has a reason, such as a warranty claim. Message text is kept: it is the evidence.

## The catalog

`GET /api/readings` lists every reading with its description, class, parameters and their defaults, what it may carry that redaction removes, and a typical `took_ms`. The list below is the catalog as designed; the live catalog is authoritative.

| Reading | What it reads | Class | Parameters |
| --- | --- | --- | --- |
| `health` | Whether the bridge works: PowerShell found, its version, a trivial round trip, decoder present, data directory | raw | |
| `events` | Records from a Windows log by level | raw | `log` (System, Application), `levels` (1 critical, 2 error, 3 warning, 4 information; default 1,2), `count` (default 50) |
| `record` | The log around a moment: the records before a timestamp, oldest last | raw | `before` (ISO timestamp), `count` (default 50), `log` (default System) |
| `whea` | WHEA-Logger records with their binary payload, each decoded beside it | raw + derived | `count` (default 30) |
| `storms` | WHEA records over a window in time buckets, grouped by signature, with burst and acceleration flags | derived | `hours` (default 24), `bucket_seconds` (60), `burst_threshold` (5), `accel_threshold` (2.0) |
| `dumps` | The crash-dump inventory: names, sizes, times under the Windows dump locations | raw | |
| `system` | The snapshot: OS, build, boot time, uptime, processor load, memory | raw | |
| `hardware` | The fingerprint and configuration: CPU, GPU, board, BIOS, boot storage, Secure Boot, Fast Startup, virtualization | invariant + raw + derived | |
| `hardware.cpu` | Processor and platform detail | raw + derived | |
| `hardware.gpu` | Display adapters and driver | raw + derived | |
| `hardware.board` | Board and firmware | raw + derived | |
| `hardware.storage` | Disks, volumes, SMART where exposed | raw + derived | |
| `hardware.network` | Adapters and connectivity | raw + derived | |
| `drivers` | Driver changes: the most recently dated signed drivers | raw | `count` (default 30) |
| `pcie` | The PCIe fabric: endpoints, roots, shared groups | raw + derived | |
| `power` | Power configuration and transitions | raw + derived | |
| `memory` | Physical memory and stability signals | raw + derived | |
| `constraints` | Configured limits and their sources | raw + derived | |
| `signals` | Forensic signals across the readings and the recent log: suppressions, gaps, pressure, transitions, mismatches | inferred | |

`GET /api/readings/{name}` takes the reading. Parameters are query parameters. Heavy readings (`hardware.*`, `pcie`, `power`, `memory`) take seconds; `took_ms` in the catalog is the last observed cost on this machine.

## The record around a moment

`record` is the composer's most distinctive mechanism and the one an agent should reach for first when a person says "it froze": the log does not announce a freeze; the next start does. Take `events` to find the start (Kernel-Power 41, EventLog 6008, Kernel-General 12), then `record` with `before` set to that start to see what the machine was doing.

## The stream

`GET /api/stream` is server-sent events. It emits `event` messages for new records matching the tool's presets (crash and power, WHEA, storage, driver and service, application crashes, TPM), a `heartbeat` on every poll, and a `bridge` message when a poll fails, with the same outcome vocabulary as a reading. A silent stream is not a healthy machine; a stream with heartbeats and no `bridge` messages is.

## The stack

The stack is the evidence a person or an agent has chosen to hand on. It lives on the server so the desktop, the phone and the agent see one stack.

| Route | Does |
| --- | --- |
| `GET /api/stack` | The items, the active prompt, the options |
| `POST /api/stack/items` | Add an item. An item is a reading taken now, a `record` around a moment, a selection of records from a reading by their ids, or a note. The item keeps the data as it was read, with its provenance. |
| `PATCH /api/stack/items/{id}` | Change `rank` (1 first to 5 last) or `verbosity` (`summary`, `full`) |
| `DELETE /api/stack/items/{id}` | Remove one |
| `DELETE /api/stack` | Clear |
| `GET /api/stack/composed` | The handoff text: the chosen prompt, then the items by rank, each labelled with its class and provenance; redacted unless `unredacted=true` |
| `GET /api/prompts`, `POST`, `PATCH /{id}`, `DELETE /{id}` | The prompt library: six presets to start, yours to add, edit and delete |

The composed text is what the dashboard copies to the clipboard. An agent reads the same text and needs no clipboard.

## Captures

`POST /api/captures` writes a ZIP into the data directory and returns it: every reading, the stack, the composed text, and a manifest that lists exactly the members and the redaction applied. `GET /api/captures` lists what is on disk. Nothing is sent anywhere.

## What is not here

No live telemetry as a service, no prediction, no crash-dump decoding, no diagnosis. A burst of corrected errors, a gap in the log or a correlated event is a lead; the reading belongs to whoever holds the evidence.
