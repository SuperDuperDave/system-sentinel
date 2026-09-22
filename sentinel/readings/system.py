"""The machine: what it is, how it is configured, and what it is carrying.

``system`` is the snapshot a person looks at first. ``hardware`` is the
fingerprint and the configuration, with the observations that follow from the
configuration kept apart from it. The five ``hardware.*`` readings are the deep
look at one subsystem each, taken on demand because each costs seconds.
``drivers`` is what was most recently signed and dated.

Every script here returns what Windows said and nothing else. The arithmetic and
the observations are made below, in Python, where one function holds each rule
and a test can hold the function to it. A sub-query that failed inside a reading
that otherwise answered comes back in the payload's ``warnings`` and is lifted
into the envelope: an absence the tool could not look at is never silent.

The queries are the old backend's, kept where they answer: ``collectors/system.py``,
``services/system_info.py`` and ``services/domains/hardware/``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..bridge import Bridge
from ..reading import Param, Reading, Section, Spec, from_bridge, from_object, register

# The hardware payloads nest four and five deep (a disk holds its volumes, an
# adapter its addresses); the bridge's default serialization depth would flatten
# the innermost object into a type name.
DEEP = 8

# ---------------------------------------------------------------------------
# The queries
# ---------------------------------------------------------------------------

SYSTEM_SCRIPT = r"""
$os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
$boot = $os.LastBootUpTime
[pscustomobject]@{
    os_caption             = $os.Caption
    os_version             = $os.Version
    os_build               = $os.BuildNumber
    architecture           = $os.OSArchitecture
    boot_time              = $boot.ToUniversalTime().ToString('o')
    uptime_seconds         = [int]((Get-Date) - $boot).TotalSeconds
    processor_load_percent = (Get-CimInstance Win32_Processor -ErrorAction Stop | Measure-Object -Property LoadPercentage -Average).Average
    memory_total_kb        = [int64]$os.TotalVisibleMemorySize
    memory_free_kb         = [int64]$os.FreePhysicalMemory
}
"""

HARDWARE_SCRIPT = r"""
$warnings = @()
$cpu   = @(Get-CimInstance Win32_Processor)[0]
# A virtual display adapter can sort ahead of the card: the fingerprint wants the
# largest display adapter on the PCI bus, and says so when there is none.
$gpus  = @(Get-CimInstance Win32_VideoController)
$gpu   = @($gpus | Where-Object { $_.PNPDeviceID -like 'PCI\*' } | Sort-Object AdapterRAM -Descending)[0]
if (-not $gpu) {
    $gpu = $gpus[0]
    if ($gpu) { $warnings += 'No display adapter is on the PCI bus; the first adapter Windows listed was fingerprinted.' }
}
$board = @(Get-CimInstance Win32_BaseBoard)[0]
$bios  = @(Get-CimInstance Win32_BIOS)[0]
$os    = Get-CimInstance Win32_OperatingSystem
$disk  = @(Get-CimInstance Win32_DiskDrive | Where-Object { $_.Index -eq 0 })[0]
if (-not $cpu)   { $warnings += 'Win32_Processor returned nothing.' }
if (-not $gpu)   { $warnings += 'Win32_VideoController returned nothing.' }
if (-not $board) { $warnings += 'Win32_BaseBoard returned nothing.' }
if (-not $disk)  { $warnings += 'No disk is at index 0.' }

$secure_boot = $null
$value = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\SecureBoot\State' -Name UEFISecureBootEnabled -ErrorAction SilentlyContinue).UEFISecureBootEnabled
if ($null -ne $value) { $secure_boot = ($value -eq 1) }

$fast_startup = $null
$value = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled -ErrorAction SilentlyContinue).HiberbootEnabled
if ($null -ne $value) { $fast_startup = ($value -eq 1) }

$free_percent = $null
$drive = Get-PSDrive -Name C -ErrorAction SilentlyContinue
if ($drive -and (($drive.Used + $drive.Free) -gt 0)) {
    $free_percent = [math]::Round(($drive.Free / ($drive.Used + $drive.Free)) * 100, 1)
} else {
    $warnings += 'The C drive did not report its free space.'
}

$uptime = $null
if ($os.LastBootUpTime) { $uptime = [int]((Get-Date) - $os.LastBootUpTime).TotalSeconds }

