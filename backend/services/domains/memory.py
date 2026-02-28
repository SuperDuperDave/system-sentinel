import logging
import json
from typing import Dict, Any, Optional
from services.powershell import run_powershell
from services.cper_decoder import WheaDecoderService

logger = logging.getLogger(__name__)

async def get_memory_context() -> Dict[str, Any]:
    """
    Retrieves Quantum Memory Context.
    - Topology: Physical slots, capacity, max capacity.
    - Physics: ECC wired status, voltage rail reality.
    - Integrity: Kit homogeneity check.
    - Forensic: WHEA (Enhanced with CPER Decode) and BugCheck timeline.
    """
    try:
        script = _get_memory_script()
        data = run_powershell(script)
        
        if data and isinstance(data, dict):
            # Quantum Enhancement: Decode CPER for WHEA events in the ledger
            ledger = data.get('ledger', {})
            events = ledger.get('events', [])
            
            if events:
                decoder = WheaDecoderService()
                for event in events:
                    if event.get('Type') == 'whea' and event.get('RawData'):
                        try:
                            decoded = decoder.decode_cper_hex(str(event.get('Id', '?')), event['RawData'])
                            event['DecodedCPER'] = decoded
                        except Exception as e:
                            logger.warning(f"Failed to decode CPER for event {event.get('Id')}: {e}")
            
            return data
            
        return {"error": "Invalid data returned from Memory service"}
        
    except Exception as e:
        logger.error(f"Memory context fetch failed: {e}")
        return {"error": str(e)}

def _get_memory_script() -> str:
    return r"""
    $ErrorActionPreference = "SilentlyContinue"

    # 1. Physical Layer (Topology & Electrical)
    $mem_physical = Get-CimInstance Win32_PhysicalMemory | Select-Object BankLabel, Manufacturer, PartNumber, SerialNumber, ConfiguredClockSpeed, Speed, Capacity, DeviceLocator, SMBIOSMemoryType, TotalWidth, DataWidth, ConfiguredVoltage, MinVoltage, MaxVoltage
    $mem_array = Get-CimInstance Win32_PhysicalMemoryArray | Select-Object MaxCapacity, MemoryDevices, MemoryErrorCorrection

    # 2. Forensic Layer (System Rot) - 30 Days
    $genesis = (Get-Date).AddDays(-30)
    
    # helper for dates
    $d = { param($x) if ($x) { return $x.ToString('o') } return $null }

    # WHEA Events (17,18,19,47)
    # Extract RawData blob for CPER decoding (BitConverter usually works for Byte[])
    $whea_events = Get-WinEvent -FilterHashTable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=$genesis} -ErrorAction SilentlyContinue | 
        Select-Object Id, @{N='TimeCreated';E={$_.TimeCreated.ToString('o')}}, LevelDisplayName, Message, ProviderName, @{N='Type';E={'whea'}}, @{N='RawData'; Expression={
            $bytes = $_.Properties | Where-Object { $_.Value -is [byte[]] } | Select-Object -ExpandProperty Value -First 1;
            if ($bytes) { [System.BitConverter]::ToString($bytes) } else { "" }
        }}
    
    # BugChecks (1001)
    $bugchecks = Get-WinEvent -FilterHashTable @{LogName='System'; EventId=1001; StartTime=$genesis} -ErrorAction SilentlyContinue | 
        Select-Object Id, @{N='TimeCreated';E={$_.TimeCreated.ToString('o')}}, LevelDisplayName, Message, ProviderName, @{N='Type';E={'system'}}

    # 3. Processing
    
    $sticks = @()
    $total_gb = 0
    $unique_kits = @{}
    $occupied_slots = 0
    
    foreach ($m in $mem_physical) {
        $cap_gb = 0
        if ($m.Capacity) { $cap_gb = $m.Capacity / 1GB }
        $total_gb += $cap_gb
        $occupied_slots++
        
        $ecc_present = $false
        if ($m.TotalWidth -gt $m.DataWidth) { $ecc_present = $true }

        $kit_id = "$($m.Manufacturer):$($m.PartNumber)"
        if (-not $unique_kits.ContainsKey($kit_id)) { $unique_kits[$kit_id] = 1 }

        $volt_configured = if ($m.ConfiguredVoltage) { $m.ConfiguredVoltage } else { 0 }
        
        # Enhanced Labeling for Unique ID
        # If DeviceLocator is generic "DIMM 1", we prepend the BankLabel "P0 CHANNEL A"
        $display_locator = $m.DeviceLocator
        if ($m.DeviceLocator -eq "DIMM 1" -or $m.DeviceLocator -match "^DIMM \d+$") {
           $display_locator = "$($m.BankLabel) - $($m.DeviceLocator)"
        }
        
        $sticks += @{
            status = "Populated"
            bank_label = $m.BankLabel
            device_locator = $display_locator
            original_locator = $m.DeviceLocator
            manufacturer = $m.Manufacturer
            part_number = $m.PartNumber
            serial = $m.SerialNumber
            capacity_gb = $cap_gb
            speed_rated = $m.Speed
            speed_configured = $m.ConfiguredClockSpeed
            voltage_configured = $volt_configured
            ecc_present = $ecc_present
            widths = @{ total = $m.TotalWidth; data = $m.DataWidth }
        }
    }

    # Pad with Empty Slots
    $total_slots = if ($mem_array.MemoryDevices) { $mem_array.MemoryDevices } else { $occupied_slots }
    if ($occupied_slots -lt $total_slots) {
        for ($i = $occupied_slots; $i -lt $total_slots; $i++) {
            $sticks += @{
                status = "Empty"
                device_locator = "Slot $($i + 1)"
                manufacturer = "Empty"
                capacity_gb = 0
            }
        }
    }

    # Signal Integrity Analysis
    $integrity_score = 1.0
    $integrity_issues = @()
    if ($unique_kits.Count -gt 1) {
        $integrity_score = 0.5
        $integrity_issues += "Mixed Kits Detected"
    }
    
    # Ledger
    $ledger = @()
    if ($whea_events) { $ledger += $whea_events }
    if ($bugchecks) { $ledger += $bugchecks }
    $ledger = $ledger | Sort-Object TimeCreated -Descending | Select-Object -First 50

    $stats = @{
        whea_count = ($ledger | Where-Object { $_.Type -eq 'whea' }).Count
        bsod_count = ($ledger | Where-Object { $_.Id -eq 1001 }).Count
    }

    $topo_meta = @{
        max_capacity_gb = if ($mem_array.MaxCapacity) { $mem_array.MaxCapacity / 1024 / 1024 } else { 0 }
        slots_total = $total_slots
        slots_used = $occupied_slots
    }

    @{
        topology = @{
            meta = $topo_meta
            sticks = $sticks
        }
        integrity = @{
            score = $integrity_score
            issues = $integrity_issues
            kits_found = $unique_kits.Keys
        }
        ledger = @{
            events = $ledger
            stats = $stats
            window_days = 30
        }
    } | ConvertTo-Json -Depth 4
    """
