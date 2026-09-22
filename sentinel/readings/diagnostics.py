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
between runs. ``pnputil /enum-devices /connected /relations /format xml`` reports
the whole device tree in one pass, complete, and its XML element names do not
depend on the display language.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from ..bridge import Bridge
from ..reading import REGISTRY, Reading, Section, Spec, from_object, register, take

# The fabric and the memory ledger nest deeper than the bridge's default depth:
# a group holds members which hold their upstream chains.
DEEP = 8

# ---------------------------------------------------------------------------
# The queries
# ---------------------------------------------------------------------------

PCIE_SCRIPT = r"""
$warnings = @()

$present = @()
try { $present = @(Get-PnpDevice -PresentOnly -ErrorAction Stop) }
catch { $warnings += "Get-PnpDevice did not answer: $($_.Exception.Message)" }

# The parent of every present device, in one pass over the whole device tree.
$parents = @{}
try {
    $text = (& pnputil.exe /enum-devices /connected /relations /format xml 2>&1 | Out-String)
    foreach ($d in ([xml]$text).PnpUtil.Device) {
        if ($d.InstanceId -and $d.Parent) { $parents[[string]$d.InstanceId] = [string]$d.Parent }
    }
    if ($parents.Count -eq 0) { $warnings += 'pnputil reported no parent for any device: the fabric could not be assembled.' }
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

if ($present.Count -gt 0 -and $devices.Count -eq 0) { $warnings += 'No device on this machine is enumerated on the PCI bus.' }

[pscustomobject]@{ devices = $devices; present_devices = $present.Count; warnings = $warnings }
"""

POWER_SCRIPT_TEMPLATE = r"""
$warnings = @()

$sleep_states = @()
try {
    $sleep_states = @((& powercfg.exe /a 2>&1 | Out-String) -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($sleep_states.Count -eq 0) { $warnings += 'powercfg /a produced no output: the supported sleep states were not observed.' }
} catch { $warnings += "powercfg /a did not run: $($_.Exception.Message)" }

$aspm_ac = $null
$aspm_dc = $null
try {
    foreach ($line in ((& powercfg.exe /query SCHEME_CURRENT SUB_PCIEXPRESS ASPM 2>&1 | Out-String) -split "`r?`n")) {
        if ($line -match 'AC Power Setting Index:\s*(0x[0-9a-fA-F]+)') { $aspm_ac = $Matches[1] }
        if ($line -match 'DC Power Setting Index:\s*(0x[0-9a-fA-F]+)') { $aspm_dc = $Matches[1] }
    }
    if ($null -eq $aspm_ac -and $null -eq $aspm_dc) { $warnings += 'powercfg reported no PCI Express link state power management setting for the active scheme.' }
} catch { $warnings += "powercfg /query did not run: $($_.Exception.Message)" }

$wake_armed = @()
try { $wake_armed = @((& powercfg.exe /devicequery wake_armed 2>&1) | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ }) }
catch { $warnings += "powercfg /devicequery did not run: $($_.Exception.Message)" }

$hiberboot = $null
$entry = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled -ErrorAction SilentlyContinue
if ($null -ne $entry) { $hiberboot = [int]$entry.HiberbootEnabled } else { $warnings += 'HiberbootEnabled was not readable: the fast startup setting was not observed.' }

$batteries = @()
try { $batteries = @(Get-CimInstance Win32_Battery -ErrorAction Stop | Select-Object Name, BatteryStatus, EstimatedChargeRemaining) }
catch { $warnings += "Win32_Battery did not answer: $($_.Exception.Message)" }

$boot = $null
try { $boot = (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime } catch { $warnings += "Win32_OperatingSystem did not answer: $($_.Exception.Message)" }

# Every transition the machine records: start and shutdown, sleep and resume, the
# shutdowns it did not plan, and the display driver resets that sit beside them. Each
# provider carries its own event ids, because the same number means different things
# under different providers: one filter hash table over all of them would pull in
# Kernel-General's event 1, a change of the system clock, as though it were a wake.
$query = @'
{query}
'@
$transitions = @()
try {
    $transitions = @(Get-WinEvent -FilterXml ([xml]$query) -MaxEvents 120 -ErrorAction Stop |
        Select-Object RecordId, Id, ProviderName, LevelDisplayName,
            @{Name='TimeCreated'; Expression={ $_.TimeCreated.ToUniversalTime().ToString('o') }},
            Message)
} catch {
    if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { $warnings += "The transition ledger did not read: $($_.Exception.Message)" }
}

[pscustomobject]@{
    sleep_states = $sleep_states
    aspm         = [pscustomobject]@{ ac_index = $aspm_ac; dc_index = $aspm_dc }
    wake_armed   = $wake_armed
    hiberboot_enabled = $hiberboot
    batteries    = $batteries
    boot_time    = $(if ($boot) { $boot.ToUniversalTime().ToString('o') } else { $null })
    transitions  = $transitions
    warnings     = $warnings
}
"""