[pscustomobject]@{
    fingerprint = [pscustomobject]@{
        cpu = [pscustomobject]@{
            name         = $(if ($cpu.Name) { $cpu.Name.Trim() } else { $null })
            cores        = $cpu.NumberOfCores
            logical      = $cpu.NumberOfLogicalProcessors
            manufacturer = $cpu.Manufacturer
            description  = $cpu.Description
        }
        gpu = [pscustomobject]@{
            name           = $gpu.Name
            driver_version = $gpu.DriverVersion
            vram_mb        = $(if ($gpu.AdapterRAM) { [int]($gpu.AdapterRAM / 1MB) } else { $null })
            date           = $(if ($gpu.DriverDate) { $gpu.DriverDate.ToString('yyyy-MM-dd') } else { $null })
        }
        board = [pscustomobject]@{
            product      = $(if ($board.Product) { $board.Product.Trim() } else { $null })
            manufacturer = $(if ($board.Manufacturer) { $board.Manufacturer.Trim() } else { $null })
            version      = $(if ($board.Version) { $board.Version.Trim() } else { $null })
            bios_version = $(if ($bios.SMBIOSBIOSVersion) { $bios.SMBIOSBIOSVersion.Trim() } else { $null })
            bios_date    = $(if ($bios.ReleaseDate) { $bios.ReleaseDate.ToString('yyyy-MM-dd') } else { $null })
        }
        storage = [pscustomobject]@{
            disk0_model = $disk.Model
            size_gb    = $(if ($disk.Size) { [math]::Round($disk.Size / 1GB, 0) } else { $null })
            media_type = $disk.MediaType
            interface  = $disk.InterfaceType
        }
    }
    config = [pscustomobject]@{
        secure_boot               = $secure_boot
        fast_startup              = $fast_startup
        virtualization_firmware   = $cpu.VirtualizationFirmwareEnabled
        hyperv_running            = ((Get-Service vmms -ErrorAction SilentlyContinue).Status -eq 'Running')
        os_version                = "$($os.Version) ($($os.BuildNumber))"
        uptime_seconds            = $uptime
        system_drive_free_percent = $free_percent
    }
    warnings = $warnings
}
"""

CPU_SCRIPT = r"""
$warnings = @()
# ProcessorId is the CPUID feature signature, the same on every chip of a model;
# it is kept because it pins the stepping a microcode question turns on.
$processors = @(Get-CimInstance Win32_Processor | ForEach-Object {
    [pscustomobject]@{
        name                            = $_.Name
        manufacturer                    = $_.Manufacturer
        description                     = $_.Description
        socket                          = $_.SocketDesignation
        processor_id                    = $_.ProcessorId
        revision                        = $_.Revision
        stepping                        = $_.Stepping
        max_clock_mhz                   = $_.MaxClockSpeed
        cores                           = $_.NumberOfCores
        logical_processors              = $_.NumberOfLogicalProcessors
        l2_cache_kb                     = $_.L2CacheSize
        l3_cache_kb                     = $_.L3CacheSize
        virtualization_firmware_enabled = $_.VirtualizationFirmwareEnabled
        load_percent                    = $_.LoadPercentage
    }
})
if ($processors.Count -eq 0) { $warnings += 'Win32_Processor returned nothing.' }

$dg = @(Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction SilentlyContinue)[0]
if (-not $dg) { $warnings += 'Win32_DeviceGuard did not answer: virtualization-based security was not observed.' }

$vmms = Get-Service vmms -ErrorAction SilentlyContinue
if (-not $vmms) { $warnings += 'The vmms service was not found: the Hyper-V state was not observed.' }

