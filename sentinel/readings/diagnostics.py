"""Deep diagnostics: ``pcie``, ``power``, ``memory``, ``constraints``, ``signals``.

These four readings answer the questions a crash investigation asks after the log
has been read and the parts have been named: what hangs off which PCIe root port,
what the machine is allowed to do with power, what memory is installed and whether
it has complained, and what the machine has been told not to use. ``signals`` sits
on top of them: it takes the other readings and reports what it noticed, as leads
to investigate, never as a diagnosis.

Every script collects and nothing else; the splitting, the walking and the counting
happen below in Python, where one function holds each rule and a test can hold the
function to it. A sub-query that failed inside a reading that otherwise answered
comes back in the payload's ``warnings`` and is lifted into the envelope, so an
absence the tool could not look at is never silent.

The queries are the old backend's, kept where they answer:
``services/domains/{pcie,power,memory,constraints,forensic_signals}.py``. One of
them was replaced rather than kept. The old PCIe script walked each device's parent
chain by calling ``Get-PnpDeviceProperty`` once per device, which cost seconds per
device and overran the bridge's limit. Its obvious replacement, one call with the
whole array of instance ids, is faster but wrong on this machine: it returns the
right number of property objects with the wrong instance ids attached to them, so
some devices are answered twice and others not at all, and the answer changes
between runs. ``pnputil /enum-devices /connected /relations /format xml`` can report
the device tree in one pass, and its XML element names do not depend on the
display language. Its exit status and each returned chain are checked before
the tree is used to explain a connection.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from .. import bridge as bridge_module
from ..bridge import Bridge
from ..reading import REGISTRY, Reading, Section, Spec, from_object, register, take

# The fabric nests deeper than the bridge's default depth: a group holds members
# which hold their upstream chains.
DEEP = 8

# ---------------------------------------------------------------------------
# The queries
# ---------------------------------------------------------------------------

PCIE_SCRIPT = r"""
$warnings = @()

try { $present = @(Get-PnpDevice -PresentOnly -ErrorAction Stop) }
catch { throw }

# The parent of every present device, in one pass over the whole device tree.
$parents = @{}
$relationExit = $null
$relationParsed = $false
$relationListed = 0
try {
    # Pooled PowerShell sessions can retain a native command's previous exit status.
    $global:LASTEXITCODE = $null
    $text = (& pnputil.exe /enum-devices /connected /relations /format xml 2>&1 | Out-String)
    $relationExit = $LASTEXITCODE
    $nodes = ([xml]$text).SelectNodes('/PnpUtil/Device')
    $relationParsed = $true
    $relationListed = $nodes.Count
    foreach ($d in $nodes) {
        if ($d.InstanceId -and $d.Parent) { $parents[[string]$d.InstanceId] = [string]$d.Parent }
    }
    if ($relationExit -ne 0) { $warnings += "pnputil exited with code $relationExit; its device relations may be incomplete." }
} catch { $warnings += "pnputil did not report the device tree: $($_.Exception.Message)" }

