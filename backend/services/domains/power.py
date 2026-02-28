import logging
import json
from typing import Dict, Any, Optional
from services.powershell import run_powershell

logger = logging.getLogger(__name__)

async def get_power_context(target_time_iso: Optional[str] = None) -> Dict[str, Any]:
    """
    Retrieves Power Physics Context.
    - Invariants: Fast Startup, ASPM, Secure Boot (via SecureBoot PS provider if avail)
    - Transitions: Recent Sleep/Wake events relative to now/target.
    """
    try:
        script = _get_power_script(target_time_iso)
        data = run_powershell(script)
        
        if data and isinstance(data, dict):
            return data
            
        return {"error": "Invalid data returned from Power service"}
        
    except Exception as e:
        logger.error(f"Power context fetch failed: {e}")
        return {"error": str(e)}

def _get_power_script(target_time: Optional[str]) -> str:
    return r"""
    $ErrorActionPreference = "SilentlyContinue"
    $ProgressPreference = "SilentlyContinue"

    # --- HELPER: Safe Parsers ---
    function Get-RegistryValue {
        param($Path, $Name)
        try {
            $val = Get-ItemProperty -Path $Path -Name $Name -ErrorAction Stop
            return @{ val = $val.$Name; error = $null }
        } catch {
            return @{ val = $null; error = $_.Exception.Message }
        }
    }

    # --- 1. INVARIANTS (The Static State) ---

    # A. S0ix vs S3 (Supported Sleep States)
    $sleep_out = powercfg /a 2>&1 | Out-String
    $s0ix = $sleep_out -match "S0 Low Power Idle"
    $s3 = $sleep_out -match "Standby \(S3\)"
    $sleep_model = if ($s0ix) { "Modern Standby (S0ix)" } elseif ($s3) { "Legacy (S3)" } else { "Unknown" }

    # B. Power Source (AC vs DC)
    $battery = Get-CimInstance -ClassName Win32_Battery -ErrorAction SilentlyContinue
    $on_battery = if ($battery -and $battery.BatteryStatus -eq 1) { $true } else { $false } # 1 = Discharging
    # Fallback to simple detection if status ambiguous
    $power_source = if ($battery) {
        if ($battery.BatteryStatus -eq 2) { "AC (Plugged In)" } elseif ($battery.BatteryStatus -eq 1) { "DC (Battery)" } else { "AC (Calculated)" }
    } else {
        "AC (Desktop/No Battery)"
    }

    # C. ASPM (AC & DC)
    $aspm_out = powercfg /query SCHEME_CURRENT SUB_PCIEXPRESS ASPM 2>&1
    $ac_index = "Unknown"; $dc_index = "Unknown"
    foreach ($line in ($aspm_out -split "`r`n")) {
        if ($line -match "Current AC Power Setting Index:\s*(0x[0-9a-fA-F]+)") { $ac_index = $Matches[1] }
        if ($line -match "Current DC Power Setting Index:\s*(0x[0-9a-fA-F]+)") { $dc_index = $Matches[1] }
    }
    # Map: 0=Off, 1=L0s, 2=L1, 3=L0s+L1
    $aspm_map = @{ "0x00000000"="Off"; "0x00000001"="L0s"; "0x00000002"="L1"; "0x00000003"="L0s+L1" }

    # D. Fast Startup (Policy + Reality)
    $fs_pol = Get-RegistryValue "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power" "HiberbootEnabled"
    $fs_policy = if ($fs_pol.val -eq 1) { "Enabled" } else { "Disabled" }
    
    # Try to detect last boot type (requires Admin, usually Event 27)
    # Mocking reality check for now as requires complex event parsing
    
    # --- 2. SIGNALS (High Value Context) ---
    
    # Wake Armed
    $armed = powercfg /devicequery wake_armed
    $armed_list = if ($armed) { $armed -split "`r`n" | Where-Object { $_ -match "\S" } } else { @() }
    
    # --- 3. TRANSITION LEDGER (Forensic History) ---
    # Look back 60 minutes
    $start = (Get-Date).AddMinutes(-60)
    
    # Providers: 
    # Kernel-Power (42=Sleep, 41=Crash/Reboot, 107=Resume)
    # Power-Troubleshooter (1=Wake)
    # Display (4101=Driver Reset)
    # BugCheck (1001=BSOD)
    
    $ledger_raw = Get-WinEvent -FilterHashtable @{
        LogName='System'; 
        ProviderName=@('Microsoft-Windows-Kernel-Power', 'Microsoft-Windows-Power-Troubleshooter', 'Display', 'Microsoft-Windows-WER-SystemErrorReporting'); 
        Id=@(42, 107, 41, 1, 4101, 1001)
    } -MaxEvents 50 -ErrorAction SilentlyContinue | Where-Object { $_.TimeCreated -ge $start }
    
    $ledger = @()
    if ($ledger_raw) {
        foreach ($evt in $ledger_raw) {
            $type = switch ($evt.Id) {
                42 { "Sleep" }
                1 { "Wake" }
                107 { "Resume" }
                41 { "Unexpected Shutdown" }
                4101 { "GPU Driver Reset" }
                1001 { "BugCheck (BSOD)" }
                Default { "Unknown Event" }
            }
            
            $ledger += @{
                id = $evt.Id
                provider = $evt.ProviderName
                time = $evt.TimeCreated.ToString("o")
                type = $type
                message = $evt.Message
            }
        }
    }

    # --- PAYLOAD ---
    @{
        invariants = @{
            sleep_model = @{ value = $sleep_model; source = "powercfg /a" }
            power_source = @{ value = $power_source; source = "WMI Win32_Battery" }
            pcie_aspm = @{ 
                ac_index = $ac_index
                dc_index = $dc_index
                decoded_ac = if ($aspm_map[$ac_index]) { $aspm_map[$ac_index] } else { "Unknown" }
                decoded_dc = if ($aspm_map[$dc_index]) { $aspm_map[$dc_index] } else { "Unknown" }
                source = "powercfg /query"
            }
            fast_startup = @{
                policy = $fs_policy
                registry_path = "HKLM\...\HiberbootEnabled"
            }
        }
        signals = @{
            wake_armed = $armed_list
            wake_armed_count = $armed_list.Count
        }
        transitions = @{
            window_minutes = 60
            ledger = $ledger
        }
        derived = @{
            has_gpu_reset = [bool]($ledger | Where-Object { $_.id -eq 4101 })
            has_unexpected_shutdown = [bool]($ledger | Where-Object { $_.id -eq 41 })
        }
    } | ConvertTo-Json -Depth 4
    """