MEMORY_SCRIPT = r"""
$warnings = @()

$modules = @()
try {
    $modules = @(Get-CimInstance Win32_PhysicalMemory -ErrorAction Stop | Select-Object BankLabel, DeviceLocator, Manufacturer, PartNumber,
        @{Name='serial_number'; Expression={ $_.SerialNumber }},
        Capacity, Speed, ConfiguredClockSpeed, ConfiguredVoltage, MinVoltage, MaxVoltage, SMBIOSMemoryType, TotalWidth, DataWidth)
    if ($modules.Count -eq 0) { $warnings += 'Win32_PhysicalMemory returned no modules.' }
} catch { $warnings += "Win32_PhysicalMemory did not answer: $($_.Exception.Message)" }

$array = $null
try { $array = @(Get-CimInstance Win32_PhysicalMemoryArray -ErrorAction Stop | Select-Object MaxCapacityEx, MaxCapacity, MemoryDevices, MemoryErrorCorrection)[0] }
catch { $warnings += "Win32_PhysicalMemoryArray did not answer: $($_.Exception.Message)" }

# What the machine has said about this memory. The records themselves, decoded, are the
# whea reading; here they are the count and the moments, addressable by their record id.
# A stop the machine did not plan is the crash reading's, not a second ledger here.
$since = (Get-Date).AddDays(-30)
$ledger = @()
try {
    $ledger = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=$since} -ErrorAction Stop |
        Select-Object RecordId, Id, LevelDisplayName, ProviderName,
            @{Name='TimeCreated'; Expression={ $_.TimeCreated.ToUniversalTime().ToString('o') }},
            @{Name='Kind'; Expression={ 'whea' }})
} catch {
    if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { $warnings += "The WHEA ledger did not read: $($_.Exception.Message)" }
}
$ledger = @($ledger | Sort-Object TimeCreated -Descending | Select-Object -First 100)

# Windows' own test of this memory, whenever it last ran. The result lands in the System log,
# so how far back "no result" reaches is the log's oldest record, not the life of the machine.
$diagnostic = $null
try {
    $diagnostic = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-MemoryDiagnostics-Results'} -MaxEvents 1 -ErrorAction Stop |
        Select-Object Id, LevelDisplayName, Message,
            @{Name='TimeCreated'; Expression={ $_.TimeCreated.ToUniversalTime().ToString('o') }})[0]
} catch {
    if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { $warnings += "The memory diagnostic result did not read: $($_.Exception.Message)" }
}

$log_begins = $null
try { $log_begins = (Get-WinEvent -LogName System -Oldest -MaxEvents 1 -ErrorAction Stop).TimeCreated.ToUniversalTime().ToString('o') }
catch {
    if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { $warnings += "The System log's oldest record did not read, so how far back it reaches is unknown: $($_.Exception.Message)" }
}

[pscustomobject]@{
    modules      = $modules
    array        = $array
    ledger       = $ledger
    ledger_days  = 30
    diagnostic   = $diagnostic
    log_begins   = $log_begins
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
    "Each PCI device that is the parent of another PCI device is a bridge; the rest are endpoints. "
    "An endpoint's parent chain is followed while each parent is itself a PCI device, and the last "
    "one reached is its root port. Endpoints reaching the same root port are one group: they share "
    "an upstream link, so a fault on one can present on another. An endpoint with no PCI ancestor "
    "sits on the root complex and is grouped under its own parent. The address is the bus, device "
    "and function the PCI enumerator recorded for the endpoint."
)

_ADDRESS = re.compile(r"\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)\s*$")


def _key(instance_id: Any) -> str:
    """Instance identifiers are spelled in different cases by different Windows interfaces."""
    return str(instance_id or "").upper()


def pcie_topology(devices: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the PCI devices into endpoints and bridges and group the endpoints by root port."""
    by_id = {_key(d.get("InstanceId")): d for d in devices}
    parented = Counter(_key(d.get("Parent")) for d in devices if d.get("Parent"))

    endpoints = [d for d in devices if not parented.get(_key(d.get("InstanceId")))]
    roots = [d for d in devices if parented.get(_key(d.get("InstanceId")))]

    groups: dict[str, dict[str, Any]] = {}
    for endpoint in endpoints:
        chain = _upstream(endpoint, by_id)
        root = by_id[_key(chain[-1])] if chain else None
        key = _key(chain[-1]) if chain else _key(endpoint.get("Parent"))
        group = groups.setdefault(
            key,
            {
                "root_port": {
                    "instance_id": (root or {}).get("InstanceId") or endpoint.get("Parent"),
                    "name": (root or {}).get("Name") or endpoint.get("ParentName"),
                },
                "members": [],
            },
        )
        group["members"].append(
            {
                "name": endpoint.get("Name"),
                "instance_id": endpoint.get("InstanceId"),
                "class": endpoint.get("Class"),
                "status": endpoint.get("Status"),
                "problem": endpoint.get("Problem"),
                "address": pcie_address(endpoint.get("Location")),
                "upstream": chain,
            }
        )

    ordered = sorted(groups.values(), key=lambda g: (-len(g["members"]), str(g["root_port"]["name"] or "")))
    return endpoints, roots, ordered