# Bus, device and function, from the PCI enumerator's own record. The indirect string
# ends with the resolved numbers in parentheses, which do not depend on the display language.
$locations = @{}
try {
    foreach ($model in (Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Enum\PCI' -ErrorAction Stop)) {
        foreach ($instance in (Get-ChildItem $model.PSPath -ErrorAction SilentlyContinue)) {
            $entry = Get-ItemProperty $instance.PSPath -ErrorAction SilentlyContinue
            if ($entry -and $entry.LocationInformation) {
                $locations['PCI\' + $model.PSChildName + '\' + $instance.PSChildName] = [string]$entry.LocationInformation
            }
        }
    }
    if ($locations.Count -eq 0) { $warnings += 'The PCI enumerator recorded no bus addresses.' }
} catch { $warnings += "The PCI enumerator key was not readable, so no bus addresses were observed: $($_.Exception.Message)" }

$names = @{}
foreach ($d in $present) { if ($d.InstanceId -and -not $names.ContainsKey([string]$d.InstanceId)) { $names[[string]$d.InstanceId] = [string]$d.FriendlyName } }

$devices = @(foreach ($d in $present) {
    if ($d.InstanceId -notlike 'PCI\*') { continue }
    $parent = $(if ($parents.ContainsKey([string]$d.InstanceId)) { $parents[[string]$d.InstanceId] } else { $null })
    [pscustomobject]@{
        Name               = [string]$d.FriendlyName
        InstanceId         = [string]$d.InstanceId
        Class              = [string]$d.Class
        Status             = [string]$d.Status
        Problem            = [string]$d.Problem
        ProblemDescription = [string]$d.ProblemDescription
        Service            = [string]$d.Service
        Location           = $(if ($locations.ContainsKey([string]$d.InstanceId)) { $locations[[string]$d.InstanceId] } else { $null })
        Parent             = $parent
        ParentName         = $(if ($parent -and $names.ContainsKey($parent)) { $names[$parent] } else { $null })
    }
})

[pscustomobject]@{
    devices = $devices
    relation_source = [pscustomobject]@{
        exit_code = $relationExit
        parsed = $relationParsed
        listed = $relationListed
        mapped = $parents.Count
    }
    warnings = $warnings
}
"""

POWER_SCRIPT_TEMPLATE = r"""
$warnings = @()
$sources = @{}

$sleep_states = $null
try {
    $global:LASTEXITCODE = $null
    $text = (& powercfg.exe /a 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "exit code $LASTEXITCODE" }
    $sleep_states = @($text -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $sources.sleep_states = @{ outcome = $(if ($sleep_states.Count) { 'ok' } else { 'empty' }) }
    if ($sleep_states.Count -eq 0) { $warnings += 'powercfg /a produced no output: the supported sleep states were not observed.' }
} catch { $sources.sleep_states = @{ outcome = 'failed' }; $warnings += "powercfg /a did not run: $($_.Exception.Message)" }

$aspm_ac = $null
$aspm_dc = $null
try {
    $global:LASTEXITCODE = $null
    $text = (& powercfg.exe /query SCHEME_CURRENT SUB_PCIEXPRESS ASPM 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "exit code $LASTEXITCODE" }
    foreach ($line in ($text -split "`r?`n")) {
        if ($line -match 'AC Power Setting Index:\s*(0x[0-9a-fA-F]+)') { $aspm_ac = $Matches[1] }
        if ($line -match 'DC Power Setting Index:\s*(0x[0-9a-fA-F]+)') { $aspm_dc = $Matches[1] }
    }
    $sources.aspm = @{ outcome = $(if ($null -ne $aspm_ac -or $null -ne $aspm_dc) { 'ok' } else { 'empty' }) }
    if ($null -eq $aspm_ac -and $null -eq $aspm_dc) { $warnings += 'powercfg reported no PCI Express link state power management setting for the active scheme.' }
} catch { $sources.aspm = @{ outcome = 'failed' }; $warnings += "powercfg /query did not run: $($_.Exception.Message)" }

$wake_armed = $null
try {
    $global:LASTEXITCODE = $null
    $text = (& powercfg.exe /devicequery wake_armed 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "exit code $LASTEXITCODE" }
    $wake_armed = @($text -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $sources.wake_armed = @{ outcome = $(if ($wake_armed.Count) { 'ok' } else { 'empty' }) }
} catch { $sources.wake_armed = @{ outcome = 'failed' }; $warnings += "powercfg /devicequery did not run: $($_.Exception.Message)" }

$hiberboot = $null
$entry = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled -ErrorAction SilentlyContinue
if ($null -ne $entry -and $entry.HiberbootEnabled -in @(0, 1)) { $hiberboot = [int]$entry.HiberbootEnabled; $sources.hiberboot = @{ outcome = 'ok' } }
else { $sources.hiberboot = @{ outcome = 'failed' }; $warnings += 'HiberbootEnabled was not a readable 0 or 1: the fast startup setting was not observed.' }

$batteries = $null
try {
    $batteries = @(Get-CimInstance Win32_Battery -ErrorAction Stop | Select-Object Name, BatteryStatus, EstimatedChargeRemaining)
    $sources.batteries = @{ outcome = $(if ($batteries.Count) { 'ok' } else { 'empty' }) }
} catch { $sources.batteries = @{ outcome = 'failed' }; $warnings += "Win32_Battery did not answer: $($_.Exception.Message)" }

$boot = $null
try {
    $boot = (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime
    $sources.boot_time = @{ outcome = $(if ($boot) { 'ok' } else { 'empty' }) }
} catch { $sources.boot_time = @{ outcome = 'failed' }; $warnings += "Win32_OperatingSystem did not answer: $($_.Exception.Message)" }

# Every transition the machine records: start and shutdown, sleep and resume, the
# shutdowns it did not plan, and the display driver resets that sit beside them. Each
# provider carries its own event ids, because the same number means different things
# under different providers: one filter hash table over all of them would pull in
# Kernel-General's event 1, a change of the system clock, as though it were a wake.
$query = @'
{query}
'@
$transitions = $null
$transition_limit = 120
$transition_limit_reached = $false
try {
    $found = @(Get-WinEvent -FilterXml ([xml]$query) -MaxEvents 121 -ErrorAction Stop |
        Select-Object RecordId, Id, ProviderName, LevelDisplayName,
            @{Name='TimeCreated'; Expression={ $_.TimeCreated.ToUniversalTime().ToString('o') }},
            Message)
    $transition_limit_reached = $found.Count -gt $transition_limit
    $transitions = @($found | Select-Object -First $transition_limit)
    $sources.transitions = @{ outcome = $(if ($transitions.Count) { 'ok' } else { 'empty' }); limit = $transition_limit; limit_reached = $transition_limit_reached; returned = $transitions.Count }
} catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { $transitions = @(); $sources.transitions = @{ outcome = 'empty'; limit = $transition_limit; limit_reached = $false; returned = 0 } }
    else { $sources.transitions = @{ outcome = 'failed'; limit = $transition_limit; limit_reached = $null; returned = $null }; $warnings += "The transition ledger did not read: $($_.Exception.Message)" }
}

[pscustomobject]@{
    sleep_states = $sleep_states
    aspm         = [pscustomobject]@{ ac_index = $aspm_ac; dc_index = $aspm_dc }
    wake_armed   = $wake_armed
    hiberboot_enabled = $hiberboot
    batteries    = $batteries
    boot_time    = $(if ($boot) { $boot.ToUniversalTime().ToString('o') } else { $null })
    transitions  = $transitions
    sources      = $sources
    warnings     = $warnings
}
"""

MEMORY_SCRIPT = r"""
$warnings = @()
$sources = @{}

$modules = $null
try {
    $modules = @(Get-CimInstance Win32_PhysicalMemory -ErrorAction Stop | Select-Object BankLabel, DeviceLocator, Manufacturer, PartNumber,
        @{Name='serial_number'; Expression={ $_.SerialNumber }},
        Capacity, Speed, ConfiguredClockSpeed, ConfiguredVoltage, MinVoltage, MaxVoltage, SMBIOSMemoryType, TotalWidth, DataWidth)
    $sources.modules = @{ outcome = $(if ($modules.Count) { 'ok' } else { 'empty' }) }
    if ($modules.Count -eq 0) { $warnings += 'Win32_PhysicalMemory returned no modules.' }
} catch { $sources.modules = @{ outcome = 'failed' }; $warnings += "Win32_PhysicalMemory did not answer: $($_.Exception.Message)" }

$arrays = $null
try {
    $allArrays = @(Get-CimInstance Win32_PhysicalMemoryArray -ErrorAction Stop | Select-Object MaxCapacityEx, MaxCapacity, MemoryDevices, MemoryErrorCorrection, Use)
    $arrays = @($allArrays | Where-Object { $_.Use -eq 3 })
    $sources.arrays = @{ outcome = $(if ($arrays.Count) { 'ok' } else { 'empty' }) }
    if ($arrays.Count -eq 0) { $warnings += 'Win32_PhysicalMemoryArray returned no system-memory array.' }
} catch { $sources.arrays = @{ outcome = 'failed' }; $warnings += "Win32_PhysicalMemoryArray did not answer: $($_.Exception.Message)" }

# Windows' own memory test result, whenever it last ran. A clean no-match means
# no result within System-log retention; a failed query means its absence is unknown.
$diagnostic = $null
try {
    $diagnostic = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-MemoryDiagnostics-Results'} -MaxEvents 1 -ErrorAction Stop |
        Select-Object Id, LevelDisplayName, Message,
            @{Name='TimeCreated'; Expression={ $_.TimeCreated.ToUniversalTime().ToString('o') }})[0]
    $sources.diagnostic = @{ outcome = $(if ($diagnostic) { 'ok' } else { 'empty' }) }
} catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { $sources.diagnostic = @{ outcome = 'empty' } }
    else { $sources.diagnostic = @{ outcome = 'failed' }; $warnings += "The memory diagnostic result did not read: $($_.Exception.Message)" }
}

$log_begins = $null
try {
    $log_begins = (Get-WinEvent -LogName System -Oldest -MaxEvents 1 -ErrorAction Stop).TimeCreated.ToUniversalTime().ToString('o')
    $sources.log_begins = @{ outcome = $(if ($log_begins) { 'ok' } else { 'empty' }) }
}
catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { $sources.log_begins = @{ outcome = 'empty' } }
    else { $sources.log_begins = @{ outcome = 'failed' }; $warnings += "The System log's oldest record did not read, so how far back it reaches is unknown: $($_.Exception.Message)" }
}

[pscustomobject]@{
    modules      = $modules
    arrays       = $arrays
    diagnostic   = $diagnostic
    log_begins   = $log_begins
    sources      = $sources
    warnings     = $warnings
}
"""

CONSTRAINTS_SCRIPT = r"""
# Every present device the machine is not simply using: disabled by a person, without a
# driver, or in error. One query over all of them, so the reason is the device's own.
# This is the only source: if it fails, the bridge must report that failure rather than an
# empty list that would claim every device is working.
$devices = @(Get-PnpDevice -PresentOnly -ErrorAction Stop |
    Where-Object { $_.Status -ne 'OK' -or ([string]$_.Problem -ne 'CM_PROB_NONE' -and [string]$_.Problem) } |
    Select-Object @{Name='Name'; Expression={ [string]$_.FriendlyName }},
        @{Name='InstanceId'; Expression={ [string]$_.InstanceId }},
        @{Name='Class'; Expression={ [string]$_.Class }},
        @{Name='Status'; Expression={ [string]$_.Status }},
        @{Name='Problem'; Expression={ [string]$_.Problem }},
        ProblemDescription,
        @{Name='Service'; Expression={ [string]$_.Service }})

[pscustomobject]@{ devices = $devices }
"""

# ---------------------------------------------------------------------------
# pcie
# ---------------------------------------------------------------------------

GROUPS_BASIS = (
    "A device is placed only when Windows reported its parent and every PCI ancestor's parent. "
    "Devices with a reported PCI child are omitted from the member list; when relations are partial, "
    "a placed member may still have an unseen child. A root_port group follows a reported chain to "
    "its highest PCI ancestor, so its members share that link. A non_pci_parent group has no PCI "
    "ancestor and does not establish a shared PCIe link. Missing chains are listed in coverage. "
    "The address is the bus, device and function the PCI enumerator recorded for that device."
)
COVERAGE_BASIS = (
    "With returned PCI devices, complete requires a successful pnputil XML query and a reported parent chain for every device; "
    "an empty inventory needs no parent relations. "
    "Partial keeps only groups supported by fully reported chains. None means no relation-backed group can be formed; "
    "it does not mean the PCI devices are absent. Unplaced entries name the device at the first missing link."
)

_ADDRESS = re.compile(r"\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)\s*$")


def _key(instance_id: Any) -> str:
    """Instance identifiers are spelled in different cases by different Windows interfaces."""
    return str(instance_id or "").upper()


def pcie_topology(devices: list[dict[str, Any]], relation_source: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """Place only devices with a fully observed chain, and state what could not be placed."""
    by_id = {_key(d.get("InstanceId")): d for d in devices}
    parented = Counter(_key(d.get("Parent")) for d in devices if d.get("Parent"))
    parsed = relation_source.get("parsed") is True
    exit_code = relation_source.get("exit_code")
    clean_source = parsed and type(exit_code) is int and exit_code == 0
    placed: dict[str, tuple[list[str], str]] = {}
    unplaced: list[dict[str, str]] = []
    for device in devices:
        if not parsed:
            reason = "the relation source did not answer"
            chain, bus, at = [], "", str(device.get("InstanceId") or "")
        else:
            chain, bus, reason, at = _upstream(device, by_id)
        if reason:
            unplaced.append({"instance_id": str(device.get("InstanceId") or ""), "at": at, "reason": reason})
        else:
            placed[_key(device.get("InstanceId"))] = (chain, bus)

    if not devices:
        relation_state = "complete"  # no topology is needed to establish an empty PCI inventory
    elif not parsed or not placed:
        relation_state = "none"
    elif not clean_source or unplaced:
        relation_state = "partial"
    else:
        relation_state = "complete"

    coverage = {
        "relations": relation_state,
        "returned_devices": len(devices),
        "placed_devices": len(placed),
        "unplaced": unplaced,
    }
    if relation_state == "none":
        return None, coverage

    groups: dict[str, dict[str, Any]] = {}
    for member_device in devices:
        if parented.get(_key(member_device.get("InstanceId"))):
            continue
        resolved = placed.get(_key(member_device.get("InstanceId")))
        if resolved is None:
            continue
        chain, bus = resolved
        root = by_id[_key(chain[-1])] if chain else None
        key = _key(chain[-1]) if chain else bus
        group = groups.setdefault(
            key,
            {
                "kind": "root_port" if chain else "non_pci_parent",
                "upstream": {
                    "instance_id": (root or {}).get("InstanceId") or member_device.get("Parent"),
                    "name": (root or {}).get("Name") or member_device.get("ParentName"),
                },
                "members": [],
            },
        )
        group["members"].append(
            {
                "name": member_device.get("Name"),
                "instance_id": member_device.get("InstanceId"),
                "class": member_device.get("Class"),
                "status": member_device.get("Status"),
                "problem": member_device.get("Problem"),
                "address": pcie_address(member_device.get("Location")),
                "upstream": chain,
            }
        )

    ordered = sorted(groups.values(), key=lambda g: (-len(g["members"]), str(g["upstream"]["name"] or "")))
    return ordered, coverage


def _upstream(device: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> tuple[list[str], str, str | None, str]:
    """Return an observed PCI chain and terminal bus, or the first missing relation."""
    chain: list[str] = []
    device_id = _key(device.get("InstanceId"))
    seen: set[str] = {device_id}
    parent = _key(device.get("Parent"))
    if not parent:
        return chain, "", "parent not reported", device_id
    while parent.startswith("PCI\\"):
        if parent not in by_id:
            return chain, "", "upstream PCI device was not returned", parent
        if parent in seen:
            return chain, "", "parent relations form a loop", parent
        seen.add(parent)
        chain.append(by_id[parent]["InstanceId"])
        parent = _key(by_id[parent].get("Parent"))
        if not parent:
            return chain, "", "an upstream device's parent was not reported", _key(chain[-1])
    return chain, parent, None, ""


def pcie_address(location: Any) -> dict[str, int] | None:
    """Bus, device and function from the enumerator's location string, which ends in ``(bus,device,function)``."""
    match = _ADDRESS.search(str(location or ""))
    if not match:
        return None
    bus, device, function = (int(g) for g in match.groups())
    return {"bus": bus, "device": device, "function": function, "address": f"{bus:02x}:{device:02x}.{function}"}


def take_pcie(bridge: Bridge, params: dict[str, Any]) -> Reading:
    result = bridge.run(PCIE_SCRIPT, depth=DEEP)

    def build(payload: dict[str, Any]) -> list[Section]:
        devices = list(payload.get("devices") or [])
        source = payload.get("relation_source") if isinstance(payload.get("relation_source"), dict) else {}
        groups, coverage = pcie_topology(devices, source)
        return [
            Section("devices", "raw", devices),
            Section("groups", "derived", groups, basis=GROUPS_BASIS),
            Section("coverage", "derived", coverage, basis=COVERAGE_BASIS),
            Section("collection", "raw", {"pnputil": {key: source.get(key) for key in ("exit_code", "parsed", "listed", "mapped")}}),
        ]

    reading = from_object("pcie", params, PCIE_SCRIPT, result, build)
    devices, coverage = reading.section("devices"), reading.section("coverage")
    if devices is not None and coverage is not None:
        reading.count = len(devices.data)
        if reading.outcome == "ok" and not devices.data:
            reading.outcome = "empty"  # Get-PnpDevice answered with no PCI devices
        elif coverage.data["relations"] != "complete":
            unplaced = len(coverage.data["unplaced"])
            if unplaced:
                reading.warnings.append(f"PCI parent relations are {coverage.data['relations']}; {unplaced} returned devices could not be placed, so only reported links appear in groups")
            else:
                reading.warnings.append("The PCI relation source did not complete cleanly; reported parent chains are shown, but their completeness is uncertain")
    return reading


# ---------------------------------------------------------------------------
# power
# ---------------------------------------------------------------------------

POWER_BASIS = (
    "The sleep model is read from the states powercfg lists as available; the link state power "
    "management setting is the documented index (0 off, 1 L0s, 2 L1, 3 L0s and L1); the fast startup "
    "preference is HiberbootEnabled (0 or 1), which alone does not prove hibernation availability "
    "or the last boot mode. The power source is inferred from a returned battery query: "
    "discharge or AC when status identifies it, or external power when no battery was reported. "
    "Each ledger record is named by its provider and event "
    "id together. Counts cover only the returned records; limit_reached means older matches may "
    "exist. A failed source makes its dependent derived values unknown, not empty."
)

# What the machine records a transition as: the provider and the event id together, because
# the same number means different things under different providers. This table is both the
# query and the vocabulary the ledger is counted in.
TRANSITIONS: dict[tuple[str, int], str] = {
    ("Microsoft-Windows-Kernel-Power", 41): "unexpected shutdown",
    ("Microsoft-Windows-Kernel-Power", 42): "sleep",
    ("Microsoft-Windows-Kernel-Power", 107): "resume",
    ("Microsoft-Windows-Kernel-Power", 109): "shutdown initiated",
    ("Microsoft-Windows-Kernel-General", 12): "start",
    ("Microsoft-Windows-Kernel-General", 13): "shutdown",
    ("Microsoft-Windows-Power-Troubleshooter", 1): "wake",
    ("Microsoft-Windows-WER-SystemErrorReporting", 1001): "bug check",
    ("EventLog", 6005): "log started",
    ("EventLog", 6006): "log stopped",
    ("EventLog", 6008): "unexpected shutdown, logged at the next start",
    ("Display", 4101): "display driver reset",
}

ASPM = {0: "off", 1: "L0s", 2: "L1", 3: "L0s and L1"}

# powercfg prints this when nothing on the machine may wake it.
_NONE = "none"

# powercfg heads the two halves of its answer with these; without the first one there is
# no way to tell an available state from an unavailable one, and the model stays unknown.
_AVAILABLE = "The following sleep states are available on this system:"
_UNAVAILABLE = "The following sleep states are not available on this system:"


def transitions_query() -> str:
    """One selector per provider, each with its own event ids, OR-ed inside one query."""
    by_provider: dict[str, list[int]] = defaultdict(list)
    for (provider, event_id), _ in TRANSITIONS.items():
        by_provider[provider].append(event_id)
    selects = "".join(
        f"<Select Path='System'>*[System[Provider[@Name='{provider}'] and ({' or '.join(f'EventID={i}' for i in sorted(ids))})]]</Select>"
        for provider, ids in by_provider.items()
    )
    return f"<QueryList><Query Id='0' Path='System'>{selects}</Query></QueryList>"


def power_script() -> str:
    return POWER_SCRIPT_TEMPLATE.replace("{query}", transitions_query())


def sleep_model(lines: Iterable[Any]) -> str | None:
    """Which standby state the firmware offers, read only from the half powercfg calls available."""
    text = "\n".join(str(line) for line in lines)
    if _AVAILABLE not in text:
        return None
    available = text.split(_AVAILABLE, 1)[1].split(_UNAVAILABLE, 1)[0]
    if "S0 Low Power Idle" in available:
        return "modern standby (S0 low power idle)"
    if "Standby (S3)" in available:
        return "legacy standby (S3)"
    if "Standby (S1)" in available or "Standby (S2)" in available:
        return "legacy standby (S1 or S2)"
    return "no standby state is available"


def _aspm(index: Any) -> dict[str, Any] | None:
    if index is None:
        return None
    try:
        value = int(str(index), 16 if str(index).lower().startswith("0x") else 10)
    except ValueError:
        return {"index": index, "setting": None}
    return {"index": index, "setting": ASPM.get(value)}


def _source_answered(payload: dict[str, Any], name: str, field: str) -> bool:
    source = (payload.get("sources") or {}).get(name) or {}
    return source.get("outcome") in ("ok", "empty") and _valid_source_field(payload, field, source.get("outcome"))


def _valid_source_field(payload: dict[str, Any], field: str, outcome: str | None) -> bool:
    if field not in payload:
        return False
    value = payload[field]
    if field in ("sleep_states", "wake_armed", "batteries", "transitions", "modules", "arrays"):
        if outcome in ("failed", "denied") and value is None:
            return True
        if not isinstance(value, list):
            return False
        if (outcome == "ok" and not value) or (outcome == "empty" and bool(value)):
            return False
        if field == "transitions" and outcome in ("ok", "empty"):
            source = (payload.get("sources") or {}).get("transitions") or {}
            if not (isinstance(source.get("returned"), int) and not isinstance(source.get("returned"), bool)
                    and source["returned"] == len(value) and isinstance(source.get("limit"), int)
                    and source["limit"] > 0 and isinstance(source.get("limit_reached"), bool)):
                return False
        return all(isinstance(item, dict) for item in value) if field in ("batteries", "transitions", "modules", "arrays") else True
    if field == "aspm":
        return isinstance(value, dict)
    if outcome == "ok" and field == "diagnostic":
        return isinstance(value, dict)
    if outcome == "empty" and field in ("diagnostic", "boot_time", "log_begins"):
        return value is None
    if outcome == "ok" and field in ("boot_time", "log_begins"):
        return isinstance(value, str) and bool(value)
    if outcome == "ok" and field == "hiberboot_enabled":
        return type(value) is int and value in (0, 1)
    return True


def _collection(payload: dict[str, Any], fields: dict[str, str]) -> list[str]:
    """A missing collector field or source report is a failed observation, never an empty one."""
    reported = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    missing = []
    sources = {}
    for name, field in fields.items():
        source = reported.get(name)
        if not isinstance(source, dict) or source.get("outcome") not in ("ok", "empty", "failed", "denied") or not _valid_source_field(payload, field, source.get("outcome")):
            sources[name] = {"outcome": "failed", "reason": "Collector omitted its source outcome or returned an unexpected field shape."}
            missing.append(name)
        else:
            sources[name] = source
    payload["sources"] = sources
    return missing


POWER_FIELDS = {
    "sleep_states": "sleep_states", "aspm": "aspm", "wake_armed": "wake_armed",
    "hiberboot": "hiberboot_enabled", "batteries": "batteries", "boot_time": "boot_time", "transitions": "transitions",
}
MEMORY_FIELDS = {"modules": "modules", "arrays": "arrays", "diagnostic": "diagnostic", "log_begins": "log_begins"}


def power_source(batteries: list[dict[str, Any]]) -> str:
    if not batteries:
        return "mains (no battery reported)"
    statuses = {_int(battery.get("BatteryStatus")) for battery in batteries}
    discharge = 1 in statuses
    ac = bool(statuses & {2, 6, 7, 8, 9})
    if discharge and ac:
        return "battery statuses disagree; power source unknown"
    if discharge:
        return "battery (discharging)"
    if ac:
        return "mains (battery charging)" if statuses & {6, 7, 8, 9} else "mains (battery present)"
    return "battery present; power source unknown from reported status"


def transition_kind(record: dict[str, Any]) -> str:
    """A record is named by its provider and id together, or left unnamed rather than guessed."""
    event_id = _int(record.get("Id"))
    if event_id is None:
        return "unnamed transition"
    return TRANSITIONS.get((str(record.get("ProviderName") or ""), event_id), "unnamed transition")


def power_derived(payload: dict[str, Any]) -> dict[str, Any]:
    transitions_known = _source_answered(payload, "transitions", "transitions")
    transitions = list(payload.get("transitions") or []) if transitions_known else []
    stamps = sorted(str(t.get("TimeCreated")) for t in transitions if t.get("TimeCreated"))
    counts = Counter(transition_kind(t) for t in transitions)
    armed_known = _source_answered(payload, "wake_armed", "wake_armed")
    armed = [str(w) for w in (payload.get("wake_armed") or []) if str(w).strip().lower() != _NONE] if armed_known else None
    hiberboot = payload.get("hiberboot_enabled")
    aspm = (payload.get("aspm") or {}) if _source_answered(payload, "aspm", "aspm") else {}
    transition_source = (payload.get("sources") or {}).get("transitions") or {}
    return {
        "sleep_model": sleep_model(payload.get("sleep_states") or []) if _source_answered(payload, "sleep_states", "sleep_states") else None,
        "power_source": power_source(list(payload.get("batteries") or [])) if _source_answered(payload, "batteries", "batteries") else None,
        "link_power_management": {"ac": _aspm(aspm.get("ac_index")), "dc": _aspm(aspm.get("dc_index"))},
        "fast_startup": bool(hiberboot) if _source_answered(payload, "hiberboot", "hiberboot_enabled") and hiberboot is not None else None,
        "wake_armed": armed,
        "wake_armed_count": len(armed) if armed is not None else None,
        "uptime_seconds": _seconds_since(payload.get("boot_time")) if _source_answered(payload, "boot_time", "boot_time") else None,
        "ledger": {
            "records": len(transitions) if transitions_known else None,
            "counts": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))) if transitions_known else None,
            "window": {"first": stamps[0] if stamps else None, "last": stamps[-1] if stamps else None} if transitions_known else None,
            "limit": transition_source.get("limit", 120),
            "limit_reached": transition_source.get("limit_reached") if transitions_known else None,
        },
    }


