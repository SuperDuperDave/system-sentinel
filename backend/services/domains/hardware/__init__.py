import asyncio
import json
from datetime import datetime
from ...powershell import run_powershell
from .cpu_platform import get_cpu_platform_context
from .gpu_display import get_gpu_display_context
from .firmware_board import get_firmware_board_context
from .storage_forensics import get_storage_forensics_context
from .network_connectivity import get_network_connectivity_context

async def get_hardware_overview():
    """
    Fetches the Hardware Platform Model Overview:
    - Fingerprints (Identity)
    - Configuration (Security/Power)
    - Derived Risk Flags
    - Sub-domain Contexts (CPU, GPU, etc.)
    """
    
    # Parallel fetch for sub-modules
    overview_task = _fetch_overview_script()
    cpu_task = get_cpu_platform_context()
    gpu_task = get_gpu_display_context()
    board_task = get_firmware_board_context()
    storage_task = get_storage_forensics_context()
    network_task = get_network_connectivity_context()
    
    overview_data, cpu_data, gpu_data, board_data, storage_data, network_data = await asyncio.gather(overview_task, cpu_task, gpu_task, board_task, storage_task, network_task)
    
    if not overview_data:
        return {} # Should handle error gracefully

    # Merge / Nest
    overview_data['details'] = {
        'cpu': cpu_data,
        'gpu': gpu_data,
        'board': board_data,
        'storage': storage_data,
        'network': network_data
    }
    
    return overview_data

async def _fetch_overview_script():
    ps_script = """
    $ErrorActionPreference = "SilentlyContinue"
    
    # --- 1. Identity & Fingerprints ---
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1 Name, NumberOfCores, NumberOfLogicalProcessors, Manufacturer, Description, MaxClockSpeed
    $gpu = Get-CimInstance Win32_VideoController | Select-Object -First 1 Name, DriverVersion, AdapterRAM, VideoProcessor, DriverDate
    $board = Get-CimInstance Win32_BaseBoard | Select-Object Product, Manufacturer, Version, SerialNumber
    $bios = Get-CimInstance Win32_BIOS | Select-Object SMBIOSBIOSVersion, ReleaseDate, Version, Manufacturer
    $os = Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize, FreePhysicalMemory, Version, BuildNumber, LastBootUpTime
    $disk = Get-CimInstance Win32_DiskDrive | Where-Object { $_.Index -eq 0 } | Select-Object Model, Size, MediaType, InterfaceType
    
    # --- 2. Configuration & Security ---
    
    # Secure Boot (Registry fallback if cmdlet fails)
    $secure_boot = $false
    try {
        $sb_reg = Get-ItemProperty "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\SecureBoot\\State" -Name "UEFISecureBootEnabled" -ErrorAction SilentlyContinue
        if ($sb_reg.UEFISecureBootEnabled -eq 1) { $secure_boot = $true }
    } catch { }

    # Fast Startup (Hiberboot)
    $fast_startup = $false
    try {
        $hiber = Get-ItemProperty "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Power" -Name "HiberbootEnabled" -ErrorAction SilentlyContinue
        if ($hiber.HiberbootEnabled -eq 1) { $fast_startup = $true }
    } catch { }
    
    # Virtualization
    $virt_firmware = $false
    if ($cpu.VirtualizationFirmwareEnabled) { $virt_firmware = $true }
    
    # Hyper-V Status
    $hv_present = (Get-Service vmms -ErrorAction SilentlyContinue).Status -eq 'Running'
    
    # --- 3. Derived Metrics ---
    
    # Uptime
    $uptime_seconds = 0
    if ($os.LastBootUpTime) {
        $boot = $os.LastBootUpTime
        $now = Get-Date
        $uptime_seconds = ($now - $boot).TotalSeconds
    }
    
    # Memory %
    $mem_total_gb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2)
    $mem_free_gb = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
    $mem_used_percent = 0
    if ($mem_total_gb -gt 0) {
        $mem_used_percent = [math]::Round((($mem_total_gb - $mem_free_gb) / $mem_total_gb) * 100, 1)
    }

    # Storage %
    $c_drive = Get-PSDrive C -ErrorAction SilentlyContinue
    $storage_free_percent = 0
    if ($c_drive) {
        $storage_free_percent = [math]::Round(($c_drive.Free / $c_drive.Used + $c_drive.Free) * 100, 1)
        # Fix logic: Free / (Used + Free)
        $total = $c_drive.Used + $c_drive.Free
        if ($total -gt 0) {
           $storage_free_percent = [math]::Round(($c_drive.Free / $total) * 100, 1)
        }
    }

    # --- 4. Risk Analysis ---
    $risks = @()
    
    if ($fast_startup) {
        $risks += @{ id = "risk-fast-start"; level = "warning"; message = "Fast Startup is Enabled (Prevents clean cold boots)"; domain = "power" }
    }
    if (-not $secure_boot) {
        $risks += @{ id = "risk-secure-boot"; level = "critical"; message = "Secure Boot is Disabled"; domain = "security" }
    }
    if ($storage_free_percent -lt 10) {
        $risks += @{ id = "risk-storage-space"; level = "warning"; message = "System Drive Low Space (<10%)"; domain = "storage" }
    }
    if ($uptime_seconds -gt 604800) { # 7 days
        $risks += @{ id = "risk-uptime"; level = "info"; message = "Long Uptime (>7 days). Reboot recommended."; domain = "system" }
    }
    
    # Construct Payload
    @{
        fingerprint = @{
            cpu = @{
                name = $cpu.Name
                cores = $cpu.NumberOfCores
                logical = $cpu.NumberOfLogicalProcessors
                manufacturer = $cpu.Manufacturer
                description = $cpu.Description
            }
            gpu = @{
                name = $gpu.Name
                driver_version = $gpu.DriverVersion
                vram_mb = if ($gpu.AdapterRAM) { $gpu.AdapterRAM / 1MB } else { 0 }
                date = if ($gpu.DriverDate) { $gpu.DriverDate.ToString('yyyy-MM-dd') } else { $null }
            }
            board = @{
                product = $board.Product
                manufacturer = $board.Manufacturer
                version = $board.Version
                bios_version = $bios.SMBIOSBIOSVersion
                bios_date = if ($bios.ReleaseDate) { $bios.ReleaseDate.ToString('yyyy-MM-dd') } else { $null }
            }
            storage = @{
                boot_model = $disk.Model
                size_gb = if ($disk.Size) { [math]::Round($disk.Size / 1GB, 0) } else { 0 }
                media_type = $disk.MediaType
                interface = $disk.InterfaceType
                free_percent = $storage_free_percent
            }
        }
        config = @{
            secure_boot = $secure_boot
            fast_startup = $fast_startup
            virtualization_firmware = $virt_firmware
            hyperv_running = $hv_present
            os_version = "$($os.Version) ($($os.BuildNumber))"
            uptime_seconds = $uptime_seconds
        }
        risks = $risks
        timestamp = (Get-Date).ToString('o')
    } | ConvertTo-Json -Depth 4
    """

    return run_powershell(ps_script)