def _upstream(device: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[str]:
    """The instance ids from this device's parent up to the last PCI ancestor, nearest first."""
    chain: list[str] = []
    seen: set[str] = set()
    parent = _key(device.get("Parent"))
    while parent and parent in by_id and parent not in seen:
        seen.add(parent)
        chain.append(by_id[parent]["InstanceId"])
        parent = _key(by_id[parent].get("Parent"))
    return chain


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
        endpoints, roots, groups = pcie_topology(list(payload.get("devices") or []))
        return [
            Section("endpoints", "raw", endpoints),
            Section("roots", "raw", roots),
            Section("groups", "derived", groups, basis=GROUPS_BASIS),
        ]

    reading = from_object("pcie", params, PCIE_SCRIPT, result, build)
    endpoints, roots = reading.section("endpoints"), reading.section("roots")
    if endpoints is not None and roots is not None:
        reading.count = len(endpoints.data)
        if reading.outcome == "ok" and not endpoints.data and not roots.data:
            reading.outcome = "empty"  # nothing is enumerated on the PCI bus: a finding, not an absence
    return reading


# ---------------------------------------------------------------------------
# power
# ---------------------------------------------------------------------------

POWER_BASIS = (
    "The sleep model is read from the states powercfg lists as available; the link state power "
    "management setting is the documented index (0 off, 1 L0s, 2 L1, 3 L0s and L1); fast startup "
    "is HiberbootEnabled; the power source is the battery's status, or mains where the machine has "
    "no battery. Each ledger record is named by its provider and event id together, and the window "
    "is the span the returned records actually cover."
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


def power_source(batteries: list[dict[str, Any]]) -> str:
    if not batteries:
        return "mains (no battery is present)"
    status = batteries[0].get("BatteryStatus")
    if status == 1:
        return "battery (discharging)"
    if status == 2:
        return "mains (battery present)"
    return "battery present, status not reported as charging or discharging"


def transition_kind(record: dict[str, Any]) -> str:
    """A record is named by its provider and id together, or left unnamed rather than guessed."""
    event_id = _int(record.get("Id"))
    if event_id is None:
        return "unnamed transition"
    return TRANSITIONS.get((str(record.get("ProviderName") or ""), event_id), "unnamed transition")


def power_derived(payload: dict[str, Any]) -> dict[str, Any]:
    transitions = list(payload.get("transitions") or [])
    stamps = sorted(str(t.get("TimeCreated")) for t in transitions if t.get("TimeCreated"))
    counts = Counter(transition_kind(t) for t in transitions)
    armed = [str(w) for w in (payload.get("wake_armed") or []) if str(w).strip().lower() != _NONE]
    hiberboot = payload.get("hiberboot_enabled")
    return {
        "sleep_model": sleep_model(payload.get("sleep_states") or []),
        "power_source": power_source(list(payload.get("batteries") or [])),
        "link_power_management": {"ac": _aspm((payload.get("aspm") or {}).get("ac_index")), "dc": _aspm((payload.get("aspm") or {}).get("dc_index"))},
        "fast_startup": None if hiberboot is None else bool(hiberboot),
        "wake_armed": armed,
        "wake_armed_count": len(armed),
        "uptime_seconds": _seconds_since(payload.get("boot_time")),
        "ledger": {
            "records": len(transitions),
            "counts": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "window": {"first": stamps[0] if stamps else None, "last": stamps[-1] if stamps else None},
        },
    }


def take_power(bridge: Bridge, params: dict[str, Any]) -> Reading:
    script = power_script()
    result = bridge.run(script, depth=DEEP)

    def build(payload: dict[str, Any]) -> list[Section]:
        return [Section("raw", "raw", payload), Section("derived", "derived", power_derived(payload), basis=POWER_BASIS)]

    return from_object("power", params, script, result, build)


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------

MEMORY_BASIS = (
    "Populated slots are counted from returned modules. Total slots are reported only when the "
    "physical array names a plausible total; free slots also require returned modules. An unreadable "
    "or contradictory total is unknown. Installed capacity requires a reported capacity for every module. "
    "The kit is the set of "
    "distinct manufacturer and part numbers, so more than one is a mixed kit. Error correction is "
    "read from the module's total width exceeding its data width. A module runs below its rating "
    "where its configured clock is under its rated speed. The ledger counts the WHEA records over "
    "the window; the records themselves, decoded, are the whea reading, and the stops the machine "
    "did not plan are the crash reading. The memory diagnostic is the latest "
    "Microsoft-Windows-MemoryDiagnostics-Results record in the System log: no result means no "
    "result since the log's oldest record, stated beside it, and not that the test was never run."
)

_ECC_NONE = (2, 3)  # Win32_PhysicalMemoryArray: 2 none, 3 parity


def memory_derived(payload: dict[str, Any]) -> dict[str, Any]:
    modules = list(payload.get("modules") or [])
    array = payload.get("array") or {}
    ledger = list(payload.get("ledger") or [])

    slots_used = len(modules)
    reported_slots = _int(array.get("MemoryDevices"))
    slots_total = reported_slots if reported_slots is not None and reported_slots > 0 and reported_slots >= slots_used else None
    capacities = [_int(m.get("Capacity")) for m in modules]
    capacity_known = bool(capacities) and all(value is not None and value > 0 for value in capacities)
    kits = sorted({f"{m.get('Manufacturer') or '?'} {m.get('PartNumber') or '?'}".strip() for m in modules})
    below_rating = [
        m.get("DeviceLocator")
        for m in modules
        if _int(m.get("Speed")) and _int(m.get("ConfiguredClockSpeed")) and _int(m.get("ConfiguredClockSpeed")) < _int(m.get("Speed"))
    ]
    counts = Counter(str(e.get("Kind")) for e in ledger)
    stamps = sorted(str(e.get("TimeCreated")) for e in ledger if e.get("TimeCreated"))
    diagnostic = payload.get("diagnostic") if isinstance(payload.get("diagnostic"), dict) else None

    return {
        "installed_gb": round(sum(value or 0 for value in capacities) / 1024**3, 2) if capacity_known else None,
        "slots_used": slots_used,
        "slots_total": slots_total,
        "slots_free": slots_total - slots_used if slots_total is not None and modules else None,
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
        ],
        "kits": kits,
        "mixed_kit": len(kits) > 1,
        "below_rated_speed": below_rating,
        "array_error_correction": _int(array.get("MemoryErrorCorrection")) not in _ECC_NONE if array.get("MemoryErrorCorrection") is not None else None,
        "ledger": {
            "window_days": payload.get("ledger_days"),
            "records": len(ledger),
            "counts": dict(sorted(counts.items())),
            "most_recent": stamps[-1] if stamps else None,
        },
        "memory_diagnostic": {
            "last_result": {key: diagnostic.get(key) for key in ("Id", "TimeCreated", "LevelDisplayName", "Message")} if diagnostic else None,
            "log_begins": payload.get("log_begins"),
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

    def build(payload: dict[str, Any]) -> list[Section]:
        return [Section("raw", "raw", payload), Section("derived", "derived", memory_derived(payload), basis=MEMORY_BASIS)]

    reading = from_object("memory", params, MEMORY_SCRIPT, result, build)
    raw = reading.section("raw")
    if raw is not None:
        reading.count = len(raw.data.get("modules") or [])
    return reading


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

SIGNAL_INPUTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("hardware", {}),
    ("pcie", {}),
    ("power", {}),
    ("constraints", {}),
    ("events", {"levels": [1, 2, 3, 4], "count": 200}),
    ("crash", {"count": 20}),
    ("reliability", {}),
)

# A provider's share of the recent records, not a count: the window is whatever the log
# held. Below TALKATIVE it is not worth naming; at or above LOUD one source is most of it.
TALKATIVE = 0.10
LOUD = 0.25
TOP_TALKERS = 3
STALE_DRIVER_DAYS = 730
LONG_UPTIME_DAYS = 7
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


def _basis(readings: dict[str, Reading | None], reasons: dict[str, str]) -> str:
    seen = [name for name, r in readings.items() if r is not None and r.observed]
    missed = [f"{name} ({_why(name, r, reasons)})" for name, r in readings.items() if r is None or not r.observed]
    parts = [
        "Each signal is a pattern noticed across the readings named on it, not a diagnosis; the rule is on the signal.",
        f"Observed: {', '.join(seen) if seen else 'nothing'}.",
    ]
    if missed:
        parts.append(f"Not observed, so anything they would have shown is absent from this reading: {', '.join(missed)}.")
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
                "Fast startup is on",
                "A shutdown hibernates the kernel instead of ending it, so the next start does not re-initialize the hardware and a fault that a cold start would show can persist unseen.",
                {"fast_startup": True},
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

    stops = _rows(observed.get("crash"), "stops")

    unexpected = sum(count for name, count in counts.items() if "unexpected shutdown" in name)
    if unexpected:
        evidence: dict[str, Any] = {"records": unexpected, "window": window}
        readings = ["power"]
        if stops:
            # The ledger counts the records; crash has already composed them into stops, so the
            # signal can name them here rather than send the reader back to the log for them.
            evidence["stops"] = [_stop_facts(stop) for stop in stops[:NEWEST_STOPS]]
            readings.append("crash")
        out.append(
            _signal(
                "transitions",
                "transition:unexpected-shutdown",
                f"{unexpected} unexpected shutdown record(s) in the ledger",
                "The machine stopped without a clean shutdown at least once in the window. Take the record reading before each of these moments to see what it was doing.",
                evidence,
                readings,
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
    uptime = derived.get("uptime_seconds")
    if uptime and derived.get("fast_startup") and uptime > LONG_UPTIME_DAYS * 86400:
        out.append(
            _signal(
                "transitions",
                "transition:no-cold-start",
                f"The machine has not cold started in {uptime // 86400} days",
                "With fast startup on, the hardware has not been fully re-initialized in that time, so a fault cleared only by a cold start would still be here.",
                {"uptime_days": uptime // 86400, "fast_startup": True},
                ["power"],
            )
        )
    out += _index_fall(_days(observed.get("reliability")))
    return out


def _stop_facts(stop: dict[str, Any]) -> dict[str, Any]:
    bugcheck = stop.get("bugcheck") or {}
    return {"started_at": stop.get("started_at"), "code": bugcheck.get("code"), "name": bugcheck.get("name")}


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
        members = group.get("members") or []
        unhealthy = [m for m in members if m.get("status") and m.get("status") != "OK"]
        if unhealthy and len(members) > 1:
            out.append(
                _signal(
                    "mismatches",
                    f"mismatch:root-port:{group.get('root_port', {}).get('instance_id')}",
                    f"One endpoint under {group.get('root_port', {}).get('name')} is not healthy while others are",
                    "Endpoints under one root port share an upstream link. A fault on one of them can have a cause the others also sit behind.",
                    {"root_port": (group.get("root_port") or {}).get("name"), "not_ok": [m.get("name") for m in unhealthy], "members": len(members)},
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
    taken = await asyncio.gather(*(_take_input(name, bridge, want) for name, want in wanted))
    readings: dict[str, Reading | None] = {}
    reasons: dict[str, str] = {}
    for (name, _), (reading, reason) in zip(wanted, taken, strict=True):  # gather answers every input or raises; a mismatch here would be a silent input lost
        readings[name] = reading
        if reason:
            reasons[name] = reason

    signals, basis = take_signals_sync(readings, reasons)
    observed = [name for name, r in readings.items() if r is not None and r.observed]

    reading = Reading(
        reading="signals",
        params=params,
        outcome="ok" if (observed and signals) else ("empty" if observed else "unavailable"),
        method={
            "kind": "readings",
            "readings": [
                {"name": name, "params": want, "outcome": _why(name, readings[name], reasons), "took_ms": (readings[name].took_ms if readings[name] is not None else None)}
                for name, want in wanted
            ],
        },
        took_ms=max((r.took_ms for r in readings.values() if r is not None), default=0),
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
            reading.warnings.append(f"{name} was not observed ({_why(name, r, reasons)}): what it would have shown is absent from these signals.")
    return reading


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
            "The PCIe fabric: every device on the PCI bus, split into the bridges that carry the "
            "tree and the endpoints hanging off it, grouped by the root port they share. Endpoints "
            "under one root port share an upstream link, which is why a fault on one can present on "
            "another. Device instance identifiers and bus addresses are kept: they are how endpoints "
            "are told apart and how a hardware error record is matched to a device."
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
            "from. These are leads to investigate, never a diagnosis."
        ),
        classes=("inferred",),
        take=take_signals,
        heavy=True,
    )
)