def take_power(bridge: Bridge, params: dict[str, Any]) -> Reading:
    script = power_script()
    result = bridge.run(script, depth=DEEP)
    missing: list[str] = []

    def build(payload: dict[str, Any]) -> list[Section]:
        missing.extend(_collection(payload, POWER_FIELDS))
        derived = power_derived(payload)
        sources = payload.pop("sources", {})
        return [Section("raw", "raw", payload), Section("derived", "derived", derived, basis=POWER_BASIS), Section("collection", "raw", sources)]

    reading = from_object("power", params, script, result, build)
    reading.warnings.extend(f"The {name} source did not return its expected fields or outcome." for name in missing)
    _all_sources_failed(reading)
    return reading


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------

MEMORY_BASIS = (
    "Populated slots are counted from an observed module inventory. Total slots are reported only when the "
    "system-memory arrays name plausible totals; free slots also require returned modules. An unreadable "
    "or contradictory total is unknown. Installed capacity requires a reported capacity for every module. "
    "Known kit identities are distinct manufacturer and part-number pairs; a missing identity "
    "makes the mixed-kit answer unknown unless two known identities already disagree. Error correction is "
    "read from the module's total width exceeding its data width. Array correction uses the "
    "documented Windows code only when every returned system-memory array agrees; the exact "
    "reported type remains visible. A module runs below its rating "
    "where its configured clock is under its rated speed. Hardware errors belong to the whea "
    "reading; unplanned stops belong to crash. The memory diagnostic is the latest "
    "Microsoft-Windows-MemoryDiagnostics-Results record in the System log. An empty result means "
    "none was found within the retained log, not that the test was never run; a failed query leaves "
    "the result unknown. Each source's outcome is in collection."
)

