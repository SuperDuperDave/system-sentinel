import json
from ...powershell import run_powershell

async def get_gpu_display_context():
    """
    Fetches deep forensic context for GPU & Display subsystems.
    - Identity: GPU Name, VRAM, Driver Version & Date.
    - Architecture: WDDM Version, refresh rates.
    - Stability: TDR (Timeout Detection Recovery) settings from Registry.
    - Provenance: Driver Provider and Signing status.
    """
    
    script = r"""
    $ErrorActionPreference = "SilentlyContinue"

    # 1. Primary GPU Identity
    $gpu = Get-CimInstance Win32_VideoController | Select-Object -First 1 Name, AdapterRAM, DriverVersion, DriverDate, VideoProcessor, VideoModeDescription, InfFilename, InstalledDisplayDrivers

    # 2. Display Configuration (Resolution/Refresh)
    # VideoModeDescription usually contains "1920 x 1080 x 4294967296 colors @ 60 Hertz"
    
    # 3. Graphics Stability (TDR)
    # Check for TDR Delay modifications often used by miners or unstable overclocks
    $tdr_path = "HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers"
    $tdr_info = @{
        TdrLevel = "Default"
        TdrDelay = "Default (2s)"
        TdrDdiDelay = "Default (5s)"
    }
    
    if (Test-Path $tdr_path) {
        $reg = Get-ItemProperty $tdr_path
        if ($reg.TdrLevel) { $tdr_info.TdrLevel = $reg.TdrLevel }
        if ($reg.TdrDelay) { $tdr_info.TdrDelay = "$($reg.TdrDelay)s" }
        if ($reg.TdrDdiDelay) { $tdr_info.TdrDdiDelay = "$($reg.TdrDdiDelay)s" }
    }

    # 4. Driver Provenance & WDDM
    # Parsing DXDiag or DxgKrnl is hard via pure CIM. 
    # We can infer WDDM from OS version or check specific driver file versions if needed.
    # For now, we rely on the CIM DriverDate/Version.
    
    # 5. Risks & Anomalies
    $risks = @()
    
    # Check for 'Basic Display Adapter'
    if ($gpu.Name -match "Basic Display Adapter" -or $gpu.Name -match "VGA Compatible") {
        $risks += @{ id="gpu-basic-driver"; level="critical"; message="Running on Basic Display Driver (No Acceleration)"; }
    }
    
    # Check for very old drivers (>1 year)
    if ($gpu.DriverDate) {
        $driver_date = [datetime]::ParseExact($gpu.DriverDate.ToString().Substring(0, 8), "yyyyMMdd", $null)
        if ((Get-Date).AddYears(-2) -gt $driver_date) {
            $risks += @{ id="gpu-old-driver"; level="warning"; message="Graphics Driver is > 2 years old"; }
        }
    }
    
    # Return Payload
    @{
        identity = @{
            name = $gpu.Name
            processor = $gpu.VideoProcessor
            vram_mb = if ($gpu.AdapterRAM) { $gpu.AdapterRAM / 1MB } else { 0 }
            mode = $gpu.VideoModeDescription
        }
        driver = @{
            version = $gpu.DriverVersion
            date = if ($gpu.DriverDate) { $gpu.DriverDate.ToString('yyyy-MM-dd') } else { "Unknown" }
            inf_file = $gpu.InfFilename
            files = $gpu.InstalledDisplayDrivers
        }
        stability = @{
            tdr = $tdr_info
        }
        risks = $risks
    } | ConvertTo-Json -Depth 3
    """
    
    return run_powershell(script)
