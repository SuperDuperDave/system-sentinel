# System Sentinel -- AI-Augmented Hardware Diagnostics NLAA

## What This Is

System Sentinel is a **Natural Language Agent Application** for diagnosing, monitoring, and stabilizing Dave's Windows desktop hardware. The agent operates from WSL2, reaching into the Windows host via `powershell.exe` to gather telemetry, analyze event logs, decode hardware errors, and track system health across sessions.

## System Under Observation

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

## Known Failure Signatures

Three MCA status register values observed:
- `0xbaa000000000080b` -- Bus/Interconnect Error (data fabric), APIC 0
- `0xbea0000001000108` -- Cache Hierarchy Error (L1/L2/L3), various APICs
- `0xbaa000000002010b` -- Cache Hierarchy companion error, APIC 0

Bugcheck 0x133 (DPC_WATCHDOG_VIOLATION) also recurring.

## Session Continuity

- **Stream files**: `_sessions/streams/YYYY-MM-DD-NNN-slug.md`
- **Skills**: `.claude/skills/` (initialize, diagnostic-sweep, crash-forensics)

## How to Operate

### Gathering Data
All Windows telemetry is gathered via `powershell.exe` from WSL. Use `-NoProfile -NonInteractive -ExecutionPolicy Bypass`. The `backend/services/powershell.py` shows the EncodedCommand pattern for complex scripts.

### Key PowerShell Patterns
- **WHEA events**: `Get-WinEvent -FilterHashtable @{ProviderName="Microsoft-Windows-WHEA-Logger"}`
- **Crash events**: `Get-WinEvent -FilterHashtable @{ProviderName="Microsoft-Windows-Kernel-Power"; Id=41}`
- **Bugchecks**: `Get-WinEvent -FilterHashtable @{ProviderName="Microsoft-Windows-WER-SystemErrorReporting"}`
- **System errors**: `Get-WinEvent -FilterHashtable @{LogName="System"; Level=1,2}`
- **Hardware info**: `Get-CimInstance Win32_Processor`, `Win32_BaseBoard`, `Win32_PhysicalMemory`, etc.
- **HWiNFO log**: `C:\Users\david\OneDrive\Desktop\hwinfo_log.CSV` (when running)

### Known Benign Noise (filter out)
- Hyper-V-VmSwitch port restore failures (Event 15) -- WSL2 startup noise
- TPM-WMI Secure Boot errors (Event 1796/1801) -- Secure Boot disabled
- CMigrationService termination (Event 7034) -- every boot
- WUDFRd driver failures (Event 219) -- Kingston HyperX HID + Canon EOS Webcam

## Current Mitigation Stack

See the latest session stream for the full active mitigation table. Key settings:
- BIOS: PBO Advanced, -200 MHz offset, PPT/TDC/EDC 88/60/90, Power Supply Idle Control = Typical Current Idle, CPPC enabled
- Windows: Balanced power plan, 85% max processor state, hypervisor core scheduler
- WSL2: 6 processors, 8GB RAM, autoMemoryReclaim=gradual