_ECC_TRUE = (5, 6)  # single-bit and multi-bit ECC
_ECC_FALSE = (3, 4)  # none and parity
_ECC_TYPES = {0: "reserved", 1: "other", 2: "unknown", 3: "none", 4: "parity", 5: "single-bit ECC", 6: "multi-bit ECC", 7: "CRC"}


def memory_derived(payload: dict[str, Any]) -> dict[str, Any]:
    modules_known = _source_answered(payload, "modules", "modules")
    modules = list(payload.get("modules") or []) if modules_known else []
    arrays_known = _source_answered(payload, "arrays", "arrays")
    arrays = list(payload.get("arrays") or []) if arrays_known else []

    slots_used = len(modules) if modules_known else None
    reported_counts = [_int(array.get("MemoryDevices")) for array in arrays]
    reported_slots = sum(value for value in reported_counts if value is not None) if reported_counts and all(value is not None and value > 0 for value in reported_counts) else None
    slots_total = reported_slots if reported_slots is not None and reported_slots > 0 and (slots_used is None or reported_slots >= slots_used) else None
    capacities = [_int(m.get("Capacity")) for m in modules]
    capacity_known = bool(capacities) and all(value is not None and value > 0 for value in capacities)
    identities = [f"{m.get('Manufacturer')} {m.get('PartNumber')}".strip() if m.get("Manufacturer") and m.get("PartNumber") else None for m in modules]
    kits = sorted({identity for identity in identities if identity})
    below_rating = [
        m.get("DeviceLocator")
        for m in modules
        if _int(m.get("Speed")) and _int(m.get("ConfiguredClockSpeed")) and _int(m.get("ConfiguredClockSpeed")) < _int(m.get("Speed"))
    ]
    diagnostic = payload.get("diagnostic") if _source_answered(payload, "diagnostic", "diagnostic") and isinstance(payload.get("diagnostic"), dict) else None
    correction_codes = {_int(array.get("MemoryErrorCorrection")) for array in arrays}
    correction = next(iter(correction_codes)) if arrays and len(correction_codes) == 1 else None

    return {
        "installed_gb": round(sum(value or 0 for value in capacities) / 1024**3, 2) if capacity_known else None,
        "slots_used": slots_used,
        "slots_total": slots_total,
        "slots_free": slots_total - slots_used if slots_total is not None and slots_used is not None and modules else None,
        "modules": [
            {
                "locator": _locator(m),
                "capacity_gb": round(capacities[index] / 1024**3, 2) if capacities[index] and capacities[index] > 0 else None,
                "rated_mhz": _int(m.get("Speed")),
                "configured_mhz": _int(m.get("ConfiguredClockSpeed")),
                "configured_millivolts": _int(m.get("ConfiguredVoltage")),
                "error_correction": _ecc(m),
            }
            for index, m in enumerate(modules)
        ] if modules_known else None,
        "kits": kits if modules_known else None,
        "mixed_kit": True if len(kits) > 1 else False if modules_known and bool(modules) and all(identities) else None,
        "below_rated_speed": below_rating if modules_known else None,
        "array_error_correction": True if correction in _ECC_TRUE else False if correction in _ECC_FALSE else None,
        "array_error_correction_type": _ECC_TYPES.get(correction),
        "memory_diagnostic": {
            "outcome": ((payload.get("sources") or {}).get("diagnostic") or {}).get("outcome", "failed"),
            "last_result": {key: diagnostic.get(key) for key in ("Id", "TimeCreated", "LevelDisplayName", "Message")} if diagnostic else None,
            "log_begins": payload.get("log_begins") if _source_answered(payload, "log_begins", "log_begins") else None,
        },
    }


