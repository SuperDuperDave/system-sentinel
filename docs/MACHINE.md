# The machine this was built for

Dated observations of David's own computer, moved here on 2026-09-20 from the repository's former CLAUDE.md, where they were written between 2026-03-25 and 2026-05-26. They describe the system under observation and how to query it; **verify on the host before relying on any of them**, and treat the failure signatures and mitigations as history, not as an established cause. This file is private to this repository and nothing in it is copied to a published surface.

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

Bugcheck 0x133 (DPC_WATCHDOG_VIOLATION) recurred in 2026-03. After the first mitigation round the WHEA logging stopped while unclean restarts (Kernel-Power 41 with no bugcheck) continued; the 2026-05-26 stream records that relapse. On 2026-09-20 the System log's retention began on 2026-07-25 and held eight Kernel-Power 41 records.

## How to query the host

All Windows telemetry is gathered through `powershell.exe` from WSL with `-NoProfile -NonInteractive -ExecutionPolicy Bypass`. `backend/services/powershell.py` shows the EncodedCommand pattern for complex scripts.

- WHEA events: `Get-WinEvent -FilterHashtable @{ProviderName="Microsoft-Windows-WHEA-Logger"}`
- Unclean restarts: `Get-WinEvent -FilterHashtable @{ProviderName="Microsoft-Windows-Kernel-Power"; Id=41}`
- Bugchecks: `Get-WinEvent -FilterHashtable @{ProviderName="Microsoft-Windows-WER-SystemErrorReporting"}`
- System errors: `Get-WinEvent -FilterHashtable @{LogName="System"; Level=1,2}`
- Hardware: `Get-CimInstance Win32_Processor`, `Win32_BaseBoard`, `Win32_PhysicalMemory`, and the like
- HWiNFO log, when running: a CSV on the Windows desktop (path in the 2026 streams)

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
