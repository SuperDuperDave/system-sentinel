
import json
from ...powershell import run_powershell

async def get_storage_forensics_context():
    """
    Fetches deep forensic context for Storage (NVMe/SATA/USB).
    - Identity: Model, Bus Type, Media Type, Firmware Version.
    - Reliability (SMART): Wear Level, Temperature, Unsafe Shutdowns, Error Counts.
    - Capacity: Size, Allocated, Drive Letters, Free Space.
    """
    
    script = r"""
    $ErrorActionPreference = "SilentlyContinue"

    # Get all physical disks
    $disks = Get-PhysicalDisk | Sort-Object DeviceId

    $storage_data = @()
    $risks = @()

    foreach ($disk in $disks) {
        # Reliability Counters (SMART attributes abstraction)
        $reliability = $disk | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue
        
        # Volume Mapping
        # PhysicalDisk -> Disk -> Partition -> Volume
        $volumes = @()
        try {
            $parts = $disk | Get-Disk | Get-Partition
            foreach ($part in $parts) {
                $vol = $part | Get-Volume -ErrorAction SilentlyContinue
                if ($vol -and $vol.DriveLetter) {
                    $volumes += @{
                        letter = $vol.DriveLetter
                        label = $vol.FileSystemLabel
                        fs = $vol.FileSystem
                        size_gb = [math]::Round($vol.Size / 1GB, 1)
                        free_gb = [math]::Round($vol.SizeRemaining / 1GB, 1)
                        used_percent = if ($vol.Size -gt 0) { [math]::Round((($vol.Size - $vol.SizeRemaining) / $vol.Size) * 100, 1) } else { 0 }
                    }
                    
                    # Risk: Low Disk Space (< 10GB free on C:)
                    if ($vol.DriveLetter -eq 'C' -and $vol.SizeRemaining -lt 10GB) {
                        $risks += @{ id="disk-space-c"; level="warning"; message="System Drive Low Space (< 10GB)" }
                    }
                }
            }
        } catch {}

        # Safe Unpacking of Reliability
        $wear = $null
        $temp = $null
        $unsafe_shutdowns = $null
        $power_hours = $null
        $read_errors = $null
        $write_errors = $null

        if ($reliability) {
            $wear = $reliability.Wear
            $temp = $reliability.Temperature
            # Some drives report 0 or null for temp, filter reasonable range (0-100C)
            if ($temp -gt 100 -or $temp -lt 0) { $temp = $null }
            
            # NVMe specific counters often map here
            $power_hours = $reliability.PowerOnHours
            $read_errors = $reliability.ReadErrorsTotal
            $write_errors = $reliability.WriteErrorsTotal
        }

        # Risk Analysis
        if ($disk.HealthStatus -ne 'Healthy') {
            $risks += @{ id="disk-health-$($disk.DeviceId)"; level="error"; message="$($disk.FriendlyName) reports $($disk.HealthStatus) health" }
        }
        if ($wear -gt 90) {
            $risks += @{ id="disk-wear-$($disk.DeviceId)"; level="warning"; message="$($disk.FriendlyName) Wear Level at $wear%" }
        }
        if ($temp -gt 70) {
             # NVMe can run hot, but 70C is a good warning threshold
             $risks += @{ id="disk-temp-$($disk.DeviceId)"; level="warning"; message="$($disk.FriendlyName) High Temp ($temp C)" }
        }

        $storage_data += @{
            id = $disk.DeviceId
            identity = @{
                model = $disk.FriendlyName
                bus_type = $disk.BusType
                media_type = $disk.MediaType
                firmware = $disk.FirmwareVersion
                serial = $disk.SerialNumber.Trim()
            }
            health = @{
                status = $disk.HealthStatus
                operational = $disk.OperationalStatus
                wear_percent = $wear
                temperature_c = $temp
                power_hours = $power_hours
            }
            stats = @{
                read_errors = $read_errors
                write_errors = $write_errors
            }
            capacity = @{
                total_gb = [math]::Round($disk.Size / 1GB, 1)
            }
            volumes = $volumes
        }
    }

    @{
        drives = $storage_data
        risks = $risks
    } | ConvertTo-Json -Depth 4
    """
    
    return run_powershell(script)