def _locator(module: dict[str, Any]) -> str:
    """The slot as a person reads it on the board: the bank names the channel, the locator the slot."""
    bank = str(module.get("BankLabel") or "").strip()
    locator = str(module.get("DeviceLocator") or "").strip()
    if bank and locator and re.fullmatch(r"DIMM\s*\d+", locator, re.I):
        return f"{bank} - {locator}"
    return locator or bank or "unnamed slot"


def _ecc(module: dict[str, Any]) -> bool | None:
    total, data = _int(module.get("TotalWidth")), _int(module.get("DataWidth"))
    if total is None or data is None:
        return None
    return total > data


def take_memory(bridge: Bridge, params: dict[str, Any]) -> Reading:
    result = bridge.run(MEMORY_SCRIPT, depth=DEEP)
    missing: list[str] = []

    def build(payload: dict[str, Any]) -> list[Section]:
        missing.extend(_collection(payload, MEMORY_FIELDS))
        derived = memory_derived(payload)
        sources = payload.pop("sources", {})
        return [Section("raw", "raw", payload), Section("derived", "derived", derived, basis=MEMORY_BASIS), Section("collection", "raw", sources)]

    reading = from_object("memory", params, MEMORY_SCRIPT, result, build)
    reading.warnings.extend(f"The {name} source did not return its expected fields or outcome." for name in missing)
    _all_sources_failed(reading)
    raw = reading.section("raw")
    if raw is not None:
        reading.count = len(raw.data.get("modules") or []) if _source_answered({**raw.data, "sources": reading.section("collection").data}, "modules", "modules") else None
    return reading


def _all_sources_failed(reading: Reading) -> None:
    collection = reading.section("collection")
    if collection is not None and not any(source.get("outcome") in ("ok", "empty") for source in collection.data.values()):
        reading.outcome = "failed"
        reading.error = {"kind": "failed", "detail": "Every source in this reading failed; no machine state was observed."}
        reading.sections = [collection]
        reading.count = None


# ---------------------------------------------------------------------------
# constraints
# ---------------------------------------------------------------------------

CONSTRAINTS_BASIS = (
    "A device whose Configuration Manager problem is one of the disabled ones was turned off "
    "deliberately, by a person or by policy; every other device in the list is present and not "
    "operating, for the reason it reported itself. No second vocabulary is invented for that "
    "reason: the problem the device gave is the classification. The counts are by that problem."
)

# The Configuration Manager problems that mean the device was turned off on purpose. Everything
# else in this reading is a device that is present and not working, whatever the reason.
DISABLED = ("CM_PROB_DISABLED", "CM_PROB_HARDWARE_DISABLED", "CM_PROB_DISABLED_SERVICE")


def constraint_kind(problem: Any) -> str:
    return "disabled" if str(problem or "").upper() in DISABLED else "not_working"


def constraints_derived(devices: list[dict[str, Any]]) -> dict[str, Any]:
    kinds: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for device in devices:
        kinds[constraint_kind(device.get("Problem"))].append(
            {"name": device.get("Name"), "instance_id": device.get("InstanceId"), "class": device.get("Class"), "problem": device.get("Problem"), "problem_description": device.get("ProblemDescription")}
        )
    return {
        "disabled": kinds["disabled"],
        "not_working": kinds["not_working"],
        "counts": {"disabled": len(kinds["disabled"]), "not_working": len(kinds["not_working"])},
        "by_problem": dict(sorted(Counter(str(d.get("Problem") or "unreported") for d in devices).items())),
    }


def take_constraints(bridge: Bridge, params: dict[str, Any]) -> Reading:
    result = bridge.run(CONSTRAINTS_SCRIPT)

    def build(payload: dict[str, Any]) -> list[Section]:
        devices = list(payload.get("devices") or [])
        return [Section("raw", "raw", devices), Section("derived", "derived", constraints_derived(devices), basis=CONSTRAINTS_BASIS)]

    reading = from_object("constraints", params, CONSTRAINTS_SCRIPT, result, build)
    raw = reading.section("raw")
    if raw is not None:
        reading.count = len(raw.data)
        if reading.outcome == "ok" and not raw.data:
            reading.outcome = "empty"  # every present device is working: a finding, not an absence
    return reading


# ---------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------

