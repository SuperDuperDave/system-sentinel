# The machine this was built for

Dated observations of David's own computer, moved here on 2026-09-20 from the repository's former CLAUDE.md, where they were written between 2026-03-25 and 2026-05-26, and extended on 2026-09-21 with what the logs themselves hold. They describe the system under observation and how to query it; **verify on the host before relying on any of them**, and treat the failure signatures and mitigations as history, not as an established cause. This file is private to this repository and nothing in it is copied to a published surface.

## System under observation (as recorded 2026-03 to 2026-05)

| Component | Detail |
|-----------|--------|
| CPU | AMD Ryzen 7 5800X (8C/16T, Zen 3, single CCD) |
| Board | ASRock B550 Taichi (BIOS P3.90, AMI) |
| RAM | 2x 8GB TeamGroup DDR4-4000 @ JEDEC 2400 MHz |
| GPU | NVIDIA GeForce RTX 3080 |
| Storage | Samsung 990 PRO 2TB |
| Cooler | NZXT Kraken AIO (~4.5 years, original paste) |
| PSU | Unknown (NZXT BLD pre-built) |
| OS | Windows 11 Pro Build 26200 (25H2) |
| Purchased | August 27, 2021 (NZXT BLD) |

## Failure signatures seen (history, not cause)

Three MCA status register values were observed in the 2026-03 sessions:
- `0xbaa000000000080b`: Bus/Interconnect Error (data fabric), APIC 0
- `0xbea0000001000108`: Cache Hierarchy Error (L1/L2/L3), various APICs
- `0xbaa000000002010b`: Cache Hierarchy companion error, APIC 0

Bugcheck 0x133 (DPC_WATCHDOG_VIOLATION) recurred in 2026-03. The original note said WHEA logging stopped after the first mitigation round while unclean restarts (Kernel-Power 41 with no bugcheck) continued; the 2026-05-26 stream records that relapse. That absence was inferred from the System WHEA-Logger path. On 2026-09-23, the separate Kernel-WHEA/Errors channel still retained older CPER records, so the System log alone cannot establish when all hardware error reporting stopped. On 2026-09-20 the System log's retention began on 2026-07-25 and held eight Kernel-Power 41 records.

## How to query the host

All Windows telemetry is gathered through `powershell.exe` with `-NoProfile -NonInteractive -ExecutionPolicy Bypass`, from WSL in development and natively on Windows. `sentinel/bridge.py` is the one path: the EncodedCommand pattern, the JSON prelude, and the six outcomes a query can have.