[pscustomobject]@{
    processors = $processors
    device_guard = $(if ($dg) { [pscustomobject]@{
        vbs_status          = $dg.VirtualizationBasedSecurityStatus
        services_configured = @($dg.SecurityServicesConfigured)
        services_running    = @($dg.SecurityServicesRunning)
    } } else { $null })
    hyperv_service = $(if ($vmms) { [pscustomobject]@{
        name       = 'vmms'
        status     = "$($vmms.Status)"
        start_type = "$($vmms.StartType)"
    } } else { $null })
    caches = @(Get-CimInstance Win32_CacheMemory | ForEach-Object {
        [pscustomobject]@{
            device_id         = $_.DeviceId
            level             = $_.Level
            max_cache_size_kb = $_.MaxCacheSize
            installed_size_kb = $_.InstalledSize
            cache_type        = $_.CacheType
            status            = $_.Status
        }
    })
    warnings = $warnings
}
"""

GPU_SCRIPT = r"""
$warnings = @()
$adapters = @(Get-CimInstance Win32_VideoController | ForEach-Object {
    [pscustomobject]@{
        name                      = $_.Name
        video_processor           = $_.VideoProcessor
        adapter_ram_bytes         = $_.AdapterRAM
        video_mode                = $_.VideoModeDescription
        refresh_hz                = $_.CurrentRefreshRate
        horizontal_resolution     = $_.CurrentHorizontalResolution
        vertical_resolution       = $_.CurrentVerticalResolution
        driver_version            = $_.DriverVersion
        driver_date               = $(if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') } else { $null })
        inf_file                  = $_.InfFilename
        installed_display_drivers = $_.InstalledDisplayDrivers
        status                    = $_.Status
        config_manager_error_code = $_.ConfigManagerErrorCode
    }
})
if ($adapters.Count -eq 0) { $warnings += 'Win32_VideoController returned nothing.' }

$tdr = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers' -ErrorAction SilentlyContinue
if (-not $tdr) { $warnings += 'The GraphicsDrivers key was not readable: the timeout detection settings were not observed.' }

[pscustomobject]@{
    adapters = $adapters
    tdr_registry = [pscustomobject]@{
        TdrLevel    = $tdr.TdrLevel
        TdrDelay    = $tdr.TdrDelay
        TdrDdiDelay = $tdr.TdrDdiDelay
    }
    warnings = $warnings
}
"""

BOARD_SCRIPT = r"""
$warnings = @()
$board = @(Get-CimInstance Win32_BaseBoard)[0]
$bios  = @(Get-CimInstance Win32_BIOS)[0]
if (-not $board) { $warnings += 'Win32_BaseBoard returned nothing.' }
if (-not $bios)  { $warnings += 'Win32_BIOS returned nothing.' }

# Get-Tpm does not throw without an elevated session: it returns the refusal as a
# string. A TPM state that was never observed must not arrive as a set of nulls.
$state = $null
$t = Get-Tpm -ErrorAction SilentlyContinue
if ($t -and $null -ne $t.TpmPresent) {
    $state = $t
} else {
    $warnings += "Get-Tpm did not report the TPM state: $(if ($t) { "$t" } else { 'it returned nothing' })"
}
$wmi = @(Get-CimInstance -Namespace root\CIMV2\Security\MicrosoftTpm -ClassName Win32_Tpm -ErrorAction SilentlyContinue)[0]
if (-not $wmi) { $warnings += 'Win32_Tpm did not answer: the TPM specification version was not observed.' }

$tpm = $null
if ($state -or $wmi) {
    $tpm = [pscustomobject]@{
        present            = $state.TpmPresent
        ready              = $state.TpmReady
        enabled            = $state.TpmEnabled
        activated          = $state.TpmActivated
        owned              = $state.TpmOwned
        managed_auth_level = $(if ($state) { "$($state.ManagedAuthLevel)" } else { $null })
        spec_version       = $wmi.SpecVersion
        manufacturer       = $wmi.ManufacturerIdTxt
    }
}

$sb = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\SecureBoot\State' -Name UEFISecureBootEnabled -ErrorAction SilentlyContinue).UEFISecureBootEnabled

[pscustomobject]@{
    board = [pscustomobject]@{
        manufacturer  = $(if ($board.Manufacturer) { $board.Manufacturer.Trim() } else { $null })
        product       = $(if ($board.Product) { $board.Product.Trim() } else { $null })
        version       = $(if ($board.Version) { $board.Version.Trim() } else { $null })
        serial        = $(if ($board.SerialNumber) { $board.SerialNumber.Trim() } else { $null })
        hosting_board = $board.HostingBoard
    }
    bios = [pscustomobject]@{
        manufacturer   = $(if ($bios.Manufacturer) { $bios.Manufacturer.Trim() } else { $null })
        smbios_version = $(if ($bios.SMBIOSBIOSVersion) { $bios.SMBIOSBIOSVersion.Trim() } else { $null })
        version        = $(if ($bios.Version) { $bios.Version.Trim() } else { $null })
        release_date   = $(if ($bios.ReleaseDate) { $bios.ReleaseDate.ToString('yyyy-MM-dd') } else { $null })
        caption        = $bios.Caption
    }
    tpm = $tpm
    firmware = [pscustomobject]@{
        firmware_type              = $env:firmware_type
        uefi_secure_boot_enabled   = $sb
        dma_security_key_present   = (Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Control\DmaSecurity')
    }
    warnings = $warnings
}
"""

STORAGE_SCRIPT = r"""
$warnings = @()
$partitions = @(Get-Partition -ErrorAction SilentlyContinue)
$volumes = @{}
foreach ($v in @(Get-Volume -ErrorAction SilentlyContinue)) {
    if ($v.DriveLetter) { $volumes["$($v.DriveLetter)"] = $v }
}
$disks = @(Get-PhysicalDisk -ErrorAction SilentlyContinue | Sort-Object DeviceId | ForEach-Object {
    $disk = $_
    $rc = $disk | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue -ErrorVariable rcErr
    if (-not $rc) {
        $why = (@($rcErr) | ForEach-Object { $_.Exception.Message }) -join ' '
        if (-not $why) { $why = 'the drive did not report them' }
        $warnings += "Reliability counters were not read for disk $($disk.DeviceId): $why"
    }
    $vols = @()
    foreach ($p in ($partitions | Where-Object { "$($_.DiskNumber)" -eq "$($disk.DeviceId)" })) {
        if ($p.DriveLetter -and $volumes.ContainsKey("$($p.DriveLetter)")) {
            $v = $volumes["$($p.DriveLetter)"]
            $vols += [pscustomobject]@{
                drive_letter         = "$($v.DriveLetter)"
                label                = $v.FileSystemLabel
                file_system          = $v.FileSystem
                size_bytes           = $v.Size
                size_remaining_bytes = $v.SizeRemaining
            }
        }
    }
    [pscustomobject]@{
        device_id          = "$($disk.DeviceId)"
        friendly_name      = $disk.FriendlyName
        serial_number      = $(if ($disk.SerialNumber) { $disk.SerialNumber.Trim() } else { $null })
        firmware_version   = $disk.FirmwareVersion
        bus_type           = "$($disk.BusType)"
        media_type         = "$($disk.MediaType)"
        health_status      = "$($disk.HealthStatus)"
        operational_status = (@($disk.OperationalStatus) -join ', ')
        size_bytes         = $disk.Size
        reliability        = $(if ($rc) { [pscustomobject]@{
            wear_percent      = $rc.Wear
            temperature_c     = $rc.Temperature
            temperature_max_c = $rc.TemperatureMax
            power_on_hours    = $rc.PowerOnHours
            read_errors_total = $rc.ReadErrorsTotal
            write_errors_total = $rc.WriteErrorsTotal
            start_stop_cycles = $rc.StartStopCycleCount
        } } else { $null })
        volumes = $vols
    }
})
if ($disks.Count -eq 0) { $warnings += 'Get-PhysicalDisk returned nothing.' }
[pscustomobject]@{ disks = $disks; warnings = $warnings }
"""

NETWORK_SCRIPT = r"""
$warnings = @()
$configs = @{}
$configAnswered = $true
try {
    foreach ($c in @(Get-NetIPConfiguration -All -ErrorAction Stop)) { $configs["$($c.InterfaceIndex)"] = $c }
} catch {
    # Windows can fail while assembling one NetIPConfiguration object on a host with several
    # adapters. Keep the adapter inventory and say that the IP portion was not observed.
    $configAnswered = $false
    $warnings += 'Get-NetIPConfiguration did not answer: IP addresses, gateways and DNS were not observed.'
}
# Get-PnpDevice is asked once for the whole network class, not once per adapter.
$pnp = @{}
foreach ($p in @(Get-PnpDevice -Class Net -ErrorAction SilentlyContinue)) { $pnp["$($p.InstanceId)"] = $p }

$adapters = @(Get-NetAdapter -ErrorAction SilentlyContinue | ForEach-Object {
    $a = $_
    $c = $configs["$($a.InterfaceIndex)"]
    $p = $pnp["$($a.PnPDeviceID)"]
    $dhcp = $null
    if ($c -and $c.NetIPv4Interface -and $c.NetIPv4Interface.Dhcp) { $dhcp = "$($c.NetIPv4Interface.Dhcp)" }
    [pscustomobject]@{
        name                      = $a.Name
        interface_description     = $a.InterfaceDescription
        interface_index           = $a.InterfaceIndex
        mac_address               = $a.MacAddress
        status                    = "$($a.Status)"
        admin_status              = "$($a.AdminStatus)"
        link_speed                = $a.LinkSpeed
        media_type                = $a.MediaType
        media_connection_state    = "$($a.MediaConnectionState)"
        hardware_interface        = $a.HardwareInterface
        virtual                   = $a.Virtual
        pnp_device_id             = $a.PnPDeviceID
        config_manager_error_code = $(if ($p) { $p.ConfigManagerErrorCode } else { $null })
        driver = [pscustomobject]@{
            provider    = $a.DriverProvider
            version     = $a.DriverVersionString
            date        = $a.DriverDate
            file        = $a.DriverFileName
            description = $a.DriverDescription
        }
        ip = $(if ($configAnswered -and $c) { [pscustomobject]@{
            ipv4    = @($c.IPv4Address.IPAddress | Where-Object { $_ })
            ipv6    = @($c.IPv6Address.IPAddress | Where-Object { $_ })
            gateway = @($c.IPv4DefaultGateway.NextHop | Where-Object { $_ })
            dns     = @($c.DNSServer.ServerAddresses | Where-Object { $_ })
            dhcp    = $dhcp
        } } else { $null })
    }
})
if ($adapters.Count -eq 0) { $warnings += 'Get-NetAdapter returned nothing.' }
[pscustomobject]@{ adapters = $adapters; warnings = $warnings }
"""


def drivers_script(count: int) -> str:
    """The signed drivers the machine is running, most recently dated first.

    Win32_PnPSignedDriver is the only class that reports every bound driver with
    its date, inbox drivers included; the sort and the cut happen on the host so
    only the records asked for cross the bridge.
    """
    return (
        r"""
Get-CimInstance Win32_PnPSignedDriver -ErrorAction Stop |
    Where-Object { $_.DriverDate } |
    Sort-Object DriverDate -Descending |
    Select-Object -First """
        + str(int(count))
        + r""" |
    ForEach-Object {
        [pscustomobject]@{
            device_name     = $_.DeviceName
            device_class    = $_.DeviceClass
            driver_version  = $_.DriverVersion
            driver_provider = $_.DriverProviderName
            driver_date     = $_.DriverDate.ToString('yyyy-MM-dd')
            inf_name        = $_.InfName
        }
    }
"""
    )


# ---------------------------------------------------------------------------
# Arithmetic the derived sections are made of
# ---------------------------------------------------------------------------


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _gb(value: Any, places: int = 1) -> float | int | None:
    n = _number(value)
    if n is None:
        return None
    return int(round(n / 1024**3)) if places == 0 else round(n / 1024**3, places)


def _mb(value: Any) -> int | None:
    n = _number(value)
    return int(n / 1024**2) if n is not None else None


def _days_since(text: Any) -> int | None:
    """Whole days from a ``yyyy-MM-dd`` driver date to today, UTC."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        when = datetime.strptime(text.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (datetime.now(UTC).date() - when).days


def _percent_used(size: Any, remaining: Any) -> float | None:
    total, left = _number(size), _number(remaining)
    if total is None or left is None or total <= 0:
        return None
    return round(((total - left) / total) * 100, 1)


# ---------------------------------------------------------------------------
# system
# ---------------------------------------------------------------------------


def take_system(bridge: Bridge, params: dict[str, Any]) -> Reading:
    result = bridge.run(SYSTEM_SCRIPT)
    return from_bridge("system", params, SYSTEM_SCRIPT, result, section="snapshot", shape="object")


# ---------------------------------------------------------------------------
# hardware
# ---------------------------------------------------------------------------

RISK_BASIS = (
    "Four rules over the config section: Secure Boot reported disabled; Fast Startup reported enabled; "
    "the system drive below 10 percent free; uptime above seven days. Each is an observation about the "
    "configuration, not a cause and not a recommendation."
)

_WEEK_SECONDS = 7 * 24 * 60 * 60


def hardware_risks(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The observations the configuration supports. A value the machine did not report yields none."""
    out: list[dict[str, Any]] = []
    if config.get("secure_boot") is False:
        out.append({"id": "secure-boot-off", "observation": "Secure Boot is disabled", "domain": "firmware"})
    if config.get("fast_startup") is True:
        out.append({"id": "fast-startup-on", "observation": "Fast Startup is enabled", "domain": "power"})
    free = _number(config.get("system_drive_free_percent"))
    if free is not None and free < 10:
        out.append({"id": "system-drive-low", "observation": "System drive below 10% free", "domain": "storage"})
    uptime = _number(config.get("uptime_seconds"))
    if uptime is not None and uptime > _WEEK_SECONDS:
        out.append({"id": "uptime-long", "observation": "Uptime exceeds 7 days", "domain": "system"})
    return out


FINGERPRINT_BASIS = (
    "selected from the current Windows inventory: first processor and baseboard, the PCI display "
    "adapter with the most reported memory (or the first display adapter), BIOS, and disk index 0; "
    "these identifiers and versions can change"
)


def take_hardware(bridge: Bridge, params: dict[str, Any]) -> Reading:
    result = bridge.run(HARDWARE_SCRIPT, depth=DEEP)

    def build(payload: dict[str, Any]) -> list[Section]:
        config = payload.get("config") or {}
        return [
            Section("fingerprint", "derived", payload.get("fingerprint"), basis=FINGERPRINT_BASIS),
            Section("config", "raw", config),
            Section("risks", "inferred", hardware_risks(config), basis=RISK_BASIS),
        ]

    return from_object("hardware", params, HARDWARE_SCRIPT, result, build)


# ---------------------------------------------------------------------------
# hardware.cpu
# ---------------------------------------------------------------------------

CPU_BASIS = (
    "Virtualization-based security from Win32_DeviceGuard's VirtualizationBasedSecurityStatus "
    "(0 off, 1 configured, 2 running); Hyper-V from the vmms service's state; threads per core from "
    "logical processors divided by cores."
)


def cpu_derived(payload: dict[str, Any]) -> dict[str, Any]:
    guard = payload.get("device_guard") or {}
    status = _number(guard.get("vbs_status"))
    service = payload.get("hyperv_service") or {}
    processors = payload.get("processors") or []
    threads: list[dict[str, Any]] = []
    for p in processors:
        cores, logical = _number(p.get("cores")), _number(p.get("logical_processors"))
        threads.append(
            {
                "name": p.get("name"),
                "threads_per_core": round(logical / cores, 1) if cores and logical else None,
            }
        )
    return {
        "virtualization": {
            "vbs_available": None if status is None else status != 0,
            "vbs_running": None if status is None else status == 2,
            "hyperv_running": service.get("status") == "Running" if service else None,
        },
        "processors": threads,
    }


def take_cpu(bridge: Bridge, params: dict[str, Any]) -> Reading:
    result = bridge.run(CPU_SCRIPT, depth=DEEP)

    def build(payload: dict[str, Any]) -> list[Section]:
        return [
            Section("raw", "raw", payload),
            Section("derived", "derived", cpu_derived(payload), basis=CPU_BASIS),
        ]

    return from_object("hardware.cpu", params, CPU_SCRIPT, result, build)


# ---------------------------------------------------------------------------
# hardware.gpu
# ---------------------------------------------------------------------------

GPU_BASIS = (
    "Video memory from AdapterRAM, a 32-bit field that saturates near 4096 MB on larger cards; driver age "
    "in whole days from the driver's date to today; the timeout detection values from "
    "HKLM\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers, where an absent value means the Windows "
    "default (TdrLevel 3, TdrDelay 2 seconds, TdrDdiDelay 5 seconds)."
)

_TDR_DEFAULTS = {"TdrLevel": 3, "TdrDelay": 2, "TdrDdiDelay": 5}


def gpu_derived(payload: dict[str, Any]) -> dict[str, Any]:
    registry = payload.get("tdr_registry") or {}
    tdr = {
        "level": registry.get("TdrLevel") if registry.get("TdrLevel") is not None else _TDR_DEFAULTS["TdrLevel"],
        "delay_seconds": registry.get("TdrDelay") if registry.get("TdrDelay") is not None else _TDR_DEFAULTS["TdrDelay"],
        "ddi_delay_seconds": registry.get("TdrDdiDelay") if registry.get("TdrDdiDelay") is not None else _TDR_DEFAULTS["TdrDdiDelay"],
        "at_defaults": all(registry.get(key) is None for key in _TDR_DEFAULTS),
    }
    return {
        "adapters": [
            {
                "name": a.get("name"),
                "vram_mb": _mb(a.get("adapter_ram_bytes")),
                "driver_age_days": _days_since(a.get("driver_date")),
            }
            for a in (payload.get("adapters") or [])
        ],
        "tdr": tdr,
    }


def take_gpu(bridge: Bridge, params: dict[str, Any]) -> Reading:
    result = bridge.run(GPU_SCRIPT, depth=DEEP)

    def build(payload: dict[str, Any]) -> list[Section]:
        return [
            Section("raw", "raw", payload),
            Section("derived", "derived", gpu_derived(payload), basis=GPU_BASIS),
        ]

    return from_object("hardware.gpu", params, GPU_SCRIPT, result, build)


# ---------------------------------------------------------------------------
# hardware.board
# ---------------------------------------------------------------------------

BOARD_BASIS = (
    "Secure Boot from the UEFISecureBootEnabled value under "
    "HKLM\\SYSTEM\\CurrentControlSet\\Control\\SecureBoot\\State (1 enabled, 0 disabled, absent where the "
    "firmware is not UEFI); the TPM specification's major version from the first component of SpecVersion; "
    "the firmware's age in whole days from the BIOS release date. The presence of the DmaSecurity key is "
    "reported as it stands and is not read as kernel DMA protection being on."
)


def board_derived(payload: dict[str, Any]) -> dict[str, Any]:
    firmware = payload.get("firmware") or {}
    tpm = payload.get("tpm") or {}
    value = _number(firmware.get("uefi_secure_boot_enabled"))
    spec = tpm.get("spec_version")
    major = spec.split(",")[0].strip() if isinstance(spec, str) and spec.strip() else None
    return {
        "secure_boot_enabled": None if value is None else value == 1,
        "tpm_spec_major": major,
        "bios_age_days": _days_since((payload.get("bios") or {}).get("release_date")),
    }


def take_board(bridge: Bridge, params: dict[str, Any]) -> Reading:
    result = bridge.run(BOARD_SCRIPT, depth=DEEP)

    def build(payload: dict[str, Any]) -> list[Section]:
        return [
            Section("raw", "raw", payload),
            Section("derived", "derived", board_derived(payload), basis=BOARD_BASIS),
        ]

    return from_object("hardware.board", params, BOARD_SCRIPT, result, build)


# ---------------------------------------------------------------------------
# hardware.storage
# ---------------------------------------------------------------------------

STORAGE_BASIS = (
    "Sizes from bytes over 2^30; a volume's used percentage from its size less its remaining space; the "
    "reliability counter's temperature kept only when it falls between 0 and 100 degrees Celsius, since "
    "drives that do not report one return a value outside that range."
)


def storage_derived(payload: dict[str, Any]) -> dict[str, Any]:
    disks: list[dict[str, Any]] = []
    for disk in payload.get("disks") or []:
        reliability = disk.get("reliability") or {}
        temperature = _number(reliability.get("temperature_c"))
        disks.append(
            {
                "device_id": disk.get("device_id"),
                "friendly_name": disk.get("friendly_name"),
                "size_gb": _gb(disk.get("size_bytes"), places=0),
                "temperature_c": temperature if temperature is not None and 0 <= temperature <= 100 else None,
                "volumes": [
                    {
                        "drive_letter": v.get("drive_letter"),
                        "free_gb": _gb(v.get("size_remaining_bytes")),
                        "used_percent": _percent_used(v.get("size_bytes"), v.get("size_remaining_bytes")),
                    }
                    for v in (disk.get("volumes") or [])
                ],
            }
        )
    return {"disks": disks}


def take_storage(bridge: Bridge, params: dict[str, Any]) -> Reading:
    result = bridge.run(STORAGE_SCRIPT, depth=DEEP)

    def build(payload: dict[str, Any]) -> list[Section]:
        return [
            Section("raw", "raw", payload),
            Section("derived", "derived", storage_derived(payload), basis=STORAGE_BASIS),
        ]

    return from_object("hardware.storage", params, STORAGE_SCRIPT, result, build)


# ---------------------------------------------------------------------------
# hardware.network
# ---------------------------------------------------------------------------

NETWORK_BASIS = (
    "An adapter counts as up when Windows reports its status as Up; as disabled when Configuration Manager "
    "error code 22 is set against its device; driver age in whole days from the driver's date to today."
)

_CM_DISABLED = 22


def network_derived(payload: dict[str, Any]) -> dict[str, Any]:
    adapters: list[dict[str, Any]] = []
    for a in payload.get("adapters") or []:
        code = _number(a.get("config_manager_error_code"))
        adapters.append(
            {
                "name": a.get("name"),
                "up": a.get("status") == "Up",
                "disabled": None if code is None else code == _CM_DISABLED,
                "driver_age_days": _days_since((a.get("driver") or {}).get("date")),
            }
        )
    return {"adapters": adapters}


def take_network(bridge: Bridge, params: dict[str, Any]) -> Reading:
    result = bridge.run(NETWORK_SCRIPT, depth=DEEP)

    def build(payload: dict[str, Any]) -> list[Section]:
        return [
            Section("raw", "raw", payload),
            Section("derived", "derived", network_derived(payload), basis=NETWORK_BASIS),
        ]

    return from_object("hardware.network", params, NETWORK_SCRIPT, result, build)


# ---------------------------------------------------------------------------
# drivers, dumps
# ---------------------------------------------------------------------------


def take_drivers(bridge: Bridge, params: dict[str, Any]) -> Reading:
    script = drivers_script(params["count"])
    result = bridge.run(script)
    return from_bridge("drivers", params, script, result, section="drivers")


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------

register(
    Spec(
        name="system",
        description="The snapshot: what Windows this is, when it last started, how long it has been up, what the processors are doing and how much memory is free.",
        classes=("raw",),
        take=take_system,
    )
)

register(
    Spec(
        name="hardware",
        description="The fingerprint and the configuration: the parts this machine is made of, the firmware and power settings that shape how it behaves, and the observations those settings support.",
        classes=("derived", "raw", "inferred"),
        take=take_hardware,
    )
)

register(
    Spec(
        name="hardware.cpu",
        description="Processor and platform detail: identity and topology, the cache inventory, and whether virtualization-based security and Hyper-V are running.",
        classes=("raw", "derived"),
        take=take_cpu,
        heavy=True,
    )
)

register(
    Spec(
        name="hardware.gpu",
        description="Display adapters: identity, video memory, the driver and its age, the current mode, and the timeout detection and recovery settings a crash investigation asks about.",
        classes=("raw", "derived"),
        take=take_gpu,
        heavy=True,
    )
)

register(
    Spec(
        name="hardware.board",
        description="Board and firmware: the board and its BIOS, the TPM's state, the firmware type, and whether Secure Boot is on.",
        classes=("raw", "derived"),
        take=take_board,
        private=("board.serial",),
        heavy=True,
    )
)

register(
    Spec(
        name="hardware.storage",
        description="Physical disks with the reliability counters the drive exposes (wear, temperature, hours powered on, error totals) and the volumes carried on each.",
        classes=("raw", "derived"),
        take=take_storage,
        private=("disks[].serial_number",),
        heavy=True,
    )
)

register(
    Spec(
        name="hardware.network",
        description="Network adapters: identity, link and media state, addresses and DNS, the driver and its age, and whether the device is disabled.",
        classes=("raw", "derived"),
        take=take_network,
        private=("adapters[].mac_address",),
        heavy=True,
    )
)

register(
    Spec(
        name="drivers",
        description="Driver changes: the signed drivers the machine is running, most recently dated first, so a new or replaced driver next to a run of errors is visible.",
        classes=("raw",),
        take=take_drivers,
        params=(Param("count", "int", 30, "How many of the most recently dated drivers."),),
    )
)