SIGNAL_STOP_LIMIT = 20
SIGNAL_INPUTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("hardware", {}),
    ("pcie", {}),
    ("power", {}),
    ("constraints", {}),
    ("events", {"levels": [1, 2, 3, 4], "count": 200}),
    ("crash", {"count": SIGNAL_STOP_LIMIT}),
    ("reliability", {}),
)
_SIGNAL_INPUT_SCOPES = dict(SIGNAL_INPUTS)
SIGNAL_INPUT_DESCRIPTION = (
    f"Inputs: {', '.join(name for name, _ in SIGNAL_INPUTS)}. "
    f"Events asks for up to {_SIGNAL_INPUT_SCOPES['events']['count']} records at levels "
    f"{', '.join(str(level) for level in _SIGNAL_INPUT_SCOPES['events']['levels'])}; "
    f"Crash asks for up to {_SIGNAL_INPUT_SCOPES['crash']['count']} stops. "
)

# A provider's share of the recent records, not a count: the window is whatever the log
# held. Below TALKATIVE it is not worth naming; at or above LOUD one source is most of it.
TALKATIVE = 0.10
LOUD = 0.25
TOP_TALKERS = 3
STALE_DRIVER_DAYS = 730
# Two stops with the same bug check are a pattern; one is a stop. Five of them are as many as an
# evidence line can carry and still be read.
REPEATED_STOPS = 2
NEWEST_STOPS = 5
NO_BUGCHECK = "no bug check recorded"
# How far Windows' index has to fall below where the day before left it to be worth pointing at.
INDEX_FALL = 0.5
CLASSES = ("suppressions", "gaps", "pressure", "transitions", "mismatches")


def take_signals_sync(readings: dict[str, Reading | None], reasons: dict[str, str] | None = None) -> tuple[list[dict[str, Any]], str]:
    """The five classes, from whichever inputs were observed. Pure: the tests hold it to this."""
    reasons = reasons or {}
    observed = {name: r for name, r in readings.items() if r is not None and r.observed}
    signals: list[dict[str, Any]] = []
    signals += _suppressions(observed)
    signals += _gaps(observed, readings, reasons)
    signals += _pressure(observed)
    signals += _transitions(observed)
    signals += _mismatches(observed)
    order = {name: i for i, name in enumerate(CLASSES)}
    signals.sort(key=lambda s: order.get(s["class"], len(CLASSES)))  # stable: each class keeps its own order
    return signals, _basis(readings, reasons)


def _why(name: str, reading: Reading | None, reasons: dict[str, str]) -> str:
    """Why an input says nothing: the outcome it came back with, or why it never ran."""
    return reading.outcome if reading is not None else reasons.get(name, "not taken")


def _refined_error_kind(reading: Reading | None) -> str | None:
    if reading is None or not isinstance(reading.error, dict):
        return None
    kind = reading.error.get("kind")
    return kind if isinstance(kind, str) and kind != reading.outcome else None


def _why_with_cause(name: str, reading: Reading | None, reasons: dict[str, str]) -> str:
    why = _why(name, reading, reasons)
    return f"{why}; Sentinel's bridge was busy" if _refined_error_kind(reading) == "busy" else why


def _basis(readings: dict[str, Reading | None], reasons: dict[str, str]) -> str:
    seen = [name for name, r in readings.items() if r is not None and r.observed]
    limited = [name for name, r in readings.items() if r is not None and r.observed and r.warnings]
    missed = [f"{name} ({_why_with_cause(name, r, reasons)})" for name, r in readings.items() if r is None or not r.observed]
    parts = [
        "Each signal is a pattern noticed across the readings named on it, not a diagnosis; the rule is on the signal.",
        f"Observed: {', '.join(seen) if seen else 'nothing'}.",
    ]
    if missed:
        parts.append(f"Not observed, so anything they would have shown is absent from this reading: {', '.join(missed)}.")
    if limited:
        parts.append(f"Observed with warnings: {', '.join(limited)}; inspect each input's warnings in method.readings.")
    return " ".join(parts)


def _signal(cls: str, ident: str, title: str, summary: str, evidence: dict[str, Any], readings: list[str]) -> dict[str, Any]:
    return {"id": ident, "class": cls, "title": title, "summary": summary, "evidence": evidence, "readings": readings}


def _suppressions(observed: dict[str, Reading]) -> list[dict[str, Any]]:
    """What is switched off or bypassed, so the machine has less to tell about itself."""
    out: list[dict[str, Any]] = []
    fast = _first(_derived(observed.get("power")).get("fast_startup"), _config(observed.get("hardware")).get("fast_startup"))
    if fast:
        source = "power" if _derived(observed.get("power")).get("fast_startup") is not None else "hardware"
        out.append(
            _signal(
                "suppressions",
                "suppression:fast-startup",
                "Fast Startup preference is on",
                "Windows is configured to allow hybrid shutdown when hibernation is available. This setting does not establish whether a particular shutdown used it or how the machine last booted; Restart takes a full boot path.",
                {"hiberboot_enabled": True, "last_boot_mode": "unknown"},
                [source],
            )
        )
    for device in _derived(observed.get("constraints")).get("disabled") or []:
        out.append(
            _signal(
                "suppressions",
                f"suppression:disabled:{device.get('instance_id')}",
                f"Disabled device: {device.get('name')}",
                "The device is present and turned off, so it reports nothing and appears in no other reading as working or failing.",
                {"class": device.get("class"), "problem": device.get("problem")},
                ["constraints"],
            )
        )
    aspm = (_derived(observed.get("power")).get("link_power_management") or {}).get("ac") or {}
    if aspm.get("setting") and aspm["setting"] != "off":
        out.append(
            _signal(
                "suppressions",
                "suppression:link-power-management",
                f"PCI Express link power management is {aspm['setting']}",
                "Links may drop into a low power state, so the electrical conditions a link error would appear under are not constant while the machine is idle. Whether that matters here is for the hardware error records to say.",
                {"setting": aspm["setting"], "index": aspm.get("index")},
                ["power"],
            )
        )
    return out


def _gaps(observed: dict[str, Reading], readings: dict[str, Reading | None], reasons: dict[str, str]) -> list[dict[str, Any]]:
    """Where the record has a hole: a device that cannot speak, or a reading that did not answer."""
    out: list[dict[str, Any]] = []
    coverage = _section(observed.get("pcie"), "coverage") or {}
    if coverage.get("returned_devices") and coverage.get("relations") in ("partial", "none"):
        out.append(
            _signal(
                "gaps",
                "gap:pcie-relations",
                "PCI parent relationships were not observed" if coverage["relations"] == "none" else "Some PCI parent relationships were not observed",
                "The PCI device inventory answered, but the upstream relationships did not fully answer. Groups include only devices with a reported parent chain; an absent group does not mean an absent link.",
                {"relations": coverage["relations"], "returned_devices": coverage["returned_devices"], "placed_devices": coverage["placed_devices"], "unplaced_devices": len(coverage["unplaced"])},
                ["pcie"],
            )
        )
    for device in _derived(observed.get("constraints")).get("not_working") or []:
        out.append(
            _signal(
                "gaps",
                f"gap:not-working:{device.get('instance_id')}",
                f"Present and not working: {device.get('name')}",
                "The device is there and is not operating, so nothing it could report reaches the log or any reading. The reason is the problem it gave.",
                {"class": device.get("class"), "problem": device.get("problem")},
                ["constraints"],
            )
        )
    missing = [name for name, r in readings.items() if r is None or not r.observed]
    if missing:
        out.append(
            _signal(
                "gaps",
                "gap:inputs",
                "Part of the evidence was not observed",
                "These readings did not answer, so nothing they would have shown could be noticed here. This is a hole in the evidence, not a clean result.",
                {"not_observed": {name: _why(name, readings[name], reasons) for name in missing}},
                sorted(observed),
            )
        )
    return out