- Human-readable WHEA events in System: `Get-WinEvent -FilterHashtable @{LogName="System"; ProviderName="Microsoft-Windows-WHEA-Logger"}`
- CPER hardware error records in the separate channel: `Get-WinEvent -FilterHashtable @{LogName="Microsoft-Windows-Kernel-WHEA/Errors"; ProviderName="Microsoft-Windows-Kernel-WHEA"; Id=20}`. [Microsoft describes this channel](https://learn.microsoft.com/en-us/windows-hardware/drivers/whea/registering-for-notification-of-hardware-error-events) as a source of detailed CPER events; do not infer that either log covers the other's retention.
- Unclean restarts: `Get-WinEvent -FilterHashtable @{ProviderName="Microsoft-Windows-Kernel-Power"; Id=41}`
- Bugchecks: `Get-WinEvent -FilterHashtable @{ProviderName="Microsoft-Windows-WER-SystemErrorReporting"}`
- System errors: `Get-WinEvent -FilterHashtable @{LogName="System"; Level=1,2}`
- Hardware: `Get-CimInstance Win32_Processor`, `Win32_BaseBoard`, `Win32_PhysicalMemory`, and the like
- HWiNFO log, when running: a CSV on the Windows desktop (path in the 2026 streams)

## What the logs held on 2026-09-21

Read from this machine through the bridge on that date, on Windows 11 build 26200, while `crash`, `faults` and `reliability` were being built. These are facts about Windows' own event manifests and about which classes answer here, not values out of the record: no counts, no times, nothing the machine calls itself. A manifest changes with a build, so **verify on the host before relying on any of it**.

**The stop records.** What names one unplanned stop is spread over four records in two logs.

- **Kernel-Power 41**, template version 10, carries 21 named properties, `BugcheckCode` first and `WHEABootErrorCount` last. The code is decimal, the four parameters arrive as numbers, the flags as booleans. A 41 whose `BugcheckCode` is 0 is itself the evidence: the stop wrote no bug check.
- **EventLog 6008** carries eight positional properties, unnamed: the time and the date as locale text (the date with U+200E marks through it), several empty, and a 64-byte binary value. That value holds two Win32 `SYSTEMTIME` structures — eight little-endian `uint16` each: year, month, day of week, day, hour, minute, second, milliseconds — bytes 0 to 15 the stop in local time and bytes 16 to 31 the same instant in UTC; bytes 32 to 63 are not a time. Checked against the message text on three records. This is where a stop time comes from; the locale text is never parsed.
- **Kernel-General 12** (the OS started) is named, ending in `StartTime`, an ISO 8601 UTC timestamp equal to `Win32_OperatingSystem.LastBootUpTime` within a second; **13** is the clean shutdown. In this log the 41 follows its session's 12 by two to five seconds, the 6008 follows the 41 by four to twenty seconds, EventLog 6005 (log started) lands in the same second as the 6008, and 6006 (log stopped) precedes a clean shutdown's 13 by a few seconds.
- **WER-SystemErrorReporting 1001** ("The computer has rebooted from a bugcheck") was not in this machine's System log on that date, so the reading's layout for it is Microsoft's template rather than an observation and says so in its basis: `param1` the code with its four parameters as text, `param2` the dump path, `param3` the report id, with the message text as the fallback.
- **Windows Error Reporting 1001** (Application log) carries 23 named properties, `EventName` saying what kind of report it is. A `BlueScreen` report puts the bug check code in `P1` and the parameters in `P2` to `P5` as bare lower-case hex, lists the minidump and `MEMORY.DMP` among `AttachedFiles`, and once WER has analysed the dump names the failing module in `Bucket`. A `LiveKernelEvent` has the same layout for a failure the machine survived. **One report writes two or three records under one `ReportId`**, its `ReportStatus` climbing, so a reading dedupes by report id and keeps the latest record.
- **Application Error 1000** carries 15 named properties, **Application Hang 1002** ten. `AppPath` and `ModulePath` can carry a user name or a UNC path into WSL, which is why they are declared private.

**Reliability.** `Win32_ReliabilityRecords` and `Win32_ReliabilityStabilityMetrics` both answer here without elevation, the metrics class one row per hour on UTC hour boundaries. The RAC scheduled task could not be listed; the data is there regardless. On a machine Windows kept no record of — a fresh runner — either class may be empty, which is a finding and not a failure.

**Where device arrivals are not.** Kernel-PnP 400, 410 and 420 are not in the System log; they are in `Microsoft-Windows-Kernel-PnP/Configuration`. The System log holds 219 (the known driver noise below) and 225.

**Windows' own memory test.** No `Microsoft-Windows-MemoryDiagnostics-Results` record was in the System log, which is why `memory` reports the result beside the log's oldest record time: no result reaches only as far back as the log does.

**A window is the log's index, not `StartTime`.** `Get-WinEvent -FilterHashtable` with `StartTime` answered with different counts for the same instant expressed as a UTC-kind timestamp and as a local-kind one: it does not honour Kind. Every window in the readings is an XPath clause on `TimeCreated[@SystemTime>='…']` against the index instead.

**How far back each log reaches** is a date that moves, so ask the machine rather than reading it here:

```
Get-WinEvent -LogName System -Oldest -MaxEvents 1 | Select-Object TimeCreated
Get-WinEvent -LogName Application -Oldest -MaxEvents 1 | Select-Object TimeCreated
```

The Application log reached further back than the System log on that date, which is why a bug check report can name a stop the System log no longer holds — and why `crash` treats such a report as a stop of its own, with only what the report says.

## Known benign noise (filter out)

- Hyper-V-VmSwitch port restore failures (Event 15): WSL2 startup noise
- TPM-WMI Secure Boot errors (Events 1796/1801): Secure Boot disabled
- CMigrationService termination (Event 7034): every boot
- WUDFRd driver failures (Event 219): two USB devices

## Mitigation stack (as recorded 2026-05)

- BIOS: PBO Advanced, -200 MHz offset, PPT/TDC/EDC 88/60/90, Power Supply Idle Control = Typical Current Idle, CPPC enabled
- Windows: Balanced power plan, 85% max processor state, hypervisor core scheduler
- WSL2: 6 processors, 8 GB RAM, autoMemoryReclaim=gradual

The full active mitigation table is in the latest diagnostic stream.
