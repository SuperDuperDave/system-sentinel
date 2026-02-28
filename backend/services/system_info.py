from datetime import datetime, timedelta
import asyncio
from .powershell import run_powershell

class TTLCache:
    def __init__(self, ttl_seconds):
        self.ttl = ttl_seconds
        self.data = None
        self.last_update = None

    def is_valid(self):
        if not self.data or not self.last_update:
            return False
        return datetime.now() < self.last_update + timedelta(seconds=self.ttl)

    def set(self, data):
        self.data = data
        self.last_update = datetime.now()
        return data

    def get(self):
        return self.data

_snapshot_cache = TTLCache(60)
_drivers_cache = TTLCache(300)

async def get_system_snapshot():
    if _snapshot_cache.is_valid():
        return _snapshot_cache.get()

    ps_script = """
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $lastBoot = $os.LastBootUpTime
        $now = Get-Date
        $uptime = $now - $lastBoot
        
        [pscustomobject]@{
            CPU = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
            TotalMemoryGB = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2)
            FreeMemoryGB = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
            Uptime = $uptime.ToString()
            LastBoot = $lastBoot.ToString('o')
        } | ConvertTo-Json -Depth 2
    } catch {
        # Log error to stderr so python can see it, but exit 1
        Write-Error $_.Exception.Message
        exit 1
    }
    """
    
    data = run_powershell(ps_script)
    if data:
        return _snapshot_cache.set(data)
    return {}

async def get_driver_changes():
    if _drivers_cache.is_valid():
        return _drivers_cache.get()

    # Note: Win32_PnPSignedDriver can be slow. Limiting to top 30 after sort.
    # DriverDate format in CIM is typically datetime, convertible to string for generic JSON.
    ps_script = """
    try {
        Get-CimInstance Win32_PnPSignedDriver -ErrorAction Stop | 
        Where-Object { $_.DriverDate -ne $null } |
        Sort-Object DriverDate -Descending | 
        Select-Object -First 30 -Property DeviceName, DriverVersion, DriverProviderName, DriverDate, InfName |
        ForEach-Object {
            @{
                deviceName = $_.DeviceName
                driverVersion = $_.DriverVersion
                driverProvider = $_.DriverProviderName
                driverDate = if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') } else { $null }
                infName = $_.InfName
            }
        } | ConvertTo-Json -Depth 2
    } catch {
        Write-Error $_.Exception.Message
        exit 1
    }
    """
    
    data = run_powershell(ps_script)
    if data:
        # If single result, ConvertTo-Json might output dict, list otherwise
        if isinstance(data, dict):
            data = [data]
        return _drivers_cache.set(data)
    return []