def _pressure(observed: dict[str, Reading]) -> list[dict[str, Any]]:
    """Who is filling the log. A rate against the window the records actually cover."""
    records = _records(observed.get("events"))
    if not records:
        return []
    counts = Counter(str(r.get("ProviderName") or "unnamed") for r in records)
    out: list[dict[str, Any]] = []
    for provider, count in counts.most_common(TOP_TALKERS):
        share = count / len(records)
        if share < TALKATIVE:
            break
        out.append(
            _signal(
                "pressure",
                f"pressure:{provider}",
                f"{provider} wrote {count} of the last {len(records)} records",
                "This source accounts for a large share of the recent log. A burst is a lead to follow to its cause, not a fault in itself."
                if share >= LOUD
                else "This source is among the loudest in the recent log.",
                {"provider": provider, "records": count, "of": len(records), "share": round(share, 3), "first": _stamp(records, provider, first=True), "last": _stamp(records, provider, first=False)},
                ["events"],
            )
        )
    return out


def _transitions(observed: dict[str, Reading]) -> list[dict[str, Any]]:
    """What the machine did between states, and what sat beside it."""
    out: list[dict[str, Any]] = []
    derived = _derived(observed.get("power"))
    counts = (derived.get("ledger") or {}).get("counts") or {}
    window = (derived.get("ledger") or {}).get("window") or {}

    crash = observed.get("crash")
    stops = _rows(crash, "stops")
    if stops:
        raw_limit = crash.params.get("count") if crash is not None else None
        limit = raw_limit if type(raw_limit) is int and raw_limit > 0 else SIGNAL_STOP_LIMIT
        collection = _section(crash, "collection")
        sources = collection if isinstance(collection, dict) else {}
        limit_reached = len(stops) >= limit
        sources_complete = all(
            isinstance(sources.get(name), dict)
            and sources[name].get("outcome") in ("ok", "empty")
            and sources[name].get("bound_reached") is False
            for name in ("system", "reports")
        )
        out.append(
            _signal(
                "transitions",
                "transition:unexpected-shutdown",
                f"{len(stops)} unplanned {'stop' if len(stops) == 1 else 'stops'} returned",
                "Crash composed these stops from its returned System and Application records. This is a returned count, not a lifetime total. Inspect the record before each stop for context."
                if sources_complete and not limit_reached
                else "Crash composed these stops from the sources that answered. A source or query bound may hide other stops; inspect its collection coverage before treating this as a complete count.",
                {"returned": len(stops), "limit": limit, "limit_reached": limit_reached, "sources_complete": sources_complete, "stops": [_stop_facts(stop) for stop in stops[:NEWEST_STOPS]]},
                ["crash"],
            )
        )
    out += _repeated_stops(stops)
    if counts.get("display driver reset") and (counts.get("wake") or counts.get("resume")):
        out.append(
            _signal(
                "transitions",
                "transition:display-reset-near-wake",
                "A display driver reset and a wake are in the same ledger",
                "Both appear in the transition window. Whether they are related is for the record around each moment to say.",
                {"display driver resets": counts.get("display driver reset"), "wakes": counts.get("wake"), "resumes": counts.get("resume"), "window": window},
                ["power"],
            )
        )
    out += _index_fall(_days(observed.get("reliability")))
    return out


def _stop_facts(stop: dict[str, Any]) -> dict[str, Any]:
    bugcheck = stop.get("bugcheck") or {}
    started, announced, reported = (stop.get(key) for key in ("started_at", "announced_at", "reported_at"))
    return {"started_at": started, "announced_at": announced, "reported_at": reported, "anchor_at": started or announced or reported, "code": bugcheck.get("code"), "name": bugcheck.get("name")}


