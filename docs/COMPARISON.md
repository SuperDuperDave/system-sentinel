# Beside the tools it sits next to

Anyone with a misbehaving Windows machine already has tools for this. These four are the ones
people reach for, and each does something System Sentinel does not. The table is difference, not
ranking. Every cell about another tool was checked against that tool's own documentation, listed
at the end; where a question could not be answered from it, the cell says **not checked** rather
than guess.

| | System Sentinel | Event Viewer | Reliability Monitor | WhoCrashed | HWiNFO |
| --- | --- | --- | --- | --- | --- |
| **What it is for** | The machine's record behind one API, for a person and a local agent | The system's events: the Application, Security and System logs, and the ETW providers | Stability over time, rated 1 to 10, with the failures behind a chosen day | Post-mortem crash-dump analysis | Hardware detail and real-time sensors |
| **A collection failure distinguishable from an empty result** | Yes: `empty` is a finding; `failed`, `unavailable`, `denied` and `timeout` are not observations | not checked | not checked | not checked | not checked |
| **One query serves a person and an agent** | Yes: one reading is the view on screen, a route and an MCP tool | Two paths: the console for a person, `Get-WinEvent` or a saved `.evtx` for a script | A console; the history saves to an XML file | not checked | Reports export as text, CSV, XML, HTML or MHTML |
| **The evidence stays on the machine** | Yes: bound to localhost, redacted by field name, handed on by the clipboard or a file on disk | not checked | Mostly: *Check for a solution* connects to the internet, at the person's choice | Nothing on the page says anything leaves; not checked | not checked |
| **Decodes hardware-error records** | Yes: a selected System WHEA-Logger record is decoded on an exact `whea_record` read; the list is a bounded preview | not checked | not checked | Reads crash dumps, a different artifact | Not mentioned in the listing read; not checked |
| **Diagnoses** | **No.** It shows leads, each with its rule and its inputs; the reading belongs to whoever holds the evidence | Shows the events; component logs are "a great place to start troubleshooting" | Rates, and offers to look online for a solution | Yes, with its own limit: it "cannot always be exactly sure about the root cause of a system crash" | Its listing calls it a diagnostics tool; what it concludes: not checked |

## What the row of *not checked* means

None of the four documents what it shows when a collection fails rather than finds nothing. That
is why the second row reads *not checked* four times: the question is not usually asked, so the
answer is not written down anywhere a reader can check it. It is the one distinction System
Sentinel treats as part of the contract — every reading's outcome says whether the machine was
observed — and the one this table cannot settle for anyone else.

## What System Sentinel does not do

Each of the other four covers ground this tool leaves alone. It does not decode a crash dump; it
lists the dump files and stops there, where WhoCrashed begins. It does not read sensors, so
nothing here reports a temperature, a voltage or a fan, which is HWiNFO's whole subject. It does
not score a machine, as Reliability Monitor does. And it does not diagnose at all: a burst of
corrected errors, a gap in the log or a correlated event is a lead to investigate, never a cause.

## Sources read

Checked 2026-09-21. Another tool's behaviour can change; re-read these before relying on a cell.

- Event Viewer — https://learn.microsoft.com/en-us/shows/inside/event-viewer
- `Get-WinEvent`, the scripting path to the same logs — https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.diagnostics/get-winevent
- Reliability Monitor — https://learn.microsoft.com/en-us/training/modules/monitor-windows-server-performance/4-review-reliability-with-reliability-monitor
- WhoCrashed — https://www.resplendence.com/whocrashed
- HWiNFO — https://www.fosshub.com/HWiNFO.html. The vendor's own site and its documentation PDF
  answered a bot challenge and could not be read, so this is a distributor's listing of the
  vendor's description; treat the HWiNFO column as the weakest in the table.
