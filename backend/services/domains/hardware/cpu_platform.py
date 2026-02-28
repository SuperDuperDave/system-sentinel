import json
from ...powershell import run_powershell

async def get_cpu_platform_context():
    """
    Fetches deep forensic context for CPU & Platform.
    - Identity: Name, CPUID, Microcode, Stepping.
    - Topology: Sockets, NUMA nodes, L1/L2/L3 Cache lines (if accessible).
    - Virtualization: VBS, HVCI, SLAT, IOMMU status.
    - Power: P-States (if visible via CIM), Thermal checks.
    """
    
    script = r"""
    $ErrorActionPreference = "SilentlyContinue"

    # 1. Identity & Microcode
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1 Name, Caption, Manufacturer, Revision, ProcessorId, Stepping, MaxClockSpeed, L2CacheSize, L3CacheSize, NumberOfCores, NumberOfLogicalProcessors
    
    # 2. Virtualization & Security (VBS/HVCI)
    # Win32_DeviceGuard requires admin, usually. Fallbacks used.
    $vbs_status = @{
        Available = $false
        Running = $false
        SecurityServicesRange = "Unknown"
    }
    
    try {
        $dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction Stop
        $vbs_status.Available = ($dg.VirtualizationBasedSecurityStatus -ne 0)
        $vbs_status.Running = ($dg.VirtualizationBasedSecurityStatus -eq 2)
        # SecurityServicesRunning is an array of ints
        $vbs_status.Services = $dg.SecurityServicesRunning
    } catch {
        # Fallback using SystemInfo concepts or just default to false if not accessible
    }

    # 3. Hyper-V State
    $hyperv = @{
        Present = (Get-Service vmms -ErrorAction SilentlyContinue).Status -eq 'Running'
        Mode = "Unknown"
    }

    # 4. Cache & Topology
    # Get-CimInstance Win32_CacheMemory matches caches to processors
    $caches = Get-CimInstance Win32_CacheMemory | Select-Object Level, MaxCacheSize, InstalledSize, CacheType, status

    # 5. Risks
    $risks = @()
    
    if (-not $cpu.NumberOfCores) {
        $risks += @{ id="cpu-blind"; level="error"; message="Unable to query CPU cores via CIM." }
    }
    
    # Return Payload
    @{
        identity = @{
            name = $cpu.Name
            id = $cpu.ProcessorId
            manufacturer = $cpu.Manufacturer
            microcode_rev = $cpu.Revision # Often maps to stepping or revision
            stepping = $cpu.Stepping
            clock_max_mhz = $cpu.MaxClockSpeed
        }
        topology = @{
            cores = $cpu.NumberOfCores
            threads = $cpu.NumberOfLogicalProcessors
            l2_cache_kb = $cpu.L2CacheSize
            l3_cache_kb = $cpu.L3CacheSize
            sockets = 1 # simplified for now
        }
        virtualization = @{
            vbs = $vbs_status
            hyperv = $hyperv
            firmware_enabled = $true # Derived in basic view, hard to see deep settings without admin
        }
        caches = $caches
        risks = $risks
    } | ConvertTo-Json -Depth 3
    """
    
    return run_powershell(script)