def _repeated_stops(stops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stops that carry the same bug check, or that carry none at all. What they share is the lead;
    a stop that recorded nothing shares that with the others, which is why it is its own group."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for stop in stops:
        code = (stop.get("bugcheck") or {}).get("code")
        if code:
            groups[str(code)].append(stop)
        elif stop.get("no_bugcheck_recorded"):
            groups[NO_BUGCHECK].append(stop)

    out: list[dict[str, Any]] = []
    for code, group in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(group) < REPEATED_STOPS:
            continue
        none_recorded = code == NO_BUGCHECK
        name = next((f.get("name") for f in (s.get("bugcheck") or {} for s in group) if f.get("name")), None)
        out.append(
            _signal(
                "transitions",
                f"transition:repeated-stop:{'no-bugcheck' if none_recorded else code}",
                f"{len(group)} stops wrote no bug check" if none_recorded else f"{len(group)} stops share bug check {f'{code} ({name})' if name else code}",
                "Each stop's Kernel-Power record carried bug check code 0, and the returned logs supplied no bug check for that stop. This does not establish why no code was recorded. The record before each start provides further context."
                if none_recorded
                else "More than one stop was announced with this bug check. What they have in common is a lead; whether they have one cause is for the dumps and the record before each start to say.",
                {"stops": len(group), "code": None if none_recorded else code, "name": name, "started_at": [s.get("started_at") for s in group]},
                ["crash"],
            )
        )
    return out


def _index_fall(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The adjacent UTC day Windows' index fell furthest below the prior day's end."""
    falls: list[tuple[float, float, dict[str, Any]]] = []
    for before, day in zip(days, days[1:], strict=False):  # a list against its own tail: the last day has no day after it
        try:
            adjacent = (datetime.fromisoformat(str(day.get("day"))) - datetime.fromisoformat(str(before.get("day")))).days == 1
        except ValueError:
            adjacent = False
        if not adjacent:
            continue  # a missing day cannot locate when the index moved
        left_at, went_to = _float(before.get("index_last")), _float(day.get("index_min"))
        if left_at is None or went_to is None or left_at - went_to < INDEX_FALL:
            continue
        falls.append((round(left_at - went_to, 3), left_at, day))
    if not falls:
        return []
    fall, left_at, day = max(falls, key=lambda f: f[0])
    return [
        _signal(
            "transitions",
            "transition:reliability-index-fall",
            f"Windows' reliability index fell {fall} on {day['day']}",
            "Windows' index changed between adjacent UTC days. "
            + ("Reliability events could not be observed for this reading. " if day.get("records") is None else "The events returned for this day are shown here. ")
            + "The index does not identify which event drove the change.",
            {"day": day["day"], "index_before": left_at, "index_min": _float(day.get("index_min")), "fall": fall, "records": day.get("records"), "event_types": day.get("event_types")},
            ["reliability"],
        )
    ]


def _mismatches(observed: dict[str, Reading]) -> list[dict[str, Any]]:
    """Where two parts of the record disagree, or a part is out of step with the rest."""
    out: list[dict[str, Any]] = []
    gpu = (_fingerprint(observed.get("hardware")).get("gpu")) or {}
    age = _days_since(gpu.get("date"))
    if age is not None and age > STALE_DRIVER_DAYS:
        out.append(
            _signal(
                "mismatches",
                "mismatch:display-driver-age",
                f"The display driver is dated {age} days ago",
                "The driver predates the rest of the configuration by years. Whether that matters depends on what else changed since.",
                {"driver_version": gpu.get("driver_version"), "driver_age_days": age},
                ["hardware"],
            )
        )
    for group in _section(observed.get("pcie"), "groups") or []:
        if group.get("kind") != "root_port":
            continue
        members = group.get("members") or []
        unhealthy = [m for m in members if (m.get("status") and m.get("status") != "OK") or (m.get("problem") and m.get("problem") != "CM_PROB_NONE")]
        if unhealthy and len(members) > 1:
            out.append(
                _signal(
                    "mismatches",
                    f"mismatch:root-port:{group.get('upstream', {}).get('instance_id')}",
                    f"A PCI device under {group.get('upstream', {}).get('name')} reports a non-OK state while others share its reported upstream parent",
                    "These returned devices share a reported upstream PCI link. The different states are a lead to inspect, not a cause established by this reading.",
                    {"root_port": (group.get("upstream") or {}).get("name"), "not_ok": [m.get("name") for m in unhealthy], "members": len(members)},
                    ["pcie"],
                )
            )
    return out


async def take_signals(bridge: Bridge, params: dict[str, Any]) -> Reading:
    """Take every input that is registered, together, then notice what is in them.

    An input that is not registered, that did not observe the machine, or that failed on its
    own is recorded as not observed and the rest carry on: a partial view is still evidence
    as long as the basis says what is missing from it. Nothing here re-reads the machine;
    every fact comes from an input's envelope, with its provenance attached.
    """
    wanted = list(SIGNAL_INPUTS)
    # Limit this reading's share of the bridge. Other callers can still occupy the remaining
    # session; this is a per-call bound, not a global priority lane or a free-session guarantee.
    lanes = asyncio.Semaphore(len(wanted) if bridge_module.POOL_SIZE <= 0 else max(1, bridge_module.POOL_SIZE - 1))

    async def input_in_lane(name: str, want: dict[str, Any]) -> tuple[Reading | None, str | None]:
        async with lanes:
            return await _take_input(name, bridge, want)

    taken = await asyncio.gather(*(input_in_lane(name, want) for name, want in wanted))
    readings: dict[str, Reading | None] = {}
    reasons: dict[str, str] = {}
    for (name, _), (reading, reason) in zip(wanted, taken, strict=True):  # gather answers every input or raises; a mismatch here would be a silent input lost
        readings[name] = reading
        if reason:
            reasons[name] = reason

    signals, basis = take_signals_sync(readings, reasons)
    observed = [name for name, r in readings.items() if r is not None and r.observed]
    input_sources = []
    for name, want in wanted:
        input_reading = readings[name]
        source = {
            "name": name,
            "params": want,
            "outcome": _why(name, input_reading, reasons),
            "took_ms": input_reading.took_ms if input_reading is not None else None,
            "warnings": [_bounded_warning(w) for w in (input_reading.warnings if input_reading is not None else [])[:5]],
            "warnings_total": len(input_reading.warnings) if input_reading is not None else 0,
        }
        if kind := _refined_error_kind(input_reading):
            source["error_kind"] = kind
        input_sources.append(source)

    reading = Reading(
        reading="signals",
        params=params,
        outcome="ok" if (observed and signals) else ("empty" if observed else "unavailable"),
        method={"kind": "readings", "readings": input_sources},
        count=len(signals),
    )
    # Nothing was observed, so nothing here is a finding: the section stays empty and the
    # error says why, as it does for every other reading that did not see the machine.
    reading.sections = [Section("signals", "inferred", signals if observed else [], basis=basis)]
    if not observed:
        reading.count = 0
        reading.error = {"kind": "unavailable", "detail": "no reading this one is made of observed the machine"}
    for name, r in readings.items():
        if r is None or not r.observed:
            reading.warnings.append(f"{name} was not observed ({_why_with_cause(name, r, reasons)}): what it would have shown is absent from these signals.")
        elif r.warnings:
            reading.warnings.append(f"{name} answered with {len(r.warnings)} {'warning' if len(r.warnings) == 1 else 'warnings'}; first: {_bounded_warning(r.warnings[0])}")
    return reading


def _bounded_warning(value: Any) -> str:
    warning = str(value)
    if len(warning) <= 300:
        return warning
    cut = next((index for index in range(296, -1, -1) if warning[index].isspace()), None)
    return warning[:cut] + "..." if cut else "Warning text exceeds 300 characters; take the source reading for full detail."


async def _take_input(name: str, bridge: Bridge, params: dict[str, Any]) -> tuple[Reading | None, str | None]:
    """Look the input up in the catalog now: another reading may register after this module does.

    An input that raises is this reading's missing input, not its failure, so the exception
    becomes the reason it was not observed and the other four still answer.
    """
    if name not in REGISTRY:
        return None, "not registered"
    try:
        return await take(name, bridge, params), None
    except Exception as exc:  # the input's own defect: recorded, never swallowed
        return None, f"the reading raised {type(exc).__name__}: {exc}"[:200]


# ---------------------------------------------------------------------------
# Reading the inputs without assuming they answered
# ---------------------------------------------------------------------------


def _section(reading: Reading | None, name: str) -> Any:
    if reading is None:
        return None
    section = reading.section(name)
    return section.data if section is not None else None


def _derived(reading: Reading | None) -> dict[str, Any]:
    data = _section(reading, "derived")
    return data if isinstance(data, dict) else {}


def _config(reading: Reading | None) -> dict[str, Any]:
    data = _section(reading, "config")
    return data if isinstance(data, dict) else {}


def _fingerprint(reading: Reading | None) -> dict[str, Any]:
    data = _section(reading, "fingerprint")
    return data if isinstance(data, dict) else {}


def _rows(reading: Reading | None, name: str) -> list[dict[str, Any]]:
    data = _section(reading, name)
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _records(reading: Reading | None) -> list[dict[str, Any]]:
    return _rows(reading, "records")


def _days(reading: Reading | None) -> list[dict[str, Any]]:
    """reliability's day-by-day section: a dict whose ``days`` holds the entries, oldest first."""
    data = _section(reading, "days")
    rows = data.get("days") if isinstance(data, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _stamp(records: list[dict[str, Any]], provider: str, *, first: bool) -> str | None:
    stamps = sorted(str(r.get("TimeCreated")) for r in records if r.get("ProviderName") == provider and r.get("TimeCreated"))
    if not stamps:
        return None
    return stamps[0] if first else stamps[-1]


def _first(*values: Any) -> Any:
    return next((v for v in values if v is not None), None)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _seconds_since(stamp: Any) -> int | None:
    moment = _parse(stamp)
    return int((datetime.now(UTC) - moment).total_seconds()) if moment else None


def _days_since(stamp: Any) -> int | None:
    seconds = _seconds_since(stamp)
    return seconds // 86400 if seconds is not None else None


def _parse(stamp: Any) -> datetime | None:
    text = str(stamp or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------

register(
    Spec(
        name="pcie",
        description=(
            "The present PCI device inventory and the parent relations Windows reported. Devices with "
            "complete reported parent chains can be grouped under a PCI root port or root complex; "
            "coverage says when the relation source or any chain is incomplete. Root-port members "
            "share an upstream link; members under a non-PCI parent do not establish that claim. Instance "
            "identifiers and bus addresses are kept to distinguish devices and match hardware errors."
        ),
        classes=("raw", "derived"),
        take=take_pcie,
        heavy=True,
    )
)

register(
    Spec(
        name="power",
        description=(
            "Power configuration and transitions: the sleep states the firmware offers, the link "
            "power management setting, fast startup, what may wake the machine, and the ledger of "
            "starts, shutdowns, sleeps, resumes and the shutdowns the machine did not plan."
        ),
        classes=("raw", "derived"),
        take=take_power,
        private=("the computer name and user names inside transition messages",),
        heavy=True,
    )
)

register(
    Spec(
        name="memory",
        description=(
            "Physical memory and stability signals: what is in each slot, how fast it is actually "
            "running against its rating, whether the kit is homogeneous, how many hardware error "
            "records the machine has written over the window, and the result of Windows' own memory "
            "test if it has run since the System log's oldest record."
        ),
        classes=("raw", "derived"),
        take=take_memory,
        private=("modules[].serial_number",),
        heavy=True,
    )
)

register(
    Spec(
        name="constraints",
        description=(
            "What the machine is not using and why: every present device that is disabled, has no "
            "driver running, or is in error, with the problem the device itself reported. An empty "
            "result is a finding: every present device is working."
        ),
        classes=("raw", "derived"),
        take=take_constraints,
    )
)

register(
    Spec(
        name="signals",
        description=(
            "Forensic signals across the readings: what is suppressed, where the record has a hole, "
            "what is filling the log, what the machine did between states, and where two parts of "
            "the record disagree. Each signal names the readings it drew on and the rule it came "
            "from. These are leads to investigate, never a diagnosis. "
            + SIGNAL_INPUT_DESCRIPTION
            + "Signals takes none of the WHEA readings and does not read the Kernel-WHEA/Errors log. "
            "System WHEA-Logger records can appear among its recent System events, but no signal classifies "
            "them as hardware errors. Take whea or storms for hardware errors. "
            "An ok answer can still lack inputs: read the gap:inputs signal "
            "and each method.readings outcome. Empty means no pattern was noticed in what was observed, "
            "not that the machine is healthy."
        ),
        classes=("inferred",),
        take=take_signals,
        heavy=True,
    )
)
